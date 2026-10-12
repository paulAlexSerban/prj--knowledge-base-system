#!/usr/bin/env bash
# One-command setup: preflight, .env, build, start, wait for health, seed, smoke test.
#
#   make setup            first run or re-run (idempotent)
#   make setup ARGS=--reset   wipe containers and volumes first
#
# Needs Docker with Compose v2 and uv. Terraform runs in a container.
set -euo pipefail
cd "$(dirname "$0")/../../"

COMPOSE=(docker compose --env-file .env -f infrastructure/orchestration/compose.yaml)
HEALTH_SECONDS="${HEALTH_SECONDS:-420}"
PORTS=(3000 4566 8000 8001 8090 8101 8102 8103 8104 8501 9090)
start=$(date +%s)

say() { printf '\n== %s\n' "$*"; }
die() { printf 'setup failed: %s\n' "$*" >&2; exit 1; }

preflight() {
  say "preflight"
  command -v docker >/dev/null || die "docker is not installed"
  docker info >/dev/null 2>&1 || die "the docker daemon is not reachable"
  docker compose version >/dev/null 2>&1 || die "docker compose v2 is missing"
  command -v uv >/dev/null || die "uv is not installed (https://docs.astral.sh/uv/)"
  if [ -r /proc/meminfo ]; then
    mem_gb=$(awk '/MemTotal/ {printf "%d", $2/1048576}' /proc/meminfo)
    [ "$mem_gb" -ge 8 ] || echo "warning: ${mem_gb} GB RAM; the stack wants about 8 GB"
  fi
  # A port held by one of our own containers is fine; anything else is a clash.
  running=$(docker ps --format '{{.Names}}' | grep -c '^kbs-' || true)
  if [ "$running" -eq 0 ]; then
    for port in "${PORTS[@]}"; do
      if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
        die "port $port is already in use"
      fi
    done
  fi
  echo "ok"
}

wait_healthy() {
  say "waiting for health (up to ${HEALTH_SECONDS}s; Neo4j needs about two minutes)"
  deadline=$(( $(date +%s) + HEALTH_SECONDS ))
  while true; do
    bad=$("${COMPOSE[@]}" ps --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null |
      awk '$2=="running" && $3!="" && $3!="healthy" {print $1}
           $2=="exited" || $2=="dead" || $2=="restarting" {print $1}' |
      grep -v -e '^terraform-apply$' -e '^floci-ready$' -e '-migrate$' | sort -u | tr '\n' ' ' || true)
    [ -z "$bad" ] && break
    if [ "$(date +%s)" -ge "$deadline" ]; then
      die "not healthy after ${HEALTH_SECONDS}s: $bad(run: make logs)"
    fi
    sleep 5
  done
  echo "all services healthy"
}

for arg in "$@"; do
  case "$arg" in
    --reset)
      say "reset"
      [ -f .env ] || cp .env.example .env
      "${COMPOSE[@]}" down --remove-orphans --volumes
      ;;
    *) die "unknown argument $arg" ;;
  esac
done

preflight
say "environment"
if [ -f .env ]; then echo ".env exists, left as is"; else cp .env.example .env && echo "created .env from .env.example"; fi
say "python dependencies"
uv sync --quiet
say "build and start (first run downloads images and models)"
make --no-print-directory up
wait_healthy
say "seed"
make --no-print-directory seed
say "smoke test"
make --no-print-directory demo ARGS=--quick || die "the demo smoke test failed"

elapsed=$(( $(date +%s) - start ))
cat <<EOF

Ready in ${elapsed}s.

  Chat page   http://localhost:8501   personas: employee_eng, hr_admin, outsider, product_lead
  Grafana     http://localhost:3000   admin / admin
  REST API    http://localhost:8000/docs
  MCP server  http://localhost:8001/mcp
  Jupyter     make notebooks, then http://localhost:8888 (token in .env)

  make demo           full demo script (add ARGS=--chaos for the worker restart)
  make down           stop everything
EOF
