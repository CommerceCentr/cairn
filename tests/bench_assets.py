"""Покриття різнотипних активів: що саме ми вміємо адресувати.

Твердження «працює з документами й мультимедіа» без числа є обіцянкою.
Цей прогін відповідає на три питання, на які досі не було відповіді:

    1  яка ЧАСТКА реальних файлів дістає справжні вузли, а яка падає
       у `whole:` — тобто де проходить межа наших умінь;
    2  чи точний видобувач Python — його ніхто не міряв;
    3  чи точний видобувач Markdown — так само.

Вивід ЗНЕОСОБЛЕНИЙ: лише розширення, кількості й частки. Жодного імені
файла, класу чи заголовка, щоб результат можна було показати стороннім.

Запуск: python tests/bench_assets.py <корінь> [<корінь> …] [--limit=N]
"""

from __future__ import annotations

import ast
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.console import setup as _console_setup  # noqa: E402
_console_setup()

from extract import nodes_of  # noqa: E402

SKIP_DIRS = {".git", "node_modules", "vendor", "__pycache__", ".venv",
             "venv", "dist", "build", ".idea", ".claude"}


def walk(root: str, limit: int) -> list[str]:
    out: list[str] = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            out.append(os.path.join(base, f))
            if len(out) >= limit:
                return out
    return out


def py_truth(path: str) -> int:
    """Незалежний лічильник: класи й функції через ast напряму.

    Для Python еталон об'єктивний — розбирач мови у стандартній
    бібліотеці. Саме тому тут можна говорити про точність, а не лише
    про збіг із чужим індексом.
    """
    try:
        with open(path, "rb") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError, ValueError):
        return -1
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            n += 1
    return n


RE_HEAD = re.compile(rb"^#{1,6}[ \t]+\S", re.M)
RE_FENCE = re.compile(rb"^(```|~~~)", re.M)


def md_truth(path: str) -> int:
    """Незалежний лічильник заголовків, що НЕ лежать у блоці коду."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return -1
    spans = []
    open_at = None
    for m in RE_FENCE.finditer(data):
        if open_at is None:
            open_at = m.start()
        else:
            spans.append((open_at, m.end()))
            open_at = None
    if open_at is not None:
        spans.append((open_at, len(data)))
    n = 0
    for m in RE_HEAD.finditer(data):
        if not any(s <= m.start() < e for s, e in spans):
            n += 1
    return n


def main() -> int:
    roots = [a for a in sys.argv[1:] if not a.startswith("--")]
    limit = 4000
    for a in sys.argv[1:]:
        if a.startswith("--limit="):
            limit = int(a.split("=", 1)[1])
    if not roots:
        print(__doc__)
        return 2

    by_kind: collections.Counter = collections.Counter()
    by_ext_whole: collections.Counter = collections.Counter()
    files = 0
    nodes_total = 0

    py_files = py_ok = py_diff = 0
    py_ours = py_ref = 0
    md_files = md_ok = 0
    md_ours = md_ref = 0

    for root in roots:
        if not os.path.isdir(root):
            print(f"немає теки: {root}")
            continue
        for path in walk(root, limit):
            files += 1
            try:
                nodes = nodes_of(path)
            except Exception:
                by_kind["ПОМИЛКА"] += 1
                continue
            nodes_total += len(nodes)
            kinds = {n.kind for n in nodes}
            if kinds == {"whole"}:
                by_kind["whole"] += 1
                by_ext_whole[os.path.splitext(path)[1].lower() or "(без)"] += 1
            else:
                for k in kinds:
                    by_kind[k] += 1

            ext = os.path.splitext(path)[1].lower()
            if ext == ".py":
                ref = py_truth(path)
                if ref >= 0:
                    ours = sum(1 for n in nodes
                               if n.address.startswith(("class:", "method:", "function:")))
                    py_files += 1
                    py_ours += ours
                    py_ref += ref
                    if ours == ref:
                        py_ok += 1
                    else:
                        py_diff += 1
            elif ext in (".md", ".markdown"):
                ref = md_truth(path)
                if ref >= 0:
                    ours = sum(1 for n in nodes if n.kind == "section")
                    md_files += 1
                    md_ours += ours
                    md_ref += ref
                    if ours == ref:
                        md_ok += 1

    print("# Покриття різнотипних активів\n")
    print(f"файлів оглянуто: {files}")
    print(f"вузлів усього:   {nodes_total}\n")

    print("## 1. Який тип вузла дістав файл\n")
    print("| вид вузла | файлів | частка |")
    print("|---|---:|---:|")
    for k, n in by_kind.most_common():
        print(f"| {k} | {n} | {100 * n / files if files else 0:.1f}% |")

    print("\n## 2. Що саме падає у `whole:` (топ розширень)\n")
    print("| розширення | файлів |")
    print("|---|---:|")
    for e, n in by_ext_whole.most_common(12):
        print(f"| `{e}` | {n} |")
    print("\nЦе не дефект: `whole:` означає «адресуємо файлом», і на цьому")
    print("рівні працюють і сховище сесій, і запит. Дефектом було б падіння.")

    print("\n## 3. Python: проти ast як еталона\n")
    if py_files:
        print(f"| файлів | точний збіг | розійшлось | наших вузлів | еталон |")
        print(f"|---:|---:|---:|---:|---:|")
        print(f"| {py_files} | {py_ok} ({100 * py_ok / py_files:.1f}%) | "
              f"{py_diff} | {py_ours} | {py_ref} |")
        print(f"\nвідношення знайденого до еталона: "
              f"{py_ours / py_ref if py_ref else 0:.3f}")
    else:
        print("файлів .py не знайдено")

    print("\n## 4. Markdown: проти незалежного лічильника заголовків\n")
    if md_files:
        print(f"| файлів | точний збіг | наших розділів | еталон |")
        print(f"|---:|---:|---:|---:|")
        print(f"| {md_files} | {md_ok} ({100 * md_ok / md_files:.1f}%) | "
              f"{md_ours} | {md_ref} |")
    else:
        print("файлів .md не знайдено")

    print("\n## Межі цього прогону\n")
    print("* еталон для Python об'єктивний (розбирач мови), для Markdown —")
    print("  власний лічильник за тим самим правилом, тож він перевіряє")
    print("  узгодженість, а не істину;")
    print("* обхід обмежений, і теки збірок пропущено: числа описують")
    print("  вихідний код, а не весь диск.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
