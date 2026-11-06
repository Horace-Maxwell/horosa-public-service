"""The released ``HorosaSkillService`` with every persistence and lifecycle component replaced.

horosa-skill is not modified. Public mode is obtained from outside by

* passing inert stand-ins for the memory store and the runtime manager to the constructor,
* replacing the trace recorder with one that yields no ids and never writes,
* marking both backends as ready so a request can never trigger a runtime start, and
* overriding ``run_tool`` to enforce the public allowlist and discard persistence arguments.

Log emission is disabled by the CLI, and the output projection (``surface.render_public_result``)
never forwards engine warning text, so degrade notes that embed request values cannot reach a client.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from horosa_skill.config import Settings
from horosa_skill.errors import ToolValidationError
from horosa_skill.service import HorosaSkillService
from horosa_skill.tracing import TraceRecorder

from horosa_public.config import PublicConfig, forbidden_environment_fields
from horosa_public.surface import allowed_public_tool_ids


class PublicModeViolation(AttributeError):
    """A component that must stay inert in public mode was used.

    Subclasses ``AttributeError`` so ``getattr(obj, name, default)`` probes inside the skill keep
    working, while any direct use fails loudly instead of persisting data or starting processes.
    """


class NullMemoryStore:
    """Inert stand-in for ``MemoryStore``: truthy, opens nothing, every method is an error."""

    corruption_recovery = None

    def __getattr__(self, name: str) -> Any:
        raise PublicModeViolation(f"memory store is disabled in public mode: {name}")


class NullRuntimeManager:
    """Inert stand-in for ``HorosaRuntimeManager``: no installed runtime and no lifecycle actions."""

    def __init__(self) -> None:
        self.lifecycle_requests = 0

    def java_backend_cooldown_remaining(self) -> float:
        return 0.0

    def load_installed_manifest(self, *, strict: bool = False) -> None:
        return None

    def load_runtime_state(self, *, strict: bool = False) -> None:
        return None

    def runtime_mode(self) -> str:
        return "public-external"

    def start_local_services(self, *, wait_seconds: float | None = None) -> dict[str, Any]:
        # The skill asks for a (re)start after a backend connection error. Public deployments own
        # the backend lifecycle, so this only records the request and answers "nothing started";
        # the caller then retries the call once and surfaces the transport error.
        self.lifecycle_requests += 1
        return {"ok": False, "started": False, "starting": False, "degraded": False, "public_mode": True}

    @property
    def current_dir(self) -> Any:
        raise PublicModeViolation("no installed runtime in public mode")

    def __getattr__(self, name: str) -> Any:
        raise PublicModeViolation(f"runtime lifecycle is disabled in public mode: {name}")


class NullTraceRecorder(TraceRecorder):
    """Yields no ids and never writes, emits or sanitizes."""

    def __init__(self, settings: Settings) -> None:  # deliberately does not call super().__init__
        self.settings = settings
        self.trace_dir = None
        self.enabled = False

    def new_trace_id(self) -> None:  # type: ignore[override]
        return None

    def new_group_id(self) -> None:  # type: ignore[override]
        return None

    def latest_trace_files(self, *, limit: int = 5) -> list:
        return []

    def read_latest(self, *, limit: int = 50) -> list:
        return []

    @contextmanager
    def span(
        self,
        *,
        workflow_name: str,
        trace_id: str | None = None,
        group_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        yield {"trace_id": None, "group_id": None}

    def _write_event(self, event: dict[str, Any]) -> None:
        raise PublicModeViolation("trace emission is disabled in public mode")

    def _emit_otlp(self, event: dict[str, Any]) -> None:
        raise PublicModeViolation("trace emission is disabled in public mode")

    def _sanitize(self, value: Any, *, key: str | None = None) -> Any:
        raise PublicModeViolation("trace capture is disabled in public mode")


class PublicHorosaService(HorosaSkillService):
    """Anonymous, read-only, stateless service over the released skill."""

    def __init__(
        self,
        config: PublicConfig,
        *,
        client: Any = None,
        chart_client: Any = None,
        js_client: Any = None,
    ) -> None:
        # Re-check at construction time: the environment decides whether the skill would reach a
        # cloud decision layer (HOROSA_JEV) or other local-only behaviour.
        fields = forbidden_environment_fields(os.environ)
        if fields:
            raise ToolValidationError(
                "公共模式配置无效。 / Public-mode configuration is invalid.",
                code="public.config_invalid",
                details={"fields": fields},
            )
        self.config = config
        super().__init__(
            config.settings,
            client=client,
            chart_client=chart_client,
            store=NullMemoryStore(),
            js_client=js_client,
            runtime_manager=NullRuntimeManager(),
            decision_layer=None,
        )
        if self.decision_layer is not None:
            raise ToolValidationError(
                "公共模式不接受云端决策层。 / Public mode does not accept a cloud decision layer.",
                code="public.decision_layer_invalid",
            )
        self.tracer = NullTraceRecorder(config.settings)
        # Both backends are owned by the deployment: a request must never probe-and-start a runtime.
        self._java_runtime_ready = True
        self._chart_runtime_ready = True

    def run_tool(  # type: ignore[override]
        self,
        tool_name: str,
        payload: dict[str, Any],
        *,
        save_result: bool = False,
        run_id: str | None = None,
        query_text: str | None = None,
        group_id: str | None = None,
        evaluation_case_id: str | None = None,
    ):
        if tool_name not in allowed_public_tool_ids():
            raise ToolValidationError(
                "该技法不在公共工具白名单内。 / This technique is not on the public tool allowlist.",
                code="public.tool_invalid",
            )
        # Persistence and correlation arguments are discarded on purpose.
        return super().run_tool(tool_name, payload, save_result=False)


def assert_public_service(service: Any) -> None:
    """Refuse to serve anything that still carries a store, a cloud layer, a live tracer or a real runtime manager."""
    if not (
        isinstance(getattr(service, "store", None), NullMemoryStore)
        and isinstance(getattr(service, "runtime_manager", None), NullRuntimeManager)
        and getattr(getattr(service, "tracer", None), "enabled", None) is False
        and getattr(service, "decision_layer", object()) is None
        and getattr(service, "settings", None) is not None
    ):
        raise ToolValidationError(
            "公共模式配置不安全。 / Unsafe public-mode configuration.",
            code="public.configuration_invalid",
        )
