"""Marketplace search: a manager types what they need; Muse Spark Open turns it into filters."""

import streamlit as st

from core import db, marketplace, parsers
from ui.common import (SKILL_LABELS, avatar, chips, current_business, footer, md, money,
                      neighborhood_map, role, setup_page)

setup_page()
biz = current_business()
st.title("Find part-timers nearby")
st.caption("Matching uses only role, skills, availability, rate and distance.")

q = st.text_input("What do you need?", value=st.session_state.get("mp_query",
                  "experienced barista for Saturday morning, under $20, within 5 miles"))
if st.button("Search", type="primary") or "mp_filter" not in st.session_state:
    with st.spinner("Reading your search…"):
        try:
            st.session_state.mp_filter = parsers.parse_search(q)
            st.session_state.mp_query = q
        except Exception as e:
            st.error(f"Couldn't read the search: {e}")
            st.stop()

f = st.session_state.mp_filter
understood = ([role(r) for r in f.roles] + [SKILL_LABELS.get(s, s) for s in f.skills] + f.days
              + ([f"{f.start or ''}–{f.end or ''}"] if f.start or f.end else [])
              + ([f"≤ {money(f.max_rate_cents)}/h"] if f.max_rate_cents else [])
              + ([f"≤ {f.max_distance_miles:g} mi"] if f.max_distance_miles else [])
              + ([f"{f.min_years:g}+ years"] if f.min_years else []))
st.markdown("**Understood as**")
chips(understood or ["(no filters)"])

how = st.segmented_control("Sort", ["Best fit", "Cheapest", "Nearest"], default="Best fit", key="mp_sort",
                           label_visibility="collapsed") or "Best fit"
results = marketplace.rank(marketplace.search(f, biz, db.part_timers(), db.bookings(), limit=500), f.roles,
                           how)[:24]
st.markdown(f"**{len(results)} people** · " + {
    "Best fit": "best fit first: rate, plus 50¢ an hour for each mile away, minus 50¢ for each year of "
                "experience in the role (up to 4)",
    "Cheapest": "cheapest first, then nearest", "Nearest": "nearest first, then cheapest"}[how])
all_pts = {p.id: p for p in db.part_timers()}
neighborhood_map(biz, [{"pt": all_pts[c["id"]], "kind": "candidate",
                        "info": f"{money(c['rate_cents'])}/h · {c['distance_miles']} mi"} for c in results])


def _card(col, c):
    with col.container(border=True, key=f"nh-card-mp-{c['id']}"):
        st.markdown(avatar(c["name"]) + md(f" **{c['name']}**"), unsafe_allow_html=True)
        st.markdown(md(f"<span class=\"nh-rate\">{money(c['rate_cents'])}/h</span> · "
                       f"{c['distance_miles']} mi away"), unsafe_allow_html=True)
        chips([f"{role(r['role'])} {r['years']:g}y" for r in c["roles"]])
        chips([SKILL_LABELS.get(s, s) for s in c["skills"]], "warn")
        st.caption(md("Why here: " + marketplace.fit(c, f.roles)[1]))
        if c["free_days"]:
            st.caption("Free: " + ", ".join(c["free_days"]))
        bio = db.part_timer(c["id"]).bio
        if bio:
            with st.expander("In their words"):
                st.markdown(md(bio))


for row in range(0, len(results), 3):   # one row of three at a time, so cards in a row line up
    with st.container(key=f"nh-cards-mp-{row}"):
        cols = st.columns(3)
    for col, c in zip(cols, results[row:row + 3]):
        _card(col, c)
footer()
