"""Read-only Cypher templates.

Neo4j Community has no roles. The session is read-only. Each template filters
on acl_groups, acl_departments, and min_clearance. The service then checks
acl_rules, because one node can carry more than one rule. A missed predicate
is not caught by the database. The gateway and the worker share the neo4j user.
"""

from dataclasses import dataclass

from neo4j import READ_ACCESS

from knowledge_store.acl import Principal, allows, clearance_rank, view_from_rules

LIMIT = 25

_DEPARTMENT = """
  AND (
    size(coalesce({alias}.acl_departments, [])) = 0
    OR $department IN {alias}.acl_departments
  )
"""

TEAMMATES = """
MATCH (p:Person {id: $id})-[:BELONGS_TO]->(t:Team)<-[:BELONGS_TO]-(other:Person)
WHERE any(g IN coalesce(p.acl_groups, []) WHERE g IN $groups)
  AND coalesce(p.min_clearance, 99) <= $clearance
""" + _DEPARTMENT.format(alias="p") + """
  AND any(g IN coalesce(other.acl_groups, []) WHERE g IN $groups)
  AND coalesce(other.min_clearance, 99) <= $clearance
""" + _DEPARTMENT.format(alias="other") + """
  AND other.id <> p.id
RETURN p.id AS from_id, t.id AS team_id, other.id AS to_id, other.name AS to_name,
       p.acl_rules AS from_rules, other.acl_rules AS to_rules,
       coalesce(other.source_id, p.source_id, '') AS source_id
LIMIT $limit
"""

TEAM = """
MATCH (t:Team {id: $id})<-[:BELONGS_TO]-(p:Person)
WHERE any(g IN coalesce(p.acl_groups, []) WHERE g IN $groups)
  AND coalesce(p.min_clearance, 99) <= $clearance
""" + _DEPARTMENT.format(alias="p") + """
RETURN t.id AS team_id, p.id AS person_id, p.name AS name,
       p.acl_rules AS acl_rules, coalesce(p.source_id, '') AS source_id
LIMIT $limit
"""

POLICIES = """
MATCH (p:Policy)
WHERE ($id = "" OR p.id = $id)
  AND any(g IN coalesce(p.acl_groups, []) WHERE g IN $groups)
  AND coalesce(p.min_clearance, 99) <= $clearance
""" + _DEPARTMENT.format(alias="p") + """
RETURN p.id AS policy_id, p.title AS title, p.version AS version,
       p.source_id AS source_id, p.acl_rules AS acl_rules
LIMIT $limit
"""


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
    ) -> list[GraphAccess]:
        if kind in ("teammates", "team") and not entity_id:
            raise ValueError("entity_id is required")
        query = {"teammates": TEAMMATES, "team": TEAM, "policies": POLICIES}.get(kind)
        if query is None:
            raise ValueError("unknown kind")
        if not principal.groups:
            return []
        params = {
            "id": entity_id or "",
            "groups": list(principal.groups),
            "department": principal.department,
            "clearance": clearance_rank(principal.clearance),
            "limit": max(1, min(limit, LIMIT)),
        }
        with self.driver.session(default_access_mode=READ_ACCESS) as session:
            rows = [record.data() for record in session.run(query, params)]
        return [_access(kind, row, principal) for row in rows]


def _access(kind: str, row: dict, principal: Principal) -> GraphAccess:
    if kind == "teammates":
        from_ok, _from_rule = allows(view_from_rules(row.get("from_rules")), principal)
        to_ok, to_rule = allows(view_from_rules(row.get("to_rules")), principal)
        allowed = from_ok and to_ok
        object_id = str(row["to_id"] if allowed or not to_ok else row["from_id"])
        rule = to_rule if allowed else None
    else:
        allowed, matched = allows(view_from_rules(row.get("acl_rules")), principal)
        object_id = str(row["person_id"] if kind == "team" else row["policy_id"])
        rule = matched if allowed else None
    source_id = str(row.get("source_id") or "")
    path = _path(kind, row) if allowed else {"steps": []}
    return GraphAccess(path, source_id, object_id, allowed, rule)


def _path(kind: str, row: dict) -> dict[str, object]:
    if kind == "teammates":
        steps = [
            {"type": "Person", "id": row["from_id"]},
            {"type": "Team", "id": row["team_id"], "relationship": "BELONGS_TO"},
            {
                "type": "Person",
                "id": row["to_id"],
                "name": row["to_name"],
                "relationship": "BELONGS_TO",
            },
        ]
    elif kind == "team":
        steps = [
            {"type": "Team", "id": row["team_id"]},
            {
                "type": "Person",
                "id": row["person_id"],
                "name": row["name"],
                "relationship": "BELONGS_TO",
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
