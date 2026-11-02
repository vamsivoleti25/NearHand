"""The gap-filling agent: Muse Spark Open decides what to try; the solver decides who works and what it costs.

Tools the model can call (all backed by deterministic code):
    get_gaps            unfilled shifts after the staff-only solve, plus staff "near misses"
    search_marketplace  nearby part-timers who could cover given gaps
    solve_with          re-solve: staff schedule fixed, chosen candidates may take the gaps
    what_if             re-solve with one staff rule relaxed (more hours, a day back) and compare
    propose_plan        finish: the plan the manager will see, with a plain-English explanation

Run from the command line:
    python -m core.agent --business biz_bean
"""

from __future__ import annotations

import copy
import json
import re

from core import llm, marketplace
from core.adapter import build_inputs
from core.schemas import Booking, Business, ConstraintSet, Employee, PartTimer, StaffingGrid
from core.solver import eligible, fill_gaps, is_available, solve_staff_only, solve_with_pool
from core.taxonomy import DAYS, from_q

MAX_TURNS = 8
BOOKING_FEE_CENTS = 500


def _money(cents: int) -> str:
    return f"-${-cents / 100:,.2f}" if cents < 0 else f"${cents / 100:,.2f}"


def _cents(money: str) -> int:
    neg = money.startswith("-")
    return (-1 if neg else 1) * round(float(money.lstrip("-$").replace(",", "")) * 100)


