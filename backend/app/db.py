import logging
from collections.abc import Iterator

from sqlalchemy import Engine, text
from sqlmodel import Session, SQLModel, create_engine

from app import crypto
from app.config import DATABASE_URL

log = logging.getLogger("groknet.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    add_missing_columns(engine)
    encrypt_plaintext_webhooks(engine)


def add_missing_columns(target: Engine) -> None:
    """Poor man's migration for SQLite: add columns the models have and the tables lack.

    create_all never alters existing tables, and a hackathon adds columns daily.
    Only additive changes are handled; renames and drops need a manual step.
    """
    if not target.dialect.name.startswith("sqlite"):
        return
    with target.begin() as con:
        for table in SQLModel.metadata.sorted_tables:
            existing = {row[1] for row in con.execute(text(f'PRAGMA table_info("{table.name}")'))}
            if not existing:
                continue
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(target.dialect)}'
                if col.default is not None and col.default.is_scalar:
                    ddl += f" DEFAULT '{col.default.arg}'"
                elif col.type.python_type is dict:
                    ddl += " DEFAULT '{}'"
                con.execute(text(ddl))


ENCRYPTED_USER_COLUMNS = ("webhook_url", "webhook_key")


def encrypt_plaintext_webhooks(target: Engine) -> int:
    """Encrypt in place every webhook_url / webhook_key that predates encryption (issue #43).

    Raw SQL on purpose: through the model, EncryptedStr would hand back plaintext and
    ciphertext alike as plain strings. Idempotent; returns the number of rows changed.
    """
    p = crypto.PREFIX
    url, key = ENCRYPTED_USER_COLUMNS
    lacks = f"substr({{c}}, 1, {len(p)}) != '{p}'"
    with target.begin() as con:
        rows = con.execute(
            text(
                f'SELECT rowid, {url}, {key} FROM "user"'
                f" WHERE {lacks.format(c=url)} OR {lacks.format(c=key)}"
            )
        ).all()
        for rowid, url_value, key_value in rows:
            con.execute(
                text(f'UPDATE "user" SET {url} = :u, {key} = :k WHERE rowid = :r'),
                {
                    "u": url_value if url_value.startswith(p) else crypto.encrypt(url_value),
                    "k": key_value if key_value.startswith(p) else crypto.encrypt(key_value),
                    "r": rowid,
                },
            )
    if rows:
        log.warning("Encrypted the webhook credentials of %d plaintext user row(s).", len(rows))
    return len(rows)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
