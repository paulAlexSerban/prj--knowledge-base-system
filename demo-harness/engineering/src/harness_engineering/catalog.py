"""Curated epics. Sales deals cite these ids. Specs use the same product keys.

Teams are the HR directory names. `eng_leadership` marks the confidential rows.
"""

SEED_AT = "2026-01-15T09:00:00.000000Z"

# epic_id, title, status, product_key, team, native acl
ROWS: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "epic-checkout-retry",
        "Checkout retry on card decline",
        "in_progress",
        "checkout",
        "payments",
        "project_member",
    ),
    ("epic-billing-tax", "EU tax on invoices", "planned", "billing", "payments", "project_member"),
    (
        "epic-billing-dunning",
        "Dunning sequences",
        "released",
        "billing",
        "payments",
        "project_member",
    ),
    (
        "epic-identity-sso",
        "Enterprise SSO",
        "in_progress",
        "identity",
        "identity",
        "eng_leadership",
    ),
    (
        "epic-identity-scim",
        "SCIM provisioning",
        "planned",
        "identity",
        "identity",
        "project_member",
    ),
    (
        "epic-mobile-offline",
        "Offline mobile checkout",
        "planned",
        "mobile",
        "product",
        "project_member",
    ),
    ("epic-analytics-cohort", "Cohort export", "released", "analytics", "data", "project_member"),
    (
        "epic-analytics-live",
        "Live revenue dashboard",
        "in_progress",
        "analytics",
        "data",
        "project_member",
    ),
    (
        "epic-notify-digest",
        "Weekly digest emails",
        "released",
        "notifications",
        "platform",
        "project_member",
    ),
    (
        "epic-notify-webhooks",
        "Customer webhooks",
        "planned",
        "notifications",
        "platform",
        "project_member",
    ),
    (
        "epic-checkout-wallets",
        "Wallet payments",
        "released",
        "checkout",
        "payments",
        "project_member",
    ),
    (
        "epic-platform-audit",
        "Audit log export",
        "in_progress",
        "identity",
        "platform",
        "project_member",
    ),
    ("epic-data-warehouse", "Warehouse sync", "released", "analytics", "data", "project_member"),
    (
        "epic-mobile-push",
        "Push notifications",
        "in_progress",
        "mobile",
        "product",
        "project_member",
    ),
    ("epic-billing-credits", "Credit notes", "planned", "billing", "payments", "project_member"),
    ("epic-identity-mfa", "Step-up MFA", "released", "identity", "identity", "project_member"),
    ("epic-checkout-3ds", "3-D Secure 2", "in_progress", "checkout", "payments", "project_member"),
    (
        "epic-platform-flags",
        "Feature flags service",
        "released",
        "notifications",
        "platform",
        "project_member",
    ),
    ("epic-analytics-forecast", "ARR forecast", "planned", "analytics", "data", "project_member"),
    ("epic-mobile-tablet", "Tablet layout", "planned", "mobile", "product", "project_member"),
    ("epic-notify-sms", "SMS fallback", "released", "notifications", "platform", "project_member"),
    (
        "epic-billing-usage",
        "Usage-based pricing",
        "in_progress",
        "billing",
        "payments",
        "eng_leadership",
    ),
    (
        "epic-identity-guest",
        "Guest checkout identity",
        "planned",
        "identity",
        "identity",
        "project_member",
    ),
    (
        "epic-data-quality",
        "Pipeline quality checks",
        "in_progress",
        "analytics",
        "data",
        "project_member",
    ),
    ("epic-platform-cost", "Cost explorer", "released", "billing", "platform", "eng_leadership"),
)

# Unreleased epics that the Q3 high-ARR deals depend on.
QUESTION_EPICS = (
    "epic-checkout-retry",
    "epic-billing-tax",
    "epic-identity-scim",
    "epic-mobile-offline",
    "epic-analytics-live",
    "epic-notify-webhooks",
    "epic-checkout-3ds",
    "epic-billing-credits",
)


def _native(acl: str) -> list[str]:
    """Leadership epics stay confidential. Every other epic is also readable by roadmap."""
    if acl == "eng_leadership":
        return [acl]
    return [acl, "roadmap_readers"]


def epics() -> list[dict[str, object]]:
    return [
        {
            "epic_id": epic_id,
            "title": title,
            "status": status,
            "product_key": product_key,
            "team": team,
            "native_acl": _native(acl),
        }
        for epic_id, title, status, product_key, team, acl in ROWS
    ]
