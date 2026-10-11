# Webhook receiver

Compose service `webhook-receiver`, port 8090. `POST /webhooks/product` accepts a body and does not enqueue it.

The production path is API Gateway plus Lambda. That function is not built, so there is no `functions/` directory.
