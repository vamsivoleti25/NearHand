# Nearhand home page brief

The app's first screen. It works like the front door of a two-sided staffing marketplace (the pattern
Medely and similar products use): two kinds of visitor, two clear doors in, the same two doors repeated
down the page. **Borrow the pattern, not anyone's look or words.** Nearhand keeps its own name, logo,
colours (design/BRIEF.md), illustrations and copy.

Who lands here: a hackathon judge (desktop, 3 minutes, wants to understand it and click into the demo),
a café owner, a part-timer on a phone.

## Sections, top to bottom

1. **Hero** (two columns on desktop, stacked on a phone)
   - Eyebrow, small, violet: "Scheduling + a neighborhood part-timer network"
   - H1: "Your week, scheduled. Your gaps, filled from down the street."
   - One-sentence sub: small businesses write their scheduling rules in plain English; Nearhand builds the
     cheapest week that follows them, then an AI agent fills the open shifts with qualified part-timers who
     live nearby, and they accept on their phone.
   - Two buttons side by side: **"I need staff"** (primary) and **"I'm looking for shifts"** (secondary).
   - Right column: assets/home/hero.png (if missing, skip the image; never a broken image).
2. **Numbers strip** (in place of a client-logo strip; we have no clients, so no logos): 3–4 figures, each
   read from real data at run time, never typed in:
   - part-timers in the demo network (count from core.db.part_timers())
   - business types supported (len(core.taxonomy.TEMPLATES))
   - from evals/results/agent_baseten.json if it exists: "N of N test weeks covered as well as the
     optimizer" (matches_optimizer × scenarios) and the eval run's cost per plan;
     from evals/results/rules_baseten.json: share of rule sets read exactly right.
   - A one-line caption: "Demo data: all people and businesses are fictional."
   If a results file is missing, leave that figure out.
3. **Two doors** (two cards side by side, stacked on a phone), each with assets/home/staff.png or
   shifts.png on top if present, a heading, 3 short bullets, and a button:
   - "For owners and shift managers": write rules the way you'd say them ("@Maya off Tuesdays, a keyholder
     at every open"); get the cheapest week that follows them; one click fills open shifts with nearby,
     qualified people, with the cost shown before anything is sent. Button: **"Open the planner"**.
   - "For part-timers": shifts from places down the street, with your rate and your calendar respected;
     one tap to accept, and an honest "should I take this?" that checks your calendar, your weekly max
     and the pay; nothing is booked without your yes. Button: **"See my offers"**.
4. **How it works**: three numbered steps in a row: "Say your rules" → "Get the cheapest week" →
   "Fill the gaps nearby". One sentence each.
5. **Under the hood** (for judges, compact, slate text): Muse Spark Open (open weights, on Baseten) reads
   rules, runs the gap-filling agent and gives offer advice; OR-Tools CP-SAT does every schedule and every
   dollar; every number the AI writes is checked against the optimizer's results; evals on the Evals page.
6. **Closing call to action**: "See it with a real week" plus the same two buttons as the hero, but with
   their own keys.
7. The existing footer (ui.common.footer()).

## Style

- Follows design/BRIEF.md: paper background, white cards, 12px radius, violet primary, one amber accent.
- Headings in Plus Jakarta Sans, large H1 (about 3rem on desktop). Generous vertical space between
  sections (about 3–4rem). Max content width about 1100px, centered.
- No testimonials, star ratings, "trusted by", invented logos or invented numbers.
- All money in markdown goes through md().
