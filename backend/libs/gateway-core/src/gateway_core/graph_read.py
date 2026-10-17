"""Read-only graph templates.

Neo4j Community has no roles. Each template filters on acl_groups,
acl_departments, and min_clearance, and drops a node whose acl_rules property
is missing. The service then checks the rules, because one node can carry more
than one rule. A missed predicate is not caught by the database. The gateway
and the worker share the neo4j user.

neomodel NodeSet cannot express "any array element is in this list" on the
ACL properties, so these four templates run through db.cypher_query inside a
read transaction. The labels and relationship types come from the node
models. Writes use the OGM. There is no open Cypher from the agent.
"""

import re
from dataclasses import dataclass

from neomodel import db

from knowledge_store.acl import Principal, allows, clearance_rank, view_from_rules
from knowledge_store.graph_db import bind
from knowledge_store.graph_models import (
    BELONGS_TO,
    DEPENDS_ON,
    OWNED_BY,
    Deal,
    Epic,
    Person,
    Policy,
    Team,
)

LIMIT = 25
# Same bar as the sales harness. A deal at this ARR in the default quarter is "high".
DEFAULT_QUARTER = "2026-Q3"
DEFAULT_MIN_ARR = 200_000
_QUARTER = re.compile(r"^\d{4}-Q[1-4]$")

_DEPARTMENT = """
  AND (
    size(coalesce({alias}.acl_departments, [])) = 0
    OR $department IN {alias}.acl_departments
  )
"""


def _templates() -> dict[str, str]:
    """Four bounded Cypher templates. Labels come from the node models."""
    person = Person.__label__
    team = Team.__label__
    policy = Policy.__label__
    deal = Deal.__label__
    epic = Epic.__label__
    teammates = (
        f"""
MATCH (p:{person} {{id: $id}})-[:{BELONGS_TO}]->(t:{team})<-[:{BELONGS_TO}]-(other:{person})
WHERE p.acl_rules IS NOT NULL
  AND any(g IN coalesce(p.acl_groups, []) WHERE g IN $groups)
  AND coalesce(p.min_clearance, 99) <= $clearance
"""
        + _DEPARTMENT.format(alias="p")
        + """
  AND other.acl_rules IS NOT NULL
  AND any(g IN coalesce(other.acl_groups, []) WHERE g IN $groups)
  AND coalesce(other.min_clearance, 99) <= $clearance
"""
        + _DEPARTMENT.format(alias="other")
        + """
  AND other.id <> p.id
RETURN p.id AS from_id, t.id AS team_id, other.id AS to_id, other.name AS to_name,
       p.acl_rules AS from_rules, other.acl_rules AS to_rules,
       coalesce(other.source_id, p.source_id, '') AS source_id
LIMIT $limit
"""
    )
    team_query = (
        f"""
MATCH (t:{team} {{id: $id}})<-[:{BELONGS_TO}]-(p:{person})
WHERE p.acl_rules IS NOT NULL
  AND any(g IN coalesce(p.acl_groups, []) WHERE g IN $groups)
  AND coalesce(p.min_clearance, 99) <= $clearance
"""
        + _DEPARTMENT.format(alias="p")
        + """
RETURN t.id AS team_id, p.id AS person_id, p.name AS name,
       p.acl_rules AS acl_rules, coalesce(p.source_id, '') AS source_id
LIMIT $limit
"""
    )
    policies = (
        f"""
MATCH (p:{policy})
WHERE ($id = "" OR p.id = $id)
  AND p.acl_rules IS NOT NULL
  AND any(g IN coalesce(p.acl_groups, []) WHERE g IN $groups)
  AND coalesce(p.min_clearance, 99) <= $clearance
"""
        + _DEPARTMENT.format(alias="p")
        + """
RETURN p.id AS policy_id, p.title AS title, p.version AS version,
       p.source_id AS source_id, p.acl_rules AS acl_rules
LIMIT $limit
"""
    )
    # The team is only a key. It has no ACL. Both the deal and the epic do.
    roadmap = (
        f"""
MATCH (d:{deal})-[:{DEPENDS_ON}]->(e:{epic})-[:{OWNED_BY}]->(t:{team})
WHERE d.quarter = $quarter
  AND d.arr >= $min_arr
  AND e.status <> 'released'
  AND d.acl_rules IS NOT NULL
  AND any(g IN coalesce(d.acl_groups, []) WHERE g IN $groups)
  AND coalesce(d.min_clearance, 99) <= $clearance
"""
        + _DEPARTMENT.format(alias="d")
        + """
  AND e.acl_rules IS NOT NULL
  AND any(g IN coalesce(e.acl_groups, []) WHERE g IN $groups)
  AND coalesce(e.min_clearance, 99) <= $clearance
"""
        + _DEPARTMENT.format(alias="e")
        + """
RETURN d.id AS deal_id, d.account_name AS account_name, d.arr AS arr,
       d.quarter AS quarter, d.version AS deal_version,
       e.id AS epic_id, e.title AS epic_title, e.status AS epic_status,
       e.version AS epic_version, t.id AS team_id,
       d.acl_rules AS deal_rules, e.acl_rules AS epic_rules,
       coalesce(d.source_id, '') AS source_id
ORDER BY d.id
LIMIT $limit
"""
    )
    return {
        "teammates": teammates,
        "team": team_query,
        "policies": policies,
        "roadmap_deals": roadmap,
    }


