"""Offers: the business proposes, the part-timer decides.

make_offers   plan -> one Offer per part-timer (their shifts bundled)
offer_facts   everything a part-timer needs to decide, computed in code
advise        Muse Spark Open turns the facts into a short recommendation (hard rules decided in code)
respond       accept -> bookings (all or nothing); decline -> the shifts reopen for the business
week_view     staff schedule + offered/booked shifts -> what the planner shows, with an honest bill
replacement_inputs   what the agent needs to fill only the reopened shifts
"""

from __future__ import annotations

import json
import time
import uuid

from core import llm, marketplace
from core.adapter import part_timer_to_worker
from core.agent import ungrounded_numbers
from core.schemas import Advice, Booking, Business, Offer, OfferShift, PartTimer
from core.solver import bill, is_available
from core.taxonomy import from_q, to_q

LIVE = ("pending", "accepted")


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _shift_slot(s: OfferShift) -> dict:
    return {"id": s.slot_id, "day": s.day, "start_q": to_q(s.start), "end_q": to_q(s.end), "role": s.role,
            "need": 1, "skills": []}


def shift_hours(o: Offer) -> float:
    return sum(to_q(s.end) - to_q(s.start) for s in o.shifts) / 4


# ---------------------------------------------------------------- making offers

def make_offers(business: Business, assignments, slots: list[dict], part_timers: dict[str, PartTimer],
                round_: int = 1, message: str = "") -> list[Offer]:
    by_slot = {s["id"]: s for s in slots}
    grouped: dict[str, list[OfferShift]] = {}
    for wid, sid in assignments:
        if wid not in part_timers:
            continue  # staff, not marketplace
        s = by_slot[sid]
        grouped.setdefault(wid, []).append(OfferShift(slot_id=sid, day=s["day"], start=from_q(s["start_q"]),
                                                      end=from_q(s["end_q"]), role=s["role"]))
    now = time.time()
    return [Offer(id=f"of_{uuid.uuid4().hex[:8]}", business_id=business.id, part_timer_id=pid, shifts=shifts,
                  rate_cents=part_timers[pid].expected_rate_cents, round=round_, message=message, created_ts=now)
            for pid, shifts in grouped.items()]


# ---------------------------------------------------------------- the part-timer's decision

def offer_facts(o: Offer, pt: PartTimer, business: Business, bookings: list[Booking],
                other_offers: list[Offer] = ()) -> dict:
    """The facts behind 'should I take this?'. All arithmetic happens here, not in the model."""
    w = marketplace.worker_for(pt, bookings)
    w_free = dict(w, availability=None)  # clash check ignores stated availability; that's reported separately
    shifts, clashes, outside = [], [], []
    for s in o.shifts:
        slot = _shift_slot(s)
        label = f"{s.day} {s.start}-{s.end}"
        shifts.append({"when": label, "role": s.role.replace("_", " "),
                       "hours": (slot["end_q"] - slot["start_q"]) / 4})
        if not is_available(w_free, slot):
            clashes.append(label)
        elif not is_available(w, slot):
            outside.append(label)
    hours = shift_hours(o)
    booked = sum(to_q(b.end) - to_q(b.start) for b in bookings if b.part_timer_id == pt.id) / 4
    overlapping_offers = [x.id for x in other_offers if x.id != o.id and x.status == "pending" and any(
        a.day == b.day and to_q(a.start) < to_q(b.end) and to_q(b.start) < to_q(a.end)
        for a in x.shifts for b in o.shifts)]
    return {
        "business": business.name,
        "shifts": shifts,
        "total_hours": hours,
        "offered_rate": _money(o.rate_cents),
        "your_asking_rate": _money(pt.expected_rate_cents),
        "total_pay": _money(round(o.rate_cents * hours)),
        "distance_miles": marketplace.distance_miles(pt, business),
        "clashes_with_your_commitments_or_bookings": clashes,
        "outside_your_stated_availability": outside,
        "hours_booked_this_week": booked,
        "hours_after_accepting": booked + hours,
        "your_weekly_max_hours": pt.max_hours,
        "over_your_max": booked + hours > pt.max_hours,
        "overlaps_other_pending_offers": len(overlapping_offers),
    }


ADVICE_SYSTEM = """You help a part-time hourly worker decide whether to accept a shift offer.
Use only the facts given. Pick one recommendation:
- "take it": fits their availability, pays at least their asking rate, reasonable distance, no clashes.
- "worth a look": mostly good but something to weigh (outside stated availability, a longer commute,
  more hours than their max, or another offer for the same time).
- "pass": it clashes with something they are already committed to.
Give 2-3 short reasons in second person ("You're free then", "It's 0.9 miles away").
Every number you write must appear in the facts. Never mention anything not in the facts."""


