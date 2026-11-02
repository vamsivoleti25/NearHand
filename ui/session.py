"""Who is signed in. Demo-grade: accounts have no passwords and every person in the app is fictional.

The rest of the app reads the same session keys as before (view, business_id, part_timer_id, employee_id),
so pages work the same whether you arrive by signing in, by a home-page button, or in a test.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path

import streamlit as st

from core import db, llm
from core.schemas import Account, Business, PartTimer, PartTimerProfile
from core.taxonomy import TEMPLATES, ZIP_COORDS

VIEW_OF = {"owner": "Manager", "staff": "Staff", "part_timer": "Part-timer"}
ROLE_OF = {v: k for k, v in VIEW_OF.items()}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


SESSIONS_DIR = llm.ROOT / "data" / "sessions"


def use_private_db() -> None:
    """Give this browser session its own copy of the demo data, so visitors never see each other's offers.
    Off with NEARHAND_PRIVATE_DEMO=0 (then everyone shares data/nearhand.db, as before)."""
    if os.getenv("NEARHAND_PRIVATE_DEMO", "1") == "0":
        return
    path = st.session_state.get("db_file")
    if not path:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - 24 * 3600          # tidy up demos nobody has touched for a day
        for f in SESSIONS_DIR.glob("*.db"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
        path = str(SESSIONS_DIR / f"{uuid.uuid4().hex[:12]}.db")
        st.session_state.db_file = path
    llm.use_db(path)
    if not Path(path).exists():
        db.load_seed(force=True)


def current() -> Account | None:
    aid = st.session_state.get("account_id")
    if not aid:
        return None
    try:
        return db.account(aid)
    except KeyError:
        st.session_state.pop("account_id", None)
        return None


def sign_in(a: Account) -> None:
    st.session_state.account_id = a.id
    st.session_state.view = VIEW_OF[a.role]
    if a.business_id:
        st.session_state.business_id = a.business_id
    if a.employee_id:
        st.session_state.employee_id = a.employee_id
    if a.part_timer_id:
        st.session_state.part_timer_id = a.part_timer_id


def sign_out() -> None:
    for k in ("account_id", "view", "employee_id", "part_timer_id"):
        st.session_state.pop(k, None)
    st.session_state.goto = "home"


def go_signin(mode: str) -> None:
    st.session_state.signin_mode = mode
    st.session_state.goto = "signin"


def switch_to_picked() -> None:
    """Sidebar 'Switch demo account' (a callback, so it runs before the page list is built)."""
    aid = st.session_state.get("demo_pick")
    if aid:
        use_private_db()   # callbacks run before app.py, so point at this visitor's data first
        try:
            a = db.account(aid)
        except KeyError:   # the widget handed back its label ("Name · Role · …") instead of the id
            name = str(aid).split(" · ")[0]
            a = next((x for x in demo_accounts() if x.name == name), None)
            if a is None:
                return
        if a.id == st.session_state.get("account_id"):
            return   # not a real switch: the label changed under the widget (a count went up or down)
        sign_in(a)
        st.session_state.goto = INBOX[a.role]   # straight to where this person acts: planner or inbox


INBOX = {"owner": "views/planner.py", "staff": "views/my_shifts.py", "part_timer": "views/offers.py"}


def demo_accounts() -> list[Account]:
    """A short, useful list for the sign-in page and the sidebar switcher: the owners, then team members
    and part-timers with offers waiting, so a demo never has to hunt for who to sign in as."""
    owners = db.accounts(role="owner")
    pending = db.offers(status="pending")
    staff_waiting = {(o.business_id, o.employee_id) for o in pending if o.kind == "extra_hours"}
    pt_waiting = {o.part_timer_id for o in pending if o.kind == "shift"}
    staff = db.accounts(role="staff")
    staff = sorted(staff, key=lambda a: ((a.business_id, a.employee_id) not in staff_waiting, a.business_id, a.name))
    pts = db.accounts(role="part_timer")
    pts = sorted(pts, key=lambda a: (a.part_timer_id not in pt_waiting, a.part_timer_id))
    return owners + staff[:4] + pts[:4]


def waiting_count(a: Account) -> int:
    """Offers waiting for an answer plus unread messages: the number on the header's account button."""
    if a.role == "staff":
        return (len(db.offers(business_id=a.business_id, employee_id=a.employee_id, status="pending"))
                + sum(db.unread(False, business_id=a.business_id, worker_id=a.employee_id).values()))
    if a.role == "part_timer":
        return (len(db.offers(part_timer_id=a.part_timer_id, status="pending"))
                + sum(db.unread(False, worker_id=a.part_timer_id).values()))
    return sum(db.unread(True, business_id=a.business_id).values())


def describe(a: Account) -> str:
    if a.role == "owner":
        return f"Owner · {db.business(a.business_id).name}"
    if a.role == "staff":
        return f"Team member · {db.business(a.business_id).name}"
    return "Part-timer"


def label(a: Account) -> str:
    n = waiting_count(a)
    return f"{a.name} · {describe(a)}" + (f" · {n} new" if n else "")


def demo_for(view: str) -> Account:
    """The account a home-page button signs you into: the café owner, or the part-timer (or team member)
    with the most offers waiting."""
    role = ROLE_OF[view]
    if role == "owner":
        bid = st.session_state.get("business_id", "biz_bean")
        return next((a for a in db.accounts(role="owner") if a.business_id == bid), db.accounts(role="owner")[0])
    return next(a for a in demo_accounts() if a.role == role)


# ---------------------------------------------------------------- sign-up

def valid_email(email: str) -> str | None:
    email = email.strip()
    if not EMAIL.match(email):
        return "That doesn't look like an email address."
    if db.account_by_email(email):
        return "There's already an account with that email. Log in instead."
    return None


def create_business_account(owner: str, email: str, business_name: str, btype: str, zip_: str) -> Account:
    t = TEMPLATES[btype]
    lat, lon = ZIP_COORDS.get(zip_, (None, None))
    b = Business(id=f"biz_{uuid.uuid4().hex[:6]}", name=business_name.strip(), type=btype, zip=zip_,
                 hours={d: tuple(h) for d, h in t["default_hours"].items()}, lat=lat, lon=lon)
    db.save_business(b)
    a = Account(id=f"acct_owner_{b.id}", role="owner", name=owner.strip(), email=email.strip().lower(),
                business_id=b.id)
    db.save_account(a)
    return a


def create_part_timer_account(name: str, email: str, zip_: str, profile: PartTimerProfile, bio: str,
                              availability: dict, max_hours: float) -> Account:
    lat, lon = ZIP_COORDS.get(zip_, (None, None))
    p = PartTimer(**profile.model_dump(exclude={"name", "zip"}), name=name.strip(), zip=zip_,
                  id=f"pt_{uuid.uuid4().hex[:6]}", availability=availability, max_hours=max_hours,
                  lat=lat, lon=lon, bio=bio)
    db.save_part_timer(p)
    a = Account(id=f"acct_{p.id}", role="part_timer", name=p.name, email=email.strip().lower(), part_timer_id=p.id)
    db.save_account(a)
    return a
