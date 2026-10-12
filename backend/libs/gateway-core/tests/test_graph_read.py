"""A node with no ACL properties is not a result."""

import json

from gateway_core.graph_read import _access
from knowledge_store.acl import Principal

PRINCIPAL = Principal("ada", ("employees",), "internal", "engineering")
RULES = json.dumps([{"group": "employees", "department": "engineering", "clearance": "internal"}])


def test_a_row_with_no_rules_is_denied() -> None:
    access = _access(
        "team",
        {
            "team_id": "platform",
            "person_id": "emp-open",
            "name": "Open",
            "source_id": "hr",
        },
        PRINCIPAL,
    )
    assert access.allowed is False
    assert access.path == {"steps": []}


def test_query_drops_nodes_that_have_no_acl(neo4j_driver) -> None:
    from gateway_core.graph_read import GraphReader

    neo4j_driver.execute_query("MATCH (n) DETACH DELETE n")
    neo4j_driver.execute_query(
        """
        CREATE (open:Person {id: 'emp-open', name: 'Open', source_id: 'hr'})
        CREATE (t:Team {id: 'platform', source_id: 'hr'})
        CREATE (open)-[:BELONGS_TO]->(t)
        CREATE (closed:Person {
            id: 'emp-closed', name: 'Closed', source_id: 'hr',
            acl_groups: ['employees'], acl_departments: ['engineering'],
            min_clearance: 1, acl_rules: $rules
        })
        CREATE (closed)-[:BELONGS_TO]->(t)
        CREATE (:Policy {id: 'pol-open', title: 'Open', source_id: 'hr'})
        CREATE (:Policy {
            id: 'pol-closed', title: 'Closed', source_id: 'hr', version: '1',
            acl_groups: ['employees'], acl_departments: ['engineering'],
            min_clearance: 1, acl_rules: $rules
        })
        """,
        rules=RULES,
    )
    reader = GraphReader(neo4j_driver)
    teammates = reader.related("teammates", PRINCIPAL, "emp-open", 25)
    assert teammates == []
    found = reader.related("teammates", PRINCIPAL, "emp-closed", 25)
    assert [item.object_id for item in found] == []
    policies = {item.object_id for item in reader.related("policies", PRINCIPAL, None, 25)}
    assert policies == {"pol-closed"}
