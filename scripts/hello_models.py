import os, time
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
PROVIDERS = {
    "meta":      ("https://api.meta.ai/v1", "MODEL_API_KEY", "muse-spark-1.3"),
    "baseten":   ("https://inference.baseten.co/v1", "BASETEN_API_KEY", "meta-models/muse-spark-open-1026"),
    "fireworks": ("https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY",
                  "accounts/fireworks/models/muse-spark-open"),
}
for name, (url, key, model) in PROVIDERS.items():
    try:
        client = OpenAI(base_url=url, api_key=os.environ[key])
        t0 = time.time()
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
            reasoning_effort="low",
            max_tokens=2000,
        )
        print(f"{name:10s} {time.time() - t0:5.1f}s  {r.choices[0].message.content!r}  {r.usage}")
    except Exception as e:
        print(f"{name:10s} FAILED: {e}")
