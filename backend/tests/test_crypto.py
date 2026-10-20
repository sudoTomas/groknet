"""Webhook URL and key encrypted at rest (issue #43)."""

import asyncio
import importlib.util
import sqlite3
from pathlib import Path

import pytest
from conftest import register_args
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlmodel import Session, delete

from app import crypto, webhooks
from app.db import encrypt_plaintext_webhooks, engine, init_db
from app.models import User

ROTATE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rotate_encryption_key.py"


def raw_columns(handle: str) -> tuple[str, str]:
    """Read the stored values with plain SQL, bypassing the EncryptedStr decorator."""
    with engine.connect() as con:
        row = con.execute(
            text('SELECT webhook_url, webhook_key FROM "user" WHERE handle = :h'), {"h": handle}
        ).one()
    return row[0], row[1]


def load_rotate_script():
    spec = importlib.util.spec_from_file_location("rotate_encryption_key", ROTATE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_user_table(path: Path, rows: list[tuple[str, str, str]]) -> None:
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE "user" (handle TEXT, webhook_url TEXT, webhook_key TEXT)')
        con.executemany('INSERT INTO "user" VALUES (?, ?, ?)', rows)


@pytest.fixture(autouse=True)
def _clean_users(client):
    yield
    with Session(engine) as session:
        session.exec(delete(User).where(User.handle.startswith("crypt_")))
        session.commit()


def test_round_trip_and_prefix() -> None:
    token = crypto.encrypt("crsr_secret")
    assert token.startswith("enc:v1:")
    assert "crsr_secret" not in token
    assert crypto.decrypt(token) == "crsr_secret"
    assert crypto.encrypt("same") != crypto.encrypt("same")  # Fernet is randomized


def test_registered_values_are_encrypted_in_the_raw_column(client) -> None:
    resp = client.post("/api/register", json=register_args("crypt_new"))
    assert resp.status_code == 201, resp.text
    url, key = raw_columns("crypt_new")
    assert url.startswith("enc:v1:") and key.startswith("enc:v1:")
    assert "crsr_crypt_new_secret" not in key and "127.0.0.1" not in url
    with Session(engine) as session:
        user = session.get(User, "crypt_new")
        assert user.webhook_key == "crsr_crypt_new_secret"
        assert user.webhook_url.startswith("http://127.0.0.1:9/")


def test_plaintext_row_from_old_schema_is_encrypted_at_startup(fake_inbox) -> None:
    with engine.begin() as con:
        con.execute(
            text(
                'INSERT INTO "user" (handle, display_name, profile, email, groknet_token,'
                " webhook_url, webhook_key, created_at) VALUES (:h, 'Legacy', '{}',"
                " 'crypt_legacy@mail.grokbot.com', 'legacy-token', :u, 'crsr_legacy_key',"
                " '2026-01-01 00:00:00')"
            ),
            {"h": "crypt_legacy", "u": fake_inbox.url},
        )

    init_db()

    url, key = raw_columns("crypt_legacy")
    assert key.startswith("enc:v1:") and "crsr_legacy_key" not in key
    assert url.startswith("enc:v1:") and fake_inbox.url not in url
    with Session(engine) as session:
        user = session.get(User, "crypt_legacy")
        assert user.webhook_key == "crsr_legacy_key"
        assert user.webhook_url == fake_inbox.url
        assert asyncio.run(webhooks.deliver(user, event_type="message", sender=user, payload={}))
    assert fake_inbox.keys == ["Bearer crsr_legacy_key"]

    # Idempotent: a second boot leaves the already-encrypted values alone.
    assert encrypt_plaintext_webhooks(engine) == 0
    assert raw_columns("crypt_legacy") == (url, key)


def test_migration_encrypts_only_the_column_that_lacks_the_prefix(tmp_path) -> None:
    path = tmp_path / "half.db"
    enc_url = crypto.encrypt("https://example.test/hook")
    make_user_table(path, [("half", enc_url, "crsr_half")])

    assert encrypt_plaintext_webhooks(create_engine(f"sqlite:///{path}")) == 1

    with sqlite3.connect(path) as con:
        url, key = con.execute('SELECT webhook_url, webhook_key FROM "user"').fetchone()
    assert url == enc_url
    assert key.startswith("enc:v1:") and crypto.decrypt(key) == "crsr_half"


def test_delivery_carries_the_plain_bearer(client, fake_inbox) -> None:
    resp = client.post("/api/register", json=register_args("crypt_d", webhook_url=fake_inbox.url))
    assert resp.status_code == 201, resp.text
    resp = client.post("/api/auth/start", json={"email": "crypt_d@mail.grokbot.com"})
    assert resp.status_code == 200, resp.text
    assert fake_inbox.keys == ["Bearer crsr_crypt_d_secret"]


def test_whoami_returns_the_plain_url_and_never_the_key(mcp) -> None:
    url = "https://api2.cursor.sh/automations/webhook/crypt-w"
    token = mcp.call("register", **register_args("crypt_w", webhook_url=url))["groknet_token"]
    me = mcp.call("whoami", groknet_token=token)
    assert me["webhook_url"] == url
    assert "webhook_key" not in me and "crsr_crypt_w_secret" not in str(me)


def test_missing_key_outside_dev_and_test_refuses_to_start() -> None:
    with pytest.raises(RuntimeError, match="GROKNET_ENCRYPTION_KEY"):
        crypto.build_fernet("", dev=False, testing=False)


@pytest.mark.parametrize(
    "flags", [{"dev": True, "testing": False}, {"dev": False, "testing": True}]
)
def test_missing_key_in_dev_or_test_generates_one_with_a_warning(caplog, flags) -> None:
    f = crypto.build_fernet("", **flags)
    assert f.decrypt(f.encrypt(b"x")) == b"x"
    assert "GROKNET_ENCRYPTION_KEY" in caplog.text


def test_configured_key_is_used() -> None:
    key = Fernet.generate_key().decode()
    f = crypto.build_fernet(key, dev=False, testing=False)
    assert Fernet(key).decrypt(f.encrypt(b"hello")) == b"hello"


def test_malformed_key_is_rejected_with_a_clear_message() -> None:
    with pytest.raises(RuntimeError, match="GROKNET_ENCRYPTION_KEY"):
        crypto.build_fernet("not-a-fernet-key", dev=False, testing=False)


def test_rotation_script_re_encrypts_both_columns(tmp_path, monkeypatch, capsys) -> None:
    old_key, new_key = Fernet.generate_key(), Fernet.generate_key()
    old = Fernet(old_key)
    path = tmp_path / "rotate.db"
    make_user_table(
        path,
        [
            (
                "r1",
                "enc:v1:" + old.encrypt(b"https://x.test/1").decode(),
                "enc:v1:" + old.encrypt(b"crsr_r1").decode(),
            ),
            ("r2", "https://x.test/2", "crsr_r2"),  # never migrated: encrypted with NEW
        ],
    )
    monkeypatch.setenv("OLD_GROKNET_ENCRYPTION_KEY", old_key.decode())
    monkeypatch.setenv("NEW_GROKNET_ENCRYPTION_KEY", new_key.decode())

    assert load_rotate_script().main([str(path)]) == 0
    assert "re-encrypted 2 user row(s)" in capsys.readouterr().out

    new = Fernet(new_key)
    with sqlite3.connect(path) as con:
        rows = con.execute('SELECT handle, webhook_url, webhook_key FROM "user"').fetchall()
    got = {}
    for handle, url, key in rows:
        assert url.startswith("enc:v1:") and key.startswith("enc:v1:")
        got[handle] = (new.decrypt(url[7:].encode()), new.decrypt(key[7:].encode()))
    assert got == {"r1": (b"https://x.test/1", b"crsr_r1"), "r2": (b"https://x.test/2", b"crsr_r2")}


def test_rotation_script_changes_nothing_when_old_key_is_wrong(tmp_path, monkeypatch) -> None:
    enc = "enc:v1:" + Fernet(Fernet.generate_key()).encrypt(b"crsr_x").decode()
    path = tmp_path / "wrong.db"
    make_user_table(path, [("ok", "https://x.test/ok", "crsr_ok"), ("x", enc, enc)])
    monkeypatch.setenv("OLD_GROKNET_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("NEW_GROKNET_ENCRYPTION_KEY", Fernet.generate_key().decode())

    assert load_rotate_script().main([str(path)]) != 0

    with sqlite3.connect(path) as con:
        rows = con.execute('SELECT handle, webhook_url, webhook_key FROM "user"').fetchall()
    assert sorted(rows) == [("ok", "https://x.test/ok", "crsr_ok"), ("x", enc, enc)]
