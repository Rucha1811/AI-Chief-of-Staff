import base64
import hashlib
import hmac
import json
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken

_PBKDF2_ITERATIONS = 120_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, _PBKDF2_ITERATIONS
    )
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def _sign(secret: str, payload: str) -> str:
    return f"{payload}.{hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()}"


def create_token(secret: str, ttl_seconds: int = 7 * 24 * 3600) -> str:
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + ttl_seconds}).encode())
        .decode()
        .rstrip("=")
    )
    return _sign(secret, payload)


def verify_token(token: str, secret: str) -> bool:
    try:
        payload, signature = token.rsplit(".", 1)
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        return data["exp"] > time.time()
    except Exception:
        return False


def _fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def encrypt_bytes(data: bytes, secret: str) -> bytes:
    return _fernet(secret).encrypt(data)


def decrypt_bytes(token: bytes, secret: str) -> bytes:
    try:
        return _fernet(secret).decrypt(token)
    except InvalidToken:
        raise ValueError("Invalid encrypted data (wrong SECRET_KEY?)")
