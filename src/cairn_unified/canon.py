"""Канонічна серіалізація, час і UUIDv7. Без зовнішніх залежностей."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    value = dt or utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def add_seconds(stamp: str, seconds: int) -> str:
    return iso(parse_iso(stamp) + timedelta(seconds=seconds))


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def payload_hash(payload: dict) -> str:
    return sha256_text(canonical(payload))


def uuid7() -> str:
    """UUIDv7: 48 біт unix-ms, версія 7, варіант RFC 4122."""
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    raw = bytearray(os.urandom(16))
    raw[0] = (ms >> 40) & 0xFF
    raw[1] = (ms >> 32) & 0xFF
    raw[2] = (ms >> 24) & 0xFF
    raw[3] = (ms >> 16) & 0xFF
    raw[4] = (ms >> 8) & 0xFF
    raw[5] = ms & 0xFF
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def stable_id(prefix: str, *parts: str) -> str:
    digest = sha256_text("\n".join(parts))[:26]
    return f"{prefix}_{digest}"


def prefixed(prefix: str) -> str:
    return f"{prefix}_{uuid7()}"
