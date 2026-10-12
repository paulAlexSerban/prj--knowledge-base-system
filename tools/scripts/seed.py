"""Load the demo data and wait until the gateway can see it.

HR: reset, then Faker people (SEED_COUNT, default 200). Sales, Product, Engineering:
reset to the curated set. This is what notebooks 00 and 02 do. The connectors pick
the change up on their next poll, so the script waits for the index, not for the POST.
"""

import os
import sys

import kbs_client as kbs

COUNT = int(os.environ.get("SEED_COUNT", "200"))
WAIT = int(os.environ.get("SEED_WAIT_SECONDS", "240"))
EXPECTED_DEALS = 6


def indexed() -> bool:
    """The Germany policy and the Q3 roadmap deals are searchable, and the queues are empty."""
    found = kbs.search("employee_eng", "parental leave Germany")
    if not any(item["object_id"] == "pol-parental-leave-de" for item in found):
        return False
    if len(kbs.deal_ids(kbs.related("product_lead", "roadmap_deals"))) != EXPECTED_DEALS:
        return False
    backlog = kbs.queue_backlog()
    return backlog is None or backlog == 0


def main() -> int:
    try:
        print("hr: reset", kbs.harness("hr", "POST", "/admin/reset")["counts"])
        if COUNT:
            result = kbs.harness(
                "hr",
                "POST",
                "/admin/seed",
                {"kind": "person", "count": COUNT, "seed": 1, "replace": False},
            )
            print(f"hr: {result['inserted']} people in {result['elapsed_seconds']}s")
        for source in ("sales", "product", "engineering"):
            print(f"{source}: reset", kbs.harness(source, "POST", "/admin/reset")["counts"])
        print(f"waiting up to {WAIT}s for the index")
        kbs.wait_until(indexed, WAIT, "seeded data is searchable", every=5)
    except kbs.Failure as exc:
        print(f"seed failed: {exc}", file=sys.stderr)
        return 1
    print("seed ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
