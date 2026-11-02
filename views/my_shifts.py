"""A team member's page: extra-hours offers from their own workplace (accept or say no), and their week."""

import streamlit as st

from core import db, offers
from core.taxonomy import DAYS, from_q, to_q
from ui.chat import badge, feed, thread
from ui.common import STATUS_BADGE, footer, load_state, md, role, setup_page

setup_page()
bid = st.session_state.get("business_id", "biz_bean")
eid = st.session_state.get("employee_id", "emp_ana")
biz = db.business(bid)
team = {e.id: e for e in db.team(bid)}
me = team.get(eid)
if me is None:
    st.info("This account isn't on a team any more.")
    st.stop()
state = load_state(bid)
mine = db.offers(business_id=bid, employee_id=eid)
pending = [o for o in mine if o.status == "pending"]
new_msgs = db.unread(False, business_id=bid, worker_id=eid)

st.title(f"Hi {me.name.split()[0]}")
st.markdown(f'<div class="nh-subtitle">{biz.name} · team member</div>', unsafe_allow_html=True)

slots = (state.get("inputs") or {}).get("slots", [])
by_slot = {s["id"]: s for s in slots}
sr = state.get("staff_result")
hours_now = offers.scheduled_hours(eid, sr, slots) if sr else 0.0
accepted_extra = sum(offers.shift_hours(o) for o in mine if o.status == "accepted")

if not pending:
    st.info("No extra hours on offer right now. When there are open shifts you could cover, "
            f"{biz.name} can ask you here first, before anyone from the neighborhood.")

for o in pending:
    f = offers.extra_hours_facts(o, me, biz, hours_now, accepted_extra)
    with st.container(border=True, key=f"nh-offer-{o.id}"):
        st.markdown(f"### {biz.name} is asking you first")
        st.markdown(md(f"## Extra hours: +{f['extra_hours']:g} h for {f['extra_pay']}"))
        for s in o.shifts:
            st.markdown(md(f"- **{s.day}** {s.start}–{s.end} · {role(s.role)}"))
        line = (f"Takes your week from {f['hours_this_week_now']:g} to {f['hours_after_accepting']:g} hours "
                f"(your usual most is {f['your_usual_max_hours']:g}).")
        st.markdown(md(line))
        if f["overtime_hours"]:
            ot_rate = f["overtime_rate"].replace("$", "&#36;")   # raw HTML: no markdown escapes in here
            st.markdown(f'<span class="nh-chip warn">{f["overtime_hours"]:g} h at overtime, {ot_rate}/h</span>',
                        unsafe_allow_html=True)
        if o.message:
            st.caption(md(f"“{o.message}”"))
        st.caption("Saying no is fine: the shift goes to part-timers nearby instead.")
        if st.button("Accept", key=f"yes_{o.id}", type="primary", width="stretch"):
            db.save_offer(offers.respond(o, True, None, biz, [])[0])
            st.toast("Thanks. It's on your schedule.", icon="✅")
            st.rerun()
        if st.button("Decline", key=f"no_{o.id}", width="stretch"):
            db.save_offer(offers.respond(o, False, None, biz, [])[0])
            st.toast(f"No problem. {biz.name} will find someone nearby.", icon="👋")
            st.rerun()
        with st.expander(f"Message {biz.name}{badge(new_msgs.get(o.id, 0))}", expanded=bool(new_msgs.get(o.id))):
            thread(o, as_business=False, me=me.name, other=biz.name, key=o.id)

# ---------------------------------------------------------------- my week
st.subheader("My week")
rows = []
if sr:
    for wid, sid in sr["assignments"]:
        if wid == eid and sid in by_slot:
            s = by_slot[sid]
            rows.append((DAYS.index(s["day"]), s["start_q"], {"Day": s["day"], "Time": f"{from_q(s['start_q'])}-{from_q(s['end_q'])}",
                                                               "Role": role(s["role"]), "": "usual"}))
for o in mine:
    if o.status == "accepted":
        for s in o.shifts:
            rows.append((DAYS.index(s.day), to_q(s.start), {"Day": s.day, "Time": f"{s.start}-{s.end}",
                                                      "Role": role(s.role), "": "extra ✓"}))
if rows:
    st.dataframe([r for *_, r in sorted(rows, key=lambda x: (x[0], x[1]))], hide_index=True, width="stretch")
    st.caption(md(f"{hours_now + accepted_extra:g} hours this week."))
else:
    st.caption("Your schedule shows up here once the week is built.")

past = [o for o in mine if o.status != "pending"]
if past:
    st.subheader("Earlier asks")
    st.dataframe([{"Shifts": ", ".join(f"{s.day} {s.start}-{s.end}" for s in o.shifts),
                   "Status": STATUS_BADGE[o.status]} for o in past], hide_index=True, width="stretch")
    for o in past:
        if o.status == "accepted" or db.messages(o.id):
            with st.expander(f"Messages · {db.shifts_text(o)}{badge(new_msgs.get(o.id, 0))}",
                             expanded=bool(new_msgs.get(o.id))):
                thread(o, as_business=False, me=me.name, other=biz.name, key=o.id)

with st.expander("Your activity"):
    feed(db.events(business_id=bid, worker_id=eid, limit=15), "Asks, answers and messages show up here.")
footer()
