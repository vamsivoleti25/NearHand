"""Nearhand: plain-English scheduling with a neighborhood part-timer marketplace.

    streamlit run app.py
"""

import os

import streamlit as st

from ui import session
from ui.common import boot

# On Streamlit Community Cloud the API keys live in the app's Secrets, not in a .env file.
# core/llm.py reads keys from the environment, so copy any top-level secrets there first.
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str):
            os.environ.setdefault(_k, _v)
except Exception:  # no secrets file when running locally: .env is used instead
    pass

session.use_private_db()   # each visitor gets their own copy of the demo data

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
signin = st.Page("views/signin.py", title="Log in", icon=":material/login:", visibility="hidden")  # header buttons

# ---------------------------------------------------------------- header, right corner
# The page links and logo live in Streamlit's top bar; the account controls float in its right corner.
with st.container(key="nh-topbar", horizontal=True, horizontal_alignment="right", gap="small", width="content"):
    if acct is None:
        st.button("Log in", key="top_login", on_click=session.go_signin, args=("Log in",))
        st.button("Get started", key="top_start", type="primary", on_click=session.go_signin,
                  args=("Create an account",))
    else:
        n = session.waiting_count(acct)
        with st.popover(acct.name.split()[0] + (f" · {n}" if n else ""), icon=":material/account_circle:"):
            st.markdown(f"**{acct.name}**")
            st.caption(f"{session.describe(acct)} · {acct.email}")
            demo = session.demo_accounts()
            ids = [a.id for a in demo]
            if acct.id not in ids:
                ids.insert(0, acct.id)
                demo.insert(0, acct)
            labels = {a.id: session.label(a) for a in demo}
            st.session_state.demo_pick = acct.id
            st.selectbox("Switch demo account", ids, key="demo_pick", format_func=labels.get,
                         on_change=session.switch_to_picked,
                         help="Demo accounts have no password. Everyone here is fictional.")
            st.button("Sign out", key="top_signout", width="stretch", on_click=session.sign_out)
            if st.button("Reset demo data", key="top_reset", width="stretch",
                         help="Clears bookings, offers and saved schedules; restores the seed data."):
                from core import db
                db.reset_demo()
                st.rerun()

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
session.remember_account()

from ui import tour
tour.banner(acct if acct is None else session.current())

# "View as" from the planner: the person's inbox in a phone-sized frame, with a way straight back.
back_to = st.session_state.get("view_back")
if back_to and acct is not None and acct.role != "owner":
    from core import db as _db
    owner = _db.account(back_to)
    st.markdown("""<style>
      .block-container {max-width: 440px !important; border: 12px solid #111827; border-radius: 40px;
        padding: 1.6rem 1.1rem 1.4rem 1.1rem !important; margin-top: 4.6rem; box-shadow: 0 18px 50px rgba(17,24,39,.25);}
      .block-container h1 {font-size: 1.7rem !important;}
    </style>""", unsafe_allow_html=True)
    with st.container(key="nh-viewas", horizontal=True, vertical_alignment="center"):
        st.caption(f"📱 {acct.name.split()[0]}'s phone")
        st.button(f"← Back to {owner.name.split()[0]}'s planner", key="nh_back", on_click=session.go_back,
                  type="primary")
goto = st.session_state.pop("goto", None)   # set by sign-in, sign-out and switch buttons
if goto == "signin" and acct is None:
    st.switch_page(signin)
elif goto == "home":
    st.switch_page(home)
elif goto:
    st.switch_page(goto)   # a page path, e.g. views/setup.py after creating a business
pg.run()
