# Ported from horosa-skill branch codex/chatgpt-plugin (052ce55) src/horosa_skill/public_surface.py;
# the contract now ships inside this package instead of the skill's contracts directory.
"""Calculation-only public contract, input boundary and minimal output projection.

This module does not import the service or construct storage/runtime clients. The JSON
contract is the single source of public tool names, fields, limits and safety filters.
Local MCP/CLI schemas and response bytes do not pass through this module.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field as _dc_field
from datetime import date, datetime
from zoneinfo import ZoneInfo
from functools import lru_cache
import json
import re
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from importlib.resources import files as _resource_files
from horosa_skill.engine.registry import TOOL_DEFINITIONS
from horosa_skill.errors import ToolValidationError
from horosa_skill.exports.parser import split_content_sections
from horosa_skill.input_normalization import normalize_request_payload
from horosa_skill.surfaces.mcp_schema import portable_property


_FIELD_DESCRIPTIONS = {
    "date": "Birth or event calendar date, YYYY-MM-DD; explicitly provided by the user.",
    "time": "Birth or event local time, HH:mm:ss; explicitly provided by the user.",
    "zone": "Timezone of that birth or event, as a UTC offset or IANA timezone.",
    "lat": "Latitude of the chart subject's birthplace or specified event place. City-level precision suffices; not the user's current location.",
    "lon": "Longitude of the chart subject's birthplace or specified event place. City-level precision suffices; not the user's current location.",
    "dirLat": "Latitude of the specified target event place, at city-level precision; not the user's current location.",
    "dirLon": "Longitude of the specified target event place, at city-level precision; not the user's current location.",
    "dirZone": "Timezone at the specified target event place and date.",
    "datetime": "Explicit target date and time, YYYY-MM-DD HH:mm:ss.",
    "ad": "Era: 1 CE or -1 BCE.",
    "agent_confirmed_settings": "True only after the user explicitly confirms result-changing settings.",
    "defaults_accepted": "True only when the user explicitly accepts Horosa defaults.",
    "clarification_notes": "Optional short confirmation note; do not include names or unrelated personal data.",
    "hsys": "House-system index: 0 Whole Sign, 1 Alcabitius, 3 Placidus; 0-24 supported.",
    "zodiacal": "Zodiac: 0 tropical; 1 sidereal (confirm ayanamsa).",
    "siderealAyanamsa": "User-confirmed sidereal ayanamsa key.",
    "timeAlg": "Time convention: 0 true solar time; 1 clock time. Confirm with the user.",
    "after23NewDay": "Day boundary: 1 changes day at 23:00; 0 at midnight.",
    "lateZiHourUseNextDay": "Late-Zi hour stem: 1 follows the next day; 0 follows the current day.",
    "gender": "Traditional chart gender setting: 1 male, 0 female; confirm where it affects the calculation.",
    "question": "Optional question for this reading; omit names and unrelated personal data.",
    "questionText": "Question for the event chart; omit names and unrelated personal data.",
    "askEvent": "Question for the event chart; omit names and unrelated personal data.",
    "period": "Explicit period selections. Each list is bounded to three entries.",
    "options": "Only the documented public calculation settings are accepted.",
    "cangVersion": "BaZi hidden-stems table version. Leave unset for the Horosa default.",
    "category": "Horary question category. Leave unset for the Horosa default.",
    "changed_code": "Code of the changed hexagram, when the user already has one.",
    "gua_code": "Code of the cast hexagram, when the user already has one.",
    "cnUnifiedZone": "China time handling: true uses Beijing time nationwide; false keeps Xinjiang time.",
    "considerationsMode": "How strictly to apply the considerations before judgment: warn, strict, lenient or ignore.",
    "dashaSystem": "Dasha system. Leave unset for vimshottari.",
    "dashaYearLength": "Length of a dasha year, such as 360 days, 365.25 days or a sidereal year. Leave unset for the Horosa default.",
    "deck": "Tarot deck. Leave unset for the Horosa default.",
    "dignities": "Whether to read elemental dignities between cards.",
    "doubingSu28": "Lunar-mansion measurement system, 0 to 8. Leave unset for 0.",
    "eclipseTimeMode": "Eclipse time shown: max for greatest eclipse (default) or syzygy.",
    "startDate": "First date of the range, YYYY-MM-DD.",
    "endDate": "Last date of the range, YYYY-MM-DD. The range can span at most 31 days.",
    "fenyeVersion": "Monthly stem-division table version. Leave unset for the Horosa default.",
    "guirengType": "Nobleman calculation method. Leave unset for the Horosa default.",
    "guolaoTrueSolarTime": "Time basis for the hour star: true for true solar time (default), mean for mean solar time, or off for clock time.",
    "hour": "Whole hour, 0 to 23, marked as the current hour in the hourly section. Defaults to 12.",
    "includeTransits": "Whether to list transits to the birth chart. Defaults to true.",
    "indiaAyanamsa": "Sidereal ayanamsa key. Leave unset to use the selected school's preset.",
    "indiaHsys": "House-system index, 0 to 24. Leave unset to use the selected school's preset.",
    "indiaSchool": "School: parashari (default), jaimini, tajika, kp, nadi or western_sidereal.",
    "isDiurnal": "Whether to treat the chart as a day chart: true for day, false for night.",
    "liuyaoSettings": "Optional Liu Yao calculation settings. Leave unset for the Horosa defaults.",
    "lotsSet": "Arabic lots to include: minimal (default) or core15.",
    "minorVariant": "Month length for minor progressions: synodic, sidereal or engine.",
    "moiraTransitDate": "Target date for the annual-transit section, YYYY-MM-DD.",
    "moiraTransitTime": "Target time for the annual-transit section, HH:mm:ss.",
    "options": "Optional Qimen settings. Unknown settings are rejected.",
    "profGrain": "Profection summary step: y for year (default), m for month or d for day.",
    "profStart": "Profection starting point: asc (default), sect, fortune, moon or mc.",
    "relative": "Relationship method index. Leave unset for the Horosa default.",
    "sihuaSchool": "Four Transformations school. Leave unset for the Horosa default.",
    "sincerityConfirmed": "Whether the asker confirms the question is sincere. Defaults to true.",
    "southMonth": "Month pillar for southern-hemisphere births: none keeps it (default); chong uses the opposite branch. Ignored for northern births.",
    "southchart": "Southern-hemisphere chart setting. Leave unset for the Horosa default.",
    "showOneThree": "Whether to include the one-and-three transmission view.",
    "spread": "Tarot spread. Leave unset for the Horosa default.",
    "targetDate": "Target date of the progression, YYYY-MM-DD.",
    "targetTime": "Target time of the progression, HH:mm:ss.",
    "topicId": "Electional topic key. Leave unset for a general election.",
    "tradeSide": "Side to favor in a trade election: sell or buy. Unset by default.",
    "tradition": "Whether to use the traditional calculation mode.",
    "transitDate": "Transit date, YYYY/MM/DD.",
    "tajakaYear": "Year of the Tajaka annual chart.",
    "usesReversals": "Whether reversed cards are allowed.",
    "variant": "Tarot spread variant. Leave unset for the Horosa default.",
    "verdictMode": "How the verdict section is worked out. Leave unset for the Horosa default.",
    "year": "Calendar year of the solar terms, for example 2026.",
    "yue": "Monthly general (Yue Jiang) setting. Leave unset for the Horosa default.",
    "zodiacBoundary": "Year boundary for the zodiac animal: lichun (default) or lunar. Changes only the zodiac line in the chart information.",
    "seed": "Optional seed the user supplies. Never invent one. Without a seed, the casting date and time give a deterministic draw.",
    "nums": "Three positive numbers the user supplies. Omit them only when the event date, time and timezone are given.",
    "inner": "First subject's explicit birth data; no names or current location.",
    "outer": "Second subject's explicit birth data; no names or current location.",
}


def _invalid(fields: list[str] | None = None) -> ToolValidationError:
    # Field names only. Never retain validation input, exception text or user values.
    return ToolValidationError(
        "公共参数无效或不完整。 / Public input is invalid or incomplete.",
        code="tool.public_invalid_payload",
        details={"fields": fields or []},
    )


@lru_cache(maxsize=1)
def load_public_surface() -> dict[str, Any]:
    contract = json.loads((_resource_files("horosa_public.contracts") / "public_surface.json").read_text(encoding="utf-8"))
    validate_public_contract(contract)
    return contract


def validate_public_contract(contract: dict[str, Any]) -> None:
    """Fail closed on malformed/expanded contracts; tests supply negative copies."""
    if contract.get("schema") != "horosa.public_surface.v1":
        raise _invalid()
    tools = contract.get("tools")
    if not isinstance(tools, list) or not tools:
        raise _invalid()
    names: set[str] = set()
    ids: set[str] = set()
    forbidden = set(contract.get("forbidden_input_keys", []))
    for item in tools:
        tool_id, name = item.get("id"), item.get("name")
        if (
            tool_id not in TOOL_DEFINITIONS or tool_id in ids or name in names
            or not isinstance(name, str) or not re.fullmatch(r"horosa_[a-z0-9_]{1,48}", name)
            or tool_id.endswith("zeri") or TOOL_DEFINITIONS[tool_id].domain in {"export", "knowledge"}
            or not item.get("title") or not 1 <= len(item.get("description", "")) <= 1024
        ):
            raise _invalid()
        fields = set(item.get("fields", []))
        for group in item.get("field_groups", []):
            if group not in contract.get("field_groups", {}):
                raise _invalid()
            fields.update(contract["field_groups"][group])
        if fields & forbidden or not set(item.get("required", [])) <= fields:
            raise _invalid()
        optional = set(item.get("optional_fields", []))
        if item.get("place") not in contract.get("place_policies", {}) or not optional <= {"lat", "lon"}:
            raise _invalid()
        if item.get("place") in {"client", "none"} and {"lat", "lon"} & (fields | optional):
            raise _invalid()
        if item.get("place") == "user_optional" and optional != {"lat", "lon"}:
            raise _invalid()
        if item.get("place_fallback") is not None and item["place_fallback"] not in contract.get("place_fallbacks", {}):
            raise _invalid()
        if item.get("client_hint") not in (None, True) or (item.get("client_hint") and item.get("place") != "user_optional"):
            raise _invalid()
        names.add(name)
        ids.add(tool_id)
    guidance = contract.get("guidance", {})
    if guidance.get("name") != "horosa_agent_guidance" or guidance["name"] in names:
        raise _invalid()
    limits = contract.get("limits", {})
    for key in ("max_request_body_bytes", "max_output_bytes", "timeout_seconds", "max_concurrent_tools", "max_text_chars", "max_array_items", "max_depth", "ephemeris_max_days"):
        if type(limits.get(key)) not in (int, float) or limits[key] <= 0:
            raise _invalid()
    if not contract.get("denied_section_patterns") or not contract.get("denied_content_patterns"):
        raise _invalid()


def allowed_public_tool_ids() -> set[str]:
    return {item["id"] for item in load_public_surface()["tools"]}


def public_tools() -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in load_public_surface()["tools"]}


def public_tool_names() -> set[str]:
    contract = load_public_surface()
    return {item["name"] for item in contract["tools"]} | {contract["guidance"]["name"]}


def _public_fields(tool_id: str) -> list[str]:
    item = public_tools().get(tool_id)
    if item is None:
        raise _invalid()
    contract = load_public_surface()
    fields: list[str] = []
    for group in item["field_groups"]:
        fields.extend(contract["field_groups"][group])
    return list(dict.fromkeys([*fields, *item["fields"], *item.get("optional_fields", [])]))


def _primitive_property(field: str, raw: dict[str, Any]) -> dict[str, Any]:
    prop = portable_property(field, raw)
    prop["description"] = _FIELD_DESCRIPTIONS.get(field) or str(raw.get("description") or f"User-confirmed {field} calculation setting.")[:180]
    # Any/union fields in the local models are scalar public choices, never object passthrough.
    if prop.get("type") == "object":
        raise _invalid([field])
    if field in {"lat", "lon", "zone", "dirLat", "dirLon", "dirZone", "siderealAyanamsa", "indiaAyanamsa", "sihuaSchool", "school"}:
        prop["type"] = "string"
    if field in {"gender", "zodiacal", "after23NewDay", "lateZiHourUseNextDay", "indiaHsys"}:
        prop["type"] = "integer"
    if prop["type"] == "string":
        prop["maxLength"] = load_public_surface()["limits"]["max_text_chars"] if field in {"question", "questionText", "askEvent", "clarification_notes", "seed"} else 128
    if prop["type"] == "array":
        if prop.get("items", {}).get("type") in {None, "object", "array"}:
            raise _invalid([field])
        prop["maxItems"] = load_public_surface()["limits"]["max_array_items"]
    if field in {"gender", "zodiacal", "after23NewDay", "lateZiHourUseNextDay", "timeAlg"}:
        prop["enum"] = [0, 1]
    if field in {"hsys", "indiaHsys"}:
        prop.update(minimum=0, maximum=24)
    if field == "ad":
        prop["enum"] = [-1, 1]
    if field == "nums":
        prop.update(minItems=3, maxItems=3, items={"type": "integer", "minimum": 1, "maximum": 999999})
    return prop


@lru_cache(maxsize=None)
def public_input_schema(tool_id: str) -> dict[str, Any]:
    item = public_tools().get(tool_id)
    if item is None:
        raise _invalid()
    model = TOOL_DEFINITIONS[tool_id].input_model
    raw = model.model_json_schema()
    props = raw.get("properties", {})
    nested = load_public_surface()["nested_inputs"]
    public_props = {}
    for field in _public_fields(tool_id):
        explicit = nested.get(f"{tool_id}.{field}")
        if explicit is not None:
            prop = deepcopy(explicit)
            prop.setdefault("description", _FIELD_DESCRIPTIONS.get(field, f"Public {field} calculation settings."))
        else:
            if field not in props:
                raise _invalid([field])
            prop = _primitive_property(field, props[field])
        if field in item.get("descriptions", {}):
            prop["description"] = item["descriptions"][field]
        public_props[field] = prop
    optional = set(item.get("optional_fields", []))
    required = list(dict.fromkeys([*[key for key in raw.get("required", []) if key in public_props and key not in optional], *item.get("required", [])]))
    # Xiaoliuren may be cast by supplied numbers, without birth/event data.
    return {"type": "object", "properties": public_props, "additionalProperties": False, **({"required": required} if required else {})}


def _bounded_shape(value: Any, *, depth: int = 0) -> None:
    limits = load_public_surface()["limits"]
    if depth > limits["max_depth"]:
        raise _invalid()
    if isinstance(value, dict):
        if set(value) & set(load_public_surface()["forbidden_input_keys"]):
            raise _invalid()
        for child in value.values():
            _bounded_shape(child, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > limits["max_array_items"]:
            raise _invalid()
        for child in value:
            _bounded_shape(child, depth=depth + 1)
    elif isinstance(value, str):
        if len(value) > limits["max_text_chars"]:
            raise _invalid()
    elif value is not None and type(value) not in (bool, int, float):
        raise _invalid()


def validate_public_shape(tool_id: str, payload: Any) -> dict[str, Any]:
    """Check the raw boundary before the SDK can discard undeclared argument keys."""
    if not isinstance(payload, dict):
        raise _invalid()
    _bounded_shape(payload)
    schema = public_input_schema(tool_id)
    if set(payload) - set(schema["properties"]):
        raise _invalid()
    # Optional nulls are absent, but forbidden keys are refused even when null.
    clean = {key: value for key, value in payload.items() if value is not None}
    # This normalizer also handles nested birth data and gender; it does not calculate charts.
    normalized = normalize_request_payload(clean)
    # Normalization internally adds GPS aliases and timezone provenance. They are
    # never public inputs; validate only user-provided keys, then forward the original
    # allowed shape so service normalization keeps its complete internal provenance.
    def project(original: Any, value: Any, field_schema: dict[str, Any]) -> Any:
        if isinstance(original, dict) and isinstance(value, dict):
            return {key: project(child, value.get(key), field_schema.get("properties", {}).get(key, {})) for key, child in original.items()}
        if isinstance(original, list) and isinstance(value, list):
            return [project(child, normalized_child, field_schema.get("items", {})) for child, normalized_child in zip(original, value)]
        if field_schema.get("type") == "integer" and type(value) is bool:
            return int(value)
        return value

    checked = project(clean, normalized, schema)
    shape_schema = {**schema, "required": []}
    errors = list(Draft202012Validator(shape_schema).iter_errors(checked))
    if errors:
        raise _invalid([str(error.path[0]) for error in errors if error.path and str(error.path[0]) in schema["properties"]][:8])
    return clean


def validate_public_payload(tool_id: str, payload: Any, *, injected: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate the client's payload, then merge server-resolved keys (rounded or synthesized place, forced clock time)."""
    clean = validate_public_shape(tool_id, payload)
    if injected:
        clean = {**clean, **injected}
    schema = public_input_schema(tool_id)
    missing = [key for key in schema.get("required", []) if key not in clean or clean[key] == ""]
    if tool_id == "xiaoliuren" and not clean.get("nums"):
        missing.extend(key for key in ("date", "time", "zone") if not clean.get(key))
    if missing:
        raise _invalid(missing)
    if tool_id == "ephemeris":
        try:
            span = (date.fromisoformat(clean["endDate"].replace("/", "-")) - date.fromisoformat(clean["startDate"].replace("/", "-"))).days
        except (ValueError, TypeError):
            raise _invalid(["startDate", "endDate"]) from None
        if not 0 <= span <= load_public_surface()["limits"]["ephemeris_max_days"]:
            raise _invalid(["startDate", "endDate"])
    try:
        TOOL_DEFINITIONS[tool_id].input_model.model_validate(normalize_request_payload(clean))
    except ValidationError as exc:
        fields = [str(error["loc"][0]) for error in exc.errors(include_input=False) if error.get("loc") and str(error["loc"][0]) in schema["properties"]]
        raise _invalid(fields[:8]) from None
    # Do not forward model defaults for hidden fields, and do not remove the user's explicit settings.
    return clean


