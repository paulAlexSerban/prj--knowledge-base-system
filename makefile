.PHONY: sync contracts-schema contracts-check test networks up down ps logs compose-check

COMPOSE_DIR := infrastructure/orchestration
ENV_FILE := .env
COMPOSE := docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.yaml

sync:
	uv sync

contracts-schema:
	uv run python -m shared.contracts.export_schema

contracts-check: contracts-schema
	uv run python -m shared.contracts.validate infrastructure/config/sources
	uv run pytest shared/contracts/tests
	git diff --exit-code -- shared/contracts/source_contract.schema.json

test:
	uv run pytest

$(ENV_FILE):
	cp .env.example $(ENV_FILE)

networks:
	@docker network inspect kbs-data >/dev/null 2>&1 || docker network create --internal kbs-data
	@docker network inspect kbs-sources >/dev/null 2>&1 || docker network create kbs-sources
	@docker network inspect kbs-aws >/dev/null 2>&1 || docker network create kbs-aws
	@docker network inspect kbs-obs >/dev/null 2>&1 || docker network create kbs-obs

up: networks $(ENV_FILE)
	$(COMPOSE) up -d --build

down:
	@test -f $(ENV_FILE) || cp .env.example $(ENV_FILE)
	$(COMPOSE) down --remove-orphans

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs --tail=200

compose-check: $(ENV_FILE)
	$(COMPOSE) config -q
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.aws.yaml config -q
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.knowledge-store.yaml config -q
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.ingestion-pipeline.yaml config -q
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.knowledge-gateway.yaml config -q
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.demo-harness.yaml config -q
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.observability.yaml config -q
