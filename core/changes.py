"""Change requests in chat: "can I do 8-12 instead?" becomes a checked, one-click change to an offer.

Muse Spark Open only reads the message into a day and times. Everything after that is code: which part of the
open shift the new time still covers, what would reopen, and the pay. Accepting splits the open shift, so a
part that reopens shows up as a gap and "Find replacements with AI" can fill it.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from core import db, llm
from core.schemas import HHMM, Day, Offer, OfferShift
from core.taxonomy import from_q, to_q


class ChangeRequest(BaseModel):
    is_change_request: bool = Field(description="True only if the message asks to work different hours or a "
                                                "different start/end time for one of the offered shifts")
    day: Optional[Day] = Field(None, description="The offered shift's day the change is about")
    start: Optional[HHMM] = Field(None, description="New start time, 24-hour")
    end: Optional[HHMM] = Field(None, description="New end time, 24-hour")


SYSTEM = ("You read one chat message from a worker about a shift offer and say whether it asks to change the "
          "hours of one of the offered shifts. If it does, give the day and the new start and end times in "
          "24-hour HH:MM. If the message gives only a new start (or only a new end), keep the other time from "
          "the offered shift. Questions, thanks, or anything else are not change requests. Return only JSON.")


def read_request(o: Offer, text: str) -> ChangeRequest:
    shifts = "; ".join(f"{s.day} {s.start}-{s.end} ({s.role})" for s in o.shifts)
    return llm.call_json("change_request", SYSTEM, f"Offered shifts: {shifts}\nMessage: {text}", ChangeRequest)


def _hours(q: int) -> float:
    return q / 4


def _span(a: int, b: int) -> str:
    return f"{_t(a)}–{_t(b)}"


def _t(q: int) -> str:
    h, m = divmod(q, 4)
    return f"{h % 12 or 12}{'' if not m else ':' + str(m * 15)}{'a' if h < 12 or h == 24 else 'p'}"


def check(o: Offer, req: ChangeRequest, slots: list[dict]) -> dict:
    """The facts about a change, in code. ok=False with a reason when it can't be applied."""
    if not req.is_change_request or not (req.day and req.start and req.end):
        return {"ok": False, "reason": "That message doesn't ask for different hours."}
    shift = next((s for s in o.shifts if s.day == req.day), None)
    if shift is None:
        return {"ok": False, "reason": f"There's no offered shift on {req.day}."}
    slot = next((s for s in slots if s["id"] == shift.slot_id), None)
    if slot is None:
        return {"ok": False, "reason": "That shift isn't in this week's schedule any more."}
    a, b = to_q(shift.start), to_q(shift.end)
    na, nb = to_q(req.start), to_q(req.end)
    if nb <= na:
        return {"ok": False, "reason": "The new end time has to be after the start."}
    ca, cb = max(a, na), min(b, nb)            # only the part inside the open shift is booked
    if cb <= ca:
        return {"ok": False, "reason": f"{_span(na, nb)} doesn't overlap the open shift ({_span(a, b)})."}
    reopen = [(x, y) for x, y in ((a, ca), (cb, b)) if y > x]
    outside = (na < a) or (nb > b)
    old_pay, new_pay = round(o.rate_cents * _hours(b - a)), round(o.rate_cents * _hours(cb - ca))
    return {"ok": True, "day": req.day, "role": shift.role, "slot_id": shift.slot_id,
            "old": _span(a, b), "new": _span(ca, cb), "new_q": (ca, cb),
            "covers_h": _hours(cb - ca), "of_h": _hours(b - a),
            "reopen": [_span(x, y) for x, y in reopen], "reopen_q": reopen,
            "reopen_h": sum(_hours(y - x) for x, y in reopen),
            "outside": f"Only {_span(ca, cb)} is booked: the rest of {_span(na, nb)} is outside the open shift."
            if outside else "",
            "old_pay_cents": old_pay, "new_pay_cents": new_pay, "pay_change_cents": new_pay - old_pay}


