# Product harness

Service `harness-product`, host port 8103, container port 8080. Its own image, using the shared stub in `demo-harness/common`. FastAPI plus an empty SQLite file.

Change API: `POST /changes` returns the webhook body the service would send. Capability `webhook`. The receiver is `webhook-receiver:8090/webhooks/product`. The stub does not send it yet.
