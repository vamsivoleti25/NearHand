"""Eval suites for the three jobs the model does in Nearhand, each scored against a known answer.

rules   plain-English rules -> typed constraints, against hand-written gold (evals/cases/rules.json)
agent   the gap-filling agent's plan, against the optimizer's best plan over the whole marketplace
advice  "should I take this?", against the answer the facts call for

Everything is read from data/seed/ (not the demo database), so runs are repeatable.
"""

from __future__ import annotations

import json
import random
import time
from collections import Counter
from pathlib import Path

from core import agent, marketplace, offers, parsers
from core.schemas import (Business, ConstraintSet, Employee, Offer, OfferShift, PartTimer, StaffingGrid)
from core.solver import fill_gaps
from core.taxonomy import DAYS, from_q, to_q

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "seed"
CASES = Path(__file__).resolve().parent / "cases"


# ---------------------------------------------------------------- seed data

def _seed(name):
    return json.loads((SEED / name).read_text())


def businesses() -> dict[str, Business]:
    return {b["id"]: Business.model_validate(b) for b in _seed("businesses.json")}


def team(business_id: str) -> list[Employee]:
    return [Employee.model_validate(e) for e in _seed("teams.json")[business_id]]


def grid(business_id: str, business: Business) -> StaffingGrid:
    g = _seed("grids.json").get(business_id)
    return StaffingGrid.model_validate(g) if g else parsers.default_grid(business)


def part_timers() -> list[PartTimer]:
    return [PartTimer.model_validate(p) for p in _seed("part_timers.json")]


def rule_cases() -> list[dict]:
    return json.loads((CASES / "rules.json").read_text())


# ---------------------------------------------------------------- 1. rules

ALL_DAYS = frozenset(DAYS)


def canon(c: dict, business: Business) -> tuple:
    """A constraint reduced to what matters, so equivalent parses compare equal."""
    c = c if isinstance(c, dict) else c.model_dump()
    t = c["type"]
    days = lambda v: "all" if v is None or frozenset(v) == ALL_DAYS else frozenset(v)  # noqa: E731
    if t == "unavailable":
        ds = frozenset(c["days"])
        opens = min(to_q(business.hours[d][0]) for d in ds if d in business.hours) if ds else 0
        closes = max(to_q(business.hours[d][1]) for d in ds if d in business.hours) if ds else 96
        start = c.get("start")
        end = c.get("end")
        start = None if start is None or to_q(start) <= opens else start   # from opening = whole morning
        end = None if end is None or to_q(end) >= closes else end           # to closing = rest of the day
        return (t, c["employee_id"], ds, start, end)
    if t == "max_hours":
        return (t, c.get("employee_id"), c.get("role"), round(float(c["hours"]), 1))
    if t == "require_skill":
        return (t, c["skill"], c["at"], days(c.get("days")), c.get("at_least", 1))
    if t == "min_rest":
        return (t, round(float(c["hours"]), 1))
    if t == "fixed_assignment":
        return (t, c["employee_id"], c["day"], c["start"], c["end"])
    if t == "prefer":
        return (t, c["employee_id"], frozenset(c["days"]), c.get("kind", "prefer"))
    if t == "budget_cap":
        return (t, round(float(c["dollars"])))
    return (t, json.dumps(c, sort_keys=True))


def match(pred: list, gold: list, business: Business) -> dict:
    p = Counter(canon(c, business) for c in pred)
    g = Counter(canon(c, business) for c in gold)
    tp = sum((p & g).values())
    return {"tp": tp, "fp": sum(p.values()) - tp, "fn": sum(g.values()) - tp, "exact": p == g}


def score_rule_case(case: dict, out: ConstraintSet, business: Business) -> dict:
    pred = [c.model_dump() for c in out.constraints]
    best = match(pred, case["gold"], business)
    for alt in case.get("alt_gold", []):        # a few rules have more than one fair reading
        m = match(pred, alt, business)
        if (m["exact"], -m["fp"] - m["fn"]) > (best["exact"], -best["fp"] - best["fn"]):
            best = m
    asked = bool(out.ambiguities)
    return {**best, "asked": asked,
            "pass": best["exact"] and (asked or not case.get("expect_question")),
            "predicted": pred, "questions": out.ambiguities}


