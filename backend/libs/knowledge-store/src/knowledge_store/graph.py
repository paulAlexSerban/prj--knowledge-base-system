"""Derived graph. Postgres is authoritative; these writes are idempotent merges.

HR writes Person -BELONGS_TO-> Team and a Policy node. A Deal -DEPENDS_ON-> Epic
edge is written when either side is seen, and dropped when either side is deleted.
"""

from knowledge_store.acl import AclView

PERSON = """
MERGE (p:Person {id: $id})
SET p.source_id = $source_id, p.name = $name, p.email = $email, p.role = $role,
    p.version = $version, p.acl_groups = $groups, p.min_clearance = $min_clearance
WITH p
OPTIONAL MATCH (p)-[old:BELONGS_TO]->(:Team)
DELETE old
"""

LINK_TEAM = """
MATCH (p:Person {id: $id})
MERGE (t:Team {id: $team})
SET t.source_id = $source_id
MERGE (p)-[:BELONGS_TO]->(t)
"""

POLICY = """
MERGE (p:Policy {id: $id})
SET p.source_id = $source_id, p.title = $title, p.version = $version,
    p.acl_groups = $groups, p.min_clearance = $min_clearance
"""

DEAL = """
MERGE (d:Deal {id: $id})
SET d.source_id = $source_id, d.version = $version, d.account_id = $account_id,
    d.epic_id = $epic_id, d.acl_groups = $groups, d.min_clearance = $min_clearance
"""

LINK_EPIC = """
MATCH (d:Deal {id: $id})
MERGE (e:Epic {id: $epic_id})
ON CREATE SET e.source_id = $source_id
MERGE (d)-[:DEPENDS_ON]->(e)
"""

EPIC = """
MERGE (e:Epic {id: $id})
SET e.source_id = $source_id, e.title = $title, e.status = $status, e.version = $version,
    e.acl_groups = $groups, e.min_clearance = $min_clearance
"""

LINK_DEALS = """
MATCH (e:Epic {id: $id})
MATCH (d:Deal)
WHERE d.epic_id = $id
MERGE (d)-[:DEPENDS_ON]->(e)
"""

DELETE = """
MATCH (n)
WHERE n.source_id = $source_id AND n.id = $object_id
DETACH DELETE n
"""

ORPHAN_TEAMS = """
MATCH (t:Team)
WHERE NOT ()-[:BELONGS_TO]->(t)
DETACH DELETE t
"""

COUNT = """
MATCH (n)
WHERE n.source_id = $source_id AND n.id = $object_id
RETURN count(n) AS n
"""

ACL = """
MATCH (n)
WHERE n.source_id = $source_id AND n.id = $object_id
SET n.acl_groups = $groups, n.min_clearance = $min_clearance
"""

DEPENDS = """
MATCH (d:Deal {id: $deal_id})-[:DEPENDS_ON]->(e:Epic {id: $epic_id})
RETURN count(*) AS n
"""


class GraphWriter:
    def __init__(self, driver: object) -> None:
        self.driver = driver

    def ping(self) -> bool:
        try:
            self.driver.verify_connectivity()
        except Exception:  # noqa: BLE001 - the driver raises several connection errors
            return False
        return True

    def write(
        self,
        source_id: str,
        object_type: str,
        object_id: str,
        record: dict,
        title: str,
        acl: AclView,
        version: str,
    ) -> None:
        common = {
            "source_id": source_id,
            "id": object_id,
            "version": version,
            "groups": list(acl.groups),
            "min_clearance": acl.min_clearance,
        }
        if object_type == "person":
            team = str(record.get("team") or "")
            self._run(
                PERSON,
                **common,
                name=str(record.get("full_name") or ""),
                email=str(record.get("email") or ""),
                role=str(record.get("role") or ""),
            )
            if team:
                self._run(LINK_TEAM, id=object_id, team=team, source_id=source_id)
        elif object_type == "policy":
            self._run(POLICY, **common, title=title)
        elif object_type == "deal":
            epic_id = str(record.get("epic_id") or "")
            self._run(
                DEAL,
                **common,
                account_id=str(record.get("account_id") or ""),
                epic_id=epic_id,
            )
            if epic_id:
                self._run(LINK_EPIC, id=object_id, epic_id=epic_id, source_id=source_id)
        elif object_type == "epic":
            self._run(
                EPIC,
                **common,
                title=str(record.get("title") or title),
                status=str(record.get("status") or ""),
            )
            self._run(LINK_DEALS, id=object_id)

    def set_acl(self, source_id: str, object_id: str, acl: AclView) -> None:
        self._run(
            ACL,
            source_id=source_id,
            object_id=object_id,
            groups=list(acl.groups),
            min_clearance=acl.min_clearance,
        )

    def delete(self, source_id: str, object_id: str) -> None:
        self._run(DELETE, source_id=source_id, object_id=object_id)
        self._run(ORPHAN_TEAMS)

    def count_node(self, source_id: str, object_id: str) -> int:
        record = self._one(COUNT, source_id=source_id, object_id=object_id)
        return int(record["n"]) if record else 0

    def depends_on(self, deal_id: str, epic_id: str) -> bool:
        record = self._one(DEPENDS, deal_id=deal_id, epic_id=epic_id)
        return bool(record and record["n"])

    def _run(self, query: str, **params: object) -> None:
        self.driver.execute_query(query, **params)

    def _one(self, query: str, **params: object):
        records, _, _ = self.driver.execute_query(query, **params)
        return records[0] if records else None
