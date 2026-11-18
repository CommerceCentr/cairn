"""Відрізки аудіо й відео — із сайдкара, не з самого медіа.

Саме медіа не розбирається: тягнути відео у памʼять заради адрес було б
дорого й безглуздо. Натомість береться те, що поруч і текстове —
субтитри чи розділи. Це дешево, не потребує кодеків і працює скрізь.

Адреса — ТАЙМКОД початку, а не номер репліки:
    segment:00:04:12
Номер зсувається від будь-якої вставки вище, час — ні. Та сама причина,
що й у коді (символ, не рядок) та в документах (шлях заголовка, не номер).

Якщо сайдкара немає, медіа лишається одним вузлом `whole:` — див. реєстр.
"""

from __future__ import annotations

import re

from . import Node

RE_SRT = re.compile(
    rb"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")
RE_VTT_HEAD = re.compile(rb"^WEBVTT", re.M)


def _secs(h: bytes, m: bytes, s: bytes, ms: bytes) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def extract(path: str, data: bytes) -> list[Node]:
    out: list[Node] = []
    seen: dict[str, int] = {}

    for m in RE_SRT.finditer(data):
        start = _secs(*m.group(1, 2, 3, 4))
        end = _secs(*m.group(5, 6, 7, 8))

        # Текст репліки — до порожнього рядка. Він іде в label, не в
        # адресу: текст правлять, час лишається.
        tail = data[m.end():m.end() + 400]
        chunk = re.split(rb"\r?\n\r?\n", tail, maxsplit=1)[0]
        label = chunk.decode("utf-8", "replace").strip().replace("\n", " ")

        raw = f"segment:{int(start // 3600):02d}:{int(start % 3600 // 60):02d}:{int(start % 60):02d}"
        seen[raw] = seen.get(raw, 0) + 1
        addr = raw if seen[raw] == 1 else f"{raw}#{seen[raw]}"

        out.append(Node(addr, "segment", "second", start, end, label[:120]))

    return out