def run_rules(provider=None, workers: int = 3, only: list[str] | None = None, progress=print) -> dict:
    biz = businesses()
    cases = [c for c in rule_cases() if not only or c["id"] in only]

    def one(case):
        b = biz[case["business_id"]]
        t0 = time.perf_counter()
        try:
            out = parsers.parse_rules(case["text"], team(case["business_id"]), b, provider=provider)
            res = score_rule_case(case, out, b)
        except Exception as e:  # a failed call is a failed case, not a crashed run
            res = {"tp": 0, "fp": 0, "fn": len(case["gold"]), "exact": False, "asked": False, "pass": False,
                   "predicted": [], "questions": [], "error": str(e)[:300]}
        res.update(id=case["id"], business_id=case["business_id"], text=case["text"], gold=case["gold"],
                   expect_question=bool(case.get("expect_question")), seconds=round(time.perf_counter() - t0, 2))
        progress(f"  {case['id']} {'pass' if res['pass'] else 'FAIL'}  {case['text'][:60]}")
        return res

    rows = _map(one, cases, workers)
    tp, fp, fn = (sum(r[k] for r in rows) for k in ("tp", "fp", "fn"))
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    unclear = [r for r in rows if r["expect_question"]]
    return {"cases": rows, "summary": {
        "cases": len(rows),
        "pass_rate": _pct(sum(r["pass"] for r in rows), len(rows)),
        "precision": round(prec, 3), "recall": round(rec, 3),
        "f1": round(2 * prec * rec / (prec + rec), 3) if prec + rec else 0.0,
        "asked_when_unclear": _pct(sum(r["asked"] for r in unclear), len(unclear)),
        "invented_rules_on_unclear": sum(r["fp"] for r in unclear),
        "avg_seconds": _avg(r["seconds"] for r in rows),
    }}


# ---------------------------------------------------------------- 2. agent vs optimizer

GOLD_RULES = {c["business_id"]: ConstraintSet.model_validate({"constraints": c["gold"]})
              for c in json.loads((CASES / "rules.json").read_text()) if c["id"] in ("r01", "r16")}
ORACLE_RADIUS_MILES = 10.0   # the agent searches 5 miles by default and may widen; the optimizer sees 10
ORACLE_PER_GAP = 15          # cheapest 15 qualified people per open shift (the agent sees 5)
ORACLE_SECONDS = 30.0


def scenarios(n_per_business: int = 5, seed: int = 7) -> list[dict]:
    """The normal week, then 'call-outs': one to three staff out sick, chosen with a fixed seed."""
    rng = random.Random(seed)
    out = []
    for bid in ("biz_bean", "biz_maple"):
        ids = [e.id for e in team(bid)]
        out.append({"id": f"{bid}-normal", "business_id": bid, "out": []})
        tries = 0
        while len([s for s in out if s["business_id"] == bid]) < n_per_business and tries < 50:
            tries += 1
            k = rng.choice([1, 1, 2, 3])
            gone = sorted(rng.sample(ids, k))
            sid = f"{bid}-out-{'-'.join(g.removeprefix('emp_') for g in gone)}"
            if all(s["id"] != sid for s in out):
                out.append({"id": sid, "business_id": bid, "out": gone})
    return out


def session_for(sc: dict, pts: list[PartTimer]) -> agent.FillSession:
    biz = businesses()[sc["business_id"]]
    staff = [e for e in team(sc["business_id"]) if e.id not in sc["out"]]
    rules = GOLD_RULES[sc["business_id"]].model_copy(deep=True)
    rules.constraints = [c for c in rules.constraints if getattr(c, "employee_id", None) not in sc["out"]]
    return agent.FillSession(biz, grid(sc["business_id"], biz), staff, rules, pts, bookings=[])


def oracle(s: agent.FillSession) -> dict:
    """The cheapest full fill the optimizer finds with every part-timer within the oracle radius."""
    gap_slots = [s.slot_by_id[sid] for sid in s.gap_alias.values()]
    pool = marketplace.pool_for_gaps(s.business, gap_slots, s.part_timers, s.bookings,
                                     max_distance_miles=ORACLE_RADIUS_MILES, per_gap=ORACLE_PER_GAP)
    r = fill_gaps(s.staff, pool, s.slots, s.staff_result, booking_fee_cents=s.fee,
                  time_limit_s=ORACLE_SECONDS, **s.kw)
    return {"added_cents": r["bill"]["total_cents"] - s.staff_result["bill"]["total_cents"],
            "remaining_gap_hours": s._gap_hours(r), "people": len({w for w, _ in r["assignments"]
                                                                   if w not in {x["id"] for x in s.staff}})}


