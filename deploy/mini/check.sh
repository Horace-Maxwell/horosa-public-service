#!/usr/bin/env bash
# Local health check for the Mac mini: container state, /healthz, tools/list count. Read-only.
set -euo pipefail
PORT="${1:-8765}"
export DOCKER_CONTEXT="${DOCKER_CONTEXT:-colima-horosa-plugin}"
echo "container: $(docker inspect -f '{{.State.Status}} (health: {{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}, restarts: {{.RestartCount}})' horosa-public 2>/dev/null || echo 'not found')"
echo "healthz:   $(curl -fsS "http://127.0.0.1:${PORT}/healthz" || echo 'unreachable')"
TOOLS=$(curl -fsS -X POST "http://127.0.0.1:${PORT}/mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-11-25' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' 2>/dev/null \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d["result"]["tools"]))' 2>/dev/null || echo "?")
echo "tools:     ${TOOLS} (expected 24)"
