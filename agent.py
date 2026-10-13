import os, sys, json, shutil, subprocess, tempfile, difflib
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(
    base_url="https://api.tokenfactory.nebius.com/v1/",
    api_key=os.environ["NEBIUS_API_KEY"],
)
MODEL = "nvidia/nemotron-3-super-120b-a12b"
MAX_ITERS = 12
MAX_OUT = 3000  # recorta salidas largas para ahorrar tokens

SRC = sys.argv[1] if len(sys.argv) > 1 else "demo_repo"
WORK = tempfile.mkdtemp(prefix="agent_")
shutil.copytree(SRC, WORK, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
print(f"[agente] trabajando en una copia: {WORK}")


def safe(path):
    full = os.path.realpath(os.path.join(WORK, path))
    if not full.startswith(os.path.realpath(WORK) + os.sep):
        raise ValueError("ruta fuera del repo")
    return full


def list_files():
    out = []
    for root, dirs, files in os.walk(WORK):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".pytest_cache")]
        for f in files:
            out.append(os.path.relpath(os.path.join(root, f), WORK))
    return "\n".join(sorted(out))


def read_file(path):
    with open(safe(path)) as f:
        return f.read()[:MAX_OUT]


def edit_file(path, old, new):
    if os.path.basename(path).startswith("test_"):
        return "ERROR: no se permite editar archivos de test."
    full = safe(path)
    with open(full) as f:
        content = f.read()
    if content.count(old) != 1:
        return f"ERROR: 'old' aparece {content.count(old)} veces; debe aparecer exactamente 1."
    with open(full, "w") as f:
        f.write(content.replace(old, new))
    return "OK: archivo editado."


def run_tests():
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                           cwd=WORK, capture_output=True, text=True, timeout=60)
        out = r.stdout + r.stderr
        status = "PASSED" if r.returncode == 0 else "FAILED"
        return f"{status}\n{out[-MAX_OUT:]}"
    except subprocess.TimeoutExpired:
        return "FAILED: timeout de 60s"


def obj(props, req):
    return {"type": "object", "properties": props, "required": req}

S = {"type": "string"}
TOOLS = [
    {"type": "function", "function": {"name": "list_files",
        "description": "Lista los archivos del repo.", "parameters": obj({}, [])}},
    {"type": "function", "function": {"name": "read_file",
        "description": "Lee un archivo del repo.", "parameters": obj({"path": S}, ["path"])}},
    {"type": "function", "function": {"name": "edit_file",
        "description": "Reemplaza el texto exacto 'old' por 'new' en un archivo (old debe aparecer una sola vez). No edita tests.",
        "parameters": obj({"path": S, "old": S, "new": S}, ["path", "old", "new"])}},
    {"type": "function", "function": {"name": "run_tests",
        "description": "Corre pytest en el repo y devuelve el resultado.", "parameters": obj({}, [])}},
]
FUNCS = {"list_files": list_files, "read_file": read_file,
         "edit_file": edit_file, "run_tests": run_tests}

SYSTEM = (
    "Eres un agente que arregla bugs. Recibes un repo con un test que falla. "
    "Flujo: lee el test y el código, diagnostica la causa en una frase, aplica el "
    "cambio MÍNIMO con edit_file y verifica con run_tests. Nunca modifiques los tests. "
    "Cuando los tests pasen, responde con un resumen breve: causa y arreglo."
)

print("[agente] corriendo tests iniciales...")
first = run_tests()
print(first)
if first.startswith("PASSED"):
    sys.exit("Los tests ya pasan, no hay nada que arreglar.")

messages = [
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": f"Estos tests fallan:\n{first}\nArréglalo."},
]

for i in range(1, MAX_ITERS + 1):
    print(f"\n=== Iteración {i}/{MAX_ITERS} ===")
    resp = client.chat.completions.create(
        model=MODEL, messages=messages, tools=TOOLS, max_tokens=4000)
    msg = resp.choices[0].message
    print(f"[tokens] {resp.usage.total_tokens} (razonamiento: "
          f"{resp.usage.completion_tokens_details.reasoning_tokens})")

    if not msg.tool_calls:
        print("\n[agente] resumen final:\n" + (msg.content or "(sin texto)"))
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
        args = json.loads(tc.function.arguments or "{}")
        print(f"[herramienta] {name}({json.dumps(args, ensure_ascii=False)[:150]})")
        try:
            result = FUNCS[name](**args)
        except Exception as e:
            result = f"ERROR: {e}"
        print(f"[resultado] {str(result)[:300]}")
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
else:
    print("\n[agente] se alcanzó el máximo de iteraciones.")

print("\n=== VERIFICACIÓN FINAL ===")
print(run_tests())

print("\n=== DIFF ===")
for root, _, files in os.walk(WORK):
    for f in files:
        if not f.endswith(".py"):
            continue
        new_path = os.path.join(root, f)
        old_path = os.path.join(SRC, os.path.relpath(new_path, WORK))
        if os.path.exists(old_path):
            a, b = open(old_path).read().splitlines(), open(new_path).read().splitlines()
            d = list(difflib.unified_diff(a, b, f"antes/{f}", f"despues/{f}", lineterm=""))
            if d:
                print("\n".join(d))