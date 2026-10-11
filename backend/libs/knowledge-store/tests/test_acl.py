"""ACL mapping and the fine check. No database."""

from knowledge_store.acl import Principal, allows, map_acl
from shared.contracts.validate import load_contracts


def _hr():
    from pathlib import Path

    root = Path(__file__).resolve().parents[4]
    return next(item for item in load_contracts(root / "infrastructure/config/sources") if item.source_id == "hr")


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


def test_unmapped_native_acl_grants_nothing() -> None:
    view = map_acl(["not-a-role"], _hr())
    assert view.groups == ()
    assert view.min_clearance == 99
    assert view.rules == ()
    principal = Principal("ada", ("employees",), "restricted", "people")
    assert allows(view, principal) == (False, None)


def test_fine_check_rejects_a_mix_that_no_single_rule_allows() -> None:
    view = map_acl(["hr_employee", "hr_policy_admin"], _hr())
    assert view.min_clearance == 1
    assert view.departments == ()
    assert set(view.groups) == {"employees", "hr_admins"}
    # Coarse columns match an admin at internal clearance. No rule does.
    principal = Principal("ada", ("hr_admins",), "internal", "people")
    assert allows(view, principal)[0] is False
