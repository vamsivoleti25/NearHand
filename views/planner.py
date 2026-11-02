"""Planner: rules in plain English -> schedule -> fill gaps with the agent -> send offers -> replace declines."""

import streamlit as st

from core import db, offers, parsers
from core.adapter import build_inputs
from core.agent import BOOKING_FEE_CENTS, FillSession, run_agent
from core.schemas import ConstraintSet
from core.solver import solve_staff_only
import time

from ui.common import (STATUS_BADGE, constraint_label, current_business, demand_hours, footer, gap_hours, legend,
                       load_state, md, money, neighborhood_map, progress, role, save_state, schedule_frame,
                       seed_text, setup_page, week_label)


def step_line(tool: str, args: dict, out: dict) -> str:
    """One line per agent step, in words an owner can follow."""
    if "error" in out:
        return f"⚠️ `{tool}` → {out['error']}"
    if tool == "get_gaps":
        return f"🔎 **Looked at the week**: {len(out['gaps'])} open shifts, {out['gap_hours']:g} hours"
    if tool == "search_marketplace":
        n = len({c['id'] for cs in out["candidates_by_gap"].values() for c in cs})
        empty = [g for g, cs in out["candidates_by_gap"].items() if not cs]
        return (f"📍 **Searched the neighborhood** within {out['max_distance_miles']:g} mi: {n} qualified, free people"
                + (f" · nobody for {', '.join(empty)}" if empty else ""))
    if tool == "solve_with":
        team = len({t["employee_id"] for t in out.get("team_extra_hours", [])})
        return (f"🧮 **Optimizer**: {out['people_booked']} people nearby"
                + (f" and {team} of your team" if team else "")
                + f" cover it for {out['added_cost']} · {len(out['remaining_gaps'])} still open")
    if tool == "what_if":
        ch = out["change"]
        what = f"{ch['extra_hours']:g} more hours" if ch.get("extra_hours") else f"{ch.get('give_back_day')} back"
        return f"🤔 **What if** {ch['employee'].split()[0]} worked {what}? Saves {out['saves']}"
    if tool == "propose_plan":
        return f"✅ **Proposed a plan**: {out['people_booked']} people, adds {out['added_cost']}"
    return f"`{tool}`"


def chosen_constraints(state) -> ConstraintSet:
    cs = state["cs"]
    return ConstraintSet(constraints=[cs.constraints[i] for i in (state.get("kept") or [])])


def run_fill(biz, grid, team, state, pts, replacement: bool) -> None:
    """Run the agent (first round or a replacement round) and keep its plan and trace in the state."""
    sent = db.offers(business_id=biz.id)
    if replacement:
        view = offers.week_view(state["inputs"], state["staff_result"], sent, pts)
        sr, pool, held, covered, no_extra = offers.replacement_inputs(state["staff_result"], view, sent,
                                                                      list(pts.values()), db.bookings(), biz.id)
    else:
        sr, pool, held, covered, no_extra = state["staff_result"], list(pts.values()), db.bookings(), None, set()
    extra = state.get("extra_hours", 8) if state.get("team_first", True) else 0
    session = FillSession(biz, grid, team, chosen_constraints(state), pool, held, staff_result=sr, covered=covered,
                          staff_extra_hours=extra, overtime_ok=state.get("overtime_ok", True), no_extra_for=no_extra)
    trace, clock = [], {"t": time.time()}
    with st.status("Muse Spark Open is working on it…", expanded=True) as status:
        def on_step(tool, args, out):
            now = time.time()
            trace.append(f"{step_line(tool, args, out)} · *{now - clock['t']:.1f}s*")
            clock["t"] = now
            st.markdown(md(trace[-1]))
        plan = run_agent(session, on_step=on_step)
        status.update(label=f"Plan ready · {plan.get('turns') or 'fallback'} steps", state="complete", expanded=False)
    state["plan"] = {k: v for k, v in plan.items() if k != "result"}
    state["plan_result"] = plan["result"]
    state["plan_slots"] = session.slots
    state["plan_gaps"] = list(session.staff_result["gaps"])
    state["trace"] = trace
    save_state(biz.id, state)


