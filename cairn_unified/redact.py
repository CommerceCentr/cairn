"""Секрети не потрапляють у події, логи й контекст.

Про машинні шляхи. Раніше три шаблони (`WIN_HOME`, `NIX_HOME`, `WIN_DRIVE`)
були оголошені й усі три пропущені через `SKIP`, бо повна редакція знищує
шлях, а шлях — координата, не секрет. Наслідок виміряно: у приватному
сховищі лежало 220 рядків із домашньою текою, і вони їхали в кожен bundle.

Тепер домашня тека ЗГОРТАЄТЬСЯ, а не ріжеться: `C:/Users/<імʼя>/x` -> `~/x`.
Імʼя користувача зникає, шлях лишається читним і порівнюваним.

`WIN_DRIVE` лишається пропущеним свідомо: він зрізав би будь-який шлях
цілком, а шлях репозиторію потрібен для роботи (`repositories.path`).
Шлях поза домашньою текою імені користувача не містить.
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
    ("WIN_DRIVE", re.compile(r"(?i)\b[A-Z]:[\\/][^\s\"']*")),
)

#: Згортання домашньої теки: замінює лише ПРЕФІКС, не весь шлях.
HOME_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("WIN_HOME", re.compile(r"(?i)[A-Z]:[\\/]Users[\\/][^\\/\s\"']+")),
    ("NIX_HOME", re.compile(r"/(?:home|Users)/[^/\s\"']+")),
)

HOME_MARK = "~"

SKIP = {"WIN_DRIVE"}
MARK = "[REDACTED:{}]"


def fold_home(text: str) -> tuple[str, dict[str, int]]:
    """Згорнути домашню теку до `~`. Шлях лишається, імʼя зникає."""
    counts: dict[str, int] = {}
    out = text or ""
    for name, rx in HOME_PATTERNS:
        out, n = rx.subn(HOME_MARK, out)
        if n:
            counts[name] = counts.get(name, 0) + n
    return out, counts


def redact_secrets(text: str) -> tuple[str, dict[str, int]]:
    """Лише секрети, без згортання теки. Для полів-посилань."""
    counts: dict[str, int] = {}
    out = text or ""
    for name, rx in PATTERNS:
        if name in SKIP:
            continue
        out, n = rx.subn(MARK.format(name), out)
        if n:
            counts[name] = counts.get(name, 0) + n
    return out, counts


def redact_text(text: str) -> tuple[str, dict[str, int]]:
    out, counts = fold_home(text or "")
    for name, rx in PATTERNS:
        if name in SKIP:
            continue
        out, n = rx.subn(MARK.format(name), out)
        if n:
            counts[name] = counts.get(name, 0) + n
    return out, counts


#: Поля-ПОСИЛАННЯ: їх значення використовується як адреса (git -C, open(),
#: порівняння коренів), тому згортати їх не можна — зламається робота.
#: Секрети в них однаково ріжуться; не згортається лише домашня тека.
#: Виміряно: згортання `path` ламало `git rev-parse HEAD` у 9 тестах.
NO_FOLD_KEYS = frozenset({
    "path", "repo", "repo_path", "repo_root", "repo_roots", "root", "cwd",
    "worktree", "db_path", "opencode_db", "packs", "packs_dir",
    "lock_file", "descriptor", "journal", "journal_path", "source_ref",
    "changed_paths", "paths", "file_path", "pack",
})


def redact_value(value, *, fold: bool = True):
    """Редакція секретів завжди; згортання домашньої теки — лише для тексту.

    Текст (summary, rationale, intent, рядки звітів) імені користувача
    містити не має. Посилання (`path`, `repo_roots`, `packs_dir`) мусять
    лишитись робочими адресами, інакше сховище перестає працювати.
    """
    if isinstance(value, str):
        return redact_text(value)[0] if fold else redact_secrets(value)[0]
    if isinstance(value, list):
        return [redact_value(v, fold=fold) for v in value]
    if isinstance(value, dict):
        return {str(k): redact_value(v, fold=fold and str(k) not in NO_FOLD_KEYS)
                for k, v in value.items()}
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
