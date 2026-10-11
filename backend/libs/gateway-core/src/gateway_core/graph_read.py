"""Read-only Cypher templates.

Neo4j Community has no roles. The session is read-only and every template
filters on acl_groups and min_clearance. That is the only graph check.
A missed predicate is not caught by the database.
"""

from neo4j import READ_ACCESS

from knowledge_store.acl import Principal, clearance_rank

LIMIT = 25

TEAMMATES = """
MATCH (p:Person {id: $id})-[:BELONGS_TO]->(t:Team)<-[:BELONGS_TO]-(other:Person)
WHERE any(g IN coalesce(p.acl_groups, []) WHERE g IN $groups)
  AND coalesce(p.min_clearance, 99) <= $clearance
  AND any(g IN coalesce(other.acl_groups, []) WHERE g IN $groups)
  AND coalesce(other.min_clearance, 99) <= $clearance
  AND other.id <> p.id
RETURN p.id AS from_id, t.id AS team_id, other.id AS to_id, other.name AS to_name
LIMIT $limit
"""

TEAM = """
MATCH (t:Team {id: $id})<-[:BELONGS_TO]-(p:Person)
WHERE any(g IN coalesce(p.acl_groups, []) WHERE g IN $groups)
  AND coalesce(p.min_clearance, 99) <= $clearance
RETURN t.id AS team_id, p.id AS person_id, p.name AS name
LIMIT $limit
"""

POLICIES = """
MATCH (p:Policy)
WHERE ($id = "" OR p.id = $id)
  AND any(g IN coalesce(p.acl_groups, []) WHERE g IN $groups)
  AND coalesce(p.min_clearance, 99) <= $clearance
RETURN p.id AS policy_id, p.title AS title, p.version AS version, p.source_id AS source_id
LIMIT $limit
"""


class GraphReader:
    def __init__(self, driver: object) -> None:
        self.driver = driver

    def related(
        self,
        kind: str,
        principal: Principal,
        entity_id: str | None,
        limit: int,
    ) -> list[dict[str, object]]:
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
            "clearance": clearance_rank(principal.clearance),
            "limit": max(1, min(limit, LIMIT)),
        }
        with self.driver.session(default_access_mode=READ_ACCESS) as session:
            rows = [record.data() for record in session.run(query, params)]
        return [_path(kind, row) for row in rows]


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