class FillSession:
    """Holds the state the tools work on. Built once per 'Fill gaps' click."""

    def __init__(self, business: Business, grid: StaffingGrid, employees: list[Employee],
                 constraints: ConstraintSet, part_timers: list[PartTimer], bookings: list[Booking],
                 booking_fee_cents: int = BOOKING_FEE_CENTS, staff_result: dict | None = None,
                 covered: dict[str, int] | None = None, staff_extra_hours: float = 0, overtime_ok: bool = True,
                 no_extra_for=()):
        self.business, self.part_timers, self.bookings = business, part_timers, bookings
        self.pt_by_id = {p.id: p for p in part_timers}
        self.fee = booking_fee_cents
        self.inp = build_inputs(business, grid, employees, constraints)
        self.staff = self.inp["workers"]
        # Shifts already covered by live offers are taken out (replacement rounds fill only what reopened).
        covered = covered or {}
        self.slots = [dict(s, need=s["need"] - covered.get(s["id"], 0)) for s in self.inp["slots"]
                      if s["need"] - covered.get(s["id"], 0) > 0]
        self.slot_by_id = {s["id"]: s for s in self.slots}
        self.kw = {"min_rest_q": self.inp["min_rest_q"]}
        # "Ask my team first": staff may take open shifts beyond their usual hours when that's cheaper
        self.extra = {"staff_extra_q": int(round(staff_extra_hours * 4)), "overtime_ok": overtime_ok,
                      "no_extra_for": tuple(no_extra_for)}
        # Reuse the schedule the manager is looking at, so the agent fills exactly those gaps.
        self.staff_result = staff_result or solve_staff_only(self.staff, self.slots, fixed=self.inp["fixed"], **self.kw)
        gap_ids = sorted(self.staff_result["gaps"], key=lambda sid: (DAYS.index(self.slot_by_id[sid]["day"]),
                                                                      self.slot_by_id[sid]["start_q"]))
        self.gap_alias = {f"g{i + 1}": sid for i, sid in enumerate(gap_ids)}   # short ids for the model
        self.alias_of = {sid: a for a, sid in self.gap_alias.items()}
        self.candidates: dict[str, dict] = {}   # part-timer id -> solver worker
        self.what_ifs: list[dict] = []
        self.results_text: list[str] = []       # every tool result, for the grounding check
        self.plan: dict | None = None

    # ---------------------------------------------------------------- helpers

    def _slot_view(self, sid: str) -> dict:
        s = self.slot_by_id[sid]
        return {"gap_id": self.alias_of.get(sid, sid), "day": s["day"],
                "time": f"{from_q(s['start_q'])}-{from_q(s['end_q'])}", "role": s["role"],
                "needs_skills": s["skills"], "unfilled": self.staff_result["gaps"].get(sid, 0)}

    def _gap_hours(self, result: dict) -> float:
        return sum(n * (self.slot_by_id[sid]["end_q"] - self.slot_by_id[sid]["start_q"])
                   for sid, n in result["gaps"].items()) / 4

    def _near_misses(self, sid: str) -> list[str]:
        """Why each staff member with the right role can't take this gap."""
        s, out = self.slot_by_id[sid], []
        hours = {ln["worker_id"]: ln["hours"] for ln in self.staff_result["bill"]["lines"]}
        working = {(w, self.slot_by_id[x]["day"]) for w, x in self.staff_result["assignments"]}
        for w in self.staff:
            if s["role"] not in w["roles"]:
                continue
            name = w["name"].split()[0]
            if (w["id"], s["day"]) in working:
                out.append(f"{name} ({w['id']}) already works {s['day']}")
            elif not is_available(w, s):
                out.append(f"{name} ({w['id']}) is unavailable {s['day']}")
            elif not eligible(w, s):
                out.append(f"{name} ({w['id']}) lacks {', '.join(sorted(set(s['skills']) - set(w['skills'])))}")
            elif hours.get(w["id"], 0) * 4 + (s["end_q"] - s["start_q"]) > w["max_q"]:
                out.append(f"{name} ({w['id']}) would exceed their {w['max_q'] / 4:g}h cap "
                           f"(has {hours.get(w['id'], 0):g}h)")
        return out[:4]

    def _record(self, result: dict) -> dict:
        self.results_text.append(json.dumps(result))
        return result

    def _summarize_fill(self, r: dict) -> dict:
        staff_ids = {w["id"] for w in self.staff}
        gaps = set(self.staff_result["gaps"])
        bookings, extra = [], []
        lines = {ln["worker_id"]: ln for ln in r["bill"]["lines"]}
        before = {ln["worker_id"]: ln for ln in self.staff_result["bill"]["lines"]}
        for wid, sid in r["assignments"]:
            if wid in staff_ids:
                if sid in gaps:   # a team member picking up an open shift as extra hours
                    s = self.slot_by_id[sid]
                    extra.append({"employee_id": wid, "name": next(w["name"] for w in self.staff if w["id"] == wid),
                                  "gap_id": self.alias_of.get(sid, sid), "day": s["day"],
                                  "time": f"{from_q(s['start_q'])}-{from_q(s['end_q'])}", "role": s["role"],
                                  "hours": (s["end_q"] - s["start_q"]) / 4})
                continue
            p, s = self.pt_by_id[wid], self.slot_by_id[sid]
            bookings.append({"part_timer_id": wid, "name": p.name, "gap_id": self.alias_of.get(sid, sid),
                             "day": s["day"], "time": f"{from_q(s['start_q'])}-{from_q(s['end_q'])}",
                             "role": s["role"], "rate": _money(p.expected_rate_cents),
                             "distance_miles": marketplace.distance_miles(p, self.business)})
        added = r["bill"]["total_cents"] - self.staff_result["bill"]["total_cents"]
        fees = r["bill"]["booking_fees_cents"]
        team = {}
        for e in extra:
            t = team.setdefault(e["employee_id"], {"employee_id": e["employee_id"], "name": e["name"]})
            ln, b0 = lines[e["employee_id"]], before.get(e["employee_id"], {})
            t.update(hours_before=b0.get("hours", 0.0), hours_after=ln["hours"],
                     extra_hours=ln["hours"] - b0.get("hours", 0.0),
                     overtime_hours=ln["overtime_hours"] - b0.get("overtime_hours", 0.0),
                     extra_pay=_money(ln["base_cents"] + ln["overtime_cents"]
                                      - b0.get("base_cents", 0) - b0.get("overtime_cents", 0)))
        return {"bookings": bookings, "people_booked": len({b["part_timer_id"] for b in bookings}),
                "team_extra_shifts": extra, "team_extra_hours": list(team.values()),
                "added_wages": _money(added - fees), "booking_fees": _money(fees),
                "added_cost": _money(added), "added_cost_note": "added_cost = added_wages + booking_fees",
                "week_total": _money(r["bill"]["total_cents"]),
                "remaining_gaps": [self.alias_of.get(sid, sid) for sid in r["gaps"]],
                "remaining_gap_hours": self._gap_hours(r),
                # counts the explanation needs, so the model quotes them instead of doing arithmetic
                "gaps_total": len(self.gap_alias),
                "gaps_filled": len(self.gap_alias) - len(r["gaps"]),
                "gap_hours_total": self._gap_hours(self.staff_result),
                "covered_gap_hours": self._gap_hours(self.staff_result) - self._gap_hours(r)}

    # ---------------------------------------------------------------- tools

    def get_gaps(self) -> dict:
        b = self.staff_result["bill"]
        return self._record({
            "staff_only_week_total": _money(b["total_cents"]), "staff_overtime": _money(b["overtime_cents"]),
            "gap_hours": self._gap_hours(self.staff_result),
            "gaps": [dict(self._slot_view(sid), staff_near_misses=self._near_misses(sid))
                     for sid in self.gap_alias.values()],
            "booking_fee_per_person": _money(self.fee),
        })

    def search_marketplace(self, gap_ids: list[str] | None = None, max_distance_miles: float = 5.0,
                           max_rate_dollars: float | None = None) -> dict:
        sids = [self.gap_alias[g] for g in (gap_ids or self.gap_alias) if g in self.gap_alias]
        found = {}
        for sid in sids:
            pool = marketplace.pool_for_gaps(self.business, [self.slot_by_id[sid]], self.part_timers, self.bookings,
                                             max_distance_miles=max_distance_miles, per_gap=5,
                                             max_rate_cents=int(max_rate_dollars * 100) if max_rate_dollars else None)
            for w in pool:
                self.candidates[w["id"]] = w
            found[self.alias_of[sid]] = [
                {"id": w["id"], "name": w["name"], "rate": _money(w["rate_cents"]),
                 "distance_miles": w["distance_miles"], "roles": w["roles"], "skills": w["skills"]}
                for w in pool]
        return self._record({"candidates_by_gap": found, "max_distance_miles": max_distance_miles})

    def _fill(self, ids: list[str] | None, staff=None, staff_result=None) -> dict:
        pool = [self.candidates[i] for i in (ids or self.candidates) if i in self.candidates]
        return fill_gaps(staff or self.staff, pool, self.slots, staff_result or self.staff_result,
                         booking_fee_cents=self.fee, **self.extra, **self.kw)

    def solve_with(self, candidate_ids: list[str] | None = None) -> dict:
        r = self._fill(candidate_ids)
        return self._record(self._summarize_fill(r))

    def what_if(self, employee_id: str, extra_hours: float = 0, give_back_day: str | None = None) -> dict:
        staff = copy.deepcopy(self.staff)
        w = next((x for x in staff if x["id"] == employee_id), None)
        if w is None:
            return self._record({"error": f"unknown employee {employee_id}"})
        if extra_hours:
            w["max_q"] += int(extra_hours * 4)
        if give_back_day:
            w["unavailable_days"] = [d for d in w["unavailable_days"] if d != give_back_day]
        base = self._fill(None)
        # Exact comparison: everyone keeps their shifts; only the changed employee may also take gaps.
        gaps = set(self.staff_result["gaps"])
        keep = list(self.staff_result["assignments"])
        restrict = {x["id"]: {sid for wid, sid in keep if wid == x["id"]} for x in staff}
        restrict[employee_id] |= gaps
        pool = list(self.candidates.values())
        alt = solve_with_pool(staff, pool, self.slots, booking_fee_cents=self.fee, fixed=keep,
                              market_slots=gaps, restrict=restrict, **self.kw)
        took = [self._slot_view(sid)["gap_id"] for wid, sid in alt["assignments"]
                if wid == employee_id and sid in gaps]
        out = {"change": {"employee_id": employee_id, "employee": w["name"], "extra_hours": extra_hours,
                          "give_back_day": give_back_day},
               "week_total_now": _money(base["bill"]["total_cents"]),
               "week_total_with_change": _money(alt["bill"]["total_cents"]),
               "saves": _money(base["bill"]["total_cents"] - alt["bill"]["total_cents"]),
               "employee_would_cover": took,
               "gap_hours_now": self._gap_hours(base), "gap_hours_with_change": self._gap_hours(alt),
               "people_booked_with_change": len({wid for wid, _ in alt["assignments"]
                                                 if wid in self.candidates})}
        self.what_ifs.append(out)
        return self._record(out)

    def propose_plan(self, candidate_ids: list[str], explanation: str, alternative: str = "") -> dict:
        r = self._fill(candidate_ids or None)
        summary = self._summarize_fill(r)
        text = explanation + " " + alternative
        ungrounded = ungrounded_numbers(text, self.results_text + [json.dumps(summary)])
        best = max(self.what_ifs, key=lambda w: _cents(w["saves"]), default=None)
        if best is not None and _cents(best["saves"]) <= 0:
            best = None
        self.plan = {**summary, "explanation": explanation, "alternative": alternative, "best_what_if": best,
                     "grounded": not ungrounded, "ungrounded_numbers": ungrounded,
                     "what_ifs": self.what_ifs, "assignments": r["assignments"], "result": r}
        return summary

    # ---------------------------------------------------------------- dispatch

    def call(self, name: str, args: dict) -> dict:
        fn = {"get_gaps": self.get_gaps, "search_marketplace": self.search_marketplace,
              "solve_with": self.solve_with, "what_if": self.what_if, "propose_plan": self.propose_plan}.get(name)
        if fn is None:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(**args)
        except Exception as e:  # tell the model, let it recover
            return {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------- grounding

_NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?")


def _norm(tok: str) -> str:
    t = tok.replace("$", "").replace(",", "")
    return t[:-3] if t.endswith(".00") else t.rstrip("0").rstrip(".") if "." in t else t


def ungrounded_numbers(text: str, sources: list[str]) -> list[str]:
    """Numbers in the explanation that appear in no tool result. Small counts (<= 20) are allowed."""
    pool = {_norm(t) for s in sources for t in _NUM.findall(s)}
    bad = []
    for tok in _NUM.findall(text):
        n = _norm(tok)
        if n in pool or (n.isdigit() and int(n) <= 20 and "$" not in tok):
            continue
        bad.append(tok)
    return bad


# ---------------------------------------------------------------- the model's side

SYSTEM = """You fill the open shifts in a small business's weekly schedule, for the owner.
The staff schedule is already built and stays as it is. Your job: cover the gaps at the lowest total cost,
using nearby part-timers from the marketplace, and tell the owner what you did in plain English.

How to work:
1. Call get_gaps. Note each gap's role, time, required skills, and the staff near misses.
2. Call search_marketplace (default 5 miles). If a gap has no candidates, search again wider (up to 10 miles).
3. Call solve_with to let the optimizer pick the cheapest combination. Fewer separate people is better:
   every person booked costs a booking fee.
4. If a near miss suggests a staff member could cover a gap with a small change (a few extra hours, a day back),
   test at most two of those with what_if. Only suggest overtime or a changed day off as an alternative
   for the owner to decide; the plan you propose uses the marketplace.
   If results list team_extra_shifts, the owner chose to ask the team first: those open shifts go to the
   business's own staff as extra hours (paid at their rate, overtime past 40 hours), because that is
   cheaper or nobody nearby qualifies. Mention them first, by first name, with extra_pay from the results.
5. Call propose_plan with the candidate ids to book and a 3-4 sentence explanation for a busy café owner:
   which gaps get filled by whom, what it adds to the week, and why these people (cost, distance, skills).
   Put the what-if that saves the most (if any saves money) in `alternative`, in one sentence.
   added_cost already includes booking_fees: never add them together.

Write for the owner: people's first names and plain job names ("shift lead", "dishwasher", "drive-thru"),
never ids like emp_ana, pt_080 or dishwasher_busser, and days and times as written ("Sat 7am-12pm" is fine).
Rules: every number you write must be copied from a tool result. Never invent names, rates or totals, and never
do arithmetic yourself: no sums, differences, counts or ranges ("2-6 miles") you worked out. For how much got
covered, quote gaps_filled, gaps_total, covered_gap_hours and remaining_gap_hours from the latest result.
Never judge people on anything except role, skills, availability, rate and distance."""

TOOLS = [
    {"type": "function", "function": {
        "name": "get_gaps", "description": "Unfilled shifts after scheduling staff, with staff near misses.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "search_marketplace",
        "description": "Find nearby part-timers who are free and qualified for the given gaps (cheapest first).",
        "parameters": {"type": "object", "properties": {
            "gap_ids": {"type": "array", "items": {"type": "string"}, "description": "e.g. [\"g1\",\"g3\"]; omit for all"},
            "max_distance_miles": {"type": "number", "description": "default 5"},
            "max_rate_dollars": {"type": "number", "description": "optional hourly cap"}}}}},
    {"type": "function", "function": {
        "name": "solve_with",
        "description": "Optimize: staff schedule fixed, these candidates may take the gaps. Returns bookings and cost.",
        "parameters": {"type": "object", "properties": {
            "candidate_ids": {"type": "array", "items": {"type": "string"},
                              "description": "part-timer ids; omit to use everyone found so far"}}}}},
    {"type": "function", "function": {
        "name": "what_if",
        "description": "Re-solve with one staff change (extra weekly hours, or a day back) and compare the week total.",
        "parameters": {"type": "object", "properties": {
            "employee_id": {"type": "string"},
            "extra_hours": {"type": "number"},
            "give_back_day": {"type": "string", "enum": DAYS}}, "required": ["employee_id"]}}},
    {"type": "function", "function": {
        "name": "propose_plan", "description": "Finish with the plan for the owner to approve.",
        "parameters": {"type": "object", "properties": {
            "candidate_ids": {"type": "array", "items": {"type": "string"}},
            "explanation": {"type": "string"},
            "alternative": {"type": "string"}}, "required": ["candidate_ids", "explanation"]}}},
]


def run_agent(session: FillSession, on_step=None, provider=None, max_turns: int = MAX_TURNS) -> dict:
    """Let the model work the tools. Returns session.plan (falls back to a fixed sequence if needed)."""
    on_step = on_step or (lambda *a: None)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Fill the open shifts for {session.business.name} this week."}]
    for turn in range(max_turns):
        try:
            msg = llm.call_tools(messages, TOOLS, task="agent", provider=provider, effort="medium")
        except Exception as e:
            on_step("error", {}, {"error": str(e)[:300]})
            break
        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         **({"tool_calls": msg["tool_calls"]} if msg.get("tool_calls") else {})})
        if not msg.get("tool_calls"):
            messages.append({"role": "user", "content": "Use the tools, and finish with propose_plan."})
            continue
        for tc in msg["tool_calls"]:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            result = session.call(name, args)
            on_step(name, args, result)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(result)})
            if name == "propose_plan" and session.plan is not None:
                if not session.plan["grounded"]:
                    session.plan["explanation"] = template_explanation(session)
                    session.plan["alternative"] = ""
                    session.plan["note"] = "Explanation replaced: it quoted numbers not found in tool results."
                session.plan["turns"] = turn + 1
                return session.plan
    return fallback_plan(session, on_step)


