# Harness helpers

Shared SQLAlchemy store and read API for Sales, Product, and Engineering. No image of its own. HR does not use this package.

`Database` opens a SQLite file through SQLAlchemy. `fetch_keyset` pages `updated_since` listings by `(updated_at, id)`: the bound is inclusive and the page token is exclusive, so a shared timestamp is not skipped. `register_catalog` mounts `/health`, `/discover`, `/ids`, and `/metrics` (`kbs_harness_up`, `kbs_harness_objects`). Each department harness owns its tables, its change feed, and its admin routes.
