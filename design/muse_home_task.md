Build the Nearhand home page described in design/home_brief.md. Read AGENTS.md, design/BRIEF.md and
design/home_brief.md first.

Scope: create views/home.py; edit app.py and ui/common.py (CSS only). Do not touch core/, tests/, data/,
evals/, scripts/ or other views. Do not add dependencies.

1. app.py: add a Home page, `st.Page("views/home.py", title="Home", icon=":material/home:", default=True)`,
   as the first page in BOTH the Manager and the Part-timer page lists (the same st.Page object), and remove
   `default=True` from the Planner and Offers pages. Keep everything else in app.py as it is.
2. views/home.py: build the sections from the brief. Start with `setup_page()` from ui.common.
   The two path buttons must work like this (tests/test_home.py checks it):
   ```python
   def go(view):
       st.session_state.view = view

   if st.button("I need staff", type="primary", key="hero_staff", on_click=go, args=("Manager",)):
       st.switch_page("views/planner.py")
   if st.button("I'm looking for shifts", key="hero_shifts", on_click=go, args=("Part-timer",)):
       st.switch_page("views/offers.py")
   ```
   The card buttons ("Open the planner", "See my offers") and the closing buttons do the same, each with
   its own key. The hero buttons keep exactly the labels "I need staff" and "I'm looking for shifts".
   Show images from assets/home/ only if the file exists (os.path.exists).
   Read the numbers for the numbers strip at run time (see the brief); skip any that are missing.
3. ui/common.py: add the CSS the page needs (hero, eyebrow, numbers strip, door cards, steps), scoped with
   `nh-home-` class names or `st.container(key="nh-home-...")` (the key becomes the class
   `st-key-nh-home-...`; Streamlit 1.66 has no stVerticalBlockBorderWrapper test id). Keep existing CSS.

Rules:
- Every button label and string listed under "Must not change" in design/BRIEF.md stays exactly as is.
- No testimonials, ratings, "trusted by" or invented numbers (tests/test_home.py checks for these).
- Run `python -m pytest -q tests/test_home.py` after each step, and `python -m pytest -q` at the end.
  All tests must pass.
- Finish with a short summary of what changed in each file.
