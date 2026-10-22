import os, re, sys, json, shutil, subprocess, tempfile, difflib, hashlib, argparse, ast
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(
    base_url="https://api.tokenfactory.nebius.com/v1/",
    api_key=os.environ["NEBIUS_API_KEY"],
)
MODEL = os.environ.get("AGENT_MODEL", "nvidia/nemotron-3-super-120b-a12b")
MAX_ITERS = int(os.environ.get("MAX_ITERS", "60"))
MAX_OUT = 6000
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "16000"))     # reasoning tokens count against this
TEST_TIMEOUT = int(os.environ.get("TEST_TIMEOUT", "240"))
AUTO_TEST = os.environ.get("AUTO_TEST", "1") == "1"
ESCALATE_MODEL = os.environ.get("AGENT_ESCALATION_MODEL")   # optional stronger model, e.g. Ultra
STUCK_AFTER = int(os.environ.get("STUCK_AFTER", "6"))       # iterations without progress before escalating
READ_LIMIT = int(os.environ.get("READ_LIMIT", "6"))         # read-only calls in a row before only action tools are offered

ap = argparse.ArgumentParser()
ap.add_argument("repo", nargs="?", default=None)
ap.add_argument("--feature", help="Description of a new feature to implement")
ap.add_argument("--scaffold", help="Natural-language spec of a whole module/app to build from scratch")
ap.add_argument("--out", help="Where to save the generated project in scaffold mode "
                              "(default: scaffold_output/<name>)")
CLI = ap.parse_args()
FEATURE, SCAFFOLD = CLI.feature, CLI.scaffold
if FEATURE and SCAFFOLD:
    sys.exit("Use either --feature or --scaffold, not both.")
if SCAFFOLD and "MAX_ITERS" not in os.environ:
    MAX_ITERS = 80          # building a project takes more steps than fixing a bug
if CLI.repo is None and not SCAFFOLD:
    CLI.repo = "demo_repo"
if SCAFFOLD and not (CLI.repo and os.path.isdir(CLI.repo)):
    SRC = os.path.realpath(tempfile.mkdtemp(prefix="agent_src_"))   # empty starting point
else:
    SRC = CLI.repo

