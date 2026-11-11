"""Run the golden set against the live gateway. `make eval`.

Prints one line per case and a summary, exits non-zero when a threshold fails.
It calls the REST API only, as each case's persona, so it measures what an agent
would get, including authorization.

    uv run python tools/eval/run.py [--label fastembed] [--json out.json]
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import kbs_client as kbs

GOLDEN = Path(__file__).with_name("golden.yaml")


def run_case(case: dict) -> dict:
    """Return {passed, rank, ms, detail}. rank is the 1-based position of the first expected id."""
    started = time.perf_counter()
    body = kbs.ask(case["persona"], case["query"], limit=case.get("limit", 5))
    ms = (time.perf_counter() - started) * 1000
    route = str((body.get("route") or {}).get("class"))
    if case["category"] == "route":
        return {
            "passed": route == case["expect_route"],
            "rank": None,
            "ms": ms,
            "detail": f"route={route}",
        }
    if case["category"] == "graph":
        seen = kbs.deal_ids(list(body.get("paths") or []))
        want = set(case["expect_deals"])
        routed = route == case.get("expect_route", "relational")
        return {
            "passed": seen == want and routed,
            "rank": None,
            "ms": ms,
            "detail": f"route={route} deals={sorted(seen)}",
        }

    found = list(body.get("results") or [])
    ids = [item["object_id"] for item in found]
    rank = None
    for expected in case.get("expect", []):
        if expected in ids:
            position = ids.index(expected) + 1
            rank = position if rank is None else min(rank, position)
    if "expect_all" in case:
        passed = all(item in ids for item in case["expect_all"])
    elif "expect_none_of" in case:
        passed = not any(item in ids for item in case["expect_none_of"]) and (
            "expect" not in case or rank is not None
        )
    elif "expect" in case:
        passed = rank is not None
    else:
        passed = ids == []
    expected_route = case.get("expect_route", "semantic")
    routed = route == expected_route
    return {
        "passed": passed and routed,
        "rank": rank,
        "ms": ms,
        "detail": f"route={route} top={ids[:3]}",
    }


def summarize(cases: list[dict], results: list[dict]) -> dict[str, float]:
    def rate(category: str) -> float:
        picked = [r for c, r in zip(cases, results, strict=True) if c["category"] == category]
        return sum(r["passed"] for r in picked) / len(picked) if picked else 1.0

    ranked = [
        r for c, r in zip(cases, results, strict=True) if c["category"] in {"known", "paraphrase"}
    ]
    reciprocal = [1 / r["rank"] if r["rank"] else 0.0 for r in ranked]
    times = sorted(r["ms"] for r in results)
    p95 = times[min(len(times) - 1, int(len(times) * 0.95))] if times else 0.0
    return {
        "recall_at_5": sum(r["passed"] for r in ranked) / len(ranked),
        "paraphrase_recall_at_5": rate("paraphrase"),
        "mrr": statistics.mean(reciprocal) if reciprocal else 0.0,
        "no_answer": rate("no_answer"),
        "conflicting": rate("conflicting"),
        "access": rate("access"),
        "graph": rate("graph"),
        "route": rate("route"),
        "latency_p95_ms": p95,
    }


def failed_thresholds(metrics: dict[str, float], thresholds: dict[str, float]) -> list[str]:
    bad = []
    for name, limit in thresholds.items():
        value = metrics[name]
        ok = value <= limit if name.endswith("_ms") else value >= limit
        if not ok:
            bad.append(f"{name} {value:.2f} (limit {limit})")
    return bad


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="", help="name for this run, e.g. the embedder")
    parser.add_argument("--json", help="write the metrics to this file")
    parser.add_argument("--no-latency-gate", action="store_true", help="report p95, do not fail")
    args = parser.parse_args(argv)
    spec = yaml.safe_load(GOLDEN.read_text())
    cases = spec["cases"]
    try:
        results = [run_case(case) for case in cases]
    except kbs.Failure as exc:
        print(f"eval could not run: {exc}", file=sys.stderr)
        return 2
    for case, result in zip(cases, results, strict=True):
        mark = "PASS" if result["passed"] else "FAIL"
        print(
            f"{mark} {case['category']:<12} {case['id']:<26} {result['ms']:6.0f} ms  {result['detail']}"
        )
    metrics = summarize(cases, results)
    thresholds = dict(spec["thresholds"])
    if args.no_latency_gate:
        thresholds.pop("latency_p95_ms")
    print(f"\n{args.label or 'run'}")
    for name, value in metrics.items():
        print(f"  {name:<24} {value:8.2f}   limit {spec['thresholds'].get(name, '-')}")
    if args.json:
        Path(args.json).write_text(json.dumps({"label": args.label, **metrics}, indent=2))
    bad = failed_thresholds(metrics, thresholds)
    for line in bad:
        print(f"THRESHOLD FAILED {line}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