def send_offers(biz, state, pts) -> int:
    round_ = 1 + max((o.round for o in db.offers(business_id=biz.id)), default=0)
    made = offers.make_offers(biz, state["plan_result"]["assignments"], state["plan_slots"], pts, round_,
                              message=f"Hi! {biz.name} would love your help this week.",
                              employees={e.id: e for e in db.team(biz.id)}, gaps=state.get("plan_gaps", ()),
                              team_message="We have open shifts this week and wanted to ask you first.")
    for o in made:
        db.save_offer(o)
    state["plan"] = None
    save_state(biz.id, state)
    return len(made)


setup_page()
biz = current_business()
team = db.team(biz.id)
grid = db.grid(biz.id)
state = load_state(biz.id)
names = {e.id: e.name.split()[0] for e in team}
pts = {p.id: p for p in db.part_timers()}
sent = db.offers(business_id=biz.id) if state.get("staff_result") else []

st.title(biz.name)
st.markdown(f'<div class="nh-subtitle">{week_label()}</div>', unsafe_allow_html=True)
if grid is None or not team:
    st.info("Set up your business and team first (Setup and Team pages).")
    st.stop()

view = offers.week_view(state["inputs"], state["staff_result"], sent, pts, BOOKING_FEE_CENTS) \
    if state.get("staff_result") else None
stage = 0 if not state.get("cs") else 1 if view is None else 2 if not sent else 3
if view is not None and sent and not view["gaps"] and all(o.status == "accepted" for o in sent
                                                          if o.status in offers.LIVE):
    stage = 4
progress(["Read rules", "Build schedule", "Fill open shifts", "Offers out", "Fully staffed"], stage)

# ---------------------------------------------------------------- rules (top row)
with st.container(border=True):
    left, right = st.columns([1, 1], gap="large")
    with left:
        st.markdown("**Rules for this week** · type them the way you'd say them")
        rules_text = st.text_area("Rules", value=state.get("rules_text") or seed_text("rules.json", biz.id),
                                  height=90, label_visibility="collapsed")
        st.caption("Mention people with @: " + ", ".join("@" + n for n in sorted(names.values())))
        if st.button("Read my rules", width="stretch"):
            with st.spinner("Muse Spark Open is reading your rules…"):
                try:
                    cs = parsers.parse_rules(rules_text, team, biz)
                except Exception as e:
                    st.error(f"Couldn't read the rules: {e}")
                    st.stop()
            state = {"rules_text": rules_text, "constraints": cs.model_dump()}
            save_state(biz.id, state)
            st.rerun()

    cs = state.get("cs")
    with right:
        if cs is None:
            st.markdown("**Rules I understood**")
            st.caption("Read your rules to see them here as checkable chips.")
        else:
            labels = [constraint_label(c, names) for c in cs.constraints]
            st.markdown("**Rules I understood** · untick any that are wrong")
            keep = st.pills("Rules", options=list(range(len(labels))), format_func=lambda i: labels[i],
                            selection_mode="multi", default=state.get("kept", list(range(len(labels)))),
                            label_visibility="collapsed")
            for q in cs.ambiguities:
                st.warning(md(q), icon="❓")
            if st.button("Build schedule", type="primary", width="stretch"):
                chosen = ConstraintSet(constraints=[cs.constraints[i] for i in (keep or [])])
                with st.spinner("Optimizing the week with OR-Tools CP-SAT…"):
                    inp = build_inputs(biz, grid, team, chosen)
                    result = solve_staff_only(inp["workers"], inp["slots"], min_rest_q=inp["min_rest_q"],
                                              fixed=inp["fixed"])
                for o in db.offers(business_id=biz.id, status="pending"):  # a new schedule withdraws open offers
                    db.save_offer(o.model_copy(update={"status": "unavailable"}))
                state.update({"kept": keep, "inputs": inp, "staff_result": result, "plan": None, "trace": []})
                save_state(biz.id, state)
                st.rerun()
            if state.get("inputs", {}).get("not_enforced"):
                st.caption("Noted but not enforced: " + ", ".join(state["inputs"]["not_enforced"]))

# ---------------------------------------------------------------- the week
if view is None:
    st.info("Read your rules, then build the schedule.")
    footer()
    st.stop()

slots = state["inputs"]["slots"]
gh, need = gap_hours(slots, view["gaps"]), demand_hours(slots)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Week labor cost", money(view["bill"]["total_cents"]),
          help="Staff pay plus any offered or booked part-timer shifts and booking fees.")
