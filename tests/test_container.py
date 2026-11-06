"""Container build inputs, supervisor environment and sandbox checks (no Docker is run here).

Ported from horosa-skill branch codex/chatgpt-plugin (052ce55) tests/test_public_container.py.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from horosa_public.config import forbidden_environment_fields

PROJECT = Path(__file__).resolve().parents[1]
SEED_SHA256 = "abb0003500f24e9dd6250622d2678f61f8e1e2046092e159baa468acb1e30102"  # SHA256SUMS.txt of v0.47.1
WHEEL_SHA256 = "4499ec6d265d8d5668ecf67af0fca41e0561b683e3bee3c824d437671e6b2c75"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


prepare = _load("test_public_prepare", PROJECT / "scripts/prepare_public_container.py")
entrypoint = _load("test_public_entrypoint", PROJECT / "deploy/public/entrypoint.py")
CONTRACT = json.loads((PROJECT / "src/horosa_public/contracts/public_container.json").read_text(encoding="utf-8"))
VENDOR = PROJECT / CONTRACT["skill"]["vendor_dir"]


def _assert_document_recipe(text: str) -> None:
    for required in (
        "--platform linux/amd64", "--read-only", "--tmpfs /tmp:rw,nosuid,nodev,size=64m",
        "--cap-drop ALL", "--security-opt no-new-privileges", "--publish 127.0.0.1::8765",
        "public_container.json", "SE_EPHE_PATH", "--check-docker-space", "set -e",
    ):
        assert required in text
    assert not re.search(r"--publish\s+(?:0\.0\.0\.0:|(?:[0-9]+:)?(?:8877|9977))", text)


def test_container_pins_match_release_and_vendored_toolchain():
    toolchain = json.loads((VENDOR / "contracts/runtime_toolchain.json").read_text(encoding="utf-8"))
    assert CONTRACT["node"]["version"] == toolchain["node"]["version"]
    assert "jdk-" + CONTRACT["java"]["version"] == toolchain["java"]["temurin_release"]
    assert CONTRACT["platform"] == "linux/amd64"
    assert CONTRACT["seed"]["version"] == CONTRACT["skill"]["version"] == "0.47.1"
    assert CONTRACT["seed"]["sha256"] == SEED_SHA256 and CONTRACT["skill"]["wheel"]["sha256"] == WHEEL_SHA256
    for specification in (CONTRACT["seed"], CONTRACT["node"], CONTRACT["java"], CONTRACT["skill"]["wheel"]):
        assert prepare.SHA256.fullmatch(specification["sha256"])
        assert specification["url"].startswith("https://") and "latest" not in specification["url"]
    for filename in ("scripts/runtime_seed.py", "scripts/verify_runtime_release.py", "contracts/runtime_python_lock.json"):
        assert (VENDOR / filename).is_file()
    dockerfile = (PROJECT / "deploy/public/Dockerfile").read_text(encoding="utf-8")
    assert "FROM " + CONTRACT["python_image"] in dockerfile
    assert "FROM " + CONTRACT["python_build_image"] + " AS build" in dockerfile
    assert f"USER {CONTRACT['uid']}:{CONTRACT['uid']}" in dockerfile
    runtime_lock = json.loads((VENDOR / "contracts/runtime_python_lock.json").read_text(encoding="utf-8"))
    for name, specification in CONTRACT["source_builds"].items():
        assert f"{name}=={specification['version']}" in runtime_lock["native"]
    source = (PROJECT / "scripts/prepare_public_container.py").read_text(encoding="utf-8")
    for needle in ('"--no-build-isolation"', '"--no-index"', "source.resolve(strict=True)", '"--require-hashes"', "npm"):
        assert (needle in source) == (needle != "npm"), needle  # the seed ships node_modules; the build never runs npm
    guide = (PROJECT / "docs/PUBLIC_CONTAINER.md").read_text(encoding="utf-8")
    _assert_document_recipe(guide)
    with pytest.raises(AssertionError):
        _assert_document_recipe(guide.replace("--read-only", ""))


def test_artifact_hash_guard_rejects_negative_control(tmp_path):
    artifact = tmp_path / "artifact.tar.gz"
    artifact.write_bytes(b"verified artifact")
    specification = {"sha256": prepare.digest(artifact), "size": artifact.stat().st_size}
    prepare.verify_artifact(artifact, specification)
    artifact.write_bytes(b"modified artifact")
    with pytest.raises(ValueError, match="checksum"):
        prepare.verify_artifact(artifact, specification)
    with pytest.raises(ValueError, match="SHA-256"):
        prepare.verify_artifact(artifact, {"sha256": "nope"})


def test_offline_prepare_missing_artifact_never_downloads(monkeypatch, tmp_path):
    network = Mock(side_effect=AssertionError("offline build used network"))
    monkeypatch.setattr(prepare.urllib.request, "urlopen", network)
    with pytest.raises(ValueError, match="Offline"):
        prepare.fetch_artifact(CONTRACT["node"], tmp_path, offline=True)
    network.assert_not_called()


def test_mcp_requirements_are_the_hashed_closure_with_the_pinned_skill_wheel():
    rows = prepare.locked_mcp_requirements(PROJECT / "uv.lock")
    assert all("--hash=sha256:" in row for row in rows)
    names = {row.split(" @ ")[0] if " @ " in row else row.split("==")[0] for row in rows}
    assert {"mcp", "mcp-types", "httpx2", "cryptography", "pydantic", "jsonschema", "uvicorn", "horosa-skill"} <= names
    assert not names & {"pytest", "horosa-public", "packaging"}
    skill_row = next(row for row in rows if row.startswith("horosa-skill @ "))
    assert CONTRACT["skill"]["wheel"]["url"] in skill_row and f"--hash=sha256:{WHEEL_SHA256}" in skill_row
    assert any(row.startswith("mcp==2.3.0 ") for row in rows)


def test_process_environment_is_allowlisted_and_private():
    assert entrypoint.process_cwd("chart") == entrypoint.ROOT / "Horosa-Web/astropy/websrv"
    assert entrypoint.process_cwd("java") == entrypoint.ROOT
    specs = dict((name, (args, env)) for name, args, env in entrypoint.commands(CONTRACT, {
        "SECRET_KEY": "private sentinel", "HTTP_PROXY": "private sentinel",
        "HOROSA_JEV_API_KEY": "private sentinel", "HOROSA_TRACE_CAPTURE_PAYLOADS": "1",
        "HOROSA_MCP_TOKEN": "private sentinel", "HOROSA_OPENAI_CHALLENGE": "challenge value",
        "HOROSA_MCP_ALLOWED_HOSTS": "mcp.horosa.org",
    }))
    assert "private sentinel" not in json.dumps(specs)
    for args, env in specs.values():
        assert env["PYTHONDONTWRITEBYTECODE"] == "1" and env["HOME"].endswith("/no-store")
    chart_args, chart_env = specs["chart"]
    assert chart_env["HOROSA_EPHE_PATH_FASTPATH"] == "0" and chart_env["HOROSA_STARTUP_LEDGER"] == "0"
    assert chart_env["SE_EPHE_PATH"] == f"/opt/horosa/{CONTRACT['ephemeris_path']}"
    java, env = specs["java"]
    assert env["HOROSA_DESKTOP_MONGO_SKIP_PING"] == "1" and env["needtranslog"] == "false"
    assert env["HOROSA_MONGO_FALLBACK_DIR"].endswith("/no-store/cache") and env["HOROSA_NONGLI_DAY_PERSIST"] == "0"
    assert "-Dparamhash.cache.enable=false" in java and "--server.address=127.0.0.1" in java
    assert not any("8899" in arg or "9999" in arg for arg in java)
    mcp, env = specs["mcp"]
    assert mcp[0].endswith("/mcp-venv/bin/horosa-public") and mcp[1:] == ["serve", "--host", "0.0.0.0", "--port", "8765"]
    assert forbidden_environment_fields(env) == []  # the public config would refuse anything else
    assert env["HOROSA_SERVER_ROOT"] == "http://127.0.0.1:9977" and env["HOROSA_CHART_SERVER_ROOT"] == "http://127.0.0.1:8877"
    assert env["HOROSA_OPENAI_CHALLENGE"] == "challenge value" and env["HOROSA_MCP_ALLOWED_HOSTS"] == "mcp.horosa.org"
    assert "HOROSA_MCP_PUBLIC" not in env and "PYTHONPATH" not in env and "HOROSA_SKILL_DB_PATH" not in env


@pytest.mark.parametrize("failed", [0, 1])
def test_prewarm_parser_keeps_counters_and_discards_raw_output(capsys, failed):
    state: dict = {}
    ready = threading.Event()
    stream = io.StringIO(f"private request sentinel\nkentang prewarm ready in 2.000s (loaded=14, failed={failed})\n")
    entrypoint.drain_chart(stream, state, ready)
    assert ready.is_set() and state == {"loaded": 14, "failed": failed}
    assert capsys.readouterr() == ("", "")


def test_container_refuses_root_or_writable_cache(monkeypatch):
    root = Mock()
    denied = Mock()
    root.__truediv__ = Mock(return_value=denied)
    denied.is_dir.return_value = True
    denied.stat.return_value = SimpleNamespace(st_uid=0, st_mode=0o40555)
    denied.iterdir.return_value = iter(())
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: 10001, raising=False)
    monkeypatch.setattr(entrypoint.os, "access", lambda *args: False)
    entrypoint.validate_sandbox(root)
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: 0)
    with pytest.raises(ValueError, match="non-root"):
        entrypoint.validate_sandbox(root)
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: 10001)
    denied.stat.return_value = SimpleNamespace(st_uid=0, st_mode=0o40777)
    with pytest.raises(ValueError, match="non-writable"):
        entrypoint.validate_sandbox(root)


def test_supervisor_only_stops_its_known_children(monkeypatch):
    children = [Mock(), Mock(), Mock()]
    for index, child in enumerate(children):
        child.pid = 200 + index
    signals = Mock()
    monkeypatch.setattr(entrypoint.os, "killpg", signals, raising=False)
    monkeypatch.setattr(entrypoint.signal, "SIGKILL", 9, raising=False)
    entrypoint.stop_children(children)
    assert [call.args for call in signals.call_args_list] == [
        (202, entrypoint.signal.SIGTERM), (201, entrypoint.signal.SIGTERM), (200, entrypoint.signal.SIGTERM),
        (202, entrypoint.signal.SIGKILL), (201, entrypoint.signal.SIGKILL), (200, entrypoint.signal.SIGKILL),
    ]
    for child in children:
        child.wait.assert_called_once()
    source = (PROJECT / "deploy/public/entrypoint.py").read_text(encoding="utf-8")
    assert source.count("start_new_session=True") == 2


def test_docker_space_preflight_accepts_safe_capacity_and_rejects_negative_controls(monkeypatch, capsys):
    required = CONTRACT["minimum_docker_free_bytes"]
    assert required == 12 * 1024 ** 3
    run = Mock()
    monkeypatch.setattr(prepare.subprocess, "run", run)
    for free, expected in [(required + 1, True), (required, True), (required - 1, False)]:
        run.return_value = SimpleNamespace(stdout=json.dumps({"total_bytes": required * 3, "used_bytes": required, "free_bytes": free}))
        result = prepare.check_docker_space(CONTRACT)
        assert result["ok"] is expected and result["free_bytes"] == free
    command = run.call_args.args[0]
    assert command[:2] == ["docker", "run"] and "--rm" in command and "--read-only" in command and "--pull=never" in command
    assert command[command.index("--network") + 1] == "none"
    assert not {"--mount", "--volume", "-v", "--privileged"} & set(command)
    for invalid in [{}, [], {"total_bytes": 100, "used_bytes": 1, "free_bytes": True}, {"total_bytes": 100, "used_bytes": 1, "free_bytes": 200}]:
        run.return_value = SimpleNamespace(stdout=json.dumps(invalid))
        assert prepare.check_docker_space(CONTRACT)["code"].endswith("disk_probe_invalid")
    run.return_value = SimpleNamespace(stdout="private invalid-output sentinel")
    result = prepare.check_docker_space(CONTRACT)
    assert result["code"].endswith("disk_probe_failed") and "sentinel" not in json.dumps(result)
    run.side_effect = None
    run.return_value = SimpleNamespace(stdout=json.dumps({"total_bytes": required, "used_bytes": required, "free_bytes": 0}))
    stage = Mock(side_effect=AssertionError("preflight prepared or built a container"))
    monkeypatch.setattr(prepare, "prepare", stage)
    monkeypatch.setattr(prepare, "install", stage)
    monkeypatch.setattr(prepare.sys, "argv", ["prepare_public_container.py", "--check-docker-space"])
    assert prepare.main() == 2
    assert json.loads(capsys.readouterr().out)["code"].endswith("disk_space_insufficient")
    stage.assert_not_called()
