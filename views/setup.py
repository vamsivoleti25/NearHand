"""Business setup: pick a template, describe your week in a sentence, get a staffing plan to edit."""

import pandas as pd
import streamlit as st

from core import db, parsers
from core.schemas import StaffingGrid
from core.taxonomy import DAYS, TEMPLATES, to_q
from ui.common import current_business, footer, load_state, md, role, save_state, setup_page

setup_page()
biz = current_business()
t = TEMPLATES[biz.type]
st.title(f"{biz.name} · setup")
st.markdown(f"**{t['label']}** · ZIP {biz.zip}")

toggles = st.pills("Options", options=list(t["toggles"]), default=biz.toggles, selection_mode="multi",
                   format_func=lambda x: x.replace("_", " ").capitalize())
hours = st.data_editor(pd.DataFrame([{"Day": d, "Open": biz.hours[d][0], "Close": biz.hours[d][1]}
                                     for d in DAYS if d in biz.hours]),
                       hide_index=True, disabled=["Day"], width="content")
desc = st.text_area("Describe your week in a sentence or two", value=biz.description,
                    placeholder="e.g. We get slammed 7-9am on weekdays and Saturday brunch is our busiest time.")

if st.button("Build my staffing plan", type="primary"):
    b2 = biz.model_copy(update={"toggles": toggles or [], "description": desc,
                                "hours": {r["Day"]: (r["Open"], r["Close"]) for r in hours.to_dict("records")}})
    with st.spinner("Muse Spark Open is adjusting the template to your week…"):
        try:
            grid = parsers.parse_business(b2)
        except Exception as e:
            st.error(f"Couldn't build the plan: {e}")
            st.stop()
    db.save_business(b2)
    db.save_grid(b2.id, grid)
    state = load_state(b2.id)
    save_state(b2.id, {"rules_text": state.get("rules_text")} if state else {})
    st.success("Staffing plan saved. Build the schedule on the Planner.")
    if grid.notes:
        st.markdown("**What changed from the template**")
        for n in grid.notes:
            st.markdown(md(f"- {n}"))
    st.rerun()

grid = db.grid(biz.id)
if grid:
    st.subheader("Staffing plan")
    st.caption("Staff-hours needed per role and day. Edit blocks below if anything looks off.")
    rows = [{"Role": role(b.role), "Day": b.day, "Hours": b.headcount * (to_q(b.end) - to_q(b.start)) / 4}
            for b in grid.blocks]
    pivot = pd.DataFrame(rows).pivot_table(index="Role", columns="Day", values="Hours", aggfunc="sum").reindex(
        columns=[d for d in DAYS if d in biz.hours]).fillna(0)
    top = float(pivot.values.max()) or 1.0
    shade = lambda v: f"background-color: rgba(109,40,217,{0.08 + 0.5 * v / top:.2f})" if v else ""  # noqa: E731
    st.dataframe(pivot.style.map(shade).format("{:g}"), width="stretch")
    with st.expander(f"All {len(grid.blocks)} blocks"):
        blocks = st.data_editor(pd.DataFrame([b.model_dump() for b in grid.blocks]), num_rows="dynamic",
                                width="stretch")
        if st.button("Save blocks"):
            recs = blocks.to_dict("records")
            for r in recs:
                r["skills"] = list(r.get("skills") or [])
            db.save_grid(biz.id, StaffingGrid(blocks=recs, notes=grid.notes))
            st.success("Saved.")
footer()
