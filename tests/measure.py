"""Єдиний лічильник обсягу для публічних вимірів.

Лише стандартна бібліотека: цей модуль мусить працювати в CI, де немає ні
`tokenizers`, ні кешу HuggingFace, ні приватних тек.

МЕЖА, названа прямо: `tokens_est` — це ceil(len/4), проксі, а НЕ токенізатор
цільової моделі. Абсолютні числа не переносяться між моделями. Порівнювати
можна лише два виміри, зроблені ОДНИМ лічильником. Якщо в середовищі є
`tokenizers` і локальний `tokenizer.json`, `bpe()` дасть другий, незалежний
лічильник — два незгодних лічильники означають, що висновок передчасний.
"""

from __future__ import annotations

import hashlib
import math
import os
import time

__all__ = ["norm", "tokens_est", "measure", "bpe", "add", "timed", "ratio"]


def norm(text: str) -> str:
    """Нормалізація пробілів. Та сама, що в приватному вимірювальному наборі."""
    return " ".join(text.split())


def tokens_est(text: str) -> int:
    return math.ceil(len(text) / 4)


def measure(text: str) -> dict:
    return {
        "chars": len(text),
        "bytes": len(text.encode("utf-8")),
        "lines": len(text.splitlines()),
        "tokens_est": tokens_est(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
    }


def add(left: dict, right: dict) -> dict:
    """Сума двох вимірів. `sha256` не додається — він губить сенс у сумі."""
    return {
        "chars": left["chars"] + right["chars"],
        "bytes": left["bytes"] + right["bytes"],
        "lines": left["lines"] + right["lines"],
        "tokens_est": left["tokens_est"] + right["tokens_est"],
    }


ZERO = {"chars": 0, "bytes": 0, "lines": 0, "tokens_est": 0}


def bpe(text: str) -> int | None:
    """Другий лічильник, якщо він є. None — означає «не встановлено»."""
    path = os.environ.get("CAIRN_TOKENIZER_JSON")
    if not path or not os.path.exists(path):
        return None
    try:
        from tokenizers import Tokenizer
    except ImportError:
        return None
    return len(Tokenizer.from_file(path).encode(text).ids)


def timed(fn, *args, **kwargs):
    """(результат, мілісекунди). Час одного прогону, не медіана."""
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, (time.perf_counter() - start) * 1000.0


def ratio(native: dict, cairn: dict, key: str) -> float | None:
    """Відношення. None, якщо знаменник нуль — це не «нескінченно», це «немає»."""
    bottom = cairn.get(key) or 0
    if not bottom:
        return None
    return round(native.get(key, 0) / bottom, 1)
