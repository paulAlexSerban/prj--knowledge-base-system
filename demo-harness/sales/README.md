# Sales harness

Service `harness-sales`, host port 8102, container port 8080. Its own image, using the shared stub in `demo-harness/common`. FastAPI plus an empty SQLite file.

Change API: `GET /changes?cursor=`. Capability `cursor_feed`.
