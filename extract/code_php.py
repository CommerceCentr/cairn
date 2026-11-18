"""Символи PHP: клас, метод, функція, константа, властивість.

Повного розбирача PHP тут немає і не потрібно: нас цікавлять ОГОЛОШЕННЯ,
а не семантика. Тому сканер іде по тексту, тримає глибину фігурних дужок і
пропускає рядки, коментарі та heredoc — саме вони ламають наївний regex.

ЧОМУ ВЛАСНИЙ СКАНЕР, А НЕ ЗАЛЕЖНІСТЬ. Інструмент має ставитися однією
командою й працювати там, де немає ні PHP, ні composer, ні компілятора.
Ціна названа: це не парсер, і екзотичний синтаксис він може пропустити.
Саме тому точність ВИМІРЮЄТЬСЯ (tests/bench_extract.py), а не
проголошується.

Правило адреси виведене з реальних індексів чотирьох продуктів:
    class:Ns\\Name          method:Ns\\Name::member
    const:Ns\\Name::NAME    property:name        function:name
Повтор тієї самої адреси у файлі дістає суфікс #2, #3 — як у вихідних
даних.
"""

from __future__ import annotations

import re

from . import Node

NAME = r"[A-Za-z_\x80-\xff][A-Za-z0-9_\x80-\xff]*"

RE_NAMESPACE = re.compile(rb"\bnamespace\s+([A-Za-z_\\][A-Za-z0-9_\\]*)\s*[;{]")
RE_CLASSLIKE = re.compile(
    rb"\b(?:final\s+|abstract\s+|readonly\s+)*(class|interface|trait|enum)\s+(" +
    NAME.encode() + rb")\b")
RE_FUNCTION = re.compile(rb"\bfunction\s+&?\s*(" + NAME.encode() + rb")\s*\(")
RE_CONST = re.compile(rb"\bconst\s+(?:" + NAME.encode() + rb"\s+)?(" +
                      NAME.encode() + rb")\s*=")
RE_PROPERTY = re.compile(
    rb"\b(?:public|protected|private|var|readonly|static)\s+"
    rb"(?:[?A-Za-z_\\|\[\]]+\s+)?\$(" + NAME.encode() + rb")\b")


