#!/usr/bin/env bash
# Use this wrapper so optional workers and existing HTTPS settings stay applied.
set -Eeuo pipefail
cd -- "$(dirname -- "$0")/.."
args=(docker compose -f compose.yaml)
if [[ -f .proxy-enabled && -f compose.override.yaml ]]; then
  printf '%s\n' 'Review and consolidate your existing override before enabling the supplied proxy.' >&2
  exit 1
fi
if [[ -f compose.override.yaml ]]; then args+=(-f compose.override.yaml); fi
if [[ -f .proxy-enabled ]]; then args+=(-f compose.proxy.yaml); fi
if [[ -f .workers-enabled ]]; then args+=(-f compose.workers.yaml); fi
exec "${args[@]}" "$@"
