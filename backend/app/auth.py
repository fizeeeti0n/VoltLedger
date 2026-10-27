"""Password hashing (PBKDF2-SHA256) and JWT bearer tokens."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db

SECRET = os.environ.get("VOLT_SECRET") or secrets.token_urlsafe(48)
if not os.environ.get("VOLT_SECRET"):
    print("WARNING: VOLT_SECRET not set; using a random key. Logins reset on every restart. Set VOLT_SECRET in production.")
TOKEN_TTL = 60 * 60 * 24 * 14
ITERATIONS = 240_000
_bearer = HTTPBearer(auto_error=False)


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITERATIONS)
    return f"pbkdf2${ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, dk = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(calc.hex(), dk)
    except Exception:
        return False


def make_token(uid: int) -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(uid), "iat": now, "exp": now + TOKEN_TTL}, SECRET, algorithm="HS256")


def current_user(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    if not creds:
        raise HTTPException(401, "Sign in to continue.")
    try:
        payload = jwt.decode(creds.credentials, SECRET, algorithms=["HS256"])
        user = db.get_user(int(payload["sub"]))
    except Exception:
        raise HTTPException(401, "Session expired. Please sign in again.")
    if not user:
        raise HTTPException(401, "Account not found.")
    return dict(user)