def run_agent(provider=None, n_per_business: int = 5, workers: int = 1, only: list[str] | None = None,
              progress=print) -> dict:
    pts = part_timers()
    scs = [s for s in scenarios(n_per_business) if not only or s["id"] in only]

    def one(sc):
        s = session_for(sc, pts)
        gap_h = s._gap_hours(s.staff_result)
        best = oracle(s)
        row = {"id": sc["id"], "business_id": sc["business_id"], "staff_out": sc["out"],
               "gaps": len(s.gap_alias), "gap_hours": gap_h,
               "oracle_added": best["added_cents"] / 100, "oracle_remaining_hours": best["remaining_gap_hours"],
               "oracle_people": best["people"]}
        if not s.gap_alias:
            row.update(skipped="no open shifts")
            return row
        steps = []
        t0 = time.perf_counter()
        try:
            plan = agent.run_agent(s, on_step=lambda name, a, r: steps.append((name, a)), provider=provider)
        except Exception as e:
            plan, row["error"] = agent.fallback_plan(s), str(e)[:300]
        secs = time.perf_counter() - t0
        added = (plan["result"]["bill"]["total_cents"] - s.staff_result["bill"]["total_cents"]) / 100
        cov = lambda rem: 1.0 if not gap_h else (gap_h - rem) / gap_h  # noqa: E731
        widest = max([float((a or {}).get("max_distance_miles") or 5) for n, a in steps
                      if n == "search_marketplace"], default=0)
        row.update({
            "agent_added": added, "agent_remaining_hours": plan["remaining_gap_hours"],
            "agent_people": plan["people_booked"],
            "coverage_agent": round(cov(plan["remaining_gap_hours"]), 3),
            "coverage_oracle": round(cov(best["remaining_gap_hours"]), 3),
            "extra_cost_vs_oracle": round(added - best["added_cents"] / 100, 2),
            "matches_oracle": (plan["remaining_gap_hours"] <= best["remaining_gap_hours"] + 1e-9
                               and added <= best["added_cents"] / 100 * 1.01 + 0.01),
            "grounded": bool(plan.get("grounded")), "fallback": plan.get("turns") is None,
            "hours_left_vs_oracle": round(plan["remaining_gap_hours"] - best["remaining_gap_hours"], 2),
            "widest_search_miles": widest,
            "turns": plan.get("turns"), "tools": [n for n, _ in steps],
            "used_what_if": any(n == "what_if" for n, _ in steps),
            "explanation": plan.get("explanation", ""), "seconds": round(secs, 1)})
        progress(f"  {sc['id']}: agent {row['coverage_agent']:.0%} for ${added:,.2f} | "
                 f"optimizer {row['coverage_oracle']:.0%} for ${best['added_cents'] / 100:,.2f}"
                 f"{'' if row['grounded'] else ' | explanation replaced'}{' | FALLBACK' if row['fallback'] else ''}")
        return row

    rows = [r for r in _map(one, scs, workers)]
    run = [r for r in rows if "skipped" not in r]
    full = [r for r in run if r["coverage_oracle"] >= 0.999]
    return {"cases": rows, "summary": {
        "scenarios": len(run),
        "matches_optimizer": _pct(sum(r["matches_oracle"] for r in run), len(run)),
        "coverage_agent": _avg(r["coverage_agent"] for r in run),
        "coverage_optimizer": _avg(r["coverage_oracle"] for r in run),
        "full_fill_when_possible": _pct(sum(r["coverage_agent"] >= 0.999 for r in full), len(full)),
        "extra_open_hours_vs_optimizer": _avg(r["hours_left_vs_oracle"] for r in run),
        # cost is only comparable when both cover the same hours (covering less is always cheaper)
        "avg_extra_cost_same_coverage": _avg(r["extra_cost_vs_oracle"] for r in run
                                             if abs(r["hours_left_vs_oracle"]) < 1e-9),
        "widened_search": _pct(sum(r["widest_search_miles"] > 5 for r in run), len(run)),
        "grounded": _pct(sum(r["grounded"] for r in run), len(run)),
        "fallback": _pct(sum(r["fallback"] for r in run), len(run)),
        "avg_turns": _avg(r["turns"] for r in run if r["turns"]),
        "used_what_if": _pct(sum(r["used_what_if"] for r in run), len(run)),
        "avg_seconds": _avg(r["seconds"] for r in run),
    }}


# ---------------------------------------------------------------- 3. offer advice

EXPECTED = {"fits": "take it", "clash": "pass", "outside_availability": "worth a look",
            "over_max": "worth a look", "below_asking": "worth a look"}
MODEL_DECIDES = ("fits", "below_asking")   # the other three are settled by rules in code


def _shift(day, s, e, role) -> OfferShift:
    return OfferShift(slot_id=f"eval-{day}-{s}", day=day, start=from_q(s), end=from_q(e), role=role)


