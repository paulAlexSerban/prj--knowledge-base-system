"""JWT claim checks. No database."""

from gateway_core.auth import AuthError, issue_token, principal_from_token

SECRET = "test-secret-test-secret-test-secret"


def test_persona_token_round_trips() -> None:
    token = issue_token("employee_eng", SECRET)
    principal = principal_from_token(token, SECRET)
    assert principal.subject == "employee-eng"
    assert principal.groups == ("employees",)
    assert principal.clearance == "internal"
    assert principal.department == "engineering"


def test_product_lead_can_be_issued() -> None:
    principal = principal_from_token(issue_token("product_lead", SECRET), SECRET)
    assert principal.subject == "product-lead"
    assert principal.groups == ("employees", "roadmap")
    assert principal.clearance == "internal"
    assert principal.department == "product"


def test_outsider_has_no_groups() -> None:
    principal = principal_from_token(issue_token("outsider", SECRET), SECRET)
    assert principal.groups == ()
    assert principal.subject == "outsider"


def test_expired_token_is_rejected() -> None:
    token = issue_token("hr_admin", SECRET, ttl_seconds=-10)
    try:
        principal_from_token(token, SECRET)
    except AuthError as exc:
        assert exc.reason == "expired"
    else:
        raise AssertionError("expected AuthError")


def test_bad_signature_is_rejected() -> None:
    token = issue_token("employee_eng", SECRET)
    try:
        principal_from_token(token, "other-secret-other-secret-other-1")
    except AuthError as exc:
        assert exc.reason == "invalid"
    else:
        raise AssertionError("expected AuthError")


def test_missing_claim_is_rejected() -> None:
    import time

    import jwt

    now = int(time.time())
    token = jwt.encode(
        {"sub": "ada", "groups": ["employees"], "clearance": "internal", "iat": now, "exp": now + 60},
        SECRET,
        algorithm="HS256",
    )
    try:
        principal_from_token(token, SECRET)
    except AuthError as exc:
        assert exc.reason == "missing_claim"
    else:
        raise AssertionError("expected AuthError")


def test_unknown_clearance_is_rejected() -> None:
    import time

    import jwt

    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "ada",
            "groups": ["employees"],
            "clearance": "top-secret",
            "department": "engineering",
            "iat": now,
            "exp": now + 60,
        },
        SECRET,
        algorithm="HS256",
    )
    try:
        principal_from_token(token, SECRET)
    except AuthError as exc:
        assert exc.reason == "clearance"
    else:
        raise AssertionError("expected AuthError")


def test_unknown_persona_is_rejected() -> None:
    try:
        issue_token("not-a-persona", SECRET)
    except AuthError as exc:
        assert exc.reason == "persona"
    else:
        raise AssertionError("expected AuthError")
