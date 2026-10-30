"""Small instances where the right answer is obvious by hand."""

import time

from core.solver import bill, eligible, solve, solve_staff_only, solve_with_pool, what_if
from core.taxonomy import DAYS, to_q


def worker(wid, rate, roles=("barista",), skills=(), max_h=40, ot=None, **extra):
    return {"id": wid, "name": wid, "roles": list(roles), "skills": list(skills),
            "rate_cents": rate, "ot_rate_cents": ot if ot is not None else round(rate * 1.5),
            "max_q": max_h * 4, **extra}


def slot(sid, day, start, end, role="barista", need=1, skills=()):
    return {"id": sid, "day": day, "start_q": to_q(start), "end_q": to_q(end),
            "role": role, "need": need, "skills": list(skills)}


# 1
def test_cheaper_barista_gets_the_shift():
    r = solve([worker("a", 1700), worker("b", 1500)], [slot("s1", "Mon", "06:00", "11:00")])
    assert r["assignments"] == [("b", "s1")]
    assert r["gaps"] == {}
    assert r["bill"]["total_cents"] == 1500 * 5


# 2
def test_nobody_available_becomes_a_gap():
    staff = [worker("a", 1500, unavailable_days=["Tue"])]
    r = solve(staff, [slot("s1", "Tue", "06:00", "11:00", need=2)])
    assert r["assignments"] == []
    assert r["gaps"] == {"s1": 2}


def test_skills_and_role_requirements_are_enforced():
    s = slot("s1", "Mon", "06:00", "11:00", skills=["keyholder"])
    assert not eligible(worker("a", 1500), s)
    assert eligible(worker("a", 1500, skills=["keyholder"]), s)
    cook = slot("c1", "Mon", "11:00", "19:00", role="line_cook")  # needs food_handler
    assert not eligible(worker("c", 1800, roles=["line_cook"]), cook)


# 3
def _forty_hours_plus_one(other_rate):
    """'a' must work five 8h shifts (only one available), then a 6th shift: a's overtime or b."""
    slots = [slot(f"d{i}", d, "08:00", "16:00") for i, d in enumerate(DAYS[:5])]
    slots.append(slot("sat", "Sat", "08:00", "16:00"))
    a = worker("a", 1500, max_h=48, ot=2250)
    b = worker("b", other_rate, availability={"Sat": [(0, 96)]})
    return solve([a, b], slots)


def test_overtime_avoided_when_it_costs_more():
    r = _forty_hours_plus_one(other_rate=1700)  # b at $17 < a's overtime at $22.50
    assert ("b", "sat") in r["assignments"]
    assert r["bill"]["overtime_cents"] == 0


def test_overtime_used_when_it_is_cheaper():
    r = _forty_hours_plus_one(other_rate=2400)  # b at $24 > a's overtime at $22.50
    assert ("a", "sat") in r["assignments"]
    assert r["bill"]["overtime_cents"] == (2250 - 1500) * 8


# 4
def test_marketplace_fills_only_what_staff_cannot():
    staff = [worker("maya", 1600, unavailable_days=["Tue"])]
    pool = [worker("priya", 1800)]
    slots = [slot("mon", "Mon", "06:00", "11:00"), slot("tue", "Tue", "06:00", "11:00")]
    first = solve_staff_only(staff, slots)
    assert first["gaps"] == {"tue": 1}
    second = solve_with_pool(staff, pool, slots, booking_fee_cents=500)
    assert set(second["assignments"]) == {("maya", "mon"), ("priya", "tue")}
    assert second["gaps"] == {}
    assert second["bill"]["booking_fees_cents"] == 500


# 5
def test_booking_fee_prefers_one_person_for_several_gaps():
    slots = [slot("tue", "Tue", "06:00", "11:00"), slot("thu", "Thu", "06:00", "11:00")]
    x = worker("x", 1800, availability={"Tue": [(0, 96)]})       # cheaper, Tuesday only
    y = worker("y", 1850)                                         # a bit dearer, both days
    no_fee = solve_with_pool([], [x, y], slots, booking_fee_cents=0)
    assert ("x", "tue") in no_fee["assignments"]
    with_fee = solve_with_pool([], [x, y], slots, booking_fee_cents=2000)
    assert set(with_fee["assignments"]) == {("y", "tue"), ("y", "thu")}


def test_calendar_blocks_hide_a_booked_part_timer():
    s = slot("tue", "Tue", "06:00", "11:00")
    priya = worker("priya", 1800, blocked=[("Tue", to_q("06:00"), to_q("11:00"))])
    assert not eligible(priya, s)


def test_min_rest_between_close_and_open():
    slots = [slot("close", "Mon", "14:00", "23:00"), slot("open", "Tue", "05:00", "11:00")]
    r = solve([worker("a", 1500)], slots, min_rest_q=10 * 4)  # 6h rest < 10h: can't do both
    assert len(r["assignments"]) == 1


def test_what_if_raising_a_cap_fills_a_gap():
    slots = [slot("mon", "Mon", "08:00", "16:00"), slot("tue", "Tue", "08:00", "16:00")]
    staff = [worker("maya", 1600, max_h=8)]
    r = what_if(staff, [], slots, {"type": "raise_cap", "worker_id": "maya", "hours": 8})
    assert r["gap_hours_delta"] == -8
    assert r["after_total_cents"] == 1600 * 16


def test_bill_matches_hand_calculation():
    w = [worker("a", 2000, ot=3000)]
    slots = [slot(f"s{i}", d, "08:00", "17:00") for i, d in enumerate(DAYS[:5])]  # 45 hours
    b = bill(w, slots, [("a", s["id"]) for s in slots])
    assert b["base_cents"] == 2000 * 45
    assert b["overtime_cents"] == (3000 - 2000) * 5


def test_cafe_week_solves_quickly():
    roles = ["barista", "cashier", "shift_lead"]
    staff = [worker(f"e{i}", 1500 + 50 * i, roles=[roles[i % 3], "cashier"], max_h=32)
             for i in range(15)]
    slots = [slot(f"{d}-{r}-{h}", d, f"{h:02d}:00", f"{h + 6:02d}:00", role=r, need=2 if r != "shift_lead" else 1)
             for d in DAYS for r in roles for h in (6, 12)]
    t0 = time.time()
    r = solve(staff, slots)
    assert r["status"] in ("OPTIMAL", "FEASIBLE")
    assert time.time() - t0 < 5


def test_fill_gaps_keeps_staff_and_only_fills_gaps():
    from core.solver import fill_gaps
    slots = [slot("mon", "Mon", "06:00", "11:00"), slot("tue", "Tue", "06:00", "11:00")]
    staff = [worker("maya", 2000, unavailable_days=["Tue"])]
    cheap = worker("priya", 1725)  # cheaper than Maya, but must not take Maya's Monday
    first = solve_staff_only(staff, slots)
    r = fill_gaps(staff, [cheap], slots, first, booking_fee_cents=500)
    assert set(r["assignments"]) == {("maya", "mon"), ("priya", "tue")}
