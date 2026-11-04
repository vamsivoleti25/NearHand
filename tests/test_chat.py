"""Chat on offers, the activity feed, and each visitor's private copy of the demo data."""

from core import db, llm
from tests.test_ui import fakes, run  # noqa: F401  (autouse fixture: temp database, faked model calls)


def click(app, label):
    return next(b for b in app.button if b.label == label).click().run()


def send_in(app, text, placeholder_start="Message"):
    box = next(t for t in app.text_input if (t.placeholder or "").startswith(placeholder_start))
    box.input(text)
    return click(app, "Send")


def owner_sends_offers():
    at = run("views/planner.py", business_id="biz_bean")
    for label in ["Simplify rules", "Build schedule", "Fill gaps with AI", "Send offers"]:
        if label == "Fill gaps with AI":
            at.toggle[0].set_value(False).run()  # marketplace only: part-timers get the offers
        click(at, label)
        assert not at.exception, (label, at.exception)
    return [o for o in db.offers(business_id="biz_bean") if o.kind == "shift"]


def test_messages_unread_and_read():
    o = owner_sends_offers()[0]
    db.send_message(o, from_business=False, sender="Pat", text="Can I start at 7 instead?")
    assert db.unread(True, business_id="biz_bean") == {o.id: 1}         # the owner has one to read
    assert db.unread(False, worker_id=o.part_timer_id) == {}             # the part-timer wrote it
    db.mark_read(o.id, by_business=False)                                 # own messages: nothing changes
    assert db.unread(True, business_id="biz_bean") == {o.id: 1}
    db.mark_read(o.id, by_business=True)
    assert db.unread(True, business_id="biz_bean") == {}
    db.send_message(o, from_business=True, sender="Bean & Co.", text="Sure, 7 works.")
    assert db.unread(False, worker_id=o.part_timer_id) == {o.id: 1}
    assert [m.text for m in db.messages(o.id)] == ["Can I start at 7 instead?", "Sure, 7 works."]


def test_offer_changes_are_logged():
    sent = owner_sends_offers()
    kinds = [e.kind for e in db.events(business_id="biz_bean", limit=100)]
    for k in ("rules_read", "schedule_built", "plan_proposed", "offer_sent"):
        assert k in kinds, k
    o = sent[0]
    db.save_offer(o.model_copy(update={"status": "accepted"}))
    db.save_offer(db.offer(o.id))                                         # same status again: no new event
    mine = db.events(worker_id=o.part_timer_id)
    assert [e.kind for e in mine].count("accepted") == 1
    assert "accepted" in mine[0].text


def test_part_timer_and_owner_chat_in_the_app():
    o = owner_sends_offers()[0]
    pt = run("views/offers.py", part_timer_id=o.part_timer_id)
    send_in(pt, "Is there parking nearby?")
    assert not pt.exception, pt.exception
    assert db.unread(True, business_id="biz_bean") == {o.id: 1}

    owner = run("views/planner.py", business_id="biz_bean")
    assert not owner.exception, owner.exception
    assert any("1 new message" in m.value for m in owner.markdown)       # the hint line says so
    owner.selectbox(key="msg_pick").set_value(o.id).run()
    assert db.unread(True, business_id="biz_bean") == {}                  # opening the thread reads it
    send_in(owner, "Yes, behind the shop.")
    assert [m.from_business for m in db.messages(o.id)] == [False, True]

    pt = run("views/offers.py", part_timer_id=o.part_timer_id)
    assert any("Yes, behind the shop." in m.value for m in pt.markdown)
    assert any("Bean" in e.text and "message" == e.kind for e in db.events(worker_id=o.part_timer_id))


def test_activity_tab_and_next_step():
    at = run("views/planner.py", business_id="biz_bean")
    assert any("Simplify rules" in m.value for m in at.markdown)           # the hint says what to do first
    click(at, "Simplify rules")
    assert any("Build schedule" in m.value for m in at.markdown)
    assert any("Simplified the rules" in m.value for m in at.markdown)          # and the Activity tab shows it


