import base64
import hashlib
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _int_to_bytes(value: int) -> bytes:
    return value.to_bytes((value.bit_length() + 7) // 8 or 1, "big")


def jwk(key) -> dict:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "n": b64url(_int_to_bytes(numbers.n)),
        "e": b64url(_int_to_bytes(numbers.e)),
    }


def thumbprint(key) -> str:
    jwk_dict = jwk(key)
    canonical = json.dumps(jwk_dict, separators=(",", ":"), sort_keys=True).encode()
    return b64url(hashlib.sha256(canonical).digest())


def sign(key, protected: dict, payload) -> dict:
    protected_b64 = b64url(json.dumps(protected, separators=(",", ":")).encode())
    if payload is None:
        payload_b64 = ""
    else:
        payload_b64 = b64url(payload if isinstance(payload, bytes) else json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{protected_b64}.{payload_b64}".encode()
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return {
        "protected": protected_b64,
        "payload": payload_b64,
        "signature": b64url(signature),
    }
