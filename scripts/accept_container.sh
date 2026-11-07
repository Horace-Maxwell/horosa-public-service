#!/usr/bin/env bash
# 镜像验收（docs/PUBLIC_CONTAINER.md「验收清单」的第 2–4 条）：冷启动、tools/list、六次调用、输出键与回显检查、docker diff。
# 用法：bash scripts/accept_container.sh <image-tag>     例：bash scripts/accept_container.sh horosa-public:0.1.5
# 只绑宿主回环的随机端口；只删除本脚本自己建的容器 horosa-public-accept。证据写到 notes-local/build/acceptance-<UTC 时间>/。
set -euo pipefail

IMAGE="${1:?image tag, e.g. horosa-public:0.1.5}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export DOCKER_CONTEXT="${DOCKER_CONTEXT:-colima-horosa-plugin}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${ROOT}/notes-local/build/acceptance-${TS}"
NAME="horosa-public-accept"
mkdir -p "${OUT}"

docker rm -f "${NAME}" >/dev/null 2>&1 || true
START="$(date +%s)"
docker run --detach --name "${NAME}" --platform linux/amd64 --read-only --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 --publish 127.0.0.1::8765 \
  --env HOROSA_OPENAI_CHALLENGE="accept-token-${TS}" "${IMAGE}" >/dev/null
trap 'docker rm -f "${NAME}" >/dev/null 2>&1 || true' EXIT
PORT="$(docker port "${NAME}" 8765/tcp | head -1 | awk -F: '{print $NF}')"
docker diff "${NAME}" > "${OUT}/diff-before.txt" 2>&1 || true

READY=""
for _ in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" > "${OUT}/healthz.json" 2>/dev/null; then READY=$(( $(date +%s) - START )); break; fi
  if [ "$(docker inspect -f '{{.State.Running}}' "${NAME}")" != "true" ]; then echo "container exited"; docker logs --tail 30 "${NAME}"; exit 1; fi
  sleep 3
done
[ -n "${READY}" ] || { echo "not ready within 6 minutes"; exit 1; }
echo "cold start to /healthz: ${READY} s"
curl -fsS "http://127.0.0.1:${PORT}/.well-known/openai-apps-challenge" -o "${OUT}/challenge.txt"

H=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-11-25')
call() { curl -fsS -X POST "http://127.0.0.1:${PORT}/mcp" "${H[@]}" --data "$2" -o "${OUT}/$1.json" -w "$1: http %{http_code} bytes %{size_download}\n"; }
call tools-list '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
call chart '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"horosa_astro_chart","arguments":{"date":"1990-06-15","time":"08:30:00","zone":"+08:00","lat":"31.230416","lon":"121.473701","defaults_accepted":true}}}'
call huangli '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"horosa_cn_huangli","arguments":{"date":"2026-10-04","defaults_accepted":true}}}'
call qimen-noplace '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"horosa_cn_qimen","arguments":{"date":"2026-10-04","time":"09:30:00","zone":"+08:00","defaults_accepted":true}}}'
call bazi-place '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"horosa_cn_bazi_birth","arguments":{"date":"1990-06-15","time":"08:30:00","zone":"+08:00","lat":"31n13","lon":"121e28","gender":1,"defaults_accepted":true}}}'
call horary-noplace '{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"horosa_chart_horary","arguments":{"date":"2026-10-04","time":"09:30:00","zone":"+08:00","defaults_accepted":true}}}'
call tarot-withplace-rejected '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"horosa_cn_tarot","arguments":{"date":"2026-10-04","time":"09:30:00","zone":"+08:00","lat":"31n13","lon":"121e28","defaults_accepted":true}}}'
docker diff "${NAME}" > "${OUT}/diff-after.txt" 2>&1 || true
STATS="$(docker stats --no-stream --format '{{.MemUsage}}|{{.PIDs}}' "${NAME}")"
VERSIONS="$(docker exec "${NAME}" /opt/horosa/mcp-venv/bin/python -c 'import horosa_public, horosa_skill; print(horosa_public.__version__, horosa_skill.__version__)')"
IMAGE_ID="$(docker image inspect -f '{{.Id}}' "${IMAGE}" | cut -c8-19)"

python3 - "${OUT}" "${IMAGE}" "${IMAGE_ID}" "${READY}" "${STATS}" "${VERSIONS}" <<'EOF'
import json, pathlib, re, sys
out, image, image_id, ready, stats, versions = pathlib.Path(sys.argv[1]), *sys.argv[2:]
public_version, skill_version = versions.split()
tools = json.load(open(out / "tools-list.json"))["result"]["tools"]
noauth = all(t.get("annotations", {}).get("readOnlyHint") is True and t.get("_meta", {}).get("securitySchemes") == [{"type": "noauth"}] for t in tools)
forbidden = ["trace_id", "group_id", "memory_ref", "run_id", "generated_at", "timestamp", "input_normalized", "artifact_path"]
calls, leaked_total, failures = {}, 0, []
for name in ("chart", "huangli", "qimen-noplace", "bazi-place", "horary-noplace", "tarot-withplace-rejected"):
    result = json.load(open(out / f"{name}.json"))["result"]
    content = result["structuredContent"]
    raw = json.dumps(content, ensure_ascii=False)
    leaked = [key for key in forbidden if f'"{key}"' in raw]
    leaked_total += len(leaked)
    error = (content.get("error") or {}).get("code")
    calls[name] = f"ok {len(content.get('sections', []))} sections" if content["ok"] else str(error)
    print(f"{name:26s} ok={content['ok']} sections={len(content.get('sections', []))} warnings={content.get('warnings')} error={error} leaked={leaked}")
    expected_ok = name not in ("horary-noplace", "tarot-withplace-rejected")
    if content["ok"] is not expected_ok:
        failures.append(f"{name}: ok={content['ok']}")
expected_errors = {"horary-noplace": "public.location_unavailable", "tarot-withplace-rejected": "tool.public_invalid_payload"}
for name, code in expected_errors.items():
    if calls[name] != code:
        failures.append(f"{name}: {calls[name]} != {code}")
qimen = "\n".join(s["text"] for s in json.load(open(out / "qimen-noplace.json"))["result"]["structuredContent"].get("sections", []))
place_echo = "经纬度" in qimen or "真太阳时" in qimen
if place_echo:
    failures.append("qimen-noplace echoes a synthesized place")
diff_lines = len((out / "diff-after.txt").read_text().splitlines())
if len(tools) != 24 or not noauth:
    failures.append(f"tools={len(tools)} noauth={noauth}")
if leaked_total or diff_lines:
    failures.append(f"leaked={leaked_total} docker_diff_lines={diff_lines}")
memory, pids = stats.split("|")
summary = {
    "image": f"{image} ({image_id})", "public": public_version, "skill": skill_version,
    "cold_start_seconds_to_healthz": int(ready), "tools": len(tools), "all_noauth_read_only": noauth,
    "tools_with_lat_in_schema": sum("lat" in t["inputSchema"].get("properties", {}) for t in tools),
    "calls": calls, "qimen_place_echo": place_echo,
    "forbidden_output_keys_leaked": leaked_total, "docker_diff_lines_after_calls": diff_lines,
    "memory_after_calls": memory.split("/")[0].strip(), "pids": int(pids), "failures": failures,
    "evidence_dir": str(out.relative_to(out.parents[2])),
}
(out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
sys.exit(1 if failures else 0)
EOF
