"""A node with no ACL properties is not a result."""

import json

import pytest

from gateway_core.graph_read import (
    DEFAULT_MIN_ARR,
    DEFAULT_QUARTER,
    TEMPLATES,
    GraphReader,
    _access,
)
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


ROADMAP_RULES = json.dumps(
    [
        {"group": "sales_team", "department": "sales", "clearance": "internal"},
        {"group": "roadmap", "department": "*", "clearance": "internal"},
    ]
)
EPIC_RULES = json.dumps(
    [
        {"group": "eng_project", "department": "engineering", "clearance": "internal"},
        {"group": "roadmap", "department": "*", "clearance": "internal"},
    ]
)
LEAD_RULES = json.dumps(
    [{"group": "sales_leadership", "department": "sales", "clearance": "confidential"}]
)
LEAD = Principal("product-lead", ("employees", "roadmap"), "internal", "product")


def test_bad_quarter_and_negative_arr_are_rejected() -> None:
    reader = GraphReader(object())
    with pytest.raises(ValueError, match="quarter"):
        reader.related("roadmap_deals", LEAD, None, 25, quarter="Q3")
    with pytest.raises(ValueError, match="min_arr"):
        reader.related("roadmap_deals", LEAD, None, 25, min_arr=-1)
    with pytest.raises(ValueError, match="min_arr"):
        reader.related("roadmap_deals", LEAD, None, 25, min_arr=True)


def test_templates_drop_nodes_without_acl_rules() -> None:
    assert set(TEMPLATES) == {"teammates", "team", "policies", "roadmap_deals"}
    for query in TEMPLATES.values():
        assert "acl_rules IS NOT NULL" in query
        assert "acl_groups" in query


def test_roadmap_deals_keeps_only_the_authorized_q3_paths(neo4j_driver) -> None:
    neo4j_driver.execute_query("MATCH (n) DETACH DELETE n")
    neo4j_driver.execute_query(
        """
        CREATE (t:Team {id: 'payments'})
        CREATE (openEpic:Epic {
            id: 'epic-checkout-retry', title: 'Retry', status: 'in_progress',
            version: '1', source_id: 'engineering',
            acl_groups: ['eng_project', 'roadmap'], acl_departments: [],
            min_clearance: 1, acl_rules: $epic
        })-[:OWNED_BY]->(t)
        CREATE (done:Epic {
            id: 'epic-checkout-wallets', title: 'Wallets', status: 'released',
            version: '1', source_id: 'engineering',
            acl_groups: ['roadmap'], acl_departments: [],
            min_clearance: 1, acl_rules: $epic
        })-[:OWNED_BY]->(t)
        CREATE (:Deal {
            id: 'deal-northwind-q3', account_name: 'Northwind Traders', arr: 480000,
            quarter: '2026-Q3', version: '1', source_id: 'sales', epic_id: 'epic-checkout-retry',
            acl_groups: ['roadmap', 'sales_team'], acl_departments: [],
            min_clearance: 1, acl_rules: $open
        })-[:DEPENDS_ON]->(openEpic)
        CREATE (:Deal {
            id: 'deal-contoso-q3', account_name: 'Contoso', arr: 920000,
            quarter: '2026-Q3', version: '1', source_id: 'sales',
            acl_groups: ['sales_leadership'], acl_departments: ['sales'],
            min_clearance: 2, acl_rules: $lead
        })-[:DEPENDS_ON]->(openEpic)
        CREATE (:Deal {
            id: 'deal-woodgrove-q3', account_name: 'Woodgrove Bank', arr: 700000,
            quarter: '2026-Q3', version: '1', source_id: 'sales',
            acl_groups: ['roadmap'], acl_departments: [],
            min_clearance: 1, acl_rules: $open
        })-[:DEPENDS_ON]->(done)
        CREATE (:Deal {
            id: 'deal-graphic-q3', account_name: 'Graphic', arr: 45000,
            quarter: '2026-Q3', version: '1', source_id: 'sales',
            acl_groups: ['roadmap'], acl_departments: [],
            min_clearance: 1, acl_rules: $open
        })-[:DEPENDS_ON]->(openEpic)
        CREATE (:Deal {
            id: 'deal-coho-q2', account_name: 'Coho', arr: 800000,
            quarter: '2026-Q2', version: '1', source_id: 'sales',
            acl_groups: ['roadmap'], acl_departments: [],
            min_clearance: 1, acl_rules: $open
        })-[:DEPENDS_ON]->(openEpic)
        CREATE (:Deal {
            id: 'deal-proseware-q3', account_name: 'Proseware', arr: 300000,
            quarter: '2026-Q3', version: '1', source_id: 'sales',
            acl_groups: ['roadmap'], acl_departments: [],
            min_clearance: 1, acl_rules: $open
        })
        """,
        open=ROADMAP_RULES,
        epic=EPIC_RULES,
        lead=LEAD_RULES,
    )
    reader = GraphReader(neo4j_driver)
    found = reader.related("roadmap_deals", LEAD, None, 25)
    assert [item.object_id for item in found] == ["deal-northwind-q3"]
    assert found[0].allowed
    assert found[0].source_id == "sales"
    assert found[0].rule is not None
    assert found[0].rule["group"] == "roadmap"
    steps = found[0].path["steps"]
    assert steps[0]["account_name"] == "Northwind Traders"
    assert steps[0]["arr"] == 480_000
    assert steps[1]["id"] == "epic-checkout-retry"
    assert steps[1]["status"] == "in_progress"
    assert steps[2] == {"type": "Team", "id": "payments", "relationship": "OWNED_BY"}
    earlier = reader.related("roadmap_deals", LEAD, None, 25, quarter="2026-Q2")
    assert [item.object_id for item in earlier if item.allowed] == ["deal-coho-q2"]
    smaller = reader.related("roadmap_deals", LEAD, None, 25, min_arr=500_000)
    assert smaller == []
    engineer = Principal("employee-eng", ("employees",), "internal", "engineering")
    assert reader.related("roadmap_deals", engineer, None, 25) == []
    outsider = Principal("outsider", (), "internal", "engineering")
    assert reader.related("roadmap_deals", outsider, None, 25) == []
    assert DEFAULT_QUARTER == "2026-Q3"
    assert DEFAULT_MIN_ARR == 200_000