def advice_cases(per_kind: int = 4) -> list[dict]:
    biz = businesses()
    home = {"cafe": "biz_bean", "restaurant": "biz_maple"}
    picked: dict[str, list] = {k: [] for k in EXPECTED}
    for p in sorted(part_timers(), key=lambda p: p.id):
        b = biz[next((home[t] for t in p.business_types if t in home), "biz_bean")]
        if marketplace.distance_miles(p, b) is None or marketplace.distance_miles(p, b) > 5:
            continue
        role = p.roles[0].role
        w = marketplace.worker_for(p, [])
        free = lambda d, s, e: offers.is_available(w, {"id": "x", "day": d, "start_q": s, "end_q": e,  # noqa
                                                       "role": role, "need": 1, "skills": []})
        wins = [(d, to_q(a), to_q(z)) for d, ws in (p.availability or {}).items() for a, z in ws]
        fit = next(((d, s, s + 16) for d, s, e in wins for s in range(s, e - 15, 4) if free(d, s, s + 16)), None)
        tries = {
            "fits": [fit] if fit and p.max_hours >= 4 else [],
            "below_asking": [fit] if fit and p.max_hours >= 4 else [],
            "clash": [(d, to_q(a), min(to_q(z), to_q(a) + 16)) for d, a, z in p.blocked][:1],
            "outside_availability": [(d, to_q(b.hours[d][0]), to_q(b.hours[d][0]) + 16) for d in DAYS
                                     if p.availability and d not in p.availability and d in b.hours
                                     and not any(x[0] == d for x in p.blocked)][:1],
        }
        # over max: every free 4-hour block across the week until it passes their weekly max
        over, hours = [], 0.0
        for d, s, e in wins:
            for st in range(s, e - 15, 16):
                if free(d, st, st + 16) and hours <= p.max_hours:
                    over.append((d, st, st + 16))
                    hours += 4
        tries["over_max"] = [over] if hours > p.max_hours and len(over) <= 7 else []
        for kind, options in tries.items():
            if len(picked[kind]) >= per_kind or not options:
                continue
            shifts = options[0] if kind == "over_max" else [options[0]]
            rate = p.expected_rate_cents - 150 if kind == "below_asking" else p.expected_rate_cents
            o = Offer(id=f"eval-{kind}-{p.id}", business_id=b.id, part_timer_id=p.id, rate_cents=rate,
                      shifts=[_shift(d, s, e, role) for d, s, e in shifts], message="We'd love your help.")
            facts = offers.offer_facts(o, p, b, [])
            ok = {"fits": not (facts["clashes_with_your_commitments_or_bookings"] or facts["over_your_max"]
                               or facts["outside_your_stated_availability"]),
                  "clash": bool(facts["clashes_with_your_commitments_or_bookings"]),
                  "outside_availability": bool(facts["outside_your_stated_availability"])
                  and not facts["clashes_with_your_commitments_or_bookings"] and not facts["over_your_max"],
                  "over_max": facts["over_your_max"] and not facts["clashes_with_your_commitments_or_bookings"]
                  and not facts["outside_your_stated_availability"]}
            ok["below_asking"] = ok["fits"]
            if ok[kind]:
                picked[kind].append({"id": o.id, "kind": kind, "expected": EXPECTED[kind], "facts": facts})
    return [c for k in EXPECTED for c in picked[k]]


def run_advice(provider=None, workers: int = 3, per_kind: int = 4, only: list[str] | None = None,
               progress=print) -> dict:
    cases = [c for c in advice_cases(per_kind) if not only or c["id"] in only]

    def one(case):
        t0 = time.perf_counter()
        adv = offers.advise(case["facts"], provider=provider)
        template = adv == offers.template_advice(case["facts"]) and case["kind"] != "clash"
        row = {"id": case["id"], "kind": case["kind"], "expected": case["expected"],
               "got": adv.recommendation, "correct": adv.recommendation == case["expected"],
               "decided_by": "code" if case["kind"] not in MODEL_DECIDES else
               ("template (model answer not grounded)" if template else "model"),
               "reasons": adv.reasons, "seconds": round(time.perf_counter() - t0, 2)}
        progress(f"  {case['id']}: expected {case['expected']!r}, got {adv.recommendation!r}")
        return row

    rows = _map(one, cases, workers)
    model = [r for r in rows if r["decided_by"] != "code"]
    return {"cases": rows, "summary": {
        "cases": len(rows),
        "accuracy": _pct(sum(r["correct"] for r in rows), len(rows)),
        "model_decided_accuracy": _pct(sum(r["correct"] for r in model if r["decided_by"] == "model"),
                                       sum(r["decided_by"] == "model" for r in model)),
        "fell_back_to_template": _pct(sum(r["decided_by"] != "model" for r in model), len(model)),
        "unsafe_take_it": sum(r["got"] == "take it" and r["expected"] != "take it" for r in rows),
        "avg_seconds": _avg(r["seconds"] for r in rows),
    }}


# ---------------------------------------------------------------- helpers

def _map(fn, items, workers):
    if workers <= 1:
        return [fn(x) for x in items]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, items))


def _pct(n, d):
    return round(n / d, 3) if d else None


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


SUITES = {"rules": run_rules, "agent": run_agent, "advice": run_advice}
