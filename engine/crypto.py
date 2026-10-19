"""Encryption for everything that is published (the repo and the site are public).

* Team pages: AES-256-GCM, key = PBKDF2-SHA256(TEAM_PASSWORD) - one password for the whole agency team.
* Client pages: AES-256-GCM with a per-client 32-byte key that travels only in the link fragment (#k=...),
  derived as HMAC-SHA256(DASHBOARD_MASTER_KEY, "client:<slug>:<key_version>"). Bumping key_version in the
  client's client.json revokes the old link.
Payloads are gzip-compressed before encryption.
"""
import base64
import gzip
import hashlib
import hmac
import json
import os

ITER = 310_000


def _aes():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM


def _pbkdf2(password: str, salt: bytes, iters: int = ITER) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iters).derive(password.encode())


b64 = lambda x: base64.b64encode(x).decode()
unb64 = lambda x: base64.b64decode(x)


def _pack(payload) -> bytes:
    return gzip.compress(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())


def encrypt_password(payload, password: str) -> dict:
    salt, iv = os.urandom(16), os.urandom(12)
    ct = _aes()(_pbkdf2(password, salt)).encrypt(iv, _pack(payload), None)
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": ITER, "salt": b64(salt), "iv": b64(iv), "gzip": True, "ct": b64(ct)}


def decrypt_password(enc: dict, password: str):
    key = _pbkdf2(password, unb64(enc["salt"]), enc["iter"])
    data = _aes()(key).decrypt(unb64(enc["iv"]), unb64(enc["ct"]), None)
    return json.loads(gzip.decompress(data) if enc.get("gzip") else data)


def client_key(master: str, slug: str, version: int = 1) -> bytes:
    return hmac.new(master.encode(), f"client:{slug}:{version}".encode(), hashlib.sha256).digest()


def client_key_url(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode().rstrip("=")


def encrypt_key(payload, key: bytes) -> dict:
    iv = os.urandom(12)
    ct = _aes()(key).encrypt(iv, _pack(payload), None)
    return {"v": 1, "kdf": "raw", "iv": b64(iv), "gzip": True, "ct": b64(ct)}
