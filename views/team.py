"""Team: paste your staff list the way you'd text it; Muse Spark Open turns it into records."""

import pandas as pd
import streamlit as st

from core import db, parsers
from core.schemas import Employee
from core.taxonomy import DAYS, ROLES, SKILLS
from ui.common import current_business, footer, load_state, md, role, save_state, seed_text, setup_page

setup_page()
biz = current_business()
st.title(f"{biz.name} · team")

text = st.text_area("Paste your team (names, jobs, pay, hours, days off)",
                    value=seed_text("team_texts.json", biz.id), height=220)
if st.button("Read my team", type="primary"):
    with st.spinner("Muse Spark Open is reading your list…"):
        try:
            parsed = parsers.parse_team(text, biz.type)
        except Exception as e:
            st.error(f"Couldn't read the list: {e}")
            st.stop()
    st.session_state.parsed_team = parsed

parsed = st.session_state.get("parsed_team")
employees = parsed.employees if parsed else db.team(biz.id)
if parsed:
    st.success(f"Read {len(parsed.employees)} people. Check them, edit anything, then save.")
    for q in parsed.ambiguities:
        st.warning(md(q), icon="❓")

df = pd.DataFrame([{
    "id": e.id, "Name": e.name, "Roles": ", ".join(e.roles), "Skills": ", ".join(e.skills),
    "$/hour": e.rate_cents / 100, "Overtime $/hour": e.ot_rate_cents / 100, "Max hours": e.max_hours,
    "Days off": ", ".join(e.unavailable_days), "Full-time": e.employment == "full_time"} for e in employees])
edited = st.data_editor(df, hide_index=True, width="stretch", disabled=["id"], num_rows="dynamic")
st.caption(f"Roles: {', '.join(ROLES)}. Skills: {', '.join(SKILLS)}. Days: {', '.join(DAYS)}.")


def split(v):
    return [x.strip() for x in str(v or "").split(",") if x.strip()]


if st.button("Save team"):
    try:
        team = [Employee(id=r["id"] or f"emp_{str(r['Name']).split()[0].lower()}", name=r["Name"],
                         roles=split(r["Roles"]), skills=split(r["Skills"]),
                         rate_cents=round(float(r["$/hour"]) * 100), ot_rate_cents=round(float(r["Overtime $/hour"]) * 100),
                         max_hours=float(r["Max hours"]), unavailable_days=split(r["Days off"]),
                         employment="full_time" if r["Full-time"] else "part_time")
                for r in edited.to_dict("records") if r.get("Name")]
    except Exception as e:
        st.error(f"Something in the table isn't valid: {e}")
        st.stop()
    db.save_team(biz.id, team)
    state = load_state(biz.id)
    if state:
        save_state(biz.id, {"rules_text": state.get("rules_text")})  # the old schedule no longer matches
    st.session_state.pop("parsed_team", None)
    st.success(f"Saved {len(team)} people. Rebuild the schedule on the Planner.")

st.caption("Roles on file: " + ", ".join(sorted({role(r) for e in employees for r in e.roles})))
footer()
