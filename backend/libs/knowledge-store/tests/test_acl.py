"""ACL mapping and the fine check. No database."""

from knowledge_store.acl import Principal, allows, map_acl
from shared.contracts.validate import load_contracts


def _contract(source_id: str):
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    return next(
        item
        for item in load_contracts(root / "infrastructure/config/sources")
        if item.source_id == source_id
    )


def _hr():
    return _contract("hr")


def test_employee_mapping_is_coarse_and_exact() -> None:
    view = map_acl(["hr_employee"], _hr())
    assert view.groups == ("employees",)
    assert view.departments == ()
    assert view.min_clearance == 1
    assert view.unmapped == ()
    principal = Principal("ada", ("employees",), "internal", "engineering")
    allowed, rule = allows(view, principal)
    assert allowed
    assert rule is not None
    assert rule["group"] == "employees"
    assert rule["department"] == "*"


def test_admin_mapping_stays_inside_people() -> None:
    view = map_acl(["hr_policy_admin"], _hr())
    assert view.departments == ("people",)
    assert view.min_clearance == 2
    engineer = Principal("ada", ("hr_admins",), "confidential", "engineering")
    assert allows(view, engineer) == (False, None)
    admin = Principal("ada", ("hr_admins",), "confidential", "people")
    allowed, rule = allows(view, admin)
    assert allowed
    assert rule is not None
    assert rule["group"] == "hr_admins"


def test_roadmap_readers_opens_any_department_and_unmapped_grants_nothing() -> None:
    view = map_acl(["account_team", "roadmap_readers"], _contract("sales"))
    assert view.departments == ()
    assert "roadmap" in view.groups
    lead = Principal("product-lead", ("employees", "roadmap"), "internal", "product")
    allowed, rule = allows(view, lead)
    assert allowed
    assert rule is not None
    assert rule["group"] == "roadmap"
    engineer = Principal("employee-eng", ("employees",), "internal", "engineering")
    assert allows(view, engineer) == (False, None)
    closed = map_acl(["sales_leadership"], _contract("sales"))
    assert allows(closed, lead) == (False, None)
    epic = map_acl(["project_member", "roadmap_readers"], _contract("engineering"))
    assert allows(epic, lead)[0]
    assert allows(map_acl(["eng_leadership"], _contract("engineering")), lead) == (False, None)
    unmapped = map_acl(["not-a-role"], _contract("sales"))
    assert unmapped.groups == ()
    assert unmapped.rules == ()
    assert allows(unmapped, lead) == (False, None)


def test_unmapped_native_acl_grants_nothing() -> None:
    view = map_acl(["not-a-role"], _hr())
    assert view.groups == ()
    assert view.min_clearance == 99
    assert view.rules == ()
    principal = Principal("ada", ("employees",), "restricted", "people")
    assert allows(view, principal) == (False, None)


def test_a_rule_that_omits_a_field_grants_nothing() -> None:
    from knowledge_store.acl import AclView

    principal = Principal("ada", ("employees",), "restricted", "engineering")
    missing_department = AclView(
        ("employees",),
        (),
        1,
        ({"group": "employees", "clearance": "internal"},),
        (),
    )
    missing_clearance = AclView(
        ("employees",),
        ("engineering",),
        0,
        ({"group": "employees", "department": "engineering"},),
        (),
    )
    missing_group = AclView(
        ("employees",),
        ("engineering",),
        1,
        ({"department": "engineering", "clearance": "internal"},),
        (),
    )
    assert allows(missing_department, principal) == (False, None)
    assert allows(missing_clearance, principal) == (False, None)
    assert allows(missing_group, principal) == (False, None)


def test_star_department_matches_any_department() -> None:
    view = map_acl(["hr_employee"], _hr())
    assert view.departments == ()
    assert view.rules[0]["department"] == "*"
    assert allows(view, Principal("ada", ("employees",), "internal", "engineering"))[0]
    assert allows(view, Principal("bea", ("employees",), "internal", "people"))[0]
    assert allows(view, Principal("cam", ("employees",), "public", "engineering")) == (False, None)
    assert allows(view, Principal("dee", ("sales_team",), "internal", "sales")) == (False, None)


def test_fine_check_rejects_a_mix_that_no_single_rule_allows() -> None:
    view = map_acl(["hr_employee", "hr_policy_admin"], _hr())
    assert view.min_clearance == 1
    assert view.departments == ()
    assert set(view.groups) == {"employees", "hr_admins"}
    # Coarse columns match an admin at internal clearance. No rule does.
    principal = Principal("ada", ("hr_admins",), "internal", "people")
    assert allows(view, principal)[0] is False