WORK = os.path.realpath(tempfile.mkdtemp(prefix="agent_"))
shutil.copytree(SRC, WORK, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
print(f"[agent] mode: {'scaffold (new project)' if SCAFFOLD else 'new feature' if FEATURE else 'bug fix'}")
print(f"[agent] working on a copy: {WORK}")
if not ESCALATE_MODEL:
    print("[agent] note: AGENT_ESCALATION_MODEL is not set, so the agent will never escalate to a stronger model.")

CONFIG_FILES = {"conftest.py", "pytest.ini", "setup.cfg", "tox.ini", "pyproject.toml"}
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", ".venv", "node_modules", ".deps"}
DEPS = os.path.join(WORK, ".deps")   # packages installed by install_requirements (scaffold mode)
NEW_TESTS = set()
CREATED = set()  # files the agent created with write_file (scaffold mode)
PLAN = None      # the plan submitted with submit_plan (scaffold mode)
FRESH = {}       # path -> line numbers read since that file was last edited
EDITED = set()   # source files the agent has edited (used to restore the best state)


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


def read_file(path, start=1, end=None):
    with open(safe(path), errors="ignore") as f:
        lines = f.read().splitlines()
    total = len(lines)
    start = max(1, int(start))
    if start > total:
        return f"ERROR: {path} has only {total} lines."
    end = min(total, int(end)) if end else min(total, start + 199)
    shown, size, last = [], 0, start - 1
    for i in range(start, end + 1):
        row = f"{i}: {lines[i - 1]}"
        if shown and size + len(row) + 1 > MAX_OUT:
            break
        shown.append(row)
        size += len(row) + 1
        last = i
    # only the lines that were actually shown count as "read" for edit_lines
    FRESH.setdefault(os.path.relpath(safe(path), WORK), set()).update(range(start, last + 1))
    cut = (f"\n[output cut at line {last}; call read_file again with start={last + 1} to continue]"
           if last < end else "")
    return f"[{path}, lines {start}-{last} of {total}]\n" + "\n".join(shown) + cut


def read_symbol(path, name):
    """Return a whole function, class or method by name ('pop' or 'BarrelList.pop')."""
    full = safe(path)
    try:
        with open(full, errors="ignore") as f:
            tree = ast.parse(f.read())
    except SyntaxError as e:
        return f"ERROR: cannot parse {path}: {e.msg} (line {e.lineno}). Use read_file instead."
    kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

    def find(scopes, part, deep):
        found = []
        for scope in scopes:
            nodes = ast.walk(scope) if deep else ast.iter_child_nodes(scope)
            found += [n for n in nodes if isinstance(n, kinds) and n.name == part]
        return found

    parts = name.split(".")
    matches = find([tree], parts[0], True)
    for part in parts[1:]:
        matches = find(matches, part, False)
    if not matches:
        return f"ERROR: no function or class named {name!r} in {path}. Use search_code to find it."
    out = []
    for node in matches[:5]:
        first = min([node.lineno] + [d.lineno for d in node.decorator_list])
        out.append(read_file(path, first, node.end_lineno))
    if len(matches) > 5:
        out.append(f"({len(matches)} matches; showing the first 5. Use 'Class.method' to be specific.)")
    return "\n\n".join(out)


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


def edit_file(path, old, new):
    full = safe(path)
    rel = os.path.relpath(full, WORK)
    if is_protected(rel) and rel not in NEW_TESTS:
        return "ERROR: editing original tests and pytest config files is not allowed."
    with open(full) as f:
        content = f.read()
    count = content.count(old)
    if count != 1:
        if count == 0:
            return ("ERROR: 'old' was not found. Re-read those lines with read_file "
                    "and copy them exactly (without the line-number prefix).")
        lines = [str(content.count("\n", 0, m.start()) + 1)
                 for m in re.finditer(re.escape(old), content)]
        return (f"ERROR: 'old' matches {count} places (starting at lines {', '.join(lines)}). "
                "Include more surrounding lines in 'old' so it matches exactly once, "
                "or use edit_lines with line numbers.")
    with open(full, "w") as f:
        f.write(content.replace(old, new))
    return "OK: file edited."


def edit_lines(path, start, end, new_text):
    full = safe(path)
    rel = os.path.relpath(full, WORK)
    if is_protected(rel) and rel not in NEW_TESTS:
        return "ERROR: editing original tests and pytest config files is not allowed."
    with open(full) as f:
        content = f.read()
    lines = content.split("\n")
    total = len(lines) - 1 if content.endswith("\n") else len(lines)
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        return "ERROR: start and end must be integers."
    if not (1 <= start <= end <= total):
        return f"ERROR: invalid range {start}-{end}; the file has {total} lines."
    fresh = FRESH.get(rel, set())
    missing = [n for n in range(start, end + 1) if n not in fresh]
    if missing:
        return (f"ERROR: lines {missing[0]}-{missing[-1]} have not been read since the last edit "
                f"to this file. Call read_file on a range that includes them first. Line numbers "
                "change after every edit, so older reads may be stale.")
    if new_text.endswith("\n"):
        new_text = new_text[:-1]
    new_lines = new_text.split("\n") if new_text != "" else []
    lines[start - 1:end] = new_lines
    with open(full, "w") as f:
        f.write("\n".join(lines))
    delta = len(new_lines) - (end - start + 1)
    return (f"OK: replaced lines {start}-{end} with {len(new_lines)} lines "
            f"(lines after {end} shifted by {delta:+d}).")


NOT_PROJECT = {"pytest", "pytest_asyncio", "unittest", "httpx", "fastapi", "starlette", "anyio",
               "asyncio", "typing", "__future__",
               # import names of the allowed third-party packages: they are libraries, not project code
               "jwt", "passlib", "bcrypt", "argon2", "sqlalchemy", "aiosqlite", "asyncpg", "flask",
               "requests", "jinja2", "click", "typer", "rich", "dotenv", "aiofiles", "sqlmodel",
               "alembic", "orjson", "pydantic", "pydantic_settings", "uvicorn", "multipart",
               "email_validator"}


def is_project_module(mod):
    """True if 'mod' is code that lives in the repo (or, when scaffolding, will live there)."""
    for base in (WORK, os.path.join(WORK, "src")):
        if os.path.exists(os.path.join(base, mod + ".py")) or os.path.isdir(os.path.join(base, mod)):
            return True
    return bool(SCAFFOLD or FEATURE) and mod not in sys.stdlib_module_names and mod not in NOT_PROJECT


def trivial_test_reason(content):
    """Why a test file is a fake (no real checks of the project's code), or None if it looks real."""
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return f"the file has a syntax error ({e.msg}, line {e.lineno})"
    tests = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")]
    if not tests:
        return "it contains no test functions"
    mods, relative = set(), False
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                relative = True
            elif n.module:
                mods.add(n.module.split(".")[0])
    if not relative and not any(is_project_module(m) for m in mods):
        return "it does not import any code from this project, so it cannot test it"
    asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    raises = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and
              isinstance(n.func, ast.Attribute) and n.func.attr == "raises"]
    if not asserts and not raises:
        return "it contains no assertions"
    if asserts and not raises and all(isinstance(a.test, ast.Constant) for a in asserts):
        return "its assertions are constants (like 'assert True'), so they check nothing"
    return None


def add_test(path, content):
    base = os.path.basename(path)
    if not (base.startswith("test_") and base.endswith(".py")):
        return "ERROR: the file name must match test_*.py"
    full = safe(path)
    if os.path.exists(full):
        return "ERROR: that file already exists; existing tests cannot be overwritten."
    why = trivial_test_reason(content)
    if why:
        return (f"ERROR: test rejected because {why}. Write a real test that imports code from "
                "this project and checks its behavior.")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    NEW_TESTS.add(os.path.relpath(full, WORK))
    return "OK: test created."


def submit_plan(plan):
    global PLAN
    if len(plan.strip()) < 60:
        return "ERROR: the plan is too short. List the files, what each one does, the dependencies, and the test strategy."
    PLAN = plan.strip()
    print("\n[plan]\n" + PLAN + "\n")
    return "OK: plan recorded. Now write requirements.txt, install it, and write the tests first."