class _PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicSection(_PublicModel):
    title: str
    text: str


class PublicSetting(_PublicModel):
    field: str
    label: str
    value: str


class PublicEngine(_PublicModel):
    component: str
    engine: str


class PublicVersions(_PublicModel):
    skill: str | None = None
    runtime: str | None = None
    export_settings: str | None = None


class PublicTechniqueCard(_PublicModel):
    technique: str
    tool: str
    settings: list[PublicSetting] = Field(default_factory=list)
    engines: list[PublicEngine] = Field(default_factory=list)
    declared_engines: list[str] = Field(default_factory=list)
    declared_class: str | None = None
    matches_declaration: bool | None = None
    public_section_count: int = 0
    contract_clean: bool | None = None
    versions: PublicVersions = Field(default_factory=PublicVersions)


class PublicRecovery(_PublicModel):
    must_ask_user: bool = True
    prompt_to_user: str
    fields: list[str] = Field(default_factory=list)
    retry_requires_one_of: list[str] = Field(default_factory=lambda: ["agent_confirmed_settings", "defaults_accepted"])


class PublicError(_PublicModel):
    code: str
    message: str
    agent_recovery: PublicRecovery


class PublicField(_PublicModel):
    field: str
    description: str
    required: bool


class PublicGuidance(_PublicModel):
    tool: str
    fields: list[PublicField]
    instruction: str


