#!/usr/bin/env bash
# Run from the project directory after extracting the v2.2 update.
set -Eeuo pipefail
cd -- "$(dirname -- "$0")/.."
python3 scripts/configure_security.py
base=(docker compose -f compose.yaml)
if [[ -f .proxy-enabled && -f compose.override.yaml ]]; then
  printf '%s\n' 'Review and consolidate your existing override before enabling the supplied proxy.' >&2
  exit 1
fi
if [[ -f compose.override.yaml ]]; then base+=(-f compose.override.yaml); fi
if [[ -f .proxy-enabled ]]; then base+=(-f compose.proxy.yaml); fi
if [[ -f .storage-enabled ]]; then
  mountpoint -q /srv/fraudguard || { echo 'Durable storage is not mounted; refusing startup' >&2; exit 1; }
  base+=(-f compose.storage.yaml)
fi
if [[ -f .workers-enabled ]]; then base+=(-f compose.workers.yaml); fi
"${base[@]}" config --quiet
old_container=$("${base[@]}" ps -q api)
old_image=""
if [[ -n "$old_container" ]]; then old_image=$(docker inspect --format '{{.Image}}' "$old_container"); fi
old_monitor=$("${base[@]}" ps -q monitor)
rollback() {
  trap - ERR
  if [[ "$old_image" =~ ^sha256:[a-f0-9]{64}$ ]]; then
    printf 'services:\n  api:\n    image: "%s"\n  monitor:\n    image: "%s"\n    environment:\n      API_KEY: ${API_KEY}\n' "$old_image" "$old_image" > compose.rollback.yaml
    "${base[@]}" stop monitor || true
    recovery=("${base[@]}" -f compose.rollback.yaml)
    "${recovery[@]}" up -d --no-build --pull never api
    if [[ -n "$old_monitor" ]]; then "${recovery[@]}" up -d --no-build --pull never monitor; fi
    printf '%s\n' 'Deployment checks failed. Previous image restored; inspect logs and compose.rollback.yaml.'
  else
    printf '%s\n' 'Deployment checks failed. No previous API image was available for automatic rollback.'
  fi
  exit 1
}
trap rollback ERR
"${base[@]}" build api
"${base[@]}" up -d --no-build --wait --wait-timeout 240
"${base[@]}" exec -T api python -m fraudguard.deployment --expected-version 2.5.0 --require-backup --attempts 8 --interval 15
"${base[@]}" exec -T monitor python -m fraudguard.backups check-latest
if [[ -f .workers-enabled ]]; then "${base[@]}" exec -T api python -m fraudguard.check_workers; fi
trap - ERR
printf '%s\n' 'Deployment checks passed. Open /workspace. Prior rollback overrides, if any, are not used by this script.'
