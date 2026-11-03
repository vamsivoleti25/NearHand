"""A short guided tour for judges: one line at the top of the page saying what to click next.

The step comes from what has actually happened in this visitor's demo, so it never gets out of step.
"""

from __future__ import annotations

import streamlit as st

from core import db

STEPS = 4


def start() -> None:
    st.session_state.tour = True
    st.session_state.view = "Manager"


def stop() -> None:
    st.session_state.tour = False


def _step(acct) -> tuple[int, str]:
    if acct is not None and acct.role != "owner":
        if st.session_state.get("view_back"):
            return 3, ("This is their phone. Try **Should I take this?**, then **Accept** or **Decline**, "
                       "then **← Back to the planner**.")
        return 3, "Open the account menu (top right) and switch back to Dana, the owner."
    bid = acct.business_id if acct is not None else "biz_bean"
    state = db.schedule(bid)
    sent = db.offers(business_id=bid) if state.get("staff_result") else []
    if not state.get("constraints") or not state.get("staff_result"):
        return 1, ("On the **Planner**, click **Read my rules** (Muse Spark Open reads plain English), then "
                   "**Build schedule** (OR-Tools finds the cheapest week that follows them).")
    if not sent:
        return 2, ("Click **Fill gaps with AI** and watch the agent work, asking the team first and then "
                   "people nearby. Every number it writes is checked. Then **Send offers**.")
    if not any(o.status in ("accepted", "declined") for o in sent):
        return 3, "Click a name under **See it on their phone** to answer the offer as that person."
    return 4, ("Declines are replaced automatically (see **Activity**). Now type **“Chris called out sick "
               "Thursday”** in *Someone called out?*, or ask for different hours in **Offers & messages**.")


def banner(acct) -> None:
    if not st.session_state.get("tour"):
        return
    n, text = _step(acct)
    with st.container(key="nh-tour", horizontal=True, vertical_alignment="center"):
        st.markdown(f'<span class="nh-tour-n">Tour · {n} of {STEPS}</span>', unsafe_allow_html=True)
        st.markdown(text.replace("$", "\\$"))
        st.button("End tour", key="tour_end", on_click=stop)
