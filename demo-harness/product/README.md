# Product harness

Service `harness-product`, host port 8103, container port 8080. FastAPI plus files, with SQLite for the change log stub.

Change API: `POST /changes` returns the webhook body the service would send. Capability `webhook`. The receiver is `webhook-receiver:8090/webhooks/product`. The stub does not send it yet.
