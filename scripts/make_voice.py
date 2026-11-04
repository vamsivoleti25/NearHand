"""Read the demo narration (scripts/narration.json) in your own ElevenLabs voice, one MP3 per beat.

    # in .env (never commit it):
    #   ELEVENLABS_API_KEY=...      from elevenlabs.io → Profile → API keys
    #   ELEVENLABS_VOICE_ID=...     your cloned voice: elevenlabs.io → Voices → your voice → ID
    python scripts/make_voice.py            # writes video/voice/01_intro.mp3, 02_problem.mp3, ...
    python scripts/make_voice.py --force    # re-make every line (otherwise existing files are kept)
    python scripts/make_voice.py --only change   # re-make one line after editing its words

Uses only the Python standard library (plus python-dotenv, already installed).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "video" / "voice"
URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128"


def speak(text: str, voice: str, key: str, model: str) -> bytes:
    body = json.dumps({"text": text, "model_id": model,
                       "voice_settings": {"stability": 0.5, "similarity_boost": 0.85, "style": 0.15,
                                          "use_speaker_boost": True}}).encode()
    req = urllib.request.Request(URL.format(voice=voice), data=body, method="POST",
                                 headers={"xi-api-key": key, "Content-Type": "application/json",
                                          "Accept": "audio/mpeg"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--model", default=os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2"))
    a = ap.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    key, voice = os.getenv("ELEVENLABS_API_KEY"), os.getenv("ELEVENLABS_VOICE_ID")
    if not key or not voice:
        sys.exit("Add ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID to your .env file first (see the top of this file).")

    beats = json.loads((ROOT / "scripts" / "narration.json").read_text())["beats"]
    OUT.mkdir(parents=True, exist_ok=True)
    for n, b in enumerate(beats, 1):
        f = OUT / f"{n:02d}_{b['id']}.mp3"
        if a.only and b["id"] != a.only:
            continue
        if f.exists() and not (a.force or a.only):
            print(f"  kept   {f.name}")
            continue
        try:
            f.write_bytes(speak(b["text"], voice, key, a.model))
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            sys.exit(f"ElevenLabs said {e.code} for '{b['id']}': {detail}\n"
                     "Check the API key and voice ID; for a model error, try --model eleven_v4.")
        print(f"  made   {f.name}")
    print(f"Done: {OUT}")


if __name__ == "__main__":
    main()
