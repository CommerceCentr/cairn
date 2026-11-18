"""Скан секретів і редакція тексту, який іде у сховище.

Сховище причинності зберігає ОДИН вид тексту — скомпактовані контексти, бо
саме вони є вже стиснутим «чому». Але це текст розмови: там бувають
ключі, токени й машинні шляхи. Тому перед записом він проходить тут.

Два принципи, обидва названі навмисно:

1  РЕДАКЦІЯ НЕ ВИДАЛЯЄ, А ЗАМІНЮЄ на мітку з типом. Видалення сховало б
   і сам факт, що там щось було; мітка лишає слід, за яким видно, що
   текст неповний, і чому.
2  ЩО НЕ РОЗПІЗНАНО — ТЕ НЕ ЗАХИЩЕНО. Перелік нижче скінченний, і це
   його головна межа. Він ловить відомі форми, а не «усі секрети».
   Саме тому ворота контракту ПЕРЕВІРЯЮТЬ текст ще раз перед записом:
   якщо сканер щось бачить — запис не приймається, а не «чиститься».
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

MARK = "[REDACTED:{}]"


def find_secrets(text: str) -> list[tuple[str, str]]:
    """Усе розпізнане. Порожній список — НЕ доказ чистоти, лише те, що
    відомі форми не знайдені."""
    hits: list[tuple[str, str]] = []
    for name, rx in PATTERNS:
        for m in rx.finditer(text or ""):
            hits.append((name, m.group(0)))
    return hits


def redact(text: str) -> tuple[str, dict[str, int]]:
    """(очищений текст, скільки чого замінено)."""
    counts: dict[str, int] = {}
    out = text or ""
    for name, rx in PATTERNS:
        out, n = rx.subn(MARK.format(name), out)
        if n:
            counts[name] = counts.get(name, 0) + n
    return out, counts


def is_clean(text: str) -> bool:
    return not find_secrets(text)
