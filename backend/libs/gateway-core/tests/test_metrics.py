"""Permission-group labels stay coarse and keep roadmap apart from employees."""

from gateway_core.metrics import permission_group
from knowledge_store.acl import Principal


def test_roadmap_outranks_employees_and_an_empty_principal_is_none() -> None:
    lead = Principal("product-lead", ("employees", "roadmap"), "internal", "product")
    employee = Principal("ada", ("employees",), "internal", "engineering")
    admin = Principal("cam", ("employees", "hr_admins"), "confidential", "people")
    outsider = Principal("out", (), "internal", "engineering")
    assert permission_group(lead) == "roadmap"
    assert permission_group(employee) == "employees"
    assert permission_group(admin) == "hr_admins"
    assert permission_group(outsider) == "none"
