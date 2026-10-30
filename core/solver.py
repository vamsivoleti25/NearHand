"""Weekly shift scheduling with OR-Tools CP-SAT.

The solver makes every assignment and computes every dollar; the language model never does.
It solves in two stages: first the fewest unfilled hours, then the cheapest way to staff that.

Units: time in 15-minute "quarters" from midnight (see taxonomy.to_q), money in integer cents.

A worker is a dict:
    id, name, roles (list), skills (list), rate_cents (per hour), ot_rate_cents (per hour),
    max_q (weekly cap in quarters), is_market (bool, default False),
    availability: {day: [(start_q, end_q), ...]}  -- optional; missing means available all week
    unavailable_days: [day, ...]                   -- optional
    blocked: [(day, start_q, end_q), ...]          -- optional; bookings or commitments elsewhere

A slot is a dict:
    id, day, start_q, end_q, role, need (headcount), skills (list of extra required skills)
"""

from __future__ import annotations

import copy

from ortools.sat.python import cp_model

from core.taxonomy import DAYS, ROLE_REQUIRED_SKILLS

OT_THRESHOLD_Q = 40 * 4          # weekly overtime starts after 40 hours
# Solved in two stages: (1) fewest unfilled hours, (2) cheapest way to staff that coverage.
# GAP_WEIGHT is in quarter-cents per unfilled quarter-hour ($1,000/hour): far above any real
# wage, so stage 1 always prefers a fill, overtime included.
GAP_WEIGHT = 100_000


# ---------------------------------------------------------------- eligibility

def required_skills(slot: dict) -> set[str]:
    return set(slot.get("skills", [])) | set(ROLE_REQUIRED_SKILLS.get(slot["role"], []))


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def is_available(worker: dict, slot: dict) -> bool:
    day, s, e = slot["day"], slot["start_q"], slot["end_q"]
    if day in worker.get("unavailable_days", []):
        return False
    avail = worker.get("availability")
    if avail is not None and not any(ws <= s and e <= we for ws, we in avail.get(day, [])):
        return False
    return not any(bd == day and _overlaps(s, e, bs, be) for bd, bs, be in worker.get("blocked", []))


def eligible(worker: dict, slot: dict) -> bool:
    return (
        slot["role"] in worker["roles"]
        and required_skills(slot) <= set(worker.get("skills", []))
        and is_available(worker, slot)
    )


# ---------------------------------------------------------------- core solve

