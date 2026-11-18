"""Точність видобувача вузлів — проти ЧУЖОГО індексу, не проти себе.

Еталон тут незалежний: індекс, побудований іншим інструментом, іншою
мовою, іншою людиною. Якщо наш видобувач дає ті самі адреси `class:` і
`method:`, то зв'язка «вузол:метод» збережена, і це не наша думка, а збіг
із зовнішнім джерелом.

Запуск:
    python tests/bench_extract.py <heat.tsv> <корінь дерева> [скільки файлів]

Вивід ЗНЕОСОБЛЕНИЙ: лише числа й частки. Жодного імені файла чи класу,
щоб результат можна було показати стороннім.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.console import setup as _console_setup  # noqa: E402
_console_setup()

from extract import nodes_of  # noqa: E402

KINDS = ("class:", "method:")


def load_heat(path: str) -> dict[str, set[str]]:
    """{відносний шлях -> множина адрес}. Формат виміряний."""
    out: dict[str, set[str]] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            col = ln.rstrip("\n").split("\t")
            if len(col) < 5:
                continue
            addrs = set()
            for chunk in col[4].split(";"):
                a = chunk.split("|")[0]
                if a and a.startswith(KINDS):
                    addrs.add(a)
            if addrs:
                out[col[0].replace("\\", "/")] = addrs
    return out


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    heat_path, root = sys.argv[1], sys.argv[2]
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 120

    heat = load_heat(heat_path)
    if not heat:
        print("еталонний індекс порожній — порівнювати нема з чим")
        return 2

    files = 0
    exact = 0
    tp = fp = fn = 0
    empty_ours = 0

    for rel, want in sorted(heat.items()):
        if files >= limit:
            break
        full = os.path.join(root, rel)
        if not os.path.isfile(full):
            continue
        files += 1

        got = {n.address for n in nodes_of(full) if n.address.startswith(KINDS)}
        if not got:
            empty_ours += 1

        tp += len(got & want)
        fp += len(got - want)
        fn += len(want - got)
        if got == want:
            exact += 1

    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0

    print(f"файлів звірено:            {files}")
    print(f"повний збіг набору адрес:  {exact} ({100 * exact / files if files else 0:.1f}%)")
    print(f"адрес знайдено правильно:  {tp}")
    print(f"зайвих (у нас є, в них ні): {fp}")
    print(f"пропущених (в них є, у нас ні): {fn}")
    print(f"наш видобувач не дав нічого: {empty_ours} файлів")
    print()
    print(f"точність (precision): {prec:.3f}")
    print(f"повнота  (recall):    {rec:.3f}")
    print(f"F1:                   {f1:.3f}")
    print()
    print("МЕЖА: еталон теж не є істиною — він побудований іншим")
    print("      інструментом, який має власні пропуски. Розбіжність")
    print("      означає «розійшлися», а не «ми помилились».")
    return 0


if __name__ == "__main__":
    sys.exit(main())
