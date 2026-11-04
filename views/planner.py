"""Planner: rules in plain English -> schedule -> fill gaps with the agent -> send offers -> replace declines."""

import datetime
import html
import re

import streamlit as st

from core import changes, db, offers, parsers
from core.adapter import build_inputs
from core.agent import BOOKING_FEE_CENTS, FillSession, run_agent
from core.schemas import ConstraintSet
from core.solver import solve_staff_only
from core.taxonomy import DAYS
import time

from ui import session
from ui.chat import badge, feed, thread
from ui.common import (STATUS_BADGE, constraint_label, current_business, demand_hours, footer, gap_hours, legend,
                       load_state, md, money, neighborhood_map, progress, role, save_state, schedule_frame,
                       seed_text, setup_page, week_label)


def people(n: int) -> str:
    return f"{n} {'person' if n == 1 else 'people'}"


def step_line(tool: str, args: dict, out: dict) -> str:
    """One line per agent step, in words an owner can follow."""
    if "error" in out:
        return f"⚠️ `{tool}` → {out['error']}"
    if tool == "get_gaps":
        g = len(out["gaps"])
        return f"🔎 **Looked at the week**: {g} open shift{'s' if g != 1 else ''}, {out['gap_hours']:g} hours"
    if tool == "search_marketplace":
        n = len({c['id'] for cs in out["candidates_by_gap"].values() for c in cs})
        empty = [g for g, cs in out["candidates_by_gap"].items() if not cs]
        return (f"📍 **Searched the neighborhood** within {out['max_distance_miles']:g} mi: {n} qualified and free"
                + (f" · nobody for {', '.join(empty)}" if empty else ""))
    if tool == "solve_with":
        team = len({t["employee_id"] for t in out.get("team_extra_hours", [])})
        n = out["people_booked"]
        left = len(out["remaining_gaps"])
        return (f"🧮 **Optimizer**: {people(n)} nearby"
                + (f" and {team} of your team" if team else "")
                + f" cover it for {out['added_cost']} · " + (f"{left} still open" if left else "nothing left open"))
    if tool == "what_if":
        ch = out["change"]
        first = ch["employee"].split()[0]
        what = f"+{ch['extra_hours']:g} h" if ch.get("extra_hours") else f"{ch.get('give_back_day')} back"
        saves = out["saves"]
        if saves.startswith("-"):
            return f"🤔 **Tried** {first} {what}: would cost {saves[1:]} more, so skipped"
        if saves in ("$0.00", "0"):
            return f"🤔 **Tried** {first} {what}: no cheaper, so skipped"
        return f"🤔 **Tried** {first} {what}: saves {saves}"
    if tool == "propose_plan":
        team = len(out.get("team_extra_hours", []))
        who = " and ".join(x for x in (f"{people(out['people_booked'])} nearby" if out["people_booked"] else "",
                                       f"{team} of your team" if team else "") if x) or "nobody"
        return f"✅ **Proposed a plan**: {who}, adds {out['added_cost']}"
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
    changed_by = set(state.get("changed_by", []))   # asked for shorter hours: don't offer them what they gave up
    pool = [p for p in pool if p.id not in changed_by]
    no_extra = set(no_extra) | changed_by
    extra = state.get("extra_hours", 8) if state.get("team_first", True) else 0
    session = FillSession(biz, grid, team, chosen_constraints(state), pool, held, staff_result=sr, covered=covered,
                          staff_extra_hours=extra, overtime_ok=state.get("overtime_ok", True), no_extra_for=no_extra,
                          slots=state["inputs"]["slots"], out_sick=[tuple(x) for x in state.get("out_sick", [])])
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
    if plan.get("bookings") or plan.get("team_extra_hours"):   # an empty plan is reported by the caller
        db.log_event(biz.id, "plan_proposed",
                     f"{'Replacement plan' if replacement else 'AI plan'}: covers {plan.get('covered_gap_hours', 0):g} of "
                     f"{plan.get('gap_hours_total', 0):g} open hours, adds {plan['added_cost']}")


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
team_names = {e.id: e.name for e in team}
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
progress(["Simplify rules", "Build schedule", "Fill open shifts", "Offers out", "Fully staffed"], stage)

