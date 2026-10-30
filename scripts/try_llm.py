"""Live check of core/llm.py: one tiny structured call per provider, twice (second is cached)."""

from pydantic import BaseModel

from core import llm


class Rule(BaseModel):
    employee: str
    day_off: str
    max_hours: int


SYSTEM = "Extract the scheduling rule as JSON. Days are three letters, e.g. Tue."
USER = "Maya can't work Tuesdays and shouldn't go over 30 hours this week."

for provider in ["baseten", "fireworks", "meta"]:
    for attempt in (1, 2):
        try:
            out = llm.call_json("try_llm", SYSTEM, USER, Rule, provider=provider)
            print(f"{provider:10s} try {attempt}: {out.model_dump()}")
        except Exception as e:
            print(f"{provider:10s} try {attempt}: FAILED {str(e)[:200]}")

print("\nSpend so far:")
for row in llm.spend_summary():
    print(row)
