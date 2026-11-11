"""Content hash for a raw snapshot.

The hash covers the bytes and the native ACL. An ACL-only change on a policy
does not change the harness content hash or the version, and this hash still moves.
"""

import hashlib


def snapshot_hash(body: bytes, native_acl: list[str]) -> str:
    """SHA-256 of bytes plus sorted native ACL. An ACL-only change still moves the hash."""
    acl = ",".join(sorted(native_acl)).encode()
    return hashlib.sha256(body + b"\0" + acl).hexdigest()
