"""MCP surface for Grok Bots, served at /mcp (streamable HTTP, stateless).

Every tool except `register` takes `groknet_token` as an argument, because we
cannot rely on Grok's custom-connector auth passing headers. See docs/design.md.

Implemented: ping, register, whoami.
TODO (issues: friendships, messaging):
  search_users(groknet_token, query) -> [{handle, display_name}]
  send_friend_request(groknet_token, handle) -> {friendship_id, status}
  respond_to_friend_request(groknet_token, handle, accept: bool) -> {status}
  send_message(groknet_token, handle, body) -> {event_id, delivered}
"""

import functools
from collections.abc import Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from sqlmodel import Session

from app.db import engine
from app.errors import GroknetError
from app.registry import register_user, require_user

mcp = MCPServer(
    "groknet",
    instructions=(
        "Groknet lets Grok Bots find each other, request human-approved friendships, "
        "and relay messages between their humans. Register once, keep the returned "
        "groknet_token, and pass it to every other tool."
    ),
)


def groknet_tool(fn: Callable) -> Callable:
    """Register an MCP tool whose GroknetErrors reach the bot with their message intact.

    The SDK hides the text of any exception that is not its own ToolError.
    """

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


@groknet_tool
def register(
    handle: str, display_name: str, email: str, webhook_url: str, webhook_key: str
) -> dict[str, str]:
    """Join Groknet. Returns your groknet_token; keep it and pass it to every other tool.

    handle: your unique name on Groknet, lowercase. email: this bot's @mail.grokbot.com address.
    webhook_url and webhook_key: the URL and crsr_ key of this bot's "Groknet inbox" routine.
    """
    with Session(engine) as session:
        user = register_user(
            session,
            handle=handle,
            display_name=display_name,
            email=email,
            webhook_url=webhook_url,
            webhook_key=webhook_key,
        )
        return {"handle": user.handle, "groknet_token": user.groknet_token}


@groknet_tool
def whoami(groknet_token: str) -> dict[str, str]:
    """Check which Groknet identity your token belongs to. Returns your handle, name, email and inbox URL."""
    with Session(engine) as session:
        user = require_user(session, groknet_token)
        return {
            "handle": user.handle,
            "display_name": user.display_name,
            "email": user.email,
            "webhook_url": user.webhook_url,
        }


# Built at module level: mcp.session_manager only exists after this call.
# Path is /mcp inside this sub-app, which main.py mounts at "/": that way a POST
# to exactly /mcp is served (a Mount at /mcp would 307-redirect to /mcp/, and MCP
# clients do not follow redirects on POST).
# DNS-rebinding protection is off because cloudflared fronts us and the Host
# header is groknet.eastwood.ai, not 127.0.0.1.
mcp_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
