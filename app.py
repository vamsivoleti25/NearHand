"""Nearhand: plain-English scheduling with a neighborhood part-timer marketplace.

    streamlit run app.py
"""

import os

import streamlit as st

from ui import session
from ui.common import boot

_HAS_LOGO = os.path.exists("assets/logo.png")
_HAS_FAVICON = os.path.exists("assets/favicon.png")

st.set_page_config(page_title="Nearhand", page_icon="assets/favicon.png" if _HAS_FAVICON else "🤝",
                   layout="wide")
boot()

if _HAS_LOGO:
    st.logo("assets/logo.png", size="large", icon_image="assets/favicon.png" if _HAS_FAVICON else "assets/logo.png")

# A home-page button sets the view it wants ("I need staff" -> Manager). If nobody matching is signed in,
# sign in the demo account for that side, so a visitor is one click from the product.
acct = session.current()
want = st.session_state.get("view")
if want in session.ROLE_OF and (acct is None or session.VIEW_OF[acct.role] != want):
    acct = session.demo_for(want)
    session.sign_in(acct)
elif acct is not None:
    session.sign_in(acct)   # keeps business / person ids in step with the account

home = st.Page("views/home.py", title="Home", icon=":material/home:", default=True)
signin = st.Page("views/signin.py", title="Log in", icon=":material/login:")

with st.sidebar:
    st.markdown("## Nearhand" if _HAS_LOGO else "## 🤝 Nearhand")
    st.caption("Your week, scheduled. Your gaps, filled from down the street.")
    if acct is None:
        st.button("Log in", type="primary", width="stretch", on_click=session.go_signin, args=("Log in",))
        st.button("Get started", width="stretch", on_click=session.go_signin, args=("Create an account",))
    else:
        with st.container(border=True, key="nh-who"):
            st.markdown(f"**{acct.name}**")
            st.caption(session.describe(acct))
            st.button("Sign out", width="stretch", on_click=session.sign_out)
    demo = session.demo_accounts()
    ids = [a.id for a in demo]
    if acct is not None and acct.id not in ids:
        ids.insert(0, acct.id)
        demo.insert(0, acct)
    labels = {a.id: session.label(a) for a in demo}
    st.session_state.demo_pick = acct.id if acct else None
    st.selectbox("Switch demo account", ids, key="demo_pick", placeholder="Choose someone to be",
                 format_func=labels.get, on_change=session.switch_to_picked,
                 help="Demo accounts have no password. Everyone here is fictional.")
    st.divider()
    if st.button("Reset demo data", help="Clears bookings, offers and saved schedules; restores the seed data."):
        from core import db
        db.reset_demo()
        st.rerun()
    st.caption("Built with Muse Spark Open")

if acct is None:
    pages = [home, signin]
elif acct.role == "owner":
    pages = [home,
             st.Page("views/planner.py", title="Planner", icon=":material/calendar_month:"),
             st.Page("views/marketplace.py", title="Marketplace", icon=":material/storefront:"),
             st.Page("views/team.py", title="Team", icon=":material/group:"),
             st.Page("views/setup.py", title="Business setup", icon=":material/store:"),
             st.Page("views/evals.py", title="Evals", icon=":material/monitoring:")]
elif acct.role == "staff":
    n = session.waiting_count(acct)
    pages = [home, st.Page("views/my_shifts.py", title=f"My shifts ({n})" if n else "My shifts",
                           icon=":material/inbox:")]
else:
    n = session.waiting_count(acct)
    pages = [home,
             st.Page("views/offers.py", title=f"Offers ({n})" if n else "Offers", icon=":material/inbox:"),
             st.Page("views/calendar.py", title="My calendar", icon=":material/event:"),
             st.Page("views/profile.py", title="My profile", icon=":material/badge:")]
pg = st.navigation(pages, position="top")
goto = st.session_state.pop("goto", None)   # set by sign-in, sign-out and switch buttons
if goto == "signin" and acct is None:
    st.switch_page(signin)
elif goto == "home":
    st.switch_page(home)
elif goto:
    st.switch_page(goto)   # a page path, e.g. views/setup.py after creating a business
pg.run()
