# Engineering harness

Service `harness-engineering`, host port 8104, container port 8080. Its own image, using the shared stub in `demo-harness/common`. FastAPI plus an empty SQLite file.

Change API: `GET /changes?updated_since=`. Capability `updated_since`. Jira-like epics are not loaded yet.
