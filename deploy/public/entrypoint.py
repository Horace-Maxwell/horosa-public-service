#!/usr/bin/env python3
"""Supervise the three public processes (chart service, Java backend, public MCP) without recording requests or raw logs.

Ported from horosa-skill branch codex/chatgpt-plugin (052ce55) deploy/public/entrypoint.py; the MCP process is now
this project's `horosa-public serve` from an installed venv, and the chart service gets SE_EPHE_PATH.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = Path("/opt/horosa")
PREWARM = re.compile(r"^kentang prewarm ready in [0-9.]+s \(loaded=([0-9]+), failed=([0-9]+)\)$")


def read_json(url: str) -> dict:
    # Explicitly ignore host HTTP(S)_PROXY settings for local readiness probes.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=2) as response:
            data = json.loads(response.read(32768))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def base_environment() -> dict[str, str]:
    """No inherited credentials, proxy, tracing or cloud-decision environment."""
    return {
        "PATH": f"{ROOT}/node/bin:{ROOT}/java/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(ROOT / "no-store"), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
    }


def commands(contract: dict, environ: dict[str, str]) -> list[tuple[str, list[str], dict[str, str]]]:
    ports = contract["ports"]
    chart_url = f"http://127.0.0.1:{ports['chart']}"
    java_url = f"http://127.0.0.1:{ports['java']}"
    chart_env = base_environment() | {
        "PYTHONPATH": f"{ROOT}/Horosa-Web/flatlib-ctrad2:{ROOT}/Horosa-Web/astropy:{ROOT}/Horosa-Web/vendor",
        "HOROSA_CHART_PORT": str(ports["chart"]),
        "HOROSA_STARTUP_LEDGER": "0", "HOROSA_EPHE_PATH_FASTPATH": "0",
        # libswe resolves its ephemeris directory from this variable on first use in every thread.
        "SE_EPHE_PATH": str(ROOT / contract["ephemeris_path"]),
    }
    java_env = base_environment() | {
        "HOROSA_DESKTOP_MONGO_OPTIONAL": "1", "HOROSA_DESKTOP_MONGO_SKIP_PING": "1",
        "HOROSA_MONGO_FALLBACK_DIR": str(ROOT / "no-store/cache"),
        "HOROSA_NONGLI_DAY_PERSIST": "0",
        "needtranslog": "false", "SPRING_MAIN_LAZY_INITIALIZATION": "true",
        "HOROSA_STARTUP_LEDGER": "0",
        "HOROSA_ENABLE_STARTUP_CRON": "0", "HOROSA_ENABLE_STARTUP_TRANSGROUP_INIT": "0",
    }
    # Only what horosa_public.config allows: explicit loopback backends, JS engine roots, bind address.
    # No store/trace/cloud keys exist here; the public config refuses to start if any appear.
    mcp_env = base_environment() | {
        "HOROSA_SERVER_ROOT": java_url, "HOROSA_CHART_SERVER_ROOT": chart_url,
        "HOROSA_CORE_JS_ROOT": str(ROOT / "horosa-core-js"), "HOROSA_NODE_BIN": str(ROOT / "node/bin/node"),
        "HOROSA_SKILL_HOST": "0.0.0.0", "HOROSA_SKILL_PORT": str(ports["mcp"]),
    }
    for key in ("HOROSA_MCP_ALLOWED_HOSTS", "HOROSA_MCP_MAX_CONCURRENT_TOOLS", "HOROSA_OPENAI_CHALLENGE"):
        if environ.get(key):
            mcp_env[key] = environ[key]
    java = [
        str(ROOT / "java/bin/java"), "-Xms128m", "-Xmx1536m", "-Djava.awt.headless=true",
        "-Duser.home=" + str(ROOT / "no-store"), "-Djava.io.tmpdir=/tmp",
        "-Duser.language=zh", "-Duser.country=CN", "-Dfile.encoding=UTF-8", "-Dsun.jnu.encoding=UTF-8",
        "-Dlog4j2.configurationFile=file:" + str(ROOT / "log4j2.xml"), "-Dlog4j2.statusLevel=OFF",
        "-Dparamhash.cache.enable=false", "-Dparamhash.cache.local.enable=false", "-Dparamhash.cache.redis.enable=false",
        "-Dhorosa.cache.lazyinit=true", "-Dspring.main.banner-mode=off",
        "-jar", str(ROOT / "runtime/linux/bundle/astrostudyboot.jar"),
        f"--server.port={ports['java']}", "--server.address=127.0.0.1", f"--astrosrv={chart_url}",
        "--mongodb.ip=127.0.0.1", "--mongodb.port=9", "--redis.ip=127.0.0.1", "--redis.port=9",
        "--paramhash.cache.redis.enable=false",
    ]
    return [
        ("chart", [str(ROOT / "chart-venv/bin/python"), str(ROOT / "Horosa-Web/astropy/websrv/webchartsrv.py")], chart_env),
        ("java", java, java_env),
        ("mcp", [str(ROOT / "mcp-venv/bin/horosa-public"), "serve", "--host", "0.0.0.0", "--port", str(ports["mcp"])], mcp_env),
    ]


def validate_sandbox(root: Path = ROOT) -> None:
    if os.geteuid() == 0:
        raise ValueError("公共容器必须使用非 root 用户 / Public container must run as a non-root user")
    denied = root / "no-store"
    if not denied.is_dir() or denied.stat().st_uid != 0 or denied.stat().st_mode & 0o222:
        raise ValueError("请求缓存根必须由 root 拥有且不可写 / Request cache root must be root-owned and non-writable")
    if any(denied.iterdir()) or os.access(denied, os.W_OK):
        raise ValueError("请求缓存根必须为空且不可写 / Request cache root must be empty and non-writable")
    # Refuse starts where another layer made the runtime or source writable.
    if os.access(root, os.W_OK):
        raise ValueError("公共运行目录必须不可写 / Public runtime directory must be non-writable")


def process_cwd(name: str) -> Path:
    # Match the upstream chart launcher; Java and MCP keep the bundle root.
    return ROOT / "Horosa-Web/astropy/websrv" if name == "chart" else ROOT


def drain_chart(stream, state: dict[str, int], ready: threading.Event) -> None:
    # Raw child output is discarded forever; only two readiness counters survive.
    while True:
        line = stream.readline(4096)
        if not line:
            return
        match = PREWARM.fullmatch(line.strip())
        if match:
            state["loaded"], state["failed"] = map(int, match.groups())
            ready.set()


def stop_children(children: list[subprocess.Popen]) -> None:
    # Every listed child was started in a fresh session. Signal only those exact
    # groups, including transient Node workers spawned by MCP requests.
    def signal_group(child: subprocess.Popen, signum: int) -> None:
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    for child in reversed(children):
        signal_group(child, signal.SIGTERM)
    deadline = time.monotonic() + 10
    for child in reversed(children):
        try:
            child.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            signal_group(child, signal.SIGKILL)
            child.wait(timeout=5)
        # The session leader can exit before its Node child. Clean that same
        # created group after waiting so workers cannot outlive the container.
        signal_group(child, signal.SIGKILL)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()
    contract = json.loads((ROOT / "public_container.json").read_text(encoding="utf-8"))
    if args.healthcheck:
        return 0 if read_json(f"http://127.0.0.1:{contract['ports']['mcp']}/healthz").get("ok") is True else 1
    try:
        validate_sandbox()
    except ValueError:
        print("Public container safety check failed; inspect the non-root/read-only launch configuration.", file=sys.stderr)
        return 2
    children = []
    stopping = threading.Event()
    prewarm = threading.Event()
    state: dict[str, int] = {}
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    specs = commands(contract, dict(os.environ))
    try:
        for name, command, environment in specs[:2]:
            child = subprocess.Popen(command, env=environment, cwd=process_cwd(name), stdout=subprocess.PIPE if name == "chart" else subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", start_new_session=True)
            children.append(child)
            if name == "chart":
                threading.Thread(target=drain_chart, args=(child.stdout, state, prewarm), daemon=True).start()
        deadline = time.monotonic() + contract["startup_timeout_seconds"]
        while not stopping.wait(0.25):
            if any(child.poll() is not None for child in children) or time.monotonic() >= deadline:
                print("Public backend startup failed or exceeded its budget.", file=sys.stderr)
                return 1
            if not prewarm.is_set():
                continue
            if state.get("failed") != 0 or state.get("loaded", 0) < 1:
                print("Public backend prewarm failed; the MCP endpoint remains closed.", file=sys.stderr)
                return 1
            chart = read_json(f"http://127.0.0.1:{contract['ports']['chart']}/healthz")
            java = read_json(f"http://127.0.0.1:{contract['ports']['java']}/horosaIdentity")
            if chart.get("warm") is True and str(java.get("app", "")).startswith("horosa"):
                break
        if stopping.is_set():
            return 0
        _, command, environment = specs[2]
        children.append(subprocess.Popen(command, env=environment, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         start_new_session=True))
        print(f"Public backends ready (loaded={state['loaded']}, failed=0).", flush=True)
        while not stopping.wait(0.25):
            if any(child.poll() is not None for child in children):
                print("A public service exited; stopping its sibling processes.", file=sys.stderr)
                return 1
        return 0
    except OSError:
        print("A public service could not start.", file=sys.stderr)
        return 1
    finally:
        stop_children(children)


if __name__ == "__main__":
    raise SystemExit(main())
