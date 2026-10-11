"""Default seed: 48 Faker people and the thirty-eight curated policies.

Teams are the shared keys the other harness services refer to (Engineering owns epics per
team, Sales owns accounts per sales team). Seed rows carry a fixed timestamp so a fresh
database always looks the same; mutations stamp the current time. Larger loads go through
`generate` and `POST /admin/seed`.
"""

from pathlib import Path

from harness_hr.generate import generate_people
from harness_hr.store import Store

SEED_AT = "2026-01-15T09:00:00.000000Z"
POLICY_DIR = Path(__file__).parent / "policies"

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
    (
        "pol-parental-leave-fr",
        "Parental Leave Policy (France)",
        "leave",
        "FR",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-parental-leave-nl",
        "Parental Leave Policy (Netherlands)",
        "leave",
        "NL",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-parental-leave-es",
        "Parental Leave Policy (Spain)",
        "leave",
        "ES",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-parental-leave-ie",
        "Parental Leave Policy (Ireland)",
        "leave",
        "IE",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-parental-leave-ca",
        "Parental Leave Policy (Canada)",
        "leave",
        "CA",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-annual-leave-fr",
        "Annual Leave Policy (France)",
        "leave",
        "FR",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-annual-leave-nl",
        "Annual Leave Policy (Netherlands)",
        "leave",
        "NL",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-annual-leave-es",
        "Annual Leave Policy (Spain)",
        "leave",
        "ES",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-annual-leave-ie",
        "Annual Leave Policy (Ireland)",
        "leave",
        "IE",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-annual-leave-ca",
        "Annual Leave Policy (Canada)",
        "leave",
        "CA",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-annual-leave-uk",
        "Annual Leave Policy (United Kingdom)",
        "leave",
        "UK",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-annual-leave-us",
        "Annual Leave Policy (United States)",
        "leave",
        "US",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-benefits-fr",
        "Benefits Supplement (France)",
        "benefits",
        "FR",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-benefits-nl",
        "Benefits Supplement (Netherlands)",
        "benefits",
        "NL",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-benefits-es",
        "Benefits Supplement (Spain)",
        "benefits",
        "ES",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-benefits-ie",
        "Benefits Supplement (Ireland)",
        "benefits",
        "IE",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-benefits-ca",
        "Benefits Supplement (Canada)",
        "benefits",
        "CA",
        "markdown",
        ["hr_employee"],
    ),
    ("pol-sick-leave", "Sick Leave Policy", "leave", "GLOBAL", "markdown", ["hr_employee"]),
    (
        "pol-grievance-procedure",
        "Grievance Procedure",
        "conduct",
        "GLOBAL",
        "pdf",
        ["hr_employee"],
    ),
    (
        "pol-performance-review",
        "Performance Review Procedure",
        "working-conditions",
        "GLOBAL",
        "pdf",
        ["hr_employee"],
    ),
    (
        "pol-onboarding-offboarding",
        "Onboarding and Offboarding Procedure",
        "working-conditions",
        "GLOBAL",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-relocation",
        "Relocation Procedure",
        "working-conditions",
        "GLOBAL",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-whistleblowing",
        "Whistleblowing Policy",
        "conduct",
        "GLOBAL",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-redundancy-procedure",
        "Redundancy Procedure",
        "conduct",
        "GLOBAL",
        "markdown",
        ["hr_policy_admin"],
    ),
    (
        "pol-home-office-allowance",
        "Home Office Allowance",
        "benefits",
        "GLOBAL",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-learning-and-development",
        "Learning and Development Policy",
        "benefits",
        "GLOBAL",
        "markdown",
        ["hr_employee"],
    ),
    (
        "pol-employee-assistance-and-wellbeing",
        "Employee Assistance and Wellbeing",
        "benefits",
        "GLOBAL",
        "markdown",
        ["hr_employee"],
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
