"""Horosa 星阙 public MCP surface (anonymous, read-only, stateless).

This package depends on the released ``horosa-skill`` wheel and never modifies it: public mode is
layered on from outside with a service subclass and inert stand-ins for the memory store, the runtime
manager and the trace recorder (see ``horosa_public.service``).
"""

__version__ = "0.1.6"
