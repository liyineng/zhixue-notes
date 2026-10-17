#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv .venv
  else
    python3 -m venv .venv
  fi
fi

if [ ! -f .venv/.deps_ok ]; then
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python .venv/bin/python -r requirements.txt
  else
    .venv/bin/python -m pip install -r requirements.txt
  fi
  touch .venv/.deps_ok
fi

PORT="${PORT:-8765}"

if curl -fsS "http://127.0.0.1:${PORT}/api/health" 2>/dev/null | grep -q '"ok":true'; then
  printf '智学笔记已经在运行：http://127.0.0.1:%s\n' "$PORT"
  exit 0
fi

if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  printf '端口 %s 已被其他程序占用，正在寻找可用端口...\n' "$PORT"
  for candidate in $(seq $((PORT + 1)) $((PORT + 30))); do
    if ! lsof -nP -iTCP:"$candidate" -sTCP:LISTEN >/dev/null 2>&1; then
      PORT="$candidate"
      break
    fi
  done
fi

RELOAD_ARGS=()
if [ "${DEV:-0}" = "1" ]; then
  RELOAD_ARGS=(--reload)
fi

printf '智学笔记已启动：http://127.0.0.1:%s\n' "$PORT"
if [ "${#RELOAD_ARGS[@]}" -gt 0 ]; then
  exec .venv/bin/python -m uvicorn app.main:app \
    --host 127.0.0.1 \
    --port "$PORT" \
    "${RELOAD_ARGS[@]}"
fi

exec .venv/bin/python -m uvicorn app.main:app \
  --host 127.0.0.1 \
  --port "$PORT"