unread = db.unread(True, business_id=biz.id)          # messages from people, not yet read, per offer
n_unread = sum(unread.values())
waiting = sum(o.status == "pending" for o in sent)
reopened = bool(view and view["gaps"] and sent)

# ---------------------------------------------------------------- auto-replace declines
# When someone says no, the agent doesn't wait for the owner: it fills only what reopened, skips anyone who said
# no, sends the new offers, and logs it. (Offers still need a yes; nothing is booked automatically.)
declined = [o for o in sent if o.status == "declined" and o.id not in state.get("auto_done", [])]
if state.get("auto_backfill", True) and reopened and declined and not state.get("plan"):
    run_fill(biz, grid, team, state, pts, replacement=True)
    state["auto_done"] = state.get("auto_done", []) + [o.id for o in declined]
    plan = state.get("plan") or {}
    who = ", ".join(sorted({db.worker_name(o).split()[0] for o in declined}))
    if plan.get("bookings") or plan.get("team_extra_hours"):
        n = send_offers(biz, state, pts)
        db.log_event(biz.id, "auto_replaced", f"{who} said no, so the agent sent {n} replacement "
                     f"offer{'s' if n != 1 else ''} (adds {plan['added_cost']})")
        st.session_state.auto_note = (f"{who} said no. The agent already sent {n} replacement "
                                      f"offer{'s' if n != 1 else ''}, adding {plan['added_cost']}.")
    else:
        db.log_event(biz.id, "auto_replaced", f"{who} said no; nobody else is free for those hours yet")
    save_state(biz.id, state)
    st.rerun()


def next_step() -> str:
    """One line saying what to do now, so the flow never needs explaining."""
    if not state.get("cs"):
        return "Check the rules below, then click **Simplify rules**."
    if view is None:
        return "Untick anything I got wrong, then click **Build schedule**."
    if state.get("plan") and not (state["plan"].get("bookings") or state["plan"].get("team_extra_hours")):
        return ("Nobody else is free for the open hours yet. Leave them open, message someone, or allow more "
                "extra hours and try again.")
    if state.get("plan"):
        return "Review the AI's plan below, then click **Send offers**."
    if view["gaps"] and not sent:
        return f"Click **Fill gaps with AI** to cover the {len(view['gaps'])} open shifts."
    if reopened:
        return "Some hours reopened. **Find replacements with AI** fills only those."
    if waiting:
        return (f"Waiting on {waiting} answer{'s' if waiting != 1 else ''}. Click a name below to see the offer "
                "on their phone and answer as them.")
    return "Fully staffed. Nothing to do this week."


hint = next_step() + (f" · 💬 **{n_unread} new message{'s' if n_unread != 1 else ''}** in Offers & messages."
                      if n_unread else "")
hint = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(hint).replace("$", "&#36;"))
st.markdown(f'<div class="nh-next">👉 {hint}</div>', unsafe_allow_html=True)

# The other side of the handoff, one click away: each person's offer as they see it on their phone.
waiting_people = []
for o in sorted(sent, key=lambda o: (o.status != "pending", o.created_ts)):
    if o.status in ("pending", "accepted") and (a := session.account_for(o)) and a.id not in [x.id for x in waiting_people]:
        waiting_people.append(a)
if waiting_people:
    with st.container(key="nh-viewas-row", horizontal=True, vertical_alignment="center", gap="small"):
        st.caption("📱 See it on their phone:")
        for a in waiting_people[:6]:
            st.button(f"{a.name.split()[0]} →", key=f"viewas_{a.id}", on_click=session.view_as, args=(a.id,),
                      help=f"Open {a.name}'s inbox as they see it; a button brings you back here.")

if st.session_state.get("auto_note"):
    st.success("🔄 " + md(st.session_state.pop("auto_note")))

# Stable labels keep the chosen tab across reruns (sending a message stays on Offers & messages).
tab_week, tab_offers, tab_activity = st.tabs(["Week", "Offers & messages", "Activity"])

