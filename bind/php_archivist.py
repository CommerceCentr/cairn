"""Необовʼязковий шар: адреса вузла <-> файл, через індекс архіваріуса.

Ядро cairn мовно-незалежне і знає тільки шляхи файлів. Причина
виміряна: агенти говорять шляхами, а не адресами вузлів (у пробі сесії —
12 згадок `.php` і НУЛЬ згадок `Class::method`). Адреса вузла
мовно-специфічна, тож її розуміння винесене сюди.

Цей модуль читає `heat.tsv` архіваріуса — зовнішнього інструмента для
PHP. Якщо його немає, усе інше працює без змін: це додаток, не умова.

Шлях до архіваріуса НЕ зашитий. Він приходить аргументом або зі змінної
CAIRN_ARCH_ROOT: інструмент має лишатися придатним на чужій машині.

Межа, названа прямо: звʼязок «скомпактований контекст -> вузол» тут СЕСІЙНИЙ, а не
причинний. Він означає «файл цього вузла торкались у сесії, яка дала цю
скомпактований контекст», а не «скомпактований контекст про цей вузол». Сесія чіпає багато файлів.
"""

from __future__ import annotations

import os

NAME = "php_archivist"
ENV_ROOT = "CAIRN_ARCH_ROOT"


def arch_root(explicit: str | None = None) -> str | None:
    return explicit or os.environ.get(ENV_ROOT) or None


def heat_path(root: str, zone: str) -> str:
    return os.path.join(root, "zones", zone, "heat.tsv")


def zones(root: str) -> list[str]:
    base = os.path.join(root, "zones")
    if not os.path.isdir(base):
        return []
    return sorted(d for d in os.listdir(base)
                  if os.path.isfile(heat_path(root, d)))


def zone_repo(root: str, zone: str) -> str | None:
    """Шлях до репозиторію зони, з її ж zone.json.

    Без цього `bind` зіставляв би ЛИШЕ за рядком шляху, через усі
    репозиторії відразу. Виміряно 2026-11-18, чому це хибно: ядро
    розкладене копіями по зонах, тож `core/Services/Routing/Router.php`
    існує в кількох продуктах. Зона beta.loc, яка не має жодної сесії,
    показувала 5 із 8 покритих вузлів — а ті дотики належали cc.one і
    community.loc.
    """
    import json
    p = os.path.join(root, "zones", zone, "zone.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    repo = data.get("repo") if isinstance(data, dict) else None
    return repo or None


def load(root: str, zone: str) -> tuple[dict[str, list[str]], dict[str, str]]:
    """(файл -> адреси, адреса -> файл).

    Формат heat.tsv виміряний:
        <файл> \\t C \\t ? \\t X \\t <адреса>|C|?|прапорці;<адреса>|…
    """
    path = heat_path(root, zone)
    if not os.path.exists(path):
        raise FileNotFoundError(f"немає {path}")

    by_file: dict[str, list[str]] = {}
    by_addr: dict[str, str] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            col = ln.rstrip("\n").split("\t")
            if len(col) < 5:
                continue
            f = col[0].replace("\\", "/")
            addrs: list[str] = []
            for chunk in col[4].split(";"):
                parts = chunk.split("|")
                if parts and parts[0]:
                    addrs.append(parts[0])
                    by_addr.setdefault(parts[0], f)
            if addrs:
                by_file.setdefault(f, []).extend(addrs)
    return by_file, by_addr


def file_of(addr: str, root: str, zone: str) -> str | None:
    """Файл, у якому живе вузол. None — якщо адреси в індексі немає."""
    _, by_addr = load(root, zone)
    return by_addr.get(addr)


def nodes_of(rel_path: str, root: str, zone: str) -> list[str]:
    """Вузли файла. Порожньо — якщо файл не в індексі."""
    by_file, _ = load(root, zone)
    return sorted(set(by_file.get(rel_path.replace("\\", "/"), [])))
