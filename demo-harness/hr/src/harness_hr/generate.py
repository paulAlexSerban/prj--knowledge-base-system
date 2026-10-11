"""Faker generators for the HR source.

A fixed seed makes a run reproducible. Ids are `emp-NNNNNN` and `pol-gen-NNNNNN`,
starting at `start` so a second load continues past ids already in the database.
Other harnesses can register their own generators later; this module is HR only.
"""

from faker import Faker

# team, head role, member role, seats in one wave, regions
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
CATEGORIES = [
    "leave",
    "benefits",
    "conduct",
    "security",
    "working-conditions",
    "finance",
]
REGIONS = ["DE", "US", "UK", "FR", "GLOBAL"]
SEATS = [
    (team, head if seat == 0 else member, regions[seat % len(regions)], seat == 0)
    for team, head, member, count, regions in TEAMS
    for seat in range(count)
]


def _faker(seed: int) -> Faker:
    Faker.seed(seed)
    fake = Faker()
    fake.seed_instance(seed)
    return fake


def generate_people(count: int, seed: int, start: int = 1) -> list[dict[str, object]]:
    fake = _faker(seed)
    heads: dict[tuple[str, int], str] = {}
    rows: list[dict[str, object]] = []
    for offset in range(count):
        number = start + offset
        wave, seat = divmod(offset, len(SEATS))
        team, role, region, is_head = SEATS[seat]
        person_id = f"emp-{number:06d}"
        if is_head:
            heads[(team, wave)] = person_id
            manager_id = None
        else:
            manager_id = heads.get((team, wave))
        first = fake.first_name().replace(" ", "")
        last = fake.last_name().replace(" ", "")
        rows.append(
            {
                "person_id": person_id,
                "email": f"{first}.{last}.{number:06d}@example.com".lower(),
                "full_name": f"{first} {last}",
                "team": team,
                "role": role,
                "region": region,
                "manager_id": manager_id,
                "native_acl": ["hr_employee"],
                "salary": fake.random_int(min=45000, max=180000),
                "national_id": f"NID-{fake.random_int(min=0, max=999999):06d}-{number:06d}",
            }
        )
    return rows


def generate_policies(count: int, seed: int, start: int = 1) -> list[dict[str, object]]:
    fake = _faker(seed)
    rows: list[dict[str, object]] = []
    for offset in range(count):
        number = start + offset
        title = (
            f"{CATEGORIES[offset % len(CATEGORIES)].replace('-', ' ').title()} Policy {number:06d}"
        )
        sections = [
            f"## {heading}\n\n{fake.paragraph(nb_sentences=4)}"
            for heading in ("Eligibility", "What the company provides", "How to apply")
        ]
        rows.append(
            {
                "policy_id": f"pol-gen-{number:06d}",
                "title": title,
                "category": CATEGORIES[offset % len(CATEGORIES)],
                "region": REGIONS[offset % len(REGIONS)],
                "format": "pdf" if offset % 5 == 0 else "markdown",
                "native_acl": ["hr_policy_admin"] if offset % 20 == 0 else ["hr_employee"],
                "body": f"# {title}\n\n" + "\n\n".join(sections) + "\n",
            }
        )
    return rows
