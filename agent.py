import os, sys, json, shutil, subprocess, tempfile, difflib, hashlib, argparse
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(
    base_url="https://api.tokenfactory.nebius.com/v1/",
    api_key=os.environ["NEBIUS_API_KEY"],
)
MODEL = "nvidia/nemotron-3-super-120b-a12b"
MAX_ITERS = 12
MAX_OUT = 3000

ap = argparse.ArgumentParser()
ap.add_argument("repo", nargs="?", default="demo_repo")
ap.add_argument("--feature", help="Descripción de una función nueva a implementar")
CLI = ap.parse_args()
SRC, FEATURE = CLI.repo, CLI.feature

WORK = tempfile.mkdtemp(prefix="agent_")
shutil.copytree(SRC, WORK, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
print(f"[agente] modo: {'función nueva' if FEATURE else 'arreglar bug'}")
print(f"[agente] trabajando en una copia: {WORK}")

CONFIG_FILES = {"conftest.py", "pytest.ini", "setup.cfg", "tox.ini", "pyproject.toml"}
NEW_TESTS = set()


def is_protected(rel):
    base = os.path.basename(rel)
    return base.startswith("test_") or base.endswith("_test.py") or base in CONFIG_FILES


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# Huellas de los tests originales, para detectar trampa al final
ORIGINAL = {}
for _root, _dirs, _files in os.walk(WORK):
    for _f in _files:
        _rel = os.path.relpath(os.path.join(_root, _f), WORK)
        if is_protected(_rel):
            ORIGINAL[_rel] = sha(os.path.join(WORK, _rel))


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
    full = safe(path)
    rel = os.path.relpath(full, WORK)
    if is_protected(rel) and rel not in NEW_TESTS:
        return "ERROR: no se permite editar tests originales ni archivos de configuración de pytest."
    with open(full) as f:
        content = f.read()
    if content.count(old) != 1:
        return f"ERROR: 'old' aparece {content.count(old)} veces; debe aparecer exactamente 1."
    with open(full, "w") as f:
        f.write(content.replace(old, new))
    return "OK: archivo editado."


def add_test(path, content):
    base = os.path.basename(path)
    if not (base.startswith("test_") and base.endswith(".py")):
        return "ERROR: el archivo debe llamarse test_*.py"
    full = safe(path)
    if os.path.exists(full):
        return "ERROR: ese archivo ya existe; no se pueden sobrescribir tests."
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    NEW_TESTS.add(os.path.relpath(full, WORK))
    return "OK: test creado."


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
        "description": "Reemplaza el texto exacto 'old' por 'new' en un archivo (old debe aparecer una sola vez). No edita tests originales.",
        "parameters": obj({"path": S, "old": S, "new": S}, ["path", "old", "new"])}},
    {"type": "function", "function": {"name": "add_test",
        "description": "Crea un archivo de test NUEVO (test_*.py). No puede sobrescribir archivos existentes.",
        "parameters": obj({"path": S, "content": S}, ["path", "content"])}},
    {"type": "function", "function": {"name": "run_tests",
        "description": "Corre pytest en el repo y devuelve el resultado.", "parameters": obj({}, [])}},
]
FUNCS = {"list_files": list_files, "read_file": read_file, "edit_file": edit_file,
         "add_test": add_test, "run_tests": run_tests}

SYSTEM_BUG = (
    "Eres un agente que arregla bugs. Recibes un repo con tests que fallan. "
    "Flujo: lee los tests y el código, diagnostica la causa en una frase, aplica el "
    "cambio MÍNIMO con edit_file y verifica con run_tests. Nunca modifiques los tests "
    "existentes ni hardcodees resultados esperados. Cuando todo pase, responde con un "
    "resumen breve: causa y arreglo."
)
SYSTEM_FEATURE = (
    "Eres un agente que implementa funciones nuevas con TDD. Flujo: "
    "1) lee el código existente; "
    "2) escribe con add_test un test NUEVO que describa el comportamiento pedido, "
    "incluyendo casos borde (por ejemplo lista vacía); "
    "3) corre run_tests y confirma que el test nuevo FALLA; "
    "4) implementa lo mínimo con edit_file; "
    "5) corre run_tests hasta que todo pase. "
    "Nunca modifiques tests existentes ni hardcodees resultados. "
    "Termina con un resumen breve: qué test agregaste y qué cambiaste."
)

print("[agente] corriendo tests iniciales...")
first = run_tests()
print(first)

if FEATURE:
    system = SYSTEM_FEATURE
    user = (f"Petición de función nueva: {FEATURE}\n\n"
            f"Estado inicial de los tests:\n{first}")
else:
    if first.startswith("PASSED"):
        sys.exit("Los tests ya pasan, no hay nada que arreglar.")
    system = SYSTEM_BUG
    user = f"Estos tests fallan:\n{first}\nArréglalo."

messages = [{"role": "system", "content": system},
            {"role": "user", "content": user}]

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
        try:
            args = json.loads(tc.function.arguments or "{}")
            print(f"[herramienta] {name}({json.dumps(args, ensure_ascii=False)[:150]})")
            result = FUNCS[name](**args)
        except Exception as e:
            result = f"ERROR: {e}"
        print(f"[resultado] {str(result)[:300]}")
        messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
else:
    print("\n[agente] se alcanzó el máximo de iteraciones.")

print("\n=== VERIFICACIÓN FINAL ===")
final = run_tests()
print(final)

tampered = [r for r, h in ORIGINAL.items()
            if not os.path.exists(os.path.join(WORK, r)) or sha(os.path.join(WORK, r)) != h]
print("Tests originales intactos:", "SÍ" if not tampered else f"NO, modificados: {tampered}")
if FEATURE:
    print("Tests nuevos agregados:", sorted(NEW_TESTS) or "NINGUNO")
ok = final.startswith("PASSED") and not tampered and (not FEATURE or bool(NEW_TESTS))
print("RESULTADO:", "ÉXITO" if ok else "REVISAR")

print("\n=== CAMBIOS ===")
for root, dirs, files in os.walk(WORK):
    dirs[:] = [d for d in dirs if d not in ("__pycache__", ".pytest_cache")]
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
            d = list(difflib.unified_diff(a, b, f"antes/{rel}", f"despues/{rel}", lineterm=""))
            if d:
                print("\n".join(d))
        else:
            print(f"\n+++ ARCHIVO NUEVO: {rel}")
            print("\n".join("+" + line for line in b))