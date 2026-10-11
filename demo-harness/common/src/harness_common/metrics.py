"""Harness gauges. A second importer reuses the series already registered."""

from prometheus_client import REGISTRY, Gauge


def gauge(name: str, documentation: str, labels: list[str]) -> Gauge:
    existing = REGISTRY._names_to_collectors.get(name)
    if isinstance(existing, Gauge):
        return existing
    return Gauge(name, documentation, labels)
