"""The home page: both paths in, real numbers only, and the buttons land on the right screen."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core import db, llm

ROOT = Path(__file__).resolve().parent.parent
STAFF, WORK = "I need staff", "I'm looking for shifts"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "DB_PATH", tmp_path / "home.db")
    db.load_seed(force=True)


def app():
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()


def labels(at):
    return [b.label for b in at.button]


def test_app_opens_on_home_with_both_paths():
    at = app()
    assert not at.exception, at.exception
    assert STAFF in labels(at) and WORK in labels(at)


@pytest.mark.parametrize("label, view, lands_on", [(STAFF, "Manager", "Simplify rules"),
                                                   (WORK, "Part-timer", "No offers right now")])
def test_each_path_switches_view_and_lands_on_its_screen(label, view, lands_on):
    at = app()
    next(b for b in at.button if b.label == label).click().run()
    assert not at.exception, at.exception
    assert at.session_state["view"] == view
    text = " ".join([m.value for m in at.markdown] + [i.value for i in at.info] + labels(at))
    assert lands_on in text, f"{label!r} should land on the {view} home screen"


def test_home_makes_no_unbacked_claims():
    src = (ROOT / "views" / "home.py").read_text().lower()
    for banned in ("testimonial", "trusted by", "5 stars", "★★★★★"):
        assert banned not in src, f"home page must not contain {banned!r}: no invented social proof"
