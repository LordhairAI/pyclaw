#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

export UV_CACHE_DIR="${UV_CACHE_DIR:-.uv-cache}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
RELOAD="${RELOAD:-1}"

if [[ "$RELOAD" == "1" ]]; then
  exec uv run uvicorn app.main:app --reload --host "$HOST" --port "$PORT"
else
  exec uv run uvicorn app.main:app --host "$HOST" --port "$PORT"
fi
