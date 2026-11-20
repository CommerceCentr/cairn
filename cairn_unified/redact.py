"""Секрети не потрапляють у події, логи й контекст.

Машинні шляхи тут НЕ ріжуться: операційне сховище локальне і шлях
репозиторію є координатою, а не секретом. Відомі форми ключів — ріжуться.
"""

from __future__ import annotations

import re

#: (назва, вираз). Назва потрапляє в мітку, щоб було видно, що саме зникло.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ANTHROPIC_KEY", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("OPENAI_KEY", re.compile(r"\bsk-(?!ant-)[A-Za-z0-9]{20,}")),
    ("GITHUB_TOKEN", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("AWS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GOOGLE_KEY", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("SLACK_TOKEN", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("BEARER", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}")),
    ("PASSWORD", re.compile(r"(?i)\b(?:password|passwd|secret|api[_\-]?key)\s*[:=]\s*\S{6,}")),
    ("CONNSTR", re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s/@]+:[^\s/@]+@\S+")),
    # Машинні шляхи: не секрет у вузькому сенсі, але привʼязують сховище
    # до однієї машини й розкривають імʼя користувача.
    ("WIN_HOME", re.compile(r"(?i)[A-Z]:[\\/]Users[\\/][^\\/\s\"']+")),
    ("NIX_HOME", re.compile(r"/(?:home|Users)/[^/\s\"']+")),
    ("WIN_DRIVE", re.compile(r"(?i)\b[A-Z]:[\\/][^\s\"']*")),
)


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
