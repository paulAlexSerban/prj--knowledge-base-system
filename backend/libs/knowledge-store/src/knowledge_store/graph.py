"""Derived graph. Postgres is authoritative; these writes are idempotent merges.

HR writes Person -BELONGS_TO-> Team and a Policy node. A Deal -DEPENDS_ON-> Epic
edge is written when either side is seen, and dropped when either side is deleted.
An Epic -OWNED_BY-> Team edge uses the HR team key. A team is removed only when
nothing belongs to it and nothing is owned by it.
Each content node stores acl_groups, acl_departments, min_clearance, and acl_rules.
"""

import json

from neomodel import db

from knowledge_store.acl import AclView
from knowledge_store.graph_db import bind
from knowledge_store.graph_models import (
    CONTENT_TYPES,
    NODE_TYPES,
    Deal,
    Epic,
    Person,
    Policy,
    Team,
)

TEAM_SOURCE = "hr"


def deal_fields(record: dict, version: str, source_id: str, acl: AclView) -> dict[str, object]:
    """Deal node properties. Account fields sit on the deal; there is no Account node."""
    return {
        "source_id": source_id,
        "version": version,
        "account_id": str(record.get("account_id") or ""),
        "account_name": str(record.get("account_name") or ""),
        "epic_id": str(record.get("epic_id") or ""),
        "arr": _arr(record.get("arr")),
        "quarter": str(record.get("quarter") or ""),
        "stage": str(record.get("stage") or ""),
        "product_key": str(record.get("product_key") or ""),
        **_acl_fields(acl),
    }


def epic_fields(
    record: dict, title: str, version: str, source_id: str, acl: AclView
) -> dict[str, object]:
    """Epic node properties. team is the HR team key for OWNED_BY."""
    return {
        "source_id": source_id,
        "version": version,
        "title": str(record.get("title") or title),
        "status": str(record.get("status") or ""),
        "team": str(record.get("team") or ""),
        "product_key": str(record.get("product_key") or ""),
        **_acl_fields(acl),
    }


class GraphWriter:
    def __init__(self, driver: object) -> None:
        self.driver = driver

    def ping(self) -> bool:
        """True when the Neo4j driver can verify connectivity."""
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
        """Idempotent merge for person, policy, deal, or epic. Specs are not graph nodes."""
        bind(self.driver)
        with db.write_transaction:
            if object_type == "person":
                _write_person(source_id, object_id, record, acl, version)
            elif object_type == "policy":
                _upsert(
                    Policy,
                    object_id,
                    source_id=source_id,
                    title=title,
                    version=version,
                    **_acl_fields(acl),
                )
            elif object_type == "deal":
                _write_deal(source_id, object_id, record, acl, version)
            elif object_type == "epic":
                _write_epic(source_id, object_id, record, title, acl, version)

    def set_acl(self, source_id: str, object_id: str, acl: AclView) -> None:
        """Patch ACL properties on matching content nodes. Edges stay as they are."""
        bind(self.driver)
        fields = _acl_fields(acl)
        with db.write_transaction:
            for node in _matching(source_id, object_id, CONTENT_TYPES):
                for key, value in fields.items():
                    setattr(node, key, value)
                node.save()

    def delete(self, source_id: str, object_id: str) -> None:
        """Remove the object's nodes, then drop teams and stub epics that nothing references."""
        bind(self.driver)
        with db.write_transaction:
            for node in _matching(source_id, object_id, NODE_TYPES):
                node.delete()
            _drop_orphan_teams()
            _drop_stub_epics()

    def count_node(self, source_id: str, object_id: str) -> int:
        """Remaining nodes after a delete. Non-zero is a verified failure."""
        bind(self.driver)
        with db.read_transaction:
            return len(_matching(source_id, object_id, NODE_TYPES))

    def depends_on(self, deal_id: str, epic_id: str) -> bool:
        """True when the deal-to-epic edge exists. Used by tests, not by retrieval."""
        bind(self.driver)
        with db.read_transaction:
            deal = Deal.nodes.get_or_none(node_id=deal_id)
            if deal is None:
                return False
            return any(epic.node_id == epic_id for epic in deal.depends_on.all())


def _write_person(source_id: str, object_id: str, record: dict, acl: AclView, version: str) -> None:
    """Person node and BELONGS_TO the HR team named on the record."""
    team = str(record.get("team") or "")
    person = _upsert(
        Person,
        object_id,
        source_id=source_id,
        name=str(record.get("full_name") or ""),
        email=str(record.get("email") or ""),
        role=str(record.get("role") or ""),
        version=version,
        **_acl_fields(acl),
    )
    person.belongs_to.disconnect_all()
    if team:
        linked = _upsert(Team, team, source_id=source_id)
        person.belongs_to.connect(linked)


def _write_deal(source_id: str, object_id: str, record: dict, acl: AclView, version: str) -> None:
    """Deal node and DEPENDS_ON a stub epic if that epic has not been ingested yet."""
    fields = deal_fields(record, version, source_id, acl)
    deal = _upsert(Deal, object_id, **fields)
    epic_id = str(fields["epic_id"])
    if epic_id:
        epic = Epic.nodes.get_or_none(node_id=epic_id)
        if epic is None:
            epic = Epic(node_id=epic_id, source_id=source_id).save()
        if not deal.depends_on.is_connected(epic):
            deal.depends_on.connect(epic)


def _write_epic(
    source_id: str, object_id: str, record: dict, title: str, acl: AclView, version: str
) -> None:
    """Epic node, OWNED_BY its team, and backfill DEPENDS_ON from deals that named it."""
    fields = epic_fields(record, title, version, source_id, acl)
    epic = _upsert(Epic, object_id, **fields)
    epic.owned_by.disconnect_all()
    team = str(fields["team"])
    if team:
        owner = Team.nodes.get_or_none(node_id=team)
        if owner is None:
            owner = Team(node_id=team, source_id=TEAM_SOURCE).save()
        epic.owned_by.connect(owner)
    for deal in Deal.nodes.filter(epic_id=object_id):
        if not deal.depends_on.is_connected(epic):
            deal.depends_on.connect(epic)


def _upsert(cls, node_id: str, **fields: object):
    """Create or overwrite by business key `id`. Safe to retry after a graph failure."""
    node = cls.nodes.get_or_none(node_id=node_id)
    if node is None:
        return cls(node_id=node_id, **fields).save()
    for key, value in fields.items():
        setattr(node, key, value)
    return node.save()


def _matching(source_id: str, object_id: str, kinds: tuple) -> list:
    """Nodes of the given types with this source and object id."""
    found = []
    for cls in kinds:
        found.extend(cls.nodes.filter(node_id=object_id, source_id=source_id).all())
    return found


def _drop_orphan_teams() -> None:
    """Remove a team that has no members and no owned epics."""
    for team in Team.nodes.all():
        if not team.members.all() and not team.epics.all():
            team.delete()


def _drop_stub_epics() -> None:
    """Remove an epic created only as a deal dependency, with no version and no edges left."""
    for epic in Epic.nodes.all():
        if epic.version is None and not epic.deals.all() and not epic.owned_by.all():
            epic.delete()


def _arr(value: object) -> int | None:
    """Integer ARR for the roadmap filter. Empty is stored as null."""
    if value is None or value == "":
        return None
    return int(value)


def _acl_fields(acl: AclView) -> dict[str, object]:
    """Coarse columns plus JSON rules copied onto the node for the Cypher templates."""
    return {
        "acl_groups": list(acl.groups),
        "acl_departments": list(acl.departments),
        "min_clearance": acl.min_clearance,
        "acl_rules": json.dumps(acl.as_json()),
    }
