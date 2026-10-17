"""Neo4j node types. The business key is stored as `id`.

neomodel reserves the Python name `id`, so the attribute is `node_id` and
`db_property` maps it. Account and Spec are not nodes. Constraints are applied
by database/neo4j/init.cypher, not by install_labels.
"""

from neomodel import (
    ArrayProperty,
    IntegerProperty,
    RelationshipFrom,
    RelationshipTo,
    StringProperty,
    StructuredNode,
)

BELONGS_TO = "BELONGS_TO"
DEPENDS_ON = "DEPENDS_ON"
OWNED_BY = "OWNED_BY"


def _key() -> StringProperty:
    """Business key stored as Neo4j `id`. Python uses node_id because neomodel reserves id."""
    return StringProperty(unique_index=True, required=True, db_property="id")


class Team(StructuredNode):
    node_id = _key()
    source_id = StringProperty()
    members = RelationshipFrom("Person", BELONGS_TO)
    epics = RelationshipFrom("Epic", OWNED_BY)


class Person(StructuredNode):
    node_id = _key()
    source_id = StringProperty()
    name = StringProperty()
    email = StringProperty()
    role = StringProperty()
    version = StringProperty()
    acl_groups = ArrayProperty(StringProperty())
    acl_departments = ArrayProperty(StringProperty())
    min_clearance = IntegerProperty()
    acl_rules = StringProperty()
    belongs_to = RelationshipTo("Team", BELONGS_TO)


class Policy(StructuredNode):
    node_id = _key()
    source_id = StringProperty()
    title = StringProperty()
    version = StringProperty()
    acl_groups = ArrayProperty(StringProperty())
    acl_departments = ArrayProperty(StringProperty())
    min_clearance = IntegerProperty()
    acl_rules = StringProperty()


class Epic(StructuredNode):
    node_id = _key()
    source_id = StringProperty()
    title = StringProperty()
    status = StringProperty()
    version = StringProperty()
    team = StringProperty()
    product_key = StringProperty()
    acl_groups = ArrayProperty(StringProperty())
    acl_departments = ArrayProperty(StringProperty())
    min_clearance = IntegerProperty()
    acl_rules = StringProperty()
    owned_by = RelationshipTo("Team", OWNED_BY)
    deals = RelationshipFrom("Deal", DEPENDS_ON)


class Deal(StructuredNode):
    node_id = _key()
    source_id = StringProperty()
    version = StringProperty()
    account_id = StringProperty()
    account_name = StringProperty()
    epic_id = StringProperty()
    arr = IntegerProperty()
    quarter = StringProperty()
    stage = StringProperty()
    product_key = StringProperty()
    acl_groups = ArrayProperty(StringProperty())
    acl_departments = ArrayProperty(StringProperty())
    min_clearance = IntegerProperty()
    acl_rules = StringProperty()
    depends_on = RelationshipTo("Epic", DEPENDS_ON)


CONTENT_TYPES = (Person, Policy, Deal, Epic)
NODE_TYPES = (*CONTENT_TYPES, Team)
