"""SQLite storage. Each record is stored as JSON next to its id; Pydantic models give it shape.

The database rebuilds itself from data/seed/ when empty, and reset_demo() puts it back
to the seed state before every recording.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core import llm
import time
import uuid

from core.schemas import Account, Booking, Business, Employee, Event, Message, Offer, PartTimer, StaffingGrid

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
    "messages": "id TEXT PRIMARY KEY, offer_id TEXT, business_id TEXT, worker_id TEXT, ts REAL, json TEXT",
    "events": "id TEXT PRIMARY KEY, business_id TEXT, worker_id TEXT, ts REAL, json TEXT",
}

# Fictional owners for the two demo businesses (sign-in shows them as demo accounts).
OWNERS = {"biz_bean": "Dana Okafor", "biz_maple": "Marcus Bell"}
DOMAINS = {"biz_bean": "beanandco.example", "biz_maple": "mapletable.example"}


def connect() -> sqlite3.Connection:
    path = llm.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
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
    """Save an offer; a new offer or a change of status is also written to the activity feed."""
    who = o.employee_id if o.kind == "extra_hours" else o.part_timer_id   # the column holds the recipient
    with connect() as con:
        row = con.execute("SELECT status FROM offers WHERE id = ?", (o.id,)).fetchone()
        con.execute("INSERT OR REPLACE INTO offers VALUES (?, ?, ?, ?, ?)",
                    (o.id, o.business_id, who, o.status, o.model_dump_json()))
    before = row[0] if row else None
    if before != o.status:
        _log_offer(o, before)


def worker_name(o: Offer) -> str:
    if o.kind == "extra_hours":
        return next((e.name for e in team(o.business_id) if e.id == o.employee_id), o.employee_id)
    try:
        return part_timer(o.part_timer_id).name
    except KeyError:
        return o.part_timer_id


def shifts_text(o: Offer) -> str:
    def t(hhmm: str) -> str:
        h, m = int(hhmm[:2]), hhmm[3:]
        return f"{h % 12 or 12}{'' if m == '00' else ':' + m}{'a' if h < 12 else 'p'}"
    return ", ".join(f"{s.day} {t(s.start)}–{t(s.end)}" for s in o.shifts)


def _log_offer(o: Offer, before: str | None) -> None:
    name, what = worker_name(o), shifts_text(o)
    extra = o.kind == "extra_hours"
    text = {
        "pending": (f"Asked {name} (your team) to pick up extra hours: {what}" if extra
                    else f"Offer sent to {name}: {what}"),
        "accepted": f"{name} accepted {'extra hours ' if extra else ''}{what}",
        "declined": f"{name} declined {what}",
        "unavailable": (f"Offer to {name} withdrawn: the schedule was rebuilt" if before == "pending"
                        and o.responded_ts is None else f"{name} is no longer free for {what}"),
    }[o.status]
    kind = "offer_sent" if o.status == "pending" else o.status
    log_event(o.business_id, kind, text, worker_id=o.employee_id or o.part_timer_id, offer_id=o.id)


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


# ---------------------------------------------------------------- activity feed

def log_event(business_id: str, kind: str, text: str, worker_id: str = "", offer_id: str = "") -> Event:
    e = Event(id=f"ev_{uuid.uuid4().hex[:10]}", business_id=business_id, ts=time.time(), kind=kind, text=text,
              worker_id=worker_id, offer_id=offer_id)
    with connect() as con:
        con.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?)",
                    (e.id, e.business_id, e.worker_id, e.ts, e.model_dump_json()))
    return e


def events(business_id: str | None = None, worker_id: str | None = None, limit: int = 30) -> list[Event]:
    """Newest first."""
    q, args = "SELECT json FROM events WHERE 1=1", []
    for col, val in (("business_id", business_id), ("worker_id", worker_id)):
        if val:
            q, args = q + f" AND {col} = ?", args + [val]
    with connect() as con:
        rows = con.execute(q + " ORDER BY ts DESC LIMIT ?", args + [limit]).fetchall()
    return [Event.model_validate_json(r[0]) for r in rows]


# ---------------------------------------------------------------- chat on offers

def send_message(o: Offer, from_business: bool, sender: str, text: str) -> Message:
    m = Message(id=f"msg_{uuid.uuid4().hex[:10]}", offer_id=o.id, business_id=o.business_id,
                worker_id=o.employee_id or o.part_timer_id, from_business=from_business, sender=sender,
                text=text.strip()[:1000], ts=time.time())
    with connect() as con:
        con.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                    (m.id, m.offer_id, m.business_id, m.worker_id, m.ts, m.model_dump_json()))
    short = m.text if len(m.text) <= 80 else m.text[:77] + "…"
    log_event(o.business_id, "message", f"{sender}: “{short}”", worker_id=m.worker_id, offer_id=o.id)
    return m


def messages(offer_id: str) -> list[Message]:
    with connect() as con:
        rows = con.execute("SELECT json FROM messages WHERE offer_id = ? ORDER BY ts", (offer_id,)).fetchall()
    return [Message.model_validate_json(r[0]) for r in rows]


def unread(for_business: bool, business_id: str | None = None, worker_id: str | None = None) -> dict[str, int]:
    """Unread messages per offer. for_business=True: messages workers wrote that the business hasn't read;
    False: messages the business wrote that the worker hasn't read. Filter by business and/or worker."""
    q, args = "SELECT json FROM messages WHERE 1=1", []
    for col, val in (("business_id", business_id), ("worker_id", worker_id)):
        if val:
            q, args = q + f" AND {col} = ?", args + [val]
    with connect() as con:
        rows = [Message.model_validate_json(r[0]) for r in con.execute(q, args)]
    out: dict[str, int] = {}
    for m in rows:
        if not m.read and m.from_business != for_business:
            out[m.offer_id] = out.get(m.offer_id, 0) + 1
    return out


def mark_read(offer_id: str, by_business: bool) -> None:
    """Mark the other side's messages on this offer as read."""
    with connect() as con:
        rows = con.execute("SELECT id, json FROM messages WHERE offer_id = ?", (offer_id,)).fetchall()
        for mid, js in rows:
            m = Message.model_validate_json(js)
            if not m.read and m.from_business != by_business:
                con.execute("UPDATE messages SET json = ? WHERE id = ?",
                            (m.model_copy(update={"read": True}).model_dump_json(), mid))