def _strip(data: bytes) -> bytes:
    """Замінює вміст рядків і коментарів пробілами, зберігаючи ЗМІЩЕННЯ.

    Саме збереження довжини робить подальші межі байтово правильними:
    якщо вирізати, усі зміщення попливуть.
    """
    out = bytearray(data)
    i, n = 0, len(data)
    while i < n:
        c = data[i:i + 1]
        two = data[i:i + 2]
        if two == b"//" or c == b"#":
            j = data.find(b"\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                if out[k] not in (10, 13):
                    out[k] = 32
            i = j
        elif two == b"/*":
            j = data.find(b"*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] not in (10, 13):
                    out[k] = 32
            i = j
        elif c in (b"'", b'"'):
            q = c
            j = i + 1
            while j < n:
                if data[j:j + 1] == b"\\":
                    j += 2
                    continue
                if data[j:j + 1] == q:
                    j += 1
                    break
                j += 1
            for k in range(i, min(j, n)):
                if out[k] not in (10, 13):
                    out[k] = 32
            i = j
        elif two == b"<<" and data[i:i + 3] == b"<<<":
            m = re.match(rb"<<<\s*[\"']?(" + NAME.encode() + rb")[\"']?\r?\n", data[i:])
            if not m:
                i += 3
                continue
            tag = m.group(1)
            end = re.search(rb"\n\s*" + re.escape(tag) + rb"\b", data[i:])
            j = n if not end else i + end.end()
            for k in range(i, min(j, n)):
                if out[k] not in (10, 13):
                    out[k] = 32
            i = j
        else:
            i += 1
    return bytes(out)


def _depths(clean: bytes) -> list[int]:
    """Глибина фігурних дужок перед кожним байтом."""
    out = [0] * (len(clean) + 1)
    d = 0
    for i, b in enumerate(clean):
        out[i] = d
        if b == 0x7B:       # {
            d += 1
        elif b == 0x7D:     # }
            d -= 1
    out[len(clean)] = d
    return out


def extract(path: str, data: bytes) -> list[Node]:
    clean = _strip(data)
    depth = _depths(clean)

    ns = ""
    m = RE_NAMESPACE.search(clean)
    if m:
        ns = m.group(1).decode("utf-8", "replace").strip("\\")

    # Класи з їхніми межами: від ключового слова до парної закривної дужки.
    classes: list[tuple[int, int, str]] = []
    for m in RE_CLASSLIKE.finditer(clean):
        start = m.start()
        brace = clean.find(b"{", m.end())
        if brace < 0:
            continue
        d0 = depth[brace]
        end = len(clean)
        for k in range(brace + 1, len(clean)):
            if clean[k] == 0x7D and depth[k] == d0:
                end = k + 1
                break
        fqn = (ns + "\\" if ns else "") + m.group(2).decode("utf-8", "replace")
        classes.append((start, end, fqn))

    def owner(pos: int) -> str | None:
        best = None
        for s, e, fqn in classes:
            if s <= pos < e and (best is None or s > best[0]):
                best = (s, fqn)
        return best[1] if best else None

    seen: dict[str, int] = {}

    def addr(raw: str) -> str:
        seen[raw] = seen.get(raw, 0) + 1
        return raw if seen[raw] == 1 else f"{raw}#{seen[raw]}"

    nodes: list[Node] = []
    for s, e, fqn in classes:
        nodes.append(Node(addr(f"class:{fqn}"), "symbol", "byte", s, e, fqn))

    for m in RE_FUNCTION.finditer(clean):
        name = m.group(1).decode("utf-8", "replace")
        own = owner(m.start())
        raw = f"method:{own}::{name}" if own else \
              f"function:{(ns + chr(92)) if ns else ''}{name}"
        end = _body_end(clean, depth, m.end())
        nodes.append(Node(addr(raw), "symbol", "byte", m.start(), end, name))

    for m in RE_CONST.finditer(clean):
        name = m.group(1).decode("utf-8", "replace")
        own = owner(m.start())
        raw = f"const:{own}::{name}" if own else \
              f"const:{(ns + chr(92)) if ns else ''}{name}"
        end = clean.find(b";", m.end())
        end = m.end() if end < 0 else end + 1
        nodes.append(Node(addr(raw), "symbol", "byte", m.start(), end, name))

    for m in RE_PROPERTY.finditer(clean):
        own = owner(m.start())
        if own is None:
            continue
        name = m.group(1).decode("utf-8", "replace")
        end = clean.find(b";", m.end())
        end = m.end() if end < 0 else end + 1
        # Властивість адресується ВКЛАДЕНО, під своїм класом. Виміряно на
        # чужому індексі: там це `class:Ns\Name/property:field`, і саме
        # так адреса лишається однозначною, коли однойменні поля є в
        # кількох класах одного файла. Метод натомість уже однозначний
        # завдяки `::`, тож його форма не змінюється.
        nodes.append(Node(addr(f"class:{own}/property:{name}"), "symbol",
                          "byte", m.start(), end, name))

    nodes.sort(key=lambda n: (n.start, n.address))
    return nodes


def _body_end(clean: bytes, depth: list[int], pos: int) -> int:
    """Кінець функції: тіло до парної дужки, або «;» для абстрактних."""
    brace = clean.find(b"{", pos)
    semi = clean.find(b";", pos)
    if semi >= 0 and (brace < 0 or semi < brace):
        return semi + 1
    if brace < 0:
        return pos
    d0 = depth[brace]
    for k in range(brace + 1, len(clean)):
        if clean[k] == 0x7D and depth[k] == d0:
            return k + 1
    return len(clean)
