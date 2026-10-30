from core.taxonomy import (
    DAYS, ROLE_REQUIRED_SKILLS, ROLES, SKILLS, TEMPLATES, from_q, roles_for, to_q,
)


def test_every_template_role_is_known():
    for t in TEMPLATES.values():
        used = t["base_roles"] + [r for rs in t["toggles"].values() for r in rs]
        assert set(used) <= set(ROLES)


def test_required_skills_are_known():
    for role, skills in ROLE_REQUIRED_SKILLS.items():
        assert role in ROLES
        assert set(skills) <= set(SKILLS)


def test_every_template_has_hours_for_every_day():
    for t in TEMPLATES.values():
        assert set(t["default_hours"]) == set(DAYS)
        for open_, close in t["default_hours"].values():
            assert to_q(open_) < to_q(close)


def test_toggles_add_roles():
    assert "drive_thru_order" in roles_for("cafe", {"drive_thru"})
    assert "drive_thru_order" not in roles_for("cafe")


def test_time_round_trip():
    assert to_q("06:30") == 26
    assert from_q(26) == "06:30"
