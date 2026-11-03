"""Nearhand home page: the front door for managers and part-timers."""

import json
import os
from pathlib import Path

import streamlit as st

from ui import tour

from core import db
from core.taxonomy import TEMPLATES
from ui.common import footer, setup_page

setup_page()

RESULTS = Path(__file__).resolve().parent.parent / "evals" / "results"


def go(view):
    st.session_state.view = view


def _results(name: str):
    p = RESULTS / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- hero
with st.container(key="nh-home-hero"):
    left, right = st.columns([1.1, 1], gap="large", vertical_alignment="center")
    with left:
        st.markdown('<div class="nh-home-eyebrow">Scheduling + a neighborhood part-timer network</div>',
                    unsafe_allow_html=True)
        st.markdown('<div class="nh-home-h1">Your week, scheduled. Your gaps, filled from down '
                    'the street.</div>', unsafe_allow_html=True)
        st.markdown('<div class="nh-home-sub">Small businesses write their scheduling rules in plain '
                    'English; Nearhand builds the cheapest week that follows them, then an AI agent fills '
                    'the open shifts with qualified part-timers who live nearby, and they accept on '
                    'their phone.</div>', unsafe_allow_html=True)
        hero_a, hero_b = st.columns(2)
        with hero_a:
            if st.button("I need staff", type="primary", key="hero_staff", on_click=go, args=("Manager",),
                         width="stretch"):
                st.switch_page("views/planner.py")
        with hero_b:
            if st.button("I'm looking for shifts", key="hero_shifts", on_click=go, args=("Part-timer",),
                         width="stretch"):
                st.switch_page("views/offers.py")
        if st.button("▶ Take the 2-minute tour", key="hero_tour", on_click=tour.start, type="tertiary",
                     help="Signs you in as Dana, a café owner, and shows what to click at each step."):
            st.switch_page("views/planner.py")
    with right:
        if os.path.exists("assets/home/hero.png"):
            st.image("assets/home/hero.png")

# ---------------------------------------------------------------- numbers strip
with st.container(key="nh-home-numbers"):
    st.markdown('<div class="nh-home-section">', unsafe_allow_html=True)
    figures: list[tuple[str, str]] = []
    try:
        figures.append((str(len(db.part_timers())), "part-timers nearby"))
    except Exception:  # noqa: BLE001
        pass
    try:
        figures.append((str(len(TEMPLATES)), "business types"))
    except Exception:  # noqa: BLE001
        pass
    agent = _results("agent_baseten.json")
    if agent and isinstance(agent.get("summary"), dict):
        s = agent["summary"]
        try:
            scenarios = int(s["scenarios"])
            matched = int(round(float(s["matches_optimizer"]) * scenarios))
            figures.append((f"{matched} of {scenarios}",
                            "test weeks matched the optimizer"))
        except (KeyError, TypeError, ValueError):
            pass
        try:
            cost = float(agent["cost_usd"]) / int(s["scenarios"])
            figures.append((f"${cost:.2f}", "AI cost per plan"))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            pass
    rules = _results("rules_baseten.json")
    if rules and isinstance(rules.get("summary"), dict):
        try:
            figures.append((f"{float(rules['summary']['pass_rate']):.0%}", "rule sets read right"))
        except (KeyError, TypeError, ValueError):
            pass
    if figures:
        cols = st.columns(len(figures))
        for col, (value, label) in zip(cols, figures):
            col.metric(label, value)
        st.markdown('<div class="nh-home-caption">Demo data: all people and businesses are '
                    'fictional.</div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------- two doors
with st.container(key="nh-home-doors"):
    st.markdown('<div class="nh-home-section">', unsafe_allow_html=True)
    with st.container(key="nh-cards-home"):
        door_a, door_b = st.columns(2, gap="large")
    with door_a:
        with st.container(border=True, key="nh-card-home-staff"):
            if os.path.exists("assets/home/staff.png"):
                st.markdown('<div class="nh-home-door-img">', unsafe_allow_html=True)
                st.image("assets/home/staff.png")
                st.markdown("</div>", unsafe_allow_html=True)
            st.subheader("For businesses")
            st.markdown("- Write rules the way you say them\n"
                       "- Get the cheapest week that follows them\n"
                       "- Fill open shifts nearby, with the cost shown before anything is sent")
            if st.button("Open the planner", type="primary", key="door_planner", on_click=go,
                         args=("Manager",), width="stretch"):
                st.switch_page("views/planner.py")
    with door_b:
        with st.container(border=True, key="nh-card-home-shifts"):
            if os.path.exists("assets/home/shifts.png"):
                st.markdown('<div class="nh-home-door-img">', unsafe_allow_html=True)
                st.image("assets/home/shifts.png")
                st.markdown("</div>", unsafe_allow_html=True)
            st.subheader("For part-timers")
            st.markdown("- Shifts down the street, at your rate, around your calendar\n"
                       "- One tap to accept, and an honest answer on whether it's worth it\n"
                       "- Nothing is booked without your yes")
            if st.button("See my offers", type="primary", key="door_offers", on_click=go, args=("Part-timer",),
                         width="stretch"):
                st.switch_page("views/offers.py")
    st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------- how it works
with st.container(key="nh-home-steps"):
    st.markdown('<div class="nh-home-section">', unsafe_allow_html=True)
    st.subheader("How it works")
    s1, s2, s3 = st.columns(3, gap="large")
    with s1:
        st.markdown('<div class="nh-home-stepnum">1</div>', unsafe_allow_html=True)
        st.markdown('<div class="nh-home-steptitle">Say your rules</div>', unsafe_allow_html=True)
        st.markdown("Write the week the way you would say it, and Nearhand reads it into scheduling rules.")
    with s2:
        st.markdown('<div class="nh-home-stepnum">2</div>', unsafe_allow_html=True)
        st.markdown('<div class="nh-home-steptitle">Get the cheapest week</div>', unsafe_allow_html=True)
        st.markdown("The optimizer builds the lowest-cost schedule that follows every rule.")
    with s3:
        st.markdown('<div class="nh-home-stepnum">3</div>', unsafe_allow_html=True)
        st.markdown('<div class="nh-home-steptitle">Fill the gaps nearby</div>', unsafe_allow_html=True)
        st.markdown("An agent fills the open shifts with qualified part-timers from the neighborhood.")
    st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------- under the hood
with st.container(key="nh-home-under"):
    st.markdown('<div class="nh-home-section">', unsafe_allow_html=True)
    st.subheader("Under the hood")
    st.markdown('<div class="nh-home-under">Muse Spark Open reads the rules, runs the gap-filling agent '
                "and gives offer advice. OR-Tools CP-SAT does every schedule and every cost figure, and "
                "every number the model writes is checked against the optimizer before it is shown. "
                "Full scores and costs are on the Evals page.</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------- closing call to action
with st.container(key="nh-home-close"):
    st.markdown('<div class="nh-home-section">', unsafe_allow_html=True)
    st.subheader("See it with a real week")
    st.markdown("Pick a door and try the demo on fictional data.")
    close_a, close_b = st.columns(2)
    with close_a:
        if st.button("I need staff", type="primary", key="close_staff", on_click=go, args=("Manager",),
                     width="stretch"):
            st.switch_page("views/planner.py")
    with close_b:
        if st.button("I'm looking for shifts", key="close_shifts", on_click=go, args=("Part-timer",),
                     width="stretch"):
            st.switch_page("views/offers.py")
    st.markdown("</div>", unsafe_allow_html=True)

footer()