c2.metric("Overtime", money(view["bill"]["overtime_cents"]))
c3.metric("Coverage", f"{100 * (1 - gh / need):.1f}%" if need else "–")
c4.metric("Open shifts", f"{len(view['gaps'])} · {gh:g} h" if gh else "None")

# ---------------------------------------------------------------- before / after
plan_now = state.get("plan")
projected = view
if plan_now and state.get("plan_result"):  # show what the proposal would do, before it is sent
    draft = offers.make_offers(biz, state["plan_result"]["assignments"], state["plan_slots"], pts,
                               employees={e.id: e for e in team}, gaps=state.get("plan_gaps", ()))
    projected = offers.week_view(state["inputs"], state["staff_result"], sent + draft, pts, BOOKING_FEE_CENTS)
base = state["staff_result"]
if projected is not view or sent:
    b_gaps, b_h = len(base["gaps"]), gap_hours(slots, base["gaps"])
    a_gaps, a_h = len(projected["gaps"]), gap_hours(slots, projected["gaps"])
    b_cost, a_cost = base["bill"]["total_cents"], projected["bill"]["total_cents"]
    booked = sum(1 for s in projected["status"].values() if s == "accepted")
    with st.container(border=True, key="nh-impact"):
        st.markdown("**Impact** · " + ("if you send these offers" if projected is not view
                                         else f"{booked} booked, {len(projected['status']) - booked} waiting"))
        i1, i2, i3, i4 = st.columns(4)
        i1.metric("Open shifts", a_gaps, a_gaps - b_gaps, delta_color="inverse",
                  help="Compared with your own staff only.")
        i2.metric("Hours covered", f"{b_h - a_h:g} of {b_h:g}")
        i3.metric("Coverage", f"{100 * (1 - a_h / need):.1f}%", f"{100 * (b_h - a_h) / need:+.1f} pts")
        i4.metric("Added to the week", money(a_cost - b_cost), f"{100 * (a_cost - b_cost) / b_cost:+.1f}% of labor",
                  delta_color="off")

legend([("✓", "booked"), ("⏳", "offer sent"), ("#F59E0B", "extra hours for your team"),
        ("#EF4444", "open shift")])
frame = schedule_frame(slots, view["assignments"], view["gaps"], view["workers"], view["status"],
                       extra_cells=view.get("extra_cells"))
st.dataframe(frame, width="stretch", height=35 * (len(frame.data) + 1) + 3)
st.caption("Purple rows are marketplace part-timers: ⏳ offer sent, ✓ booked. Amber cells are open shifts "
           "offered to your own team as extra hours. Red rows are shifts nobody has covered yet.")

# ---------------------------------------------------------------- offers already out
if sent:
    st.subheader("Offers")
    rows = []
    team_names = {e.id: e.name for e in team}
    for o in sorted(sent, key=lambda o: (o.round, o.created_ts)):
        rows.append({"Round": o.round,
                     "Who": team_names.get(o.employee_id, o.employee_id) + " (your team)" if o.kind == "extra_hours"
                     else pts[o.part_timer_id].name,
                     "Kind": "Extra hours" if o.kind == "extra_hours" else "Marketplace",
                     "Shifts": ", ".join(f"{s.day} {s.start}-{s.end}" for s in o.shifts),
                     "Role": ", ".join(sorted({role(s.role) for s in o.shifts})), "Rate": money(o.rate_cents) + "/h",
                     "Status": STATUS_BADGE[o.status]})
    st.dataframe(rows, hide_index=True, width="stretch")
    kind = {"accepted": "booked", "pending": "offered", "declined": "declined", "unavailable": "declined"}
    latest = {}
    for o in sorted(sent, key=lambda o: o.created_ts):
        if o.kind == "shift":
            latest[o.part_timer_id] = o
    with st.expander("Map: who's coming in", expanded=False):
        neighborhood_map(biz, [{"pt": pts[i], "kind": kind[o.status],
                                "info": f"{STATUS_BADGE[o.status]} · {money(o.rate_cents)}/h"}
                               for i, o in latest.items()], height=320)
    waiting = sum(o.status == "pending" for o in sent)
    if waiting:
        st.caption(f"{waiting} waiting for an answer. Use **Switch demo account** in the sidebar to answer as them.")

