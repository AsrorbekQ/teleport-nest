#!/bin/sh
# Starts Teleport Nest on http://127.0.0.1:8787 (override with server.host/port in config.toml).
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
fi
HOST=$(.venv/bin/python -c "from nest.config import load_config as l; print(l().host)")
PORT=$(.venv/bin/python -c "from nest.config import load_config as l; print(l().port)")
exec .venv/bin/uvicorn nest.app:app --host "$HOST" --port "$PORT"
