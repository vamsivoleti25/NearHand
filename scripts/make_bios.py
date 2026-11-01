"""Write each seeded part-timer's work history in their own (messy) words, with Muse Spark Open.

    python -m scripts.make_bios            # all part-timers without a bio (~150 calls, about $0.50)
    python -m scripts.make_bios --limit 5  # try a few first

The structured record is the gold answer; the bio is what profile extraction must recover.
That makes data/seed/part_timers.json an eval set for parse_experience for free.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field

from core import llm

SEED = Path(__file__).resolve().parent.parent / "data" / "seed" / "part_timers.json"
TYPE_WORDS = {"cafe": "coffee shops / cafés", "restaurant": "restaurants", "quick_service": "fast food / fast casual",
              "bakery": "bakeries", "retail": "retail or convenience stores"}
STYLES = ["casual and short, lowercase, a couple of typos", "chatty, one long run-on paragraph",
          "bullet-ish fragments separated by commas", "polite and a bit formal", "texting style with abbreviations"]


class Bio(BaseModel):
    bio: str = Field(description="What they typed into the 'Tell us about your experience' box, 40-110 words")


SYSTEM = """You write realistic text that a part-time hourly worker types into a job app's
"Tell us about your work experience" box. Write in first person, in the style asked for.
Include every role with roughly how long they did it (vague phrasing is fine: "about 2 yrs", "a summer"),
the kinds of places they worked (invent plausible local business names, never real chains),
any certifications listed, and their asking pay. Do not mention their name, address or availability.
Do not add roles, certifications or experience that are not listed.
Write exactly one version: no numbering, no alternatives."""


def describe(p: dict, style: str) -> str:
    roles = "; ".join(f"{r['role'].replace('_', ' ')} for {r['years']:g} years" for r in p["roles"])
    certs = {"keyholder": "has opened and closed a store (keyholder)", "food_handler": "food handler card",
             "food_safety_manager": "ServSafe Manager certification", "alcohol_service": "TIPS alcohol certification"}
    skills = ", ".join(certs[s] for s in p["skills"]) or "none"
    places = ", ".join(TYPE_WORDS[t] for t in p["business_types"]) or "various"
    return (f"Roles: {roles}\nPlaces: {places}\nCertifications: {skills}\n"
            f"Asking pay: ${p['expected_rate_cents'] / 100:.2f}/hr\nStyle: {style}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--provider", default=None)
    ap.add_argument("--workers", type=int, default=3, help="parallel requests; lower it if you hit rate limits")
    args = ap.parse_args()

    pts = json.loads(SEED.read_text())
    todo = [p for p in pts if not p.get("bio")][: args.limit]
    rng = np.random.default_rng(7)
    styles = {p["id"]: STYLES[int(rng.integers(len(STYLES)))] for p in todo}

    def one(p):
        try:
            out = llm.call_json("make_bio", SYSTEM, describe(p, styles[p["id"]]), Bio, provider=args.provider,
                                max_tokens=2000)
            return p["id"], out.bio
        except Exception as e:  # keep going; a rerun picks up whatever is missing
            print(f"  {p['id']} failed: {str(e)[:120]}")
            return p["id"], None

    bios, done = {}, 0
    by_id = {p["id"]: p for p in pts}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for pid, bio in pool.map(one, todo):
            if bio:
                bios[pid] = bio
                by_id[pid]["bio"] = bio
            done += 1
            if done % 10 == 0 or done == len(todo):
                SEED.write_text(json.dumps(pts, indent=1))  # save progress as we go
                print(f"  {done}/{len(todo)}")
    missing = len(todo) - len(bios)
    if missing:
        print(f"{missing} failed; run the same command again to finish them (finished ones are skipped).")
    first = next((p for p in todo if p["id"] in bios), None)
    print(f"Wrote {len(bios)} bios." + (f" Example:\n{first['name']}: {bios[first['id']]}" if first else ""))
    spent = [r for r in llm.spend_summary() if r["task"] == "make_bio"]
    if spent:
        print(f"make_bio spend: ${spent[0]['cost_usd']}")


if __name__ == "__main__":
    main()
