"""PII protection.

* Phones are stored as a keyed HMAC-SHA256 ("blind index") so events can be joined to leads
  by equality without the plaintext number ever being in the database.
* The full PII values (name, phone) are stored Fernet-encrypted (AES-128-CBC + HMAC) so they
  are recoverable by someone holding the key, e.g. to actually place a call.
* Only the last 4 digits of the phone are kept in clear, which the masked report needs.

The secret comes from LEDGER_SECRET, or a key file created next to the database on first use.
It must never be committed (see .gitignore).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet


class Vault:
    def __init__(self, secret: bytes):
        self._hmac_key = hashlib.sha256(b"ledger-hmac:" + secret).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"ledger-enc:" + secret).digest()))

    @classmethod
    def for_db(cls, db_path: Path) -> "Vault":
        env = os.environ.get("LEDGER_SECRET")
        if env:
            return cls(env.encode())
        key_file = Path(str(db_path) + ".key")
        if not key_file.exists():
            key_file.write_text(secrets.token_hex(32))
            key_file.chmod(0o600)
        return cls(key_file.read_text().strip().encode())

    def blind_index(self, canonical_phone: str) -> str:
        return hmac.new(self._hmac_key, canonical_phone.encode(), hashlib.sha256).hexdigest()

    def encrypt(self, obj) -> bytes:
        return self._fernet.encrypt(json.dumps(obj, sort_keys=True).encode())

    def decrypt(self, token: bytes):
        return json.loads(self._fernet.decrypt(token))
