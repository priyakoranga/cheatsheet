import hashlib
import hmac
import os
import secrets

from fastapi import HTTPException, Request


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32
        )
        return hmac.compare_digest(digest, bytes.fromhex(digest_hex))
    except Exception:
        return False


def csrf_token(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_hex(16)
    return request.session["csrf"]


def check_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf", "")
    if not expected or not hmac.compare_digest(expected, token or ""):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
