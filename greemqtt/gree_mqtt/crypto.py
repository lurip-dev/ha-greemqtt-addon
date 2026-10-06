"""Encryption used by the Gree WiFi LAN protocol.

Two schemes exist in the field:
* ECB - older firmware, AES-128-ECB with PKCS#7 padding.
* GCM - firmware 1.21+ ("encryption v2"), AES-128-GCM with a fixed nonce/AAD;
  packets carry an extra ``tag`` field.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

GENERIC_KEY_ECB = "a3K8Bx%2r8Y7#xDh"
GENERIC_KEY_GCM = "{yxAHAY_Lm6pbC/<"
GCM_NONCE = b"\x54\x40\x78\x44\x49\x67\x5a\x51\x6c\x5e\x63\x13"
GCM_AAD = b"qualcomm-test"

ECB = "ecb"
GCM = "gcm"


class CipherError(Exception):
    """Payload could not be decrypted (wrong key or scheme)."""


def _loads(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace")
    end = text.rfind("}")
    if end < 0:
        raise CipherError("decrypted payload is not JSON")
    try:
        return json.loads(text[: end + 1])
    except json.JSONDecodeError as err:
        raise CipherError(f"decrypted payload is not JSON: {err}") from err


def encrypt(scheme: str, key: str, data: dict[str, Any]) -> dict[str, str]:
    """Encrypt ``data`` and return the packet fields (``pack`` and maybe ``tag``)."""
    plain = json.dumps(data, separators=(",", ":")).encode()
    if scheme == ECB:
        pad = 16 - len(plain) % 16
        plain += bytes([pad]) * pad
        enc = Cipher(algorithms.AES(key.encode()), modes.ECB()).encryptor()
        return {"pack": base64.b64encode(enc.update(plain) + enc.finalize()).decode()}
    if scheme == GCM:
        enc = Cipher(algorithms.AES(key.encode()), modes.GCM(GCM_NONCE)).encryptor()
        enc.authenticate_additional_data(GCM_AAD)
        body = enc.update(plain) + enc.finalize()
        return {
            "pack": base64.b64encode(body).decode(),
            "tag": base64.b64encode(enc.tag).decode(),
        }
    raise ValueError(f"unknown cipher {scheme!r}")


def decrypt(scheme: str, key: str, packet: dict[str, Any]) -> dict[str, Any]:
    """Decrypt the ``pack`` field of a received packet."""
    try:
        body = base64.b64decode(packet["pack"])
    except (KeyError, ValueError, TypeError) as err:
        raise CipherError(f"packet has no valid pack: {err}") from err
    try:
        if scheme == ECB:
            if len(body) % 16:
                raise CipherError("ECB payload length is not a multiple of 16")
            dec = Cipher(algorithms.AES(key.encode()), modes.ECB()).decryptor()
            return _loads(dec.update(body) + dec.finalize())
        if scheme == GCM:
            if "tag" not in packet:
                raise CipherError("GCM packet without tag")
            tag = base64.b64decode(packet["tag"])
            dec = Cipher(algorithms.AES(key.encode()), modes.GCM(GCM_NONCE, tag)).decryptor()
            dec.authenticate_additional_data(GCM_AAD)
            return _loads(dec.update(body) + dec.finalize())
    except (InvalidTag, ValueError) as err:
        raise CipherError(f"cannot decrypt with {scheme}: {err}") from err
    raise ValueError(f"unknown cipher {scheme!r}")


def scheme_of(packet: dict[str, Any]) -> str:
    """Guess the scheme a device used from the shape of its packet."""
    return GCM if "tag" in packet else ECB


def generic_key(scheme: str) -> str:
    return GENERIC_KEY_GCM if scheme == GCM else GENERIC_KEY_ECB
