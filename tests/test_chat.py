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
    for label in ["Read my rules", "Build schedule", "Fill gaps with AI", "Send offers"]:
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
    assert any("Read my rules" in m.value for m in at.markdown)           # the hint says what to do first
    click(at, "Read my rules")
    assert any("Build schedule" in m.value for m in at.markdown)
    assert any("Read the rules" in m.value for m in at.markdown)          # and the Activity tab shows it


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


def test_change_request_in_chat_splits_the_shift_and_reopens_the_rest(monkeypatch):
    from core import changes
    from core.taxonomy import to_q
    sent = owner_sends_offers()
    o = next(x for x in sent if to_q(x.shifts[0].end) - to_q(x.shifts[0].start) >= 16)   # a 4 h+ shift
    s0 = o.shifts[0]
    new_start = f"{int(s0.start[:2]) + 2:02d}:{s0.start[3:]}"                              # two hours later
    monkeypatch.setattr(changes.llm, "call_json", lambda *a, **k: changes.ChangeRequest(
        is_change_request=True, day=s0.day, start=new_start, end=s0.end))
    db.send_message(o, False, "Pat", f"Could I start at {new_start} instead?")

    owner = run("views/planner.py", business_id="biz_bean")
    owner.selectbox(key="msg_pick").set_value(o.id).run()
    click(owner, next(b.label for b in owner.button if b.label.startswith("✨ Read")))
    assert not owner.exception, owner.exception
    assert any("Would reopen" in m.label for m in owner.metric)
    click(owner, "Accept change")
    assert not owner.exception, owner.exception

    changed = db.offer(o.id)
    assert changed.shifts[0].start == new_start and changed.status == "pending"
    assert db.messages(o.id)[-1].from_business                                            # a reply went out
    assert any(e.kind == "change_accepted" for e in db.events(business_id="biz_bean"))
    owner = run("views/planner.py", business_id="biz_bean")
    assert any("Find replacements with AI" == b.label for b in owner.button)               # the 2 h reopened
    click(owner, "Find replacements with AI")
    assert not owner.exception, owner.exception
    click(owner, "Send offers")
    round2 = [x for x in db.offers(business_id="biz_bean") if x.round == 2]
    assert round2 and any(sh.end == new_start for x in round2 for sh in x.shifts)


def test_not_a_change_request():
    from core import changes
    o = owner_sends_offers()[0]
    f = changes.check(o, changes.ChangeRequest(is_change_request=False), [])
    assert not f["ok"] and "doesn't ask" in f["reason"]
