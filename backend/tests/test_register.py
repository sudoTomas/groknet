import json

import pytest
from conftest import ToolError, register_args


def test_register_returns_a_token_and_lists_the_user(mcp, client) -> None:
    result = mcp.call("register", **register_args("alejandro"))

    assert result["groknet_token"]
    assert result["handle"] == "alejandro"
    users = {u["handle"]: u for u in client.get("/api/users").json()}
    assert users["alejandro"]["display_name"] == "Alejandro"


def test_register_rejects_emails_outside_mail_grokbot_com(mcp, client) -> None:
    with pytest.raises(ToolError, match="@mail.grokbot.com"):
        mcp.call("register", **register_args("intruder", email="intruder@gmail.com"))

    assert "intruder" not in {u["handle"] for u in client.get("/api/users").json()}


@pytest.mark.parametrize("bad", ["Tomas", "t", "to mas", "tomas!", "a" * 33])
def test_register_rejects_handles_that_are_not_lowercase_slugs(mcp, bad) -> None:
    with pytest.raises(ToolError, match="handle"):
        mcp.call("register", **register_args(bad, email="ok@mail.grokbot.com"))


def test_whoami_with_unknown_token_is_rejected(mcp) -> None:
    with pytest.raises(ToolError, match="token"):
        mcp.call("whoami", groknet_token="not-a-real-token")


def test_reregistering_a_handle_replaces_the_inbox_and_rotates_the_token(mcp) -> None:
    first = mcp.call("register", **register_args("juan"))
    second = mcp.call(
        "register",
        **register_args("juan", webhook_url="https://api2.cursor.sh/automations/webhook/juan-v2"),
    )

    assert second["groknet_token"] != first["groknet_token"]
    me = mcp.call("whoami", groknet_token=second["groknet_token"])
    assert me["webhook_url"] == "https://api2.cursor.sh/automations/webhook/juan-v2"
    with pytest.raises(ToolError, match="token"):
        mcp.call("whoami", groknet_token=first["groknet_token"])


def test_reregistering_a_handle_with_another_email_is_rejected(mcp) -> None:
    mcp.call("register", **register_args("marcela"))
    with pytest.raises(ToolError, match="email"):
        mcp.call("register", **register_args("marcela", email="other@mail.grokbot.com"))


def test_read_api_never_exposes_credentials(mcp, client) -> None:
    mcp.call("register", **register_args("secretive"))

    body = client.get("/api/users").text
    assert "crsr_secretive_secret" not in body
    assert "automations/webhook" not in body
    assert "groknet_token" not in body


def test_registration_is_announced_on_the_event_bus(mcp) -> None:
    from app.events import bus

    inbox = bus.subscribe()
    try:
        mcp.call("register", **register_args("announcer"))
        event = json.loads(inbox.get_nowait())
    finally:
        bus.unsubscribe(inbox)

    assert event["type"] == "user_registered"
    assert event["handle"] == "announcer"
    assert "groknet_token" not in event and "webhook_key" not in event