class PublicResult(_PublicModel):
    ok: bool
    tool: str
    sections: list[PublicSection] = Field(default_factory=list)
    technique_card: PublicTechniqueCard | None = None
    warnings: list[str] = Field(default_factory=list)
    error: PublicError | None = None
    guidance: PublicGuidance | None = None


_CONFIRM = (
    "请先向用户确认所需时间、出生地或指定事件地点、时区和计算口径；只有用户明确确认或接受默认后才重试。 / "
    "Ask the user to confirm the required time, birthplace or specified event place, timezone and calculation settings. "
    "Retry only after explicit confirmation or explicit acceptance of defaults. Never request the user's current location."
)


def public_error(tool_id: str, code: str = "tool.public_invalid_payload", *, fields: list[str] | None = None, must_ask_user: bool = True, prompt: str | None = None) -> PublicResult:
    items = public_tools()
    tool_name = items[tool_id]["name"] if tool_id in items else load_public_surface()["guidance"]["name"]
    safe_fields = [field for field in (fields or []) if field in (_public_fields(tool_id) if tool_id in items else ["tool_name"])]
    if code == "agent_guidance.required":
        message = "需要用户确认设置。 / User confirmation is required."
    elif code == "tool.public_timeout":
        message = "计算超过响应时限。 / The calculation exceeded the response deadline."
    elif code == "tool.public_unavailable":
        message = "公共计算暂不可用，请稍后重试。 / Public calculation is temporarily unavailable; retry later."
    elif code == "tool.public_invalid_payload":
        message = "公共参数无效或不完整。 / Public input is invalid or incomplete."
    elif code == "public.location_unavailable":
        message = ("此盘按提问时所在地起盘，公共版只从客户端的粗略位置获取，当前未提供。 / "
                   "This chart is cast for the place of the question; the public version obtains it only from the client's coarse location hint, which was not provided.")
    else:
        # Preserve a machine error code but never backend exception text, request previews or traces.
        code = code if re.fullmatch(r"[a-z0-9_.]{1,80}", code or "") else "tool.public_failed"
        message = "计算未完成，请核对参数后重试。 / Calculation did not complete; check the inputs before retrying."
    return PublicResult(ok=False, tool=tool_name, error=PublicError(
        code=code, message=message,
        agent_recovery=PublicRecovery(must_ask_user=must_ask_user, prompt_to_user=prompt or (_CONFIRM if must_ask_user else "请稍后重试同一请求。 / Retry the same request later."), fields=safe_fields),
    ))


