"""Marketplace search, booking and the full gap-filling story on the seed data."""

import json
from pathlib import Path

import pytest

from core import db, llm, marketplace
from core.adapter import build_inputs
from core.schemas import Business, ConstraintSet, PartTimer, RoleExperience, SearchFilter
from core.solver import fill_gaps, solve_staff_only

SEED = Path(__file__).resolve().parent.parent / "data" / "seed"

CAFE_RULES = ConstraintSet.model_validate({"constraints": [  # gold parse of the café rules
    {"type": "unavailable", "employee_id": "emp_maya", "days": ["Tue"]},
    {"type": "max_hours", "role": "barista", "hours": 32},
    {"type": "require_skill", "skill": "keyholder", "at": "open"},
    {"type": "require_skill", "skill": "keyholder", "at": "close"},
    {"type": "min_rest", "hours": 10}]})


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "DB_PATH", tmp_path / "test.db")
    db.load_seed()
    return db


def pt(pid="pt_x", roles=("barista",), rate=1900, skills=(), zip="06095", **extra):
    return PartTimer(id=pid, name=pid, zip=zip, expected_rate_cents=rate, skills=list(skills),
                     roles=[RoleExperience(role=r, years=2) for r in roles], **extra)


def cafe():
    return Business(id="biz_bean", name="Bean", type="cafe", zip="06095", hours={"Sat": ("07:00", "17:00")})


def sat_slot(role="barista", start=28, end=48, skills=()):
    return {"id": "s", "day": "Sat", "start_q": start, "end_q": end, "role": role, "need": 1, "skills": list(skills)}


# ---------------------------------------------------------------- distance and search

def test_distance_between_windsor_and_bloomfield_is_a_few_miles():
    d = marketplace.distance_miles(pt(zip="06002"), cafe())
    assert 3 < d < 6


def test_search_filters_role_rate_distance_and_skills():
    pts = [pt("cheap", rate=1800), pt("dear", rate=2400), pt("far", zip="06109"),
           pt("cook", roles=["line_cook"]), pt("keys", skills=["keyholder"])]
    f = SearchFilter(roles=["barista"], max_rate_cents=2000, max_distance_miles=5)
    assert [c["id"] for c in marketplace.search(f, cafe(), pts, [])] == ["cheap", "keys"]
    f2 = SearchFilter(roles=["barista"], skills=["keyholder"])
    assert [c["id"] for c in marketplace.search(f2, cafe(), pts, [])] == ["keys"]


def test_search_respects_availability_window():
    p = pt(availability={"Sat": [("06:00", "12:00")]})
    f = SearchFilter(days=["Sat"], start="07:00", end="11:00")
    assert len(marketplace.search(f, cafe(), [p], [])) == 1
    f_late = SearchFilter(days=["Sat"], start="13:00", end="17:00")
    assert marketplace.search(f_late, cafe(), [p], []) == []


# ---------------------------------------------------------------- booking

def test_booking_blocks_the_slot_for_everyone_and_refuses_double_booking():
    p = pt()
    b1 = marketplace.book(p, cafe(), sat_slot(), [])
    other = Business(id="biz_maple", name="Maple", type="restaurant", zip="06002", hours={})
    f = SearchFilter(days=["Sat"], start="07:00", end="12:00")
    assert marketplace.search(f, other, [p], [b1]) == []
    with pytest.raises(marketplace.DoubleBooking):
        marketplace.book(p, other, sat_slot(start=32, end=52), [b1])


def test_commitments_elsewhere_hide_a_part_timer():
    p = pt(blocked=[("Sat", "08:00", "12:00")])
    assert marketplace.pool_for_gaps(cafe(), [sat_slot()], [p], []) == []


# ---------------------------------------------------------------- the seed data

def test_seed_is_deterministic_and_complete():
    pts = json.loads((SEED / "part_timers.json").read_text())
    assert len(pts) == 150 and len({p["id"] for p in pts}) == 150
    assert min(p["expected_rate_cents"] for p in pts) >= 1725  # above CT minimum wage
    assert all(PartTimer.model_validate(p) for p in pts)


def test_marketplace_fills_the_cafe_gaps(seeded):
    biz = seeded.business("biz_bean")
    inp = build_inputs(biz, seeded.grid("biz_bean"), seeded.team("biz_bean"), CAFE_RULES)
    staff_only = solve_staff_only(inp["workers"], inp["slots"], min_rest_q=inp["min_rest_q"])
    assert staff_only["gaps"], "the demo café should have a few gaps for the marketplace to fill"
    gap_slots = [s for s in inp["slots"] if s["id"] in staff_only["gaps"]]
    pool = marketplace.pool_for_gaps(biz, gap_slots, seeded.part_timers(), seeded.bookings())
    assert pool
    filled = fill_gaps(inp["workers"], pool, inp["slots"], staff_only, booking_fee_cents=500,
                       min_rest_q=inp["min_rest_q"])
    gap_h = lambda r: sum(n * (s["end_q"] - s["start_q"]) for s in inp["slots"] for sid, n in r["gaps"].items()
                          if s["id"] == sid)
    assert gap_h(filled) < gap_h(staff_only)
    assert filled["bill"]["booking_fees_cents"] > 0
    staff_ids = {w["id"] for w in inp["workers"]}
    assert {a for a in filled["assignments"] if a[0] in staff_ids} == set(staff_only["assignments"])
    market_shifts = {sid for wid, sid in filled["assignments"] if wid not in staff_ids}
    assert market_shifts <= set(staff_only["gaps"])  # part-timers only take gap shifts


def test_booked_part_timer_disappears_from_the_restaurants_search(seeded):
    cafe_biz, maple = seeded.business("biz_bean"), seeded.business("biz_maple")
    p = next(x for x in seeded.part_timers() if "Sat" in x.availability
             and "barista" in [r.role for r in x.roles] and not any(b[0] == "Sat" for b in x.blocked))
    s, e = p.availability["Sat"][0]
    slot = {"id": "x", "day": "Sat", "start_q": marketplace.to_q(s), "end_q": marketplace.to_q(s) + 16,
            "role": "barista", "need": 1, "skills": []}
    seeded.add_booking(marketplace.book(p, cafe_biz, slot, seeded.bookings()))
    f = SearchFilter(days=["Sat"], start=s, end=marketplace.from_q(marketplace.to_q(s) + 16))
    assert p.id not in [c["id"] for c in marketplace.search(f, maple, seeded.part_timers(), seeded.bookings(), limit=200)]
    seeded.reset_demo()
    assert seeded.bookings() == []
