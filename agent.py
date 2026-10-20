import os, re, sys, json, shutil, subprocess, tempfile, difflib, hashlib, argparse
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(
    base_url="https://api.tokenfactory.nebius.com/v1/",
    api_key=os.environ["NEBIUS_API_KEY"],
)
MODEL = "nvidia/nemotron-3-super-120b-a12b"
MAX_ITERS = 12
MAX_OUT = 6000

ap = argparse.ArgumentParser()
ap.add_argument("repo", nargs="?", default="demo_repo")
ap.add_argument("--feature", help="Description of a new feature to implement")
CLI = ap.parse_args()
SRC, FEATURE = CLI.repo, CLI.feature

WORK = tempfile.mkdtemp(prefix="agent_")
shutil.copytree(SRC, WORK, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
print(f"[agent] mode: {'new feature' if FEATURE else 'bug fix'}")
print(f"[agent] working on a copy: {WORK}")

CONFIG_FILES = {"conftest.py", "pytest.ini", "setup.cfg", "tox.ini", "pyproject.toml"}
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", ".venv", "node_modules"}
NEW_TESTS = set()

#functions 
def is_protected(rel):
    base = os.path.basename(rel)
    return base.startswith("test_") or base.endswith("_test.py") or base in CONFIG_FILES


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# Fingerprint the original tests so we can detect tampering at the end
ORIGINAL = {}
for _root, _dirs, _files in os.walk(WORK):
    for _f in _files:
        _rel = os.path.relpath(os.path.join(_root, _f), WORK)
        if is_protected(_rel):
            ORIGINAL[_rel] = sha(os.path.join(WORK, _rel))


def safe(path):
    root = os.path.realpath(WORK)
    full = os.path.realpath(os.path.join(WORK, path))
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("path is outside the repo")
    return full


def list_files():
    out = []
    for root, dirs, files in os.walk(WORK):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            out.append(os.path.relpath(os.path.join(root, f), WORK))
    return "\n".join(sorted(out))

#read file
def read_file(path, start=1, end=None):
    with open(safe(path), errors="ignore") as f:
        lines = f.read().splitlines()
    total = len(lines)
    start = max(1, int(start))
    end = min(total, int(end)) if end else min(total, start + 199)
    chunk = "\n".join(f"{i}: {lines[i-1]}" for i in range(start, end + 1))
    return f"[{path}, lines {start}-{end} of {total}]\n{chunk}"[:MAX_OUT]


def search_code(pattern, path="."):
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"ERROR: invalid regex: {e}"
    hits = []
    for root, dirs, files in os.walk(safe(path)):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in sorted(files):
            if not f.endswith((".py", ".md", ".txt", ".toml", ".cfg")):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, WORK)
            try:
                with open(full, errors="ignore") as fh:
                    for n, line in enumerate(fh, 1):
                        if rx.search(line):
                            hits.append(f"{rel}:{n}: {line.rstrip()[:200]}")
                            if len(hits) >= 40:
                                return "\n".join(hits) + "\n(truncated at 40 matches)"
            except OSError:
                continue
    return "\n".join(hits) or "No matches."

#edii file
def edit_file(path, old, new):
    full = safe(path)
    rel = os.path.relpath(full, WORK)
    if is_protected(rel) and rel not in NEW_TESTS:
        return "ERROR: editing original tests and pytest config files is not allowed."
    with open(full) as f:
        content = f.read()
    if content.count(old) != 1:
        return f"ERROR: 'old' was found {content.count(old)} times; it must match exactly once."
    with open(full, "w") as f:
        f.write(content.replace(old, new))
    return "OK: file edited."

#add test, always before run test
def add_test(path, content):
    base = os.path.basename(path)
    if not (base.startswith("test_") and base.endswith(".py")):
        return "ERROR: the file name must match test_*.py"
    full = safe(path)
    if os.path.exists(full):
        return "ERROR: that file already exists; existing tests cannot be overwritten."
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    NEW_TESTS.add(os.path.relpath(full, WORK))
    return "OK: test created."

#run test
def run_tests():
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                           cwd=WORK, capture_output=True, text=True, timeout=60)
        out = r.stdout + r.stderr
        status = "PASSED" if r.returncode == 0 else "FAILED"
        return f"{status}\n{out[-MAX_OUT:]}"
    except subprocess.TimeoutExpired:
        return "FAILED: timed out after 60s"


def obj(props, req):
    return {"type": "object", "properties": props, "required": req}

S = {"type": "string"}
TOOLS = [
    {"type": "function", "function": {"name": "list_files",
        "description": "List all files in the repo.",
        "parameters": obj({}, [])}},
    {"type": "function", "function": {"name": "read_file",
        "description": "Read a file, optionally limited to a line range (start, end). "
                       "The line numbers shown in the output are NOT part of the file: "
                       "never include them in edit_file.",
        "parameters": obj({"path": S, "start": {"type": "integer"},
                           "end": {"type": "integer"}}, ["path"])}},
    {"type": "function", "function": {"name": "search_code",
        "description": "Search the repo's files with a regular expression. "
                       "Returns matches as file:line: text.",
        "parameters": obj({"pattern": S, "path": S}, ["pattern"])}},
    {"type": "function", "function": {"name": "edit_file",
        "description": "Replace the exact text 'old' with 'new' in a file. "
                       "'old' must appear exactly once. Original tests cannot be edited.",
        "parameters": obj({"path": S, "old": S, "new": S}, ["path", "old", "new"])}},
    {"type": "function", "function": {"name": "add_test",
        "description": "Create a NEW test file (test_*.py). Cannot overwrite existing files.",
        "parameters": obj({"path": S, "content": S}, ["path", "content"])}},
    {"type": "function", "function": {"name": "run_tests",
        "description": "Run pytest on the repo and return the result.",
        "parameters": obj({}, [])}},
]
FUNCS = {"list_files": list_files, "read_file": read_file, "edit_file": edit_file,
         "add_test": add_test, "run_tests": run_tests, "search_code": search_code}

