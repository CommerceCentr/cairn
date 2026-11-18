"""Видобування вузлів із активу будь-якого типу.

ВУЗОЛ — адресована частина активу. Не обовʼязково символ коду: користувач
може працювати з документами, таблицями, аудіо чи відео, і причинність
йому потрібна така сама. Тому модель узагальнена:

    kind     unit      приклад адреси              що це
    symbol   byte      method:Ns\\Class::name       клас, метод, функція
    section  byte      section:Вступ/Межі          заголовок і його текст
    page     page      page:12                     сторінка PDF
    segment  second    segment:00:04:12-00:05:01   відрізок аудіо чи відео
    whole    none      whole:                      файл як один вузол

ГОЛОВНЕ ПРАВИЛО: невідомий тип не є помилкою. Для нього повертається один
вузол `whole:` — тоді інструмент не ламається ніколи, а лише дає грубішу
адресу. Деградація замість винятку.

ДРУГЕ ПРАВИЛО: бінарне не читається у памʼять заради вузлів. Для медіа
береться розмір і хеш, а відрізки — лише якщо поруч лежить сайдкар
(субтитри, розділи), бо він текстовий і дешевий.

Видобувач — функція (path, data|None) -> list[Node]. Реєстрація за
розширенням; свій тип додається одним записом у REGISTRY.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

#: Одиниці виміру межі вузла.
UNITS = ("byte", "page", "second", "none")

#: Види вузлів.
KINDS = ("symbol", "section", "page", "segment", "whole")


@dataclass(frozen=True)
class Node:
    """Адресована частина активу.

    `address` — те, що потрапляє у сховище і за чим шукають. `start`/`end`
    у одиниці `unit`; для `whole` вони нульові й нічого не означають.
    `label` — людське імʼя, НЕ частина адреси: воно може змінитися.
    """

    address: str
    kind: str
    unit: str
    start: float = 0.0
    end: float = 0.0
    label: str = ""
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"невідомий kind={self.kind!r}, дозволені {KINDS}")
        if self.unit not in UNITS:
            raise ValueError(f"невідомий unit={self.unit!r}, дозволені {UNITS}")


def whole(path: str, size: int = 0, **meta) -> list[Node]:
    """Запасний видобувач: файл як один вузол.

    Повертається для всього, чого ми не вміємо розібрати. Це не поразка:
    на рівні файла працює і сховище сесій, і запит `why`.
    """
    return [Node(address="whole:", kind="whole", unit="none",
                 label=os.path.basename(path), meta={"size": size, **meta})]


#: Розширення -> назва модуля в цьому пакеті. Порожній запис = `whole`.
REGISTRY: dict[str, str] = {
    ".php": "code_php",
    ".py": "code_python",
    ".md": "doc_markdown",
    ".markdown": "doc_markdown",
    ".srt": "media_sidecar",
    ".vtt": "media_sidecar",
}

#: Типи, які свідомо НЕ читаються у памʼять: лише метадані.
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".mp3", ".wav", ".ogg", ".flac", ".m4a",
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".pdf", ".zip", ".gz", ".7z", ".rar", ".exe", ".dll", ".so",
    ".xlsx", ".docx", ".pptx", ".odt", ".ods",
}


def extractor_for(path: str):
    """Видобувач для файла або None, якщо спеціального немає."""
    ext = os.path.splitext(path)[1].lower()
    mod = REGISTRY.get(ext)
    if not mod:
        return None
    from importlib import import_module
    return import_module(f"{__name__}.{mod}")


def nodes_of(path: str, data: bytes | None = None) -> list[Node]:
    """Вузли файла. Ніколи не кидає через невідомий тип.

    Для бінарного вмісту не читає файл: розмір береться зі стату, бо
    тягнути відео у памʼять заради одного вузла — безглуздо.
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        size = os.path.getsize(path) if os.path.exists(path) else len(data or b"")
    except OSError:
        size = 0

    if ext in BINARY_EXT:
        return whole(path, size, binary=True)

    mod = extractor_for(path)
    if mod is None:
        return whole(path, size)

    if data is None:
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            return whole(path, size)

    try:
        out = mod.extract(path, data)
    except Exception:
        # Видобувач упав — це не привід втратити файл цілком.
        return whole(path, size, extractor_failed=True)

    return out or whole(path, size, empty=True)
