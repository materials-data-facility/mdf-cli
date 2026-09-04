"""MDF Model Context Protocol server (read-only).

Exposes MDF's public v2 API as MCP tools so AI assistants can search, inspect
and cite datasets. Run it with ``mdf mcp serve``; print a client config snippet
with ``mdf mcp install``.

Importing this package does not require the ``mcp`` SDK — only
:func:`~mdf.mcp.server.create_server` and :func:`~mdf.mcp.server.run_server` do
(``pip install "mdf-cli[mcp]"``).
"""

from mdf.mcp.server import (
    MCP_INSTALL_HINT,
    SERVER_INSTRUCTIONS,
    SERVER_NAME,
    TOOL_NAMES,
    TOOL_SPECS,
    TOOLS,
    configure,
    create_server,
    run_server,
)

__all__ = [
    "MCP_INSTALL_HINT",
    "SERVER_INSTRUCTIONS",
    "SERVER_NAME",
    "TOOLS",
    "TOOL_NAMES",
    "TOOL_SPECS",
    "configure",
    "create_server",
    "run_server",
]