def apply(state: dict, o: Offer, facts: dict, business_name: str) -> Offer:
    """Change the offer's shift, and split the open shift in the saved week so what reopens is a gap."""
    slots, gaps = state["inputs"]["slots"], state["staff_result"]["gaps"]
    sid = facts["slot_id"]
    slot = next(s for s in slots if s["id"] == sid)
    ca, cb = facts["new_q"]
    new_ids = []
    for i, (x, y) in enumerate([(ca, cb)] + list(facts["reopen_q"])):
        nid = f"{sid}~{x}-{y}"
        slots.append(dict(slot, id=nid, start_q=x, end_q=y, need=1))
        gaps[nid] = 1
        new_ids.append(nid)
    slot["need"] -= 1                               # the unit the offer was covering moves to the pieces
    gaps[sid] = gaps.get(sid, 1) - 1
    if gaps[sid] <= 0:
        gaps.pop(sid)
    if slot["need"] <= 0:
        slots.remove(slot)
    shifts = [OfferShift(slot_id=new_ids[0], day=s.day, start=from_q(ca), end=from_q(cb), role=s.role)
              if s.slot_id == sid else s for s in o.shifts]
    o2 = o.model_copy(update={"shifts": shifts})
    who_id = o.employee_id or o.part_timer_id      # they gave those hours up: never ask them to cover them
    if who_id not in state.setdefault("changed_by", []):
        state["changed_by"].append(who_id)
    db.save_offer(o2)                               # same status: no offer event, so log the change here
    who = db.worker_name(o2)
    db.log_event(o.business_id, "change_accepted",
                 f"Changed {who}'s {facts['day']} shift to {facts['new']} (was {facts['old']})"
                 + (f"; {', '.join(facts['reopen'])} reopened" if facts["reopen"] else ""),
                 worker_id=o.employee_id or o.part_timer_id, offer_id=o.id)
    db.send_message(o2, True, business_name, f"Done: {facts['day']} is now {facts['new']}. "
                    "You can accept the offer whenever you're ready.")
    return o2


# ---------------------------------------------------------------- someone calls out

class CallOut(BaseModel):
    employee: Optional[str] = Field(None, description="The team member's full name, exactly as in the team list")
    days: list[Day] = Field(default=[], description="The days they can't work")


CALLOUT_SYSTEM = ("A small-business owner says a team member can't work (called out sick, emergency, and so on). "
                  "Give that person's full name exactly as it appears in the team list, and the days they'll miss. "
                  "'today'/'tomorrow' use the day given. If no team member or no day is clear, leave it empty. "
                  "Return only JSON.")


def read_call_out(text: str, team: list, today: str) -> CallOut:
    names = ", ".join(e.name for e in team)
    return llm.call_json("call_out", CALLOUT_SYSTEM, f"Team: {names}\nToday is {today}.\nOwner: {text}", CallOut)


def apply_call_out(state: dict, business_id: str, employee, days: list[str]) -> dict:
    """Take someone off their shifts on those days: the shifts reopen as gaps, any extra-hours offer they had on
    those days is withdrawn, and the agent won't give them hours on those days. Returns what reopened."""
    from core.solver import bill
    slots = {s["id"]: s for s in state["inputs"]["slots"]}
    sr = state["staff_result"]
    gaps = sr["gaps"]
    reopened = [sid for wid, sid in sr["assignments"] if wid == employee.id and slots[sid]["day"] in days]
    sr["assignments"] = [a for a in sr["assignments"] if not (a[0] == employee.id and a[1] in reopened)]
    for sid in reopened:
        gaps[sid] = gaps.get(sid, 0) + 1
    sr["bill"] = bill(state["inputs"]["workers"], state["inputs"]["slots"], sr["assignments"])
    for o in db.offers(business_id=business_id, status="pending") + db.offers(business_id=business_id,
                                                                               status="accepted"):
        if o.kind == "extra_hours" and o.employee_id == employee.id and any(s.day in days for s in o.shifts):
            db.save_offer(o.model_copy(update={"status": "unavailable"}))
    out = state.setdefault("out_sick", [])
    for d in days:
        if [employee.id, d] not in out:
            out.append([employee.id, d])
    hours = sum(slots[sid]["end_q"] - slots[sid]["start_q"] for sid in reopened) / 4
    what = ", ".join(f"{slots[sid]['day']} {_span(slots[sid]['start_q'], slots[sid]['end_q'])}" for sid in reopened)
    db.log_event(business_id, "called_out",
                 f"{employee.name} called out ({', '.join(days)}): "
                 + (f"{len(reopened)} shift{'s' if len(reopened) != 1 else ''} reopened, {hours:g} h ({what})"
                    if reopened else "no shifts that day"), worker_id=employee.id)
    return {"shifts": len(reopened), "hours": hours, "what": what}
