"""The agent on the seed café, with a scripted fake model (no network, no cost)."""

import json

import pytest

from core import agent, db, llm
from tests.test_marketplace import CAFE_RULES


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "DB_PATH", tmp_path / "test.db")
    db.load_seed()
    return agent.FillSession(db.business("biz_bean"), db.grid("biz_bean"), db.team("biz_bean"),
                             CAFE_RULES, db.part_timers(), db.bookings())


def script(monkeypatch, turns):
    """Fake call_tools: each turn is a list of (tool, args), or a callable(session_results) -> list."""
    seen = []

    def fake(messages, tools, **kw):
        seen.append(messages)
        calls = turns.pop(0)
        if callable(calls):
            calls = calls(messages)
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": f"c{len(seen)}_{i}", "type": "function",
                                "function": {"name": n, "arguments": json.dumps(a)}}
                               for i, (n, a) in enumerate(calls)]}
    monkeypatch.setattr(agent.llm, "call_tools", fake)
    return seen


def test_gaps_have_short_ids_and_near_misses(session):
    g = session.get_gaps()
    assert g["gaps"] and all(x["gap_id"].startswith("g") for x in g["gaps"])
    assert any(x["staff_near_misses"] for x in g["gaps"])


def test_scripted_agent_fills_every_gap_and_stays_grounded(session, monkeypatch):
    def propose(messages):
        solved = json.loads(messages[-1]["content"])
        ids = sorted({b["part_timer_id"] for b in solved["bookings"]})
        return [("propose_plan", {"candidate_ids": ids,
                                  "explanation": f"All gaps covered, adding {solved['added_cost']} to the week."})]
    script(monkeypatch, [[("get_gaps", {})], [("search_marketplace", {})], [("solve_with", {})], propose])
    plan = agent.run_agent(session)
    assert plan["remaining_gaps"] == []
    assert plan["grounded"] and plan["turns"] == 4
    staff_ids = {w["id"] for w in session.staff}
    assert {a for a in plan["assignments"] if a[0] in staff_ids} == set(session.staff_result["assignments"])


def test_invented_numbers_are_caught_and_replaced(session, monkeypatch):
    script(monkeypatch, [[("get_gaps", {}), ("search_marketplace", {})],
                         [("propose_plan", {"candidate_ids": [], "explanation": "This saves $9,999.99 a week."})]])
    plan = agent.run_agent(session)
    assert not plan["grounded"] and "$9,999.99" in plan["ungrounded_numbers"]
    assert "$9,999.99" not in plan["explanation"]


def test_what_if_reports_a_comparison(session):
    session.search_marketplace()
    emp = next(w["id"] for w in session.staff if "barista" in w["roles"])
    r = session.what_if(emp, extra_hours=4)
    assert {"week_total_now", "week_total_with_change", "saves"} <= set(r)


def test_bad_tool_arguments_come_back_as_errors_not_crashes(session):
    assert "error" in session.call("what_if", {"employee_id": "emp_nobody"})
    assert "error" in session.call("no_such_tool", {})
    assert "error" in session.call("solve_with", {"bogus": 1})


def test_falls_back_when_the_model_never_finishes(session, monkeypatch):
    script(monkeypatch, [[("get_gaps", {})]] * agent.MAX_TURNS)
    plan = agent.run_agent(session)
    assert plan["note"].startswith("Fallback") and plan["remaining_gaps"] == []


def test_grounding_check():
    src = ['{"added_cost": "$405.75", "week_total": "$9,034.25", "gap_hours": 21.0}']
    assert agent.ungrounded_numbers("Adds $405.75; total $9,034.25 for 21 hours, 3 people.", src) == []
    assert agent.ungrounded_numbers("Saves $120 overall.", src) == ["$120"]


def test_plan_reports_wages_fees_and_the_best_what_if(session, monkeypatch):
    session.search_marketplace()
    session.what_ifs = [{"saves": "$11.00", "change": {}}, {"saves": "$119.00", "change": {}},
                        {"saves": "-$4.00", "change": {}}]
    out = session.propose_plan([], "Covered.")
    assert {"added_wages", "booking_fees", "added_cost"} <= set(out)
    assert agent._cents(out["added_wages"]) + agent._cents(out["booking_fees"]) == agent._cents(out["added_cost"])
    assert session.plan["best_what_if"]["saves"] == "$119.00"


def test_what_if_is_exact_and_monotone(session):
    session.search_marketplace()
    for w in session.staff:
        small = session.what_if(w["id"], extra_hours=5)
        big = session.what_if(w["id"], extra_hours=10)
        assert agent._cents(big["saves"]) >= agent._cents(small["saves"]), w["id"]  # more hours never costs more
        if not small["employee_would_cover"]:
            assert small["saves"] == "$0.00", w["id"]  # no change in who works -> no change in cost