# ================================================================ WEEK
with tab_week:
    with st.container(border=True):
        left, right = st.columns([1, 1], gap="large")
        with left:
            st.markdown("**Rules for this week** · type them the way you'd say them")
            rules_text = st.text_area("Rules", value=state.get("rules_text") or seed_text("rules.json", biz.id),
                                      height=90, label_visibility="collapsed")
            st.caption("Mention people with @: " + ", ".join("@" + n for n in sorted(names.values())))
            if st.button("Simplify rules", width="stretch"):
                with st.spinner("Muse Spark Open is simplifying your rules…"):
                    try:
                        cs = parsers.parse_rules(rules_text, team, biz)
                    except Exception as e:
                        st.error(f"Couldn't read the rules: {e}")
                        st.stop()
                state = {"rules_text": rules_text, "constraints": cs.model_dump()}
                save_state(biz.id, state)
                q = len(cs.ambiguities)
                db.log_event(biz.id, "rules_read", f"Simplified the rules: {len(cs.constraints)} understood"
                             + (f", {q} question{'s' if q != 1 else ''} back" if q else ""))
                st.rerun()

        cs = state.get("cs")
        with right:
            if cs is None:
                st.markdown("**Rules I understood**")
                st.caption("Simplify your rules to see them here as checkable chips.")
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
                    for o in db.offers(business_id=biz.id, status="pending"):  # a new schedule withdraws offers
                        db.save_offer(o.model_copy(update={"status": "unavailable"}))
                    state.update({"kept": keep, "inputs": inp, "staff_result": result, "plan": None, "trace": [],
                                  "out_sick": [], "changed_by": [],
                                  "auto_done": [o.id for o in db.offers(business_id=biz.id)]})  # old answers
                    save_state(biz.id, state)
                    gh0 = gap_hours(inp["slots"], result["gaps"])
                    db.log_event(biz.id, "schedule_built",
                                 f"Built the week: {money(result['bill']['total_cents'])} in staff pay, "
                                 + (f"{len(result['gaps'])} open shifts ({gh0:g} h)" if result["gaps"]
                                    else "no open shifts"))
                    st.rerun()
                if state.get("inputs", {}).get("not_enforced"):
                    st.caption("Noted but not enforced: " + ", ".join(state["inputs"]["not_enforced"]))

    if view is None:
        st.info("Simplify your rules, then build the schedule.")
    else:
        slots = state["inputs"]["slots"]
        # ------------------------------------------------------------ someone calls out
        with st.container(border=True, key="nh-callout"):
            with st.form("callout", border=False, clear_on_submit=True):
                a, b = st.columns([4, 1], vertical_alignment="bottom")
                said = a.text_input("Someone called out?", placeholder="e.g. Chris called out sick Thursday",
                                    help="Muse Spark Open reads who and which days; their shifts reopen and the "
                                         "AI fills them.")
                go = b.form_submit_button("Re-plan", width="stretch")
            if go and said.strip():
                with st.spinner("Muse Spark Open is reading that…"):
                    try:
                        co = changes.read_call_out(said, team, DAYS[datetime.date.today().weekday()])
                    except Exception:
                        co = None
                        st.warning("I couldn't read that. Try: “Chris called out sick Thursday”.")
                emp = next((e for e in team if co and co.employee and e.name.lower() == co.employee.lower()), None) \
                    or next((e for e in team if co and co.employee
                             and e.name.split()[0].lower() == co.employee.split()[0].lower()), None)
                if co is not None and (emp is None or not co.days):
                    st.warning("I couldn't tell who and which day. Try: “Chris called out sick Thursday”.")
                elif emp is not None:
                    r = changes.apply_call_out(state, biz.id, emp, list(co.days))
                    state["plan"] = None
                    save_state(biz.id, state)
                    st.session_state.callout_note = (
                        f"{emp.name.split()[0]} is off {', '.join(co.days)}: "
                        + (f"{r['shifts']} shift{'s' if r['shifts'] != 1 else ''} reopened ({r['what']})."
                           if r["shifts"] else "they had no shifts then."))
                    st.rerun()
            if st.session_state.get("callout_note"):
                st.caption("🤒 " + md(st.session_state.pop("callout_note")))

        gh, need = gap_hours(slots, view["gaps"]), demand_hours(slots)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Week labor cost", money(view["bill"]["total_cents"]),
                  help="Staff pay plus any offered or booked part-timer shifts and booking fees.")
        c2.metric("Overtime", money(view["bill"]["overtime_cents"]))
        # Planned counts offers still waiting for a yes; confirmed counts only what people accepted.
        firm = offers.week_view(state["inputs"], state["staff_result"], [o for o in sent if o.status == "accepted"],
                                pts, BOOKING_FEE_CENTS)
        gh_firm = gap_hours(slots, firm["gaps"])
        pending_shifts = sum(len(o.shifts) for o in sent if o.status == "pending")
        if need and pending_shifts:
            c3.metric("Coverage", f"{100 * (1 - gh / need):.1f}% planned",
                      f"{100 * (1 - gh_firm / need):.1f}% confirmed", delta_color="off",
                      help="Planned counts offers still waiting for an answer; confirmed counts only accepted ones.")
        else:
            c3.metric("Coverage", f"{100 * (1 - gh / need):.1f}%" if need else "–")
        open_now = f"{len(view['gaps'])} · {gh:g} h" if gh else "None"
        c4.metric("Open shifts", open_now if not pending_shifts else (open_now if gh else "0"),
                  f"{pending_shifts} waiting for a yes" if pending_shifts else None, delta_color="off")

        # ------------------------------------------------------------ before / after
        plan_now = state.get("plan")
        projected = view
        if plan_now and state.get("plan_result"):  # show what the proposal would do, before it is sent
            draft = offers.make_offers(biz, state["plan_result"]["assignments"], state["plan_slots"], pts,
                                       employees={e.id: e for e in team}, gaps=state.get("plan_gaps", ()))
            projected = offers.week_view(state["inputs"], state["staff_result"], sent + draft, pts,
                                         BOOKING_FEE_CENTS)
        base = state["staff_result"]
        if projected is not view or sent:
            b_gaps, b_h = len(base["gaps"]), gap_hours(slots, base["gaps"])
            a_gaps, a_h = len(projected["gaps"]), gap_hours(slots, projected["gaps"])
            b_cost, a_cost = base["bill"]["total_cents"], projected["bill"]["total_cents"]
            booked = sum(o.status == "accepted" for o in sent)          # part-timers and team members alike
            with st.container(border=True, key="nh-impact"):
                st.markdown("**Impact** · " + ("if you send these offers" if projected is not view
                                                 else f"{booked} booked, {waiting} waiting"))
                i1, i2, i3, i4 = st.columns(4)
                i1.metric("Open shifts", a_gaps, a_gaps - b_gaps, delta_color="inverse",
                          help="Compared with your own staff only.")
                i2.metric("Hours covered", f"{b_h - a_h:g} of {b_h:g}", f"+{b_h - a_h:g} h")
                i3.metric("Coverage", f"{100 * (1 - a_h / need):.1f}%", f"{100 * (b_h - a_h) / need:+.1f} pts")
                i4.metric("Added to the week", money(a_cost - b_cost),
                          f"{100 * (a_cost - b_cost) / b_cost:+.1f}% of labor", delta_color="off")

        legend([("✓", "booked"), ("⏳", "offer sent"), ("#F59E0B", "extra hours for your team"),
                ("#EF4444", "open shift")])
        frame = schedule_frame(slots, view["assignments"], view["gaps"], view["workers"], view["status"],
                               extra_cells=view.get("extra_cells"))
        st.dataframe(frame, width="stretch", height=35 * (len(frame.data) + 1) + 3)
        st.caption("Purple rows are marketplace part-timers: ⏳ offer sent, ✓ booked. Amber cells are open shifts "
                   "offered to your own team as extra hours. Red rows are shifts nobody has covered yet.")

        if view["gaps"] and not state.get("plan"):
            st.subheader("Find replacements" if reopened else "Fill the open shifts")
            if reopened:
                st.caption("Some hours reopened (a decline or a changed shift). The agent fills only those and "
                           "skips anyone who said no.")
            c1, c2, c3 = st.columns([1.3, 1, 1], vertical_alignment="center")
            team_first = c1.toggle("Ask my team first", value=state.get("team_first", True),
                                   help="Offer open shifts to your own staff as extra hours when that's cheaper "
                                        "than a part-timer plus the booking fee, or when nobody nearby qualifies. "
                                        "They can say no.")
            extra_h = c2.number_input("Extra hours each, at most", min_value=2, max_value=16, step=2,
                                      value=int(state.get("extra_hours", 8)), disabled=not team_first)
            ot_ok = c3.checkbox("Allow overtime past 40 h", value=state.get("overtime_ok", True),
                                disabled=not team_first,
                                help="Hours past 40 in a week are paid at the overtime rate (1.5x)")
            if (team_first, extra_h, ot_ok) != (state.get("team_first", True), state.get("extra_hours", 8),
                                                state.get("overtime_ok", True)):
                state.update(team_first=team_first, extra_hours=extra_h, overtime_ok=ot_ok)
                save_state(biz.id, state)
            if st.button("Find replacements with AI" if reopened else "Fill gaps with AI", type="primary"):
                run_fill(biz, grid, team, state, pts, replacement=reopened)
                st.rerun()

        # ------------------------------------------------------------ the agent's proposal
        plan = state.get("plan")
        if plan and not plan.get("bookings") and not plan.get("team_extra_hours"):
            # The agent found nobody: say so plainly instead of a $0.00 "plan" with a Send button.
            with st.container(border=True, key="nh-plan"):
                st.markdown("**Nobody is free for this yet.** No one on your team or within reach of the "
                            "marketplace can take the open hours without breaking a rule or a calendar.")
                if plan.get("explanation"):
                    st.caption(md(plan["explanation"]))
                st.caption("Options: leave it open for now, message someone directly, or allow more extra hours "
                           "or overtime above and try again.")
                if st.button("OK, keep it open", width="stretch"):
                    state["plan"] = None
                    save_state(biz.id, state)
                    db.log_event(biz.id, "plan_discarded", "Agent found nobody free; kept the shift open")
                    st.rerun()
        elif plan:
            with st.container(border=True, key="nh-plan"):
                st.markdown(md(f"**Proposed plan** · adds **{plan['added_cost']}** "
                               f"({plan['added_wages']} wages + {plan['booking_fees']} booking fees) · "
                               f"week total {plan['week_total']}"))
                st.markdown(md(plan["explanation"]))
                if plan.get("alternative"):
                    st.caption(md("Alternative: " + plan["alternative"]))
                if plan.get("grounded"):
                    st.markdown('<span class="nh-badge-ok">✓ Every number above was checked against the '
                                "optimizer's results.</span>", unsafe_allow_html=True)
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
                             "Rate": b["rate"] + "/h", "Distance": f"{b['distance_miles']} mi"}
                            for b in plan["bookings"]]
                    st.dataframe(rows, hide_index=True, width="stretch")
                    who = {}
                    for b in plan["bookings"]:
                        who.setdefault(b["part_timer_id"], []).append(f"{b['day']} {b['time']}")
                    neighborhood_map(biz, [{"pt": pts[i], "kind": "offered", "info": ", ".join(t)}
                                           for i, t in who.items()], height=300)
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
                    db.log_event(biz.id, "plan_discarded", "Discarded the AI plan")
                    st.rerun()

        if stage == 4:
            st.success("Fully staffed. Every open shift is booked, and those hours are blocked for other "
                       "businesses.")


