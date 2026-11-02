Restyle the Nearhand Streamlit app according to design/BRIEF.md. Read AGENTS.md and design/BRIEF.md first.

Scope: edit only app.py, ui/common.py, views/*.py, .streamlit/config.toml. Do not touch core/, tests/,
data/ or scripts/. Do not add dependencies.

Do these, in order:

1. .streamlit/config.toml: set the theme from the brief's tokens (base light, primaryColor,
   backgroundColor, secondaryBackgroundColor, textColor, borderColor, baseRadius, and the fonts if this
   Streamlit version supports them; check with `python -c "import streamlit; print(streamlit.__version__)"`).
2. app.py: if assets/logo.png exists, call st.logo("assets/logo.png", icon_image="assets/favicon.png" if it
   exists) and use assets/favicon.png as page_icon; otherwise keep the current emoji. Keep the sidebar
   order from the brief.
3. ui/common.py: extend CSS with the brief's typography (Google Fonts @import for Plus Jakarta Sans and
   Inter), card styling, metric cards, chip and pill styles, the impact-card style, an initials-avatar
   helper `avatar(name) -> html`, and a small `legend(items)` helper. Keep every existing function and its
   signature (other files import them).
4. views/planner.py: header (business name, week subtitle, progress), legend above the grid, impact card
   and plan card styles from the brief, agent trace as a timeline inside the existing expander.
5. views/offers.py: phone-width card layout from the brief.
6. views/marketplace.py: cards with avatar, rate pill, chips.
7. views/calendar.py: softer colours and a legend.

Rules:
- Keep every button label and the other strings listed under "Must not change" in the brief exactly.
- Any markdown containing money goes through md().
- Prefer CSS in ui/common.py over inline styles scattered through views.
- After each step run: python -m pytest -q tests/test_ui.py
  When everything is done run the whole suite: python -m pytest -q   (all tests must pass).
- Finish with a short summary of what changed per file.
