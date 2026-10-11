"""Map a source's native ACL onto the coarse columns and the exact rules.

An unmapped native value adds no grant. If nothing maps, the object is stored
and nobody can read it. Clearance order is public < internal < confidential <
restricted. A mapping that omits clearance is treated as public.
"""

from dataclasses import dataclass

from shared.contracts.source_contract import SourceContract

CLEARANCE = ("public", "internal", "confidential", "restricted")
CLOSED = 99


@dataclass(frozen=True)
class AclView:
    groups: tuple[str, ...]
    departments: tuple[str, ...]
    min_clearance: int
    rules: tuple[dict[str, str], ...]
    unmapped: tuple[str, ...]

    def as_json(self) -> list[dict[str, str]]:
        return [dict(rule) for rule in self.rules]


def clearance_rank(label: str) -> int:
    try:
        return CLEARANCE.index(label)
    except ValueError:
        return -1


def map_acl(native: list[str], contract: SourceContract) -> AclView:
    by_native = {rule.native: rule for rule in contract.acl_mapping}
    rules: list[dict[str, str]] = []
    unmapped: list[str] = []
    for name in native:
        rule = by_native.get(name)
        if rule is None:
            unmapped.append(name)
            continue
        rules.append({key.value: value for key, value in rule.attributes.items()})
    if not rules:
        return AclView((), (), CLOSED, (), tuple(sorted(unmapped)))
    groups = tuple(sorted({rule["group"] for rule in rules if rule.get("group")}))
    departments = tuple(sorted({rule["department"] for rule in rules if rule.get("department")}))
    ranks = [clearance_rank(rule.get("clearance", "public")) for rule in rules]
    return AclView(groups, departments, min(ranks), tuple(rules), tuple(sorted(unmapped)))


@dataclass(frozen=True)
class Principal:
    subject: str
    groups: tuple[str, ...]
    clearance: str
    department: str


def allows(view: AclView, principal: Principal) -> tuple[bool, dict[str, str] | None]:
    """Fine check. The coarse columns can be wider than any single rule."""
    rank = clearance_rank(principal.clearance)
    groups = set(principal.groups)
    for rule in view.rules:
        group = rule.get("group")
        department = rule.get("department")
        needed = clearance_rank(rule.get("clearance", "public"))
        if group and group not in groups:
            continue
        if department and department != principal.department:
            continue
        if needed > rank:
            continue
        return True, rule
    return False, None
