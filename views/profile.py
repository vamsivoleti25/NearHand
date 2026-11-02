"""A part-timer describes their experience in their own words; Muse Spark Open turns it into tags."""

import streamlit as st

from core import db, parsers
from ui.common import SKILL_LABELS, chips, footer, md, money, role, setup_page

setup_page()
p = db.part_timer(st.session_state.get("part_timer_id", "pt_001"))
st.title(f"{p.name} · profile")

st.markdown("**Your profile today**")
chips([f"{role(r.role)} {r.years:g}y" for r in p.roles] + [f"{money(p.expected_rate_cents)}/h"]
      + [t.replace("_", " ") for t in p.business_types])
chips([SKILL_LABELS.get(s, s) for s in p.skills], "warn")

bio = st.text_area("Tell businesses about your work experience", value=p.bio, height=170)
if st.button("Update my profile from this", type="primary"):
    with st.spinner("Muse Spark Open is reading your experience…"):
        try:
            prof = parsers.parse_experience(bio)
        except Exception as e:
            st.error(f"Couldn't read it: {e}")
            st.stop()
    st.session_state.new_profile = prof

prof = st.session_state.get("new_profile")
if prof:
    with st.container(border=True):
        st.markdown("**Here's what I picked up.** Save it if it looks right.")
        chips([f"{role(r.role)} {r.years:g}y" for r in prof.roles]
              + ([f"{money(prof.expected_rate_cents)}/h"] if prof.expected_rate_cents else [])
              + [t.replace("_", " ") for t in prof.business_types])
        chips([SKILL_LABELS.get(s, s) for s in prof.skills], "warn")
        if prof.summary:
            st.caption(md(prof.summary))
        if st.button("Save profile"):
            update = {"roles": prof.roles, "skills": prof.skills, "business_types": prof.business_types,
                      "summary": prof.summary, "bio": bio}
            if prof.expected_rate_cents:
                update["expected_rate_cents"] = prof.expected_rate_cents
            db.save_part_timer(p.model_copy(update=update))
            st.session_state.pop("new_profile")
            st.success("Saved. Businesses searching nearby now see the updated profile.")
            st.rerun()
footer()
