"""Per-tool place policy: user birthplace (rounded), optional birthplace, client hint, synthesized meridian, none."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from horosa_skill.errors import ToolValidationError

from horosa_public.config import PublicConfig
from horosa_public.server import create_public_mcp_server
from horosa_public.service import NullMemoryStore, NullRuntimeManager
from horosa_public.surface import (
    WARN_CLIENT_PLACE, WARN_CLOCK_TIME, WARN_ROUNDED, city_level, client_place, coordinate_decimal, load_public_surface,
    public_input_schema, public_tools, render_public_result, resolve_place, validate_public_payload, zone_meridian,
)

MOMENT = {"date": "2026-04-04", "time": "21:00:00", "zone": "+08:00"}
BIRTH = {**MOMENT, "lat": "31n13", "lon": "121e28"}
HINT = {"city": "Shanghai", "region": "Shanghai", "country": "CN", "timezone": "Asia/Shanghai", "latitude": 31.2304, "longitude": 121.4737}


def envelope(lines: str = "经纬度：120e00 0n00\n时间算法：直接时间\n真太阳时：2026-04-04 21:03:00\n局数：阳遁一局") -> dict:
    return {"ok": True, "warnings": [], "data": {"export_snapshot": {"sections": [{"title": "起局", "body": lines, "included": True}]},
            "technique_card": {"technique": {"label": "奇门", "key": "qimen"}, "settings": {}, "compute": {"declared_class": "js", "declared_engines": ["qimen"], "measured": {"qimen": "qimen"}, "matches_declaration": True}, "sections": {"contract_clean": True}, "versions": {"skill": "0.40.1"}}}}


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
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HOROSA_SERVER_ROOT", "http://127.0.0.1:9")
    monkeypatch.setenv("HOROSA_CHART_SERVER_ROOT", "http://127.0.0.1:9")
    config = PublicConfig.from_env(no_store_dir=tmp_path / "no-store")
    service = FakeService(config.settings)
    srv = create_public_mcp_server(service, config)
    yield srv, service
    srv._public_pool.close()


def call(server, tool_id, arguments, hint=None):
    srv, _ = server
    srv.client_location = lambda context=None: hint  # the lowlevel request _meta is not available outside a live request
    return asyncio.run(srv.call_tool(public_tools()[tool_id]["name"], arguments))


# --- contract shape ---------------------------------------------------------------------------

def test_place_policies_partition_the_tools():
    by_policy = {}
    for tool_id, item in public_tools().items():
        by_policy.setdefault(item["place"], set()).add(tool_id)
    assert by_policy["none"] == {"tarot", "huangli"}
    assert by_policy["client"] == {"qimen", "sixyao", "xiaoliuren", "nongli_time", "horary"}
    assert by_policy["user_optional"] == {"bazi_birth", "ziwei_birth", "liureng_gods"}
    assert by_policy["user"] == {"chart", "relative", "india_chart", "guolao_chart", "election", "solarreturn", "lunarreturn",
                                 "profection", "firdaria", "prog", "solararc", "ephemeris", "jieqi_year"}


def test_schemas_expose_place_fields_only_where_the_policy_allows():
    for tool_id, item in public_tools().items():
        schema = public_input_schema(tool_id)
        props, required = set(schema["properties"]), set(schema.get("required", []))
        if item["place"] in {"client", "none"}:
            assert not {"lat", "lon", "dirLat", "dirLon"} & props, tool_id
        elif item["place"] == "user_optional":
            assert {"lat", "lon"} <= props and not {"lat", "lon"} & required, tool_id
            assert "Omit" in schema["properties"]["lat"]["description"]
        elif tool_id != "relative":
            assert {"lat", "lon"} <= required, tool_id
    for tool_id in ("solarreturn", "lunarreturn", "profection", "prog", "solararc"):
        schema = public_input_schema(tool_id)
        assert {"dirLat", "dirLon", "dirZone"} <= set(schema["properties"])
        assert not {"dirLat", "dirLon", "dirZone"} & set(schema["required"])


@pytest.mark.parametrize("mutation", ["client_with_place", "optional_without_both", "unknown_policy", "unknown_fallback", "hint_on_birth_tool"])
def test_contract_place_negative_controls(mutation):
    from copy import deepcopy
    from horosa_public.surface import validate_public_contract
    contract = deepcopy(load_public_surface())
    tool = next(t for t in contract["tools"] if t["id"] == "qimen")
    if mutation == "client_with_place":
        tool["field_groups"] = ["birth", "gate"]
    elif mutation == "optional_without_both":
        tool = next(t for t in contract["tools"] if t["id"] == "bazi_birth"); tool["optional_fields"] = ["lon"]
    elif mutation == "unknown_policy":
        tool["place"] = "anything"
    elif mutation == "hint_on_birth_tool":
        tool = next(t for t in contract["tools"] if t["id"] == "chart"); tool["client_hint"] = True
    else:
        tool["place_fallback"] = "guess"
    with pytest.raises(ToolValidationError):
        validate_public_contract(contract)


# --- helpers ---------------------------------------------------------------------------------

@pytest.mark.parametrize("value,axis,expected", [
    ("31n13", "lat", 31.216667), ("121e28", "lon", 121.466667), ("33s52", "lat", -33.866667), ("74w00", "lon", -74.0),
    ("31.230416", "lat", 31.230416), (-121.473701, "lon", -121.473701), ("31e13", "lat", None), ("91.0", "lat", None),
    ("181", "lon", None), ("abc", "lat", None), (True, "lat", None), (None, "lon", None),
])
def test_coordinate_parsing(value, axis, expected):
    parsed = coordinate_decimal(value, axis)
    assert parsed is None if expected is None else abs(parsed - expected) < 1e-5


def test_city_level_rounding_and_zone_meridian():
    assert city_level("31.230416", "lat") == "31.23" and city_level("121e28", "lon") == "121.47" and city_level(-0.004, "lat") == "-0.00"
    assert zone_meridian("+08:00") == 120.0 and zone_meridian("-05:00") == -75.0 and zone_meridian("+05:30") == 82.5
    assert zone_meridian("Asia/Shanghai", "2026-04-04", "21:00:00") == 120.0
    assert zone_meridian("Asia/Shanghai", "1990-06-15", "12:00:00") == 135.0  # historical summer time is honoured
    assert zone_meridian("Nowhere/Invalid") == 0.0


@pytest.mark.parametrize("hint,expected", [
    (HINT, ("31.23", "121.47")), ({"latitude": 0, "longitude": 0}, ("0.00", "0.00")), ({"latitude": "31.2", "longitude": 121.4}, None),
    ({"latitude": 95, "longitude": 10}, None), ({"city": "Shanghai"}, None), ("31.2,121.4", None), (None, None),
])
def test_client_hint_parsing(hint, expected):
    assert client_place(hint) == expected


# --- resolution per policy ---------------------------------------------------------------------

def test_user_place_is_rounded_to_city_level_including_targets_and_parties():
    result = resolve_place("chart", {**MOMENT, "lat": "31.230416", "lon": "121.473701"})
    assert result.injected == {"lat": "31.23", "lon": "121.47"} and result.warnings == [WARN_ROUNDED] and not result.synthesized
    result = resolve_place("chart", BIRTH)  # minute precision is already coarser than 0.01°
    assert result.injected == {"lat": "31.22", "lon": "121.47"} and WARN_ROUNDED in result.warnings
    result = resolve_place("solarreturn", {**BIRTH, "datetime": "2026-06-15 12:00:00", "dirLat": "40.7128", "dirLon": "-74.0060"})
    assert result.injected["dirLat"] == "40.71" and result.injected["dirLon"] == "-74.01"
    result = resolve_place("relative", {"inner": {**BIRTH, "lat": "31.230416"}, "outer": BIRTH})
    assert result.injected["inner"]["lat"] == "31.23" and result.injected["outer"]["lon"] == "121.47"
    with pytest.raises(ToolValidationError):
        resolve_place("chart", {**MOMENT, "lat": "31e13", "lon": "121e28"})


def test_optional_birthplace_present_absent_or_half():
    present = resolve_place("bazi_birth", BIRTH)
    assert present.injected == {"lat": "31.22", "lon": "121.47"} and "timeAlg" not in present.injected and not present.synthesized
    absent = resolve_place("bazi_birth", MOMENT)
    assert absent.injected == {"lat": "0.00", "lon": "120.00", "timeAlg": 1} and absent.synthesized and absent.warnings == [WARN_CLOCK_TIME]
    absent_with_hint = resolve_place("ziwei_birth", MOMENT, HINT)  # a birthplace is never the user's current location
    assert absent_with_hint.injected["timeAlg"] == 1 and absent_with_hint.synthesized and WARN_CLIENT_PLACE not in absent_with_hint.warnings
    question_place = resolve_place("liureng_gods", MOMENT, HINT)  # the place of the question may come from the client
    assert question_place.injected == {"lat": "31.23", "lon": "121.47"} and question_place.warnings == [WARN_CLIENT_PLACE]
    assert resolve_place("liureng_gods", MOMENT).injected["timeAlg"] == 1
    with pytest.raises(ToolValidationError):
        resolve_place("bazi_birth", {**MOMENT, "lat": "31n13"})


def test_here_and_now_tools_use_the_client_hint_or_clock_time():
    with_hint = resolve_place("qimen", MOMENT, HINT)
    assert with_hint.injected == {"lat": "31.23", "lon": "121.47"} and with_hint.warnings == [WARN_CLIENT_PLACE] and not with_hint.synthesized
    without = resolve_place("qimen", {**MOMENT, "timeAlg": 0})
    assert without.injected == {"lat": "0.00", "lon": "120.00", "timeAlg": 1} and without.synthesized and without.warnings == [WARN_CLOCK_TIME]
    malformed = resolve_place("sixyao", MOMENT, {"latitude": "31.2", "longitude": "121.4"})
    assert malformed.synthesized and malformed.injected["timeAlg"] == 1


def test_horary_requires_the_client_hint():
    result = resolve_place("horary", MOMENT)
    assert result.error is not None and result.error.error.code == "public.location_unavailable"
    assert result.error.error.agent_recovery.must_ask_user is True
    assert "coordinates" in result.error.error.agent_recovery.prompt_to_user
    assert resolve_place("horary", MOMENT, HINT).injected == {"lat": "31.23", "lon": "121.47"}


def test_tarot_and_huangli_never_use_a_place():
    tarot = resolve_place("tarot", MOMENT, HINT)
    assert tarot.injected == {"lat": "0.00", "lon": "120.00"} and tarot.synthesized and tarot.warnings == []
    assert resolve_place("huangli", {"date": "2026-04-04"}, HINT).injected == {}


def test_injected_keys_pass_model_validation_but_never_arrive_from_clients():
    payload = validate_public_payload("qimen", {**MOMENT, "defaults_accepted": True}, injected={"lat": "0.00", "lon": "120.00", "timeAlg": 1})
    assert payload["lat"] == "0.00" and payload["timeAlg"] == 1
    with pytest.raises(ToolValidationError):
        validate_public_payload("qimen", {**BIRTH, "defaults_accepted": True})
    with pytest.raises(ToolValidationError):
        validate_public_payload("tarot", {**BIRTH, "defaults_accepted": True})


# --- end to end through the public server -----------------------------------------------------

def test_qimen_without_hint_runs_with_clock_time_and_strips_place_echo(server):
    srv, service = server
    result = call(server, "qimen", {**MOMENT, "defaults_accepted": True})
    assert not result.is_error
    tool, payload, _ = service.calls[-1]
    assert (payload["lat"], payload["lon"], payload["timeAlg"]) == ("0.00", "120.00", 1)
    text = result.structured_content
    assert WARN_CLOCK_TIME in text["warnings"]
    body = text["sections"][0]["text"]
    assert "经纬度" not in body and "真太阳时" not in body and "时间算法：直接时间" in body and "局数" in body


def test_qimen_with_hint_uses_rounded_client_place(server):
    srv, service = server
    result = call(server, "qimen", {**MOMENT, "timeAlg": 0, "defaults_accepted": True}, hint=HINT)
    assert not result.is_error
    payload = service.calls[-1][1]
    assert (payload["lat"], payload["lon"], payload["timeAlg"]) == ("31.23", "121.47", 0)
    assert WARN_CLIENT_PLACE in result.structured_content["warnings"]
    assert "经纬度：120e00 0n00" in result.structured_content["sections"][0]["text"]  # a real place is echoed as the engine wrote it


def test_horary_without_hint_is_a_safe_error_and_runs_nothing(server):
    srv, service = server
    result = call(server, "horary", {**MOMENT, "defaults_accepted": True})
    assert result.is_error and result.structured_content["error"]["code"] == "public.location_unavailable"
    assert not service.calls
    assert not call(server, "horary", {**MOMENT, "defaults_accepted": True}, hint=HINT).is_error


def test_place_arguments_are_rejected_for_client_and_none_tools(server):
    srv, service = server
    for tool_id in ("qimen", "tarot", "nongli_time"):
        result = call(server, tool_id, {**BIRTH, "defaults_accepted": True})
        assert result.is_error and result.structured_content["error"]["code"] == "tool.public_invalid_payload"
    assert not service.calls


def test_birth_chart_coordinates_are_rounded_before_the_engine_sees_them(server):
    srv, service = server
    result = call(server, "chart", {**MOMENT, "lat": "31.230416", "lon": "121.473701", "defaults_accepted": True})
    assert not result.is_error
    payload = service.calls[-1][1]
    assert (payload["lat"], payload["lon"]) == ("31.23", "121.47")
    assert WARN_ROUNDED in result.structured_content["warnings"]


def test_render_filters_place_echo_only_when_synthesized():
    raw = envelope("经度：120e00， 纬度：0n00\n地点：—\n行星：太阳")
    kept = render_public_result("chart", raw)
    assert "经度" in kept.sections[0].text
    stripped = render_public_result("chart", raw, place_synthesized=True, extra_warnings=[WARN_CLOCK_TIME])
    assert stripped.sections[0].text == "行星：太阳" and stripped.warnings[0] == WARN_CLOCK_TIME


def test_client_hint_arrives_through_the_real_request_meta(server):
    """The other tests stub client_location; this one sends `_meta` over HTTP, so an SDK change to request context turns red."""
    from starlette.testclient import TestClient

    srv, service = server
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": public_tools()["qimen"]["name"], "_meta": {"openai/userLocation": HINT},
        "arguments": {"date": "2026-10-04", "time": "09:30:00", "zone": "+08:00", "defaults_accepted": True}}}
    with TestClient(srv.streamable_http_app(), base_url="http://127.0.0.1:8765") as client:
        response = client.post("/mcp", headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()["result"]["structuredContent"]["ok"] is True
    tool, payload, save_result = service.calls[-1]
    assert (tool, save_result) == ("qimen", False)
    assert payload["lat"] == "31.23" and payload["lon"] == "121.47"
