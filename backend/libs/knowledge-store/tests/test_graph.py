"""Deal and epic properties, and the team edge that must survive a person delete."""

from knowledge_store.acl import AclView
from knowledge_store.graph import TEAM_SOURCE, GraphWriter, deal_fields, epic_fields
from knowledge_store.graph_models import BELONGS_TO, OWNED_BY, Epic, Person, Team

ACL = AclView(
    ("sales_team",),
    ("sales",),
    1,
    ({"group": "sales_team", "department": "sales", "clearance": "internal"},),
    (),
)


def test_deal_and_epic_fields_keep_the_question_columns() -> None:
    deal = deal_fields(
        {
            "account_id": "northwind",
            "account_name": "Northwind Traders",
            "epic_id": "epic-checkout-retry",
            "arr": 480_000,
            "quarter": "Q3",
            "stage": "negotiation",
            "product_key": "checkout",
        },
        "2",
        "sales",
        ACL,
    )
    assert deal["arr"] == 480_000
    assert deal["quarter"] == "Q3"
    assert deal["stage"] == "negotiation"
    assert deal["product_key"] == "checkout"
    assert deal["epic_id"] == "epic-checkout-retry"
    assert deal["account_name"] == "Northwind Traders"
    epic = epic_fields(
        {
            "title": "Retry",
            "status": "in_progress",
            "team": "platform",
            "product_key": "checkout",
        },
        "Retry",
        "3",
        "engineering",
        ACL,
    )
    assert epic["team"] == "platform"
    assert epic["product_key"] == "checkout"
    assert epic["status"] == "in_progress"
    assert epic["source_id"] == "engineering"
    assert TEAM_SOURCE == "hr"


def test_orphan_sweep_watches_both_team_edges() -> None:
    assert Person.belongs_to.definition["relation_type"] == BELONGS_TO
    assert Team.members.definition["relation_type"] == BELONGS_TO
    assert Epic.owned_by.definition["relation_type"] == OWNED_BY
    assert Team.epics.definition["relation_type"] == OWNED_BY


def test_blank_arr_is_null_and_names_no_epic() -> None:
    fields = deal_fields({"account_id": "acme", "epic_id": ""}, "1", "sales", ACL)
    assert fields["arr"] is None
    assert fields["epic_id"] == ""


def test_owner_edge_keeps_the_team_after_the_person_is_deleted(neo4j_driver) -> None:
    graph = GraphWriter(neo4j_driver)
    neo4j_driver.execute_query("MATCH (n) DETACH DELETE n")
    graph.write(
        "hr",
        "person",
        "emp-1",
        {"full_name": "Ada", "email": "ada@example.com", "role": "Eng", "team": "platform"},
        "Ada",
        ACL,
        "1",
    )
    graph.write(
        "engineering",
        "epic",
        "epic-checkout-retry",
        {
            "title": "Retry",
            "status": "in_progress",
            "team": "platform",
            "product_key": "checkout",
        },
        "Retry",
        ACL,
        "3",
    )
    graph.write(
        "sales",
        "deal",
        "deal-northwind-q3",
        {
            "account_id": "northwind",
            "epic_id": "epic-checkout-retry",
            "arr": 480_000,
            "quarter": "Q3",
            "stage": "negotiation",
            "product_key": "checkout",
        },
        "Northwind",
        ACL,
        "2",
    )
    deal = neo4j_driver.execute_query(
        "MATCH (d:Deal {id: $id}) RETURN properties(d) AS props", id="deal-northwind-q3"
    )[0][0]["props"]
    assert deal["arr"] == 480_000
    assert deal["quarter"] == "Q3"
    assert deal["stage"] == "negotiation"
    assert deal["product_key"] == "checkout"
    epic = neo4j_driver.execute_query(
        "MATCH (e:Epic {id: $id}) RETURN properties(e) AS props", id="epic-checkout-retry"
    )[0][0]["props"]
    assert epic["team"] == "platform"
    assert epic["product_key"] == "checkout"
    assert epic["status"] == "in_progress"
    graph.delete("hr", "emp-1")
    left = neo4j_driver.execute_query(
        """
        MATCH (e:Epic {id: $id})-[:OWNED_BY]->(t:Team {id: 'platform'})
        RETURN t.source_id AS source
        """,
        id="epic-checkout-retry",
    )[0]
    assert left[0]["source"] == "hr"
    graph.delete("engineering", "epic-checkout-retry")
    gone = neo4j_driver.execute_query("MATCH (t:Team {id: 'platform'}) RETURN count(t) AS n")[0][0][
        "n"
    ]
    assert gone == 0


def test_deleting_the_last_deal_removes_a_versionless_epic(neo4j_driver) -> None:
    graph = GraphWriter(neo4j_driver)
    neo4j_driver.execute_query("MATCH (n) DETACH DELETE n")
    deal = {
        "account_id": "acme",
        "epic_id": "epic-missing",
        "arr": 1000,
        "quarter": "Q3",
        "stage": "prospect",
        "product_key": "checkout",
    }
    graph.write("sales", "deal", "deal-1", deal, "Acme", ACL, "1")
    graph.write("sales", "deal", "deal-2", deal, "Acme", ACL, "1")
    graph.write(
        "engineering",
        "epic",
        "epic-real",
        {"title": "Real", "status": "released", "product_key": "billing"},
        "Real",
        ACL,
        "4",
    )
    stub = neo4j_driver.execute_query(
        "MATCH (e:Epic {id: 'epic-missing'}) RETURN e.version AS version, e.acl_rules AS rules"
    )[0][0]
    assert stub["version"] is None
    assert stub["rules"] is None
    graph.delete("sales", "deal-1")
    still = neo4j_driver.execute_query("MATCH (e:Epic {id: 'epic-missing'}) RETURN count(e) AS n")[
        0
    ][0]["n"]
    assert still == 1
    graph.delete("sales", "deal-2")
    gone = neo4j_driver.execute_query("MATCH (e:Epic {id: 'epic-missing'}) RETURN count(e) AS n")[
        0
    ][0]["n"]
    assert gone == 0
    real = neo4j_driver.execute_query("MATCH (e:Epic {id: 'epic-real'}) RETURN count(e) AS n")[0][
        0
    ]["n"]
    assert real == 1
