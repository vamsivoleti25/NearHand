"""Data shapes shared by the parsers, the solver adapter, the database and the UI.

Times the model reads and writes are "HH:MM" strings (easier for it than 15-minute units);
convert with taxonomy.to_q. Money is integer cents.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from core.taxonomy import DAYS, ROLES, SKILLS, TEMPLATES

Role = Literal[tuple(ROLES)]
Skill = Literal[tuple(SKILLS)]
Day = Literal[tuple(DAYS)]
BusinessType = Literal[tuple(TEMPLATES)]
HHMM = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="24-hour time, e.g. 06:30")]


# ---------------------------------------------------------------- business and staffing grid

class Business(BaseModel):
    id: str
    name: str
    type: BusinessType
    toggles: list[str] = []
    zip: str = ""
    hours: dict[Day, tuple[HHMM, HHMM]]
    description: str = ""


class StaffingBlock(BaseModel):
    day: Day
    role: Role
    start: HHMM
    end: HHMM
    headcount: int = Field(ge=1, le=20)
    skills: list[Skill] = []


class StaffingGrid(BaseModel):
    blocks: list[StaffingBlock]
    notes: list[str] = Field(default=[], description="Short notes on what was changed from the template and why")


# ---------------------------------------------------------------- team

class Employee(BaseModel):
    id: str = Field(description="emp_ + lowercase first name, e.g. emp_maya")
    name: str
    roles: list[Role] = Field(min_length=1)
    skills: list[Skill] = []
    rate_cents: int = Field(ge=0, description="Hourly pay in cents, e.g. $17.50/hr -> 1750")
    ot_rate_cents: int = Field(ge=0, description="Overtime hourly pay in cents; 1.5x rate_cents if not stated")
    max_hours: float = Field(gt=0, le=80, description="Weekly cap; 40 for full-time, 25 for part-time if not stated")
    employment: Literal["full_time", "part_time"] = "part_time"
    unavailable_days: list[Day] = []


class TeamParse(BaseModel):
    employees: list[Employee]
    ambiguities: list[str] = Field(default=[], description="Anything guessed or unclear, as a short question")


# ---------------------------------------------------------------- rules (constraints)

class Unavailable(BaseModel):
    type: Literal["unavailable"] = "unavailable"
    employee_id: str
    days: list[Day]
    start: HHMM | None = Field(default=None, description="Omit for the whole day")
    end: HHMM | None = None


class MaxHours(BaseModel):
    type: Literal["max_hours"] = "max_hours"
    employee_id: str | None = Field(default=None, description="One person; omit when it applies to a role or everyone")
    role: Role | None = Field(default=None, description="All workers with this role; omit both for everyone")
    hours: float = Field(gt=0, le=80)


class RequireSkill(BaseModel):
    type: Literal["require_skill"] = "require_skill"
    skill: Skill
    at: Literal["open", "close", "all_shifts"]
    days: list[Day] = Field(default=list(DAYS))
    at_least: int = Field(default=1, ge=1)


class MinRest(BaseModel):
    type: Literal["min_rest"] = "min_rest"
    hours: float = Field(gt=0, le=24)


class FixedAssignment(BaseModel):
    type: Literal["fixed_assignment"] = "fixed_assignment"
    employee_id: str
    day: Day
    start: HHMM
    end: HHMM


class Prefer(BaseModel):
    type: Literal["prefer"] = "prefer"
    employee_id: str
    days: list[Day]
    kind: Literal["prefer", "avoid"] = "prefer"
    weight: int = Field(default=3, ge=1, le=10)


class BudgetCap(BaseModel):
    type: Literal["budget_cap"] = "budget_cap"
    dollars: float = Field(gt=0)


Constraint = Annotated[
    Union[Unavailable, MaxHours, RequireSkill, MinRest, FixedAssignment, Prefer, BudgetCap],
    Field(discriminator="type"),
]


class ConstraintSet(BaseModel):
    constraints: list[Constraint]
    ambiguities: list[str] = Field(default=[], description="Anything unclear, as a short question for the manager")


# ---------------------------------------------------------------- marketplace

class RoleExperience(BaseModel):
    role: Role
    years: float = Field(ge=0, le=50)


class PartTimerProfile(BaseModel):
    name: str
    zip: str = ""
    expected_rate_cents: int = Field(ge=0, description="Hourly rate they asked for, in cents; 0 if not stated")
    roles: list[RoleExperience]
    business_types: list[BusinessType] = []
    skills: list[Skill] = []
    summary: str = Field(default="", description="One sentence, third person, for a profile card")


class PartTimer(PartTimerProfile):
    id: str
    availability: dict[Day, list[tuple[HHMM, HHMM]]] = {}
    blocked: list[tuple[Day, HHMM, HHMM]] = Field(default=[], description="Commitments elsewhere and bookings")
    max_hours: float = 25


class SearchFilter(BaseModel):
    roles: list[Role] = []
    skills: list[Skill] = []
    days: list[Day] = []
    start: HHMM | None = None
    end: HHMM | None = None
    max_rate_cents: int | None = None
    max_distance_miles: float | None = None
    min_years: float | None = None
