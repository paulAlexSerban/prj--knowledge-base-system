"""Curated deals. Account fields sit on the deal. There is no account object.

`epic_id` cites an engineering epic. Empty means the deal depends on nothing.
High ARR in this demo is 200,000 or more. The eight QUESTION_DEALS are the
Q3 rows that depend on an unreleased epic. The counterexamples are the rows
a careless filter would include.
"""

SEED_AT = "2026-01-15T09:00:00.000000Z"
HIGH_ARR = 200_000

QUESTION_DEALS = (
    "deal-northwind-q3",
    "deal-contoso-q3",
    "deal-fabrikam-q3",
    "deal-adventure-q3",
    "deal-wideworld-q3",
    "deal-litware-q3",
    "deal-alpine-q3",
    "deal-tailspin-q3",
)

# deal_id, account_id, account_name, arr, quarter, stage, owner, product_key, epic_id, acl, rate
NAMED: tuple[tuple[str, str, str, int, str, str, str, str, str, str, float], ...] = (
    (
        "deal-northwind-q3",
        "acc-northwind",
        "Northwind Traders",
        480_000,
        "2026-Q3",
        "negotiation",
        "ada@sales.example",
        "checkout",
        "epic-checkout-retry",
        "account_team",
        0.08,
    ),
    (
        "deal-contoso-q3",
        "acc-contoso",
        "Contoso",
        920_000,
        "2026-Q3",
        "proposal",
        "grace@sales.example",
        "billing",
        "epic-billing-tax",
        "sales_leadership",
        0.12,
    ),
    (
        "deal-fabrikam-q3",
        "acc-fabrikam",
        "Fabrikam",
        310_000,
        "2026-Q3",
        "qualified",
        "ken@sales.example",
        "identity",
        "epic-identity-scim",
        "account_team",
        0.07,
    ),
    (
        "deal-adventure-q3",
        "acc-adventure",
        "Adventure Works",
        250_000,
        "2026-Q3",
        "negotiation",
        "ada@sales.example",
        "mobile",
        "epic-mobile-offline",
        "account_team",
        0.09,
    ),
    (
        "deal-wideworld-q3",
        "acc-wideworld",
        "Wide World Importers",
        640_000,
        "2026-Q3",
        "proposal",
        "grace@sales.example",
        "analytics",
        "epic-analytics-live",
        "account_team",
        0.10,
    ),
    (
        "deal-litware-q3",
        "acc-litware",
        "Litware",
        410_000,
        "2026-Q3",
        "negotiation",
        "ken@sales.example",
        "notifications",
        "epic-notify-webhooks",
        "account_team",
        0.08,
    ),
    (
        "deal-alpine-q3",
        "acc-alpine",
        "Alpine Ski House",
        275_000,
        "2026-Q3",
        "qualified",
        "ada@sales.example",
        "checkout",
        "epic-checkout-3ds",
        "account_team",
        0.06,
    ),
    (
        "deal-tailspin-q3",
        "acc-tailspin",
        "Tailspin Toys",
        530_000,
        "2026-Q3",
        "proposal",
        "grace@sales.example",
        "billing",
        "epic-billing-credits",
        "sales_leadership",
        0.11,
    ),
    (
        "deal-woodgrove-q3",
        "acc-woodgrove",
        "Woodgrove Bank",
        700_000,
        "2026-Q3",
        "won",
        "ken@sales.example",
        "checkout",
        "epic-checkout-wallets",
        "account_team",
        0.09,
    ),
    (
        "deal-proseware-q3",
        "acc-proseware",
        "Proseware",
        300_000,
        "2026-Q3",
        "prospect",
        "ada@sales.example",
        "identity",
        "",
        "account_team",
        0.05,
    ),
    (
        "deal-coho-q2",
        "acc-coho",
        "Coho Winery",
        800_000,
        "2026-Q2",
        "negotiation",
        "grace@sales.example",
        "identity",
        "epic-identity-sso",
        "sales_leadership",
        0.12,
    ),
    (
        "deal-graphic-q3",
        "acc-graphic",
        "Graphic Design Institute",
        45_000,
        "2026-Q3",
        "qualified",
        "ken@sales.example",
        "mobile",
        "epic-mobile-push",
        "account_team",
        0.06,
    ),
    (
        "deal-lucerne-q4",
        "acc-lucerne",
        "Lucerne Publishing",
        360_000,
        "2026-Q4",
        "proposal",
        "ada@sales.example",
        "analytics",
        "epic-analytics-forecast",
        "account_team",
        0.08,
    ),
)

_FILLER_EPICS = (
    "epic-billing-dunning",
    "epic-analytics-cohort",
    "epic-notify-digest",
    "epic-checkout-wallets",
    "",
)


def _native(acl: str) -> list[str]:
    """Leadership deals stay closed. Every other deal is also readable by roadmap."""
    if acl == "sales_leadership":
        return [acl]
    return [acl, "roadmap_readers"]


def _row(
    deal_id: str,
    account_id: str,
    account_name: str,
    arr: int,
    quarter: str,
    stage: str,
    owner_email: str,
    product_key: str,
    epic_id: str,
    acl: str,
    commission_rate: float,
) -> dict[str, object]:
    return {
        "deal_id": deal_id,
        "account_id": account_id,
        "account_name": account_name,
        "arr": arr,
        "quarter": quarter,
        "stage": stage,
        "owner_email": owner_email,
        "product_key": product_key,
        "epic_id": epic_id or None,
        "native_acl": _native(acl),
        "commission_rate": commission_rate,
    }


def _fillers() -> list[dict[str, object]]:
    """Low-ARR deals so a Q3 high-ARR filter cannot mistake them for the answer."""
    quarters = ("2026-Q2", "2026-Q3", "2026-Q4")
    stages = ("prospect", "qualified", "proposal", "negotiation", "won", "lost")
    keys = ("checkout", "billing", "identity", "mobile", "analytics", "notifications")
    owners = ("ada@sales.example", "grace@sales.example", "ken@sales.example")
    rows = []
    for index in range(1, 28):
        rows.append(
            _row(
                f"deal-filler-{index:02d}",
                f"acc-filler-{index:02d}",
                f"Filler Account {index:02d}",
                20_000 + index * 1_000,
                quarters[index % 3],
                stages[index % 6],
                owners[index % 3],
                keys[index % 6],
                _FILLER_EPICS[index % len(_FILLER_EPICS)],
                "account_team",
                0.05,
            )
        )
    return rows


def deals() -> list[dict[str, object]]:
    named = [_row(*row) for row in NAMED]
    return named + _fillers()
