#!/usr/bin/env bash
# One-command server deploy: fill in .env, then run ./deploy.sh
#
#   ./deploy.sh            build + start (default)
#   ./deploy.sh update     git pull, rebuild, restart
#   ./deploy.sh logs       follow application logs
#   ./deploy.sh status     container state + /health
#   ./deploy.sh stop       stop everything
set -euo pipefail

cd "$(dirname "$0")"

log()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

# Read KEY from .env without sourcing it (values may contain shell metacharacters).
env_get() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' || true; }

# Set KEY=VALUE in .env (replace if present, append otherwise).
env_set() {
  if grep -qE "^$1=" .env; then
    local tmp; tmp="$(mktemp)"
    awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' .env > "$tmp"
    cat "$tmp" > .env && rm -f "$tmp"
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}

random_hex() { openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    [ "$(uname -s)" = "Linux" ] || die "Docker is not installed. Install Docker Desktop, then re-run."
    log "Docker not found - installing via the official get.docker.com script"
    command -v curl >/dev/null 2>&1 || die "curl is required to install Docker"
    curl -fsSL https://get.docker.com | $SUDO sh
    $SUDO systemctl enable --now docker >/dev/null 2>&1 || true
  fi
  if docker info >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="$SUDO docker"; fi
  $DOCKER compose version >/dev/null 2>&1 || die "'docker compose' (v2 plugin) is required"
}

compose() {
  local profiles=()
  [ -n "$(env_get DOMAIN)" ] && profiles=(--profile https)
  $DOCKER compose "${profiles[@]}" "$@"
}

prepare_env() {
  if [ ! -f .env ]; then
    cp .env.example .env
    die ".env created from .env.example. Fill in TURSO_DATABASE_URL, TURSO_AUTH_TOKEN and your other keys, then run ./deploy.sh again."
  fi

  local missing=()
  for key in TURSO_DATABASE_URL TURSO_AUTH_TOKEN; do
    [ -n "$(env_get "$key")" ] || missing+=("$key")
  done
  [ "${#missing[@]}" -eq 0 ] || die "Missing required value(s) in .env: ${missing[*]}"

  # Generate secrets the operator should not have to invent.
  [ -n "$(env_get TELEGRAM_WEBHOOK_SECRET)" ] || { env_set TELEGRAM_WEBHOOK_SECRET "$(random_hex)"; log "Generated TELEGRAM_WEBHOOK_SECRET"; }
  [ -n "$(env_get DASHBOARD_TOKEN)" ]         || { env_set DASHBOARD_TOKEN "$(random_hex)"; log "Generated DASHBOARD_TOKEN"; }

  # Telegram webhooks need a public HTTPS URL; derive it from DOMAIN.
  local domain; domain="$(env_get DOMAIN)"
  if [ -n "$domain" ] && [ -z "$(env_get PUBLIC_BASE_URL)" ]; then
    env_set PUBLIC_BASE_URL "https://$domain"
    log "Set PUBLIC_BASE_URL=https://$domain"
  fi
  if [ -n "$(env_get TELEGRAM_BOT_TOKEN)" ] && [ -z "$(env_get PUBLIC_BASE_URL)" ]; then
    warn "TELEGRAM_BOT_TOKEN is set but neither DOMAIN nor PUBLIC_BASE_URL is: Telegram buttons/commands need a public HTTPS URL."
  fi
  { [ -n "$(env_get BINGX_API_KEY)" ] && [ -n "$(env_get BINGX_SECRET)" ]; } || warn "BINGX_API_KEY/BINGX_SECRET not set: no VST orders can be placed."
  { [ -n "$(env_get AZURE_ANTHROPIC_API_KEY)" ] || [ -n "$(env_get OPENAI_API_KEY)" ] || [ -n "$(env_get AZURE_OPENAI_API_KEY)" ]; } \
    || warn "No AI key set (AZURE_ANTHROPIC_API_KEY or OpenAI/Azure OpenAI): the AI will always SKIP."
}

health_port() { local p; p="$(env_get HOST_PORT)"; echo "${p:-5000}"; }

wait_healthy() {
  local port; port="$(health_port)"
  log "Waiting for the service to become healthy (up to 90s)"
  for _ in $(seq 1 45); do
    if curl -fsS "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
      log "Healthy: http://127.0.0.1:${port}/health"
      return 0
    fi
    sleep 2
  done
  warn "Service did not become healthy in time. Recent logs:"
  compose logs --tail=60 app || true
  return 1
}

cmd="${1:-up}"
ensure_docker

case "$cmd" in
  up)
    prepare_env
    log "Building and starting containers"
    compose up -d --build
    wait_healthy
    domain="$(env_get DOMAIN)"
    if [ -n "$domain" ]; then log "Public URL: https://$domain/health"
    else log "No DOMAIN set: service is bound to 127.0.0.1 only (put your own reverse proxy in front)."; fi
    log "Logs: ./deploy.sh logs"
    ;;
  update)
    if command -v git >/dev/null 2>&1 && [ -d .git ]; then git pull --ff-only; else warn "Skipping git pull"; fi
    prepare_env
    compose up -d --build
    wait_healthy
    ;;
  logs)   compose logs -f --tail=100 app ;;
  status) compose ps; curl -fsS "http://127.0.0.1:$(health_port)/health" || true; echo ;;
  stop)   compose down ;;
  *)      die "Unknown command '$cmd' (use: up | update | logs | status | stop)" ;;
esac
