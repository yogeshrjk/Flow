"""Anonymous browser ownership for a private single-server app (not account auth)."""
import hashlib
import hmac
import os
import secrets
from pathlib import Path
from app.config import settings

_key = None


def signing_key():
    global _key
    if _key is None:
        path = Path(settings.data_dir) / ".identity-key"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(secrets.token_bytes(32))
        except FileExistsError:
            pass
        _key = path.read_bytes()
    return _key


def issue_identity():
    uid = secrets.token_hex(16)
    return uid + "." + hmac.new(signing_key(), uid.encode(), hashlib.sha256).hexdigest()


def read_identity(cookie):
    if not cookie or len(cookie) != 97:
        return None
    uid, _, signature = cookie.partition(".")
    if any(c not in "0123456789abcdef" for c in uid):
        return None
    expected = hmac.new(signing_key(), uid.encode(), hashlib.sha256).hexdigest()
    return uid if hmac.compare_digest(expected, signature) else None
