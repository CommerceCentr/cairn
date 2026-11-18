"""Колізія відносних шляхів між репозиторіями.

Привід виміряний: ядро розкладене копіями по продуктах, тож
`core/Services/Routing/Router.php` існує в кількох репозиторіях. Доки
вибірка йшла ЗА РЯДКОМ ШЛЯХУ, зона beta.loc без жодної сесії показувала
5 із 8 покритих вузлів — дотики належали cc.one і community.loc.

Перелік обходу:
    два репо, однаковий шлях -> кожному СВОЇ сесії, не спільні
    репо без записів         -> порожньо, не чужі дані
    згортки чужого репо      -> не рахуються
    контроль непорожності    -> свій репо таки дає непорожнє
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.console import setup as _console_setup  # noqa: E402
_console_setup()

from core.store import Store  # noqa: E402

SHARED = "core/Services/Routing/Router.php"


def rec(repo, sess, eid, kind="tool_call", paths=(SHARED,)):
    return {"repo_id": repo, "session_id": sess, "turn": 0, "event_id": eid,
            "ts": "2026-11-18T10:00:00Z", "actor": "tool", "kind": kind,
            "paths": list(paths)}


def main() -> int:
    bad = 0
    tmp = tempfile.mkdtemp(prefix="cairn-scope-")
    try:
        s = Store(os.path.join(tmp, "store"))
        s.append([
            rec("repoA", "sA1", "a1"),
            rec("repoA", "sA2", "a2"),
            rec("repoA", "sA1", "a3", kind="compact_summary", paths=()),
            rec("repoB", "sB1", "b1"),
            rec("repoB", "sB1", "b2", kind="compact_summary", paths=()),
            rec("repoB", "sB2", "b3", kind="compact_summary", paths=()),
        ])

        ta, sua = s.touched("repoA")
        tb, sub = s.touched("repoB")
        tc, suc = s.touched("repoC-якого-немає")

        checks = [
            ("A бачить свої 2 сесії", len(ta.get(SHARED, set())) == 2),
            ("B бачить свою 1 сесію", len(tb.get(SHARED, set())) == 1),
            ("A не бачить згорток B", sum(sua.values()) == 1),
            ("B не бачить згорток A", sum(sub.values()) == 2),
            ("невідомий репо порожній", not tc and not suc),
            ("контроль непорожності", bool(ta) and bool(tb)),
        ]
        for name, ok in checks:
            bad += 0 if ok else 1
            print(f"  {'ok ' if ok else 'ПРОВАЛ'} {name}")

        # Головне: сума по репозиторіях НЕ дорівнює вибірці за рядком.
        naive = set()
        for r in s.iter_records():
            if SHARED in r.get("paths", []):
                naive.add(r["session_id"])
        ok = len(naive) == 3 and len(ta.get(SHARED, set())) == 2
        bad += 0 if ok else 1
        print(f"  {'ok ' if ok else 'ПРОВАЛ'} вибірка за рядком дала б "
              f"{len(naive)} сесій замість {len(ta.get(SHARED, set()))}")

        print()
        print("ВЕРДИКТ:", "межі репозиторіїв дотримані" if bad == 0
              else f"ПРОВАЛІВ {bad}")
        return 0 if bad == 0 else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
