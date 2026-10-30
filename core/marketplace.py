"""The part-timer marketplace: search, gap-filling candidates and booking.

Matching uses only availability, skills, role experience, rate and distance. Nothing else is
used to filter or rank people.
Plain functions over lists, so they are easy to test; core/db.py supplies the data.
"""

from __future__ import annotations

import math
import uuid

from core.adapter import part_timer_to_worker
from core.schemas import Booking, Business, PartTimer, SearchFilter
from core.solver import eligible
from core.taxonomy import DAYS, ZIP_COORDS, from_q, to_q  # noqa: F401  (re-exported for callers)


class DoubleBooking(ValueError):
    pass


# ---------------------------------------------------------------- distance

def miles(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance (haversine)."""
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def location(obj) -> tuple[float, float] | None:
    if getattr(obj, "lat", None) is not None and getattr(obj, "lon", None) is not None:
        return obj.lat, obj.lon
    return ZIP_COORDS.get(getattr(obj, "zip", ""))


def distance_miles(pt: PartTimer, business: Business) -> float | None:
    a, b = location(pt), location(business)
    return round(miles(*a, *b), 1) if a and b else None


# ---------------------------------------------------------------- calendars

def blocked_with_bookings(pt: PartTimer, bookings: list[Booking]) -> list[tuple[str, int, int]]:
    """Commitments elsewhere plus every booking on Nearhand, in 15-minute units."""
    out = [(d, to_q(s), to_q(e)) for d, s, e in pt.blocked]
    out += [(b.day, to_q(b.start), to_q(b.end)) for b in bookings if b.part_timer_id == pt.id]
    return out


def worker_for(pt: PartTimer, bookings: list[Booking]) -> dict:
    """Solver worker dict for a part-timer, with their bookings blocked out."""
    w = part_timer_to_worker(pt)
    w["blocked"] = blocked_with_bookings(pt, bookings)
    return w


def _free(w: dict, day: str, s: int, e: int) -> bool:
    probe = {"id": "probe", "day": day, "start_q": s, "end_q": e, "role": "_", "need": 1, "skills": []}
    from core.solver import is_available
    return is_available(w, probe)


# ---------------------------------------------------------------- search (what the manager types)

def search(f: SearchFilter, business: Business, part_timers: list[PartTimer], bookings: list[Booking],
           limit: int = 25) -> list[dict]:
    """Part-timers matching the filters, cheapest first, then nearest."""
    out = []
    for pt in part_timers:
        years = {r.role: r.years for r in pt.roles}
        if f.roles and not any(r in years for r in f.roles):
            continue
        if f.min_years is not None:
            relevant = [years[r] for r in f.roles if r in years] if f.roles else list(years.values())
            if not relevant or max(relevant) < f.min_years:
                continue
        if not set(f.skills) <= set(pt.skills):
            continue
        if f.max_rate_cents is not None and pt.expected_rate_cents > f.max_rate_cents:
            continue
        dist = distance_miles(pt, business)
        if f.max_distance_miles is not None and (dist is None or dist > f.max_distance_miles):
            continue
        w = worker_for(pt, bookings)
        days = f.days or (DAYS if (f.start or f.end) else [])
        free_days = days
        if days:
            s = to_q(f.start) if f.start else 0
            e = to_q(f.end) if f.end else 96
            free_days = [d for d in days if _free(w, d, s, e)]
            if not free_days:
                continue
        out.append({
            "id": pt.id, "name": pt.name, "rate_cents": pt.expected_rate_cents, "distance_miles": dist,
            "roles": [{"role": r.role, "years": r.years} for r in pt.roles], "skills": pt.skills,
            "business_types": pt.business_types, "summary": pt.summary, "free_days": free_days,
        })
    out.sort(key=lambda c: (c["rate_cents"], c["distance_miles"] if c["distance_miles"] is not None else 99))
    return out[:limit]


# ---------------------------------------------------------------- candidates for the solver

def pool_for_gaps(business: Business, gap_slots: list[dict], part_timers: list[PartTimer],
                  bookings: list[Booking], max_distance_miles: float = 5.0,
                  max_rate_cents: int | None = None, per_gap: int = 8) -> list[dict]:
    """Solver workers who could cover at least one gap: the cheapest `per_gap` nearby for each gap."""
    chosen: dict[str, dict] = {}
    workers = []
    for pt in part_timers:
        dist = distance_miles(pt, business)
        if dist is None or dist > max_distance_miles:
            continue
        if max_rate_cents is not None and pt.expected_rate_cents > max_rate_cents:
            continue
        w = worker_for(pt, bookings)
        w["distance_miles"] = dist
        workers.append(w)
    for slot in gap_slots:
        fits = sorted((w for w in workers if eligible(w, slot)),
                      key=lambda w: (w["rate_cents"], w["distance_miles"]))
        for w in fits[:per_gap]:
            chosen[w["id"]] = w
    return list(chosen.values())


# ---------------------------------------------------------------- booking

def book(pt: PartTimer, business: Business, slot: dict, bookings: list[Booking]) -> Booking:
    """Create a booking for one slot. Refuses if it clashes with a booking or commitment."""
    w = worker_for(pt, bookings)
    if not _free(w, slot["day"], slot["start_q"], slot["end_q"]):
        raise DoubleBooking(f"{pt.name} is not free {slot['day']} "
                            f"{from_q(slot['start_q'])}-{from_q(slot['end_q'])}")
    return Booking(id=f"bk_{uuid.uuid4().hex[:8]}", part_timer_id=pt.id, business_id=business.id,
                   day=slot["day"], start=from_q(slot["start_q"]), end=from_q(slot["end_q"]),
                   role=slot["role"], rate_cents=pt.expected_rate_cents)
