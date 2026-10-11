"""Deterministic seed data: a small people directory and eleven policies.

Teams are the shared keys the other harness services refer to (Engineering owns epics per
team, Sales owns accounts per sales team). Seed rows carry a fixed timestamp so a fresh
database always looks the same; mutations stamp the current time.
"""

from pathlib import Path

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

FIRST = [
    "Anna",
    "Ben",
    "Clara",
    "David",
    "Elena",
    "Felix",
    "Greta",
    "Hugo",
    "Iris",
    "Jonas",
    "Katja",
    "Lukas",
    "Mara",
    "Nils",
    "Olivia",
    "Paul",
    "Quinn",
    "Rosa",
    "Sven",
    "Tara",
    "Uwe",
    "Vera",
    "Wim",
    "Xenia",
    "Yusuf",
    "Zoe",
]
LAST = [
    "Becker",
    "Chen",
    "Dubois",
    "Evans",
    "Fischer",
    "Garcia",
    "Hoffmann",
    "Ivanov",
    "Jensen",
    "Keller",
    "Lopez",
    "Meyer",
    "Novak",
    "Olsen",
    "Patel",
    "Richter",
    "Schmidt",
    "Taylor",
    "Vogel",
    "Weber",
]

# team, head role, member role, count, regions cycled through
TEAMS = [
    ("people", "Head of People", "People Partner", 4, ["DE", "US"]),
    ("platform", "Engineering Manager", "Software Engineer", 7, ["DE", "UK"]),
    ("payments", "Engineering Manager", "Software Engineer", 6, ["DE", "FR"]),
    ("identity", "Engineering Manager", "Software Engineer", 5, ["US", "UK"]),
    ("data", "Engineering Manager", "Data Engineer", 5, ["DE", "US"]),
    ("product", "Head of Product", "Product Manager", 5, ["DE", "US", "UK"]),
    ("sales-emea", "Regional Sales Director", "Account Executive", 8, ["DE", "UK", "FR"]),
    ("sales-amer", "Regional Sales Director", "Account Executive", 8, ["US"]),
]


def people() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    index = 0
    for team, head_role, member_role, count, regions in TEAMS:
        head_id = f"emp-{index + 1:03d}"
        for seat in range(count):
            index += 1
            first, last = FIRST[index % len(FIRST)], LAST[(index * 7) % len(LAST)]
            rows.append(
                {
                    "person_id": f"emp-{index:03d}",
                    "email": f"{first}.{last}.{index:03d}@example.com".lower(),
                    "full_name": f"{first} {last}",
                    "team": team,
                    "role": head_role if seat == 0 else member_role,
                    "region": regions[seat % len(regions)],
                    "manager_id": None if seat == 0 else head_id,
                    "native_acl": ["hr_employee"],
                    # Held by the source, never returned by the API.
                    "salary": 60000 + index * 750,
                    "national_id": f"NID-{index:06d}",
                }
            )
    return rows


def policy_body(policy_id: str) -> str:
    return (POLICY_DIR / f"{policy_id}.md").read_text(encoding="utf-8")


def seed(store: Store) -> dict[str, int]:
    for person in people():
        store.put_person(person, at=SEED_AT)
    for policy_id, title, category, region, fmt, acl in POLICIES:
        store.create_policy(
            policy_id, title, category, region, fmt, acl, policy_body(policy_id), at=SEED_AT
        )
    return store.counts()
