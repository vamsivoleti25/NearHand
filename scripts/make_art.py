"""Homepage illustrations with Muse Image (Meta Model API), in the same style as the logo.

    python -m scripts.make_art --comic      # comic-style pictures (the site look): hero, both doors, log-in image
    python -m scripts.make_art --comic --only hero   # just one again
    python -m scripts.make_art --photo      # the same scenes as photo-style pictures
    python -m scripts.make_art              # the original flat illustrations

Saves to assets/home/<name>.png. Uses META_API_KEY from .env (falls back to MODEL_API_KEY).
Every person in these pictures is generated and fictional.
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

PHOTO_STYLE = ("Natural, candid documentary-style photograph, soft morning daylight, realistic, shallow depth "
               "of field, warm neutral tones with a few deep violet (#6D28D9) accents in clothing or decor, "
               "clean and uncluttered, no text, no signs with words, no logos, no brand names, no watermarks. "
               "Everyone shown is an ordinary, fictional adult, not a celebrity.")

COMIC_STYLE = ("Modern comic-book illustration: confident black ink outlines, flat cel-shaded colors with a little "
               "halftone dot texture in the shadows, expressive friendly characters, clean composition with room "
               "to breathe. Palette: deep violet (#6D28D9) and warm amber (#F59E0B) accents, soft cream and slate "
               "neutrals, white background around the scene. No speech bubbles, no captions, no text, no letters, "
               "no logos, no brand names. Everyone shown is an ordinary, fictional adult.")

PHOTOS = {  # the scenes for the site look, drawn in COMIC_STYLE or PHOTO_STYLE
    "hero": ("wide", "A small neighborhood café in the morning: a barista in a violet apron hands a coffee across "
                     "the counter to a smiling customer, plants and a chalkboard without words, sunlight from "
                     "the street window."),
    "staff": ("wide", "A café owner in an apron leaning on the counter, checking a weekly schedule on a tablet, "
                      "calm and confident, empty café before opening."),
    "shifts": ("wide", "A young adult sitting by a sunny window at home, smiling at their phone, a small café "
                       "visible across the street outside."),
    "signin": ("square", "Close-up of a barista's hands pouring latte art into a ceramic cup on a wooden counter, "
                         "a violet apron in soft focus."),
}

SIZES = {"wide": [{"size": "1536x1024"}, {"extra_body": {"aspect_ratio": "16:9"}}, {"size": "1024x1024"}],
         "square": [{"size": "1024x1024"}, {"extra_body": {"aspect_ratio": "1:1"}}, {}]}


def trim(png: bytes, pad: int = 6) -> bytes:
    """Crop the empty white margin the model sometimes leaves around a scene, so it fills its space."""
    import io
    import numpy as np
    from PIL import Image
    im = Image.open(io.BytesIO(png)).convert("RGB")
    ink = np.asarray(im).min(axis=2) < 235
    rows, cols = np.where(ink.mean(axis=1) > 0.02)[0], np.where(ink.mean(axis=0) > 0.02)[0]
    if not len(rows) or not len(cols):
        return png
    box = (max(0, cols[0] - pad), max(0, rows[0] - pad), min(im.width, cols[-1] + pad + 1),
           min(im.height, rows[-1] + pad + 1))
    out = io.BytesIO()
    im.crop(box).save(out, "PNG")
    return out.getvalue()


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
    ap.add_argument("--only", choices=sorted(set(ART) | set(PHOTOS)))
    ap.add_argument("--photo", action="store_true", help="photo-style pictures")
    ap.add_argument("--comic", action="store_true", help="comic-style pictures (the site look)")
    ap.add_argument("--trim", action="store_true", help="just crop white margins off the existing pictures")
    args = ap.parse_args()
    if args.trim:
        for f in sorted((ASSETS / "home").glob("*.png")):
            f.write_bytes(trim(f.read_bytes()))
            print(f"trimmed {f.name}")
        return
    out = ASSETS / "home"
    out.mkdir(parents=True, exist_ok=True)
    c = client()
    scenes, style = ((PHOTOS, COMIC_STYLE) if args.comic else (PHOTOS, PHOTO_STYLE) if args.photo
                     else (ART, STYLE))
    for name, (shape, scene) in scenes.items():
        if args.only and name != args.only:
            continue
        print(f"{name}: generating…", flush=True)
        try:
            (out / f"{name}.png").write_bytes(trim(generate(c, f"{scene} {style}", shape)))
            print(f"  saved assets/home/{name}.png")
        except Exception as e:
            print(f"  failed: {str(e)[:400]}")
    print("\nOpen assets/home/ to check them; re-run one with --only NAME if it's off.")


if __name__ == "__main__":
    main()
