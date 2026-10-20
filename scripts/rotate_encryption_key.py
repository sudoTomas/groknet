"""Re-encrypt every bot's webhook_url and webhook_key with a new GROKNET_ENCRYPTION_KEY.

Usage (stop the API first, it holds the old key):
  OLD_GROKNET_ENCRYPTION_KEY=... NEW_GROKNET_ENCRYPTION_KEY=... \
    python3 rotate_encryption_key.py /path/to/groknet.db

Values without the "enc:v1:" prefix (rows never migrated) are encrypted with NEW too.
Everything is decrypted with OLD before anything is written; if one value fails, nothing
changes. Stdlib + cryptography only, so it runs inside the api container.
See infra/README.md, "Encryption key".
"""

import os
import sqlite3
import sys

from cryptography.fernet import Fernet, InvalidToken

PREFIX = "enc:v1:"  # must match backend/app/crypto.py
COLUMNS = ("webhook_url", "webhook_key")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        old = Fernet(os.environ["OLD_GROKNET_ENCRYPTION_KEY"].encode())
        new = Fernet(os.environ["NEW_GROKNET_ENCRYPTION_KEY"].encode())
    except KeyError as exc:
        print(f"{exc.args[0]} is not set", file=sys.stderr)
        return 2
    except ValueError:
        print("OLD/NEW key is not a valid Fernet key", file=sys.stderr)
        return 2

    con = sqlite3.connect(argv[0])
    try:
        rows = con.execute(f'SELECT rowid, {", ".join(COLUMNS)} FROM "user"').fetchall()
        updates, plaintext = [], 0
        for rowid, *values in rows:
            plain = []
            for value in values:
                if value.startswith(PREFIX):
                    plain.append(old.decrypt(value[len(PREFIX) :].encode()).decode())
                else:
                    plain.append(value)
                    plaintext += 1
            updates.append([PREFIX + new.encrypt(v.encode()).decode() for v in plain] + [rowid])
    except InvalidToken:
        print("A stored value does not decrypt with OLD; nothing was changed.", file=sys.stderr)
        con.close()
        return 1

    with con:
        con.executemany(
            f'UPDATE "user" SET {", ".join(f"{c} = ?" for c in COLUMNS)} WHERE rowid = ?',
            updates,
        )
    con.close()
    print(
        f"re-encrypted {len(updates)} user row(s) ({len(updates) * len(COLUMNS)} values, "
        f"{plaintext} of them were plaintext)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
