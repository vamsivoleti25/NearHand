"""Generate Nearhand logo options with Muse Image (Meta Model API), then install the one you pick.

    python -m scripts.make_logo                 # 6 options -> assets/logo_options/option_1.png ...
    python -m scripts.make_logo --only 2        # just concept 2 again
    python -m scripts.make_logo --pick 3        # use option_3 as the app logo + browser icon

Needs MODEL_API_KEY in .env. Each image is one Muse Image call.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
OPTIONS = ROOT / "assets" / "logo_options"
ASSETS = ROOT / "assets"
MODEL = "muse-image-1.0"

STYLE = ("Flat vector app icon, centered on a plain warm off-white background (#FAF7F2), generous padding, "
         "deep violet (#6D28D9) as the main color with one small warm amber (#F59E0B) accent, "
         "simple geometric shapes, rounded corners, bold and legible at 32 pixels, no text, no letters, "
         "no gradients, no shadows, no photo realism.")

CONCEPTS = [
    "A friendly open hand whose palm forms a map location pin, suggesting help from nearby.",
    "Two simple hands meeting in a high five that together form the shape of a house roof.",
    "A rounded square calendar with one cell filled by a small hand icon, suggesting a shift being covered.",
    "A location pin with a small clock face inside it, the clock hands pointing to a shift start.",
    "A neighborhood of three tiny rounded houses, the middle one with a hand-shaped door, cozy and warm.",
    "A single lowercase-n shape made from two hands forming an arch, minimal monogram mark.",
]


def client():
    from openai import OpenAI
    load_dotenv(ROOT / ".env")
    key = os.getenv("MODEL_API_KEY")
    if not key:
        sys.exit("MODEL_API_KEY is not set in .env")
    return OpenAI(base_url="https://api.meta.ai/v1", api_key=key, timeout=180)


def generate(c, prompt: str) -> bytes:
    """Try the OpenAI-style images endpoint with a few parameter sets; return PNG bytes."""
    attempts = [
        {"size": "1024x1024", "response_format": "b64_json"},
        {"size": "1024x1024"},
        {"extra_body": {"aspect_ratio": "1:1"}},
        {},
    ]
    last = None
    for extra in attempts:
        try:
            r = c.images.generate(model=MODEL, prompt=prompt, n=1, **extra)
            d = r.data[0]
            if getattr(d, "b64_json", None):
                return base64.b64decode(d.b64_json)
            if getattr(d, "url", None):
                return urllib.request.urlopen(d.url, timeout=60).read()
        except Exception as e:  # keep the message: it tells us which parameter the API rejected
            last = e
    raise RuntimeError(f"Muse Image call failed: {last}")


def pick(n: int) -> None:
    from PIL import Image
    src = OPTIONS / f"option_{n}.png"
    if not src.exists():
        sys.exit(f"{src} not found; generate options first")
    img = Image.open(src).convert("RGBA")
    ASSETS.mkdir(exist_ok=True)
    img.resize((512, 512), Image.LANCZOS).save(ASSETS / "logo.png")
    img.resize((64, 64), Image.LANCZOS).save(ASSETS / "favicon.png")
    print(f"Installed option {n}: assets/logo.png (512px) and assets/favicon.png (64px).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pick", type=int)
    ap.add_argument("--only", type=int, help="regenerate one concept (1-6)")
    args = ap.parse_args()
    if args.pick:
        return pick(args.pick)
    c = client()
    OPTIONS.mkdir(parents=True, exist_ok=True)
    todo = [args.only] if args.only else range(1, len(CONCEPTS) + 1)
    for i in todo:
        prompt = f"{CONCEPTS[i - 1]} {STYLE}"
        print(f"option {i}: generating…", flush=True)
        try:
            (OPTIONS / f"option_{i}.png").write_bytes(generate(c, prompt))
            print(f"  saved assets/logo_options/option_{i}.png")
        except Exception as e:
            print(f"  failed: {str(e)[:400]}")
    print("\nOpen assets/logo_options/ to compare, then: python -m scripts.make_logo --pick N")


if __name__ == "__main__":
    main()
