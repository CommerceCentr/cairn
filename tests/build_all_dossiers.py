"""Генерація компактованих досьє для чотирьох PHP-зон і 5-ї зони cairn."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import dossiers, history  # noqa: E402

HOME = Path(r"Q:\650\home")
OUT = Path(r"Q:\650\home\_archivist\packs_python")
ZONES = ("beta.loc", "cc.one", "community.loc", "cms-stage.loc")

#: 5-та зона: той самий конвеєр, але Python-шляхи й інший корінь.
#: `*.php` тут дав би 1 файл з 14 у коміті 8c1eea5 (виміряно).
EXTRA_ZONES = (("cairn", Path(r"Q:\prj\cairn"), "*.py"),)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for zone in ZONES:
        t0 = time.perf_counter()
        stats = dossiers.write_zone(HOME / zone, zone, OUT, min_events=3)
        pack = Path(stats["path"]).read_text(encoding="utf-8")
        parsed = history._parse_pack(pack)
        dossiers_count = sum(len(v) for v in parsed["dossiers"].values())
        print(f"{zone:<16} HEAD {stats['head']:<8} вузлів {stats['nodes']:<5} "
              f"EV {dossiers_count:<6} WAS {stats['was_blocks']:<5} "
              f"{stats['chars']/1e6:.2f} MB {stats['sha256'][:12]} "
              f"{'змінено' if stats['changed'] else 'без змін'} "
              f"за {(time.perf_counter()-t0):.1f}s")
    for zone, repo, pathspec in EXTRA_ZONES:
        t0 = time.perf_counter()
        stats = dossiers.write_zone(repo, zone, OUT, min_events=1,
                                    pathspec=pathspec)
        pack = Path(stats["path"]).read_text(encoding="utf-8")
        parsed = history._parse_pack(pack)
        dossiers_count = sum(len(v) for v in parsed["dossiers"].values())
        print(f"{zone:<16} HEAD {stats['head']:<8} вузлів {stats['nodes']:<5} "
              f"EV {dossiers_count:<6} WAS {stats['was_blocks']:<5} "
              f"{stats['chars']/1e6:.2f} MB {stats['sha256'][:12]} "
              f"{'змінено' if stats['changed'] else 'без змін'} "
              f"за {(time.perf_counter()-t0):.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
