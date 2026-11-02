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


def test_app_shell_renders_both_views():
    at = run("app.py")
    assert not at.exception, at.exception
    at.sidebar.segmented_control[0].set_value("Part-timer").run()
    assert not at.exception, at.exception


def test_full_story_offers_accept_decline_replace(monkeypatch):
    from core import offers as offers_mod
    from core.schemas import Advice
    monkeypatch.setattr(offers_mod.llm, "call_json", lambda *a, **k: Advice(recommendation="take it",
                                                                            reasons=["You're free then."]))
    at = run("views/planner.py", business_id="biz_bean")
    click = lambda app, label: next(b for b in app.button if b.label == label).click().run()  # noqa: E731
    for label in ["Read my rules", "Build schedule", "Fill gaps with AI", "Send offers"]:
        click(at, label)
        assert not at.exception, (label, at.exception)
    sent = db.offers(business_id="biz_bean")
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

    # the manager sees the reopened shift and the agent finds a replacement
    at = run("views/planner.py", business_id="biz_bean")
    assert not at.exception, at.exception
    click(at, "Find replacements with AI")
    assert not at.exception, at.exception
    click(at, "Send offers")
    round2 = [o for o in db.offers(business_id="biz_bean") if o.round == 2]
    assert round2 and no.part_timer_id not in {o.part_timer_id for o in round2}


def test_offers_inbox_empty_state():
    at = run("views/offers.py", part_timer_id="pt_001")
    assert not at.exception and any("No offers" in i.value for i in at.info)