reopened = bool(view["gaps"] and sent)
if view["gaps"] and not state.get("plan"):
    st.subheader("Find replacements" if reopened else "Fill the open shifts")
    if reopened:
        st.caption("Some offers were declined. The agent fills only the reopened shifts and skips anyone who said no.")
    c1, c2, c3 = st.columns([1.3, 1, 1], vertical_alignment="center")
    team_first = c1.toggle("Ask my team first", value=state.get("team_first", True),
                           help="Offer open shifts to your own staff as extra hours when that's cheaper than a "
                                "part-timer plus the booking fee, or when nobody nearby qualifies. They can say no.")
    extra_h = c2.number_input("Extra hours each, at most", min_value=2, max_value=16, step=2,
                              value=int(state.get("extra_hours", 8)), disabled=not team_first)
    ot_ok = c3.checkbox("Allow overtime past 40 h", value=state.get("overtime_ok", True), disabled=not team_first,
                        help="Hours past 40 in a week are paid at the overtime rate (1.5x)")
    if (team_first, extra_h, ot_ok) != (state.get("team_first", True), state.get("extra_hours", 8),
                                        state.get("overtime_ok", True)):
        state.update(team_first=team_first, extra_hours=extra_h, overtime_ok=ot_ok)
        save_state(biz.id, state)
    if st.button("Find replacements with AI" if reopened else "Fill gaps with AI", type="primary"):
        run_fill(biz, grid, team, state, pts, replacement=reopened)
        st.rerun()

# ---------------------------------------------------------------- the agent's proposal
plan = state.get("plan")
if plan:
    with st.container(border=True, key="nh-plan"):
        st.markdown(md(f"**Proposed plan** · adds **{plan['added_cost']}** "
                       f"({plan['added_wages']} wages + {plan['booking_fees']} booking fees) · "
                       f"week total {plan['week_total']}"))
        st.markdown(md(plan["explanation"]))
        if plan.get("alternative"):
            st.caption(md("Alternative: " + plan["alternative"]))
        if plan.get("grounded"):
            st.markdown('<span class="nh-badge-ok">✓ Every number above was checked against the optimizer\'s '
                        "results.</span>", unsafe_allow_html=True)
        if plan.get("team_extra_hours"):
            st.markdown("**Your team first** · extra hours, asked before anyone nearby")
            shifts = {}
            for t in plan.get("team_extra_shifts", []):
                shifts.setdefault(t["employee_id"], []).append(f"{t['day']} {t['time']} {role(t['role'])}")
            st.dataframe([{"Who": t["name"], "Shifts": ", ".join(shifts.get(t["employee_id"], [])),
                           "Week": f"{t['hours_before']:g} → {t['hours_after']:g} h",
                           "Overtime": f"{t['overtime_hours']:g} h" if t["overtime_hours"] else "–",
                           "Extra pay": t["extra_pay"]} for t in plan["team_extra_hours"]],
                         hide_index=True, width="stretch")
        if plan["bookings"]:
            if plan.get("team_extra_hours"):
                st.markdown("**From the neighborhood**")
            rows = [{"Day": b["day"], "Time": b["time"], "Role": role(b["role"]), "Who": b["name"],
                     "Rate": b["rate"] + "/h", "Distance": f"{b['distance_miles']} mi"} for b in plan["bookings"]]
            st.dataframe(rows, hide_index=True, width="stretch")
            who = {}
            for b in plan["bookings"]:
                who.setdefault(b["part_timer_id"], []).append(f"{b['day']} {b['time']}")
            neighborhood_map(biz, [{"pt": pts[i], "kind": "offered", "info": ", ".join(t)} for i, t in who.items()],
                             height=300)
        if plan.get("note"):
            st.caption(plan["note"])
        if state.get("trace"):
            with st.expander("How the agent got here"):
                with st.container(key="nh-timeline"):
                    for line in state["trace"]:
                        st.markdown(md(line))
        a, b = st.columns(2)
        if a.button("Send offers", type="primary", width="stretch"):
            n = send_offers(biz, state, pts)
            st.toast(f"Sent {n} offer{'s' if n != 1 else ''}.", icon="📨")
            st.rerun()
        if b.button("Discard", width="stretch"):
            state["plan"] = None
            save_state(biz.id, state)
            st.rerun()

if stage == 4:
    st.success("Fully staffed. Every open shift is booked, and those hours are blocked for other businesses.")
footer()
