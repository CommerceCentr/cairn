"""ПІДРОБКА: події є, але поля заповнені «на вигляд правильно».

Третя форма, найпідступніша: адаптер щось віддає, і без перевірки це
зарахували б. actor поза переліком -> ворота мусять упасти на D.
"""

from __future__ import annotations

from . import claude_code

NAME = "_probe_badactor"


def discover(root: str | None = None) -> list[str]:
    return claude_code.discover(root)


def read(path: str, offset: int = 0):
    for ev in claude_code.read(path, offset):
        ev["actor"] = "assistant"  # такого актора контракт не знає
        yield ev
