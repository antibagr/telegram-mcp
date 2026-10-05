"""Fork divergence from upstream chigwell/telegram-mcp: tool failures raise.

Upstream returns failures as plain strings, which reports the call as a success.
This fork raises ``ToolError`` from ``log_and_format_error`` (ec8c372), so the
server answers ``isError: true``, and the message carries the real exception
(13aec40) instead of a hashed ``An error occurred (code: ...)``. Persistent logs
stay redacted as upstream requires.

Upstream tests that assert on a returned error string call these helpers instead,
which also assert that the failure was raised as a tool execution error.
"""

import pytest
from mcp.server.mcpserver.exceptions import ToolError


async def tool_error_text(awaitable) -> str:
    """Await a tool call that must fail; return the ToolError message."""
    with pytest.raises(ToolError) as excinfo:
        await awaitable
    return str(excinfo.value)


def sync_tool_error_text(func, *args, **kwargs) -> str:
    """Call ``func`` (e.g. log_and_format_error), which must raise; return the message."""
    with pytest.raises(ToolError) as excinfo:
        func(*args, **kwargs)
    return str(excinfo.value)
