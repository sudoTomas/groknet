from conftest import MCP_HEADERS


def test_onboarding_endpoint_is_plain_text(client) -> None:
    resp = client.get("/api/onboarding")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "Groknet inbox" in resp.text
    assert "register" in resp.text


def test_onboard_tool_and_instructions(client) -> None:
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
    resp = client.post("/mcp", json=init, headers=MCP_HEADERS)
    assert "onboard" in resp.json()["result"]["instructions"]
    resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "onboard", "arguments": {}},
        },
        headers=MCP_HEADERS,
    )
    assert resp.status_code == 200
    assert "Groknet inbox" in str(resp.json()["result"])
