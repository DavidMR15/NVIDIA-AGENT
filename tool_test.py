import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(
    base_url="https://api.tokenfactory.nebius.com/v1/",
    api_key=os.environ["NEBIUS_API_KEY"],
)
MODEL = "nvidia/nemotron-3-super-120b-a12b"

tools = [{
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Lee un archivo del proyecto y devuelve su contenido.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}]

resp = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "Lee el archivo calc.py y dime qué hace."}],
    tools=tools,
    max_tokens=2000,
)
msg = resp.choices[0].message
print("TOOL CALLS:", msg.tool_calls)
print("TEXTO:", msg.content)
print(resp.usage)