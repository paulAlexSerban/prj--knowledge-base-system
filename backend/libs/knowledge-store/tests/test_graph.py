"""Deal and epic properties, and the team edge that must survive a person delete."""

from knowledge_store.acl import AclView
from knowledge_store.graph import ORPHAN_TEAMS, GraphWriter

ACL = AclView(
    ("sales_team",),
    ("sales",),
    1,
    ({"group": "sales_team", "department": "sales", "clearance": "internal"},),
    (),
)


class FakeDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute_query(self, query: str, **params: object):
        self.calls.append((query, params))
        return ([], None, None)


def _params(driver: FakeDriver, marker: str) -> dict:
    found = [params for query, params in driver.calls if marker in query]
    assert found, marker
    return found[-1]


def test_deal_and_epic_writes_pass_the_question_fields() -> None:
    driver = FakeDriver()
    graph = GraphWriter(driver)
    graph.write(
        "sales",
        "deal",
        "deal-northwind-q3",
        {
            "account_id": "northwind",
            "account_name": "Northwind Traders",
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
    deal = _params(driver, "d.arr")
    assert deal["arr"] == 480_000
    assert deal["quarter"] == "Q3"
    assert deal["stage"] == "negotiation"
    assert deal["product_key"] == "checkout"
    assert deal["epic_id"] == "epic-checkout-retry"
    assert deal["account_name"] == "Northwind Traders"
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
    epic = _params(driver, "e.team")
    assert epic["team"] == "platform"
    assert epic["product_key"] == "checkout"
    assert epic["status"] == "in_progress"
    owner = _params(driver, "OWNED_BY")
    assert owner["team"] == "platform"
    assert owner["team_source"] == "hr"


def test_orphan_sweep_keeps_a_team_that_only_owns_an_epic() -> None:
    assert "OWNED_BY" in ORPHAN_TEAMS
    assert "BELONGS_TO" in ORPHAN_TEAMS
    driver = FakeDriver()
    GraphWriter(driver).delete("hr", "emp-1")
    assert any("OWNED_BY" in query for query, _params in driver.calls)


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


def test_blank_arr_is_stored_as_null() -> None:
    driver = FakeDriver()
    GraphWriter(driver).write(
        "sales",
        "deal",
        "deal-1",
        {"account_id": "acme", "epic_id": ""},
        "Acme",
        ACL,
        "1",
    )
    assert _params(driver, "d.arr")["arr"] is None
    assert not any("OWNED_BY" in query and "MERGE" in query for query, _params in driver.calls)
