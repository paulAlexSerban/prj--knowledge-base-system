.PHONY: setup seed demo eval reindex dashboards sync contracts-schema contracts-check test e2e networks up down ps logs notebooks tools tf-apply compose-check dlq-replay

COMPOSE_DIR := infrastructure/orchestration
ENV_FILE := .env
COMPOSE := docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.yaml

sync:
	uv sync

contracts-schema:
	uv run python -m shared.contracts.export_schema

contracts-check: contracts-schema
	uv run python -m shared.contracts.validate infrastructure/config/sources
	uv run pytest shared/src/shared/contracts/tests
	git diff --exit-code -- shared/src/shared/contracts/source_contract.schema.json

test:
	uv run pytest

e2e:
	uv run pytest -o addopts= -m e2e backend/tests/e2e

$(ENV_FILE):
	cp .env.example $(ENV_FILE)

networks:
	@docker network inspect kbs-data >/dev/null 2>&1 || docker network create --internal kbs-data
	@docker network inspect kbs-sources >/dev/null 2>&1 || docker network create kbs-sources
	@docker network inspect kbs-aws >/dev/null 2>&1 || docker network create kbs-aws
	@docker network inspect kbs-obs >/dev/null 2>&1 || docker network create kbs-obs
	@docker network inspect kbs-tools >/dev/null 2>&1 || docker network create kbs-tools

up: networks $(ENV_FILE)
	$(COMPOSE) up -d --build

# One command: preflight, .env, build, start, wait for health, seed, smoke test.
setup:
	bash tools/scripts/setup.sh $(ARGS)

# Rebuild the index under the active EMBEDDER (ADR 0003), then drop other models' chunks.
reindex: networks $(ENV_FILE)
	$(COMPOSE) run --rm --no-deps --no-TTY ingestion-worker python -m ingestion_worker.reindex

seed:
	uv run python tools/scripts/seed.py

eval:
	uv run python tools/eval/run.py $(ARGS)

demo:
	uv run python tools/scripts/demo.py $(ARGS)

# Re-read observability/grafana/dashboards into the running Grafana.
# The JSON is bind-mounted; Grafana keeps a copy in its database until this reload.
dashboards: networks $(ENV_FILE)
	$(COMPOSE) up -d grafana
	@password=$$(set -a && . ./$(ENV_FILE) && set +a && printf '%s' "$${GRAFANA_ADMIN_PASSWORD:-admin}"); \
	i=0; \
	until curl -sf -u "admin:$$password" http://127.0.0.1:3000/api/health >/dev/null; do \
		i=$$((i + 1)); \
		if [ $$i -ge 30 ]; then echo "grafana did not become ready on :3000" >&2; exit 1; fi; \
		sleep 1; \
	done; \
	curl -fsS -u "admin:$$password" -X POST http://127.0.0.1:3000/api/admin/provisioning/dashboards/reload; \
	echo

down:
	@test -f $(ENV_FILE) || cp .env.example $(ENV_FILE)
	$(COMPOSE) down --remove-orphans

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs --tail=200

notebooks: networks $(ENV_FILE)
	$(COMPOSE) up -d --build jupyter

tools: networks $(ENV_FILE)
	$(COMPOSE) up -d postgres postgres-migrate pgadmin \
		harness-hr harness-sales harness-product harness-engineering \
		sqlite-hr sqlite-sales sqlite-product sqlite-engineering

# Terraform runs in a one-shot container with throwaway state. floci is in memory,
# so after a floci restart run `make tf-apply` again to recreate queues and bucket.
tf-apply: networks $(ENV_FILE)
	$(COMPOSE) up --no-deps --force-recreate terraform-apply

# Re-send dead-letter messages to the tier queues. floci must already be up.
dlq-replay: $(ENV_FILE)
	set -a && . ./$(ENV_FILE) && set +a && uv run python tools/scripts/dlq_replay.py

compose-check: $(ENV_FILE)
	$(COMPOSE) config -q
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.aws.yaml config -q
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.knowledge-store.yaml config -q
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.ingestion-pipeline.yaml config -q
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.knowledge-gateway.yaml config -q
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.demo-harness.yaml config -q
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.observability.yaml config -q
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.frontend.yaml config -q
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.notebooks.yaml config -q
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.tools.yaml config -q
	docker run --rm --entrypoint promtool \
		-v $(CURDIR)/observability/prometheus:/prometheus:ro \
		prom/prometheus:v3.15.0 check rules /prometheus/rules.yml
