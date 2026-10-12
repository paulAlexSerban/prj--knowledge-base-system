# Webhook receiver

Compose service `webhook-receiver`, port 8090. `POST /webhooks/product` checks the pointer and puts it on `kbs-product-hooks`. The response is 202 only after SQS accepts it. An invalid body is 422 and is not enqueued.

The production path is API Gateway plus Lambda. That function is not built, so there is no `functions/` directory.