TEMPLATES = _templates()


@dataclass(frozen=True)
class GraphAccess:
    path: dict[str, object]
    source_id: str
    object_id: str
    allowed: bool
    rule: dict[str, str] | None


class GraphReader:
    def __init__(self, driver: object) -> None:
        self.driver = driver

    def related(
        self,
        kind: str,
        principal: Principal,
        entity_id: str | None,
        limit: int,
        quarter: str | None = None,
        min_arr: int | None = None,
    ) -> list[GraphAccess]:
        """Read-only template plus a fine-grained rule check. Empty groups see nothing."""
        if kind in ("teammates", "team") and not entity_id:
            raise ValueError("entity_id is required")
        query = TEMPLATES.get(kind)
        if query is None:
            raise ValueError("unknown kind")
        resolved_quarter, resolved_arr = _bounds(quarter, min_arr)
        if not principal.groups:
            return []
        params = {
            "id": entity_id or "",
            "groups": list(principal.groups),
            "department": principal.department,
            "clearance": clearance_rank(principal.clearance),
            "limit": max(1, min(limit, LIMIT)),
        }
        if kind == "roadmap_deals":
            params["quarter"] = resolved_quarter
            params["min_arr"] = resolved_arr
        bind(self.driver)
        with db.read_transaction:
            results, meta = db.cypher_query(query, params)
        rows = [dict(zip(meta, row, strict=True)) for row in results]
        return [_access(kind, row, principal) for row in rows]


def _bounds(quarter: str | None, min_arr: int | None) -> tuple[str, int]:
    """Default 2026-Q3 and 200000 ARR. Invalid values are a client error, not a fallback."""
    resolved_quarter = DEFAULT_QUARTER if quarter is None else quarter
    if _QUARTER.fullmatch(resolved_quarter) is None:
        raise ValueError("quarter")
    if min_arr is None:
        return resolved_quarter, DEFAULT_MIN_ARR
    if isinstance(min_arr, bool) or not isinstance(min_arr, int) or min_arr < 0:
        raise ValueError("min_arr")
    return resolved_quarter, min_arr


def _access(kind: str, row: dict, principal: Principal) -> GraphAccess:
    """Post-check every node on the path. One failed rule denies the whole path."""
    # On a deny, object_id is the node whose rule failed, so the audit row names it.
    if kind == "teammates":
        from_ok, _from_rule = allows(view_from_rules(row.get("from_rules")), principal)
        to_ok, to_rule = allows(view_from_rules(row.get("to_rules")), principal)
        allowed = from_ok and to_ok
        object_id = str(row["to_id"] if allowed or not to_ok else row["from_id"])
        rule = to_rule if allowed else None
    elif kind == "roadmap_deals":
        deal_ok, deal_rule = allows(view_from_rules(row.get("deal_rules")), principal)
        epic_ok, epic_rule = allows(view_from_rules(row.get("epic_rules")), principal)
        allowed = deal_ok and epic_ok
        object_id = str(row["epic_id"] if deal_ok and not epic_ok else row["deal_id"])
        rule = deal_rule if allowed else None
        if allowed:
            row = {**row, "deal_rule": deal_rule, "epic_rule": epic_rule}
    else:
        allowed, matched = allows(view_from_rules(row.get("acl_rules")), principal)
        object_id = str(row["person_id"] if kind == "team" else row["policy_id"])
        rule = matched if allowed else None
    source_id = str(row.get("source_id") or "")
    path = _path(kind, row) if allowed else {"steps": []}
    return GraphAccess(path, source_id, object_id, allowed, rule)


def _path(kind: str, row: dict) -> dict[str, object]:
    """Steps the agent cites. Teams have no ACL; deals and epics do."""
    if kind == "teammates":
        steps = [
            {"type": "Person", "id": row["from_id"]},
            {"type": "Team", "id": row["team_id"], "relationship": BELONGS_TO},
            {
                "type": "Person",
                "id": row["to_id"],
                "name": row["to_name"],
                "relationship": BELONGS_TO,
            },
        ]
    elif kind == "roadmap_deals":
        steps = [
            {
                "type": "Deal",
                "id": row["deal_id"],
                "account_name": row.get("account_name") or "",
                "arr": row["arr"],
                "quarter": row["quarter"],
                "version": row.get("deal_version"),
                "rule": row.get("deal_rule"),
            },
            {
                "type": "Epic",
                "id": row["epic_id"],
                "title": row.get("epic_title") or "",
                "status": row.get("epic_status") or "",
                "version": row.get("epic_version"),
                "relationship": DEPENDS_ON,
                "rule": row.get("epic_rule"),
            },
            {"type": "Team", "id": row["team_id"], "relationship": OWNED_BY},
        ]
    elif kind == "team":
        steps = [
            {"type": "Team", "id": row["team_id"]},
            {
                "type": "Person",
                "id": row["person_id"],
                "name": row["name"],
                "relationship": BELONGS_TO,
            },
        ]
    else:
        steps = [
            {
                "type": "Policy",
                "id": row["policy_id"],
                "title": row["title"],
                "version": row["version"],
                "source_id": row["source_id"],
            }
        ]
    return {"steps": steps}
