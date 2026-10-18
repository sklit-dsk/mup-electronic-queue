import hashlib
import hmac
import json

from cryptography.fernet import Fernet
from fastapi import HTTPException

from .config import settings


def secret(name):
    cfg = settings()
    val = getattr(cfg, name)
    if val:
        return val
    if cfg.app_env == "pilot":
        raise RuntimeError(f"Missing {name}")
    path = cfg.data_dir / f".{name}"
    if not path.exists():
        try:
            with path.open("x") as stream:
                stream.write(Fernet.generate_key().decode())
            path.chmod(0o600)
        except FileExistsError:
            pass
    return path.read_text().strip()


def cipher():
    return Fernet(secret("encryption_key").encode())


def encrypt(value):
    return cipher().encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()


def decrypt(value):
    return json.loads(cipher().decrypt(value.encode()))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def keyed(value):
    return hmac.new(secret("fingerprint_key").encode(), value.encode(), hashlib.sha256).hexdigest()


def fingerprint(fields):
    import re

    country = fields["country"].upper().strip()
    number = re.sub(r"[\s-]", "", fields["number"].upper())
    if not re.fullmatch(r"[A-Z]{3}", country) or not re.fullmatch(r"[A-Z0-9]{4,20}", number):
        raise HTTPException(422, "DOCUMENT_FORMAT")
    return keyed(json.dumps([country, "passport", number], separators=(",", ":")))


def fail(code, status=409):
    raise HTTPException(status, code)
