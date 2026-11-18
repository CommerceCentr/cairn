"""Символи Python — через `ast`, бо він у стандартній бібліотеці.

Тут, на відміну від PHP, здогадуватись не треба: розбирач мови вже є, і
він дає точні межі. Саме так і має виглядати видобувач, коли мова
постачає свій парсер — сканер пишеться лише там, де вибору немає.

Адреси тримають ту саму форму, що й для інших мов:
    class:Pkg.Class        method:Pkg.Class.name
    function:Pkg.name      const:Pkg.NAME
Крапка замість зворотної скісної, бо так пишеться Python; розділювач —
властивість мови, а не адреси.
"""

from __future__ import annotations

import ast
import os

from . import Node


def _module_name(path: str) -> str:
    """Імʼя модуля з імені файла. Без пакета: шлях до кореня нам невідомий,
    а вигадувати його означало б вигадати частину адреси."""
    base = os.path.basename(path)
    return base[:-3] if base.endswith(".py") else base


def extract(path: str, data: bytes) -> list[Node]:
    src = data.decode("utf-8", "replace")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        # Непридатний до розбору файл — не привід втратити його цілком.
        return []

    # Зміщення початку кожного рядка: ast дає рядок і стовпець, а межі
    # вузла ми тримаємо в байтах, як і для решти типів.
    offsets = [0]
    for line in src.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line.encode("utf-8")))

    def span(node: ast.AST) -> tuple[int, int]:
        lineno = getattr(node, "lineno", 1) - 1
        end_lineno = getattr(node, "end_lineno", None)
        start = offsets[min(lineno, len(offsets) - 1)]
        if end_lineno is None:
            return start, start
        end = offsets[min(end_lineno, len(offsets) - 1)]
        return start, end

    mod = _module_name(path)
    seen: dict[str, int] = {}

    def addr(raw: str) -> str:
        seen[raw] = seen.get(raw, 0) + 1
        return raw if seen[raw] == 1 else f"{raw}#{seen[raw]}"

    out: list[Node] = []

    def walk(node: ast.AST, owner: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                fqn = f"{mod}.{child.name}" if owner is None else f"{owner}.{child.name}"
                s, e = span(child)
                out.append(Node(addr(f"class:{fqn}"), "symbol", "byte", s, e, child.name))
                walk(child, fqn)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                s, e = span(child)
                raw = (f"method:{owner}.{child.name}" if owner
                       else f"function:{mod}.{child.name}")
                out.append(Node(addr(raw), "symbol", "byte", s, e, child.name))
                walk(child, owner)
            elif isinstance(child, ast.Assign) and owner is None:
                for t in child.targets:
                    if isinstance(t, ast.Name) and t.id.isupper():
                        s, e = span(child)
                        out.append(Node(addr(f"const:{mod}.{t.id}"), "symbol",
                                        "byte", s, e, t.id))
            else:
                walk(child, owner)

    walk(tree, None)
    out.sort(key=lambda n: (n.start, n.address))
    return out
