"""Log in / Get started. Two kinds of account, chosen first (like most staffing marketplaces), then a
short form. Demo-grade: no passwords; a part-timer's work history is read by Muse Spark Open."""

import os

import streamlit as st

from core import parsers
from core.schemas import PartTimerProfile, RoleExperience
from core.taxonomy import DAYS, ROLES, SKILLS, TEMPLATES, ZIP_COORDS
from ui import session
from ui.common import SKILL_LABELS, chips, footer, md, money, role, setup_page

setup_page()


def demo_shortlist():
    """Four accounts that show the whole story: both owners, plus whoever has an offer waiting."""
    demo = session.demo_accounts()
    owners = [a for a in demo if a.role == "owner"]
    staff = next((a for a in demo if a.role == "staff"), None)
    pt = next((a for a in demo if a.role == "part_timer"), None)
    return owners[:1] + [x for x in (staff, pt) if x] + owners[1:2]


if os.path.exists("assets/home/signin.png"):   # form left, picture right (stacked on a phone)
    form_col, pic_col = st.columns([1, 1], gap="large", vertical_alignment="center")
    with pic_col:
        st.image("assets/home/signin.png", width="stretch")
else:
    form_col = st.container()

with form_col.container(key="nh-auth"):
    mode = st.segmented_control("Account", ["Log in", "Create an account"], key="signin_mode",
                                label_visibility="collapsed") or "Log in"

    # ------------------------------------------------------------ log in
    if mode == "Log in":
        st.markdown('<div class="nh-auth-title">Log in to Nearhand</div>', unsafe_allow_html=True)
        with st.form("login", border=False):
            email = st.text_input("Email", placeholder="you@example.com")
            go = st.form_submit_button("Continue", type="primary", width="stretch")
        if go:
            from core import db
            a = db.account_by_email(email)
            if a is None:
                st.error("No account with that email. Create one, or pick a demo account below.")
            else:
                session.sign_in(a)
                st.session_state.goto = "home"
                st.rerun()
        with st.expander("Try a demo account", expanded=True):
            st.caption("No password needed. Everyone here is fictional.")
            for a in demo_shortlist():
                c1, c2 = st.columns([3, 1], vertical_alignment="center")
                c1.markdown(f"**{a.name}** · {session.describe(a)}")
                if c2.button("Use", key=f"use_{a.id}", width="stretch"):
                    session.sign_in(a)
                    st.session_state.goto = "home"
                    st.rerun()

    # ------------------------------------------------------------ create an account
    else:
        st.markdown('<div class="nh-auth-title">Join Nearhand</div>', unsafe_allow_html=True)
        side = st.session_state.get("signup_side")
        if side is None:
            st.markdown("Which describes you?")
            with st.container(key="nh-cards-signup"):
                a, b = st.columns(2)
            with a.container(border=True, key="nh-card-signup-biz"):
                st.markdown("**I run a business**")
                st.caption("Schedule your team, then fill open shifts with people from nearby.")
                if st.button("I need staff", key="side_biz", type="primary", width="stretch"):
                    st.session_state.signup_side = "business"
                    st.rerun()
            with b.container(border=True, key="nh-card-signup-pt"):
                st.markdown("**I'm looking for shifts**")
                st.caption("Pick up hourly shifts near you, at your rate, around your calendar.")
                if st.button("I want shifts", key="side_pt", type="primary", width="stretch"):
                    st.session_state.signup_side = "part_timer"
                    st.rerun()

        elif side == "business":
            st.caption("Step 2 of 2 · about you and your business")
            with st.form("signup_biz", border=False):
                owner = st.text_input("Your name")
                email = st.text_input("Work email")
                name = st.text_input("Business name")
                btype = st.selectbox("Type of business", list(TEMPLATES), format_func=lambda t: TEMPLATES[t]["label"])
                zip_ = st.selectbox("ZIP code", sorted(ZIP_COORDS))
                ok = st.form_submit_button("Create my account", type="primary", width="stretch")
            if ok:
                problem = (session.valid_email(email) or (None if owner.strip() else "Add your name.")
                           or (None if name.strip() else "Add your business name."))
                if problem:
                    st.error(problem)
                else:
                    session.sign_in(session.create_business_account(owner, email, name, btype, zip_))
                    st.session_state.pop("signup_side", None)
                    st.session_state.goto = "views/setup.py"   # next: hours, then the team
                    st.rerun()
            if st.button("Back", key="back_biz"):
                st.session_state.pop("signup_side", None)
                st.rerun()

        else:  # part-timer
            st.caption("Step 2 of 2 · about you and your work")
            name = st.text_input("Your name")
            email = st.text_input("Email")
            c1, c2 = st.columns(2)
            zip_ = c1.selectbox("Home ZIP code", sorted(ZIP_COORDS), help="Only your distance to a business is ever shown.")
            max_h = c2.number_input("Most hours a week", min_value=4, max_value=40, value=20, step=2)
            prof0 = st.session_state.get("signup_profile")
            days = st.pills("Days you're usually free", DAYS, selection_mode="multi",
                            default=[d for d in ("Sat", "Sun") if not (prof0 and d in prof0.days_off)],
                            key=f"signup_days_{','.join(prof0.days_off) if prof0 else ''}")
            c1, c2 = st.columns(2)
            times = [f"{h:02d}:00" for h in range(5, 24)]
            start = c1.selectbox("From", times, index=times.index("07:00"))
            end = c2.selectbox("Until", times, index=times.index("15:00"))
            bio = st.text_area("Your work experience, in your own words", height=120,
                               placeholder="e.g. Two years as a barista at a busy café, some cashier work, "
                                           "food handler card, looking for about $18/hour.")
            if st.button("Read my experience", help="Muse Spark Open turns it into roles, years and skills"):
                with st.spinner("Muse Spark Open is reading your experience…"):
                    try:
                        st.session_state.signup_profile = parsers.parse_experience(bio)
                    except Exception as e:
                        st.error(f"Couldn't read it ({e}). Pick your roles below instead.")
                    else:
                        st.rerun()   # so "no Sundays" also updates the days above
            prof: PartTimerProfile | None = st.session_state.get("signup_profile")
            if prof:
                chips([f"{role(r.role)} {r.years:g}y" for r in prof.roles]
                      + ([f"{money(prof.expected_rate_cents)}/h"] if prof.expected_rate_cents else []))
                if prof.skills:
                    chips([SKILL_LABELS.get(s, s) for s in prof.skills], "warn")
                if prof.days_off:
                    chips([f"Not on {d}" for d in prof.days_off], "warn")
            roles = st.multiselect("Roles", ROLES, default=[r.role for r in prof.roles] if prof else [],
                                   format_func=role)
            skills = st.multiselect("Certifications", SKILLS, default=list(prof.skills) if prof else [],
                                    format_func=lambda s: SKILL_LABELS.get(s, s))
            rate = st.number_input("Your hourly rate ($)", min_value=16.94, max_value=60.0, step=0.25,
                                   value=max(16.94, (prof.expected_rate_cents or 1800) / 100 if prof else 18.0),
                                   help="Connecticut's minimum wage is $16.94 in 2026")
            if st.button("Create my account", type="primary", width="stretch"):
                years = {r.role: r.years for r in prof.roles} if prof else {}
                problem = (session.valid_email(email) or (None if name.strip() else "Add your name.")
                           or (None if roles else "Pick at least one role.")
                           or (None if days else "Pick the days you're usually free.")
                           or (None if start < end else "'Until' has to be after 'From'."))
                if problem:
                    st.error(problem)
                else:
                    profile = PartTimerProfile(
                        name=name, expected_rate_cents=int(round(rate * 100)), skills=skills,
                        roles=[RoleExperience(role=r, years=years.get(r, 1.0)) for r in roles],
                        business_types=list(prof.business_types) if prof else [],
                        summary=prof.summary if prof else "")
                    a = session.create_part_timer_account(name, email, zip_, profile, bio,
                                                          {d: [(start, end)] for d in days}, float(max_h))
                    session.sign_in(a)
                    for k in ("signup_side", "signup_profile"):
                        st.session_state.pop(k, None)
                    st.session_state.goto = "views/profile.py"
                    st.rerun()
            if st.button("Back", key="back_pt"):
                for k in ("signup_side", "signup_profile"):
                    st.session_state.pop(k, None)
                st.rerun()
        st.caption(md("By creating an account you agree that this is a hackathon demo: no real shifts, "
                      "no real pay."))
footer()
