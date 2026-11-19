"""Секрети не потрапляють у події, логи й контекст.

Машинні шляхи тут НЕ ріжуться: операційне сховище локальне і шлях
репозиторію є координатою, а не секретом. Відомі форми ключів — ріжуться.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.redact import PATTERNS  # noqa: E402

SKIP = {"WIN_HOME", "NIX_HOME", "WIN_DRIVE"}
MARK = "[REDACTED:{}]"


def redact_text(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    out = text or ""
    for name, rx in PATTERNS:
        if name in SKIP:
            continue
        out, n = rx.subn(MARK.format(name), out)
        if n:
            counts[name] = counts.get(name, 0) + n
    return out, counts


def redact_value(value):
    if isinstance(value, str):
        return redact_text(value)[0]
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): redact_value(v) for k, v in value.items()}
    return value


def secret_hits(value) -> list[str]:
    found: list[str] = []

    def walk(node):
        if isinstance(node, str):
            for name, rx in PATTERNS:
                if name in SKIP:
                    continue
                if rx.search(node):
                    found.append(name)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for item in node.values():
                walk(item)

    walk(value)
    return found
