"""Fail-closed configuration for the public process.

Builds the released skill's :class:`horosa_skill.config.Settings` from an allowlisted environment
without modifying horosa-skill. The process refuses to start when the environment would enable a
local-only mode (memory store, tracing, cloud decisions, static tokens, runtime port discovery) or
when a backend URL is not an explicit loopback address with a port. Error details carry field
names only, never values.
"""
from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from horosa_skill.config import Settings
from horosa_skill.errors import ToolValidationError

CHALLENGE_ENV = "HOROSA_OPENAI_CHALLENGE"
# A path that must never come into existence. Every component that would write under data_dir or
# runtime_root is replaced by an inert stand-in (see horosa_public.service); tests point this at a
# temporary directory and assert it is never created.
DEFAULT_NO_STORE_DIR = Path("/nonexistent/horosa-public-no-store")

# Presence alone is a configuration error: these keys select local-only behaviour or carry secrets.
FORBIDDEN_ENV: tuple[str, ...] = (
    "HOROSA_MCP_COMPACT",
    "HOROSA_TOOLSETS",
    "HOROSA_MCP_TOKEN",
    "HOROSA_MCP_ELICIT",
    "HOROSA_MCP_ELICIT_TIMEOUT_SECONDS",
    "HOROSA_PORTS",
    "HOROSA_TRACE_DIR",
    "HOROSA_TRACE_CAPTURE_PAYLOADS",
    "HOROSA_TRACE_CAPTURE_AI_ANSWERS",
    "HOROSA_TRACE_OTLP_ENDPOINT",
    "HOROSA_SKILL_DB_PATH",
    "HOROSA_SKILL_OUTPUT_DIR",
    "HOROSA_REPORT_OUTPUT_ROOTS",
)
# These may be set only to an explicit "off" value.
FORBIDDEN_IF_TRUTHY: tuple[str, ...] = ("HOROSA_TRACE_ENABLED", "HOROSA_JEV")
_OFF = {"", "0", "false", "no", "off"}


def _config_invalid(fields: list[str]) -> ToolValidationError:
    return ToolValidationError(
        "公共模式配置无效。 / Public-mode configuration is invalid.",
        code="public.config_invalid",
        details={"fields": sorted(set(fields))},
    )


def _url_invalid(field_name: str) -> ToolValidationError:
    return ToolValidationError(
        "公共后端地址必须是显式回环 http://127.0.0.1:<端口>。 / Public backend URLs must be explicit loopback http://127.0.0.1:<port>.",
        code="public.backend_url_invalid",
        details={"fields": [field_name]},
    )


def forbidden_environment_fields(env: Mapping[str, str]) -> list[str]:
    """Names of environment keys that make a public process unsafe (values are never returned)."""
    fields = [key for key in FORBIDDEN_ENV if key in env]
    fields += [key for key in FORBIDDEN_IF_TRUTHY if env.get(key, "").strip().lower() not in _OFF]
    # Any other cloud-decision knob (API key, model, scope, ledger …) is a secret or enables network calls.
    fields += [key for key in env if key.startswith("HOROSA_JEV") and key != "HOROSA_JEV"]
    return sorted(set(fields))


def loopback_backend_url(value: str | None, field_name: str) -> str:
    """Accept only ``http://<loopback-ip>:<port>`` with no path, query, fragment or credentials."""
    if not isinstance(value, str) or not value.strip():
        raise _url_invalid(field_name)
    parts = urlsplit(value.strip())
    if parts.scheme != "http" or parts.path not in ("", "/") or parts.query or parts.fragment:
        raise _url_invalid(field_name)
    if parts.username is not None or parts.password is not None or not parts.hostname:
        raise _url_invalid(field_name)
    try:
        address = ipaddress.ip_address(parts.hostname)
        port = parts.port
    except ValueError:
        raise _url_invalid(field_name) from None
    if not address.is_loopback or port is None or not 1 <= port <= 65535:
        raise _url_invalid(field_name)
    host = f"[{address}]" if address.version == 6 else str(address)
    return f"http://{host}:{port}"


def bind_host(value: str | None) -> str:
    """Loopback (host behind a tunnel or proxy) or the container wildcard; nothing else."""
    host = (value or "127.0.0.1").strip()
    if host in {"0.0.0.0", "::"}:
        return host
    try:
        if ipaddress.ip_address(host).is_loopback:
            return host
    except ValueError:
        pass
    raise _config_invalid(["HOROSA_SKILL_HOST"])


def _int_env(env: Mapping[str, str], key: str, default: int, minimum: int, maximum: int) -> int:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise _config_invalid([key]) from None
    if not minimum <= value <= maximum:
        raise _config_invalid([key])
    return value


def _float_env(env: Mapping[str, str], key: str, default: float, minimum: float, maximum: float) -> float:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        raise _config_invalid([key]) from None
    if not minimum <= value <= maximum:
        raise _config_invalid([key])
    return value


@dataclass(frozen=True)
class PublicConfig:
    settings: Settings
    host: str
    port: int
    no_store_dir: Path
    challenge_env: str = CHALLENGE_ENV

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        host: str | None = None,
        port: int | None = None,
        no_store_dir: Path | None = None,
    ) -> "PublicConfig":
        env = dict(os.environ if env is None else env)
        fields = forbidden_environment_fields(env)
        if fields:
            raise _config_invalid(fields)
        server_root = loopback_backend_url(env.get("HOROSA_SERVER_ROOT"), "HOROSA_SERVER_ROOT")
        chart_root = loopback_backend_url(env.get("HOROSA_CHART_SERVER_ROOT"), "HOROSA_CHART_SERVER_ROOT")
        resolved_host = bind_host(host if host is not None else env.get("HOROSA_SKILL_HOST"))
        resolved_port = port if port is not None else _int_env(env, "HOROSA_SKILL_PORT", 8765, 1, 65535)
        if not 1 <= int(resolved_port) <= 65535:
            raise _config_invalid(["HOROSA_SKILL_PORT"])
        store_dir = Path(no_store_dir) if no_store_dir is not None else DEFAULT_NO_STORE_DIR
        settings = Settings(
            server_root=server_root,
            chart_server_root=chart_root,
            data_dir=store_dir,
            runtime_root=store_dir / "runtime",
            db_path=None,
            output_dir=None,
            mcp_compact=False,
            host=resolved_host,
            port=int(resolved_port),
            log_level="WARNING",
            trace_enabled=False,
            trace_dir=None,
            trace_capture_payloads=False,
            trace_capture_ai_answers=False,
            trace_otlp_endpoint=None,
            js_engine_timeout_seconds=_float_env(env, "HOROSA_JS_ENGINE_TIMEOUT_SECONDS", 60.0, 0.1, 600.0),
        )
        return cls(settings=settings, host=resolved_host, port=int(resolved_port), no_store_dir=store_dir)

    def summary(self) -> dict[str, Any]:
        """Redacted summary for ``horosa-public check``: no URLs, paths or token values."""
        return {
            "ok": True,
            "host": self.host,
            "port": self.port,
            "backends": "explicit loopback",
            "memory_store": "disabled",
            "trace": "disabled",
            "cloud_decisions": "disabled",
            "challenge_configured": bool(os.environ.get(self.challenge_env)),
        }