def test_each_visitor_gets_their_own_demo_data(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    try:
        llm.use_db(a)
        db.load_seed(force=True)
        db.log_event("biz_bean", "note", "only in a")
        llm.use_db(b)
        db.load_seed(force=True)
        assert db.events(business_id="biz_bean") == []
        assert len(db.part_timers()) == 150                               # same seed data, separate copy
        llm.use_db(a)
        assert [e.text for e in db.events(business_id="biz_bean")] == ["only in a"]
        db.reset_demo()                                                   # reset clears only this copy
        assert db.events(business_id="biz_bean") == []
    finally:
        llm.use_db(None)


def ask_for_change(monkeypatch, o, day, start, end):
    """The worker writes; the owner has Muse Spark Open (faked here) read it and accepts the change."""
    from core import changes
    monkeypatch.setattr(changes.llm, "call_json", lambda *a, **k: changes.ChangeRequest(
        is_change_request=True, day=day, start=start, end=end))
    db.send_message(o, False, "Them", f"Could I do {start}-{end} instead?")
    owner = run("views/planner.py", business_id="biz_bean")
    owner.selectbox(key="msg_pick").set_value(o.id).run()
    click(owner, next(b.label for b in owner.button if b.label.startswith("✨ Read")))
    assert not owner.exception, owner.exception
    assert any("Would reopen" in m.label for m in owner.metric)
    click(owner, "Accept change")
    assert not owner.exception, owner.exception
    return run("views/planner.py", business_id="biz_bean")


def test_team_member_leaves_early_and_someone_else_covers_the_rest(monkeypatch):
    """The demo story: a team member asks to leave two hours early; the reopened end of the shift goes to
    someone else, never back to them. (Independent of which shifts the solver leaves open.)"""
    from core.taxonomy import from_q, to_q
    at = run("views/planner.py", business_id="biz_bean")
    for label in ["Simplify rules", "Build schedule", "Fill gaps with AI", "Send offers"]:
        click(at, label)
    o = next(x for x in db.offers(business_id="biz_bean") if x.kind == "extra_hours"
             and to_q(x.shifts[0].end) - to_q(x.shifts[0].start) >= 16)
    s0 = o.shifts[0]
    early = from_q(to_q(s0.end) - 8)                                         # leave two hours early
    owner = ask_for_change(monkeypatch, o, s0.day, s0.start, early)

    changed = db.offer(o.id)
    assert (changed.shifts[0].start, changed.shifts[0].end, changed.status) == (s0.start, early, "pending")
    assert db.messages(o.id)[-1].from_business                                            # a reply went out
    assert any(e.kind == "change_accepted" for e in db.events(business_id="biz_bean"))
    click(owner, "Find replacements with AI")
    assert not owner.exception, owner.exception
    if any(b.label == "Send offers" for b in owner.button):
        click(owner, "Send offers")
        round2 = [x for x in db.offers(business_id="biz_bean") if x.round == 2]
        assert [(s.day, s.start, s.end) for x in round2 for s in x.shifts] == [(s0.day, early, s0.end)]
        assert o.employee_id not in {x.employee_id for x in round2}   # not asked to cover what they gave up
    else:
        assert any("Nobody is free" in m.value for m in owner.markdown)


def test_nobody_free_for_the_reopened_hours(monkeypatch):
    """A part-timer shortens their shift; nobody else is free for the hours that reopen, and the planner says
    so plainly instead of offering a $0.00 plan. (Independent of which shifts the solver leaves open.)"""
    from core.taxonomy import from_q, to_q
    o = next(x for x in owner_sends_offers() if to_q(x.shifts[0].end) - to_q(x.shifts[0].start) >= 12)
    s0 = o.shifts[0]
    later = from_q(to_q(s0.start) + 4)                                        # start an hour later
    for p in db.part_timers():                                                # everyone else is busy
        if p.id != o.part_timer_id:
            db.save_part_timer(p.model_copy(update={"availability": {"Mon": [("23:00", "23:30")]}}))
    owner = ask_for_change(monkeypatch, o, s0.day, later, s0.end)
    click(owner, "Find replacements with AI")                                 # team first is off here
    assert not owner.exception, owner.exception
    assert any("Nobody is free" in m.value for m in owner.markdown)
    assert not any(b.label == "Send offers" for b in owner.button)
    click(owner, "OK, keep it open")
    assert db.offer(o.id).shifts[0].start == later


def test_not_a_change_request():
    from core import changes
    o = owner_sends_offers()[0]
    f = changes.check(o, changes.ChangeRequest(is_change_request=False), [])
    assert not f["ok"] and "doesn't ask" in f["reason"]


def test_refresh_keeps_the_same_demo_copy_and_account(tmp_path, monkeypatch):
    """The demo copy's id and the signed-in account ride in the address, so a refresh or a new tab (a new
    Streamlit session) comes back to the same data instead of a blank demo."""
    from streamlit.testing.v1 import AppTest
    from tests.test_ui import ROOT
    from ui import session
    monkeypatch.setenv("NEARHAND_PRIVATE_DEMO", "1")
    monkeypatch.setattr(session, "SESSIONS_DIR", tmp_path / "sessions")
    try:
        first = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
        first.session_state["view"] = "Part-timer"           # the "I'm looking for shifts" door
        first.run()
        assert not first.exception, first.exception
        demo, who = first.query_params["demo"], first.query_params["as"]
        llm.use_db(tmp_path / "sessions" / f"{demo}.db")       # write into that visitor's copy
        db.log_event("biz_bean", "note", "written before the refresh")
        llm.use_db(None)
        again = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)   # a fresh session
        again.query_params["demo"], again.query_params["as"] = demo, who
        again.run()
        assert not again.exception, again.exception
        assert again.session_state["account_id"] == who and again.query_params["demo"] == demo
        llm.use_db(tmp_path / "sessions" / f"{demo}.db")
        assert [e.text for e in db.events(business_id="biz_bean")] == ["written before the refresh"]
    finally:
        llm.use_db(None)


