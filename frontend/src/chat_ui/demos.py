"""Sidebar questions. Each one names the persona and the route the rules would pick."""

DEFAULT_PERSONAS = "employee_eng,hr_admin,outsider,product_lead"
ROADMAP_QUESTION = (
    "Which high-ARR Sales deals in Q3 depend on unreleased Engineering epics?"
)

# Keys parental-leave, roadmap, and roadmap-outsider stay. Notebooks and the
# old sidebar buttons used them. `route` is the rules classifier, which is high
# confidence for every question here, so an optional model tie-break does not run.
DEMOS = (
    {
        "key": "parental-leave",
        "category": "Policy",
        "label": "What is our parental leave policy in Germany?",
        "question": "What is our parental leave policy in Germany?",
        "persona": "employee_eng",
        "route": "semantic",
    },
    {
        "key": "teammates",
        "category": "Organization",
        "label": "Who are the teammates of emp-000001?",
        "question": "Who are the teammates of emp-000001?",
        "persona": "employee_eng",
        "route": "relational",
    },
    {
        "key": "team-sales-emea",
        "category": "Organization",
        "label": "Who is on team sales-emea?",
        "question": "Who is on team sales-emea?",
        "persona": "employee_eng",
        "route": "relational",
    },
    {
        "key": "policies-visible",
        "category": "Organization",
        "label": "Which policies can I see?",
        "question": "Which policies can I see?",
        "persona": "employee_eng",
        "route": "relational",
    },
    {
        "key": "roadmap",
        "category": "Roadmap",
        "label": ROADMAP_QUESTION,
        "question": ROADMAP_QUESTION,
        "persona": "product_lead",
        "route": "relational",
    },
    {
        "key": "roadmap-hybrid",
        "category": "Roadmap",
        "label": "Explain why high-ARR Q3 deals depend on unreleased epics",
        "question": "Explain why high-ARR deals in Q3 depend on unreleased engineering epics",
        "persona": "product_lead",
        "route": "hybrid",
    },
    {
        "key": "roadmap-outsider",
        "category": "Access",
        "label": "Same roadmap question as an outsider",
        "question": ROADMAP_QUESTION,
        "persona": "outsider",
        "route": "relational",
    },
    {
        "key": "roadmap-employee",
        "category": "Access",
        "label": "Same roadmap question as an engineer",
        "question": ROADMAP_QUESTION,
        "persona": "employee_eng",
        "route": "relational",
    },
    {
        "key": "no-answer",
        "category": "No answer",
        "label": "A question the corpus does not cover",
        "question": "xylophone quantum payroll",
        "persona": "employee_eng",
        "route": "semantic",
    },
    {
        "key": "parental-all",
        "category": "Conflicting sources",
        "label": "Parental leave, every country",
        "question": "What is our parental leave policy?",
        "persona": "employee_eng",
        "route": "semantic",
    },
    {
        "key": "product-checkout",
        "category": "Product",
        "label": "What does the checkout spec say about wallets?",
        "question": "What does the checkout product spec say about wallets and 3-D Secure?",
        "persona": "product_lead",
        "route": "semantic",
    },
    {
        "key": "engineering-retry",
        "category": "Engineering",
        "label": "Checkout retry on card decline",
        "question": "What is the status of checkout retry on card decline?",
        "persona": "product_lead",
        "route": "semantic",
    },
)


def categories() -> list[str]:
    """Category names in sidebar order."""
    found: list[str] = []
    for demo in DEMOS:
        if demo["category"] not in found:
            found.append(demo["category"])
    return found


def in_category(category: str) -> list[dict]:
    return [demo for demo in DEMOS if demo["category"] == category]
