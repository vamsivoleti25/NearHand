"""Parsed objects -> solver inputs -> schedule, with no model calls."""

from core import parsers
from core.adapter import build_inputs, grid_to_slots
from core.schemas import Business, ConstraintSet, Employee, StaffingGrid
from core.solver import solve
from core.taxonomy import DAYS


def cafe() -> Business:
    return Business(id="biz_bean", name="Bean & Co.", type="cafe", zip="06095",
                    hours={d: ("06:00", "14:00") for d in DAYS[:5]})


def emp(name, roles, rate=1600, skills=(), max_hours=40, off=()):
    return Employee(id=f"emp_{name.lower()}", name=name, roles=roles, skills=list(skills),
                    rate_cents=rate, ot_rate_cents=round(rate * 1.5), max_hours=max_hours,
                    unavailable_days=list(off))


def test_default_grid_covers_every_role_and_open_day():
    grid = parsers.default_grid(cafe())
    roles = {b.role for b in grid.blocks}
    assert roles == {"barista", "cashier", "shift_lead", "dishwasher_busser"}
    assert {b.day for b in grid.blocks} == set(DAYS[:5])
    assert all(b.start >= "06:00" and b.end <= "14:00" for b in grid.blocks)


def test_toggle_adds_drive_thru_to_grid():
    b = cafe().model_copy(update={"toggles": ["drive_thru"]})
    assert "drive_thru_order" in {blk.role for blk in parsers.default_grid(b).blocks}


def test_unavailable_rule_keeps_maya_off_tuesday():
    grid = StaffingGrid.model_validate({"blocks": [
        {"day": "Tue", "role": "barista", "start": "06:00", "end": "11:00", "headcount": 1}]})
    team = [emp("Maya", ["barista"], rate=1500), emp("Leo", ["barista"], rate=1900)]
    cs = ConstraintSet.model_validate({"constraints": [
        {"type": "unavailable", "employee_id": "emp_maya", "days": ["Tue"]}]})
    inp = build_inputs(cafe(), grid, team, cs)
    r = solve(inp["workers"], inp["slots"], min_rest_q=inp["min_rest_q"])
    assert [w for w, _ in r["assignments"]] == ["emp_leo"]


def test_role_hour_cap_applies_to_everyone_in_the_role():
    team = [emp("Maya", ["barista"]), emp("Ana", ["cashier"])]
    cs = ConstraintSet.model_validate({"constraints": [{"type": "max_hours", "role": "barista", "hours": 32}]})
    inp = build_inputs(cafe(), StaffingGrid(blocks=[]), team, cs)
    caps = {w["id"]: w["max_q"] for w in inp["workers"]}
    assert caps == {"emp_maya": 128, "emp_ana": 160}


def test_keyholder_at_open_splits_one_headcount_off():
    grid = StaffingGrid.model_validate({"blocks": [
        {"day": "Mon", "role": "barista", "start": "06:00", "end": "11:00", "headcount": 2}]})
    cs = ConstraintSet.model_validate({"constraints": [
        {"type": "require_skill", "skill": "keyholder", "at": "open", "days": ["Mon"]}]})
    team = [emp("Maya", ["barista"], skills=["keyholder"], rate=1800), emp("Leo", ["barista"], rate=1500),
            emp("Sam", ["barista"], rate=1500)]
    inp = build_inputs(cafe(), grid, team, cs)
    needs = sorted((s["need"], tuple(s["skills"])) for s in inp["slots"])
    assert needs == [(1, ()), (1, ("keyholder",))]
    r = solve(inp["workers"], inp["slots"])
    assert "emp_maya" in {w for w, _ in r["assignments"]}  # the only keyholder, despite costing more


def test_fixed_assignment_pins_a_shift():
    grid = StaffingGrid.model_validate({"blocks": [
        {"day": "Mon", "role": "barista", "start": "06:00", "end": "11:00", "headcount": 1}]})
    team = [emp("Maya", ["barista"], rate=2000), emp("Leo", ["barista"], rate=1500)]
    cs = ConstraintSet.model_validate({"constraints": [
        {"type": "fixed_assignment", "employee_id": "emp_maya", "day": "Mon", "start": "06:00", "end": "11:00"}]})
    inp = build_inputs(cafe(), grid, team, cs)
    r = solve(inp["workers"], inp["slots"], fixed=inp["fixed"])
    assert [w for w, _ in r["assignments"]] == ["emp_maya"]


def test_soft_rules_are_reported_not_silently_dropped():
    cs = ConstraintSet.model_validate({"constraints": [
        {"type": "prefer", "employee_id": "emp_maya", "days": ["Sat"]}, {"type": "budget_cap", "dollars": 5000}]})
    inp = build_inputs(cafe(), StaffingGrid(blocks=[]), [emp("Maya", ["barista"])], cs)
    assert inp["not_enforced"] == ["prefer", "budget_cap"]


def test_parse_rules_drops_invented_employee_ids(monkeypatch):
    fake = ConstraintSet.model_validate({"constraints": [
        {"type": "unavailable", "employee_id": "emp_maya", "days": ["Tue"]},
        {"type": "unavailable", "employee_id": "emp_zoe", "days": ["Wed"]}]})
    monkeypatch.setattr(parsers.llm, "call_json", lambda *a, **k: fake.model_copy(deep=True))
    out = parsers.parse_rules("@Maya off Tue, @Zoe off Wed", [emp("Maya", ["barista"])])
    assert [c.employee_id for c in out.constraints] == ["emp_maya"]
    assert any("emp_zoe" in q for q in out.ambiguities)


def test_grid_slot_ids_are_unique():
    slots = grid_to_slots(parsers.default_grid(cafe()))
    assert len({s["id"] for s in slots}) == len(slots)
