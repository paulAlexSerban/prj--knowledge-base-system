"""Read routes every department harness exposes: health, discover, ids, metrics."""

from collections.abc import Callable

from fastapi import FastAPI, HTTPException
from prometheus_client import make_asgi_app

from harness_common.db import Conflict, Invalid, NotFound
from harness_common.metrics import gauge
from shared.http_metrics import HttpMetricsMiddleware

Counts = Callable[[], dict[str, int]]
Ids = Callable[[], dict[str, list[dict[str, object]]]]
Now = Callable[[], str]

CATALOG_ROUTES = ("/health", "/discover", "/ids", "/metrics", "/changes")


def register_catalog(
    app: FastAPI,
    *,
    service: str,
    capabilities: list[str],
    object_types: list[dict[str, object]],
    native_acls: list[str],
    counts: Counts,
    ids: Ids,
    now: Now,
    routes: tuple[str, ...] = (),
) -> Callable[[], None]:
    """Register the shared read routes. Returns a function that refreshes object gauges."""
    up = gauge("kbs_harness_up", "1 when a harness service is serving", ["service"])
    objects = gauge(
        "kbs_harness_objects", "Live objects in the source", ["service", "object_type"]
    )
    up.labels(service=service).set(1)

    def refresh() -> None:
        for kind, count in counts().items():
            objects.labels(service=service, object_type=kind).set(count)

    refresh()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": service,
            "capability": capabilities[0] if capabilities else "",
        }

    @app.get("/discover")
    def discover() -> dict[str, object]:
        return {
            "service": service,
            "capabilities": capabilities,
            "object_types": object_types,
            "native_acls": native_acls,
            "counts": counts(),
        }

    @app.get("/ids")
    def list_ids() -> dict[str, object]:
        """Every live id and version. The reconciliation sweep diffs this against the index."""
        return {"service": service, "server_time": now(), "ids": ids()}

    app.mount("/metrics", make_asgi_app())
    app.add_middleware(HttpMetricsMiddleware, templates=CATALOG_ROUTES + routes)
    return refresh


def guard[T](action: Callable[[], T], refresh: Callable[[], None]) -> T:
    try:
        result = action()
    except NotFound as error:
        raise HTTPException(404, f"not found: {error}") from error
    except Conflict as error:
        raise HTTPException(409, f"conflict: {error}") from error
    except Invalid as error:
        raise HTTPException(422, str(error)) from error
    refresh()
    return result
