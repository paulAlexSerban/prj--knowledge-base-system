"""Bind the process-wide neomodel handle to a caller-owned Neo4j driver.

neomodel does not own the driver. The runtime still builds it, and ping still
uses that driver. install_labels is never called. Constraints stay in
database/neo4j/init.cypher.
"""

from neomodel import db

_bound: object = None


def bind(driver: object) -> None:
    global _bound
    if driver is None or driver is _bound:
        return
    db.set_connection(driver=driver)
    _bound = driver
