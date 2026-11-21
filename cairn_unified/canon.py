"""Канонічна серіалізація, час і UUIDv7. Без зовнішніх залежностей."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
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


def write_json_atomic(path, value, *, indent: int = 2) -> None:
    """Записати JSON через tmp + `os.replace`.

    Обрив звичайного `write_text` лишає півфайла. Для `manifest.json` це
    фатально: `Store.open` вважає його доказом існування проєкту, тож
    порваний запис робить сховище неоткриваним. `os.replace` атомарний на
    Windows і POSIX.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                              indent=indent) + "\n",
                   encoding="utf-8", newline="\n")
    os.replace(tmp, target)


def audit_text(value) -> str:
    """Серіалізація для digest аудиту зон і merge.

    Типові розділювачі — НЕ випадковість: саме в цій формі обчислені всі
    наявні digest-и дескрипторів, журналів і bundle зон. Зміна форми
    знецінила б їх, тому форма зафіксована тут і більше ніде не
    повторюється.

    Для подій і bundle федерації діє `canonical()` (компактна форма). Це
    два РІЗНІ домени: їхні значення лежать поруч і не порівнюються.
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def audit_digest(value) -> str:
    """sha256 від `audit_text`. Без префікса — як у наявних даних."""
    return sha256_text(audit_text(value))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()




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




def prefixed(prefix: str) -> str:
    return f"{prefix}_{uuid7()}"
