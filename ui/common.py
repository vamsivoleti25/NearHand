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
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Plus+Jakarta+Sans:wght@600;700;800&display=swap');
  .block-container {padding-top: 2.2rem; max-width: 1400px;}
  html, body, p, div, span, li, caption {font-family: 'Inter', sans-serif;}
  h1, h2, h3, h4, h5, h6 {font-family: 'Plus Jakarta Sans', 'Inter', sans-serif;}
  div[data-testid="stMetricValue"] {font-variant-numeric: tabular-nums;}
  /* cards: white, 12px radius, 1px slate-200 border, one soft shadow */
  div[data-testid="stVerticalBlockBorderWrapper"] {border-color:#E2E8F0; border-radius:12px;
    box-shadow:0 1px 3px rgba(15,23,42,.06);}
  /* metric cards */
  div[data-testid="stMetric"] {background:#FFFFFF; border:1px solid #E2E8F0; border-radius:12px;
    padding:.7rem 1rem; box-shadow:0 1px 3px rgba(15,23,42,.06);}
  div[data-testid="stMetric"] label {color:#475569 !important; font-size:.8rem;}
  div[data-testid="stMetricValue"] {font-size:1.5rem; font-variant-numeric: tabular-nums;}
  .nh-chip {display:inline-block; padding:2px 10px; margin:2px 4px 2px 0; border-radius:999px;
            font-size:0.82rem; background:#F5F3FF; color:#0F172A; border:1px solid #E2E8F0;}
  .nh-chip.warn {background:#FFFFFF; border-color:#F59E0B; color:#0F172A;}
  .nh-chip.mp {background:#F5F3FF; border-color:rgba(109,40,217,.45);}
  .nh-pill, .nh-rate {display:inline-block; padding:2px 12px; border-radius:999px; font-size:.82rem;
    font-weight:600; background:#F5F3FF; border:1px solid rgba(109,40,217,.4); color:#0F172A;
    white-space:nowrap;}
  .nh-muted {opacity:.7; font-size:.85rem; color:#475569;}
  .nh-footer {opacity:.6; font-size:.8rem; margin-top:2rem;}
  div[data-testid="stButtonGroup"] > div {flex-wrap: wrap !important; overflow: visible !important;}
  .nh-steps {display:flex; flex-wrap:wrap; align-items:center; gap:6px; margin:-.4rem 0 1rem 0;}
  .nh-step {padding:3px 12px; border-radius:999px; font-size:.85rem; border:1px solid rgba(109,40,217,.35);}
  .nh-step.done {background:#6D28D9; color:white; border-color:#6D28D9;}
  .nh-step.now {border:2px solid #6D28D9; font-weight:600;}
  .nh-step.todo {opacity:.55;}
  .nh-arrow {opacity:.4;}
  .nh-rec {display:inline-block; padding:4px 14px; border-radius:999px; font-weight:700; font-size:.95rem;}
  .nh-rec.take {background:rgba(22,163,74,.16);} .nh-rec.look {background:rgba(245,158,11,.2);}
  .nh-rec.pass {background:rgba(239,68,68,.16);}
  .nh-subtitle {color:#475569; font-size:1.05rem; margin:-.6rem 0 1rem 0;}
  .nh-legend {display:flex; flex-wrap:wrap; gap:4px 14px; align-items:center; color:#475569;
    font-size:.85rem; margin:.2rem 0 .6rem 0;}
  .nh-legend .dot {display:inline-block; width:10px; height:10px; border-radius:999px; margin-right:5px;}
  .nh-badge-ok {display:inline-block; padding:2px 10px; border-radius:999px; font-size:.78rem; font-weight:600;
    background:rgba(22,163,74,.14); color:#15803D; border:1px solid rgba(22,163,74,.4);}
  .nh-avatar {display:inline-flex; align-items:center; justify-content:center; width:34px; height:34px;
    border-radius:999px; color:#FFFFFF; font-weight:700; font-size:.85rem; margin-right:8px;
    vertical-align:middle; flex:none;}
  /* keyed containers: st.container(key="nh-...") gets the class st-key-nh-... */
  .st-key-nh-impact {background:#F5F3FF; border-left:4px solid #6D28D9 !important;}
  .st-key-nh-plan {background:#FFFFFF; border-left:4px solid #6D28D9 !important;}
  div[class*="st-key-nh-offer-"] {max-width:560px; margin-left:auto; margin-right:auto; background:#FFFFFF;}
  /* agent trace as a timeline inside the expander */
  .st-key-nh-timeline {border-left:2px solid #E2E8F0; margin:.4rem 0 .4rem 6px; padding-left:18px; gap:.5rem;}
  .st-key-nh-timeline [data-testid="stMarkdown"] {position:relative; font-size:.9rem;}
  .st-key-nh-timeline [data-testid="stMarkdown"]::before {content:""; position:absolute; left:-24px; top:7px;
    width:10px; height:10px; border-radius:999px; background:#6D28D9;}
  /* home page */
  .st-key-nh-home-hero, .st-key-nh-home-numbers, .st-key-nh-home-doors, .st-key-nh-home-steps,
  .st-key-nh-home-under, .st-key-nh-home-close {max-width:1100px; margin-left:auto; margin-right:auto;}
  .nh-home-eyebrow {color:#6D28D9; font-weight:700; font-size:.85rem; margin-bottom:.4rem;}
  .nh-home-h1 {font-family:'Plus Jakarta Sans','Inter',sans-serif; font-weight:700; font-size:3rem;
    line-height:1.08; letter-spacing:-0.02em; color:#0F172A; margin:.2rem 0 .8rem 0;}
  @media (max-width:640px) { .nh-home-h1 {font-size:2rem;} }
  .nh-home-sub {color:#475569; font-size:1.08rem; line-height:1.55; max-width:38rem; margin-bottom:1.2rem;}
  .nh-home-section {margin:3.5rem 0;}
  .nh-home-caption {color:#475569; font-size:.85rem; margin-top:.4rem;}
  .nh-home-door-img img {border-radius:12px 12px 0 0;}
  .nh-home-stepnum {display:inline-flex; align-items:center; justify-content:center; width:32px; height:32px;
    border-radius:999px; background:#6D28D9; color:#FFFFFF; font-weight:700; font-size:.95rem; margin-bottom:.5rem;}
  .nh-home-steptitle {font-family:'Plus Jakarta Sans','Inter',sans-serif; font-weight:700; color:#0F172A;
    margin-bottom:.25rem;}
  .nh-home-under {color:#475569; font-size:.95rem; line-height:1.6;}

  /* sign-in: one centered card, like a marketplace's log-in page */
  .st-key-nh-auth {max-width:520px; margin:0 auto; background:#FFFFFF; border:1px solid #E2E8F0;
    border-radius:12px; padding:1.6rem 1.6rem 1.2rem 1.6rem;}
  .nh-auth-title {font-family:"Plus Jakarta Sans",sans-serif; font-weight:700; font-size:1.6rem; margin:.4rem 0 .6rem 0;}
  .st-key-nh-who {background:#FFFFFF;}
  .st-key-nh-auth [data-testid="stTextInputRootElement"], .st-key-nh-auth [data-testid="stTextAreaRootElement"],
  .st-key-nh-auth [data-baseweb="select"], .st-key-nh-auth [data-baseweb="select"] > div,
  .st-key-nh-auth [data-testid="stNumberInputContainer"] {
    background:#F8FAFC !important; border:1px solid #E2E8F0 !important;}
  .st-key-nh-auth input, .st-key-nh-auth textarea {background:transparent !important;}
  /* Rows of cards share one height, with their last element (usually the button) on the bottom edge.
     Wrap the columns in st.container(key="nh-cards-...") and give each card key="nh-card-...". */
  [class*="st-key-nh-cards-"] [data-testid="stHorizontalBlock"] {align-items:stretch;}
  [class*="st-key-nh-cards-"] [data-testid="stColumn"] > [data-testid="stVerticalBlock"] {height:100%;}
  [data-testid="stLayoutWrapper"]:has(> [class*="st-key-nh-card-"]) {flex:1 1 auto; display:flex; flex-direction:column;}
  [class*="st-key-nh-card-"] {flex:1 1 auto; background:#FFFFFF;}
  [class*="st-key-nh-card-"] > :last-child {margin-top:auto;}
  /* metric tiles in a row share one height too */
  [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) {align-items:stretch;}
  [data-testid="stColumn"]:has([data-testid="stMetric"]) > [data-testid="stVerticalBlock"] {height:100%;}
  [data-testid="stElementContainer"]:has(> [data-testid="stMetric"]) {flex:1 1 auto;}
  div[data-testid="stMetric"] {height:100%; box-sizing:border-box;}
  div[data-testid="stMetric"] label p, div[data-testid="stMetric"] label div {white-space:normal !important;
    overflow:visible !important; text-overflow:clip !important;}
  div[data-testid="stMetric"] {display:flex; flex-direction:column;}
  div[data-testid="stMetric"] > div {flex:1 1 auto; display:flex; flex-direction:column;}
  div[data-testid="stMetric"] [data-testid="stMetricValue"] {margin-top:auto;}   /* numbers line up across a row */
  /* ---- site look: white pages, top header, big type, generous spacing ---- */
  /* no sidebar content: on a desktop the links sit in the top bar; on a phone they fold into its menu */
  .block-container, [data-testid="stMainBlockContainer"] {max-width:1200px; padding:5.5rem 2rem 4rem 2rem;}
  header[data-testid="stHeader"] {background:rgba(255,255,255,.96); border-bottom:1px solid #EEF0F3;
    backdrop-filter:saturate(180%) blur(8px); height:4.25rem;}
  h1 {font-size:2.6rem !important; font-weight:800 !important; letter-spacing:-.025em; line-height:1.12 !important;}
  h2 {font-size:1.9rem !important; font-weight:800 !important; letter-spacing:-.02em;}
  h3 {font-size:1.35rem !important; font-weight:700 !important; letter-spacing:-.01em;}
  .nh-subtitle {font-size:1.15rem !important;}
  p, li {line-height:1.6;}
  /* buttons: taller, bolder, pill-shaped */
  .stButton > button, .stFormSubmitButton > button, [data-testid="stPopover"] button {min-height:2.75rem;
    border-radius:999px !important; font-weight:600 !important; padding-left:1.25rem; padding-right:1.25rem;}
  [data-testid="stImage"] img {border-radius:16px;}
  /* the account controls sit in the header's right corner */
  .st-key-nh-topbar {position:fixed; top:.7rem; right:1.5rem; z-index:999991; width:auto !important;
    background:transparent;}
  .st-key-nh-topbar .stButton > button, .st-key-nh-topbar [data-testid="stPopover"] button {min-height:2.5rem;}
  @media (max-width:640px) {
    .block-container, [data-testid="stMainBlockContainer"] {padding:5rem 1rem 3rem 1rem;}
    h1 {font-size:2rem !important;}
    .st-key-nh-topbar {right:.5rem;}
  }
  /* home: grey bands behind the numbers and the steps; bigger hero */
  .st-key-nh-home-numbers, .st-key-nh-home-steps {background:#F8FAFC; border-radius:20px; padding:2rem 2rem 1.5rem 2rem;}
  .nh-home-h1 {font-size:3.6rem !important; font-weight:800 !important; letter-spacing:-.03em; line-height:1.05 !important;}
  .nh-home-sub {font-size:1.2rem !important;}
  @media (max-width:640px) { .nh-home-h1 {font-size:2.3rem !important;} }
  .nh-home-section {margin:2.75rem 0 !important;}
  /* wordmark next to the logo in the header */
  :is(div, a, button):has(> img[data-testid="stHeaderLogo"]) {display:flex; align-items:center; gap:.55rem;
    width:auto !important; overflow:visible !important;}
  :is(div, a, button):has(> img[data-testid="stHeaderLogo"])::after {content:"Nearhand";
    font-family:'Plus Jakarta Sans','Inter',sans-serif; font-weight:800; font-size:1.3rem; letter-spacing:-.02em;
    color:#0F172A; white-space:nowrap;}
  div:has(> [data-testid="stLogoLink"]) {width:auto !important; margin-right:1rem;}
  /* home sections are spaced by margins, not by empty spacer elements */
  [data-testid="stElementContainer"]:has(> div > div > .nh-home-section),
  [data-testid="stElementContainer"]:has(.nh-home-section:empty) {display:none;}
  .st-key-nh-home-numbers, .st-key-nh-home-doors, .st-key-nh-home-steps, .st-key-nh-home-under,
  .st-key-nh-home-close {margin-top:3.5rem;}
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


_AVATAR_COLORS = ["#6D28D9", "#0E7490", "#15803D", "#B45309", "#BE123C", "#475569"]


def avatar(name: str) -> str:
    """Initials avatar circle; colour is picked from the name so it is stable. Returns html."""
    parts = str(name).split()
    initials = "".join(p[0] for p in parts[:2]).upper() or "?"
    color = _AVATAR_COLORS[sum(ord(c) for c in str(name)) % len(_AVATAR_COLORS)]
    return f'<span class="nh-avatar" style="background:{color}">{initials}</span>'


def legend(items: list) -> None:
    """One-line legend row. Each item is a (swatch, label) tuple: a '#...' color becomes a dot,
    anything else is shown as a symbol. A plain string item is shown as-is."""
    bits = []
    for it in items:
        if isinstance(it, str):
            bits.append(md(it))
            continue
        swatch, label = it
        if str(swatch).startswith("#"):
            bits.append(f'<span><span class="dot" style="background:{swatch}"></span>{md(label)}</span>')
        else:
            bits.append(f"<span>{swatch} {md(label)}</span>")
    if bits:
        st.markdown('<div class="nh-legend">' + " · ".join(bits) + "</div>", unsafe_allow_html=True)


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


def schedule_frame(slots, assignments, gaps, workers, market_status: dict | None = None,
                   extra_cells: dict | None = None):
    """One row per person, one column per day, then one row per open shift. Returns a pandas Styler.
    market_status: part-timer id -> "pending" (offer sent) or "accepted" (booked).
    extra_cells: (employee id, slot id) -> "pending"/"accepted" for open shifts offered to the team."""
    market_status = market_status or {}
    extra_cells = extra_cells or {}
    amber: dict[tuple[str, str], str] = {}
    by_slot = {s["id"]: s for s in slots}
    names = {w["id"]: w["name"] for w in workers}
    hours = {}
    rows = {}
    for wid, sid in assignments:
        s = by_slot[sid]
        label = names.get(wid, wid) + MP_SUFFIX.get(market_status.get(wid), "")
        rows.setdefault(label, {d: "" for d in DAYS})
        rows[label][s["day"]] = f"{hm(s['start_q'])}–{hm(s['end_q'])} {role(s['role'])}"
        if (wid, sid) in extra_cells:
            rows[label][s["day"]] += " · extra" + MP_SUFFIX[extra_cells[(wid, sid)]]
            amber[(label, day_label(s["day"]))] = extra_cells[(wid, sid)]   # columns get dated labels below
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
        return [("background-color: rgba(245,158,11,.18)" if over and c == "Hours" else
                 "background-color: rgba(245,158,11,.22); font-weight: 600" if amber.get((row.name, c)) == "accepted" else
                 "background-color: rgba(245,158,11,.12); font-style: italic" if amber.get((row.name, c)) else "")
                for c in row.index]

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