def write_file(path, content):
    """Create a new (non-test) file, or rewrite a file this agent created itself."""
    rel = os.path.relpath(safe(path), WORK)
    full = safe(path)
    base = os.path.basename(rel)
    if base.startswith("test_") or base.endswith("_test.py"):
        return "ERROR: test files must be created with add_test."
    if set(rel.split(os.sep)) & SKIP_DIRS:
        return "ERROR: that location is reserved (cache, .git, .deps, ...). Choose another path."
    if os.path.isdir(full):
        return "ERROR: that path is a directory."
    exists = os.path.exists(full)
    if exists and rel not in CREATED:
        return ("ERROR: that file already exists and was not created by you; use edit_file or "
                "edit_lines to change it.")
    if len(content) > 60000:
        return "ERROR: file too large; split it into several modules."
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    CREATED.add(rel)
    n = len(content.splitlines())
    FRESH[rel] = set(range(1, n + 1))     # the model just wrote these lines, so they count as read
    msg = f"OK: {'rewrote' if exists else 'created'} {rel} ({n} lines)."
    problem = syntax_problem(full)
    if problem:
        msg += f"\nWARNING: {problem}\nFix it before you continue."
    return msg


ALLOWED_PACKAGES = {
    "fastapi", "starlette", "uvicorn", "pydantic", "pydantic-settings", "httpx", "anyio",
    "pytest-asyncio", "pyjwt", "passlib", "bcrypt", "argon2-cffi", "python-multipart",
    "email-validator", "sqlalchemy", "aiosqlite", "asyncpg", "flask", "requests", "jinja2",
    "click", "typer", "rich", "python-dotenv", "aiofiles", "sqlmodel", "alembic", "orjson",
}
REQ_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)(\[[A-Za-z0-9_,\-]+\])?\s*"
                      r"((==|>=|<=|~=|<|>)\s*[A-Za-z0-9_.*]+(\s*,\s*(==|>=|<=|~=|<|>)\s*[A-Za-z0-9_.*]+)*)?$")


def clean_env():
    """Environment for running generated code: no API keys or secrets are passed on."""
    env = {k: v for k, v in os.environ.items()
           if not re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", k, re.I)}
    parts = []
    if os.path.isdir(os.path.join(WORK, "src")):
        parts.append(os.path.join(WORK, "src"))     # src/ layout: use the copy, not an installed version
    parts.append(WORK)
    if os.path.isdir(DEPS):
        parts.append(DEPS)
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def install_requirements():
    req = os.path.join(WORK, "requirements.txt")
    if not os.path.exists(req):
        return "ERROR: write requirements.txt first (with write_file)."
    specs, bad = [], []
    with open(req) as f:
        for line in f.read().splitlines():
            line = line.split("#")[0].strip()
            if not line:
                continue
            m = REQ_LINE.match(line)
            name = re.sub(r"[-_.]+", "-", m.group(1)).lower() if m else None
            if not m or name not in ALLOWED_PACKAGES | {"pytest"}:
                bad.append(line)
            elif name != "pytest":          # pytest is already available to the agent
                specs.append(line)
    if bad:
        return ("ERROR: not allowed or not understood: " + ", ".join(bad) + ". Allowed packages: "
                + ", ".join(sorted(ALLOWED_PACKAGES)) + " (plus pytest). Use exact 'name' or "
                "'name>=1.0' lines, nothing else.")
    if not specs:
        return "OK: nothing to install (only pytest)."
    cmd = [sys.executable, "-m", "pip", "install", "--only-binary=:all:", "--quiet",
           "--disable-pip-version-check", "--upgrade", "--target", DEPS] + specs
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=clean_env())
    except subprocess.TimeoutExpired:
        return "ERROR: pip timed out after 600s."
    if r.returncode != 0:
        return ("ERROR: pip failed:\n" + (r.stderr or r.stdout)[-1500:] +
                "\nIf one package has no compatible wheel, remove it from requirements.txt and "
                "import it lazily inside the function that uses it, so the tests do not need it.")
    return "OK: installed " + ", ".join(specs)


