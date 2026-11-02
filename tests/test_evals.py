"""The eval harness itself: scoring is right, and every suite runs end to end with the model faked."""

import json

import pytest

from core import agent, llm, offers, parsers
from core.schemas import Advice, ConstraintSet
from evals import harness as h


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "DB_PATH", tmp_path / "evals.db")
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")


BEAN = h.businesses()["biz_bean"]


def test_equivalent_parses_score_as_equal():
    gold = [{"type": "unavailable", "employee_id": "emp_kai", "days": ["Fri"], "start": "12:00"}]
    same = [{"type": "unavailable", "employee_id": "emp_kai", "days": ["Fri"], "start": "12:00", "end": "18:00"}]
    assert h.match(same, gold, BEAN)["exact"]                     # "to closing" == no end time
    whole = [{"type": "unavailable", "employee_id": "emp_kai", "days": ["Fri"], "start": "06:00"}]
    assert h.match(whole, [{"type": "unavailable", "employee_id": "emp_kai", "days": ["Fri"]}], BEAN)["exact"]
    every = [{"type": "require_skill", "skill": "keyholder", "at": "open",
              "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}]
    assert h.match(every, [{"type": "require_skill", "skill": "keyholder", "at": "open"}], BEAN)["exact"]
    m = h.match(same + [{"type": "min_rest", "hours": 8}], gold, BEAN)
    assert (m["tp"], m["fp"], m["fn"], m["exact"]) == (1, 1, 0, False)


def test_gold_cases_are_valid():
    for c in h.rule_cases():
        ConstraintSet.model_validate({"constraints": c["gold"]})
        assert c["gold"] or c.get("expect_question"), c["id"]


def test_rules_suite_scores_a_perfect_parser_perfectly(monkeypatch):
    by_text = {c["text"]: c for c in h.rule_cases()}
    monkeypatch.setattr(parsers, "parse_rules", lambda text, *a, **k: ConstraintSet.model_validate(
        {"constraints": by_text[text]["gold"],
         "ambiguities": ["Which day?"] if by_text[text].get("expect_question") else []}))
    s = h.run_rules(workers=1, progress=lambda *a: None)["summary"]
    assert s["f1"] == 1.0 and s["pass_rate"] == 1.0 and s["asked_when_unclear"] == 1.0


def test_agent_suite_compares_against_the_optimizer(monkeypatch):
    def scripted(messages, tools, **kw):
        n = sum(1 for m in messages if m["role"] == "assistant")
        steps = [("get_gaps", {}), ("search_marketplace", {}), ("solve_with", {})]
        if n < 3:
            name, args = steps[n]
        else:
            solved = json.loads(messages[-1]["content"])
            name, args = "propose_plan", {"candidate_ids": sorted({b["part_timer_id"] for b in solved["bookings"]}),
                                          "explanation": f"Adds {solved['added_cost']}."}
        return {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"t{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
    monkeypatch.setattr(agent.llm, "call_tools", scripted)
    out = h.run_agent(only=["biz_bean-normal"], progress=lambda *a: None)
    row = out["cases"][0]
    assert row["gaps"] == 4 and row["grounded"] and not row["fallback"] and row["turns"] == 4
    assert row["coverage_agent"] <= row["coverage_oracle"] + 1e-9   # the optimizer is the ceiling
    assert out["summary"]["scenarios"] == 1


def test_advice_suite_cases_and_scoring(monkeypatch):
    cases = h.advice_cases()
    assert {c["kind"] for c in cases} == set(h.EXPECTED)

    def fake(task, system, user, schema, **kw):
        f = json.loads(user)
        low = f["offered_rate"] < f["your_asking_rate"]
        return Advice(recommendation="worth a look" if low else "take it",
                      reasons=[f"It's {f['distance_miles']} miles away."])
    monkeypatch.setattr(offers.llm, "call_json", fake)
    s = h.run_advice(workers=1, progress=lambda *a: None)["summary"]
    assert s["accuracy"] == 1.0 and s["unsafe_take_it"] == 0 and s["fell_back_to_template"] == 0


def test_split_or_repeated_time_off_scores_as_the_same_rule():
    gold = [{"type": "unavailable", "employee_id": "emp_leo", "days": ["Sat", "Sun"]}]
    seen = [{"type": "unavailable", "employee_id": "emp_leo", "days": ["Sat", "Sun"], "start": "07:00", "end": "17:00"},
            {"type": "unavailable", "employee_id": "emp_leo", "days": ["Sat"], "start": "07:00", "end": "17:00"},
            {"type": "unavailable", "employee_id": "emp_leo", "days": ["Sun"]}]
    assert h.match(seen, gold, BEAN)["exact"]


def test_parser_tidies_time_off_into_one_rule_per_person():
    from core.schemas import Unavailable
    got = parsers.tidy_constraints([
        Unavailable(employee_id="emp_leo", days=["Sat", "Sun"], start="07:00", end="17:00"),
        Unavailable(employee_id="emp_leo", days=["Sat"]),
        Unavailable(employee_id="emp_kai", days=["Fri"], start="12:00"),
        Unavailable(employee_id="emp_kai", days=["Fri"], start="12:00")], BEAN)
    assert [(c.employee_id, c.days, c.start) for c in got] == [("emp_leo", ["Sat", "Sun"], None),
                                                              ("emp_kai", ["Fri"], "12:00")]
