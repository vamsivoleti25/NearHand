"""llm.py with a fake provider: no network, no cost."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from core import llm


class Shift(BaseModel):
    role: str
    hours: int


class FakeCompletions:
    def __init__(self, replies, reject_format=False):
        self.replies, self.calls, self.reject_format = list(replies), [], reject_format

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.reject_format and "response_format" in kwargs:
            raise RuntimeError("400: response_format json_schema not supported")
        content = self.replies.pop(0)
        usage = {"prompt_tokens": 1000, "completion_tokens": 200,
                 "prompt_tokens_details": {"cached_tokens": 600},
                 "completion_tokens_details": {"reasoning_tokens": 150}}
        msg = SimpleNamespace(content=content, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(llm, "DB_PATH", tmp_path / "test.db")

    def install(replies, reject_format=False):
        comp = FakeCompletions(replies, reject_format)
        monkeypatch.setattr(llm, "_client", lambda provider: SimpleNamespace(chat=SimpleNamespace(completions=comp)))
        return comp
    return install


def test_returns_validated_object(fake):
    fake(['{"role": "barista", "hours": 5}'])
    out = llm.call_json("t", "sys", "user", Shift, provider="baseten")
    assert out == Shift(role="barista", hours=5)


def test_second_identical_call_hits_the_cache(fake):
    comp = fake(['{"role": "barista", "hours": 5}'])
    llm.call_json("t", "sys", "user", Shift, provider="baseten")
    llm.call_json("t", "sys", "user", Shift, provider="baseten")
    assert len(comp.calls) == 1


def test_bad_json_is_retried_once(fake):
    comp = fake(['{"role": "barista"}', '```json\n{"role": "barista", "hours": 5}\n```'])
    out = llm.call_json("t", "sys", "user", Shift, provider="baseten")
    assert out.hours == 5 and len(comp.calls) == 2


def test_falls_back_when_provider_rejects_json_schema(fake):
    comp = fake(['{"role": "cashier", "hours": 4}'], reject_format=True)
    out = llm.call_json("t", "sys", "user", Shift, provider="fireworks")
    assert out.role == "cashier"
    assert "JSON schema" in comp.calls[-1]["messages"][0]["content"]


def test_every_call_sets_reasoning_effort(fake):
    comp = fake(['{"role": "barista", "hours": 5}'])
    llm.call_json("t", "sys", "user", Shift, provider="baseten")
    assert comp.calls[0]["reasoning_effort"] == "low"


def test_cost_is_logged(fake):
    fake(['{"role": "barista", "hours": 5}'])
    llm.call_json("parse_test", "sys", "user", Shift, provider="baseten")
    rows = llm.spend_summary()
    expected = (400 * 1.25 + 600 * 0.15 + 200 * 4.25) / 1e6
    assert rows[0]["task"] == "parse_test"
    assert rows[0]["cost_usd"] == round(expected, 4)
