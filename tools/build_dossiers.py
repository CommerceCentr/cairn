"""Генерація компактованих досьє для зон, заданих АРГУМЕНТАМИ.

Був `tests/build_all_dossiers.py` — не тест, а генератор із побічними
ефектами, прописаний на приватний диск автора (чотири
реальні імена зон). У теці `tests/` він створював враження перевірки,
хоча нічого не перевіряв, і на будь-якій іншій машині падав.

Тепер шляхи приходять аргументами, і нічого не припускається:

    python tools/build_dossiers.py --out ./packs \\
        --zone sample=../sample-project \\
        --zone cairn=.:*.py

Формат `--zone <імʼя>=<шлях>[:<pathspec>]`. `pathspec` за замовчуванням
`*.php`; для Python-зони його треба задати явно — виміряно, що `*.php`
на зоні `cairn` давав 1 файл із 14 у коміті 8c1eea5.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import dossiers, history  # noqa: E402


def parse_zone(raw: str) -> tuple[str, Path, str, int]:
    """`імʼя=шлях[:pathspec]` -> (імʼя, шлях, pathspec, min_events)."""
    if "=" not in raw:
        raise argparse.ArgumentTypeError(f"очікується імʼя=шлях, отримано {raw!r}")
    name, _, rest = raw.partition("=")
    pathspec = "*.php"
    # Розділювач pathspec шукаємо з кінця й лише після роздільника шляху,
    # щоб не зіпсувати літеру диска й двокрапку на Windows.
    head, sep, tail = rest.rpartition(":")
    if sep and ("*" in tail or "." in tail) and "/" not in tail and "\\" not in tail:
        rest, pathspec = head, tail
    path = Path(rest).expanduser()
    if not path.exists():
        raise argparse.ArgumentTypeError(f"зони {name}: немає {path}")
    min_events = 1 if pathspec.endswith(".py") else 3
    return name, path, pathspec, min_events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_dossiers")
    parser.add_argument("--out", required=True, help="тека для пакетів")
    parser.add_argument("--zone", action="append", required=True, type=parse_zone,
                        metavar="NAME=PATH[:PATHSPEC]")
    parser.add_argument("--min-events", type=int, default=None,
                        help="перекрити мінімум подій на вузол")
    args = parser.parse_args(argv)

    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    for name, path, pathspec, default_min in args.zone:
        start = time.perf_counter()
        stats = dossiers.write_zone(path, name, out,
                                    min_events=args.min_events or default_min,
                                    pathspec=pathspec)
        parsed = history._parse_pack(Path(stats["path"]).read_text(encoding="utf-8"))
        entries = sum(len(v) for v in parsed["dossiers"].values())
        print(f"{name:<16} HEAD {stats['head']:<8} вузлів {stats['nodes']:<5} "
              f"EV {entries:<6} WAS {stats['was_blocks']:<5} "
              f"{stats['chars'] / 1e6:.2f} MB {stats['sha256'][:12]} "
              f"{'змінено' if stats['changed'] else 'без змін'} "
              f"за {time.perf_counter() - start:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
