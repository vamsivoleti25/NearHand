# Nearhand design brief

**Product:** Nearhand schedules a small business's week from plain English, then fills open shifts with
part-timers from the neighborhood. Two people use it: the **owner/shift manager** (desktop, busy, wants
answers at a glance) and the **part-timer** (phone, wants to know "is this worth it?" in five seconds).

**Tagline:** Your week, scheduled. Your gaps, filled from down the street.

**Personality:** warm, neighborly, competent. A good shift lead, not an HR system. Plain words, no jargon,
no exclamation marks in the UI except the business's own offer message.

## Brand tokens

| Token | Value | Use |
| --- | --- | --- |
| Violet 700 (primary) | `#6D28D9` | buttons, active steps, links, the logo |
| Violet 50 | `#F5F3FF` | selected chips, plan card background |
| Amber 500 (accent) | `#F59E0B` | "offer sent", highlights, one accent per screen at most |
| Green 600 | `#16A34A` | booked, "Take it", coverage gains |
| Red 500 | `#EF4444` | open shifts, "Pass" |
| Slate 900 / 600 / 200 | `#0F172A` / `#475569` / `#E2E8F0` | text / secondary text / borders |
| Paper | `#FAF7F2` | app background (warm off-white) |
| White | `#FFFFFF` | cards |

- **Type:** "Plus Jakarta Sans" for headings (600–700), "Inter" for body (400–500), from Google Fonts.
  Numbers in metrics use tabular figures.
- **Shape:** 12px radius on cards, 999px on chips and pills, 1px slate-200 borders, no heavy shadows
  (one soft shadow on cards at most).
- **Status is never colour alone:** keep the ✓ / ⏳ / ⚠ marks and the words.

## Screen by screen

**Sidebar:** logo (assets/logo.png via `st.logo`), tagline, "View as" switch, business or part-timer
picker, then "Reset demo data" as a quiet secondary button at the bottom.

**Planner (manager, the demo's hero screen):**
- Header: business name, then "Week of Nov 2–8" as a subtitle, then the progress steps.
- Rules card: two columns as now; the understood-rules chips in violet-50.
- Metrics: four white cards in a row, label small and slate-600, value large.
- Impact card: visually the strongest element after the grid (violet-50 background, violet left border),
  because it carries the "so what" for judges.
- Grid: keep it; add a small legend row above it (✓ booked, ⏳ offer sent, red = open shift).
- Agent plan card: white, violet left border; the "checked against the optimizer" line as a small
  green badge; the agent trace in an expander styled like a timeline.

**Offers inbox (part-timer):** designed for a phone width. One card per offer, max width ~560px,
centered: business name + distance, "You'd earn $172.50" big, shifts as a short list, the
recommendation pill, then Accept (primary, full width) and Decline (secondary).

**Marketplace:** cards with an initials avatar circle (colour picked from the name), name, rate pill,
distance, role-years chips, certification chips in amber outline, "In their words" expander.

**Calendar (part-timer):** keep the hour grid; soften colours (green-50 free, slate-100 busy elsewhere,
violet booked) and add a one-line legend.

**Empty states:** one friendly sentence and what to do next.

## Must not change

- Anything in `core/`, `tests/`, `data/`, `scripts/`.
- The text of these buttons and labels (the tests click them by name): "Simplify rules", "Build schedule",
  "Fill gaps with AI", "Find replacements with AI", "Send offers", "Discard", "Should I take this?",
  "Accept", "Decline", "Read my team", "Save team", "Build my staffing plan", "Search",
  the metric label "Open shifts", the recommendation pills ("Take it", "Worth a look", "Pass"),
  and the info message starting "No offers right now".
- Money in any markdown must go through `md()` (otherwise `$` renders as maths).
- The "Built with Muse Spark Open" line (required by the model licence).