def public_preflight(tool_id: str, payload: dict[str, Any]) -> PublicResult | None:
    # Deliberately cannot be disabled through HOROSA_CLARIFY or loosened by coercing a string to true.
    if payload.get("agent_confirmed_settings") is True or payload.get("defaults_accepted") is True:
        return None
    return public_error(tool_id, "agent_guidance.required", fields=[key for key in public_input_schema(tool_id).get("required", []) if key not in payload])


def public_guidance(tool_name: str) -> PublicResult:
    match = next((item for item in public_tools().values() if item["name"] == tool_name), None)
    if match is None:
        return public_error("guidance", fields=["tool_name"])
    schema = public_input_schema(match["id"])
    return PublicResult(ok=True, tool=load_public_surface()["guidance"]["name"], guidance=PublicGuidance(
        tool=tool_name,
        fields=[PublicField(field=key, description=value["description"], required=key in schema.get("required", [])) for key, value in schema["properties"].items()],
        instruction=_CONFIRM,
    ))


def _denied(text: str, key: str) -> bool:
    lowered = text.casefold()
    return any(pattern.casefold() in lowered for pattern in load_public_surface()[key])


_META_LINE = re.compile(r"trace[_ -]?id|group[_ -]?id|run[_ -]?id|request[_ -]?id|session[_ -]?id|call[_ -]?id|memory_ref|generated_at|created_at|exported_at|artifact_path|生成时间|生成于|导出时刻|导出时间|generated (?:at|on)|日志|timestamp|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", re.I)


