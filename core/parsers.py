"""The five language jobs. Each is a system prompt + a schema; core/llm.py does the call.

The model only turns text into structure. The solver (via adapter.py) decides assignments and money.
"""

from __future__ import annotations

import json

from core import llm
from core.schemas import (
    Business, ConstraintSet, Employee, PartTimerProfile, SearchFilter, StaffingBlock, StaffingGrid,
    TeamParse,
)
from core.taxonomy import DAYS, ROLE_REQUIRED_SKILLS, ROLES, SKILLS, TEMPLATES, from_q, roles_for, to_q

VOCAB = (
    f"Allowed role IDs: {', '.join(ROLES)}.\n"
    f"Allowed skill IDs: {', '.join(SKILLS)} "
    "(keyholder = can open and close; food_safety_manager = certified food protection manager).\n"
    f"Days: {', '.join(DAYS)}. Times are 24-hour HH:MM. Money is integer cents ($17.50 -> 1750)."
)

UNSURE = "If something is unclear, do not guess silently: add a short question to `ambiguities`."


# ---------------------------------------------------------------- 1. business -> staffing grid

def default_grid(business: Business) -> StaffingGrid:
    """Template staffing with no model call: each role, each open day, split into two shifts."""
    blocks = []
    for day in DAYS:
        if day not in business.hours:
            continue
        open_q, close_q = to_q(business.hours[day][0]), to_q(business.hours[day][1])
        mid = open_q + ((close_q - open_q) // 2 // 4) * 4  # split on the hour
        for role in roles_for(business.type, set(business.toggles)):
            # No skills here: "a keyholder at open/close" comes from the manager's rules (adapter.py).
            for s, e in ((open_q, mid), (mid, close_q)):
                blocks.append(StaffingBlock(day=day, role=role, start=from_q(s), end=from_q(e), headcount=1))
    return StaffingGrid(blocks=blocks, notes=["Template default: one per role per half-day."])


MIN_BLOCK_Q = 4 * 4  # nobody works a shift shorter than 4 hours


def normalize_grid(grid: StaffingGrid, business: Business) -> StaffingGrid:
    """Stretch blocks shorter than 4 hours to 4 hours, staying inside opening hours where possible."""
    blocks = []
    for b in grid.blocks:
        s, e = to_q(b.start), to_q(b.end)
        if e - s < MIN_BLOCK_Q and b.day in business.hours:
            open_q, close_q = to_q(business.hours[b.day][0]), to_q(business.hours[b.day][1])
            e = min(max(e, s + MIN_BLOCK_Q), max(close_q, e))
            s = max(min(s, e - MIN_BLOCK_Q), 0 if b.role == "baker" else open_q)
            b = b.model_copy(update={"start": from_q(s), "end": from_q(e)})
        blocks.append(b)
    return grid.model_copy(update={"blocks": blocks})


GRID_SYSTEM = f"""You adjust a small business's weekly staffing grid.
You get the business type, opening hours, a template grid, and the owner's one-line description.
Return the full adjusted grid: add headcount for the busy times they describe, add a mid shift
(at least 4 hours, e.g. 07:00-11:00 for a 7-9am rush) where that fits better than raising a whole
half-day, cut headcount for times they say are slow, and drop roles they say they lack.
Every block must be at least 4 hours long: nobody works a 2-hour shift.
Keep blocks inside opening hours (except bakers, who may start up to 3 hours before opening).
Keep it realistic for a small business: rarely more than 3 of one role at once.
Put a one-line reason for each change in `notes`.
{VOCAB}"""


def parse_business(business: Business, provider=None) -> StaffingGrid:
    base = default_grid(business)
    if not business.description.strip():
        return base
    user = json.dumps({
        "business_type": TEMPLATES[business.type]["label"],
        "toggles": business.toggles,
        "hours": business.hours,
        "description": business.description,
        "template_grid": [b.model_dump() for b in base.blocks],
    })
    grid = llm.call_json("parse_business", GRID_SYSTEM, user, StaffingGrid, provider=provider, max_tokens=12000)
    return normalize_grid(grid, business)


# ---------------------------------------------------------------- 2. pasted team -> employees

TEAM_SYSTEM = f"""You turn a manager's pasted team list into employee records.
Rules:
- id is emp_ + lowercase first name (add the last initial if two people share a first name).
- Map job titles to role IDs (e.g. "coffee"/"bar" -> barista, "register"/"front" -> cashier,
  "lead"/"supervisor"/"opener" -> shift_lead). A person can have several roles.
- Overtime rate is 1.5x the hourly rate unless stated.
- max_hours: use what is stated; otherwise 40 for full-time, 25 for part-time.
- Put days they can't work in unavailable_days.
- Add keyholder for anyone who opens or closes ("opens", "closer", "opener", "has keys") and for
  shift leads and managers unless the text says they don't; mention it in ambiguities so the manager
  can confirm. Add other certifications only when stated.
{VOCAB}
{UNSURE}"""

LEAD_ROLES = ("shift_lead", "shift_manager")


def parse_team(text: str, business_type: str = "cafe", provider=None) -> TeamParse:
    user = f"Business type: {TEMPLATES[business_type]['label']}\n\nTeam list:\n{text}"
    out = llm.call_json("parse_team", TEAM_SYSTEM, user, TeamParse, provider=provider, max_tokens=8000)
    for e in out.employees:  # certifications the role requires by law are assumed held, but flagged
        if any(r in LEAD_ROLES for r in e.roles) and "keyholder" not in e.skills:
            e.skills.append("keyholder")
            out.ambiguities.append(f"Assumed {e.name} is a keyholder as a shift lead; confirm.")
        for role in e.roles:
            for skill in ROLE_REQUIRED_SKILLS.get(role, []):
                if skill not in e.skills:
                    e.skills.append(skill)
                    out.ambiguities.append(f"Assumed {e.name} holds {skill} for {role}; confirm.")
    return out


# ---------------------------------------------------------------- 3. rules with @mentions -> constraints

RULES_SYSTEM = f"""You turn a manager's scheduling rules into typed constraints.
- "@Name" or a plain first name refers to an employee: use their employee_id from the team list.
  If a name is not on the team, do not invent an id: add a question to ambiguities.
- "off Tuesdays", "can't do Tue" -> unavailable. "mornings off" -> unavailable with start/end.
- "max 32h", "no more than 32 hours" -> max_hours (employee, role, or everyone).
- "a keyholder at every open" -> require_skill with at = open (close / all_shifts likewise).
- "10 hours between shifts" -> min_rest. "Leo works Saturday 8-2" -> fixed_assignment.
- "Ana prefers weekends" -> prefer; "avoid giving Sam Mondays" -> prefer with kind = avoid.
- "keep labor under $5k" -> budget_cap.
Only include constraints the text actually states.
{VOCAB}
{UNSURE} Vague times ("mornings", "late") need a question unless the opening hours make them obvious."""


def parse_rules(text: str, employees: list[Employee], business: Business | None = None,
                provider=None) -> ConstraintSet:
    team = [{"employee_id": e.id, "name": e.name, "roles": e.roles} for e in employees]
    user = json.dumps({"team": team, "opening_hours": business.hours if business else None, "rules": text})
    out = llm.call_json("parse_rules", RULES_SYSTEM, user, ConstraintSet, provider=provider)
    known = {e.id for e in employees}
    kept = []
    for c in out.constraints:  # never let an invented employee id through
        eid = getattr(c, "employee_id", None)
        if eid is not None and eid not in known:
            out.ambiguities.append(f"Rule mentions unknown employee '{eid}'; who is this?")
            continue
        kept.append(c)
    out.constraints = kept
    return out


# ---------------------------------------------------------------- 4. work history -> part-timer profile

EXPERIENCE_SYSTEM = f"""You turn a part-time worker's description of their work history into a profile.
- roles: every role they've done, with total years (round to the nearest 0.5; "a summer" = 0.25).
- business_types: the kinds of businesses they've worked in ({', '.join(TEMPLATES)}).
- skills: only certifications or duties they state (opening/closing a store -> keyholder;
  "ServSafe Manager" -> food_safety_manager; "food handler card" -> food_handler; "TIPS" -> alcohol_service).
- expected_rate_cents: the hourly pay they ask for; 0 if they don't say.
- summary: one plain sentence in third person.
{VOCAB}"""


def parse_experience(text: str, provider=None) -> PartTimerProfile:
    return llm.call_json("parse_experience", EXPERIENCE_SYSTEM, text, PartTimerProfile, provider=provider)


# ---------------------------------------------------------------- 5. manager search -> filters

SEARCH_SYSTEM = f"""You turn a manager's marketplace search into filters. Leave a filter empty unless stated.
"under $20" -> max_rate_cents 2000. "within 3 miles" / "nearby" -> max_distance_miles (nearby = 3).
"weekends" -> Sat, Sun. "mornings" -> 06:00-12:00. "experienced" -> min_years 2.
"latte art" or "espresso" -> role barista. {VOCAB}"""


def parse_search(text: str, provider=None) -> SearchFilter:
    return llm.call_json("parse_search", SEARCH_SYSTEM, text, SearchFilter, provider=provider)
