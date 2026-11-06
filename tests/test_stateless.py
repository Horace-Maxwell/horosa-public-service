"""Public-mode isolation of the wrapper: no live backends, sockets, installed runtime or files."""
from __future__ import annotations

import json
import logging
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import horosa_skill.service as skill_service
import horosa_skill.service.remote as skill_remote
from horosa_skill.errors import ToolTransportError, ToolValidationError
from horosa_skill.tracing import TraceRecorder

from horosa_public import cli
from horosa_public.config import PublicConfig
from horosa_public.service import (
    NullMemoryStore,
    NullRuntimeManager,
    NullTraceRecorder,
    PublicHorosaService,
    PublicModeViolation,
    assert_public_service,
)
from horosa_public.surface import allowed_public_tool_ids


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("HOROSA_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("HOROSA_SERVER_ROOT", "http://127.0.0.1:9977")
    monkeypatch.setenv("HOROSA_CHART_SERVER_ROOT", "http://127.0.0.1:8877")


def _config(tmp_path, **kwargs) -> PublicConfig:
    return PublicConfig.from_env(no_store_dir=tmp_path / "no-store", **kwargs)


def _no_files(tmp_path) -> None:
    assert list(tmp_path.iterdir()) == []


def test_constructor_never_builds_store_runtime_manager_or_cloud_layer(monkeypatch, tmp_path):
    store_factory = Mock(side_effect=AssertionError("MemoryStore constructed in public mode"))
    manager_factory = Mock(side_effect=AssertionError("HorosaRuntimeManager constructed in public mode"))
    monkeypatch.setattr(skill_service, "MemoryStore", store_factory)
    monkeypatch.setattr(skill_service, "HorosaRuntimeManager", manager_factory)
    service = PublicHorosaService(_config(tmp_path))
    assert isinstance(service.store, NullMemoryStore)
    assert isinstance(service.runtime_manager, NullRuntimeManager)
    assert isinstance(service.tracer, NullTraceRecorder) and service.tracer.enabled is False
    assert service.decision_layer is None
    assert service._java_runtime_ready is True and service._chart_runtime_ready is True
    store_factory.assert_not_called()
    manager_factory.assert_not_called()
    assert_public_service(service)
    _no_files(tmp_path)

    # Negative control: the released service still builds its store from the same settings.
    store_factory.side_effect = None
    store_factory.return_value = Mock()
    skill_service.HorosaSkillService(_config(tmp_path).settings, runtime_manager=Mock())
    store_factory.assert_called_once()


def test_cloud_layer_cannot_be_enabled_or_injected(monkeypatch, tmp_path):
    config = _config(tmp_path)
    monkeypatch.setenv("HOROSA_JEV", "shadow")
    with pytest.raises(ToolValidationError) as caught:
        PublicHorosaService(config)
    assert caught.value.code == "public.config_invalid" and caught.value.details["fields"] == ["HOROSA_JEV"]
    monkeypatch.delenv("HOROSA_JEV")
    monkeypatch.setattr(skill_service.DecisionLayer, "from_settings", classmethod(lambda cls, *args, **kwargs: Mock()))
    with pytest.raises(ToolValidationError) as caught:
        PublicHorosaService(config)
    assert caught.value.code == "public.decision_layer_invalid"
    _no_files(tmp_path)


def test_trace_recorder_yields_no_ids_and_never_writes(tmp_path):
    config = _config(tmp_path)
    recorder = NullTraceRecorder(config.settings)
    with recorder.span(workflow_name="tool.run", metadata={"payload": "sensitive sentinel"}) as span:
        assert span == {"trace_id": None, "group_id": None}
    assert recorder.new_trace_id() is None and recorder.new_group_id() is None
    assert recorder.read_latest() == [] and recorder.latest_trace_files() == []
    with pytest.raises(PublicModeViolation):
        recorder._write_event({})
    _no_files(tmp_path)
    # Negative control: the released recorder with tracing enabled persists ids.
    local = TraceRecorder(config.settings.model_copy(update={
        "trace_enabled": True, "trace_dir": tmp_path / "traces",
        "db_path": tmp_path / "memory.db", "output_dir": tmp_path / "runs",  # the released recorder requires a complete local layout
    }))
    with local.span(workflow_name="tool.run", metadata={"payload": "sensitive sentinel"}):
        pass
    rows = local.read_latest()
    assert rows and rows[0]["trace_id"] and rows[0]["payload"] == "<redacted>"


def test_run_tool_discards_persistence_arguments_and_ids(monkeypatch, tmp_path):
    service = PublicHorosaService(_config(tmp_path))
    monkeypatch.setattr(service, "_run_local_tool", lambda *args: {"snapshot_text": "[起课]\n测试快照"})
    result = service.run_tool(
        "xiaoliuren", {"nums": [1, 2, 3], "agent_confirmed_settings": True},
        save_result=True, run_id="forbidden", query_text="private", group_id="forbidden", evaluation_case_id="forbidden",
    )
    assert result.ok
    assert result.memory_ref is None and result.trace_id is None and result.group_id is None
    _no_files(tmp_path)


def test_non_allowlisted_tools_are_rejected_before_execution(monkeypatch, tmp_path):
    service = PublicHorosaService(_config(tmp_path))
    execute = Mock(side_effect=AssertionError("non-public operation executed"))
    monkeypatch.setattr(service, "_run_local_tool", execute)
    for tool in ("astrodata", "export_registry", "knowledge_read"):
        assert tool not in allowed_public_tool_ids()
        with pytest.raises(ToolValidationError) as caught:
            service.run_tool(tool, {}, save_result=False)
        assert caught.value.code == "public.tool_invalid"
    execute.assert_not_called()
    _no_files(tmp_path)


@pytest.mark.parametrize("endpoint", ["/chart", "/nongli/time"])
def test_backend_failure_never_probes_or_starts_a_runtime(monkeypatch, tmp_path, endpoint):
    backend = Mock()
    backend.call.side_effect = ToolTransportError("Unavailable", code="transport.connection_error")
    backend.probe.return_value = False
    manager_factory = Mock(side_effect=AssertionError("HorosaRuntimeManager constructed"))
    monkeypatch.setattr(skill_service, "HorosaRuntimeManager", manager_factory)
    # The skill pauses 1 s before its single retry; since v0.41.1 that lives in service/remote.py (_call_remote).
    monkeypatch.setattr(skill_remote.time, "sleep", lambda seconds: None)
    service = PublicHorosaService(_config(tmp_path), client=backend, chart_client=backend)
    with pytest.raises(ToolTransportError) as caught:
        service._call_remote(endpoint, {})
    assert caught.value.code == "transport.connection_error"
    backend.probe.assert_not_called()  # readiness flags skip the probe-and-start path entirely
    assert backend.call.call_count == 2  # one retry after the inert "nothing started" answer
    assert service.runtime_manager.lifecycle_requests == 1
    manager_factory.assert_not_called()
    _no_files(tmp_path)


@pytest.mark.parametrize("flag,value", [
    ("HOROSA_MCP_COMPACT", "1"), ("HOROSA_MCP_COMPACT", "0"), ("HOROSA_TOOLSETS", "cn"),
    ("HOROSA_MCP_TOKEN", "secret-sentinel"), ("HOROSA_MCP_ELICIT", "1"), ("HOROSA_PORTS", "auto"),
    ("HOROSA_JEV", "shadow"), ("HOROSA_JEV_API_KEY", "secret-sentinel"), ("HOROSA_JEV_MODEL", "x"),
    ("HOROSA_TRACE_ENABLED", "1"), ("HOROSA_TRACE_DIR", "/tmp/secret-sentinel"),
    ("HOROSA_TRACE_CAPTURE_PAYLOADS", "1"), ("HOROSA_TRACE_CAPTURE_AI_ANSWERS", "1"),
    ("HOROSA_TRACE_OTLP_ENDPOINT", "https://secret-sentinel.invalid"),
    ("HOROSA_SKILL_DB_PATH", "/tmp/secret-sentinel.db"), ("HOROSA_SKILL_OUTPUT_DIR", "/tmp/secret-sentinel"),
])
def test_environment_conflicts_are_rejected_without_echoing_values(monkeypatch, tmp_path, flag, value):
    monkeypatch.setenv(flag, value)
    with pytest.raises(ToolValidationError) as caught:
        _config(tmp_path)
    assert caught.value.code == "public.config_invalid"
    assert flag in caught.value.details["fields"]
    assert "secret-sentinel" not in str(caught.value) and "secret-sentinel" not in json.dumps(caught.value.details)
    _no_files(tmp_path)


def test_explicit_off_values_are_accepted(monkeypatch, tmp_path):
    monkeypatch.setenv("HOROSA_JEV", "off")
    monkeypatch.setenv("HOROSA_TRACE_ENABLED", "0")
    config = _config(tmp_path)
    assert config.settings.trace_enabled is False and config.settings.mcp_compact is False
    assert config.settings.server_root == "http://127.0.0.1:9977"
    assert config.settings.db_path is None and config.settings.output_dir is None and config.settings.trace_dir is None
    _no_files(tmp_path)


@pytest.mark.parametrize("url", [
    "https://example.com:443", "http://localhost:9977", "http://127.0.0.1",
    "http://127.0.0.1:9977/path", "http://127.0.0.1:9977?token=secret-sentinel",
    "http://user:secret-sentinel@127.0.0.1:9977", "http://127.0.0.1:invalid",
    "http://10.0.0.1:9977", "http://2130706433:9977", "file:///tmp/backend", "http://127.0.0.1:0", "",
])
def test_backend_urls_must_be_explicit_loopback(monkeypatch, tmp_path, url):
    monkeypatch.setenv("HOROSA_SERVER_ROOT", url)
    with pytest.raises(ToolValidationError) as caught:
        _config(tmp_path)
    assert caught.value.code == "public.backend_url_invalid"
    assert caught.value.details["fields"] == ["HOROSA_SERVER_ROOT"]
    assert "secret-sentinel" not in str(caught.value) and "secret-sentinel" not in json.dumps(caught.value.details)


def test_missing_backend_url_is_rejected(monkeypatch, tmp_path):
    monkeypatch.delenv("HOROSA_CHART_SERVER_ROOT")
    with pytest.raises(ToolValidationError) as caught:
        _config(tmp_path)
    assert caught.value.code == "public.backend_url_invalid"
    assert caught.value.details["fields"] == ["HOROSA_CHART_SERVER_ROOT"]


def test_ipv6_loopback_accepted_and_bind_host_restricted(monkeypatch, tmp_path):
    monkeypatch.setenv("HOROSA_SERVER_ROOT", "http://[::1]:9977")
    assert _config(tmp_path).settings.server_root == "http://[::1]:9977"
    monkeypatch.setenv("HOROSA_SKILL_HOST", "10.0.0.5")
    with pytest.raises(ToolValidationError) as caught:
        _config(tmp_path)
    assert caught.value.code == "public.config_invalid" and caught.value.details["fields"] == ["HOROSA_SKILL_HOST"]
    monkeypatch.setenv("HOROSA_SKILL_HOST", "0.0.0.0")
    monkeypatch.setenv("HOROSA_SKILL_PORT", "18765")
    config = _config(tmp_path)
    assert (config.host, config.port) == ("0.0.0.0", 18765)
    monkeypatch.setenv("HOROSA_SKILL_PORT", "70000")
    with pytest.raises(ToolValidationError) as caught:
        _config(tmp_path)
    assert caught.value.details["fields"] == ["HOROSA_SKILL_PORT"]


def test_assert_public_service_rejects_unsafe_components(tmp_path):
    service = PublicHorosaService(_config(tmp_path))
    assert_public_service(service)
    for attribute, unsafe in (
        ("store", object()), ("runtime_manager", object()),
        ("decision_layer", object()), ("tracer", SimpleNamespace(enabled=True)),
    ):
        original = getattr(service, attribute)
        setattr(service, attribute, unsafe)
        try:
            with pytest.raises(ToolValidationError) as caught:
                assert_public_service(service)
            assert caught.value.code == "public.configuration_invalid"
        finally:
            setattr(service, attribute, original)


def test_inert_components_fail_loudly_but_tolerate_getattr_probes():
    store = NullMemoryStore()
    assert store and getattr(store, "query_runs", None) is None
    with pytest.raises(PublicModeViolation):
        store.create_run()
    manager = NullRuntimeManager()
    assert manager.load_installed_manifest() is None and manager.java_backend_cooldown_remaining() == 0.0
    assert getattr(manager, "install", None) is None
    with pytest.raises(PublicModeViolation):
        manager.stop_local_services()
    with pytest.raises(PublicModeViolation):
        _ = manager.current_dir


def test_cli_serve_wires_public_components_and_disables_logging(monkeypatch, tmp_path, capsys):
    built: dict = {}

    class FakeServer:
        def run(self, *, transport):
            built["transport"] = transport
            built["logging_disabled_during_run"] = logging.root.manager.disable

    def fake_create(service, config):
        built["service"], built["config"] = service, config
        return FakeServer()

    monkeypatch.setattr(cli, "create_public_mcp_server", fake_create)
    previous = logging.root.manager.disable
    assert cli.main(["serve", "--port", "18765"]) == 0
    assert isinstance(built["service"], PublicHorosaService)
    assert built["config"].port == 18765 and built["transport"] == "streamable-http"
    assert built["logging_disabled_during_run"] == logging.CRITICAL
    assert logging.root.manager.disable == previous
    assert cli.STARTUP_NOTICE in capsys.readouterr().err
    _no_files(tmp_path)


def test_cli_check_prints_redacted_summary_and_rejects_bad_environment(monkeypatch, capsys):
    monkeypatch.setenv("HOROSA_OPENAI_CHALLENGE", "token-sentinel")
    assert cli.main(["check"]) == 0
    out = capsys.readouterr().out
    summary = json.loads(out)
    assert summary["ok"] is True and summary["challenge_configured"] is True
    assert "token-sentinel" not in out and "127.0.0.1:9977" not in out
    monkeypatch.setenv("HOROSA_MCP_TOKEN", "secret-sentinel")
    assert cli.main(["serve"]) == 2
    err = capsys.readouterr().err
    assert "public.config_invalid" in err and "HOROSA_MCP_TOKEN" in err and "secret-sentinel" not in err
