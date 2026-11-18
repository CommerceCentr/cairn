"""Вивід не має падати на власних даних.

Сховище тримає текст будь-якими мовами, а консоль Windows часто у cp866
або cp1251. Виміряно двічі: спершу `why` упав на кириличній скомпактованому контексті, потім
тест упав на символі ʼ (U+02BC) у слові «звʼязка».

Заміна нечитного символу гірша за точний вивід, але краща за виняток
посеред відповіді. Викликати на початку будь-якої точки входу.
"""

from __future__ import annotations

import sys


def setup() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