def solve(workers: list[dict], slots: list[dict], booking_fee_cents: int = 0,
          min_rest_q: int = 0, fixed=(), market_slots=None, time_limit_s: float = 6.0) -> dict:
    """Cheapest weekly schedule. Unfillable headcount comes back as gaps, never as 'infeasible'.

    fixed: (worker_id, slot_id) pairs that must stay (pinned shifts, or the staff schedule
           when filling gaps); skipped if that worker isn't eligible.
    market_slots: if given, marketplace workers may only take these slot ids (the gaps).
    """
    m = cp_model.CpModel()
    slot_by_id = {s["id"]: s for s in slots}
    length = {s["id"]: s["end_q"] - s["start_q"] for s in slots}

    def allowed(w, s):
        if w.get("is_market") and market_slots is not None and s["id"] not in market_slots:
            return False
        return eligible(w, s)

    x = {(w["id"], s["id"]): m.NewBoolVar(f"x_{w['id']}_{s['id']}")
         for w in workers for s in slots if allowed(w, s)}
    u = {s["id"]: m.NewIntVar(0, s["need"], f"u_{s['id']}") for s in slots}
    for pair in fixed:
        if tuple(pair) in x:
            m.Add(x[tuple(pair)] == 1)

    for s in slots:  # coverage: assigned + unfilled == headcount needed
        m.Add(sum(v for (_, sid), v in x.items() if sid == s["id"]) + u[s["id"]] == s["need"])

    gap_q = sum(length[sid] * u[sid] for sid in u)  # unfilled quarter-hours
    objective = []  # labor cost in quarter-cents
    h_vars, o_vars, y_vars = {}, {}, {}

    for w in workers:
        mine = [(slot_by_id[sid], v) for (wid, sid), v in x.items() if wid == w["id"]]
        if not mine:
            continue
        # at most one shift per day
        for day in {s["day"] for s, _ in mine}:
            m.Add(sum(v for s, v in mine if s["day"] == day) <= 1)
        # minimum rest between a shift and the next day's shift
        if min_rest_q:
            for a, va in mine:
                for b, vb in mine:
                    if DAYS.index(b["day"]) == DAYS.index(a["day"]) + 1:
                        if (b["start_q"] + 96) - a["end_q"] < min_rest_q:
                            m.Add(va + vb <= 1)
        # hours, cap and weekly overtime
        h = m.NewIntVar(0, w["max_q"], f"h_{w['id']}")
        m.Add(h == sum(length[s["id"]] * v for s, v in mine))
        o = m.NewIntVar(0, w["max_q"], f"o_{w['id']}")
        m.Add(o >= h - OT_THRESHOLD_Q)
        objective.append(w["rate_cents"] * h + (w["ot_rate_cents"] - w["rate_cents"]) * o)
        h_vars[w["id"]], o_vars[w["id"]] = h, o
        # one booking fee per marketplace worker used, however many shifts they take
        if w.get("is_market") and booking_fee_cents:
            y = m.NewBoolVar(f"y_{w['id']}")
            for _, v in mine:
                m.AddImplication(v, y)
            objective.append(4 * booking_fee_cents * y)
            y_vars[w["id"]] = y

    # Stage 1: cover as much as possible. An unfilled hour costs far more than any real fill,
    # and the labor term guides the search toward sensible schedules quickly.
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 8
    solver.parameters.interleave_search = True       # same inputs -> same schedule, run after run
    solver.parameters.random_seed = 7
    solver.parameters.max_deterministic_time = time_limit_s / 2
    solver.parameters.max_time_in_seconds = time_limit_s * 2  # hard wall-clock safety net
    m.Minimize(GAP_WEIGHT * gap_q + sum(objective))
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"status": solver.StatusName(status), "assignments": [], "gaps": {}, "bill": {}}
    best_gap_q = sum(length[sid] * solver.Value(v) for sid, v in u.items())
    stage1_optimal = status == cp_model.OPTIMAL

    # Stage 2: keep that coverage, find the cheapest way to staff it (stop within 0.5% of optimal).
    m.Add(gap_q <= best_gap_q)
    m.ClearHints()
    for var in list(x.values()) + list(u.values()):
        m.AddHint(var, solver.Value(var))
    m.Minimize(sum(objective))
    solver.parameters.max_deterministic_time = time_limit_s / 2
    solver.parameters.relative_gap_limit = 0.005
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"status": solver.StatusName(status), "assignments": [], "gaps": {}, "bill": {}}

    assignments = sorted(k for k, v in x.items() if solver.Value(v))
    gaps = {sid: solver.Value(v) for sid, v in u.items() if solver.Value(v)}
    result = {
        "status": solver.StatusName(status),
        "coverage_proven_best": stage1_optimal,
        "assignments": assignments,
        "gaps": gaps,
        "binding_caps": sorted(wid for wid, h in h_vars.items()
                               if solver.Value(h) == next(w["max_q"] for w in workers if w["id"] == wid)),
    }
    result["bill"] = bill(workers, slots, assignments, booking_fee_cents)
    return result


# ---------------------------------------------------------------- the bill (plain Python, easy to test)

def _cents(rate_cents_per_hour: int, quarters: int) -> int:
    return (rate_cents_per_hour * quarters + 2) // 4  # rounded to the nearest cent


