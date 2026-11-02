"""Offers: bundle, decide, accept or decline, reopen, replace."""

import pytest

from core import agent, db, llm, offers
from core.schemas import Advice, Business, OfferShift, PartTimer, RoleExperience
from tests.test_marketplace import CAFE_RULES


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "DB_PATH", tmp_path / "o.db")
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    db.load_seed(force=True)
    return db


@pytest.fixture
def planned(seeded):
    """The seed café's week, gaps filled by the fixed agent sequence."""
    s = agent.FillSession(seeded.business("biz_bean"), seeded.grid("biz_bean"), seeded.team("biz_bean"),
                          CAFE_RULES, seeded.part_timers(), seeded.bookings())
    plan = agent.fallback_plan(s)
    pts = {p.id: p for p in seeded.part_timers()}
    made = offers.make_offers(s.business, plan["result"]["assignments"], s.slots, pts)
    for o in made:
        seeded.save_offer(o)
    return s, made, pts


def pt(**kw):
    base = dict(id="pt_x", name="Pat Doe", zip="06095", expected_rate_cents=1800,
                roles=[RoleExperience(role="barista", years=2)], availability={"Sat": [("06:00", "14:00")]},
                max_hours=20)
    return PartTimer(**{**base, **kw})


def cafe():
    return Business(id="biz_bean", name="Bean", type="cafe", zip="06095", hours={})


def offer(shifts=(("Sat", "07:00", "12:00"),), rate=1800):
    return offers.Offer(id="of_1", business_id="biz_bean", part_timer_id="pt_x", rate_cents=rate,
                        shifts=[OfferShift(slot_id=f"s{i}", day=d, start=a, end=b, role="barista")
                                for i, (d, a, b) in enumerate(shifts)])


def test_one_offer_per_part_timer_with_their_shifts_bundled(planned):
    s, made, _ = planned
    assert made and len({o.part_timer_id for o in made}) == len(made)
    assert sum(len(o.shifts) for o in made) == sum(s.staff_result["gaps"].values())


def test_facts_compute_pay_hours_and_fit():
    f = offers.offer_facts(offer(), pt(), cafe(), [])
    assert f["total_hours"] == 5 and f["total_pay"] == "$90.00"
    assert f["clashes_with_your_commitments_or_bookings"] == [] and not f["over_your_max"]
    f2 = offers.offer_facts(offer(), pt(blocked=[("Sat", "08:00", "10:00")]), cafe(), [])
    assert f2["clashes_with_your_commitments_or_bookings"] == ["Sat 07:00-12:00"]
    f3 = offers.offer_facts(offer((("Sun", "07:00", "12:00"),)), pt(), cafe(), [])
    assert f3["outside_your_stated_availability"] == ["Sun 07:00-12:00"]


def test_clash_is_a_hard_pass_decided_in_code(monkeypatch):
    monkeypatch.setattr(offers.llm, "call_json", lambda *a, **k: pytest.fail("model should not be asked"))
    f = offers.offer_facts(offer(), pt(blocked=[("Sat", "08:00", "10:00")]), cafe(), [])
    assert offers.advise(f).recommendation == "pass"


def test_invented_numbers_in_advice_fall_back_to_template(monkeypatch):
    monkeypatch.setattr(offers.llm, "call_json", lambda *a, **k: Advice(
        recommendation="take it", reasons=["You'll earn $500 for this."]))
    adv = offers.advise(offers.offer_facts(offer(), pt(), cafe(), []))
    assert "$500" not in " ".join(adv.reasons) and "$90.00" in " ".join(adv.reasons)


def test_no_plain_take_it_past_the_weekly_max(monkeypatch):
    monkeypatch.setattr(offers.llm, "call_json", lambda *a, **k: Advice(recommendation="take it", reasons=["Ok."]))
    f = offers.offer_facts(offer(), pt(max_hours=4), cafe(), [])
    assert offers.advise(f).recommendation == "worth a look"


def test_accept_books_all_shifts_and_decline_books_none():
    o = offer((("Sat", "07:00", "12:00"), ("Sat", "12:00", "13:00")))
    accepted, made = offers.respond(o, True, pt(), cafe(), [])
    assert accepted.status == "accepted" and len(made) == 2 and made[0].rate_cents == 1800
    declined, none = offers.respond(o, False, pt(), cafe(), [])
    assert declined.status == "declined" and none == []
    with pytest.raises(ValueError):
        offers.respond(accepted, True, pt(), cafe(), [])


def test_accept_is_all_or_nothing_when_something_clashes():
    o = offer((("Sat", "07:00", "12:00"), ("Sat", "12:00", "13:00")))
    _, first = offers.respond(o, True, pt(), cafe(), [])
    again, made = offers.respond(o, True, pt(), cafe(), first[:1])
    assert again.status == "unavailable" and made == []


def test_decline_reopens_the_gap_and_replacement_skips_the_decliner(planned, seeded):
    s, made, pts = planned
    view = offers.week_view(s.inp, s.staff_result, made, pts)
    assert view["gaps"] == {}
    gone = made[0].model_copy(update={"status": "declined"})
    rest = [gone] + made[1:]
    view2 = offers.week_view(s.inp, s.staff_result, rest, pts)
    assert sum(view2["gaps"].values()) == len(gone.shifts)
    sr, pool, held, covered, _ = offers.replacement_inputs(s.staff_result, view2, rest, seeded.part_timers(),
                                                        seeded.bookings(), "biz_bean")
    assert gone.part_timer_id not in {p.id for p in pool}
    s2 = agent.FillSession(s.business, seeded.grid("biz_bean"), seeded.team("biz_bean"), CAFE_RULES, pool, held,
                           staff_result=sr, covered=covered)
    plan2 = agent.fallback_plan(s2)
    newcomers = {w for w, _ in plan2["result"]["assignments"] if w in pts}
    assert gone.part_timer_id not in newcomers
    assert len(plan2["remaining_gaps"]) <= len(gone.shifts)
    assert len(s2.gap_alias) == len(gone.shifts)  # only the reopened shifts are in play
