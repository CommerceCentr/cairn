"""ПІДРОБКА: журнали знайдено, але жодної події не видано.

Друга форма: шлях вгадано, формат не розібрано, і адаптер тихо віддає
порожнечу. Ворота мусять упасти на перевірці C.
"""

from __future__ import annotations

from . import claude_code

NAME = "_probe_noevents"


def discover(root: str | None = None) -> list[str]:
    return claude_code.discover(root)


def read(path: str, offset: int = 0):
    return iter(())