def bill(workers, slots, assignments, booking_fee_cents=0) -> dict:
    """Base pay, overtime premium and booking fees for a set of assignments."""
    length = {s["id"]: s["end_q"] - s["start_q"] for s in slots}
    by_worker = {}
    for wid, sid in assignments:
        by_worker.setdefault(wid, []).append(sid)
    lines, totals = [], {"base_cents": 0, "overtime_cents": 0, "booking_fees_cents": 0}
    for w in workers:
        if w["id"] not in by_worker:
            continue
        q = sum(length[sid] for sid in by_worker[w["id"]])
        ot_q = max(0, q - OT_THRESHOLD_Q)
        line = {
            "worker_id": w["id"], "name": w.get("name", w["id"]), "is_market": bool(w.get("is_market")),
            "hours": q / 4, "overtime_hours": ot_q / 4,
            "base_cents": _cents(w["rate_cents"], q),
            "overtime_cents": _cents(w["ot_rate_cents"] - w["rate_cents"], ot_q),
            "booking_fee_cents": booking_fee_cents if w.get("is_market") else 0,
        }
        lines.append(line)
        totals["base_cents"] += line["base_cents"]
        totals["overtime_cents"] += line["overtime_cents"]
        totals["booking_fees_cents"] += line["booking_fee_cents"]
    totals["total_cents"] = sum(totals.values())
    return {"lines": lines, **totals}


# ---------------------------------------------------------------- two-pass flow and what-ifs

def solve_staff_only(staff, slots, **kw) -> dict:
    """Pass 1: in-house staff only. The gaps are what the marketplace needs to fill."""
    return solve(staff, slots, **kw)


def solve_with_pool(staff, pool, slots, booking_fee_cents=0, platform_fee=0.0, **kw) -> dict:
    """Pass 2: staff plus marketplace candidates priced at rate x (1 + platform fee)."""
    market = []
    for p in pool:
        p = dict(p, is_market=True)
        p["rate_cents"] = round(p["rate_cents"] * (1 + platform_fee))
        p["ot_rate_cents"] = round(p.get("ot_rate_cents", p["rate_cents"]) * (1 + platform_fee))
        market.append(p)
    return solve(list(staff) + market, slots, booking_fee_cents=booking_fee_cents, **kw)


def fill_gaps(staff, pool, slots, staff_result: dict, booking_fee_cents=0, platform_fee=0.0, **kw) -> dict:
    """Pass 2 as a manager expects it: the staff schedule stays exactly as it is, and
    marketplace workers may only take the gap shifts left by pass 1."""
    keep = list(staff_result["assignments"]) + list(kw.pop("fixed", ()))
    return solve_with_pool(staff, pool, slots, booking_fee_cents, platform_fee,
                           fixed=keep, market_slots=set(staff_result["gaps"]), **kw)


def _gap_hours(result, slots) -> float:
    length = {s["id"]: s["end_q"] - s["start_q"] for s in slots}
    return sum(n * length[sid] for sid, n in result["gaps"].items()) / 4


def what_if(staff, pool, slots, change: dict, booking_fee_cents=0, platform_fee=0.0, **kw) -> dict:
    """Re-solve with one change and report the difference against the current plan.

    change examples:
        {"type": "raise_cap", "worker_id": "emp_maya", "hours": 2}
        {"type": "allow_day", "worker_id": "emp_maya", "day": "Tue"}
        {"type": "remove_worker", "worker_id": "pt_priya"}
    """
    def run(st, po):
        return solve_with_pool(st, po, slots, booking_fee_cents, platform_fee, **kw)

    base = run(staff, pool)
    staff2, pool2 = copy.deepcopy(staff), copy.deepcopy(pool)
    target = next((w for w in staff2 + pool2 if w["id"] == change["worker_id"]), None)
    if target is None:
        raise ValueError(f"unknown worker {change['worker_id']}")
    if change["type"] == "raise_cap":
        target["max_q"] += int(change["hours"] * 4)
    elif change["type"] == "allow_day":
        target["unavailable_days"] = [d for d in target.get("unavailable_days", []) if d != change["day"]]
    elif change["type"] == "remove_worker":
        staff2 = [w for w in staff2 if w["id"] != change["worker_id"]]
        pool2 = [w for w in pool2 if w["id"] != change["worker_id"]]
    else:
        raise ValueError(f"unknown change type {change['type']}")
    new = run(staff2, pool2)
    return {
        "change": change,
        "cost_delta_cents": new["bill"]["total_cents"] - base["bill"]["total_cents"],
        "gap_hours_delta": _gap_hours(new, slots) - _gap_hours(base, slots),
        "before_total_cents": base["bill"]["total_cents"],
        "after_total_cents": new["bill"]["total_cents"],
        "result": new,
    }
