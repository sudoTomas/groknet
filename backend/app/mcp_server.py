"""MCP server instance and the `groknet_tool` decorator.

Tools live in app/tools/<feature>.py and register themselves by importing `mcp`
from here. app/tools/__init__.py imports every feature module; main.py imports
that package and builds the ASGI app. Every tool except `register` takes
`groknet_token` as an argument, because we cannot rely on Grok's custom-connector
auth passing headers. See docs/design.md.
"""

import functools
import inspect
from collections.abc import Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.errors import GroknetError

mcp = MCPServer(
    "groknet",
    instructions=(
        "Groknet lets Grok Bots find each other, request human-approved friendships, "
        "and relay messages between their humans. Register once, keep the returned "
        "groknet_token, and pass it to every other tool. If you are not registered yet, "
        "call the onboard tool first and follow its steps, confirming with your human "
        "before you register or share any of their details."
    ),
)


def groknet_tool(fn: Callable) -> Callable:
    """Register an MCP tool (sync or async) whose GroknetErrors reach the bot with their message intact.

    The SDK hides the text of any exception that is not its own ToolError. Async tools get an
    async wrapper: a sync one would hand the SDK an unawaited coroutine as the tool's result.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except GroknetError as e:
                raise ToolError(str(e)) from e

        return mcp.tool()(async_wrapper)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except GroknetError as e:
            raise ToolError(str(e)) from e

    return mcp.tool()(wrapper)


@mcp.tool()
def ping() -> dict[str, str]:
    """Check that Groknet is reachable."""
    return {"status": "ok", "service": "groknet"}
