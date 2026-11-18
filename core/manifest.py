"""Маніфест журналів: розмір на зрізі та sha256 саме тих байтів.

Навіщо не просто mtime: журнал живої сесії ДОПИСУЄТЬСЯ весь час. Якщо
звіряти ціле, то все «змінилось» і треба перечитувати 2.5 ГБ. Якщо ж
зафіксувати (розмір, sha256 перших N байтів), то:

    дописали в кінець  -> перші N байтів ті самі -> MATCH_APPENDED,
                          читаємо лише хвіст
    переписали/вкоротили -> перші N байтів інші -> REWRITTEN, читаємо все

Тобто інкрементність тут не оптимізація, а умова здійсненності.
"""

from __future__ import annotations

import hashlib
import json
import os

CHUNK = 1 << 20

MATCH_APPENDED = "MATCH_APPENDED"
MATCH_SAME = "MATCH_SAME"
REWRITTEN = "REWRITTEN"
NEW = "NEW"
GONE = "GONE"


def sha256_prefix(path: str, nbytes: int) -> str:
    """sha256 РІВНО перших nbytes байтів."""
    h = hashlib.sha256()
    left = nbytes
    with open(path, "rb") as fh:
        while left > 0:
            blk = fh.read(min(CHUNK, left))
            if not blk:
                break
            left -= len(blk)
            h.update(blk)
    return h.hexdigest()


def snapshot(path: str) -> dict:
    size = os.path.getsize(path)
    return {"size": size, "sha256": sha256_prefix(path, size)}


def classify(path: str, prev: dict | None) -> tuple[str, int]:
    """(стан, зміщення, з якого читати). Нічого не пише."""
    if not os.path.exists(path):
        return GONE, 0
    size = os.path.getsize(path)
    if prev is None:
        return NEW, 0

    prev_size = int(prev.get("size", -1))
    if prev_size < 0 or prev_size > size:
        # Файл ВКОРОТИВСЯ — це не доповнення, перечитуємо все.
        return REWRITTEN, 0

    # Порівнюємо саме ту кількість байтів, яку бачили минулого разу.
    if sha256_prefix(path, prev_size) != prev.get("sha256"):
        return REWRITTEN, 0

    return (MATCH_SAME if prev_size == size else MATCH_APPENDED), prev_size


def load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError:
            return {}
    return data.get("files", {}) if isinstance(data, dict) else {}


def save(path: str, files: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"schema": "cairn-manifest/v1", "files": files},
                  fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)
