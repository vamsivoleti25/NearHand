"""A part-timer's week: when they're free, busy elsewhere, or booked through Nearhand."""

import pandas as pd
import streamlit as st

from core import db, marketplace
from core.taxonomy import DAYS, to_q
from ui.common import SKILL_LABELS, chips, footer, legend, money, role, setup_page

setup_page()
p = db.part_timer(st.session_state.get("part_timer_id", "pt_001"))
bookings = db.bookings(part_timer_id=p.id)
biz_names = {b.id: b.name for b in db.businesses()}

st.title(f"{p.name.split()[0]}'s week")
chips([f"{role(r.role)} {r.years:g}y" for r in p.roles] + [f"{money(p.expected_rate_cents)}/h"])
chips([SKILL_LABELS.get(s, s) for s in p.skills], "warn")

hours = list(range(5, 24))
grid = pd.DataFrame("", index=[f"{h:02d}:00" for h in hours], columns=DAYS)


def mark(day, start, end, text):
    for h in hours:
        if to_q(start) < (h + 1) * 4 and h * 4 < to_q(end):
            grid.loc[f"{h:02d}:00", day] = text


for d, wins in p.availability.items():
    for s, e in wins:
        mark(d, s, e, "Free")
for d, s, e in p.blocked:
    mark(d, s, e, "Busy elsewhere")
for b in bookings:
    mark(b.day, b.start, b.end, f"Booked · {biz_names.get(b.business_id, b.business_id)}")


def color(v):
    if v.startswith("Booked"):
        return "background-color: rgba(109,40,217,.14); font-weight: 600"
    if v == "Free":
        return "background-color: rgba(22,163,74,.08)"
    if v == "Busy elsewhere":
        return "background-color: rgba(148,163,184,.15)"
    return ""


legend([("#16A34A", "free"), ("#94A3B8", "busy elsewhere"), ("#6D28D9", "booked")])
st.dataframe(grid.style.map(color), width="stretch", height=36 * len(hours) + 40)

st.subheader("Booked through Nearhand")
if bookings:
    st.dataframe([{"Day": b.day, "Time": f"{b.start}–{b.end}", "Where": biz_names.get(b.business_id),
                   "Role": role(b.role), "Pay": f"{money(b.rate_cents)}/h"} for b in bookings], hide_index=True)
else:
    st.caption("No bookings yet. Shifts you're booked for appear here and block that time for other businesses.")
d = marketplace.distance_miles(p, db.business("biz_bean"))
st.caption(f"Bean & Co. is {d} mi away. Your address is never shown, only distance.")
footer()
