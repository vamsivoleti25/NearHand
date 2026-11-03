"""Every screen renders, and the planner flow works end to end, with the model calls faked."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core import agent, db, llm, parsers
from core.schemas import PartTimerProfile, RoleExperience, SearchFilter
from tests.test_marketplace import CAFE_RULES


@pytest.fixture(autouse=True)
def fakes(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "DB_PATH", tmp_path / "ui.db")
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    db.load_seed(force=True)
    monkeypatch.setattr(parsers, "parse_rules", lambda *a, **k: CAFE_RULES.model_copy(deep=True))
    monkeypatch.setattr(parsers, "parse_search", lambda *a, **k: SearchFilter(roles=["barista"], days=["Sat"],
                                                                                 start="07:00", end="12:00",
                                                                                 max_distance_miles=5))
    monkeypatch.setattr(parsers, "parse_experience", lambda *a, **k: PartTimerProfile(
        name="x", expected_rate_cents=2000, roles=[RoleExperience(role="barista", years=3)], skills=["keyholder"]))

    def scripted(messages, tools, **kw):  # the agent: gaps -> search -> solve -> propose
        n = sum(1 for m in messages if m["role"] == "assistant")
        plan = [("get_gaps", {}), ("search_marketplace", {}), ("solve_with", {})]
        if n < 3:
            name, args = plan[n]
        else:
            solved = json.loads(messages[-1]["content"])
            name, args = "propose_plan", {"candidate_ids": sorted({b["part_timer_id"] for b in solved["bookings"]}),
                                          "explanation": f"Covered every open shift for {solved['added_cost']}."}
        return {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"t{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
    monkeypatch.setattr(agent.llm, "call_tools", scripted)


ROOT = Path(__file__).resolve().parent.parent


def run(path, **state):
    at = AppTest.from_file(str(ROOT / path), default_timeout=120)
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


@pytest.mark.parametrize("page", ["views/marketplace.py", "views/team.py", "views/setup.py", "views/evals.py",
                                  "views/calendar.py", "views/profile.py", "views/offers.py",
                                  "views/planner.py"])
def test_every_screen_renders(page):
    at = run(page, business_id="biz_bean", part_timer_id="pt_001")
    assert not at.exception, at.exception


def test_app_shell_renders_for_every_kind_of_account():
    at = run("app.py")                                   # signed out: home and log in
    assert not at.exception, at.exception
    for acct in ("acct_owner_biz_bean", "acct_biz_bean_emp_ana", "acct_pt_001"):
        at = run("app.py", account_id=acct)
        assert not at.exception, (acct, at.exception)


def click(app, label, **match):
    btn = next(b for b in app.button if b.label == label and all(getattr(b, k) == v for k, v in match.items()))
    return btn.click().run()


def test_log_in_by_email_and_unknown_email():
    at = run("views/signin.py")
    at.text_input[0].input("nobody@example.com")
    at.button[0].click().run()
    assert any("No account" in e.value for e in at.error)
    at.text_input[0].input("dana@beanandco.example")
    at.button[0].click().run()
    assert at.session_state["account_id"] == "acct_owner_biz_bean" and at.session_state["view"] == "Manager"


def test_sign_up_as_a_part_timer_reads_experience_and_creates_the_account():
    at = run("views/signin.py", signin_mode="Create an account")
    click(at, "I want shifts")
    at.text_input[0].input("Robin Lee")
    at.text_input[1].input("robin@example.com")
    at.text_area[0].input("Three years as a barista, keyholder, about $20/hour")
    click(at, "Read my experience")
    assert not at.exception, at.exception
    click(at, "Create my account")
    assert not at.exception, at.exception
    a = db.account_by_email("robin@example.com")
    assert a and a.role == "part_timer" and at.session_state["account_id"] == a.id
    p = db.part_timer(a.part_timer_id)
    assert [r.role for r in p.roles] == ["barista"] and p.availability


def test_sign_up_a_business():
    at = run("views/signin.py", signin_mode="Create an account")
    click(at, "I need staff")
    for i, v in enumerate(["Sam Rivera", "sam@riverabakes.example", "Rivera Bakes"]):
        at.text_input[i].input(v)
    click(at, "Create my account")
    assert not at.exception, at.exception
    a = db.account_by_email("sam@riverabakes.example")
    assert a.role == "owner" and db.business(a.business_id).name == "Rivera Bakes"


def test_team_first_extra_hours_story():
    """Owner asks the team first; a team member accepts, another declines; the agent replaces the decline
    without asking that person again."""
    at = run("views/planner.py", business_id="biz_bean")
    for label in ["Read my rules", "Build schedule", "Fill gaps with AI", "Send offers"]:
        click(at, label)
        assert not at.exception, (label, at.exception)
    extra = [o for o in db.offers(business_id="biz_bean") if o.kind == "extra_hours"]
    assert extra, "with 'Ask my team first' on, some open shifts go to the team as extra hours"

    yes = extra[0]
    me = run("views/my_shifts.py", business_id="biz_bean", employee_id=yes.employee_id)
    assert not me.exception, me.exception
    click(me, "Accept")
    assert db.offer(yes.id).status == "accepted"
    if len(extra) > 1:
        no = extra[1]
        them = run("views/my_shifts.py", business_id="biz_bean", employee_id=no.employee_id)
        click(them, "Decline")
        assert db.offer(no.id).status == "declined"
        at = run("views/planner.py", business_id="biz_bean")   # the agent replaces the decline on its own
        assert not at.exception, at.exception
        round2 = [o for o in db.offers(business_id="biz_bean") if o.round == 2]
        assert no.employee_id not in {o.employee_id for o in round2}
        assert any(e.kind == "auto_replaced" for e in db.events(business_id="biz_bean"))


def test_full_story_offers_accept_decline_replace(monkeypatch):
    from core import offers as offers_mod
    from core.schemas import Advice
    monkeypatch.setattr(offers_mod.llm, "call_json", lambda *a, **k: Advice(recommendation="take it",
                                                                            reasons=["You're free then."]))
    at = run("views/planner.py", business_id="biz_bean")
    click = lambda app, label: next(b for b in app.button if b.label == label).click().run()  # noqa: E731
    for label in ["Read my rules", "Build schedule", "Fill gaps with AI", "Send offers"]:
        if label == "Fill gaps with AI":
            at.toggle[0].set_value(False).run()   # marketplace only: this story is about part-timers
        click(at, label)
        assert not at.exception, (label, at.exception)
    sent = [o for o in db.offers(business_id="biz_bean") if o.kind == "shift"]
    assert sent and all(o.status == "pending" for o in sent)

    # first part-timer accepts (after asking for advice), second declines
    yes, no = sent[0], sent[1]
    pt_view = run("views/offers.py", part_timer_id=yes.part_timer_id)
    click(pt_view, "Should I take this?")
    assert any("Take it" in m.value for m in pt_view.markdown)
    click(pt_view, "Accept")
    assert not pt_view.exception, pt_view.exception
    assert db.offer(yes.id).status == "accepted" and db.bookings(part_timer_id=yes.part_timer_id)
    pt_view2 = run("views/offers.py", part_timer_id=no.part_timer_id)
    click(pt_view2, "Decline")
    assert db.offer(no.id).status == "declined"

    # the agent replaces the decline as soon as the manager's planner loads, skipping whoever said no
    at = run("views/planner.py", business_id="biz_bean")
    assert not at.exception, at.exception
    round2 = [o for o in db.offers(business_id="biz_bean") if o.round == 2]
    assert round2 and no.part_timer_id not in {o.part_timer_id for o in round2}
    assert any("replacement" in m.value for m in at.success)


def test_auto_replace_off_leaves_it_to_the_owner(monkeypatch):
    from core import offers as offers_mod
    from core.schemas import Advice
    monkeypatch.setattr(offers_mod.llm, "call_json", lambda *a, **k: Advice(recommendation="pass", reasons=["x"]))
    at = run("views/planner.py", business_id="biz_bean")
    for label in ["Read my rules", "Build schedule", "Fill gaps with AI", "Send offers"]:
        if label == "Fill gaps with AI":
            at.toggle[0].set_value(False).run()
        click(at, label)
    next(t for t in at.toggle if t.label == "Auto-replace declines").set_value(False).run()
    no = next(o for o in db.offers(business_id="biz_bean") if o.kind == "shift")
    pt_view = run("views/offers.py", part_timer_id=no.part_timer_id)
    click(pt_view, "Decline")
    at = run("views/planner.py", business_id="biz_bean")
    assert not [o for o in db.offers(business_id="biz_bean") if o.round == 2]
    click(at, "Find replacements with AI")
    assert not at.exception, at.exception


def test_offers_inbox_empty_state():
    at = run("views/offers.py", part_timer_id="pt_001")
    assert not at.exception and any("No offers" in i.value for i in at.info)
