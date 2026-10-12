"""CI checks the notebook files, not a live run. `make e2e` covers the live cycle."""

import importlib.util
import json
from pathlib import Path

from connector_hr.client import canonical_record

NOTEBOOKS = Path(__file__).parents[1]
FORBIDDEN = ("salary", "compensation", "ssn", "national_id")
EXPECTED = [
    "00-seed-hr.ipynb",
    "01-inspect-hr.ipynb",
    "02-seed-sales-product-eng.ipynb",
    "10-policy-create-read.ipynb",
    "11-policy-update.ipynb",
    "12-policy-acl-change.ipynb",
    "13-policy-delete.ipynb",
    "14-sales-mutate.ipynb",
    "15-engineering-mutate.ipynb",
    "16-product-mutate.ipynb",
    "20-person-create-read.ipynb",
    "21-person-update.ipynb",
    "22-person-acl-and-delete.ipynb",
    "30-end-to-end-cycle.ipynb",
    "40-gateway-identity.ipynb",
    "41-gateway-search-abac.ipynb",
    "42-gateway-mcp.ipynb",
    "43-gateway-freshness.ipynb",
    "44-roadmap-question.ipynb",
]
CRUD = {
    "10-policy-create-read.ipynb": ("/admin/policies", "409", "422"),
    "11-policy-update.ipynb": ("/admin/policies/", "404"),
    "12-policy-acl-change.ipynb": ("/admin/policies/", "/acl"),
    "13-policy-delete.ipynb": ("/admin/policies/", "kbs_snapshots_deleted_total"),
    "20-person-create-read.ipynb": ("/admin/people", "409", "422"),
    "21-person-update.ipynb": ("/admin/people/", "manager_id", "404"),
    "22-person-acl-and-delete.ipynb": ("/admin/people/", "native_acl"),
    "30-end-to-end-cycle.ipynb": ("/admin/policies", "drift"),
}


def load_notebook(name: str) -> dict:
    return json.loads((NOTEBOOKS / name).read_text(encoding="utf-8"))


def cell_text(notebook: dict, code_only: bool = False) -> str:
    chunks = []
    for cell in notebook["cells"]:
        if code_only and cell["cell_type"] != "code":
            continue
        source = cell["source"]
        chunks.append("".join(source) if isinstance(source, list) else source)
    return "\n".join(chunks)


def load_helpers():
    spec = importlib.util.spec_from_file_location("hr_demo", NOTEBOOKS / "hr_demo.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_notebook_set_parses_without_saved_output():
    found = sorted(path.name for path in NOTEBOOKS.glob("*.ipynb"))
    assert found == EXPECTED
    for name in EXPECTED:
        notebook = load_notebook(name)
        assert notebook["nbformat"] >= 4
        for cell in notebook["cells"]:
            if cell["cell_type"] != "code":
                continue
            assert cell["outputs"] == []
            assert cell["execution_count"] is None
            source = cell["source"]
            text = "".join(source) if isinstance(source, list) else source
            compile(text, name, "exec")


def test_seed_notebook_is_a_small_demo_load():
    text = cell_text(load_notebook("00-seed-hr.ipynb"), code_only=True)
    assert 'os.environ.get("SEED_SOURCE", "hr")' in text
    assert 'os.environ.get("SEED_KIND", "person")' in text
    assert 'os.environ.get("SEED_COUNT", "200")' in text
    assert "SEED_VALUE" in text and "SEED_RESET" in text
    assert "/admin/seed" in text and "/admin/reset" in text
    assert "/ids" in text
    assert "next_page_token" not in text


def test_inspect_notebook_checks_the_policy_and_the_bucket():
    text = cell_text(load_notebook("01-inspect-hr.ipynb"))
    assert "FORBIDDEN" in text
    assert "pol-parental-leave-de" in text
    assert "kbs-raw-snapshots" in text
    assert "drift" in text
    assert "Elternzeit" in text


def test_crud_notebooks_call_admin_and_read_snapshots():
    for name, needles in CRUD.items():
        text = cell_text(load_notebook(name))
        assert "raw/hr/" in text
        for needle in needles:
            assert needle in text, name


def test_source_notebooks_reset_and_mutate():
    seed = cell_text(load_notebook("02-seed-sales-product-eng.ipynb"))
    assert "/admin/reset" in seed
    assert "40" in seed and "25" in seed and "12" in seed
    assert "SEED_COUNT" not in seed
    sales = cell_text(load_notebook("14-sales-mutate.ipynb"))
    assert "/admin/deals" in sales and "raw/sales/deal/" in sales
    assert "commission_rate" in sales
    engineering = cell_text(load_notebook("15-engineering-mutate.ipynb"))
    assert "/admin/epics" in engineering and "/acl" in engineering
    product = cell_text(load_notebook("16-product-mutate.ipynb"))
    assert "/admin/specs" in product and "product_org" in product
    assert "422" in product


def test_forbidden_names_stay_out_of_the_notebooks():
    for name in EXPECTED:
        text = cell_text(load_notebook(name))
        for line in text.splitlines():
            if "FORBIDDEN" in line:
                continue
            for name_ in FORBIDDEN:
                assert name_ not in line, f"{name}: {line}"


def test_helpers_are_importable_and_match_the_connector():
    helpers = load_helpers()
    assert helpers.FORBIDDEN == frozenset(FORBIDDEN)
    for name in ("admin", "s3_client", "snapshot", "versions", "wait_until", "metric"):
        assert callable(getattr(helpers, name))
    record = {
        "person_id": "emp-900001",
        "native_acl": ["hr_policy_admin", "hr_employee"],
        "manager_id": None,
        "version": 2,
    }
    assert helpers.canonical(record) == canonical_record(record)
    sample = '\nkbs_snapshots_deleted_total{source_id="hr"} 4\n'
    assert helpers.parse_metric(sample, "kbs_snapshots_deleted_total", {"source_id": "hr"}) == 4.0
    created = 'kbs_snapshots_deleted_total_created{source_id="hr"} 1\n'
    assert helpers.parse_metric(created, "kbs_snapshots_deleted_total", {"source_id": "hr"}) is None


def test_gateway_notebooks_stay_off_the_databases():
    needles = {
        "40-gateway-identity.ipynb": ("employee_eng", "outsider", "401"),
        "41-gateway-search-abac.ipynb": ("pol-parental-leave-de", "decision", "nb-deny"),
        "42-gateway-mcp.ipynb": ("search_knowledge", "mcp_tools", "pol-parental-leave-de"),
        "43-gateway-freshness.ipynb": ("pol-demo-gateway", "policies", "hr_policy_admin"),
        "44-roadmap-question.ipynb": ("product_lead", "roadmap_deals", "employee_eng"),
    }
    for name, expected in needles.items():
        text = cell_text(load_notebook(name))
        assert "psycopg" not in text and "neo4j" not in text
        for needle in expected:
            assert needle in text, name
    spec = importlib.util.spec_from_file_location("gateway_demo", NOTEBOOKS / "gateway_demo.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("token", "search", "related", "decision_line", "mcp_tools", "mcp_call"):
        assert callable(getattr(module, name))
