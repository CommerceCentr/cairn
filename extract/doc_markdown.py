"""Розділи документа: заголовок і текст під ним.

Документ — такий самий актив, як код, і причинність до нього та сама:
«чому цей розділ такий» і «хто його правив». Вузлом тут є РОЗДІЛ, а не
абзац: абзаци рухаються й зливаються, заголовки живуть довше.

Адреса — шлях заголовків, а не номер:
    section:Вступ
    section:Вступ/Межі
Номер розділу зсувається від будь-якої вставки вище, а шлях — ні. Це та
сама причина, через яку в коді ми адресуємо символ, а не рядок.

Межа розділу — від його заголовка до наступного заголовка того самого або
вищого рівня. Текст під заголовком належить йому, вкладені розділи —
своїм власним вузлам.
"""

from __future__ import annotations

import re

from . import Node

RE_ATX = re.compile(rb"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
RE_FENCE = re.compile(rb"^(```|~~~)", re.M)


def _fenced_spans(data: bytes) -> list[tuple[int, int]]:
    """Межі блоків коду: заголовок усередині ``` не є заголовком."""
    spans: list[tuple[int, int]] = []
    open_at: int | None = None
    for m in RE_FENCE.finditer(data):
        if open_at is None:
            open_at = m.start()
        else:
            spans.append((open_at, m.end()))
            open_at = None
    if open_at is not None:
        spans.append((open_at, len(data)))
    return spans


def extract(path: str, data: bytes) -> list[Node]:
    fenced = _fenced_spans(data)

    def in_fence(pos: int) -> bool:
        return any(s <= pos < e for s, e in fenced)

    heads: list[tuple[int, int, str]] = []   # (зміщення, рівень, текст)
    for m in RE_ATX.finditer(data):
        if in_fence(m.start()):
            continue
        level = len(m.group(1))
        text = m.group(2).decode("utf-8", "replace").strip()
        text = re.sub(r"\s+", " ", text)
        heads.append((m.start(), level, text))

    if not heads:
        return []

    seen: dict[str, int] = {}

    def addr(raw: str) -> str:
        seen[raw] = seen.get(raw, 0) + 1
        return raw if seen[raw] == 1 else f"{raw}#{seen[raw]}"

    out: list[Node] = []
    stack: list[tuple[int, str]] = []      # (рівень, текст)

    for i, (pos, level, text) in enumerate(heads):
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, text))
        path_txt = "/".join(t for _, t in stack)

        end = len(data)
        for pos2, level2, _ in heads[i + 1:]:
            if level2 <= level:
                end = pos2
                break

        out.append(Node(addr(f"section:{path_txt}"), "section", "byte",
                        pos, end, text, {"level": level}))

    return out
