#!/usr/bin/env bash
# Install the daily 04:00 restart of the horosa-public container on the Mac mini, run it once now, and prove it worked.
# The privacy policy (site/privacy.html, Retention) says the engine's in-memory result cache is cleared by a daily restart,
# so request data is kept for at most 24 hours. Keep this job installed for as long as that sentence is published.
# Self-contained: copy this one file to the mini and run  bash install_daily_restart.sh [port]
# A Mac that sleeps through 04:00 runs the job when it wakes; the mini is set never to sleep.
set -euo pipefail

PORT="${1:-8765}"
LABEL="com.horosa.public-daily-restart"
TARGET="${HOME}/Library/LaunchAgents/${LABEL}.plist"
export DOCKER_CONTEXT="${DOCKER_CONTEXT:-colima-horosa-plugin}"

DOCKER_BIN="$(command -v docker || true)"
if [ -z "${DOCKER_BIN}" ]; then
  echo "docker not found on PATH; run deploy/mini/setup_mini.sh first"; exit 1
fi
if [ "$(docker inspect -f '{{.State.Running}}' horosa-public 2>/dev/null)" != "true" ]; then
  echo "container horosa-public is not running; start it with deploy/mini/run_container.sh first"; exit 1
fi

mkdir -p "${HOME}/Library/LaunchAgents"
cat > "${TARGET}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${DOCKER_BIN}</string>
    <string>--context</string>
    <string>${DOCKER_CONTEXT}</string>
    <string>restart</string>
    <string>horosa-public</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/tmp/horosa-public-daily-restart.out.log</string>
  <key>StandardErrorPath</key><string>/tmp/horosa-public-daily-restart.err.log</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$(dirname "${DOCKER_BIN}"):/usr/local/bin:/usr/bin:/bin</string></dict>
</dict>
</plist>
PLIST
plutil -lint "${TARGET}" >/dev/null
launchctl bootout "gui/$(id -u)/${LABEL}" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "${TARGET}"

BEFORE="$(docker inspect -f '{{.State.StartedAt}}' horosa-public)"
echo "installed ${TARGET}; running it once now to prove it works (the service is down for about a minute) ..."
launchctl kickstart -k "gui/$(id -u)/${LABEL}"
for _ in $(seq 1 30); do
  AFTER="$(docker inspect -f '{{.State.StartedAt}}' horosa-public 2>/dev/null || true)"
  [ -n "${AFTER}" ] && [ "${AFTER}" != "${BEFORE}" ] && break
  sleep 2
done
if [ "${AFTER:-}" = "${BEFORE}" ] || [ -z "${AFTER:-}" ]; then
  echo "the job did not restart the container; see /tmp/horosa-public-daily-restart.err.log"; exit 1
fi
echo "container restarted (started at ${AFTER}); waiting for readiness ..."
for i in $(seq 1 90); do
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
    echo "ready after ~$((i * 4))s. Daily restart is scheduled at 04:00:"
    launchctl print "gui/$(id -u)/${LABEL}" | grep -E 'state|Hour|Minute' | head -5 || true
    exit 0
  fi
  sleep 4
done
echo "container restarted but /healthz is not answering after 6 minutes; run deploy/mini/check.sh"; exit 1
