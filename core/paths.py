"""Тотожність репозиторію і нормалізація шляхів.

Це те, завдяки чому сховище переноситься між машинами й акаунтами.

    repo_id   sha ПЕРШОГО коміта. Він однаковий у будь-якому клоні, на
              будь-якій машині, у будь-якого користувача — на відміну
              від шляху, імені теки чи адреси remote
    шлях      тільки відносний від кореня репозиторію

Абсолютний шлях у сховище не потрапляє ніколи: він привʼязав би дані до
однієї машини й заодно розкрив би імена користувача й тек.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from functools import lru_cache


def _git(args: list[str], cwd: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", cwd] + args,
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


@lru_cache(maxsize=512)
def repo_root(path: str) -> str | None:
    """Корінь репозиторію, що містить path. None — якщо це не репозиторій."""
    d = path if os.path.isdir(path) else os.path.dirname(path)
    if not d or not os.path.exists(d):
        return None
    root = _git(["rev-parse", "--show-toplevel"], d)
    return os.path.normpath(root) if root else None


@lru_cache(maxsize=512)
def fingerprint(root: str) -> tuple[str, ...] | None:
    """УСІ кореневі коміти репозиторію, відсортовані.

    Чому не один «перший». Виміряно 2026-11-18: beta.loc і community.loc
    мають ОДНАКОВИЙ найменший корінь (73797aa4…) і НУЛЬ спільних комітів
    у HEAD — 175 проти 479. Два різні продукти склеювалися в одну
    тотожність.

    Чому не «корінь плюс другий коміт», як я спробував спершу: виміряно,
    що community.loc має 3 корені, а cc.one — 4, і тоді «другий коміт»
    береться з ланцюга HEAD, тобто ЗАЛЕЖИТЬ ВІД ГІЛКИ. Для ключа, який
    має бути однаковим у будь-якому клоні, це непридатно.

    Набір коренів від гілки не залежить і вже розводить усі пʼять наших
    репозиторіїв. Межа названа: форк, що має ТОЙ САМИЙ набір коренів,
    матиме той самий ключ — і це правильно, бо в них спільна історія.
    """
    out = _git(["rev-list", "--max-parents=0", "--all"], root)
    if not out:
        return None
    roots = tuple(sorted(x.strip() for x in out.splitlines() if x.strip()))
    return roots or None


@lru_cache(maxsize=512)
def repo_id(root: str) -> str | None:
    """Стабільна тотожність репозиторію: відбиток набору коренів.

    Непрозорий навмисно, щоб ніхто не плутав його з sha коміта.
    Розшифровку тримає реєстр repos.json у сховищі.
    """
    fp = fingerprint(root)
    if not fp:
        return None
    return hashlib.sha256("\n".join(fp).encode()).hexdigest()[:40]


def norm(p: str) -> str:
    """Один вигляд шляху: прямі скісні, без кінцевої."""
    return p.replace("\\", "/").rstrip("/")


def relativize(abs_path: str, roots: dict[str, str]) -> tuple[str, str] | None:
    """(repo_id, відносний шлях) або None, якщо файл поза відомими репо.

    roots: {нормалізований корінь -> repo_id}. Беремо НАЙДОВШИЙ корінь,
    що підходить: вкладені репозиторії (worktree всередині дерева) інакше
    приписались би батьківському.
    """
    a = norm(os.path.normpath(abs_path)) if os.path.isabs(abs_path) else norm(abs_path)
    best: tuple[str, str] | None = None
    best_len = -1
    for root, rid in roots.items():
        r = norm(root)
        if a == r or a.lower().startswith(r.lower() + "/"):
            if len(r) > best_len:
                best_len = len(r)
                best = (rid, a[len(r) + 1:]) if a != r else (rid, "")
    return best
