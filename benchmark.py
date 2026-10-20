import json, re, subprocess, sys, time

cases = json.load(open("cases.json"))
rows = ["| Case | Result | Original tests intact | Iterations | Tokens | Time (s) |",
        "|---|---|---|---|---|---|"]

for c in cases:
    cmd = [sys.executable, "agent.py", c["repo"]]
    if c.get("feature"):
        cmd += ["--feature", c["feature"]]
    print(f"Running {c['name']}...", flush=True)
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        out = proc.stdout
    except subprocess.TimeoutExpired:
        out = ""
    secs = round(time.time() - t0)
    result = re.findall(r"RESULT: (\S+)", out)
    intact = re.findall(r"Original tests intact: (\S+)", out)
    iters = len(re.findall(r"=== Iteration", out))
    tokens = sum(int(t) for t in re.findall(r"\[tokens\] (\d+)", out))
    rows.append(f"| {c['name']} | {result[-1] if result else 'ERROR'} | "
                f"{intact[-1] if intact else '?'} | {iters} | {tokens} | {secs} |")

table = "\n".join(rows)
print("\n" + table)
open("results.md", "w").write(table + "\n")
print("\nSaved to results.md")
