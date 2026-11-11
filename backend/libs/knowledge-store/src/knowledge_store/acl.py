"""Map a source's native ACL onto the coarse columns and the exact rules.

An unmapped native value adds no grant. If nothing maps, the object is stored
and nobody can read it. Clearance order is public < internal < confidential <
restricted. Every rule needs a group, a clearance, and a department. A
department of "*" grants every department and leaves the coarse department
list empty. A rule that omits any of the three does not grant.
"""

import json
from dataclasses import dataclass

from shared.contracts.source_contract import CLEARANCE_LABELS, SourceContract

CLEARANCE = CLEARANCE_LABELS
CLOSED = 99
ANY_DEPARTMENT = "*"


@dataclass(frozen=True)
class AclView:
    groups: tuple[str, ...]
    departments: tuple[str, ...]
    min_clearance: int
    rules: tuple[dict[str, str], ...]
    unmapped: tuple[str, ...]

    def as_json(self) -> list[dict[str, str]]:
        """Exact rules stored on the chunk and the graph node."""
        return [dict(rule) for rule in self.rules]


def clearance_rank(label: str) -> int:
    """Index in public < internal < confidential < restricted. Unknown is -1."""
    try:
        return CLEARANCE.index(label)
    except ValueError:
        return -1


def map_acl(native: list[str], contract: SourceContract) -> AclView:
    """Native labels to coarse columns plus exact rules. Unmapped values grant nothing."""
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
    complete = [rule for rule in rules if _complete(rule)]
    if not complete:
        return AclView((), (), CLOSED, tuple(rules), tuple(sorted(unmapped)))
    groups = tuple(sorted({rule["group"] for rule in complete}))
    # "*" opens the object to every department. The coarse column must not
    # be narrower than that rule.
    if any(rule["department"] == ANY_DEPARTMENT for rule in complete):
        departments: tuple[str, ...] = ()
    else:
        departments = tuple(sorted({rule["department"] for rule in complete}))
    minimum = min(clearance_rank(rule["clearance"]) for rule in complete)
    return AclView(groups, departments, minimum, tuple(rules), tuple(sorted(unmapped)))


def view_from_rules(raw: object) -> AclView:
    """Exact rules as stored on a chunk or a graph node. Coarse columns are unused."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            raw = []
    if not isinstance(raw, list):
        raw = []
    rules = tuple(dict(rule) for rule in raw if isinstance(rule, dict))
    return AclView((), (), CLOSED, rules, ())


@dataclass(frozen=True)
class Principal:
    subject: str
    groups: tuple[str, ...]
    clearance: str
    department: str


def allows(view: AclView, principal: Principal) -> tuple[bool, dict[str, str] | None]:
    """One rule must match group, department, and clearance together."""
    rank = clearance_rank(principal.clearance)
    if rank < 0 or not principal.department:
        return False, None
    groups = set(principal.groups)
    for rule in view.rules:
        group = rule.get("group") or ""
        department = rule.get("department") or ""
        needed = clearance_rank(rule.get("clearance") or "")
        if not group or group not in groups:
            continue
        if department != ANY_DEPARTMENT and department != principal.department:
            continue
        if needed < 0 or needed > rank:
            continue
        return True, rule
    return False, None


def _complete(rule: dict[str, str]) -> bool:
    """A grant needs group, department, and a known clearance together."""
    group = rule.get("group") or ""
    department = rule.get("department") or ""
    return bool(group and department and clearance_rank(rule.get("clearance") or "") >= 0)
