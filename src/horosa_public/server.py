# Ported from horosa-skill branch codex/chatgpt-plugin (052ce55) src/horosa_skill/surfaces/mcp_public.py;
# the service/config checks now target horosa_public's own types.
"""Dedicated stateless, anonymous public MCP surface; local registration is unchanged."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from inspect import Parameter, Signature
import json
import os
import threading
from typing import Annotated, Any

from mcp import types
from mcp.server.mcpserver import Context, MCPServer
from pydantic import WithJsonSchema
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from horosa_public import __version__
from horosa_skill.errors import ToolValidationError
from horosa_public.config import PublicConfig
from horosa_public.service import assert_public_service
from horosa_public.surface import (
    PublicResult,
    load_public_surface,
    public_error,
    public_guidance,
    public_input_schema,
    public_preflight,
    public_tool_names,
    public_tools,
    render_public_result,
    resolve_place,
    validate_public_payload,
    validate_public_shape,
)


PUBLIC_INSTRUCTIONS = """Horosa 星阙 calculates traditional astrology and divination charts for cultural and entertainment reference.
Use one individually listed tool for one calculation. Ask the user for missing result-changing time,
birthplace or specified event place, timezone and school settings; never obtain the user's current location.
Set agent_confirmed_settings=true only after explicit confirmation, or defaults_accepted=true only after
the user explicitly accepts Horosa defaults. clarification_notes is optional. Never invent a casting seed.
The guidance tool explains settings for one public calculation and does not run it.
Explain only the returned section text; do not hand-calculate missing results. Quote the technique card's
settings, actual engine, section health and version chain. Disclose matches_declaration=false.
Respect warnings and omitted/truncated sections. Do not infer a result from a missing section.
These outputs are cultural and entertainment references, not medical, legal or financial advice.
The public interface stores no user inputs or results and provides no memory, report-file or local installation tools.
"""

_NOAUTH = [{"type": "noauth"}]
_READONLY = types.ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False, idempotentHint=True)


class _PublicRequestBoundary:
    """Reject malformed protocol messages before SDK errors can echo their values."""

    def __init__(self, app: Any, *, max_bytes: int, timeout: float) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.timeout = timeout

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") not in {"/mcp", "/mcp/"}:
            await self.app(scope, receive, send)
            return
        body = bytearray()
        deadline = asyncio.get_running_loop().time() + self.timeout
        while True:
            try:
                remaining = max(0.0, deadline - asyncio.get_running_loop().time())
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except TimeoutError:
                await JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Request body deadline exceeded."}}, status_code=408)(scope, receive, send)
                return
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                await JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Request body limit exceeded."}}, status_code=413)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        try:
            payload = json.loads(body)
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            types.jsonrpc_message_adapter.validate_python(payload)
            if not isinstance(payload, dict):
                raise ValueError("公共消息格式无效。 / Invalid public message.")
            method = payload.get("method")
            if "id" in payload:
                if type(payload["id"]) not in (str, int) or (isinstance(payload["id"], str) and len(payload["id"]) > 128):
                    raise ValueError("公共请求编号无效。 / Invalid public request identifier.")
                if method not in {"initialize", "server/discover", "ping", "tools/list", "tools/call", "resources/list", "resources/templates/list", "prompts/list"}:
                    raise ValueError("公共方法不可用。 / Public method unavailable.")
                types.client_request_adapter.validate_python(payload)
                if method == "tools/call" and payload.get("params", {}).get("name") not in public_tool_names():
                    raise ValueError("公共工具不可用。 / Public tool unavailable.")
            else:
                if method not in {"notifications/initialized", "notifications/cancelled"}:
                    raise ValueError("公共通知不可用。 / Public notification unavailable.")
                types.client_notification_adapter.validate_python(payload)
        except Exception:
            await JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid public MCP request."}}, status_code=400)(scope, receive, send)
            return

        consumed = False

        async def replay() -> dict[str, Any]:
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class _PublicWorkPool:
    """Bound actual computations, including computations whose clients time out/cancel.

    Cancellation cannot stop synchronous engines safely. A timed-out worker keeps its
    permit until it actually returns; repeated timeouts cannot accumulate unbounded
    orphan work. Excess calls receive a bounded busy response instead of queuing.
    """

    def __init__(self, max_workers: int, timeout: float) -> None:
        self.timeout = timeout
        self._slots = threading.BoundedSemaphore(max_workers)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="horosa-public")

    async def run(self, tool_id: str, fn: Any) -> PublicResult:
        if not self._slots.acquire(blocking=False):
            return public_error(tool_id, "tool.public_unavailable", must_ask_user=False)
        try:
            future = self._executor.submit(fn)
        except Exception:
            self._slots.release()
            return public_error(tool_id, "tool.public_unavailable", must_ask_user=False)
        future.add_done_callback(lambda _: self._slots.release())
        wrapped = asyncio.wrap_future(future)
        try:
            return await asyncio.wait_for(asyncio.shield(wrapped), timeout=self.timeout)
        except TimeoutError:
            return public_error(tool_id, "tool.public_timeout", must_ask_user=False)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


async def _top_level_security_schemes(ctx: Any, call_next: Any) -> Any:
    """Add the top-level `securitySchemes` OpenAI reads first to each tool in tools/list.

    mcp 2.x filters results through its per-version wire models, which drop undeclared keys, so
    the key cannot come from the handler. Middleware sees the filtered wire dict. `_meta` keeps
    the mirror for clients that only read `_meta`.
    """
    result = await call_next(ctx)
    if ctx.method != "tools/list":
        return result
    return {**result, "tools": [{**tool, "securitySchemes": [dict(item) for item in _NOAUTH]} for tool in result["tools"]]}


class PublicFastMCP(MCPServer):
    """Public-only descriptor metadata and raw call boundary.

    The SDK's tool layer drops additional argument keys before invoking registered functions.
    Validate the original mapping here and invoke only the public handlers, so hidden
    local model fields and request escape hatches cannot become accepted inputs.
    """

    _public_host: str
    _public_port: int
    _public_transport_security: Any

    def streamable_http_app(self) -> Any:
        limits = load_public_surface()["limits"]
        app = super().streamable_http_app(
            streamable_http_path="/mcp", stateless_http=True, json_response=True,
            max_request_body_size=int(limits["max_request_body_bytes"]),
            transport_security=self._public_transport_security, host=self._public_host,
        )
        app.add_middleware(_PublicRequestBoundary, max_bytes=int(limits["max_request_body_bytes"]), timeout=float(limits["timeout_seconds"]))
        return app

    def client_location(self, context: Context | None = None) -> Any:
        """The client's coarse location hint (_meta openai/userLocation) for the current request, if any."""
        try:
            hint = context.request_context.meta.get("openai/userLocation")
        except Exception:  # noqa: BLE001 - outside a request there is no hint
            return None
        return hint if isinstance(hint, dict) else None

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Context | None = None) -> types.CallToolResult:
        handler = self._public_handlers.get(name)
        tool_id = self._public_ids.get(name, "guidance")
        if handler is None:
            result = public_error(tool_id)
        else:
            try:
                result = await handler(arguments, self.client_location(context))
            except ToolValidationError as exc:
                result = public_error(tool_id, fields=exc.details.get("fields", []))
            except Exception:
                # Never serialize/log an exception carrying user inputs or internal paths.
                result = public_error(tool_id, "tool.public_failed", must_ask_user=False)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=result.model_dump_json())],
            structured_content=result.model_dump(mode="json"),
            is_error=not result.ok,
        )

    async def run_streamable_http_async(self) -> None:
        import uvicorn

        # Access paths/query strings can contain personal text. Never enable access logs here.
        config = uvicorn.Config(self.streamable_http_app(), host=self._public_host, port=self._public_port,
                                log_level="warning", access_log=False)
        try:
            await uvicorn.Server(config).serve()
        finally:
            self._public_pool.close()