def _clean_text(value: Any, *, limit: int = 2048) -> str:
    if type(value) not in (str, int, float, bool):
        return ""
    return "\n".join(line for line in str(value).splitlines() if not _META_LINE.search(line))[:limit]


def _card_text(value: Any, *, limit: int = 2048) -> str:
    text = _clean_text(value, limit=limit)
    return "" if _denied(text, "denied_content_patterns") or _denied(text, "denied_section_patterns") else text


def _card(tool_id: str, raw: Any, count: int) -> PublicTechniqueCard | None:
    if not isinstance(raw, dict):
        return None
    compute = raw.get("compute") if isinstance(raw.get("compute"), dict) else {}
    settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    versions = raw.get("versions") if isinstance(raw.get("versions"), dict) else {}
    technique = raw.get("technique") if isinstance(raw.get("technique"), dict) else {}
    measured = compute.get("measured") if isinstance(compute.get("measured"), dict) else {}
    declared = compute.get("declared_engines") if isinstance(compute.get("declared_engines"), list) else []
    health = raw.get("sections") if isinstance(raw.get("sections"), dict) else {}
    return PublicTechniqueCard(
        technique=_card_text(technique.get("label") or technique.get("key") or public_tools()[tool_id]["title"], limit=120),
        tool=public_tools()[tool_id]["name"],
        settings=[PublicSetting(field=field, label=_card_text(item.get("label"), limit=100), value=_card_text(item.get("value"), limit=300))
                  for field, item in settings.items() if field in load_public_surface()["card_setting_fields"] and isinstance(item, dict)],
        engines=[PublicEngine(component=_card_text(key, limit=80), engine=_card_text(value, limit=120))
                 for key, value in list(measured.items())[:16] if not _META_LINE.search(str(key)) and isinstance(value, str)],
        declared_engines=[_card_text(value, limit=120) for value in declared[:16] if isinstance(value, str)],
        declared_class=_card_text(compute.get("declared_class"), limit=120) or None,
        matches_declaration=compute.get("matches_declaration") if type(compute.get("matches_declaration")) is bool else None,
        public_section_count=count,
        contract_clean=health.get("contract_clean") if type(health.get("contract_clean")) is bool else None,
        versions=PublicVersions(**{key: _card_text(versions.get(key), limit=80) or None for key in ("skill", "runtime", "export_settings")}),
    )