SYSTEM_BUG = (
    "You are a bug-fixing agent. You are given a repository with failing tests. "
    "Workflow: read the failing tests and the code under test, state the root cause "
    "in one sentence, make the smallest possible change with edit_file, then verify "
    "with run_tests. Never modify existing tests, and never hardcode expected values "
    "or special-case test inputs: the fix must be correct for all inputs, not just "
    "the ones in the tests. In larger repos, use search_code to locate the relevant "
    "code and read_file with start/end to read only what you need. "
    "When all tests pass, reply in English with a short summary: the root cause and "
    "the fix."
)
SYSTEM_FEATURE = (
    "You are an agent that implements new features using test-driven development. "
    "Workflow: "
    "1) Read the existing code. In larger repos, use search_code to locate it and "
    "read_file with start/end to read only what you need. "
    "2) Use add_test to write a NEW test file describing the requested behavior, "
    "including edge cases (for example an empty list or an even number of items). "
    "3) Run run_tests and confirm the new test FAILS before you implement anything. "
    "4) Implement the smallest change that satisfies the test, using edit_file. "
    "5) Run run_tests until everything passes. "
    "Never modify existing tests, and never hardcode expected values: the "
    "implementation must be correct for all inputs, not just the ones in your tests. "
    "Finish with a short summary in English: which test you added and what you changed."
)

print("[agent] running the initial tests...")
first = run_tests()
print(first)

if FEATURE:
    system = SYSTEM_FEATURE
    user = (f"Feature request: {FEATURE}\n\n"
            f"Initial test status:\n{first}")
else:
    if first.startswith("PASSED"):
        sys.exit("The tests already pass, so there is nothing to fix.")
    system = SYSTEM_BUG
    user = f"These tests are failing:\n{first}\nFix the bug."

messages = [{"role": "system", "content": system},
            {"role": "user", "content": user}]

for i in range(1, MAX_ITERS + 1):
    print(f"\n=== Iteration {i}/{MAX_ITERS} ===")
    resp = client.chat.completions.create(
        model=MODEL, messages=messages, tools=TOOLS, max_tokens=4000)
    msg = resp.choices[0].message
    print(f"[tokens] {resp.usage.total_tokens} (reasoning: "
          f"{resp.usage.completion_tokens_details.reasoning_tokens})")

    if not msg.tool_calls:
        print("\n[agent] final summary:\n" + (msg.content or "(no text)"))
        break

    messages.append({
        "role": "assistant",
        "content": msg.content or "",
        "tool_calls": [{"id": tc.id, "type": "function",
                        "function": {"name": tc.function.name,
                                     "arguments": tc.function.arguments}}
                       for tc in msg.tool_calls],
    })
    for tc in msg.tool_calls:
        name = tc.function.name
        try:
            args = json.loads(tc.function.arguments or "{}")
            print(f"[tool] {name}({json.dumps(args, ensure_ascii=False)[:150]})")
            result = FUNCS[name](**args)
        except Exception as e:
            result = f"ERROR: {e}"
        print(f"[result] {str(result)[:300]}")
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
else:
    print("\n[agent] reached the maximum number of iterations.")

print("\n=== FINAL VERIFICATION ===")
final = run_tests()
print(final)

tampered = [r for r, h in ORIGINAL.items()
            if not os.path.exists(os.path.join(WORK, r)) or sha(os.path.join(WORK, r)) != h]
print("Original tests intact:", "YES" if not tampered else f"NO, modified: {tampered}")
if FEATURE:
    print("New tests added:", sorted(NEW_TESTS) or "NONE")
ok = final.startswith("PASSED") and not tampered and (not FEATURE or bool(NEW_TESTS))
print("RESULT:", "SUCCESS" if ok else "REVIEW")

print("\n=== CHANGES ===")
for root, dirs, files in os.walk(WORK):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for f in sorted(files):
        if not f.endswith(".py"):
            continue
        new_path = os.path.join(root, f)
        rel = os.path.relpath(new_path, WORK)
        old_path = os.path.join(SRC, rel)
        with open(new_path) as fh:
            b = fh.read().splitlines()
        if os.path.exists(old_path):
            with open(old_path) as fh:
                a = fh.read().splitlines()
            d = list(difflib.unified_diff(a, b, f"before/{rel}", f"after/{rel}", lineterm=""))
            if d:
                print("\n".join(d))
        else:
            print(f"\n+++ NEW FILE: {rel}")
            print("\n".join("+" + line for line in b))