"""Shared helpers for the Streamlit screens. All logic lives in core/; this file only presents it."""

from __future__ import annotations

import datetime as dt
import json
import math

import pandas as pd
import pydeck as pdk
import streamlit as st

from core import db
from core.schemas import ConstraintSet
from core.taxonomy import DAYS, TEMPLATES

ROLE_LABELS = {
    "barista": "Barista", "cashier": "Cashier", "shift_lead": "Shift lead", "shift_manager": "Shift manager",
    "drive_thru_order": "Drive-thru", "drive_thru_window": "Drive-thru window", "food_prep": "Food prep",
    "prep_cook": "Prep cook", "line_cook": "Line cook", "dishwasher_busser": "Dishwasher", "host": "Host",
    "server": "Server", "bartender": "Bartender", "to_go": "To-go", "baker": "Baker",
    "pastry_decorator": "Pastry", "stock_associate": "Stock", "sales_associate": "Sales floor",
    "deli_associate": "Deli",
}
SKILL_LABELS = {"keyholder": "Keyholder", "food_handler": "Food handler", "food_safety_manager": "ServSafe manager",
                "alcohol_service": "Alcohol service"}
DAY_LONG = dict(zip(DAYS, ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]))

CSS = """
<style>
  .block-container {padding-top: 2.2rem; max-width: 1400px;}
  .nh-chip {display:inline-block; padding:2px 10px; margin:2px 4px 2px 0; border-radius:999px;
            font-size:0.82rem; background:rgba(99,102,241,.12); color:inherit; border:1px solid rgba(99,102,241,.35);}
  .nh-chip.warn {background:rgba(245,158,11,.12); border-color:rgba(245,158,11,.45);}
  .nh-chip.mp {background:rgba(147,51,234,.12); border-color:rgba(147,51,234,.45);}
  .nh-muted {opacity:.7; font-size:.85rem;}
  .nh-footer {opacity:.6; font-size:.8rem; margin-top:2rem;}
  div[data-testid="stButtonGroup"] > div {flex-wrap: wrap !important; overflow: visible !important;}
  .nh-steps {display:flex; flex-wrap:wrap; align-items:center; gap:6px; margin:-.4rem 0 1rem 0;}
  .nh-step {padding:3px 12px; border-radius:999px; font-size:.85rem; border:1px solid rgba(109,40,217,.35);}
  .nh-step.done {background:#6D28D9; color:white; border-color:#6D28D9;}
  .nh-step.now {border:2px solid #6D28D9; font-weight:600;}
  .nh-step.todo {opacity:.55;}
  .nh-arrow {opacity:.4;}
  .nh-rec {display:inline-block; padding:4px 14px; border-radius:999px; font-weight:700; font-size:.95rem;}
  .nh-rec.take {background:rgba(34,197,94,.18);} .nh-rec.look {background:rgba(245,158,11,.2);}
  .nh-rec.pass {background:rgba(239,68,68,.18);}
</style>
"""


@st.cache_resource
def boot() -> bool:
    db.load_seed()
    return True


def setup_page() -> None:
    boot()
    st.markdown(CSS, unsafe_allow_html=True)


def md(text: str) -> str:
    """Escape $ so Streamlit markdown doesn't render money as math."""
    return str(text).replace("$", "\\$")


def hm(q: int) -> str:
    """26 -> '6:30a', 48 -> '12p' (compact times for the grid)."""
    h, m = divmod(q, 4)
    suffix = "a" if h < 12 or h == 24 else "p"
    h12 = h % 12 or 12
    return f"{h12}{':' + str(m * 15).zfill(2) if m else ''}{suffix}"


def money(cents: int | float) -> str:
    return f"-${-cents / 100:,.2f}" if cents < 0 else f"${cents / 100:,.2f}"


def role(r: str) -> str:
    return ROLE_LABELS.get(r, r.replace("_", " ").capitalize())


def chips(items: list[str], kind: str = "") -> None:
    if items:
        st.markdown(" ".join(f'<span class="nh-chip {kind}">{md(i)}</span>' for i in items), unsafe_allow_html=True)


def footer() -> None:
    st.markdown('<div class="nh-footer">Built with Muse Spark Open · Nearhand, Meta Global AI Developer '
                'Hackathon 2026 · all people and businesses are fictional</div>', unsafe_allow_html=True)


def current_business():
    return db.business(st.session_state.get("business_id", "biz_bean"))


def seed_text(name: str, business_id: str) -> str:
    return json.loads((db.SEED_DIR / name).read_text()).get(business_id, "")


def business_label(b) -> str:
    return f"{b.name} · {TEMPLATES[b.type]['label']}"