def public_result_size(result: PublicResult) -> int:
    # Count the actual MCP result shape, including both copies and escaped text JSON.
    from mcp.types import CallToolResult, TextContent

    wire = CallToolResult(content=[TextContent(type="text", text=result.model_dump_json())],
                          structured_content=result.model_dump(mode="json"), is_error=not result.ok)
    return len(wire.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8"))


def _strip_place_echo(text: str) -> str:
    prefixes = tuple(load_public_surface().get("place_echo_prefixes", []))
    return "\n".join(line for line in text.split("\n") if not line.strip().startswith(prefixes))


def render_public_result(tool_id: str, envelope: Any, *, place_synthesized: bool = False, extra_warnings: list[str] | tuple[str, ...] = ()) -> PublicResult:
    raw = envelope.model_dump(mode="json") if isinstance(envelope, BaseModel) else envelope
    if not isinstance(raw, dict):
        return public_error(tool_id, "tool.public_failed", must_ask_user=False)
    if raw.get("ok") is not True:
        error = raw.get("error") if isinstance(raw.get("error"), dict) else {}
        code = error.get("code") or raw.get("code") or "tool.public_failed"
        return public_error(tool_id, code, must_ask_user=code == "agent_guidance.required" or "invalid" in str(code))
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    snapshot = data.get("export_snapshot") if isinstance(data.get("export_snapshot"), dict) else {}
    source_sections = snapshot.get("sections")
    if not isinstance(source_sections, list):
        source_sections = split_content_sections(str(snapshot.get("export_text") or ""), str(snapshot.get("technique") or ""))
    sections: list[PublicSection] = []
    filtered = False
    metadata_omitted = False
    limit = int(load_public_surface()["limits"]["max_output_bytes"])
    for section in source_sections:
        if not isinstance(section, dict) or section.get("included") is False:
            continue
        title = _clean_text(section.get("title") or section.get("raw_title"), limit=200)
        if title in load_public_surface().get("excluded_section_titles", []):
            metadata_omitted = True
            continue
        text = _clean_text(section.get("body") or section.get("text"), limit=limit)
        if place_synthesized:
            # A synthesized place (timezone meridian) must never be echoed as if it were real.
            text = _strip_place_echo(text).strip()
        if _denied(title, "denied_section_patterns"):
            filtered = True
            continue
        # One denied line must not take a whole chart with it, so content is judged line by line.
        lines = text.splitlines()
        kept = [line for line in lines if not _denied(line, "denied_content_patterns")]
        if len(kept) != len(lines):
            filtered = True
            text = "\n".join(kept).strip()
        if text:
            sections.append(PublicSection(title=title, text=text))
    warnings: list[str] = list(extra_warnings)
    if raw.get("warnings"):
        warnings.append("引擎报告了计算警告；请谨慎使用可能不完整的结果。 / The engine reported calculation warnings; the result may be incomplete.")
    if filtered:
        warnings.append("公共版本已省略不适合一般受众的判读内容。 / Interpretive content unsuitable for a general audience was omitted.")
    if metadata_omitted:
        warnings.append("公共版本已省略依赖导出时刻的附加信息。 / Additional context based on the export time was omitted.")
    result = PublicResult(ok=True, tool=public_tools()[tool_id]["name"], sections=sections, technique_card=_card(tool_id, data.get("technique_card"), len(sections)), warnings=warnings)
    if result.technique_card and result.technique_card.matches_declaration is False:
        result.warnings.append("实际算源与声明不一致。 / Measured computation does not match its declaration.")
    if not sections:
        result.warnings.append("本地引擎没有返回可公开的段落。 / The engine returned no publishable sections.")
    if public_result_size(result) > limit:
        result.warnings.append("输出达到体积上限，末尾段落已截断。 / Output reached the size limit; trailing sections were truncated.")
        while result.sections and public_result_size(result) > limit:
            last = result.sections[-1]
            original = last.text
            last.text = "…"
            if public_result_size(result) > limit:
                result.sections.pop()
            else:
                # JSON quoting and the duplicate structured copy make a byte subtraction
                # inaccurate. Search whole Unicode prefixes against the actual wire shape.
                lo, hi = 0, len(original)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    last.text = original[:mid] + "…"
                    if public_result_size(result) <= limit:
                        lo = mid
                    else:
                        hi = mid - 1
                last.text = original[:lo] + "…"
        if result.technique_card:
            result.technique_card.public_section_count = len(result.sections)
    return result


# --- Place policy -----------------------------------------------------------------------------

WARN_CLOCK_TIME = ("未提供地点：按钟表时计算，未做真太阳时与地理修正。 / "
                   "No place was given: computed with clock time, without true-solar-time or geographic correction.")
WARN_CLIENT_PLACE = ("地点取自客户端提供的粗略位置（城市级）。 / "
                     "The place came from the client's coarse location hint (city level).")
WARN_ROUNDED = ("坐标已按城市级精度（0.01°）取整。 / Coordinates were rounded to city-level precision (0.01°).")
PLACE_DECIMALS = 2
_COMPACT = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s*([nsewNSEW])\s*(\d{1,2}(?:\.\d+)?)?\s*$")
_ZONE_OFFSET = re.compile(r"^([+-])(\d{2}):?(\d{2})$")


def coordinate_decimal(value: Any, axis: str) -> float | None:
    """Parse ``31n13`` / ``121e28`` / decimal strings or numbers into signed decimal degrees."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        decimal = float(value)
    else:
        text = str(value).strip()
        match = _COMPACT.match(text)
        if match:
            degrees, hemisphere, minutes = match.groups()
            decimal = float(degrees) + (float(minutes) if minutes else 0.0) / 60.0
            if hemisphere.lower() in ("s", "w"):
                decimal = -decimal
            if (axis == "lat") != (hemisphere.lower() in ("n", "s")):
                return None
        else:
            try:
                decimal = float(text)
            except ValueError:
                return None
    limit = 90.0 if axis == "lat" else 180.0
    return decimal if -limit <= decimal <= limit else None


def city_level(value: Any, axis: str) -> str | None:
    decimal = coordinate_decimal(value, axis)
    return None if decimal is None else f"{round(decimal, PLACE_DECIMALS):.{PLACE_DECIMALS}f}"


def zone_meridian(zone: Any, on_date: Any = None, at_time: Any = None) -> float:
    """Central meridian (degrees) of the timezone in force at the given moment; 0.0 when the zone is unreadable."""
    text = str(zone or "").strip()
    match = _ZONE_OFFSET.match(text)
    if match:
        sign, hours, minutes = match.groups()
        offset_hours = int(hours) + int(minutes) / 60.0
        return (-offset_hours if sign == "-" else offset_hours) * 15.0
    try:
        moment = datetime.fromisoformat(f"{str(on_date or '2000-01-01').replace('/', '-')[:10]}T{str(at_time or '12:00:00')[:8]}")
        offset = moment.replace(tzinfo=ZoneInfo(text)).utcoffset()
    except Exception:  # noqa: BLE001 - an unreadable zone is reported by model validation, not here
        return 0.0
    return 0.0 if offset is None else offset.total_seconds() / 3600.0 * 15.0


def client_place(hint: Any) -> tuple[str, str] | None:
    """City-level coordinates from the client's coarse location hint; None when absent or malformed."""
    if not isinstance(hint, dict):
        return None
    lat, lon = city_level(hint.get("latitude"), "lat"), city_level(hint.get("longitude"), "lon")
    if lat is None or lon is None or isinstance(hint.get("latitude"), str) or isinstance(hint.get("longitude"), str):
        return None
    return lat, lon


@dataclass
class PlaceResolution:
    injected: dict[str, Any] = _dc_field(default_factory=dict)
    warnings: list[str] = _dc_field(default_factory=list)
    synthesized: bool = False
    error: PublicResult | None = None


def _rounded_pair(container: dict[str, Any], lat_key: str, lon_key: str) -> tuple[dict[str, Any], bool]:
    """Round a lat/lon pair in place (copy); report whether rounding changed a value."""
    updated, changed = dict(container), False
    for key, axis in ((lat_key, "lat"), (lon_key, "lon")):
        if key in container and container[key] not in (None, ""):
            rounded = city_level(container[key], axis)
            if rounded is None:
                raise _invalid([key])
            original = coordinate_decimal(container[key], axis)
            changed = changed or abs(float(rounded) - float(original)) > 1e-9
            updated[key] = rounded
    return updated, changed


def resolve_place(tool_id: str, clean: dict[str, Any], client_location: Any = None) -> PlaceResolution:
    """Apply the tool's place policy to a shape-validated payload.

    Returns the keys to inject before model validation: rounded user coordinates, the client's coarse
    place, or a synthesized timezone meridian with clock time forced. Never reads anything else.
    """
    item = public_tools()[tool_id]
    policy, fallback = item.get("place", "user"), item.get("place_fallback")
    result = PlaceResolution()
    changed = False
    if policy == "user":
        top, changed_top = _rounded_pair(clean, "lat", "lon")
        changed = changed or changed_top
        result.injected.update({key: top[key] for key in ("lat", "lon") if key in top})
        dirs, changed_dir = _rounded_pair(clean, "dirLat", "dirLon")
        changed = changed or changed_dir
        result.injected.update({key: dirs[key] for key in ("dirLat", "dirLon") if key in dirs})
        for party in ("inner", "outer"):
            if isinstance(clean.get(party), dict):
                nested, changed_nested = _rounded_pair(clean[party], "lat", "lon")
                changed = changed or changed_nested
                result.injected[party] = nested
    elif policy == "user_optional":
        if clean.get("lat") not in (None, "") and clean.get("lon") not in (None, ""):
            top, changed = _rounded_pair(clean, "lat", "lon")
            result.injected.update({"lat": top["lat"], "lon": top["lon"]})
        elif clean.get("lat") not in (None, "") or clean.get("lon") not in (None, ""):
            raise _invalid(["lat", "lon"])
        else:
            _apply_fallback(tool_id, clean, result, fallback, client_location)
    elif policy == "client":
        _apply_fallback(tool_id, clean, result, fallback, client_location)
    elif policy == "none":
        if fallback == "ignore":
            _synthesize(clean, result, force_clock_time=False, warn=False)
    if changed:
        result.warnings.append(WARN_ROUNDED)
    return result


def _apply_fallback(tool_id: str, clean: dict[str, Any], result: PlaceResolution, fallback: str | None, client_location: Any) -> None:
    item = public_tools()[tool_id]
    # Only a chart cast for "here and now" may take the client's coarse location; a birthplace never comes from it.
    hint = client_place(client_location) if item.get("place") == "client" or item.get("client_hint") is True else None
    if hint is not None:
        result.injected.update({"lat": hint[0], "lon": hint[1]})
        result.warnings.append(WARN_CLIENT_PLACE)
        return
    if fallback == "required":
        result.error = public_error(tool_id, "public.location_unavailable", must_ask_user=True,
                                    prompt=("告诉用户：此盘需要提问时所在的城市级位置，公共版只能从客户端的粗略位置获取，当前客户端未提供；不要向用户索取坐标。 / "
                                            "Tell the user that this chart needs the city-level place of the question and that the public version can only obtain it from the client's coarse location hint, which was not provided. Do not ask the user for coordinates."))
        return
    if fallback == "clock_time":
        _synthesize(clean, result, force_clock_time=True, warn=True)
        return
    if fallback == "ignore":
        _synthesize(clean, result, force_clock_time=False, warn=False)


def _synthesize(clean: dict[str, Any], result: PlaceResolution, *, force_clock_time: bool, warn: bool) -> None:
    meridian = zone_meridian(clean.get("zone"), clean.get("date"), clean.get("time"))
    result.injected.update({"lat": f"{0.0:.{PLACE_DECIMALS}f}", "lon": f"{meridian:.{PLACE_DECIMALS}f}"})
    result.synthesized = True
    if force_clock_time:
        result.injected["timeAlg"] = 1
    if warn:
        result.warnings.append(WARN_CLOCK_TIME)
