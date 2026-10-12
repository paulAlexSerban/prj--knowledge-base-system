"""Curated specs. Product keys and epic ids match Sales and Engineering.

Specs are prose. Nothing here becomes a graph node.
"""

SEED_AT = "2026-01-15T09:00:00.000000Z"


def _body(title: str, sections: list[tuple[str, str]]) -> str:
    parts = [f"# {title}", ""]
    for heading, prose in sections:
        parts.extend([f"## {heading}", "", prose, ""])
    return "\n".join(parts).strip() + "\n"


SPECS: tuple[tuple[str, str, str, str], ...] = (
    (
        "spec-checkout",
        "Checkout",
        "checkout",
        _body(
            "Checkout",
            [
                (
                    "Card payments",
                    "Product key checkout covers card capture, wallets, and 3-D Secure.",
                ),
                (
                    "Decline retry",
                    "Epic epic-checkout-retry retries a soft decline before the buyer sees a failure.",
                ),
            ],
        ),
    ),
    (
        "spec-billing",
        "Billing",
        "billing",
        _body(
            "Billing",
            [
                ("Invoices", "Product key billing covers invoices, credits, and dunning."),
                (
                    "EU tax",
                    "Epic epic-billing-tax adds VAT on EU invoices. It is not released.",
                ),
            ],
        ),
    ),
    (
        "spec-identity",
        "Identity",
        "identity",
        _body(
            "Identity",
            [
                ("Accounts", "Product key identity covers employees, guests, and SSO."),
                (
                    "Provisioning",
                    "Epic epic-identity-scim provisions users from a customer directory.",
                ),
            ],
        ),
    ),
    (
        "spec-mobile",
        "Mobile",
        "mobile",
        _body(
            "Mobile",
            [
                ("Clients", "Product key mobile covers the phone and tablet clients."),
                (
                    "Offline checkout",
                    "Epic epic-mobile-offline lets a store take payment without a network.",
                ),
            ],
        ),
    ),
    (
        "spec-analytics",
        "Analytics",
        "analytics",
        _body(
            "Analytics",
            [
                ("Reporting", "Product key analytics covers cohorts, forecasts, and live revenue."),
                (
                    "Live revenue",
                    "Epic epic-analytics-live shows ARR as it changes during the quarter.",
                ),
            ],
        ),
    ),
    (
        "spec-notifications",
        "Notifications",
        "notifications",
        _body(
            "Notifications",
            [
                ("Channels", "Product key notifications covers email, SMS, and customer webhooks."),
                (
                    "Customer webhooks",
                    "Epic epic-notify-webhooks posts deal and billing events to the customer.",
                ),
            ],
        ),
    ),
    (
        "spec-checkout-retry",
        "Checkout retry",
        "checkout",
        _body(
            "Checkout retry",
            [
                (
                    "When it runs",
                    "A soft decline on product key checkout is retried once by epic-checkout-retry.",
                ),
                ("Status", "The epic is in progress. Wallet payments are already released."),
            ],
        ),
    ),
    (
        "spec-eu-tax",
        "EU tax",
        "billing",
        _body(
            "EU tax",
            [
                (
                    "Scope",
                    "Epic epic-billing-tax calculates VAT for product key billing. It is planned.",
                ),
                ("Out of scope", "US sales tax is not part of this spec."),
            ],
        ),
    ),
    (
        "spec-enterprise-sso",
        "Enterprise SSO",
        "identity",
        _body(
            "Enterprise SSO",
            [
                (
                    "Buyers",
                    "Epic epic-identity-sso is the identity work behind large product key identity deals.",
                ),
                ("Status", "SSO is in progress. Step-up MFA is already released."),
            ],
        ),
    ),
    (
        "spec-offline-mobile",
        "Offline mobile checkout",
        "mobile",
        _body(
            "Offline mobile checkout",
            [
                (
                    "Store mode",
                    "Epic epic-mobile-offline queues a payment on product key mobile and syncs later.",
                ),
                (
                    "Limit",
                    "Offline tickets are capped and reconciled the next time the device is online.",
                ),
            ],
        ),
    ),
    (
        "spec-live-revenue",
        "Live revenue",
        "analytics",
        _body(
            "Live revenue",
            [
                (
                    "Dashboard",
                    "Epic epic-analytics-live is the in-progress dashboard for product key analytics.",
                ),
                ("Numbers", "ARR on the dashboard is the same figure Sales records on a deal."),
            ],
        ),
    ),
    (
        "spec-customer-webhooks",
        "Customer webhooks",
        "notifications",
        _body(
            "Customer webhooks",
            [
                (
                    "Events",
                    "Epic epic-notify-webhooks sends product key notifications when a deal stage changes.",
                ),
                (
                    "Delivery",
                    "The receiver acknowledges with 2xx. Failures retry, then dead-letter.",
                ),
            ],
        ),
    ),
)


def specs() -> list[dict[str, object]]:
    return [
        {
            "spec_id": spec_id,
            "title": title,
            "product_key": product_key,
            "body": body,
            "native_acl": ["product_org", "roadmap_readers"],
        }
        for spec_id, title, product_key, body in SPECS
    ]
