"""Nearhand: plain-English scheduling with a neighborhood part-timer marketplace.

    streamlit run app.py
"""

import os

import streamlit as st

from core import db
from ui.common import boot, business_label

_HAS_LOGO = os.path.exists("assets/logo.png")
_HAS_FAVICON = os.path.exists("assets/favicon.png")

st.set_page_config(page_title="Nearhand", page_icon="assets/favicon.png" if _HAS_FAVICON else "🤝",
                   layout="wide")
boot()

if _HAS_LOGO:
    st.logo("assets/logo.png", icon_image="assets/favicon.png" if _HAS_FAVICON else "assets/logo.png")

with st.sidebar:
    st.markdown("## Nearhand" if _HAS_LOGO else "## 🤝 Nearhand")
    st.caption("Your week, scheduled. Your gaps, filled from down the street.")
    view = st.segmented_control("View as", ["Manager", "Part-timer"], default="Manager", key="view") or "Manager"
    if view == "Manager":
        businesses = db.businesses()
        ids = [b.id for b in businesses]
        st.session_state.setdefault("business_id", ids[0])
        st.selectbox("Business", ids, key="business_id",
                     format_func=lambda i: business_label(next(b for b in businesses if b.id == i)))
    else:
        pts = db.part_timers()
        waiting = {}
        for o in db.offers(status="pending"):
            waiting[o.part_timer_id] = waiting.get(o.part_timer_id, 0) + 1
        ids = sorted((p.id for p in pts), key=lambda i: (-waiting.get(i, 0), i))  # people with offers first
        names = {p.id: p.name for p in pts}
        st.session_state.setdefault("part_timer_id", ids[0])
        st.selectbox("Part-timer", ids, key="part_timer_id",
                     format_func=lambda i: names[i] + (f"  · {waiting[i]} offer{'s' if waiting[i] > 1 else ''}"
                                                       if waiting.get(i) else ""))
    st.divider()
    if st.button("Reset demo data", help="Clears bookings, offers and saved schedules; restores the seed data."):
        db.reset_demo()
        st.rerun()
    st.caption("Built with Muse Spark Open")

if view == "Manager":
    pages = [
        st.Page("views/planner.py", title="Planner", icon=":material/calendar_month:", default=True),
        st.Page("views/marketplace.py", title="Marketplace", icon=":material/storefront:"),
        st.Page("views/team.py", title="Team", icon=":material/group:"),
        st.Page("views/setup.py", title="Business setup", icon=":material/store:"),
        st.Page("views/evals.py", title="Evals", icon=":material/monitoring:"),
    ]
else:
    n = len(db.offers(part_timer_id=st.session_state.get("part_timer_id"), status="pending"))
    pages = [
        st.Page("views/offers.py", title=f"Offers ({n})" if n else "Offers", icon=":material/inbox:", default=True),
        st.Page("views/calendar.py", title="My calendar", icon=":material/event:"),
        st.Page("views/profile.py", title="My profile", icon=":material/badge:"),
    ]
st.navigation(pages, position="top").run()
