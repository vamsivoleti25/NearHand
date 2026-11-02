"""Ask the team first: the optimizer offers open shifts to staff as extra hours when that's cheaper, or when
nobody nearby qualifies; staff accept or decline in their own inbox; a 'no' reopens the shift."""

import pytest

from core import db, llm, offers
from core.agent import FillSession
from core.schemas import Account
from evals import harness as h


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "DB_PATH", tmp_path / "x.db")
    db.load_seed(force=True)


def session(bid="biz_maple", extra=8, out=(), no_extra_for=()):
    sc = {"id": "t", "business_id": bid, "out": list(out)}
    s = h.session_for(sc, h.part_timers())
    return FillSession(s.business, h.grid(bid, s.business), [e for e in h.team(bid) if e.id not in out],
                       h.GOLD_RULES[bid], h.part_timers(), [], staff_extra_hours=extra, no_extra_for=no_extra_for)


def fill(s):
    s.search_marketplace(max_distance_miles=10)
    return s.solve_with()


def test_team_first_covers_shifts_no_part_timer_can():
    without, with_team = fill(session(extra=0)), fill(session(extra=8))
    assert with_team["remaining_gap_hours"] < without["remaining_gap_hours"]
    assert with_team["team_extra_hours"], "team members pick up shifts no part-timer nearby qualifies for"
    for t in with_team["team_extra_hours"]:
        assert 0 < t["extra_hours"] <= 8 and t["hours_after"] == t["hours_before"] + t["extra_hours"]


def test_overtime_only_past_40_and_paid_at_overtime_rate():
    r = fill(session(extra=8))
    for t in r["team_extra_hours"]:
        assert t["overtime_hours"] == max(0, t["hours_after"] - 40) - max(0, t["hours_before"] - 40)


def test_extra_hours_offers_accept_and_decline():
    s = session(extra=8)
    fill(s)
    s.propose_plan([], "")
    biz, team = db.business("biz_maple"), {e.id: e for e in db.team("biz_maple")}
    made = offers.make_offers(biz, s.plan["assignments"], s.slots, {p.id: p for p in db.part_timers()},
                              employees=team, gaps=s.staff_result["gaps"])
    extra = [o for o in made if o.kind == "extra_hours"]
    assert extra and all(o.employee_id in team and not o.part_timer_id for o in extra)
    for o in made:
        db.save_offer(o)
    first = extra[0]
    assert [o.id for o in db.offers(business_id="biz_maple", employee_id=first.employee_id)] == [first.id]
    assert not db.offers(part_timer_id=first.employee_id)

    hours_now = offers.scheduled_hours(first.employee_id, s.staff_result, s.slots)
    f = offers.extra_hours_facts(first, team[first.employee_id], biz, hours_now)
    assert f["hours_after_accepting"] == hours_now + f["extra_hours"]

    yes, booked = offers.respond(first, True, None, biz, [])
    assert yes.status == "accepted" and booked == []          # no marketplace booking for staff
    view = offers.week_view(s.inp, s.staff_result, [yes], {}, 500)
    assert all((first.employee_id, sh.slot_id) in view["assignments"] for sh in first.shifts)
    assert view["extra_cells"][(first.employee_id, first.shifts[0].slot_id)] == "accepted"

    no, _ = offers.respond(extra[1], False, None, biz, []) if len(extra) > 1 else (None, None)
    if no:
        view2 = offers.week_view(s.inp, s.staff_result, [yes, no], {}, 500)
        assert all(view2["gaps"].get(sh.slot_id) for sh in no.shifts)   # the shift is open again
        *_, skip = offers.replacement_inputs(s.staff_result, view2, [yes, no], db.part_timers(), [], "biz_maple")
        assert no.employee_id in skip and yes.employee_id in skip


def test_replacement_round_does_not_ask_the_same_person_again():
    s = session(extra=8)
    r = fill(s)
    asked = {t["employee_id"] for t in r["team_extra_hours"]}
    again = fill(session(extra=8, no_extra_for=asked))
    assert not asked & {t["employee_id"] for t in again["team_extra_hours"]}


def test_every_employee_and_part_timer_has_an_account():
    accts = db.accounts()
    assert {a.employee_id for a in accts if a.role == "staff"} >= {e.id for e in db.team("biz_bean")}
    assert len([a for a in accts if a.role == "part_timer"]) == len(db.part_timers())
    owner = db.account_by_email("dana@beanandco.example")
    assert owner and owner.role == "owner" and owner.business_id == "biz_bean"
    db.save_account(Account(id="acct_new", role="part_timer", name="New Person", email="New@Example.com",
                            part_timer_id="pt_new"))
    assert db.account_by_email("new@example.com").id == "acct_new"
