"""Homepage illustrations with Muse Image (Meta Model API), in the same style as the logo.

    python -m scripts.make_art              # hero (wide) + one picture for each path in
    python -m scripts.make_art --only hero  # just one again

Saves to assets/home/<name>.png. Uses META_API_KEY from .env (falls back to MODEL_API_KEY).
"""

from __future__ import annotations

import argparse
import base64
import urllib.request

from scripts.make_logo import ASSETS, MODEL, client

STYLE = ("Flat vector illustration, warm and friendly, simple geometric shapes with rounded corners, "
         "deep violet (#6D28D9) as the main color, warm amber (#F59E0B) accents, soft slate and off-white "
         "(#FAF7F2) background, generous empty space, no text, no letters, no logos, no brand names, "
         "no gradients, no photo realism. People are simple, diverse, faceless stylized figures.")

ART = {
    "hero": ("wide", "A cozy small-town main street at morning: a corner café with an awning, a small restaurant "
                     "and a bakery, two people walking toward the café with a phone and a tote bag, short "
                     "dotted paths connecting nearby houses to the shops, a small map pin above the café."),
    "staff": ("square", "A café owner behind a counter looking at a simple weekly calendar on a tablet; a few "
                        "calendar cells glow amber as open shifts and one fills in violet."),
    "shifts": ("square", "A young part-timer at home holding a phone that shows one shift offer card with a "
                         "check mark, a small map pin and a short dotted path to a nearby café in the window."),
}

SIZES = {"wide": [{"size": "1536x1024"}, {"extra_body": {"aspect_ratio": "16:9"}}, {"size": "1024x1024"}],
         "square": [{"size": "1024x1024"}, {"extra_body": {"aspect_ratio": "1:1"}}, {}]}


def generate(c, prompt: str, shape: str) -> bytes:
    last = None
    for extra in SIZES[shape]:
        try:
            d = c.images.generate(model=MODEL, prompt=prompt, n=1, **extra).data[0]
            if getattr(d, "b64_json", None):
                return base64.b64decode(d.b64_json)
            if getattr(d, "url", None):
                return urllib.request.urlopen(d.url, timeout=60).read()
        except Exception as e:  # the message says which parameter the API rejected
            last = e
    raise RuntimeError(f"Muse Image call failed: {last}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=list(ART))
    args = ap.parse_args()
    out = ASSETS / "home"
    out.mkdir(parents=True, exist_ok=True)
    c = client()
    for name, (shape, scene) in ART.items():
        if args.only and name != args.only:
            continue
        print(f"{name}: generating…", flush=True)
        try:
            (out / f"{name}.png").write_bytes(generate(c, f"{scene} {STYLE}", shape))
            print(f"  saved assets/home/{name}.png")
        except Exception as e:
            print(f"  failed: {str(e)[:400]}")
    print("\nOpen assets/home/ to check them; re-run one with --only NAME if it's off.")


if __name__ == "__main__":
    main()
