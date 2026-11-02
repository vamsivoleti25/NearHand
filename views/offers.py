"""A part-timer's offers inbox: see the shifts, ask 'should I take this?', accept or decline."""

import streamlit as st

from core import db, offers
from ui.chat import badge, feed, thread
from ui.common import STATUS_BADGE, footer, md, money, role, setup_page

REC_CLASS = {"take it": "take", "worth a look": "look", "pass": "pass"}

setup_page()
p = db.part_timer(st.session_state.get("part_timer_id", "pt_001"))
mine = db.offers(part_timer_id=p.id)
pending = [o for o in mine if o.status == "pending"]
businesses = {b.id: b for b in db.businesses()}
new_msgs = db.unread(False, worker_id=p.id)   # unread messages from businesses, per offer

st.title(f"Offers for {p.name.split()[0]}")
if not pending:
    st.info("No offers right now. Businesses nearby see your profile when they look for help, "
            "and offers show up here.")

for o in pending:
    biz = businesses[o.business_id]
    facts = offers.offer_facts(o, p, biz, db.bookings(), mine)
    with st.container(border=True, key=f"nh-offer-{o.id}"):
        st.markdown(md(f"### {biz.name}\n{facts['distance_miles']} mi away"))
        st.markdown(md(f"## You'd earn {facts['total_pay']}"))
        st.caption(md(f"for {facts['total_hours']:g} hours at {money(o.rate_cents)}/h"))
        for s in o.shifts:
            st.markdown(md(f"- **{s.day}** {s.start}–{s.end} · {role(s.role)}"))
        if o.message:
            st.caption(md(f"“{o.message}”"))

        key = f"advice_{o.id}"
        if st.button("Should I take this?", key=f"ask_{o.id}"):
            with st.spinner("Muse Spark Open is weighing it up…"):
                st.session_state[key] = offers.advise(facts)
        adv = st.session_state.get(key)
        if adv:
            st.markdown(f'<span class="nh-rec {REC_CLASS[adv.recommendation]}">{adv.recommendation.capitalize()}</span>',
                        unsafe_allow_html=True)
            for r in adv.reasons:
                st.markdown(md(f"- {r}"))
            st.caption("Based on your calendar, asking rate, weekly max and distance. Numbers checked against them.")

        if st.button("Accept", key=f"yes_{o.id}", type="primary", width="stretch"):
            done, made = offers.respond(o, True, p, biz, db.bookings())
            for bk in made:
                db.add_booking(bk)
            db.save_offer(done)
            if done.status == "accepted":
                st.toast(f"Booked with {biz.name}. It's on your calendar.", icon="✅")
            else:
                st.toast("Those hours aren't free anymore, so the offer was closed.", icon="⚠️")
            st.rerun()
        if st.button("Decline", key=f"no_{o.id}", width="stretch"):
            db.save_offer(offers.respond(o, False, p, biz, db.bookings())[0])
            st.toast(f"Declined. {biz.name} will find someone else.", icon="👋")
            st.rerun()
        with st.expander(f"Message {biz.name}{badge(new_msgs.get(o.id, 0))}", expanded=bool(new_msgs.get(o.id))):
            thread(o, as_business=False, me=p.name, other=biz.name, key=o.id)

booked = [o for o in mine if o.status == "accepted"]
if booked:
    st.subheader("Booked")
    for o in booked:
        biz = businesses[o.business_id]
        with st.container(border=True, key=f"nh-booked-{o.id}"):
            st.markdown(md(f"**{biz.name}** · {db.shifts_text(o)} · {money(o.rate_cents)}/h"))
            with st.expander(f"Messages{badge(new_msgs.get(o.id, 0))}", expanded=bool(new_msgs.get(o.id))):
                thread(o, as_business=False, me=p.name, other=biz.name, key=o.id)

with st.expander("Your activity"):
    feed(db.events(worker_id=p.id, limit=15), "Offers, answers and messages show up here.")

past = [o for o in mine if o.status not in ("pending", "accepted")]
if past:
    st.subheader("Earlier offers")
    st.dataframe([{"Business": businesses[o.business_id].name,
                   "Shifts": ", ".join(f"{s.day} {s.start}-{s.end}" for s in o.shifts),
                   "Rate": money(o.rate_cents) + "/h", "Status": STATUS_BADGE[o.status]} for o in past],
                 hide_index=True, width="stretch")
footer()