# ---------------------------------------------------------------- constraints in plain words

def constraint_label(c, names: dict[str, str]) -> str:
    who = lambda eid: names.get(eid, eid)  # noqa: E731
    t = c.type
    if t == "unavailable":
        when = f" {c.start}-{c.end}" if c.start and c.end else ""
        return f"{who(c.employee_id)} off {', '.join(c.days)}{when}"
    if t == "max_hours":
        target = who(c.employee_id) if c.employee_id else (role(c.role) + "s" if c.role else "Everyone")
        return f"{target} ≤ {c.hours:g}h/week"
    if t == "require_skill":
        days = "every day" if len(c.days) == 7 else ", ".join(c.days)
        where = {"open": "at open", "close": "at close", "all_shifts": "on every shift"}[c.at]
        return f"{SKILL_LABELS.get(c.skill, c.skill)} {where} ({days})"
    if t == "min_rest":
        return f"{c.hours:g}h rest between shifts"
    if t == "fixed_assignment":
        return f"{who(c.employee_id)} works {c.day} {c.start}-{c.end}"
    if t == "prefer":
        verb = "prefers" if c.kind == "prefer" else "avoids"
        return f"{who(c.employee_id)} {verb} {', '.join(c.days)} (noted)"
    if t == "budget_cap":
        return f"Budget ≤ ${c.dollars:,.0f} (noted)"
    return t


# ---------------------------------------------------------------- saved planner state

def load_state(business_id: str) -> dict:
    s = db.schedule(business_id)
    for key in ("staff_result", "final_result"):
        if s.get(key):
            s[key]["assignments"] = [tuple(a) for a in s[key]["assignments"]]
    if s.get("constraints"):
        s["cs"] = ConstraintSet.model_validate(s["constraints"])
    return s


def save_state(business_id: str, s: dict) -> None:
    db.save_schedule(business_id, {k: v for k, v in s.items() if k != "cs"})


# ---------------------------------------------------------------- the schedule grid

MP_SUFFIX = {"accepted": " ✓", "pending": " ⏳"}


def schedule_frame(slots, assignments, gaps, workers, market_status: dict | None = None):
    """One row per person, one column per day, then one row per open shift. Returns a pandas Styler.
    market_status: part-timer id -> "pending" (offer sent) or "accepted" (booked)."""
    market_status = market_status or {}
    by_slot = {s["id"]: s for s in slots}
    names = {w["id"]: w["name"] for w in workers}
    hours = {}
    rows = {}
    for wid, sid in assignments:
        s = by_slot[sid]
        label = names.get(wid, wid) + MP_SUFFIX.get(market_status.get(wid), "")
        rows.setdefault(label, {d: "" for d in DAYS})
        rows[label][s["day"]] = f"{hm(s['start_q'])}–{hm(s['end_q'])} {role(s['role'])}"
        hours[label] = hours.get(label, 0) + (s["end_q"] - s["start_q"]) / 4
    gap_cells = {d: [] for d in DAYS}  # one "Open shift" row per gap, so every gap is visible
    for sid, n in sorted(gaps.items(), key=lambda kv: by_slot[kv[0]]["start_q"]):
        s = by_slot[sid]
        gap_cells[s["day"]] += [f"{hm(s['start_q'])}–{hm(s['end_q'])} {role(s['role'])}"] * n
    df = pd.DataFrame.from_dict(rows, orient="index", columns=DAYS)
    df.insert(0, "Hours", [f"{hours[i]:g}" for i in df.index])
    is_mp = lambda i: i.endswith(tuple(MP_SUFFIX.values()))  # noqa: E731
    staff_rows = sorted([i for i in df.index if not is_mp(i)])
    mp_rows = sorted([i for i in df.index if is_mp(i)])
    df = df.loc[staff_rows + mp_rows]
    for i in range(max((len(v) for v in gap_cells.values()), default=0)):
        label = "⚠ Open shift" + (f" {i + 1}" if i else "")
        df.loc[label] = [""] + [gap_cells[d][i] if i < len(gap_cells[d]) else "" for d in DAYS]

    def color(row):
        if row.name.startswith("⚠ Open shift"):
            return ["" if not v else "background-color: rgba(239,68,68,.18); font-weight: 600" for v in row]
        if row.name.endswith(MP_SUFFIX["accepted"]):
            return ["background-color: rgba(147,51,234,.22); font-weight: 600" if v and c != "Hours" else ""
                    for c, v in row.items()]
        if row.name.endswith(MP_SUFFIX["pending"]):
            return ["background-color: rgba(147,51,234,.08); font-style: italic" if v and c != "Hours" else ""
                    for c, v in row.items()]
        over = row["Hours"] != "" and float(row["Hours"]) > 40
        return [("background-color: rgba(245,158,11,.18)" if over and c == "Hours" else "") for c in row.index]

    df.columns = ["Hours"] + [day_label(d) for d in DAYS]
    return df.style.apply(color, axis=1)


