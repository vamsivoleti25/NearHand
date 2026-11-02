"""Generate Nearhand logo options with Muse Image (Meta Model API), then install the one you pick.

    python -m scripts.make_logo                 # 6 picture marks -> assets/logo_options/option_1.png ...
    python -m scripts.make_logo --set monogram  # 6 letter marks (a bold "n") -> assets/logo_options/monogram_1.png ...
    python -m scripts.make_logo --only 2        # just concept 2 again (add --set monogram for those)
    python -m scripts.make_logo --pick 3        # use option_3 as the app logo + browser icon
    python -m scripts.make_logo --set monogram --pick 3   # use monogram_3
    python -m scripts.make_logo --set story     # 6 letter marks that tell the story -> story_1.png ...

Uses META_API_KEY from .env (falls back to MODEL_API_KEY). Each image is one Muse Image call.
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


MONO_STYLE = ("Flat vector logo mark on a plain white background, centered with generous padding, deep violet "
              "(#6D28D9) with at most one small warm amber (#F59E0B) accent, geometric, crisp edges, thick even "
              "strokes, bold and legible at 32 pixels, no other letters or words, no gradients, no shadows, "
              "no 3D, no photo realism.")

MONOGRAMS = [
    "A single bold geometric lowercase letter n with thick rounded strokes, and a small amber dot just above "
    "its right leg, like the dot of a map pin.",
    "A solid violet rounded-square app icon with a bold white lowercase letter n in the middle and a small "
    "amber dot at the top right corner.",
    "A lowercase letter n drawn as one continuous thick line, like a short path, ending in a small amber "
    "location dot.",
    "A lowercase letter n whose arch is shaped like the roof of a small shop, with a tiny amber window, "
    "minimal and geometric.",
    "A violet circle with a bold lowercase letter n cut out of it in white, and a small amber dot on the "
    "edge of the circle.",
    "A bold lowercase letter n made of two rounded vertical bars joined by an arch, the right bar slightly "
    "taller and topped with a small amber dot, modern and friendly.",
]

STORY = [  # letter marks that tell the product story: a gap in the week, filled from nearby (amber = the help)
    "A bold geometric lowercase letter n in deep violet with one small square block missing from the top of "
    "its arch, and a small amber block sliding into that gap to fill it.",
    "A lowercase letter n built from seven short rounded vertical bars side by side, like the seven days of a "
    "week, rising and falling to form the arch of the n; six bars violet, one bar amber.",
    "A bold lowercase letter n in deep violet whose right leg ends in the point of a map pin, with a small "
    "amber dot inside the pin head.",
    "A bold lowercase letter n in deep violet shaped like the top of a calendar, with a small grid of three "
    "rounded cells under its arch, one cell filled amber.",
    "A bold lowercase letter n in deep violet shaped like an archway, with a small amber dot walking into it "
    "along a short dotted path from the left.",
    "A bold geometric lowercase letter n in deep violet with a gap at the top of its arch, filled by a small "
    "amber map pin that fits the gap exactly.",
]


def client():
    from openai import OpenAI
    load_dotenv(ROOT / ".env")
    name = "META_API_KEY" if os.getenv("META_API_KEY") else "MODEL_API_KEY"
    key = os.getenv(name)
    if not key:
        sys.exit("Neither META_API_KEY nor MODEL_API_KEY is set in .env")
    print(f"Using {name}")
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


def clear_background(img):
    """Make the plain background (whatever colour sits in the corners) transparent, with soft edges,
    so the mark sits cleanly on the site's white pages."""
    import numpy as np
    from PIL import Image
    a = np.asarray(img.convert("RGBA")).astype(float)
    corners = np.array([a[0, 0, :3], a[0, -1, :3], a[-1, 0, :3], a[-1, -1, :3]]).mean(axis=0)
    dist = np.sqrt(((a[..., :3] - corners) ** 2).sum(axis=2))
    a[..., 3] = np.clip((dist - 6) / 24, 0, 1) * 255
    return Image.fromarray(a.astype("uint8"), "RGBA")


def trim_square(img, pad_ratio: float = 0.06):
    """Crop to the mark itself (plus a little padding), as a square, so it fills the header slot."""
    from PIL import Image
    box = img.getchannel("A").point(lambda v: 255 if v > 20 else 0).getbbox()
    if not box:
        return img
    w, h = box[2] - box[0], box[3] - box[1]
    side = int(max(w, h) * (1 + 2 * pad_ratio))
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(img.crop(box), ((side - w) // 2, (side - h) // 2))
    return out


def pick(n: int, prefix: str = "option") -> None:
    from PIL import Image
    src = OPTIONS / f"{prefix}_{n}.png"
    if not src.exists():
        sys.exit(f"{src} not found; generate options first")
    img = trim_square(clear_background(Image.open(src)))
    ASSETS.mkdir(exist_ok=True)
    img.resize((512, 512), Image.LANCZOS).save(ASSETS / "logo.png")
    img.resize((64, 64), Image.LANCZOS).save(ASSETS / "favicon.png")
    print(f"Installed {prefix} {n}: assets/logo.png (512px) and assets/favicon.png (64px), transparent background.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pick", type=int)
    ap.add_argument("--only", type=int, help="regenerate one concept (1-6)")
    ap.add_argument("--set", choices=["picture", "monogram", "story"], default="picture")
    args = ap.parse_args()
    prefix, concepts, style = {"picture": ("option", CONCEPTS, STYLE),
                               "monogram": ("monogram", MONOGRAMS, MONO_STYLE),
                               "story": ("story", STORY, MONO_STYLE)}[args.set]
    if args.pick:
        return pick(args.pick, prefix)
    c = client()
    OPTIONS.mkdir(parents=True, exist_ok=True)
    todo = [args.only] if args.only else range(1, len(concepts) + 1)
    for i in todo:
        prompt = f"{concepts[i - 1]} {style}"
        print(f"{prefix} {i}: generating…", flush=True)
        try:
            (OPTIONS / f"{prefix}_{i}.png").write_bytes(generate(c, prompt))
            print(f"  saved assets/logo_options/{prefix}_{i}.png")
        except Exception as e:
            print(f"  failed: {str(e)[:400]}")
    more = f" --set {args.set}" if args.set != "picture" else ""
    print(f"\nOpen assets/logo_options/ to compare, then: python -m scripts.make_logo{more} --pick N")


if __name__ == "__main__":
    main()
