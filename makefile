.PHONY: sync contracts-schema contracts-check test e2e networks up down ps logs notebooks tools tf-apply tf-destroy compose-check dlq-replay

COMPOSE_DIR := infrastructure/orchestration
ENV_FILE := .env
COMPOSE := docker compose --env-file $(ENV_FILE) -f $(COMPOSE_DIR)/compose.yaml
TF_DIR := infrastructure/provisioning/terraform

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
	$(MAKE) tf-apply

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

# Host Terraform. State stays in $(TF_DIR)/terraform.tfstate.
# floci is in memory: after a floci restart, delete that file or run
# tf-destroy while floci is still up, then apply again.
tf-apply: $(ENV_FILE)
	i=0; \
	while [ "$$i" -lt 30 ]; do \
	  if curl -s -o /dev/null --max-time 2 http://127.0.0.1:4566/; then \
	    break; \
	  fi; \
	  i=$$((i + 1)); \
	  sleep 2; \
	done; \
	test "$$i" -lt 30
	terraform -chdir=$(TF_DIR) init -input=false
	terraform -chdir=$(TF_DIR) apply -input=false -auto-approve -refresh=true

tf-destroy:
	terraform -chdir=$(TF_DIR) destroy -input=false -auto-approve

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
