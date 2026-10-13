import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(
    base_url="https://api.tokenfactory.nebius.com/v1/",
    api_key=os.environ["NEBIUS_API_KEY"],
)
MODEL = "nvidia/nemotron-3-super-120b-a12b"  # copia el ID exacto del playground

resp = client.chat.completions.create(
    model=MODEL,
    messages=[{"role": "user", "content": "Di hola en una frase."}],
    max_tokens=1000,
)
print(resp.choices[0].message.content)
print(resp.usage)