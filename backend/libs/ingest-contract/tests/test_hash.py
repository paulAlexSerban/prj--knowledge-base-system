"""The snapshot hash covers bytes and the native ACL."""

from ingest_contract.hashing import snapshot_hash


def test_hash_changes_when_only_the_acl_changes() -> None:
    body = b"same bytes"
    assert snapshot_hash(body, ["hr_employee"]) != snapshot_hash(body, ["hr_policy_admin"])


def test_hash_ignores_acl_order() -> None:
    body = b"same bytes"
    assert snapshot_hash(body, ["b", "a"]) == snapshot_hash(body, ["a", "b"])
