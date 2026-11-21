"""Тимчасовий git-репозиторій і тека пакетів для публічних тестів.

Навіщо. Виміряно 2026-11-21: `verify_auto_gate.py` і `verify_was_rationale.py`
стояли в `tests/run_all.py`, тобто у воротах CI, і при цьому зверталися до
`Q:\\650\\home\\beta.loc` — приватного диска автора. Перевірено прогоном із
підміненим шляхом: обидва дають **exit 1**, а не «тихо пропускають». Отже
ворота CI на чужій машині зелені бути не могли.

Тут немає PHP, немає мережі й немає жодного зовнішнього шляху: усе
створюється в temp і прибирається викликачем.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

__all__ = ["git", "make_repo", "make_packs", "PHP_FILE", "PY_FILE"]

PHP_FILE = "core/Engine/Controller.php"
PY_FILE = "src/app/service.py"

_PHP_V1 = """<?php
namespace Fixture\\Engine;

class Controller
{
    public function run(): int
    {
        return 1;
    }
}
"""

_PHP_V2 = _PHP_V1.replace("return 1;", "return 2;")


def git(repo: str | Path, *args: str) -> str:
    """git у фікстурі. `check=True`: мовчазний провал фікстури гірший за виняток."""
    return subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout.strip()


def make_repo(path: Path, *, files: dict[str, str] | None = None,
              second: dict[str, str] | None = None,
              messages: tuple[str, str] = ("initial", "change the node")) -> dict:
    """Створити репозиторій із двома комітами; повернути {path, head, first}.

    Два коміти, а не один: історія вузла з одного коміту не відрізняє
    «вузол не змінювався» від «вузла ще не було».
    """
    files = files or {PHP_FILE: _PHP_V1}
    second = second or {PHP_FILE: _PHP_V2}
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "user.email", "fixture@local")
    git(path, "config", "user.name", "fixture")
    for rel, text in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-qm", messages[0])
    first = git(path, "rev-parse", "HEAD")
    for rel, text in second.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-qm", messages[1])
    return {"path": str(path), "head": git(path, "rev-parse", "HEAD"), "first": first}


def make_packs(path: Path, packs: dict[str, str]) -> Path:
    """Тека пакетів із заданим вмістом. Digest рахує викликач із цього ж тексту."""
    path.mkdir(parents=True, exist_ok=True)
    for name, text in packs.items():
        (path / name).write_text(text, encoding="utf-8")
    return path
