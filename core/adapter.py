"""Turn parsed objects (schemas.py) into solver inputs (solver.py). Plain Python, fully tested.

Constraints the solver enforces: unavailable, max_hours, require_skill, min_rest, fixed_assignment.
Constraints only reported for now: prefer, budget_cap (returned in `not_enforced` so the UI can say so).
"""

from __future__ import annotations

from core.schemas import Business, ConstraintSet, Employee, PartTimer, StaffingGrid
from core.taxonomy import DAYS, to_q

LEAD_ROLES = ("shift_lead", "shift_manager")


def employee_to_worker(e: Employee) -> dict:
    return {
        "id": e.id, "name": e.name, "roles": list(e.roles), "skills": list(e.skills),
        "rate_cents": e.rate_cents, "ot_rate_cents": e.ot_rate_cents,
        "max_q": int(round(e.max_hours * 4)), "unavailable_days": list(e.unavailable_days),
        "blocked": [], "is_market": False,
    }


def part_timer_to_worker(p: PartTimer) -> dict:
    avail = {d: [(to_q(s), to_q(e)) for s, e in wins] for d, wins in p.availability.items()} or None
    return {
        "id": p.id, "name": p.name, "roles": [r.role for r in p.roles], "skills": list(p.skills),
        "rate_cents": p.expected_rate_cents, "ot_rate_cents": round(p.expected_rate_cents * 1.5),
        "max_q": int(round(p.max_hours * 4)), "availability": avail,
        "blocked": [(d, to_q(s), to_q(e)) for d, s, e in p.blocked], "is_market": True,
    }


def grid_to_slots(grid: StaffingGrid) -> list[dict]:
    slots = []
    for i, b in enumerate(grid.blocks):
        slots.append({
            "id": f"{b.day}-{b.role}-{b.start.replace(':', '')}-{i}", "day": b.day,
            "start_q": to_q(b.start), "end_q": to_q(b.end), "role": b.role,
            "need": b.headcount, "skills": list(b.skills),
        })
    return slots


def _split_for_skill(slots: list[dict], target: dict, skill: str, at_least: int) -> None:
    """Make `at_least` of target's headcount require `skill` by splitting it into two slots."""
    if skill in target["skills"]:
        return
    if target["need"] <= at_least:
        target["skills"] = target["skills"] + [skill]
        return
    target["need"] -= at_least
    slots.append({**target, "id": target["id"] + f"-{skill}", "need": at_least,
                  "skills": target["skills"] + [skill]})


def _pick(day_slots: list[dict], edge: str) -> dict | None:
    """The slot that opens (earliest start) or closes (latest end) the day, preferring lead roles."""
    if not day_slots:
        return None
    key = (lambda s: s["start_q"]) if edge == "open" else (lambda s: -s["end_q"])
    best = min(key(s) for s in day_slots)
    edge_slots = [s for s in day_slots if key(s) == best]
    leads = [s for s in edge_slots if s["role"] in LEAD_ROLES]
    return (leads or edge_slots)[0]


def apply_constraints(workers: list[dict], slots: list[dict], cs: ConstraintSet,
                      business: Business | None = None) -> dict:
    """Mutates copies of workers/slots per the constraints. Returns solver inputs + bookkeeping."""
    workers = [dict(w, unavailable_days=list(w.get("unavailable_days", [])),
                    blocked=list(w.get("blocked", []))) for w in workers]
    slots = [dict(s, skills=list(s["skills"])) for s in slots]
    by_id = {w["id"]: w for w in workers}
    min_rest_q, fixed, not_enforced = 0, [], []

    for c in cs.constraints:
        if c.type == "unavailable":
            w = by_id[c.employee_id]
            for d in c.days:
                if c.start and c.end:
                    w["blocked"].append((d, to_q(c.start), to_q(c.end)))
                elif d not in w["unavailable_days"]:
                    w["unavailable_days"].append(d)
        elif c.type == "max_hours":
            targets = ([by_id[c.employee_id]] if c.employee_id
                       else [w for w in workers if c.role is None or c.role in w["roles"]])
            for w in targets:
                w["max_q"] = min(w["max_q"], int(round(c.hours * 4)))
        elif c.type == "require_skill":
            for d in c.days:
                day_slots = [s for s in slots if s["day"] == d]
                if c.at == "all_shifts":
                    # one per distinct time window that day
                    for window in sorted({(s["start_q"], s["end_q"]) for s in day_slots}):
                        same = [s for s in day_slots if (s["start_q"], s["end_q"]) == window]
                        leads = [s for s in same if s["role"] in LEAD_ROLES]
                        _split_for_skill(slots, (leads or same)[0], c.skill, c.at_least)
                else:
                    target = _pick(day_slots, c.at)
                    if target:
                        _split_for_skill(slots, target, c.skill, c.at_least)
        elif c.type == "min_rest":
            min_rest_q = max(min_rest_q, int(round(c.hours * 4)))
        elif c.type == "fixed_assignment":
            s, e = to_q(c.start), to_q(c.end)
            match = next((sl for sl in slots if sl["day"] == c.day and sl["start_q"] == s and sl["end_q"] == e
                          and sl["role"] in by_id[c.employee_id]["roles"]), None)
            if match:
                fixed.append((c.employee_id, match["id"]))
            else:
                not_enforced.append(f"{c.employee_id} on {c.day} {c.start}-{c.end}: no matching shift in the grid")
        else:  # prefer, budget_cap
            not_enforced.append(c.type)

    return {"workers": workers, "slots": slots, "min_rest_q": min_rest_q,
            "fixed": fixed, "not_enforced": not_enforced}


def build_inputs(business: Business, grid: StaffingGrid, employees: list[Employee],
                 cs: ConstraintSet) -> dict:
    """Everything solver.solve needs, from the parsed objects."""
    workers = [employee_to_worker(e) for e in employees]
    return apply_constraints(workers, grid_to_slots(grid), cs, business)


__all__ = ["DAYS", "build_inputs", "apply_constraints", "employee_to_worker", "part_timer_to_worker",
           "grid_to_slots"]
