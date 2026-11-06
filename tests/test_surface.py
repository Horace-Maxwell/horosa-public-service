"""Public-boundary negative controls; no backend, socket or installed runtime is used.

Ported from horosa-skill branch codex/chatgpt-plugin (052ce55) tests/test_public_surface.py.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from starlette.testclient import TestClient

from horosa_skill.errors import ToolValidationError

from horosa_public.config import PublicConfig
from horosa_public.server import _PublicRequestBoundary, _PublicWorkPool, create_public_mcp_server
from horosa_public.service import NullMemoryStore, NullRuntimeManager
from horosa_public.surface import (
    PublicResult, allowed_public_tool_ids, load_public_surface, public_error,
    public_input_schema, public_result_size, public_tool_names, public_tools,
    render_public_result, validate_public_contract, validate_public_payload,
)


BIRTH = {"date": "1990-06-15", "time": "12:00:00", "zone": "+08:00", "lat": "31n13", "lon": "121e28"}
MOMENT = {"date": "1990-06-15", "time": "12:00:00", "zone": "+08:00"}  # here-and-now tools take no place


def envelope() -> dict:
    return {"ok": True, "tool": "chart", "input_normalized": {"name": "PRIVATE_SENTINEL"},
            "run_id": "PRIVATE_SENTINEL", "trace_id": "PRIVATE_SENTINEL", "group_id": "PRIVATE_SENTINEL", "memory_ref": {},
            "warnings": [], "data": {"raw_backend": {"secret": "PRIVATE_SENTINEL"},
            "export_snapshot": {"sections": [{"title": "行星", "body": "太阳：白羊座", "included": True}]},
            "technique_card": {"generated_at": "2030-01-01T00:00:00Z", "refs": {"run_id": "PRIVATE_SENTINEL"},
                               "technique": {"label": "星盘", "key": "astrochart"},
                               "settings": {"hsys": {"label": "分宫制", "value": 1}},
                               "compute": {"declared_class": "python_backend", "declared_engines": ["chart"],
                                           "measured": {"chart": "chart"}, "matches_declaration": True},
                               "sections": {"contract_clean": True}, "versions": {"skill": "0.40.0", "runtime": "0.40.0", "export_settings": 15}}}}


class FakeService:
    decision_layer = None

    def __init__(self, settings) -> None:
        self.settings = settings
        self.store = NullMemoryStore()
        self.runtime_manager = NullRuntimeManager()
        self.tracer = SimpleNamespace(enabled=False)
        self.calls = []
        self.result = envelope()

    def run_tool(self, tool, payload, *, save_result):
        self.calls.append((tool, payload, save_result))
        return self.result


@pytest.fixture
def public_server(tmp_path, monkeypatch):
    for key in ("HOROSA_MCP_COMPACT", "HOROSA_TOOLSETS", "HOROSA_MCP_TOKEN", "HOROSA_JEV", "HOROSA_TRACE_ENABLED",
                "HOROSA_TRACE_CAPTURE_PAYLOADS", "HOROSA_TRACE_CAPTURE_AI_ANSWERS", "HOROSA_TRACE_OTLP_ENDPOINT", "HOROSA_PORTS",
                "HOROSA_MCP_MAX_CONCURRENT_TOOLS", "HOROSA_OPENAI_CHALLENGE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOROSA_SERVER_ROOT", "http://127.0.0.1:9")
    monkeypatch.setenv("HOROSA_CHART_SERVER_ROOT", "http://127.0.0.1:9")
    config = PublicConfig.from_env(no_store_dir=tmp_path / "no-store")
    service = FakeService(config.settings)
    server = create_public_mcp_server(service, config)
    yield server, service, config
    server._public_pool.close()


def invoke(server, tool_id="chart", arguments=None):
    return asyncio.run(server.call_tool(public_tools()[tool_id]["name"], arguments or {}))


def test_public_tools_are_exact_allowlist_with_metadata_and_native_schema(public_server):
    server, _, _ = public_server
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == public_tool_names()
    assert len(allowed_public_tool_ids()) == len(load_public_surface()["tools"])
    forbidden = ("tool_run", "dispatch", "hecan", "memory", "report", "export")
    for tool in tools:
        assert not any(word in tool.name for word in forbidden)
        assert tool.title and tool.description and len(tool.description) <= 1024
        assert tool.annotations.model_dump(by_alias=True, exclude_none=True) == {
            "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
        assert tool.meta["securitySchemes"] == [{"type": "noauth"}]
        assert tool.output_schema
        assert len(json.dumps(tool.input_schema, ensure_ascii=False).encode()) <= 5000
        blob = json.dumps(tool.input_schema)
        for forbidden_key in load_public_surface()["forbidden_input_keys"]:
            assert f'"{forbidden_key}"' not in blob
        for forbidden_key in ("$ref", "$defs", "anyOf", "oneOf", "default", "title"):
            assert f'"{forbidden_key}"' not in blob
    assert asyncio.run(server.list_resources()) == []
    assert asyncio.run(server.list_prompts()) == []


@pytest.mark.parametrize("mutation", ["duplicate", "forbidden_field", "unknown_tool", "empty_safety", "negative_limit"])
def test_contract_negative_controls(mutation):
    contract = deepcopy(load_public_surface())
    if mutation == "duplicate":
        contract["tools"].append(deepcopy(contract["tools"][0]))
    elif mutation == "forbidden_field":
        contract["tools"][0]["fields"].append("request")
    elif mutation == "unknown_tool":
        contract["tools"][0]["id"] = "dispatch"
    elif mutation == "empty_safety":
        contract["denied_section_patterns"] = []
    else:
        contract["limits"]["max_concurrent_tools"] = 0
    with pytest.raises(ToolValidationError):
        validate_public_contract(contract)


@pytest.mark.parametrize("forbidden", load_public_surface()["forbidden_input_keys"] + ["options", "includePrimaryDirection", "secret_parameter"])
def test_raw_call_rejects_undeclared_and_forbidden_fields_before_arg_dropping(public_server, forbidden):
    server, service, _ = public_server
    result = invoke(server, arguments={**BIRTH, "defaults_accepted": True, forbidden: "PRIVATE_SENTINEL"})
    assert result.is_error
    assert result.structured_content["error"]["code"] == "tool.public_invalid_payload"
    assert "PRIVATE_SENTINEL" not in result.model_dump_json()
    assert not service.calls


@pytest.mark.parametrize("tool_id,payload", [
    ("relative", {"inner": {**BIRTH, "name": "PRIVATE_SENTINEL"}, "outer": BIRTH}),
    ("relative", {"inner": {**BIRTH, "unknown": "PRIVATE_SENTINEL"}, "outer": BIRTH}),
    ("qimen", {**MOMENT, "options": {"request": {"name": "PRIVATE_SENTINEL"}}}),
    ("qimen", {**MOMENT, "options": {"undocumented": "PRIVATE_SENTINEL"}}),
    ("sixyao", {**MOMENT, "lines": [{"value": 1, "name": "PRIVATE_SENTINEL"}] * 6}),
    ("sixyao", {**MOMENT, "liuyaoSettings": {"unexpected": "PRIVATE_SENTINEL"}}),
])
def test_nested_input_negative_controls(public_server, tool_id, payload):
    server, service, _ = public_server
    result = invoke(server, tool_id, {**payload, "defaults_accepted": True})
    assert result.is_error
    assert "PRIVATE_SENTINEL" not in result.model_dump_json()
    assert not service.calls


def test_normalized_coordinates_and_timezone_keep_original_input_provenance(public_server):
    server, service, _ = public_server
    payload = {**BIRTH, "zone": "Asia/Urumqi", "lat": 43.8, "lon": 87.6, "defaults_accepted": True}
    result = invoke(server, arguments=payload)
    assert not result.is_error
    # Coordinates reach the engine rounded to city level (0.01°); everything else is forwarded as given.
    assert service.calls[0] == ("chart", {**payload, "lat": "43.80", "lon": "87.60"}, False)
    nested = {"inner": BIRTH, "outer": {**BIRTH, "lat": 40.7, "lon": -74}, "agent_confirmed_settings": True}
    assert validate_public_payload("relative", nested) == nested


def test_missing_confirmation_cannot_be_disabled_and_note_is_optional(public_server, monkeypatch):
    server, service, _ = public_server
    monkeypatch.setenv("HOROSA_CLARIFY", "off")
    result = invoke(server, arguments=BIRTH)
    assert result.structured_content["error"]["code"] == "agent_guidance.required"
    assert result.structured_content["error"]["agent_recovery"]["prompt_to_user"]
    assert not service.calls
    result = invoke(server, arguments={**BIRTH, "defaults_accepted": True})
    assert not result.is_error
    assert service.calls[-1][2] is False


def test_confirmed_missing_required_fields_is_schema_conformant_safe_error(public_server):
    server, _, _ = public_server
    result = invoke(server, arguments={"defaults_accepted": True})
    assert result.is_error
    assert set(result.structured_content["error"]["agent_recovery"]["fields"]) >= {"date", "time"}


def test_success_errors_guidance_all_match_advertised_output_schema(public_server):
    server, _, _ = public_server
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    results = [invoke(server, arguments={**BIRTH, "defaults_accepted": True}), invoke(server),
               invoke(server, arguments={"defaults_accepted": True})]
    results.append(asyncio.run(server.call_tool("horosa_agent_guidance", {"tool_name": public_tools()["chart"]["name"]})))
    for result in results:
        data = result.structured_content
        Draft202012Validator(tools[data["tool"]].output_schema).validate(data)
        assert json.loads(result.content[0].text) == data


def test_guidance_cannot_discover_hidden_operations_or_request_current_location(public_server):
    server, service, _ = public_server
    for arguments in ({"tool_name": "horosa_tool_run"}, {"include_all": True}, {"tool_name": "PRIVATE_SENTINEL"}, {"tool_name": "horosa_astro_chart", "intent": "PRIVATE_SENTINEL"}):
        result = asyncio.run(server.call_tool("horosa_agent_guidance", arguments))
        assert result.is_error and "PRIVATE_SENTINEL" not in result.model_dump_json()
    result = asyncio.run(server.call_tool("horosa_agent_guidance", {"tool_name": "horosa_cn_qimen"}))
    assert not result.is_error
    text = result.model_dump_json()
    assert "当前位置/客户端位置" not in text
    assert "horosa_tool_run" not in text
    assert not service.calls


def test_no_data_files_and_no_metadata_or_raw_objects(public_server):
    server, _, config = public_server
    result = invoke(server, arguments={**BIRTH, "defaults_accepted": True})
    assert not config.no_store_dir.exists()
    assert not config.settings.runtime_root.exists()
    text = result.model_dump_json()
    assert "PRIVATE_SENTINEL" not in text
    for key in load_public_surface()["forbidden_output_keys"]:
        assert f'"{key}"' not in text


@pytest.mark.parametrize("danger", ["寿命格局", "寿命判读", "Jaimini 三对法寿命", "Āyurdāya 寿命基础", "疾病判断", "death prediction"])
def test_safety_sections_and_embedded_interpretations_removed(danger):
    raw = envelope()
    raw["data"]["export_snapshot"]["sections"].extend([
        {"title": danger, "body": "UNSAFE_SENTINEL"},
        {"title": "普通标题", "body": danger + " UNSAFE_SENTINEL"}])
    result = render_public_result("chart", raw)
    assert "UNSAFE_SENTINEL" not in result.model_dump_json()
    assert len(result.sections) == 1
    assert result.warnings


def test_card_and_metadata_negative_controls():
    raw = envelope()
    card = raw["data"]["technique_card"]
    card["settings"]["seed"] = {"label": "种子", "value": "寿命判读：2030年死亡"}
    card["settings"]["school"] = {"label": "school", "value": "request_id=PRIVATE_SENTINEL"}
    card["compute"]["measured"]["session_id"] = "PRIVATE_SENTINEL"
    card["compute"]["matches_declaration"] = False
    result = render_public_result("chart", raw)
    text = result.model_dump_json()
    assert "死亡" not in text and "寿命" not in text and "PRIVATE_SENTINEL" not in text
    assert result.technique_card.matches_declaration is False
    assert any("does not match" in warning for warning in result.warnings)


def test_wire_size_cap_includes_both_content_and_structured_copies():
    raw = envelope()
    raw["data"]["export_snapshot"]["sections"] = [{"title": "行星", "body": '汉字"\\\n' * 50000}]
    result = render_public_result("chart", raw)
    assert public_result_size(result) <= load_public_surface()["limits"]["max_output_bytes"]
    assert result.sections
    assert any("truncated" in warning for warning in result.warnings)


def test_recorded_fixture_measurement_and_budget_are_reproducible():
    measurement = load_public_surface()["output_measurement"]
    sizes = []
    for sample in measurement["samples"]:
        path = Path(__file__).resolve().parents[1] / sample["fixture"]
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == sample["sha256"]
        text = json.loads(raw)["live_snapshot_text"] if path.suffix == ".json" else raw.decode("utf-8")
        result = render_public_result(sample["tool"], {"ok": True, "data": {"export_snapshot": {"export_text": text}}})
        size = public_result_size(result)
        assert size == sample["public_mcp_result_bytes"]
        assert size <= load_public_surface()["limits"]["max_output_bytes"]
        sizes.append(size)
    assert max(sizes) == measurement["max_measured_public_mcp_result_bytes"]


def test_export_timestamps_removed_without_removing_computed_return_dates():
    raw = envelope()
    raw["data"]["export_snapshot"]["sections"] = [
        {"title": "当前时点", "body": "盘主当前年龄：99\n导出时刻：2030-01-01 10:20:30"},
        {"title": "太阳返照", "body": "返照日期：2026-06-15\n导出时间：2030-01-01 10:20:30"},
    ]
    result = render_public_result("solarreturn", raw)
    text = result.model_dump_json()
    assert "2030-01-01" not in text and "盘主当前年龄" not in text
    assert "2026-06-15" in text


def test_engine_errors_and_warnings_do_not_echo_payload_or_paths(public_server, caplog):
    server, service, _ = public_server
    service.result = {"ok": False, "error": {"code": "tool.internal_error", "message": "PRIVATE_SENTINEL", "details": {"request_preview": "PRIVATE_SENTINEL"}}}
    result = invoke(server, arguments={**BIRTH, "defaults_accepted": True})
    assert "PRIVATE_SENTINEL" not in result.model_dump_json()
    assert "PRIVATE_SENTINEL" not in caplog.text


def test_explicit_time_casts_remain_supported_and_no_seed_is_invented(public_server):
    server, service, _ = public_server
    for tool_id in ("tarot", "sixyao"):
        result = invoke(server, tool_id, {**MOMENT, "defaults_accepted": True})
        assert not result.is_error
        assert "seed" not in service.calls[-1][1]
    assert not invoke(server, "tarot", {**MOMENT, "seed": "user-provided", "defaults_accepted": True}).is_error
    assert service.calls[-1][1]["seed"] == "user-provided"


def test_ephemeris_bounds_and_explicit_predictive_targets():
    with pytest.raises(ToolValidationError):
        validate_public_payload("ephemeris", {**BIRTH, "startDate": "2020-01-01", "endDate": "2030-01-01"})
    with pytest.raises(ToolValidationError):
        validate_public_payload("solarreturn", BIRTH)
    assert validate_public_payload("ephemeris", {**BIRTH, "startDate": "2020-01-01", "endDate": "2020-01-03"})


def test_routes_and_challenge_exact_bytes(public_server, monkeypatch):
    server, _, _ = public_server
    with TestClient(server.streamable_http_app(), base_url="http://127.0.0.1:8765") as client:
        assert client.get("/healthz").status_code == 200
        assert set(client.get("/healthz").json()) == {"ok", "version"}
        assert client.get("/.well-known/openai-apps-challenge").status_code == 404
        token = " token-with-spaces\n原样 "
        monkeypatch.setenv("HOROSA_OPENAI_CHALLENGE", token)
        response = client.get("/.well-known/openai-apps-challenge")
        assert response.content == token.encode("utf-8")
        assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.parametrize("payload", [
    {"jsonrpc": "2.0", "id": 1, "method": {"birth": "PRIVATE_SENTINEL"}},
    {"jsonrpc": "2.0", "id": {"birth": "PRIVATE_SENTINEL"}, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "horosa_astro_chart", "arguments": ["PRIVATE_SENTINEL"]}},
    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "PRIVATE_SENTINEL"}},
    {"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": "PRIVATE_SENTINEL"}},
])
def test_malformed_protocol_and_hidden_methods_never_reach_sdk_validation(public_server, payload, caplog):
    server, _, _ = public_server
    with TestClient(server.streamable_http_app(), base_url="http://127.0.0.1:8765") as client:
        response = client.post("/mcp", json=payload)
    assert response.status_code == 400
    assert "PRIVATE_SENTINEL" not in response.text
    assert "PRIVATE_SENTINEL" not in caplog.text


def test_asgi_body_limit(public_server):
    server, _, _ = public_server
    with TestClient(server.streamable_http_app(), base_url="http://127.0.0.1:8765") as client:
        response = client.post("/mcp", content=b"x" * (load_public_surface()["limits"]["max_request_body_bytes"] + 1))
    assert response.status_code == 413


def test_slow_request_body_has_a_total_deadline():
    async def scenario():
        sent = []
        async def app(scope, receive, send):
            raise AssertionError("Overslow bodies must not reach the SDK")
        async def receive():
            await asyncio.sleep(.1)
            return {"type": "http.request", "body": b"{}", "more_body": False}
        async def send(message):
            sent.append(message)
        boundary = _PublicRequestBoundary(app, max_bytes=65536, timeout=.001)
        await boundary({"type": "http", "method": "POST", "path": "/mcp"}, receive, send)
        assert sent[0]["status"] == 408
    asyncio.run(scenario())


def test_mcp_http_actual_tools_list_and_safe_call(public_server):
    server, _, _ = public_server
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    with TestClient(server.streamable_http_app(), base_url="http://127.0.0.1:8765") as client:
        response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        assert response.status_code == 200
        assert {tool["name"] for tool in response.json()["result"]["tools"]} == public_tool_names()
        response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "horosa_astro_chart", "arguments": {**BIRTH, "defaults_accepted": True}}})
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["structuredContent"]["ok"] is True
        assert json.loads(data["content"][0]["text"])["ok"] is True
        assert "mcp-session-id" not in response.headers


def test_timeout_and_cancellation_keep_actual_work_permit():
    async def scenario():
        pool = _PublicWorkPool(1, .02)
        finish = threading.Event()
        def slow():
            finish.wait(2)
            return PublicResult(ok=True, tool="horosa_astro_chart")
        try:
            result = await pool.run("chart", slow)
            assert result.error.code == "tool.public_timeout"
            assert (await pool.run("chart", slow)).error.code == "tool.public_unavailable"
            finish.set()
            for _ in range(100):
                await asyncio.sleep(.001)
                result = await pool.run("chart", lambda: PublicResult(ok=True, tool="horosa_astro_chart"))
                if result.ok:
                    break
            assert result.ok
            finish.clear()
            task = asyncio.create_task(pool.run("chart", slow))
            await asyncio.sleep(.005)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert (await pool.run("chart", slow)).error.code == "tool.public_unavailable"
        finally:
            finish.set()
            pool.close()
    asyncio.run(scenario())


def test_public_startup_rejects_existing_store(public_server):
    _, service, config = public_server
    for attribute, unsafe in (
        ("store", object()),
        ("store", None),
        ("runtime_manager", object()),
        ("decision_layer", object()),
        ("settings", None),
        ("tracer", None),
        ("tracer", SimpleNamespace(enabled=True)),
    ):
        original = getattr(service, attribute)
        try:
            setattr(service, attribute, unsafe)
            with pytest.raises(ToolValidationError):
                create_public_mcp_server(service, config)
        finally:
            setattr(service, attribute, original)


def test_wire_descriptor_carries_openai_security_schemes_and_camel_case(public_server):
    """OpenAI reads the top-level key; `_meta` is only the mirror. Assert the JSON clients receive, not the Python object."""
    server, _, _ = public_server
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    with TestClient(server.streamable_http_app(), base_url="http://127.0.0.1:8765") as client:
        tools = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).json()["result"]["tools"]
    assert {tool["name"] for tool in tools} == public_tool_names()
    for tool in tools:
        assert tool["securitySchemes"] == [{"type": "noauth"}]
        assert tool["_meta"]["securitySchemes"] == [{"type": "noauth"}]
        assert tool["inputSchema"] and tool["outputSchema"]
        assert tool["annotations"] == {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}


def test_server_discover_passes_the_public_boundary(public_server):
    """2026-07-28 clients open with server/discover instead of initialize."""
    server, _, _ = public_server
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    with TestClient(server.streamable_http_app(), base_url="http://127.0.0.1:8765") as client:
        response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}})
    assert response.status_code != 400 or "Invalid public MCP request" not in response.text