def gap_hours(slots, gaps) -> float:
    by_slot = {s["id"]: s for s in slots}
    return sum(n * (by_slot[sid]["end_q"] - by_slot[sid]["start_q"]) for sid, n in gaps.items()) / 4


def demand_hours(slots) -> float:
    return sum(s["need"] * (s["end_q"] - s["start_q"]) for s in slots) / 4


STATUS_BADGE = {"pending": "⏳ Waiting", "accepted": "✅ Accepted", "declined": "❌ Declined",
                "unavailable": "⚠️ No longer free"}


def progress(steps: list[str], done: int) -> None:
    """A row of step chips: done ones filled, the current one outlined."""
    parts = []
    for i, label in enumerate(steps):
        kind = "done" if i < done else ("now" if i == done else "todo")
        parts.append(f'<span class="nh-step {kind}">{"✓ " if i < done else f"{i + 1} · "}{label}</span>')
    st.markdown('<div class="nh-steps">' + '<span class="nh-arrow">→</span>'.join(parts) + "</div>",
                unsafe_allow_html=True)


# ---------------------------------------------------------------- dates

def week_start(today: dt.date | None = None) -> dt.date:
    """The Monday of the week being planned: next Monday, or today if it is Monday."""
    today = today or dt.date.today()
    return today + dt.timedelta(days=(7 - today.weekday()) % 7)


def day_label(day: str) -> str:
    d = week_start() + dt.timedelta(days=DAYS.index(day))
    return f"{day} {d.month}/{d.day}"


def week_label() -> str:
    a = week_start()
    b = a + dt.timedelta(days=6)
    return f"Week of {a:%b} {a.day}–{b.day}" if a.month == b.month else f"Week of {a:%b} {a.day} – {b:%b} {b.day}"


# ---------------------------------------------------------------- neighborhood map

MAP_COLORS = {"business": [109, 40, 217, 255], "booked": [22, 163, 74, 230], "offered": [245, 158, 11, 230],
              "candidate": [99, 102, 241, 150], "declined": [148, 163, 184, 150]}


def neighborhood_map(business, people: list[dict], height: int = 360) -> None:
    """The business plus nearby people. People are shown at approximate locations only (about 0.5 mi)."""
    from core.marketplace import location
    here = location(business)
    if not here:
        return
    rows = [{"lat": here[0], "lon": here[1], "name": business.name, "info": "Your business",
             "color": MAP_COLORS["business"], "r": 160}]
    for p in people:
        loc = location(p["pt"])
        if not loc:
            continue
        rows.append({"lat": round(loc[0], 2), "lon": round(loc[1], 2), "name": p["pt"].name,
                     "info": p.get("info", ""), "color": MAP_COLORS[p.get("kind", "candidate")],
                     "r": 110 if p.get("kind") in ("booked", "offered") else 80})
    layer = pdk.Layer("ScatterplotLayer", data=rows, get_position="[lon, lat]", get_fill_color="color",
                      get_radius="r", radius_min_pixels=5, radius_max_pixels=14, pickable=True,
                      stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=1)
    # fit everyone on the map, with a little margin, never zooming in past street level
    lats, lons = [r["lat"] for r in rows], [r["lon"] for r in rows]
    span = max(max(lats) - min(lats), (max(lons) - min(lons)) * 0.75, 0.01)
    zoom = max(9.0, min(13.5, math.log2(360 / (span * 2.2)) - 0.8))
    view = pdk.ViewState(latitude=(max(lats) + min(lats)) / 2, longitude=(max(lons) + min(lons)) / 2, zoom=zoom)
    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view, map_style=None,
                             tooltip={"text": "{name}\n{info}"}), height=height)
    legend = {"business": "your business", "booked": "booked", "offered": "offer sent", "candidate": "available"}
    kinds = ["business"] + sorted({p.get("kind", "candidate") for p in people} & set(legend) - {"business"})
    st.markdown(" ".join(
        f'<span class="nh-chip" style="border-color:rgb{tuple(MAP_COLORS[k][:3])}">'
        f'<span style="color:rgb{tuple(MAP_COLORS[k][:3])}">●</span> {legend[k]}</span>' for k in kinds)
        + ' <span class="nh-muted">· people are shown at approximate locations</span>', unsafe_allow_html=True)
