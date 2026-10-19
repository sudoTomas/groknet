from conftest import MCP_HEADERS


def test_health(client) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_read_endpoints_start_empty(client) -> None:
    for path in ("/api/users", "/api/friendships", "/api/messages", "/api/teams"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.json() == []


def test_mcp_lists_ping_tool(client) -> None:
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }
    assert client.post("/mcp", json=init, headers=MCP_HEADERS).status_code == 200
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers=MCP_HEADERS,
    )
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()["result"]["tools"]]
    assert "ping" in names
