import os
import tempfile

# Must be set before the app is imported: the engine is built at import time.
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
# Tests run without GROKNET_ENCRYPTION_KEY: app.crypto generates a random key (issue #43).
os.environ["GROKNET_TESTING"] = "1"

import pytest
from fastapi.testclient import TestClient

from app.main import app

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


READER_HANDLE = "test_observer"


def authenticate_reader(client: TestClient) -> None:
    """Reads require an actor. Register (or re-register) the observer and make the
    shared client send its bearer by default. Call again after anything that wipes users."""
    resp = client.post("/api/register", json=register_args(READER_HANDLE, display_name="Observer"))
    assert resp.status_code in (200, 201), resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['groknet_token']}"


NO_AUTH = {"Authorization": ""}  # per-request override that drops the default bearer


@pytest.fixture(autouse=True)
def reader_auth(client):
    authenticate_reader(client)


@pytest.fixture(autouse=True)
def no_retry_backoff(monkeypatch):
    """Deliveries to dead inboxes still retry, just without the 1+3+9 s sleeps."""
    import app.webhooks

    monkeypatch.setattr(app.webhooks, "RETRY_BACKOFF_S", (0, 0, 0))


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

    def call(self, tool_name: str, **arguments) -> dict:
        """Returns the tool's structured result, or raises ToolError with the tool's message."""
        result = self._rpc("tools/call", {"name": tool_name, "arguments": arguments})["result"]
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
        # A closed local port, so a delivery nobody wired an inbox for fails fast and offline.
        "webhook_url": f"http://127.0.0.1:9/automations/webhook/{handle}-routine",
        "webhook_key": f"crsr_{handle}_secret",
    }
    args.update(overrides)
    return args


class FakeInbox:
    """A stand-in for a Grok Bot's webhook routine, running on a local port.

    Records every POST it receives (`inbox.events`), with the bearer key seen
    (`inbox.keys`). `inbox.respond_with` is a queue of status codes to return before
    falling back to 200 + {"success": true}, so tests can script 503-then-200 etc.
    """

    def __init__(self) -> None:
        import json as _json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        inbox = self
        self.events: list[dict] = []
        self.keys: list[str] = []
        self.respond_with: list[int] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                inbox.keys.append(self.headers.get("Authorization", ""))
                status = inbox.respond_with.pop(0) if inbox.respond_with else 200
                if status == 200:
                    inbox.events.append(_json.loads(body))
                    payload = _json.dumps(
                        {"success": True, "runUuid": "run-" + str(len(inbox.events))}
                    )
                elif status == 400:
                    payload = _json.dumps({"success": False, "error": "Automation is disabled"})
                elif status == 401:
                    payload = _json.dumps({"code": "error", "message": "Invalid API key"})
                else:
                    payload = _json.dumps({"error": status})
                data = payload.encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/automations/webhook/fake"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def fake_inbox():
    """One fake bot inbox. Use `register_args(handle, webhook_url=inbox.url)` to point a user at it."""
    inbox = FakeInbox()
    yield inbox
    inbox.close()


@pytest.fixture
def make_inbox():
    """Factory when a test needs several bots, each with its own inbox."""
    inboxes: list[FakeInbox] = []

    def _make() -> FakeInbox:
        inbox = FakeInbox()
        inboxes.append(inbox)
        return inbox

    yield _make
    for inbox in inboxes:
        inbox.close()
