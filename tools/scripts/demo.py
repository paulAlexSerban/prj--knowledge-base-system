"""The README demo script as an executable check, over HTTP only.

    python tools/scripts/demo.py            steps 1 to 4
    python tools/scripts/demo.py --quick    steps 1 to 3 (setup smoke test)
    python tools/scripts/demo.py --chaos    also step 5: stop the worker, create, restart

Each step prints PASS or FAIL. The exit code is non-zero if any step fails.
"""

import argparse
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import kbs_client as kbs

GERMANY = "pol-parental-leave-de"
ROADMAP_VISIBLE = {
    "deal-northwind-q3",
    "deal-fabrikam-q3",
    "deal-adventure-q3",
    "deal-wideworld-q3",
    "deal-litware-q3",
    "deal-alpine-q3",
}
ROADMAP_HIDDEN = {"deal-contoso-q3", "deal-tailspin-q3", "deal-woodgrove-q3", "deal-coho-q2"}


def ids(results: list[dict]) -> list[str]:
    return [item["object_id"] for item in results]


def step1() -> str:
    found = kbs.search("employee_eng", "parental leave Germany")
    if GERMANY not in ids(found):
        raise kbs.Failure(f"{GERMANY} not in {ids(found)}")
    top = next(item for item in found if item["object_id"] == GERMANY)
    return f"cited {top['object_id']} version {top.get('version')} synced {top.get('synced_at')}"


def step2() -> str:
    found = kbs.search("outsider", "parental leave Germany")
    if found:
        raise kbs.Failure(f"outsider received {ids(found)}")
    return "outsider gets an empty list"


def step3() -> str:
    seen = kbs.deal_ids(kbs.related("product_lead", "roadmap_deals"))
    if seen != ROADMAP_VISIBLE or seen & ROADMAP_HIDDEN:
        raise kbs.Failure(f"product_lead sees {sorted(seen)}")
    for persona in ("employee_eng", "outsider"):
        if kbs.related(persona, "roadmap_deals"):
            raise kbs.Failure(f"{persona} should see no roadmap path")
    return f"{len(seen)} deals with epic and team; others see none"


def _policy(policy_id: str, phrase: str, acl: list[str]) -> dict:
    return {
        "policy_id": policy_id,
        "title": "Demo policy",
        "category": "leave",
        "region": "DE",
        "format": "markdown",
        "native_acl": acl,
        "body": f"# Demo\n\n{phrase}\n",
    }


def _visible(persona: str, policy_id: str, phrase: str) -> bool:
    return policy_id in ids(kbs.search(persona, phrase))


def _cleanup(policy_id: str) -> None:
    try:
        kbs.harness("hr", "DELETE", f"/admin/policies/{policy_id}")
    except kbs.Failure:
        pass


def step4() -> str:
    pid = "pol-demo-script"
    phrase = f"zephyr demo policy {datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    _cleanup(pid)
    kbs.harness("hr", "POST", "/admin/policies", _policy(pid, phrase, ["hr_employee"]))
    kbs.wait_until(lambda: _visible("employee_eng", pid, phrase), 180, "created policy searchable")
    kbs.harness("hr", "PUT", f"/admin/policies/{pid}/acl", {"native_acl": ["hr_policy_admin"]})
    kbs.wait_until(lambda: not _visible("employee_eng", pid, phrase), 120, "ACL closed")
    if not _visible("hr_admin", pid, phrase):
        raise kbs.Failure("hr_admin lost access after the ACL change")
    kbs.harness("hr", "DELETE", f"/admin/policies/{pid}")
    kbs.wait_until(lambda: not _visible("hr_admin", pid, phrase), 180, "deleted policy gone")
    return "create, ACL close, delete all propagated"


def step5() -> str:
    pid = "pol-demo-chaos"
    phrase = f"quokka chaos policy {datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    compose = [
        "docker",
        "compose",
        "--env-file",
        ".env",
        "-f",
        "infrastructure/orchestration/compose.yaml",
    ]
    _cleanup(pid)
    subprocess.run([*compose, "stop", "ingestion-worker"], check=True, capture_output=True)
    try:
        kbs.harness("hr", "POST", "/admin/policies", _policy(pid, phrase, ["hr_employee"]))
        if _visible("employee_eng", pid, phrase):
            raise kbs.Failure("policy was indexed while the worker was stopped")
    finally:
        subprocess.run([*compose, "start", "ingestion-worker"], check=True, capture_output=True)
    kbs.wait_until(lambda: _visible("employee_eng", pid, phrase), 240, "visible after restart")
    hits = [r for r in kbs.search("employee_eng", phrase, limit=10) if r["object_id"] == pid]
    if len(hits) != 1:
        raise kbs.Failure(f"expected one chunk for {pid}, got {len(hits)}")
    _cleanup(pid)
    return "message survived the worker restart, one result, no duplicate"


def run(name: str, fn: Callable[[], str]) -> bool:
    try:
        print(f"PASS {name}: {fn()}", flush=True)
        return True
    except kbs.Failure as exc:
        print(f"FAIL {name}: {exc}", flush=True)
    except (httpx.HTTPError, subprocess.CalledProcessError) as exc:
        print(f"FAIL {name}: {type(exc).__name__}: {exc}", flush=True)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="steps 1 to 3 only")
    parser.add_argument("--chaos", action="store_true", help="add the worker restart step")
    args = parser.parse_args(argv)
    steps: list[tuple[str, Callable[[], str]]] = [
        ("1 allowed search with citation", step1),
        ("2 outsider denied", step2),
        ("3 roadmap_deals graph answer", step3),
    ]
    if not args.quick:
        steps.append(("4 create, ACL change, delete", step4))
    if args.chaos:
        steps.append(("5 worker restart", step5))
    results = [run(name, fn) for name, fn in steps]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