def shorten(out, limit=MAX_OUT):
    """Keep the start of the output plus the 'short test summary' at the end."""
    if len(out) <= limit:
        return out
    k = out.rfind("short test summary info")
    tail = out[k:][-min(2000, limit // 2):] if k != -1 else ""
    head = out[: limit - len(tail) - 30]
    return head + "\n...[output truncated]...\n" + tail


def focus(out, limit=14):
    """The traceback lines that matter: 'file.py:LINE: in func' frames and 'E ' error lines."""
    keep = [ln for ln in out.splitlines()
            if ln.startswith("E ") or re.match(r"^[\w/.\-]+\.py:\d+: in ", ln)]
    return "\n".join(keep[:limit])


def count_failures(result):
    """Number of failing tests plus collection errors, or None if it cannot be told."""
    if result.startswith("PASSED"):
        return 0
    lines = [ln for ln in result.splitlines() if ln.strip()]
    nums = re.findall(r"(\d+) (?:failed|errors?)\b", lines[-1]) if lines else []
    return sum(int(n) for n in nums) if nums else None


def failing_names(result):
    """Set of failing test ids ('path::test_name') from pytest's short summary."""
    return set(re.findall(r"^(?:FAILED|ERROR) (\S+?)(?: - .*)?$", result, re.M))


def brief(out):
    """Short test status, used right after each edit. Few failures: add where they happen."""
    if out.startswith("PASSED"):
        return "PASSED: " + out.strip().splitlines()[-1]
    k = out.rfind("short test summary info")
    text = out[k:][-1500:] if k != -1 else out[-800:]
    n = count_failures(out)
    if "during collection" in out or (n is not None and n <= 3):
        text += "\n" + focus(out)
    return text


def syntax_problem(full):
    """Describe a syntax error in a .py file (with the nearby lines), or return None."""
    if not full.endswith(".py"):
        return None
    with open(full) as f:
        src = f.read()
    try:
        compile(src, full, "exec")
    except SyntaxError as e:
        lines, at = src.splitlines(), e.lineno or 1
        shown = "\n".join(f"{i + 1}: {lines[i]}"
                          for i in range(max(0, at - 3), min(len(lines), at + 2)))
        return f"it creates a syntax error ({e.msg}) at line {at}:\n{shown}"
    return None


def run_tests(target=None):
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider"]
    note = ""
    if target:
        target = str(target).strip()
        file_part = target.split("::")[0]
        if target.startswith("-"):
            return "ERROR: target must be a test file, directory or node id (file.py::test_name)."
        try:
            exists = os.path.exists(safe(file_part))
        except ValueError as e:
            return f"ERROR: {e}"
        if not exists:
            return f"ERROR: no such test file or directory: {file_part}"
        cmd.append(target)
        note = f"[ran only {target}; call run_tests without target for the whole suite]\n"
    try:
        r = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True,
                           timeout=TEST_TIMEOUT, env=clean_env())
        out = r.stdout + r.stderr
        status = "PASSED" if r.returncode == 0 else "FAILED"
        return f"{status}\n{note}{shorten(out)}"
    except subprocess.TimeoutExpired:
        return f"FAILED: timed out after {TEST_TIMEOUT}s"


def obj(props, req):
    return {"type": "object", "properties": props, "required": req}

S = {"type": "string"}
I = {"type": "integer"}
TOOLS = [
    {"type": "function", "function": {"name": "list_files",
        "description": "List all files in the repo.",
        "parameters": obj({}, [])}},
    {"type": "function", "function": {"name": "read_file",
        "description": "Read a file, optionally limited to a line range (start, end). "
                       "The line numbers shown in the output are NOT part of the file: "
                       "never include them in edit_file or edit_lines.",
        "parameters": obj({"path": S, "start": I, "end": I}, ["path"])}},
    {"type": "function", "function": {"name": "read_symbol",
        "description": "Read one whole function, class or method by name, with line numbers "
                       "(like read_file). Use 'Class.method' to be specific, e.g. 'BarrelList.pop'. "
                       "Better than guessing line ranges.",
        "parameters": obj({"path": S, "name": S}, ["path", "name"])}},
    {"type": "function", "function": {"name": "search_code",
        "description": "Search the repo's files with a regular expression. "
                       "Returns matches as file:line: text.",
        "parameters": obj({"pattern": S, "path": S}, ["pattern"])}},
    {"type": "function", "function": {"name": "edit_file",
        "description": "Replace the exact text 'old' with 'new' in a file. "
                       "'old' must appear exactly once. Original tests cannot be edited.",
        "parameters": obj({"path": S, "old": S, "new": S}, ["path", "old", "new"])}},
    {"type": "function", "function": {"name": "edit_lines",
        "description": "Replace lines start..end (inclusive, 1-based, as numbered by read_file) "
                       "with new_text. Do NOT include the 'NNN: ' prefixes in new_text. Best when "
                       "the code appears more than once. You must have read those exact lines "
                       "with read_file AFTER your last edit to the file, because line numbers "
                       "shift whenever an edit changes the number of lines.",
        "parameters": obj({"path": S, "start": I, "end": I, "new_text": S},
                          ["path", "start", "end", "new_text"])}},
    {"type": "function", "function": {"name": "add_test",
        "description": "Create a NEW test file (test_*.py). Cannot overwrite existing files.",
        "parameters": obj({"path": S, "content": S}, ["path", "content"])}},
    {"type": "function", "function": {"name": "run_tests",
        "description": "Run pytest and return the result. Optional 'target' runs only one test "
                       "file or test (e.g. 'tests/test_x.py::test_name'): faster and less noise. "
                       "Without target it runs the whole suite.",
        "parameters": obj({"target": S}, [])}},
]
FUNCS = {"list_files": list_files, "read_file": read_file, "read_symbol": read_symbol,
         "search_code": search_code, "edit_file": edit_file, "edit_lines": edit_lines,
         "add_test": add_test, "run_tests": run_tests}

if SCAFFOLD:    # extra tools, only offered when building a project from scratch
    TOOLS += [
        {"type": "function", "function": {"name": "submit_plan",
            "description": "Record your plan BEFORE writing any file: the folder structure, what "
                           "each file does, the dependencies, how external services (database, "
                           "cache, ...) are handled in tests, and the test strategy.",
            "parameters": obj({"plan": S}, ["plan"])}},
        {"type": "function", "function": {"name": "write_file",
            "description": "Create a NEW file (source code, requirements.txt, README.md, config) "
                           "with the full content; parent folders are created automatically. It "
                           "can also rewrite a file you created earlier. Test files must be "
                           "created with add_test instead.",
            "parameters": obj({"path": S, "content": S}, ["path", "content"])}},
        {"type": "function", "function": {"name": "install_requirements",
            "description": "Install the packages listed in requirements.txt into an isolated "
                           "folder. Only a fixed list of well-known packages is allowed, and only "
                           "binary wheels are installed.",
            "parameters": obj({}, [])}},
    ]
    FUNCS.update({"submit_plan": submit_plan, "write_file": write_file,
                  "install_requirements": install_requirements})

TOOL_PARAMS = {t["function"]["name"]: (list(t["function"]["parameters"]["properties"]),
                                      t["function"]["parameters"]["required"]) for t in TOOLS}
ACTION_TOOLS = [t for t in TOOLS if t["function"]["name"] in
                ("edit_file", "edit_lines", "add_test", "run_tests",
                 "submit_plan", "write_file", "install_requirements")]
SCAFFOLD_GATED = ("write_file", "add_test", "install_requirements", "edit_file", "edit_lines")
READ_ONLY = ("read_file", "read_symbol", "search_code", "list_files")
EDIT_TOOLS = ("edit_file", "edit_lines")
EDITS = 0          # number of successful edits so far
SEEN = {}          # read-only calls already made, and the EDITS value when they were made
FAILS = None       # failing tests at the last full test run (None = unknown)
FAIL_NAMES = set() # names of the failing tests at the last full test run
PROGRESS = 0       # how many times an edit lowered the number of failing tests
BLOCK_STREAK = 0   # consecutive repeated calls that were blocked
READS_IN_ROW = 0   # consecutive read-only calls (blocked ones included)
BEST_FAILS = None  # fewest failing tests seen so far
BEST_FILES = {}    # snapshot of the edited files at that moment
FRESH.clear()


def snapshot():
    snap = {}
    for rel in sorted(EDITED | NEW_TESTS):
        full = os.path.join(WORK, rel)
        if os.path.exists(full):
            with open(full) as f:
                snap[rel] = f.read()
    return snap


def note_state(fails):
    """Remember the state with the fewest failing tests, so we can go back to it."""
    global BEST_FAILS, BEST_FILES
    if fails is not None and (BEST_FAILS is None or fails < BEST_FAILS):
        BEST_FAILS, BEST_FILES = fails, snapshot()


def restore_best():
    for rel in sorted(EDITED | set(BEST_FILES)):
        full = os.path.join(WORK, rel)
        if rel in BEST_FILES:
            with open(full, "w") as f:
                f.write(BEST_FILES[rel])
        elif os.path.exists(os.path.join(SRC, rel)):
            shutil.copyfile(os.path.join(SRC, rel), full)   # edited only after the best state


def normalize(args):
    """Models sometimes send numbers as strings ('108'); make them integers."""
    if not isinstance(args, dict):
        raise ValueError("tool arguments must be a JSON object")
    for k in ("start", "end"):
        if isinstance(args.get(k), str) and args[k].strip().isdigit():
            args[k] = int(args[k])
    return args


def failure_context(out, window=12):
    """Code around the frames of the first failure output: saves the exploration steps."""
    chunks, seen = [], set()
    for path, line in re.findall(r"^([\w/.\-]+\.py):(\d+): in ", out, re.M):
        if (path, line) in seen or os.path.isabs(path) or not os.path.isfile(os.path.join(WORK, path)):
            continue
        seen.add((path, line))
        n = int(line)
        chunks.append(read_file(path, max(1, n - window), n + window))
    return "\n\n".join(chunks[:6])[:5000]


def finish_edit(args, result, backup):
    """Check an edit that was just applied. Undo it if it breaks the code or makes tests worse."""
    global EDITS, FAILS, FAIL_NAMES, PROGRESS
    full = safe(args["path"])
    rel = os.path.relpath(full, WORK)
    problem, new_fails, tests = syntax_problem(full), None, ""
    if problem is None and AUTO_TEST:
        tests = run_tests()
        new_fails = count_failures(tests)
        if FAILS is not None and new_fails is not None and new_fails > FAILS:
            problem = (f"it made the tests worse (failing tests went from {FAILS} "
                       f"to {new_fails}):\n{brief(tests)}")
    if problem is not None and backup is not None:
        with open(full, "w") as f:
            f.write(backup)
        return (f"ERROR: your edit was UNDONE because {problem}\n"
                "The file is exactly as it was before this edit. Look at the lines again "
                "and try a different edit.")
    EDITS += 1
    EDITED.add(rel)
    FRESH.pop(rel, None)
    if not AUTO_TEST:
        return result
    prev, names = FAILS, failing_names(tests)
    if prev is not None and new_fails is not None and new_fails < prev:
        PROGRESS += 1
    notes = []
    if new_fails is not None:
        fixed = sorted(FAIL_NAMES - names)
        if fixed:
            notes.append("Now passing: " + ", ".join(fixed) + ".")
        if new_fails:
            notes.append(f"Still failing ({new_fails}): " + (", ".join(sorted(names)) or "see above")
                         + ". Keep going until it is 0.")
        if prev is not None and new_fails == prev and new_fails > 0:
            notes.append("This edit did NOT reduce the number of failing tests. If it is not needed "
                         "for one of the remaining failures, undo it by editing it back.")
        FAIL_NAMES = names
    FAILS = new_fails
    note_state(new_fails)
    note = ("\n" + "\n".join(notes)) if notes else ""
    return f"{result}\n\n[tests re-run automatically after your edit]\n{brief(tests)}{note}"


def run_tool(name, args):
    """Run one tool call and return the text the model should see."""
    global EDITS, FAILS, FAIL_NAMES, BLOCK_STREAK, READS_IN_ROW, BEST_FAILS, BEST_FILES
    READS_IN_ROW = READS_IN_ROW + 1 if name in READ_ONLY else 0
    if name not in FUNCS:
        return f"ERROR: unknown tool {name!r}. Available: {', '.join(FUNCS)}."
    unknown = [a for a in args if a not in TOOL_PARAMS[name][0]]
    absent = [a for a in TOOL_PARAMS[name][1] if a not in args]
    if unknown or absent:
        return (f"ERROR: wrong arguments for {name}. It takes: "
                f"{', '.join(TOOL_PARAMS[name][0]) or 'no arguments'} "
                f"(required: {', '.join(TOOL_PARAMS[name][1]) or 'none'}). "
                f"You sent: {', '.join(args) or 'none'}.")

    if SCAFFOLD and PLAN is None and name in SCAFFOLD_GATED:
        return "ERROR: call submit_plan first: the plan must come before any file is written."

    # Block reads that give no new information (same call, or lines already shown).
    key = (name, json.dumps(args, sort_keys=True))
    repeated = name in READ_ONLY and name != "read_file" and SEEN.get(key) == EDITS
    covered = False
    if name == "read_file":
        rel = os.path.relpath(safe(args["path"]), WORK)
        with open(safe(args["path"]), errors="ignore") as f:
            total = len(f.read().splitlines())
        s = max(1, int(args.get("start", 1)))
        e = min(total, int(args["end"])) if args.get("end") else min(total, s + 199)
        covered = s <= e and set(range(s, e + 1)) <= FRESH.get(rel, set())
    if repeated or covered:
        BLOCK_STREAK += 1
        return ("You already have that information: those lines were already shown to you and "
                "nothing has changed since, so their line numbers are still valid for edit_lines. "
                "Your next call must be an edit (edit_lines or edit_file) or run_tests.")
    BLOCK_STREAK = 0

    backup = None
    if name in EDIT_TOOLS:
        with open(safe(args["path"])) as f:
            backup = f.read()
    result = FUNCS[name](**args)
    if result is None:
        return "ERROR: the tool returned no result, so nothing was done."
    ok = str(result).startswith("OK")
    if name in READ_ONLY:
        SEEN[key] = EDITS
    if name == "run_tests" and not args.get("target"):   # only a full run says how many tests fail
        FAILS = count_failures(result)
        FAIL_NAMES = failing_names(result)
        note_state(FAILS)
    if ok and name == "add_test":
        EDITS += 1
        FAILS, FAIL_NAMES = None, set()
        BEST_FAILS, BEST_FILES = None, {}      # the test suite changed: old counts are not comparable
    if ok and name == "write_file":
        EDITS += 1
        if "rewrote" in str(result) and NEW_TESTS and AUTO_TEST and "WARNING" not in str(result):
            tests = run_tests()                 # rewriting existing code: show what it did to the tests
            FAILS, FAIL_NAMES = count_failures(tests), failing_names(tests)
            result = f"{result}\n\n[tests re-run automatically after your edit]\n{brief(tests)}"
    if ok and name in EDIT_TOOLS:
        return finish_edit(args, result, backup)
    return result


SYSTEM_BUG = (
    "You are a bug-fixing agent. You are given a repository with failing tests. "
    "Workflow: read the failing tests and the code under test, state the root cause "
    "in one sentence, make the smallest possible change with edit_file, then verify "
    "with run_tests. Never modify existing tests, and never hardcode expected values "
    "or special-case test inputs: the fix must be correct for all inputs, not just "
    "the ones in the tests. In larger repos, use search_code to locate the relevant "
    "code, read_symbol to read a whole function or method (for example 'BarrelList.pop') "
    "instead of guessing line ranges, and read_file with start/end for anything else. "
    "The failing test output shows the exact lines involved, and the first message already "
    "includes the code around them. Fix every function the "
    "failing tests point to, not just the first one. After EVERY edit, check the test "
    "status that comes back: it lists which tests now pass and which still fail. Work on "
    "one failing test at a time (run_tests accepts a 'target' to run only that test), and "
    "never finish while tests are still failing. Do not keep reading: once you understand "
    "a failure, make the edit. "
    "If edit_file says 'old' matches several places, use edit_lines with the line "
    "numbers from read_file instead. Several failing tests can point to different "
    "defects in the same function, so check every failing test against your change "
    "before you finish. Edits that create a syntax error or increase the number of "
    "failing tests are undone automatically, and you are told why. If an edit does not "
    "reduce the number of failing tests and is not needed, edit it back. "
    "When all tests pass, reply in English with a short summary: the root cause and "
    "the fix. "
    "STRICT RULE: Do not create dummy, placeholder, or trivial test files (e.g., containing "
    "`assert True`) simply to make pytest pass. You must locate, execute, and fix the actual " 
    "failing tests in the existing repository codebase."
)
SYSTEM_FEATURE = (
    "You are an agent that implements new features using test-driven development. "
    "Workflow: "
    "1) Read the existing code. In larger repos, use search_code to locate it, "
    "read_symbol to read a whole function or class, and read_file with start/end for "
    "anything else. "
    "2) Use add_test to write a NEW test file describing the requested behavior, "
    "including edge cases (for example an empty list or an even number of items). "
    "3) Run run_tests and confirm the new test FAILS before you implement anything. "
    "4) Implement the smallest change that satisfies the test, using edit_file or "
    "edit_lines. "
    "5) Check the test status that comes back after each edit until everything passes. "
    "Do not keep reading: once you understand the code, write the test. "
    "Never modify existing tests, and never hardcode expected values: the "
    "implementation must be correct for all inputs, not just the ones in your tests. "
    "Finish with a short summary in English: which test you added and what you changed."
)

SYSTEM_SCAFFOLD = (
    "You are an agent that builds a complete, working Python project from a natural-language "
    "specification, using test-driven development. You have no shell: you work only through "
    "your tools. Follow this workflow in order. "
    "1) Call submit_plan FIRST, before anything else: the folder structure, what each file does, "
    "the dependencies, how external services are handled in tests, and the test strategy. "
    "Keep the scope to what the specification asks for. "
    "2) Write requirements.txt with write_file (exact package names, optionally with a version "
    "bound) and call install_requirements. Only a fixed list of well-known packages is allowed. "
    "If a package cannot be installed, remove it and import it lazily inside the function that "
    "uses it. "
    "3) Write the tests FIRST with add_test, one test file per module, covering the normal "
    "path and the edge and error cases (invalid input, duplicates, wrong credentials, expired "
    "or tampered tokens, missing records...). Then call run_tests and confirm they FAIL. "
    "4) Implement each module with write_file (use edit_file or edit_lines for small fixes). "
    "After every edit you are told which tests pass and which still fail. "
    "5) Run run_tests until everything passes. If a test fails, read the traceback, find the "
    "root cause, fix the CODE, and run the tests again. Never weaken or delete a test just to "
    "make it pass, and never write placeholder or trivial tests. "
    "6) Write README.md with write_file: what the project does, the folder structure, how to "
    "install and run it, how to run the tests, the configuration (environment variables), and "
    "a section called 'Not verified' that lists everything the tests could not check. "
    "7) Finish with a short summary in English: what you built, what the tests cover, and what "
    "is not verified. "
    "RULES FOR DESIGN. External services such as PostgreSQL, Redis or third-party APIs are NOT "
    "available while testing, and tests must never need a running server or the network. "
    "Define a small interface (a repository or client class) for each external service. Write "
    "the real adapter (for example an asyncpg-based PostgreSQL repository with the SQL schema) "
    "in its own module, importing its driver lazily inside the function that connects. Test ALL "
    "the application logic through an in-memory or SQLite implementation of the same interface, "
    "injected through dependency injection (for FastAPI, dependency_overrides). The real adapter "
    "is NOT verified by the tests, so list it under 'Not verified' in the README. "
    "RULES FOR SECURITY. For password hashing call the bcrypt package directly (bcrypt.hashpw "
    "and bcrypt.checkpw) or use argon2-cffi; do not use passlib. Never hardcode secrets: read them from environment variables, and make "
    "the application refuse to start with a missing or default secret outside of tests. Never "
    "store plaintext passwords: hash them. Use parameterized SQL queries, never string "
    "concatenation. Do not log secrets or tokens. For JWT, set an expiration, verify the "
    "signature and the expiration, and pin the algorithm. "
    "Keep files small and focused. For async code, use pytest-asyncio or FastAPI's TestClient."
)

if SCAFFOLD:
    first = "(no tests yet: this is a new project)"
    print("[agent] scaffold mode: starting without tests; the plan comes first")
else:
    print("[agent] running the initial tests...")
    first = run_tests()
    print(first)
    if not FEATURE and not os.listdir(WORK):
        print("RESULT: ERROR\nThe repository is empty: there is nothing to fix. "
              "To build something from scratch use --scaffold \"<specification>\".")
        sys.exit(2)
    if not FEATURE and "no tests ran" in first:
        print("RESULT: ERROR\nNo tests were collected, so there is no failing test to fix. "
              "The case is empty or broken; check it with: python3 -m pytest <repo>")
        sys.exit(2)
FAILS = count_failures(first)
FAIL_NAMES = failing_names(first)
note_state(FAILS)

if SCAFFOLD:
    system = SYSTEM_SCAFFOLD
    existing = list_files()
    user = (f"Specification:\n{SCAFFOLD}\n\n"
            + (f"The working directory already contains these files (extend them, do not "
               f"overwrite them):\n{existing}" if existing
               else "The working directory is empty: build the project from scratch."))
elif FEATURE:
    system = SYSTEM_FEATURE
    user = (f"Feature request: {FEATURE}\n\n"
            f"Initial test status:\n{first}")
else:
    if first.startswith("PASSED"):
        sys.exit("The tests already pass, so there is nothing to fix.")
    system = SYSTEM_BUG
    user = (f"These tests are failing:\n{first}\n\n"
            f"Code around the failing lines:\n{failure_context(first)}\n\nFix the bug.")

messages = [{"role": "system", "content": system},
            {"role": "user", "content": user}]

model, seen_progress, last_progress_iter, nudged, seen_edits = MODEL, 0, 0, False, 0
for i in range(1, MAX_ITERS + 1):
    print(f"\n=== Iteration {i}/{MAX_ITERS} ===")
    if PROGRESS != seen_progress:
        seen_progress, last_progress_iter = PROGRESS, i - 1
    if SCAFFOLD and EDITS != seen_edits:        # writing new files is progress too
        seen_edits, last_progress_iter = EDITS, i - 1
    if ESCALATE_MODEL and model != ESCALATE_MODEL and i - last_progress_iter >= STUCK_AFTER:
        model = ESCALATE_MODEL
        print(f"[agent] no progress for {STUCK_AFTER} iterations: escalating to {model}")
    # stop endless re-reading: after repeated blocked calls or many reads in a row
    tools_now = ACTION_TOOLS if (BLOCK_STREAK >= 2 or READS_IN_ROW >= READ_LIMIT) else TOOLS
    if READS_IN_ROW == 0:
        nudged = False
    elif READS_IN_ROW >= READ_LIMIT and not nudged:
        messages.append({"role": "user", "content": (
            f"You have made {READS_IN_ROW} read-only calls in a row. Stop reading: make your best "
            "edit now with edit_lines or edit_file (you can read again after you see the test "
            "result), or call run_tests.")})
        nudged = True
    resp = client.chat.completions.create(
        model=model, messages=messages, tools=tools_now, max_tokens=MAX_TOKENS)

    choice = resp.choices[0]
    msg = choice.message
    details = getattr(resp.usage, "completion_tokens_details", None)
    print(f"[tokens] {resp.usage.total_tokens} (reasoning: "
          f"{getattr(details, 'reasoning_tokens', 0) or 0})")

    if choice.finish_reason == "length":
        print("[agent] reply cut off by max_tokens; asking again")
        messages.append({"role": "user", "content": "Your last reply was cut off before the tool call was complete. Reason briefly and call the tool again with ALL required arguments."})
        continue

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
            args = normalize(json.loads(tc.function.arguments or "{}"))
            print(f"[tool] {name}({json.dumps(args, ensure_ascii=False)[:150]})")
            result = run_tool(name, args)
        except Exception as e:
            result = f"ERROR: {e}"
        print(f"[result] {str(result)[:300]}")
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
else:
    print("\n[agent] reached the maximum number of iterations.")

print("\n=== FINAL VERIFICATION ===")
final = run_tests()
if (not SCAFFOLD and not final.startswith("PASSED") and BEST_FAILS is not None
        and snapshot() != BEST_FILES):
    print(f"[agent] not solved: going back to the best state seen ({BEST_FAILS} failing tests) "
          "and dropping the later edits that did not help")
    restore_best()
    final = run_tests()
print(final)

tampered = [r for r, h in ORIGINAL.items()
            if not os.path.exists(os.path.join(WORK, r)) or sha(os.path.join(WORK, r)) != h]
print("Original tests intact:", "YES" if not tampered else f"NO, modified: {tampered}")
if FEATURE or SCAFFOLD:
    print("New tests added:", sorted(NEW_TESTS) or "NONE")
ok = final.startswith("PASSED") and not tampered and (not (FEATURE or SCAFFOLD) or bool(NEW_TESTS))

SKIP_PATTERN = re.compile(r"pytest\.(mark\.(skip|skipif|xfail)|skip\(|xfail\()|collect_ignore|"
                          r"importorskip|--deselect|--ignore|pytest_collection_modifyitems|\bunittest\.skip")
if SCAFFOLD:
    problems = []
    for need in ("README.md", "requirements.txt"):
        if not os.path.exists(os.path.join(WORK, need)):
            problems.append(f"{need} is missing")
    if PLAN is None:
        problems.append("no plan was submitted")
    for rel in sorted(NEW_TESTS | {c for c in CREATED if os.path.basename(c) in CONFIG_FILES}):
        with open(os.path.join(WORK, rel), errors="ignore") as fh:
            if SKIP_PATTERN.search(fh.read()):
                problems.append(f"{rel} skips or ignores tests")
    readme = os.path.join(WORK, "README.md")
    if os.path.exists(readme):
        with open(readme, errors="ignore") as fh:
            if "not verified" not in fh.read().lower():
                problems.append("README.md has no 'Not verified' section")
    if re.search(r"\b\d+ skipped\b", final):
        problems.append("some tests were skipped, so they verify nothing")
    print("Files created:", len([c for c in CREATED | NEW_TESTS]))
    print("Checks:", "all passed" if not problems else "; ".join(problems))
    ok = ok and not problems
print("RESULT:", "SUCCESS" if ok else "REVIEW")

if SCAFFOLD:
    out_dir = CLI.out or os.path.join("scaffold_output",
                                      re.sub(r"[^a-z0-9]+", "-", SCAFFOLD.lower())[:40].strip("-") or "project")
    shutil.copytree(WORK, out_dir, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".deps", ".venv", ".git"))
    if PLAN:
        with open(os.path.join(out_dir, "PLAN.md"), "w") as fh:
            fh.write("# Plan\n\n" + PLAN + "\n")
    print(f"\n=== PROJECT SAVED TO: {os.path.abspath(out_dir)} ===")
    print("To run it: pip install -r requirements.txt && python3 -m pytest")
    print("\n=== FILES ===")
    for root, dirs, files in os.walk(WORK):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in sorted(files):
            p = os.path.join(root, f)
            with open(p, errors="ignore") as fh:
                print(f"{os.path.relpath(p, WORK)}  ({len(fh.read().splitlines())} lines)")
    sys.exit(0)

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