def _signature(schema: dict[str, Any]) -> Signature:
    return Signature(parameters=[Parameter(key, kind=Parameter.KEYWORD_ONLY, default=None,
                                         annotation=Annotated[Any, WithJsonSchema(value)])
                                 for key, value in schema["properties"].items()], return_annotation=PublicResult)


def create_public_mcp_server(service: Any, config: PublicConfig) -> PublicFastMCP:
    assert_public_service(service)
    from horosa_skill.surfaces.mcp_server import build_transport_security

    contract = load_public_surface()
    limits = contract["limits"]
    try:
        concurrent = int(os.environ.get("HOROSA_MCP_MAX_CONCURRENT_TOOLS", str(limits["max_concurrent_tools"])))
    except ValueError:
        raise ToolValidationError("公共并发配置无效。 / Invalid public concurrency configuration.", code="public.concurrency_invalid") from None
    if not 1 <= concurrent <= limits["max_concurrent_tools"]:
        raise ToolValidationError("公共并发配置无效。 / Invalid public concurrency configuration.", code="public.concurrency_invalid")
    server = PublicFastMCP("Horosa 星阙", instructions=PUBLIC_INSTRUCTIONS, version=__version__, log_level="WARNING",
                           middleware=[_top_level_security_schemes])
    server._public_host = config.host
    server._public_port = config.port
    server._public_transport_security = build_transport_security(config.host, config.port)
    server._public_handlers = {}
    server._public_ids = {}
    server._public_pool = _PublicWorkPool(concurrent, float(limits["timeout_seconds"]))

    for tool_id, item in public_tools().items():
        schema = public_input_schema(tool_id)

        def factory(selected: str) -> Any:
            async def execute(arguments: dict[str, Any], client_location: Any = None) -> PublicResult:
                checked = validate_public_shape(selected, arguments)
                gate = public_preflight(selected, checked)
                if gate is not None:
                    return gate
                place = resolve_place(selected, checked, client_location)
                if place.error is not None:
                    return place.error
                payload = validate_public_payload(selected, checked, injected=place.injected)

                def calculate() -> PublicResult:
                    try:
                        envelope = service.run_tool(selected, payload, save_result=False)
                        return render_public_result(selected, envelope, place_synthesized=place.synthesized, extra_warnings=place.warnings)
                    except ToolValidationError as exc:
                        return public_error(selected, fields=exc.details.get("fields", []))
                    except Exception:
                        return public_error(selected, "tool.public_failed", must_ask_user=False)

                return await server._public_pool.run(selected, calculate)
            return execute

        execute = factory(tool_id)

        def proxy(handler: Any) -> Any:
            async def advertised(**kwargs: Any) -> PublicResult:
                return await handler({key: value for key, value in kwargs.items() if value is not None}, None)
            return advertised

        advertised = proxy(execute)
        advertised.__signature__ = _signature(schema)
        server.tool(name=item["name"], title=item["title"], description=item["description"],
                    annotations=_READONLY, meta={"securitySchemes": _NOAUTH}, structured_output=True)(advertised)
        server._tool_manager.get_tool(item["name"]).parameters = schema
        server._public_handlers[item["name"]] = execute
        server._public_ids[item["name"]] = tool_id

    guidance = contract["guidance"]
    guidance_schema = {"type": "object", "additionalProperties": False, "required": ["tool_name"],
                       "properties": {"tool_name": {"type": "string", "enum": [item["name"] for item in contract["tools"]],
                                                       "description": "The already selected public calculation tool whose settings need confirmation."}}}

    async def guidance_advertised(**kwargs: Any) -> PublicResult:
        return public_guidance(str(kwargs.get("tool_name") or ""))

    async def guidance_execute(arguments: dict[str, Any], client_location: Any = None) -> PublicResult:
        if not isinstance(arguments, dict) or set(arguments) != {"tool_name"} or not isinstance(arguments.get("tool_name"), str):
            return public_error("guidance", fields=["tool_name"])
        return public_guidance(arguments["tool_name"])

    guidance_advertised.__signature__ = _signature(guidance_schema)
    server.tool(name=guidance["name"], title=guidance["title"], description=guidance["description"],
                annotations=_READONLY, meta={"securitySchemes": _NOAUTH}, structured_output=True)(guidance_advertised)
    server._tool_manager.get_tool(guidance["name"]).parameters = guidance_schema
    server._public_handlers[guidance["name"]] = guidance_execute

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> Response:
        return JSONResponse({"ok": True, "version": __version__}, headers={"Cache-Control": "no-store"})

    @server.custom_route("/.well-known/openai-apps-challenge", methods=["GET"])
    async def challenge(request: Request) -> Response:
        token = os.environ.get(contract["challenge_env"])
        if token is None or token == "":
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        return Response(token.encode("utf-8"), media_type="text/plain", headers={"Cache-Control": "no-store"})

    return server
