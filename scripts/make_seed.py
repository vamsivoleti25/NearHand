"""Generate the demo data in data/seed/. Deterministic: the same files every run.

    python -m scripts.make_seed

Writes businesses.json, teams.json, team_texts.json, grids.json, rules.json, part_timers.json.
Part-timer work histories (their own words) are added afterwards by scripts/make_bios.py.
All people are fictional.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from core import parsers
from core.schemas import Business, Employee, PartTimer, RoleExperience, StaffingBlock, StaffingGrid
from core.taxonomy import DAYS, ROLE_REQUIRED_SKILLS, ZIP_COORDS, from_q, to_q

OUT = Path(__file__).resolve().parent.parent / "data" / "seed"
rng = np.random.default_rng(42)

# ---------------------------------------------------------------- businesses

CAFE = Business(
    id="biz_bean", name="Bean & Co.", type="cafe", toggles=["drive_thru"], zip="06095",
    lat=41.8521, lon=-72.6431,
    hours={d: ("06:00", "18:00") if d not in ("Sat", "Sun") else ("07:00", "17:00") for d in DAYS},
    description="We get slammed 7-9am on weekdays and Saturday brunch is our busiest time. Sundays are slow.",
)
RESTAURANT = Business(
    id="biz_maple", name="Maple Table", type="restaurant", toggles=["bar"], zip="06002",
    lat=41.8402, lon=-72.6855,  # about 2.5 miles from the café
    hours={d: ("11:00", "22:00") if d not in ("Fri", "Sat") else ("11:00", "23:00") for d in DAYS},
    description="Dinner rush 5-8pm every night, Friday and Saturday nights are packed.",
)

CAFE_TEAM_TEXT = """Maya Patel - barista, $19/hr, max 30 hrs, can't do Tuesdays
Leo Kim - barista + register, 18.50, full time
Ana Ruiz - shift lead (also pulls shots), opens most days, $23/hr full-time
Sam Ortiz - cashier/drive thru order taker, $17.75, PT 20 hrs
Priya Nair - dishes & bussing, $17.25/hr, weekends only, 25h
Jordan Lee - barista, $20, part time 25h, no Mondays
Chris Wong - shift lead, can cover register, $22.50, PT 28h
Ravi Shah - barista/cashier, $19, FT
Mei Chen - register + drive thru, $18/hr, full time
Tom Baker - dishwasher, helps with food prep, $17.25, weekdays only, 32 hrs
Elena Popov - shift lead + barista, closer, $22/hr, 36 hrs
Diego Silva - drive thru & register, 17.75, 30h
Nia Brooks - barista, can do drive thru, $18.50, 24 hrs
Omar Haddad - dish/bussing, sometimes register, $17.25, 24h
Kai Nakamura - barista and cashier, $19, 32h
Lucia Romero - drive thru, register, dishes - $17.50, 30h, never Sundays"""

WEEKDAYS, WEEKEND = DAYS[:5], ["Sat", "Sun"]


def emp(name, roles, rate, max_h, skills=(), off=(), ft=False):
    first = name.split()[0].lower()
    return Employee(id=f"emp_{first}", name=name, roles=roles, skills=list(skills), rate_cents=rate,
                    ot_rate_cents=round(rate * 1.5), max_hours=max_h, unavailable_days=list(off),
                    employment="full_time" if ft else "part_time")


CAFE_TEAM = [  # the gold parse of CAFE_TEAM_TEXT
    emp("Maya Patel", ["barista"], 1900, 30, off=["Tue"]),
    emp("Leo Kim", ["barista", "cashier"], 1850, 40, ft=True),
    emp("Ana Ruiz", ["shift_lead", "barista"], 2300, 40, ["keyholder"], ft=True),
    emp("Sam Ortiz", ["cashier", "drive_thru_order"], 1775, 20),
    emp("Priya Nair", ["dishwasher_busser"], 1725, 25, off=WEEKDAYS),
    emp("Jordan Lee", ["barista"], 2000, 25, off=["Mon"]),
    emp("Chris Wong", ["shift_lead", "cashier"], 2250, 28, ["keyholder"]),
    emp("Ravi Shah", ["barista", "cashier"], 1900, 40, ft=True),
    emp("Mei Chen", ["cashier", "drive_thru_order"], 1800, 40, ft=True),
    emp("Tom Baker", ["dishwasher_busser", "food_prep"], 1725, 32, ["food_handler"], off=WEEKEND),
    emp("Elena Popov", ["shift_lead", "barista"], 2200, 36, ["keyholder"]),
    emp("Diego Silva", ["drive_thru_order", "cashier"], 1775, 30),
    emp("Nia Brooks", ["barista", "drive_thru_order"], 1850, 24),
    emp("Omar Haddad", ["dishwasher_busser", "cashier"], 1725, 24),
    emp("Kai Nakamura", ["barista", "cashier"], 1900, 32),
    emp("Lucia Romero", ["drive_thru_order", "cashier", "dishwasher_busser"], 1750, 30, off=["Sun"]),
]

RESTAURANT_TEAM_TEXT = """Grace Okafor - GM, ServSafe manager, has keys, $28/hr FT
Ben Taylor - line cook, food handler card, $21.50, 40h
Hana Sato - line cook / prep, $20.50, 32 hrs
Luis Mendez - prep cook, $19, PT 28h, off Sundays
Zoe Martin - server, $18 + tips, 30h
Aaron Fisher - server and host, $17.75, 24h
Ivy Nguyen - host, $17.25, weekends only
Malik Johnson - bartender, TIPS certified, $19, 32h
Rosa Garcia - dishwasher/busser, $17.25, 30h
Sean Walsh - shift manager, closer, $25, 36h"""

RESTAURANT_TEAM = [
    emp("Grace Okafor", ["shift_manager", "server"], 2800, 40, ["keyholder", "food_safety_manager"], ft=True),
    emp("Ben Taylor", ["line_cook", "prep_cook"], 2150, 40, ["food_handler"], ft=True),
    emp("Hana Sato", ["line_cook", "prep_cook"], 2050, 32, ["food_handler"]),
    emp("Luis Mendez", ["prep_cook"], 1900, 28, ["food_handler"], off=["Sun"]),
    emp("Zoe Martin", ["server"], 1800, 30),
    emp("Aaron Fisher", ["server", "host"], 1750, 24),
    emp("Ivy Nguyen", ["host"], 1725, 20, off=WEEKDAYS),
    emp("Malik Johnson", ["bartender"], 1900, 32, ["alcohol_service"]),
    emp("Rosa Garcia", ["dishwasher_busser"], 1725, 30),
    emp("Sean Walsh", ["shift_manager", "server"], 2500, 36, ["keyholder", "food_safety_manager"]),
]

RULES = {
    "biz_bean": "@Maya off Tuesdays, baristas max 32h, a keyholder at every open and close, "
                "10 hours between shifts",
    "biz_maple": "a food safety manager on every shift, keyholder at open and close, "
                 "@Ivy only weekends, servers max 30 hours",
}


def cafe_grid() -> StaffingGrid:
    """Reference grid for the café: template + rush mid shifts. Used by tests and as a fallback."""
    g = parsers.default_grid(CAFE)
    extra = [StaffingBlock(day=d, role=r, start="06:00", end="10:00", headcount=1)
             for d in WEEKDAYS for r in ("barista", "cashier", "drive_thru_order")]
    extra += [StaffingBlock(day="Sat", role=r, start="09:00", end="13:00", headcount=1)
              for r in ("barista", "cashier", "drive_thru_order")]
    return StaffingGrid(blocks=g.blocks + extra, notes=["Reference grid: template + weekday rush + Saturday brunch."])


# ---------------------------------------------------------------- part-timers

FIRST = ["Aaliyah", "Ahmed", "Alex", "Amara", "Andre", "Bianca", "Brandon", "Camila", "Carlos", "Chloe",
         "Darius", "Daniela", "Dev", "Emily", "Esme", "Fatima", "Gabe", "Hailey", "Imani", "Isaac",
         "Jada", "Jasmine", "Javier", "Kayla", "Kevin", "Laila", "Liam", "Marcus", "Maria", "Mateo",
         "Mia", "Nadia", "Nathan", "Noah", "Olivia", "Quinn", "Rahul", "Riley", "Sara", "Shay",
         "Sofia", "Taylor", "Theo", "Tyler", "Valeria", "Wes", "Yara", "Yusuf", "Zara", "Zion"]
LAST = ["Adams", "Bailey", "Bennett", "Brooks", "Castillo", "Cohen", "Diaz", "Edwards", "Evans", "Flores",
        "Foster", "Grant", "Hayes", "Herrera", "Hughes", "Ibrahim", "Jackson", "James", "Khan", "Lopez",
        "Moore", "Murphy", "Ortega", "Owens", "Park", "Perez", "Price", "Reyes", "Rivera", "Ross",
        "Russo", "Santos", "Shah", "Singh", "Sullivan", "Thomas", "Torres", "Vargas", "Ward", "Young"]

# archetype: (weight, roles in order of experience, business types, base certifications)
ARCHETYPES = {
    "coffee":  (0.20, ["barista", "cashier"], ["cafe", "bakery"], []),
    "counter": (0.14, ["cashier", "drive_thru_order", "drive_thru_window"], ["quick_service", "cafe"], []),
    "kitchen": (0.13, ["line_cook", "prep_cook", "dishwasher_busser"], ["restaurant", "quick_service"],
                ["food_handler"]),
    "foh":     (0.12, ["server", "host", "dishwasher_busser"], ["restaurant"], []),
    "bar":     (0.06, ["bartender", "server"], ["restaurant"], ["alcohol_service"]),
    "lead":    (0.11, ["shift_lead", "barista", "cashier"], ["cafe", "quick_service"], ["keyholder"]),
    "bakery":  (0.07, ["baker", "pastry_decorator", "cashier"], ["bakery"], ["food_handler"]),
    "retail":  (0.08, ["cashier", "stock_associate", "sales_associate"], ["retail"], []),
    "dish":    (0.09, ["dishwasher_busser", "prep_cook", "food_prep"], ["restaurant", "cafe"], ["food_handler"]),
}
BASE_RATE = {"barista": 1850, "cashier": 1750, "drive_thru_order": 1750, "drive_thru_window": 1750,
             "line_cook": 2100, "prep_cook": 1950, "food_prep": 1850, "dishwasher_busser": 1725,
             "server": 1750, "host": 1725, "bartender": 1900, "shift_lead": 2200, "baker": 2100,
             "pastry_decorator": 2050, "stock_associate": 1750, "sales_associate": 1750}

# availability pattern: (weight, days, window)
PATTERNS = {
    "weekends":   (0.24, ["Sat", "Sun"], ("06:00", "20:00")),
    "weekday_am": (0.18, WEEKDAYS, ("05:30", "14:00")),
    "evenings":   (0.20, DAYS, ("15:00", "23:30")),
    "student":    (0.16, WEEKDAYS + ["Sat"], ("11:00", "22:00")),
    "open":       (0.22, None, ("06:00", "22:00")),  # five random days, all day
}
# ZIPs weighted toward the café and restaurant
ZIP_WEIGHTS = {"06095": 6, "06002": 5, "06112": 3, "06105": 3, "06103": 2, "06117": 2, "06108": 2,
               "06074": 2, "06088": 1, "06106": 1, "06114": 1, "06107": 1, "06109": 1}


def pick(options: dict):
    keys = list(options)
    w = np.array([options[k][0] if isinstance(options[k], tuple) else options[k] for k in keys], float)
    return keys[rng.choice(len(keys), p=w / w.sum())]


def make_part_timer(i: int, used_names: set) -> PartTimer:
    while True:
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if name not in used_names:
            used_names.add(name)
            break
    arch = pick(ARCHETYPES)
    _, roles, btypes, certs = ARCHETYPES[arch]
    n_roles = int(rng.choice([1, 2, 3], p=[0.3, 0.5, 0.2]))
    years_main = round(float(rng.gamma(2.0, 1.4)) * 2) / 2
    exp = [RoleExperience(role=roles[0], years=max(0.5, min(years_main, 15)))]
    for r in roles[1:n_roles]:
        exp.append(RoleExperience(role=r, years=max(0.5, round(float(rng.uniform(0.25, 1)) * years_main * 2) / 2)))
    skills = set(certs)
    for r in exp:
        skills |= set(ROLE_REQUIRED_SKILLS.get(r.role, []))
    if years_main >= 3 and rng.random() < 0.35:
        skills.add("keyholder")
    if arch in ("kitchen", "lead") and years_main >= 4 and rng.random() < 0.3:
        skills.add("food_safety_manager")
    rate = BASE_RATE[exp[0].role] + 40 * min(years_main, 8) + int(rng.integers(-4, 5)) * 25
    rate = max(1725, int(round(rate / 25) * 25))  # CT minimum wage is $16.94 (2026)

    pat = pick(PATTERNS)
    _, days, (s, e) = PATTERNS[pat]
    if days is None:
        days = sorted(rng.choice(DAYS, size=5, replace=False).tolist(), key=DAYS.index)
    availability = {d: [(s, e)] for d in days}
    blocked = []
    for _ in range(int(rng.integers(1, 4))):  # commitments elsewhere (other jobs, classes)
        d = str(rng.choice(days))
        start = int(rng.integers(to_q(s), max(to_q(s) + 1, to_q(e) - 16)))
        start -= start % 2
        end = min(start + int(rng.integers(16, 25)), to_q(e))
        blocked.append((d, from_q(start), from_q(end)))

    z = pick(ZIP_WEIGHTS)
    lat, lon = ZIP_COORDS[z]
    btypes_out = list(dict.fromkeys(btypes[: int(rng.integers(1, len(btypes) + 1))]))
    return PartTimer(
        id=f"pt_{i:03d}", name=name, zip=z, lat=round(lat + float(rng.normal(0, 0.008)), 4),
        lon=round(lon + float(rng.normal(0, 0.008)), 4), expected_rate_cents=rate, roles=exp,
        business_types=btypes_out, skills=sorted(skills), availability=availability, blocked=blocked,
        max_hours=float(rng.choice([12, 16, 20, 24, 28, 30])), summary="", bio="",
    )


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    used = {e.name for e in CAFE_TEAM + RESTAURANT_TEAM}
    pts = [make_part_timer(i, used) for i in range(1, 151)]
    bios_path = OUT / "part_timers.json"
    if bios_path.exists():  # keep work histories already written by make_bios.py
        old = {p["id"]: p for p in json.loads(bios_path.read_text())}
        for p in pts:
            if p.id in old and old[p.id].get("name") == p.name:
                p.bio, p.summary = old[p.id].get("bio", ""), old[p.id].get("summary", "")

    def dump(name, obj):
        (OUT / name).write_text(json.dumps(obj, indent=1))

    dump("businesses.json", [CAFE.model_dump(), RESTAURANT.model_dump()])
    dump("teams.json", {"biz_bean": [e.model_dump() for e in CAFE_TEAM],
                        "biz_maple": [e.model_dump() for e in RESTAURANT_TEAM]})
    dump("team_texts.json", {"biz_bean": CAFE_TEAM_TEXT, "biz_maple": RESTAURANT_TEAM_TEXT})
    dump("grids.json", {"biz_bean": cafe_grid().model_dump(), "biz_maple": parsers.default_grid(RESTAURANT).model_dump()})
    dump("rules.json", RULES)
    dump("part_timers.json", [p.model_dump() for p in pts])
    print(f"Wrote {len(pts)} part-timers, 2 businesses, 2 teams to {OUT}")


if __name__ == "__main__":
    main()
