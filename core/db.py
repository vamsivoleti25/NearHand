"""SQLite storage. Each record is stored as JSON next to its id; Pydantic models give it shape.

The database rebuilds itself from data/seed/ when empty, and reset_demo() puts it back
to the seed state before every recording.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core import llm
from core.schemas import Account, Booking, Business, Employee, Offer, PartTimer, StaffingGrid

SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "seed"

TABLES = {
    "businesses": "id TEXT PRIMARY KEY, json TEXT",
    "employees": "id TEXT, business_id TEXT, json TEXT, PRIMARY KEY (id, business_id)",
    "part_timers": "id TEXT PRIMARY KEY, json TEXT",
    "bookings": "id TEXT PRIMARY KEY, part_timer_id TEXT, business_id TEXT, json TEXT",
    "grids": "business_id TEXT PRIMARY KEY, json TEXT",
    "schedules": "business_id TEXT PRIMARY KEY, json TEXT",  # the planner's saved week (ui/common.py)
    "offers": "id TEXT PRIMARY KEY, business_id TEXT, part_timer_id TEXT, status TEXT, json TEXT",
    "accounts": "id TEXT PRIMARY KEY, email TEXT UNIQUE, json TEXT",
}

# Fictional owners for the two demo businesses (sign-in shows them as demo accounts).
OWNERS = {"biz_bean": "Dana Okafor", "biz_maple": "Marcus Bell"}
DOMAINS = {"biz_bean": "beanandco.example", "biz_maple": "mapletable.example"}


def connect() -> sqlite3.Connection:
    llm.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(llm.DB_PATH)
    for name, cols in TABLES.items():
        con.execute(f"CREATE TABLE IF NOT EXISTS {name} ({cols})")
    return con


def _seed(name: str):
    return json.loads((SEED_DIR / name).read_text())


def load_seed(force: bool = False) -> None:
    """Fill the database from data/seed/ if it is empty (or always, with force=True)."""
    with connect() as con:
        if not force and con.execute("SELECT COUNT(*) FROM part_timers").fetchone()[0]:
            if not con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]:   # databases made before accounts
                for a in seed_accounts():
                    con.execute("INSERT OR IGNORE INTO accounts VALUES (?, ?, ?)", (a.id, a.email, a.model_dump_json()))
            return
        for t in TABLES:
            con.execute(f"DELETE FROM {t}")
        for b in _seed("businesses.json"):
            con.execute("INSERT INTO businesses VALUES (?, ?)", (b["id"], json.dumps(b)))
        for business_id, team in _seed("teams.json").items():
            for e in team:
                con.execute("INSERT INTO employees VALUES (?, ?, ?)", (e["id"], business_id, json.dumps(e)))
        for business_id, grid in _seed("grids.json").items():
            con.execute("INSERT INTO grids VALUES (?, ?)", (business_id, json.dumps(grid)))
        for p in _seed("part_timers.json"):
            con.execute("INSERT INTO part_timers VALUES (?, ?)", (p["id"], json.dumps(p)))
        for a in seed_accounts():
            con.execute("INSERT OR IGNORE INTO accounts VALUES (?, ?, ?)", (a.id, a.email, a.model_dump_json()))


def _slug(name: str) -> str:
    return "".join(ch for ch in name.lower().replace(" ", ".") if ch.isalnum() or ch == ".")


def seed_accounts() -> list[Account]:
    """An account for every owner, every employee (so they can get extra-hours offers) and every part-timer."""
    out = []
    for bid, owner in OWNERS.items():
        out.append(Account(id=f"acct_owner_{bid}", role="owner", name=owner, business_id=bid,
                           email=f"{_slug(owner.split()[0])}@{DOMAINS[bid]}"))
    for bid, team in _seed("teams.json").items():
        for e in team:
            out.append(Account(id=f"acct_{bid}_{e['id']}", role="staff", name=e["name"], business_id=bid,
                               employee_id=e["id"], email=f"{_slug(e['name'])}@{DOMAINS.get(bid, 'shop.example')}"))
    for p in _seed("part_timers.json"):
        out.append(Account(id=f"acct_{p['id']}", role="part_timer", name=p["name"], part_timer_id=p["id"],
                           email=f"{_slug(p['name'])}.{p['id'].split('_')[1]}@example.com"))
    return out


def reset_demo() -> None:
    load_seed(force=True)


# ---------------------------------------------------------------- reads

def businesses() -> list[Business]:
    with connect() as con:
        return [Business.model_validate_json(r[0]) for r in con.execute("SELECT json FROM businesses ORDER BY id")]


def business(business_id: str) -> Business:
    with connect() as con:
        row = con.execute("SELECT json FROM businesses WHERE id = ?", (business_id,)).fetchone()
    if not row:
        raise KeyError(business_id)
    return Business.model_validate_json(row[0])


def team(business_id: str) -> list[Employee]:
    with connect() as con:
        rows = con.execute("SELECT json FROM employees WHERE business_id = ? ORDER BY id", (business_id,))
        return [Employee.model_validate_json(r[0]) for r in rows]


def grid(business_id: str) -> StaffingGrid | None:
    with connect() as con:
        row = con.execute("SELECT json FROM grids WHERE business_id = ?", (business_id,)).fetchone()
    return StaffingGrid.model_validate_json(row[0]) if row else None


def part_timers() -> list[PartTimer]:
    with connect() as con:
        return [PartTimer.model_validate_json(r[0]) for r in con.execute("SELECT json FROM part_timers ORDER BY id")]


def part_timer(pt_id: str) -> PartTimer:
    with connect() as con:
        row = con.execute("SELECT json FROM part_timers WHERE id = ?", (pt_id,)).fetchone()
    if not row:
        raise KeyError(pt_id)
    return PartTimer.model_validate_json(row[0])


def bookings(business_id: str | None = None, part_timer_id: str | None = None) -> list[Booking]:
    q, args = "SELECT json FROM bookings WHERE 1=1", []
    if business_id:
        q, args = q + " AND business_id = ?", args + [business_id]
    if part_timer_id:
        q, args = q + " AND part_timer_id = ?", args + [part_timer_id]
    with connect() as con:
        return [Booking.model_validate_json(r[0]) for r in con.execute(q, args)]


# ---------------------------------------------------------------- writes

def save_business(b: Business) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO businesses VALUES (?, ?)", (b.id, b.model_dump_json()))


def save_team(business_id: str, employees: list[Employee]) -> None:
    with connect() as con:
        con.execute("DELETE FROM employees WHERE business_id = ?", (business_id,))
        for e in employees:
            con.execute("INSERT INTO employees VALUES (?, ?, ?)", (e.id, business_id, e.model_dump_json()))


def save_grid(business_id: str, g: StaffingGrid) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO grids VALUES (?, ?)", (business_id, g.model_dump_json()))


def save_part_timer(p: PartTimer) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO part_timers VALUES (?, ?)", (p.id, p.model_dump_json()))


def add_booking(b: Booking) -> None:
    with connect() as con:
        con.execute("INSERT INTO bookings VALUES (?, ?, ?, ?)",
                    (b.id, b.part_timer_id, b.business_id, b.model_dump_json()))


# ---------------------------------------------------------------- the planner's saved week

def schedule(business_id: str) -> dict:
    with connect() as con:
        row = con.execute("SELECT json FROM schedules WHERE business_id = ?", (business_id,)).fetchone()
    return json.loads(row[0]) if row else {}


def save_schedule(business_id: str, state: dict) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO schedules VALUES (?, ?)", (business_id, json.dumps(state, default=str)))


# ---------------------------------------------------------------- offers

def save_offer(o: Offer) -> None:
    who = o.employee_id if o.kind == "extra_hours" else o.part_timer_id   # the column holds the recipient
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO offers VALUES (?, ?, ?, ?, ?)",
                    (o.id, o.business_id, who, o.status, o.model_dump_json()))


def offers(business_id: str | None = None, part_timer_id: str | None = None, status: str | None = None,
           employee_id: str | None = None) -> list[Offer]:
    """Offers, oldest first. part_timer_id: marketplace offers for that person; employee_id (with
    business_id): extra-hours offers for that team member."""
    q, args = "SELECT json FROM offers WHERE 1=1", []
    for col, val in (("business_id", business_id), ("part_timer_id", part_timer_id or employee_id),
                     ("status", status)):
        if val:
            q, args = q + f" AND {col} = ?", args + [val]
    with connect() as con:
        out = [Offer.model_validate_json(r[0]) for r in con.execute(q, args)]
    if part_timer_id:
        out = [o for o in out if o.kind == "shift"]
    if employee_id:
        out = [o for o in out if o.kind == "extra_hours"]
    return sorted(out, key=lambda o: o.created_ts)


# ---------------------------------------------------------------- accounts

def accounts(role: str | None = None, business_id: str | None = None) -> list[Account]:
    with connect() as con:
        out = [Account.model_validate_json(r[0]) for r in con.execute("SELECT json FROM accounts")]
    return [a for a in out if (not role or a.role == role) and (not business_id or a.business_id == business_id)]


def account(account_id: str) -> Account:
    with connect() as con:
        row = con.execute("SELECT json FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if not row:
        raise KeyError(account_id)
    return Account.model_validate_json(row[0])


def account_by_email(email: str) -> Account | None:
    with connect() as con:
        row = con.execute("SELECT json FROM accounts WHERE lower(email) = ?", (email.strip().lower(),)).fetchone()
    return Account.model_validate_json(row[0]) if row else None


def save_account(a: Account) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO accounts VALUES (?, ?, ?)", (a.id, a.email.lower(), a.model_dump_json()))


def offer(offer_id: str) -> Offer:
    with connect() as con:
        row = con.execute("SELECT json FROM offers WHERE id = ?", (offer_id,)).fetchone()
    if not row:
        raise KeyError(offer_id)
    return Offer.model_validate_json(row[0])
