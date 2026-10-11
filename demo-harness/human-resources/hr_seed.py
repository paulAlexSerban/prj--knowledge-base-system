"""Default seed: 48 Faker people and the eleven curated policies.

Teams are the shared keys the other harness services refer to (Engineering owns epics per
team, Sales owns accounts per sales team). Seed rows carry a fixed timestamp so a fresh
database always looks the same; mutations stamp the current time. Larger loads go through
`hr_generate` and `POST /admin/seed`.
"""

from pathlib import Path

from hr_generate import generate_people
from hr_store import Store

SEED_AT = "2026-01-15T09:00:00.000000Z"
POLICY_DIR = Path(__file__).parent / "seed" / "policies"

# policy_id, title, category, region, format, native acl
POLICIES = [
    (
        "pol-parental-leave-de",
        "Parental Leave Policy (Germany)",
        "leave",
        "DE",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-parental-leave-us",
        "Parental Leave Policy (United States)",
        "leave",
        "US",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-parental-leave-uk",
        "Parental Leave Policy (United Kingdom)",
        "leave",
        "UK",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-annual-leave-de",
        "Annual Leave Policy (Germany)",
        "leave",
        "DE",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-remote-work",
        "Remote Work Policy",
        "working-conditions",
        "GLOBAL",
        "markdown",
        ["hr_employee"],
    ),
    ("pol-expenses", "Travel and Expense Policy", "finance", "GLOBAL", "pdf", ["hr_employee"]),
    ("pol-code-of-conduct", "Code of Conduct", "conduct", "GLOBAL", "pdf", ["hr_employee"]),
    (
        "pol-benefits-overview",
        "Benefits Overview",
        "benefits",
        "GLOBAL",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-information-security",
        "Information Security Policy",
        "security",
        "GLOBAL",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-disciplinary-procedure",
        "Disciplinary and Termination Procedure",
        "conduct",
        "GLOBAL",
        "markdown",
        ["hr_policy_admin"],
    ),
    (
        "pol-compensation-review",
        "Compensation Review Guidelines",
        "compensation",
        "GLOBAL",
        "markdown",
        ["hr_policy_admin"],
    ),
]

DEFAULT_PEOPLE = 48
DEFAULT_SEED = 1


def policy_body(policy_id: str) -> str:
    return (POLICY_DIR / f"{policy_id}.md").read_text(encoding="utf-8")


def curated_policies() -> list[dict[str, object]]:
    return [
        {
            "policy_id": policy_id,
            "title": title,
            "category": category,
            "region": region,
            "format": fmt,
            "native_acl": acl,
            "body": policy_body(policy_id),
        }
        for policy_id, title, category, region, fmt, acl in POLICIES
    ]


def seed(store: Store) -> dict[str, int]:
    people = generate_people(DEFAULT_PEOPLE, seed=DEFAULT_SEED, start=store.next_number("person"))
    store.bulk_insert("person", people, at=SEED_AT)
    store.bulk_insert("policy", curated_policies(), at=SEED_AT)
    return store.counts()