def who_of(o) -> str:
    return (team_names.get(o.employee_id, o.employee_id) + " (your team)" if o.kind == "extra_hours"
            else pts[o.part_timer_id].name)


def change_request_box(o, first: str) -> None:
    """Their last message asks for different hours? Muse Spark Open reads it; code checks it; one click applies."""
    msgs = db.messages(o.id)
    last = next((m for m in reversed(msgs) if not m.from_business), None)
    if o.status != "pending" or last is None or (msgs and msgs[-1].from_business):
        return
    key = f"chg_{o.id}_{last.id}"
    if key not in st.session_state:
        if st.button(f"✨ Read {first}'s message as a change request", key=f"read_{key}",
                     help="Muse Spark Open reads the message; the optimizer's numbers decide what it means "
                          "for the week."):
            with st.spinner("Muse Spark Open is reading the message…"):
                try:
                    req = changes.read_request(o, last.text)
                    st.session_state[key] = changes.check(o, req, state["inputs"]["slots"])
                except Exception as e:
                    st.session_state[key] = {"ok": False, "reason": f"Couldn't read it ({e})."}
            st.rerun()
        return
    f = st.session_state[key]
    if not f.get("ok"):
        st.caption(md(f["reason"]))
        return
    with st.container(border=True, key="nh-change"):
        st.markdown(md(f"**{first} asks for {f['day']} {f['new']}** instead of {f['old']} "
                       f"({role(f['role'])})"))
        c1, c2, c3 = st.columns(3)
        c1.metric("Still covers", f"{f['covers_h']:g} of {f['of_h']:g} h",
                  f"{100 * f['covers_h'] / f['of_h']:.0f}% of the shift", delta_color="off")
        c2.metric("Would reopen", ", ".join(f["reopen"]) if f["reopen"] else "Nothing",
                  f"{f['reopen_h']:g} h" if f["reopen_h"] else None, delta_color="off")
        c3.metric("Their pay", money(f["new_pay_cents"]),
                  f"{'+' if f['pay_change_cents'] >= 0 else '-'}{money(abs(f['pay_change_cents']))}",
                  delta_color="off")
        if f.get("outside"):
            st.caption(md(f["outside"]))
        if f["reopen"]:
            st.caption("Accepting reopens that part of the shift; **Find replacements with AI** on the Week tab "
                       "can fill it.")
        a, b = st.columns(2)
        if a.button("Accept change", type="primary", width="stretch", key=f"ok_{key}"):
            changes.apply(state, o, f, biz.name)
            state["plan"] = None
            save_state(biz.id, state)
            st.session_state.pop(key, None)
            st.toast("Shift changed and a reply sent.", icon="✅")
            st.rerun()
        if b.button("Not now", width="stretch", key=f"no_{key}"):
            st.session_state[key] = {"ok": False, "reason": "Change not applied. Reply above if you want to "
                                                            "suggest something else."}
            st.rerun()


