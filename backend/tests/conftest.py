import os
import tempfile

# Must be set before the app is imported: the engine is built at import time.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"

import pytest
from fastapi.testclient import TestClient

from app.main import app

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


@pytest.fixture(scope="session")
def client():
    # One lifespan per test session: the MCP session manager can only be started once.
    with TestClient(app, follow_redirects=False) as c:
        yield c


class Mcp:
    """Drive the MCP endpoint the way a bot's connector does."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self._id = 0

    def _rpc(self, method: str, params: dict) -> dict:
        self._id += 1
        resp = self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": self._id, "method": method, "params": params},
            headers=MCP_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def list_tools(self) -> list[str]:
        return [t["name"] for t in self._rpc("tools/list", {})["result"]["tools"]]

    def call(self, name: str, **arguments) -> dict:
        """Returns the tool's structured result, or raises ToolError with the tool's message."""
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})["result"]
        if result.get("isError"):
            raise ToolError(" ".join(c.get("text", "") for c in result["content"]))
        return result["structuredContent"]


class ToolError(Exception):
    pass


@pytest.fixture
def mcp(client):
    return Mcp(client)


def register_args(handle="tomas", **overrides) -> dict:
    args = {
        "handle": handle,
        "display_name": handle.capitalize(),
        "email": f"{handle}@mail.grokbot.com",
        "webhook_url": f"https://api2.cursor.sh/automations/webhook/{handle}-routine",
        "webhook_key": f"crsr_{handle}_secret",
    }
    args.update(overrides)
    return args
