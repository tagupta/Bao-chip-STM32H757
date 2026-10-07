"""Monocypher-3 compatible EdDSA (Ed25519 with BLAKE2b-512) in pure Python.

HoloDi keys and signatures use Monocypher's ``crypto_sign`` /
``crypto_check`` (the scheme the ArduPilot secure bootloader verifies, and
the one ``pymonocypher.signature_sign`` produces). That is RFC 8032 Ed25519
with BLAKE2b-512 in place of SHA-512, so it is *not* interchangeable with
the Ed25519ph signatures Baochip's own boot chain uses.

Only ``pure25519`` (already required by the Dabao SDK signer) and the
standard library are needed, so this works on any Python 3 without
building the pymonocypher C extension.
"""

from __future__ import annotations

import hashlib

from pure25519.basic import (
    L,
    Base,
    bytes_to_clamped_scalar,
    bytes_to_unknown_group_element,
    scalar_to_bytes,
)


def _h(*parts: bytes) -> int:
    digest = hashlib.blake2b(b"".join(parts), digest_size=64).digest()
    return int.from_bytes(digest, "little")


def _expand(secret_key: bytes) -> tuple[int, bytes]:
    if len(secret_key) != 32:
        raise ValueError(f"secret key must be 32 bytes (got {len(secret_key)})")
    digest = hashlib.blake2b(secret_key, digest_size=64).digest()
    return bytes_to_clamped_scalar(digest[:32]), digest[32:]


def public_key(secret_key: bytes) -> bytes:
    a, _ = _expand(secret_key)
    return Base.scalarmult(a).to_bytes()


def sign(secret_key: bytes, message: bytes) -> bytes:
    """Equivalent to Monocypher 3 ``crypto_sign``."""
    a, prefix = _expand(secret_key)
    pk = Base.scalarmult(a).to_bytes()
    r = _h(prefix, message) % L
    r_bytes = Base.scalarmult(r).to_bytes()
    s = (r + _h(r_bytes, pk, message) * a) % L
    return r_bytes + scalar_to_bytes(s)


def verify(signature: bytes, pk: bytes, message: bytes) -> bool:
    """Equivalent to Monocypher 3 ``crypto_check`` (True on success)."""
    if len(signature) != 64 or len(pk) != 32:
        return False
    r_bytes, s_bytes = signature[:32], signature[32:]
    s = int.from_bytes(s_bytes, "little")
    if s >= L:
        return False
    try:
        a_point = bytes_to_unknown_group_element(pk)
        r_point = bytes_to_unknown_group_element(r_bytes)
    except Exception:
        return False
    h = _h(r_bytes, pk, message) % L
    lhs = Base.scalarmult(s)
    rhs = r_point.add(a_point.scalarmult(h))
    return lhs.to_bytes() == rhs.to_bytes()