# ================================================================ OFFERS & MESSAGES
with tab_offers:
    auto = st.toggle("Auto-replace declines", value=state.get("auto_backfill", True),
                     help="When someone says no, the agent finds the next-best person and sends them the offer "
                          "right away. Offers still need a yes; nothing is booked without one.")
    if auto != state.get("auto_backfill", True):
        state["auto_backfill"] = auto
        save_state(biz.id, state)
    if not sent:
        st.info("No offers yet. Fill the open shifts on the **Week** tab, then send the plan's offers.")
    else:
        rows = []
        for o in sorted(sent, key=lambda o: (o.round, o.created_ts)):
            rows.append({"Round": o.round, "Who": who_of(o),
                         "Kind": "Extra hours" if o.kind == "extra_hours" else "Marketplace",
                         "Shifts": ", ".join(f"{s.day} {s.start}-{s.end}" for s in o.shifts),
                         "Role": ", ".join(sorted({role(s.role) for s in o.shifts})),
                         "Rate": money(o.rate_cents) + "/h", "Status": STATUS_BADGE[o.status],
                         "Messages": (f"{len(db.messages(o.id))}" + (f" · {unread[o.id]} new" if unread.get(o.id) else "")
                                      if db.messages(o.id) else "")})
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

        st.subheader("Messages" + badge(n_unread))
        def last_ts(o):
            ms = db.messages(o.id)
            return ms[-1].ts if ms else o.created_ts
        convo = sorted(sent, key=lambda o: (-last_ts(o), o.id))
        pick = st.selectbox("Conversation", [o.id for o in convo], key="msg_pick",
                            format_func=lambda i: next(f"{who_of(o)} · round {o.round}"   # stable labels: a changing
                                                       for o in convo if o.id == i))  # label resets the picker
        o = next(x for x in convo if x.id == pick)
        first = who_of(o).split(" (")[0].split()[0]
        thread(o, as_business=True, me=biz.name, other=first, key=f"biz_{o.id}")
        change_request_box(o, first)

# ================================================================ ACTIVITY
with tab_activity:
    st.caption("Everything that happened this week, newest first.")
    feed(db.events(business_id=biz.id, limit=40), "Simplify your rules to get started; everything you and your "
                                                   "people do shows up here.")
footer()