def template_explanation(session: FillSession) -> str:
    p = session.plan or {}
    names = sorted({b["name"] for b in p.get("bookings", [])})
    filled = len(session.gap_alias) - len(p.get("remaining_gaps", []))
    return (f"{filled} of {len(session.gap_alias)} open shifts are covered by {', '.join(names) or 'nobody'}, "
            f"adding {p.get('added_cost', '$0.00')} to the week (including {p.get('booking_fees', '$0.00')} "
            f"in booking fees). They are the lowest-cost qualified people nearby who are free at those times.")


def fallback_plan(session: FillSession, on_step=None) -> dict:
    """Fixed sequence when the model doesn't finish: gaps -> search -> solve -> plan."""
    on_step = on_step or (lambda *a: None)
    for name, args in [("get_gaps", {}), ("search_marketplace", {}), ("solve_with", {})]:
        on_step(name, args, session.call(name, args))
    session.propose_plan([], "", "")
    session.plan["explanation"] = template_explanation(session)
    session.plan["note"] = "Fallback: fixed sequence."
    session.plan["turns"] = None
    return session.plan


# ---------------------------------------------------------------- command line

def _demo_session(business_id: str) -> FillSession:
    from core import db, parsers
    db.load_seed()
    biz = db.business(business_id)
    team = db.team(business_id)
    rules = json.loads((db.SEED_DIR / "rules.json").read_text())[business_id]
    cs = parsers.parse_rules(rules, team, biz)
    return FillSession(biz, db.grid(business_id), team, cs, db.part_timers(), db.bookings())


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--business", default="biz_bean")
    ap.add_argument("--provider", default=None)
    args = ap.parse_args()

    s = _demo_session(args.business)
    print(f"Staff-only week: {_money(s.staff_result['bill']['total_cents'])}, "
          f"{len(s.gap_alias)} gaps ({s._gap_hours(s.staff_result):g} h)\n")

    def show(name, a, result):
        brief = json.dumps(result)[:220]
        print(f"> {name}({json.dumps(a)[:120]})\n    {brief}{'...' if len(json.dumps(result)) > 220 else ''}")

    plan = run_agent(s, on_step=show, provider=args.provider)
    print("\n=== Proposed plan ===")
    for b in plan["bookings"]:
        print(f"  {b['day']} {b['time']} {b['role']:18s} {b['name']:16s} {b['rate']}/h  {b['distance_miles']} mi")
    print(f"  Added cost {plan['added_cost']} (fees {plan['booking_fees']}), week total {plan['week_total']}, "
          f"remaining gaps {plan['remaining_gaps'] or 'none'}")
    print(f"\n{plan['explanation']}")
    if plan.get("alternative"):
        print(f"Alternative: {plan['alternative']}")
    if plan.get("best_what_if"):
        w = plan["best_what_if"]
        print(f"Best what-if (computed): {w['change']['employee']} {w['change']} saves {w['saves']}")
    print(f"\nturns={plan.get('turns')} grounded={plan['grounded']} {plan.get('note', '')}")
    for row in llm.spend_summary():
        if row["task"] == "agent":
            print(f"agent spend so far: ${row['cost_usd']} over {row['calls']} calls")
