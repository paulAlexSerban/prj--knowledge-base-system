# HR harness

Service `harness-hr`, host port 8101, container port 8080. FastAPI plus SQLite.

Change API: `GET /changes?updated_since=`. Capability `updated_since`. No webhook. The connector polls this source in-process because it is tier P0.
