import base64
import hashlib
import os
from cryptography.fernet import Fernet, InvalidToken

def _fernet() -> Fernet:
    secret = os.getenv("APP_SECRET", "")
    if not secret or secret == "dev-only-change-me":
        raise RuntimeError("APP_SECRET 未正确配置")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)

def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")

def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
