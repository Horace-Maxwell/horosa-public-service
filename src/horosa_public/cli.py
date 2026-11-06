"""``horosa-public`` command line: ``serve`` the anonymous public MCP, or ``check`` the environment."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence

from horosa_skill.errors import ToolValidationError

from horosa_public import __version__
from horosa_public.config import PublicConfig
from horosa_public.server import create_public_mcp_server
from horosa_public.service import PublicHorosaService

STARTUP_NOTICE = "Horosa public MCP: anonymous, stateless Streamable HTTP /mcp; request logging disabled."


def _error(exc: ToolValidationError) -> int:
    # Codes and field names only; never values.
    print(json.dumps({"ok": False, "code": exc.code, "details": exc.details}, ensure_ascii=False), file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="horosa-public", description="Horosa 星阙 public MCP (anonymous, read-only, stateless).")
    parser.add_argument("--version", action="version", version=f"horosa-public {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Serve the public allowlist over Streamable HTTP at /mcp.")
    serve.add_argument("--host", help="Bind address: loopback (default 127.0.0.1) or 0.0.0.0 inside a container.")
    serve.add_argument("--port", type=int, help="Bind port (default 8765 or HOROSA_SKILL_PORT).")
    commands.add_parser("check", help="Validate the environment and print a redacted summary; starts nothing.")
    args = parser.parse_args(argv)
    try:
        config = PublicConfig.from_env(host=getattr(args, "host", None), port=getattr(args, "port", None))
    except ToolValidationError as exc:
        return _error(exc)
    if args.command == "check":
        print(json.dumps(config.summary(), ensure_ascii=False))
        return 0
    try:
        service = PublicHorosaService(config)
        server = create_public_mcp_server(service, config)
    except ToolValidationError as exc:
        return _error(exc)
    print(STARTUP_NOTICE, file=sys.stderr)
    # The public process emits no SDK, HTTP or engine logs: log lines can carry request values.
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        server.run(transport="streamable-http")
    finally:
        logging.disable(previous)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
