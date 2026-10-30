"""Shared vocabulary for Nearhand: role IDs, skill IDs, business templates, time helpers.

Every other module uses these IDs, so the marketplace can match businesses to part-timers.
"""

ROLES = [
    "barista", "cashier", "shift_lead", "shift_manager",
    "drive_thru_order", "drive_thru_window", "food_prep", "prep_cook", "line_cook",
    "dishwasher_busser", "host", "server", "bartender", "to_go",
    "baker", "pastry_decorator", "stock_associate", "sales_associate", "deli_associate",
]

SKILLS = ["keyholder", "food_handler", "food_safety_manager", "alcohol_service"]

# Certifications a role always needs (a hard constraint in the solver).
ROLE_REQUIRED_SKILLS = {
    "food_prep": ["food_handler"],
    "prep_cook": ["food_handler"],
    "line_cook": ["food_handler"],
    "baker": ["food_handler"],
    "pastry_decorator": ["food_handler"],
    "deli_associate": ["food_handler"],
    "bartender": ["alcohol_service"],
}

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _hours(weekday, weekend):
    """Same open/close times every weekday, another pair on Sat and Sun."""
    return {d: (weekend if d in ("Sat", "Sun") else weekday) for d in DAYS}


TEMPLATES = {
    "cafe": {
        "label": "Café / coffee shop",
        "base_roles": ["barista", "cashier", "shift_lead", "dishwasher_busser"],
        "toggles": {"drive_thru": ["drive_thru_order"], "food_menu": ["food_prep"]},
        "default_hours": _hours(("06:00", "18:00"), ("07:00", "17:00")),
        "default_rules": ["a keyholder at every open and close"],
    },
    "restaurant": {
        "label": "Full-service restaurant",
        "base_roles": [
            "host", "server", "line_cook", "prep_cook", "dishwasher_busser", "shift_manager",
        ],
        "toggles": {"bar": ["bartender"], "takeout": ["to_go"]},
        "default_hours": _hours(("11:00", "22:00"), ("10:00", "23:00")),
        "default_rules": [
            "a food safety manager on every shift",
            "a keyholder at every open and close",
        ],
    },
    "quick_service": {
        "label": "Quick-service / fast casual",
        "base_roles": ["cashier", "line_cook", "prep_cook", "shift_lead"],
        "toggles": {
            "drive_thru": ["drive_thru_order", "drive_thru_window"],
            "delivery": ["to_go"],
        },
        "default_hours": _hours(("07:00", "23:00"), ("08:00", "23:00")),
        "default_rules": ["a keyholder at every open and close"],
    },
    "bakery": {
        "label": "Bakery",
        "base_roles": ["baker", "pastry_decorator", "cashier", "dishwasher_busser", "shift_lead"],
        # early_production adds 3-4am baker shifts when the staffing grid is built.
        "toggles": {"coffee_bar": ["barista"], "early_production": []},
        "default_hours": _hours(("06:00", "16:00"), ("07:00", "15:00")),
        "default_rules": ["a keyholder at every open and close"],
    },
    "retail": {
        "label": "Retail / convenience store",
        "base_roles": ["cashier", "stock_associate", "sales_associate", "shift_lead"],
        "toggles": {"deli": ["deli_associate"]},
        "default_hours": _hours(("07:00", "22:00"), ("08:00", "22:00")),
        "default_rules": ["a keyholder at every open and close"],
    },
}

# Demo neighborhood: Greater Hartford, CT. Approximate ZIP centroids (latitude, longitude).
ZIP_COORDS: dict[str, tuple[float, float]] = {
    "06095": (41.852, -72.644),  # Windsor
    "06002": (41.826, -72.730),  # Bloomfield
    "06103": (41.767, -72.674),  # Hartford, downtown
    "06105": (41.773, -72.704),  # Hartford, West End
    "06106": (41.749, -72.695),  # Hartford, south
    "06112": (41.790, -72.696),  # Hartford, north
    "06114": (41.740, -72.671),  # Hartford, South End
    "06117": (41.786, -72.746),  # West Hartford, north
    "06107": (41.753, -72.757),  # West Hartford, south
    "06108": (41.780, -72.623),  # East Hartford
    "06074": (41.834, -72.574),  # South Windsor
    "06088": (41.912, -72.599),  # East Windsor
    "06109": (41.703, -72.664),  # Wethersfield
}


def roles_for(business_type: str, toggles=frozenset()) -> list[str]:
    """Roles a business needs, given its type and which toggles are on."""
    t = TEMPLATES[business_type]
    extra = [r for name in toggles for r in t["toggles"].get(name, [])]
    roles = list(t["base_roles"])
    for r in extra:
        if r not in roles:
            roles.append(r)
    return roles


def to_q(hhmm: str) -> int:
    """'06:30' -> 26 (15-minute units from midnight)."""
    h, m = map(int, hhmm.split(":"))
    return h * 4 + m // 15


def from_q(q: int) -> str:
    """26 -> '06:30'."""
    return f"{q // 4:02d}:{(q % 4) * 15:02d}"