def test_view_as_opens_their_inbox_and_comes_back():
    sent = owner_sends_offers()
    at = run("views/planner.py", business_id="biz_bean", account_id="acct_owner_biz_bean")
    o = sent[0]
    name = db.part_timer(o.part_timer_id).name.split()[0]
    click(at, f"{name} →")
    assert not at.exception, at.exception
    assert at.session_state["account_id"] == f"acct_{o.part_timer_id}"
    assert at.session_state["view_back"] == "acct_owner_biz_bean"
    assert at.session_state["goto"] == "views/offers.py"


def test_sick_call_reopens_their_shifts_and_the_agent_fills_them(monkeypatch):
    from core import changes
    at = run("views/planner.py", business_id="biz_bean")
    for label in ["Simplify rules", "Build schedule"]:
        click(at, label)
    state = db.schedule("biz_bean")
    slots = {s["id"]: s for s in state["inputs"]["slots"]}
    chris = next(e for e in db.team("biz_bean") if e.name.startswith("Chris"))
    day = next(slots[sid]["day"] for wid, sid in state["staff_result"]["assignments"] if wid == chris.id)
    theirs = {sid for wid, sid in state["staff_result"]["assignments"] if wid == chris.id and slots[sid]["day"] == day}
    monkeypatch.setattr(changes.llm, "call_json", lambda *a, **k: changes.CallOut(employee=chris.name, days=[day]))

    next(t for t in at.text_input if t.label == "Someone called out?").input(f"Chris called out sick {day}")
    click(at, "Re-plan")
    assert not at.exception, at.exception
    state = db.schedule("biz_bean")
    assert theirs <= set(state["staff_result"]["gaps"])                       # their shifts are open now
    assert not any(w == chris.id and sid in theirs for w, sid in state["staff_result"]["assignments"])
    assert any(e.kind == "called_out" for e in db.events(business_id="biz_bean"))

    click(at, "Fill gaps with AI")
    assert not at.exception, at.exception
    click(at, "Send offers")
    covered = {s.slot_id for o in db.offers(business_id="biz_bean") for s in o.shifts}
    assert theirs & covered, "the agent offered at least one of the reopened shifts"
    assert not any(o.employee_id == chris.id for o in db.offers(business_id="biz_bean"))   # not back to Chris


def test_tour_follows_what_the_visitor_has_done():
    at = run("app.py", tour=True, view="Manager")
    assert not at.exception, at.exception
    assert any("Tour · 1 of 4" in m.value for m in at.markdown)
    owner_sends_offers()
    at = run("app.py", tour=True, view="Manager")
    assert any("Tour · 3 of 4" in m.value for m in at.markdown)
    click(at, "End tour")
    assert not any("Tour ·" in m.value for m in at.markdown)


def test_plain_call_outs_are_read_without_the_model(monkeypatch):
    from core import changes
    def boom(*a, **k):
        raise AssertionError("the model isn't needed for a plain message")
    monkeypatch.setattr(changes.llm, "call_json", boom)
    team = db.team("biz_bean")
    co = changes.read_call_out("Chris called out sick Thursday", team, "Tue")
    assert co.employee.startswith("Chris") and co.days == ["Thu"]
    co = changes.read_call_out("maya can't come in tomorrow", team, "Tue")
    assert co.employee.startswith("Maya") and co.days == ["Wed"]
    assert changes.quick_call_out("someone is sick", team, "Tue") is None


def test_the_restaurant_runs_the_whole_flow(monkeypatch):
    """Maple Table (a full-service restaurant) works end to end, not just the café."""
    from core import parsers
    from core.schemas import ConstraintSet
    from tests.test_marketplace import CAFE_RULES
    ids = [e.id for e in db.team("biz_maple")]
    rules = ConstraintSet.model_validate({**CAFE_RULES.model_dump(), "constraints": [
        c.model_copy(update={"employee_id": ids[0]}) if getattr(c, "employee_id", None) else c
        for c in CAFE_RULES.constraints]})
    monkeypatch.setattr(parsers, "parse_rules", lambda *a, **k: rules)
    at = run("views/planner.py", business_id="biz_maple")
    for label in ["Simplify rules", "Build schedule", "Fill gaps with AI", "Send offers"]:
        click(at, label); assert not at.exception, (label, at.exception)
    sent = db.offers(business_id="biz_maple")
    assert sent

def test_a_rule_about_someone_not_on_the_team_is_skipped():
    at = run("views/planner.py", business_id="biz_maple")   # the fake rules name Bean & Co.'s Maya
    for label in ["Simplify rules", "Build schedule"]:
        click(at, label); assert not at.exception, (label, at.exception)
    assert any("not on this team" in c.value for c in at.caption)
