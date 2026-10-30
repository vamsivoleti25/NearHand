"""End to end on a demo café: description -> grid, team paste -> employees, rules -> constraints, solve.

Run:  python -m scripts.demo_cafe            (Baseten)
      python -m scripts.demo_cafe fireworks  (any provider name from core/llm.py)
"""

import sys
from collections import defaultdict

from core import llm, parsers
from core.adapter import build_inputs
from core.schemas import Business
from core.solver import solve
from core.taxonomy import DAYS, from_q

provider = sys.argv[1] if len(sys.argv) > 1 else None

business = Business(
    id="biz_bean", name="Bean & Co.", type="cafe", toggles=["drive_thru"], zip="06095",
    hours={d: ("06:00", "18:00") if d not in ("Sat", "Sun") else ("07:00", "17:00") for d in DAYS},
    description="We get slammed 7-9am on weekdays and Saturday brunch is our busiest time. Sundays are slow.",
)

TEAM = """Maya Patel - barista, $17/hr, max 30 hrs, can't do Tuesdays
Leo Kim - barista + register, 16.50, full time
Ana Ruiz - shift lead, opens most days, $21/hr full-time
Sam Ortiz - cashier/drive thru, $15.75, PT 20 hrs
Priya Nair - dishes & bussing, $15/hr, weekends only
Jordan Lee - barista, $18, part time, no Mondays
Chris Wong - shift lead, $20.50, PT 28h"""

RULES = "@Maya off Tuesdays, baristas max 32h, a keyholder at every open and close, 10 hours between shifts"

print("1) Staffing grid from the description ...")
grid = parsers.parse_business(business, provider=provider)
print(f"   {len(grid.blocks)} blocks. Notes: {grid.notes[:3]}")

print("2) Team from the pasted list ...")
team = parsers.parse_team(TEAM, business.type, provider=provider)
for e in team.employees:
    print(f"   {e.id:12s} {', '.join(e.roles):28s} ${e.rate_cents / 100:6.2f}/h  max {e.max_hours:g}h  "
          f"skills={e.skills} off={e.unavailable_days}")
if team.ambiguities:
    print("   questions:", team.ambiguities)

print("3) Rules ...")
cs = parsers.parse_rules(RULES, team.employees, business, provider=provider)
for c in cs.constraints:
    print("  ", c.model_dump(exclude_none=True))
if cs.ambiguities:
    print("   questions:", cs.ambiguities)

print("4) Solve ...")
inp = build_inputs(business, grid, team.employees, cs)
r = solve(inp["workers"], inp["slots"], min_rest_q=inp["min_rest_q"], fixed=inp["fixed"])
slot_by_id = {s["id"]: s for s in inp["slots"]}
by_person = defaultdict(dict)
for wid, sid in r["assignments"]:
    s = slot_by_id[sid]
    by_person[wid][s["day"]] = f"{from_q(s['start_q'])}-{from_q(s['end_q'])}"
print(f"   status {r['status']}")
print("   " + "".join(f"{d:>13s}" for d in DAYS))
for wid in sorted(by_person):
    print(f"   {wid:12s}" + "".join(f"{by_person[wid].get(d, ''):>13s}" for d in DAYS)[1:])
gap_h = sum(n * (slot_by_id[sid]["end_q"] - slot_by_id[sid]["start_q"]) for sid, n in r["gaps"].items()) / 4
b = r["bill"]
print(f"   cost ${b['total_cents'] / 100:,.2f} (overtime ${b['overtime_cents'] / 100:,.2f}), "
      f"{len(r['gaps'])} gap slots, {gap_h:g} gap hours")
for sid, n in sorted(r["gaps"].items())[:10]:
    s = slot_by_id[sid]
    print(f"   GAP {s['day']} {from_q(s['start_q'])}-{from_q(s['end_q'])} {s['role']} x{n} {s['skills']}")
if inp["not_enforced"]:
    print("   noted but not enforced:", inp["not_enforced"])

print("\nSpend:")
for row in llm.spend_summary():
    print("  ", row)
