"""Mock identity. HS256 JWTs stand in for an OIDC provider.

Claims are subject, groups, clearance, and department. An empty group list
is a valid principal who can see nothing. A missing claim, a bad signature,
an expired token, or an unknown clearance is rejected.
"""

import os
import time
from pathlib import Path

import jwt
import yaml

from knowledge_store.acl import CLEARANCE, Principal

ALGORITHM = "HS256"
TOKEN_TTL_SECONDS = 8 * 60 * 60


class AuthError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def personas_path() -> Path:
    """principals.yaml. PRINCIPALS_FILE overrides for tests."""
    override = os.environ.get("PRINCIPALS_FILE")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "infrastructure" / "config" / "principals.yaml"
        if candidate.is_file():
            return candidate
    raise AuthError("personas")


def load_personas(path: Path | None = None) -> dict[str, Principal]:
    """Named demo personas. Production identity is OIDC, not this file."""
    target = path or personas_path()
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    found: dict[str, Principal] = {}
    for name, claims in raw.items():
        found[name] = _principal(str(claims.get("subject") or ""), claims)
    return found


def issue_token(
    persona: str,
    secret: str,
    *,
    path: Path | None = None,
    ttl_seconds: int = TOKEN_TTL_SECONDS,
) -> str:
    """Sign an HS256 JWT for a persona. Used by /dev/token and the notebooks."""
    if not secret:
        raise AuthError("secret")
    principals = load_personas(path)
    principal = principals.get(persona)
    if principal is None:
        raise AuthError("persona")
    now = int(time.time())
    payload = {
        "sub": principal.subject,
        "groups": list(principal.groups),
        "clearance": principal.clearance,
        "department": principal.department,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    token = jwt.encode(payload, secret, algorithm=ALGORITHM)
    if isinstance(token, bytes):
        return token.decode("ascii")
    return token


def principal_from_token(token: str, secret: str) -> Principal:
    """Validate signature, expiry, and claims. This is the only file that knows the signer."""
    if not secret:
        raise AuthError("secret")
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("invalid") from exc
    return _principal(str(claims.get("sub") or ""), claims)


def _principal(subject: str, claims: dict) -> Principal:
    """Require groups, a known clearance, and a non-empty department. Empty groups see nothing."""
    if not subject or "groups" not in claims or "clearance" not in claims or "department" not in claims:
        raise AuthError("missing_claim")
    groups = claims["groups"]
    clearance = claims["clearance"]
    department = claims["department"]
    if not isinstance(groups, list) or not all(isinstance(item, str) for item in groups):
        raise AuthError("missing_claim")
    if not isinstance(clearance, str) or clearance not in CLEARANCE:
        raise AuthError("clearance")
    if not isinstance(department, str) or not department:
        raise AuthError("missing_claim")
    return Principal(subject, tuple(groups), clearance, department)
