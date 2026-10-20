"""Encryption at rest for the bot inbox credentials (issue #43).

User.webhook_url and User.webhook_key are stored as "enc:v1:" + a Fernet token
(AES-128-CBC + HMAC-SHA256) keyed by GROKNET_ENCRYPTION_KEY. The EncryptedStr column
type does it transparently, so the rest of the code reads and writes plain strings.

A value without the prefix is a plaintext row from before this change; it reads back
as-is until init_db's migration encrypts it. scripts/rotate_encryption_key.py uses the
same prefix and must stay in step with it.
"""

import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from app.config import DEV_MODE

PREFIX = "enc:v1:"

log = logging.getLogger("groknet.crypto")


def build_fernet(key: str, *, dev: bool, testing: bool) -> Fernet:
    """Production (neither dev nor tests) refuses to run without a key."""
    if key:
        try:
            return Fernet(key.encode())
        except ValueError as exc:
            raise RuntimeError(
                "GROKNET_ENCRYPTION_KEY must be a urlsafe base64 32-byte key, as printed by "
                'python3 -c "from cryptography.fernet import Fernet;'
                'print(Fernet.generate_key().decode())"'
            ) from exc
    if not (dev or testing):
        raise RuntimeError(
            "GROKNET_ENCRYPTION_KEY is not set. It encrypts bot webhook URLs and keys at rest; "
            "set it in .env (see infra/README.md). Refusing to start."
        )
    log.warning(
        "GROKNET_ENCRYPTION_KEY is not set; using a random key for this process. "
        "Webhook credentials stored now are unreadable after a restart."
    )
    return Fernet(Fernet.generate_key())


def _testing() -> bool:
    return os.environ.get("GROKNET_TESTING") == "1" or "PYTEST_CURRENT_TEST" in os.environ


_fernet = build_fernet(
    os.environ.get("GROKNET_ENCRYPTION_KEY", ""), dev=DEV_MODE, testing=_testing()
)


def encrypt(value: str) -> str:
    return PREFIX + _fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value.startswith(PREFIX):
        raise ValueError("not an encrypted value (missing the enc:v1: prefix)")
    try:
        return _fernet.decrypt(value[len(PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "Cannot decrypt a stored webhook credential: GROKNET_ENCRYPTION_KEY is not the key "
            "it was encrypted with. Restore the old key, or have every bot re-register."
        ) from exc


class EncryptedStr(TypeDecorator):
    """A string column stored encrypted. Plaintext legacy values read back unchanged."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        return None if value is None else encrypt(value)

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None or not value.startswith(PREFIX):
            return value
        return decrypt(value)
