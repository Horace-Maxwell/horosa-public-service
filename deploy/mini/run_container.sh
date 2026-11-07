#!/usr/bin/env bash
# Start (or replace) the public MCP container on the Mac mini: loopback-only, read-only, non-root, auto-restart.
# Usage: bash deploy/mini/run_container.sh <image-tag> [port]     e.g. bash deploy/mini/run_container.sh horosa-public:0.1.5 8765
# Secrets: put the OpenAI domain-verification token in ~/.config/horosa-public/challenge (one line); never pass it on the command line.
set -euo pipefail

IMAGE="${1:?image tag, e.g. horosa-public:0.1.5}"
PORT="${2:-8765}"
NAME="horosa-public"
ALLOWED_HOSTS="${HOROSA_MCP_ALLOWED_HOSTS:-mcp.horosa.org}"
CHALLENGE_FILE="${HOME}/.config/horosa-public/challenge"
export DOCKER_CONTEXT="${DOCKER_CONTEXT:-colima-horosa-plugin}"

ENV_ARGS=(--env "HOROSA_MCP_ALLOWED_HOSTS=${ALLOWED_HOSTS}")
if [ -s "${CHALLENGE_FILE}" ]; then
  ENV_ARGS+=(--env "HOROSA_OPENAI_CHALLENGE=$(head -n1 "${CHALLENGE_FILE}")")
fi

docker rm -f "${NAME}" >/dev/null 2>&1 || true
# The published port is forwarded from this Mac. If another program already listens there, the readiness probe below
# would talk to that program instead of the container. Give a just-removed container's forwarder a moment, then refuse.
for _ in 1 2 3 4 5; do
  lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1 || break
  sleep 2
done
if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port ${PORT} on this Mac is already in use by another program:"
  lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN | sed -n '1,3p'
  echo "stop that program, or pass another port: bash deploy/mini/run_container.sh ${IMAGE} <port>"
  exit 1
fi
docker run --detach --name "${NAME}" \
  --platform linux/amd64 \
  --restart unless-stopped \
  --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --pids-limit 256 \
  --memory 3g \
  --publish "127.0.0.1:${PORT}:8765" \
  "${ENV_ARGS[@]}" \
  "${IMAGE}" >/dev/null

echo "started ${NAME} from ${IMAGE}; MCP on http://127.0.0.1:${PORT}/mcp (loopback only)"
echo "waiting for readiness (cold start is usually 1-3 minutes) ..."
for i in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
    echo "ready after ~$((i * 4))s: http://127.0.0.1:${PORT}/healthz"
    exit 0
  fi
  if [ "$(docker inspect -f '{{.State.Running}}' "${NAME}" 2>/dev/null)" != "true" ]; then
    echo "container exited early; last 40 log lines:"; docker logs --tail 40 "${NAME}" || true
    exit 1
  fi
  sleep 4
done
echo "not ready after 6 minutes; last 40 log lines:"; docker logs --tail 40 "${NAME}" || true
exit 1