def advise(facts: dict, provider=None) -> Advice:
    if facts["clashes_with_your_commitments_or_bookings"]:  # hard rule: decided in code, not by the model
        return Advice(recommendation="pass", reasons=[
            "It clashes with something you're already committed to: "
            + ", ".join(facts["clashes_with_your_commitments_or_bookings"]) + "."])
    try:
        out = llm.call_json("advise_offer", ADVICE_SYSTEM, json.dumps(facts), Advice, provider=provider,
                            max_tokens=1500)
    except Exception:
        return template_advice(facts)
    if ungrounded_numbers(" ".join(out.reasons), [json.dumps(facts)]):
        return template_advice(facts)
    if out.recommendation == "take it" and (facts["over_your_max"] or facts["outside_your_stated_availability"]):
        out.recommendation = "worth a look"  # never a plain "take it" past a stated limit
    return out


def template_advice(facts: dict) -> Advice:
    reasons = [f"{facts['total_hours']:g} hours for {facts['total_pay']} at {facts['offered_rate']}/h "
               f"(you asked {facts['your_asking_rate']}).",
               f"It's {facts['distance_miles']} miles away."]
    if facts["over_your_max"]:
        reasons.append(f"It takes you to {facts['hours_after_accepting']:g} hours, over your "
                       f"{facts['your_weekly_max_hours']:g}-hour max.")
    rec = "worth a look" if facts["over_your_max"] or facts["outside_your_stated_availability"] else "take it"
    return Advice(recommendation=rec, reasons=reasons[:3])


def respond(o: Offer, accept: bool, pt: PartTimer, business: Business,
            bookings: list[Booking]) -> tuple[Offer, list[Booking]]:
    """Accept books every shift or none (if any clashes, the offer becomes 'unavailable')."""
    if o.status != "pending":
        raise ValueError(f"offer is already {o.status}")
    if not accept:
        return o.model_copy(update={"status": "declined", "responded_ts": time.time()}), []
    made, current = [], list(bookings)
    for s in o.shifts:
        try:
            bk = marketplace.book(pt.model_copy(update={"availability": {}}), business, _shift_slot(s), current)
        except marketplace.DoubleBooking:
            return o.model_copy(update={"status": "unavailable", "responded_ts": time.time()}), []
        bk = bk.model_copy(update={"rate_cents": o.rate_cents})
        made.append(bk)
        current.append(bk)
    return o.model_copy(update={"status": "accepted", "responded_ts": time.time()}), made


# ---------------------------------------------------------------- the manager's view of the week

def week_view(inputs: dict, staff_result: dict, offers: list[Offer], part_timers: dict[str, PartTimer],
              booking_fee_cents: int = 0) -> dict:
    """Staff schedule plus every live (pending or accepted) offer shift. Declined shifts are gaps again."""
    slots = inputs["slots"]
    assignments = [tuple(a) for a in staff_result["assignments"]]
    gaps = dict(staff_result["gaps"])
    status, workers = {}, list(inputs["workers"])
    for o in offers:
        if o.status not in LIVE:
            continue
        for s in o.shifts:
            if gaps.get(s.slot_id, 0) > 0:
                gaps[s.slot_id] -= 1
                assignments.append((o.part_timer_id, s.slot_id))
        if o.part_timer_id not in status:
            w = part_timer_to_worker(part_timers[o.part_timer_id])
            workers.append(dict(w, rate_cents=o.rate_cents, ot_rate_cents=round(o.rate_cents * 1.5)))
        status[o.part_timer_id] = "accepted" if o.status == "accepted" or status.get(o.part_timer_id) == "accepted" \
            else "pending"
    gaps = {k: v for k, v in gaps.items() if v > 0}
    return {"assignments": assignments, "gaps": gaps, "status": status, "workers": workers,
            "bill": bill(workers, slots, assignments, booking_fee_cents)}


def replacement_inputs(staff_result: dict, view: dict, offers: list[Offer], part_timers: list[PartTimer],
                       bookings: list[Booking], business_id: str
                       ) -> tuple[dict, list[PartTimer], list[Booking], dict[str, int]]:
    """For a replacement round: a staff_result whose gaps are only the reopened shifts, the pool without
    anyone who declined, bookings plus holds for live offers, and the shifts live offers already cover."""
    sr = {"assignments": [tuple(a) for a in staff_result["assignments"]], "gaps": view["gaps"],
          "bill": staff_result["bill"]}
    decliners = {o.part_timer_id for o in offers if o.status in ("declined", "unavailable")}
    pool = [p for p in part_timers if p.id not in decliners]
    held = [Booking(id=f"hold_{o.id}_{i}", part_timer_id=o.part_timer_id, business_id=business_id, day=s.day,
                    start=s.start, end=s.end, role=s.role, rate_cents=o.rate_cents)
            for o in offers if o.status == "pending" for i, s in enumerate(o.shifts)]
    covered: dict[str, int] = {}
    for o in offers:
        if o.status in LIVE:
            for s in o.shifts:
                covered[s.slot_id] = covered.get(s.slot_id, 0) + 1
    return sr, pool, list(bookings) + held, covered
