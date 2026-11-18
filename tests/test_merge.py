"""Синхронізація між пристроями: що МУСИТЬ статися і що НЕ мусить.

Сценарій, заради якого це існує: архіваріус перенесено на інший пристрій,
де сесій більше, менше або частково ті самі. Після злиття має вийти
обʼєднання без дублів, і повторне злиття нічого не змінює.

Перелік обходу написано ДО перевірки:
    злиття часткового перекриття     -> обʼєднання, не сума
    повторне злиття того самого      -> нуль доданих
    злиття порожнього сховища        -> нуль доданих, не падіння
    запис, що не проходить контракт  -> ВІДХИЛЕНО, не прийнято мовчки
    той самий запис із іншим `turn`  -> НЕ дублюється (ключ за event_id)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.console import setup as _console_setup  # noqa: E402
_console_setup()

from core.store import Store  # noqa: E402


def rec(i: int, *, repo="r1", sess="s1", turn=None, eid=None, paths=None):
    out = {
        "repo_id": repo, "session_id": sess, "turn": turn if turn is not None else i,
        "ts": f"2026-11-18T10:{i:02d}:00Z", "actor": "tool", "kind": "tool_call",
        "paths": paths if paths is not None else [f"src/file{i}.py"],
    }
    if eid is not None:
        out["event_id"] = eid
    return out


def main() -> int:
    bad = 0
    tmp = tempfile.mkdtemp(prefix="cairn-merge-")
    try:
        a = Store(os.path.join(tmp, "device-a"))
        b = Store(os.path.join(tmp, "device-b"))

        # Пристрій A: події 1..5. Пристрій B: 4..8 (перекриття 4,5).
        a.append([rec(i, eid=f"e{i}") for i in range(1, 6)])
        b.append([rec(i, eid=f"e{i}") for i in range(4, 9)])
        print(f"A: {a.stats()['records']} записів, B: {b.stats()['records']}")

        res = a.merge_from(b.root)
        n = a.stats()["records"]
        want = 8
        ok = n == want and res["added"] == 3 and res["duplicates"] == 2
        bad += 0 if ok else 1
        print(f"злиття: додано {res['added']}, вже було {res['duplicates']}, "
              f"стало {n} (мусить бути {want}) -> {'ok' if ok else 'ПРОВАЛ'}")

        # Повторне злиття: нічого не змінює.
        res2 = a.merge_from(b.root)
        ok2 = res2["added"] == 0 and a.stats()["records"] == want
        bad += 0 if ok2 else 1
        print(f"повторне злиття: додано {res2['added']} "
              f"-> {'ok' if ok2 else 'ПРОВАЛ'}")

        # Та сама подія з ІНШИМ порядковим номером: дубля бути не має,
        # бо ключ будується з event_id. Саме на цьому ламалася стара
        # редакція при інкрементному читанні.
        c = Store(os.path.join(tmp, "device-c"))
        c.append([rec(3, eid="e3", turn=999)])
        res3 = a.merge_from(c.root)
        ok3 = res3["added"] == 0
        bad += 0 if ok3 else 1
        print(f"той самий event_id, інший turn: додано {res3['added']} "
              f"-> {'ok' if ok3 else 'ПРОВАЛ'}")

        # Порожнє сховище: відповідь, а не виняток.
        e = Store(os.path.join(tmp, "device-empty"))
        open(e.records_path, "w", encoding="utf-8").close()
        res4 = a.merge_from(e.root)
        ok4 = res4["added"] == 0
        bad += 0 if ok4 else 1
        print(f"порожнє сховище: додано {res4['added']} "
              f"-> {'ok' if ok4 else 'ПРОВАЛ'}")

        # Непридатний запис мусить бути ВІДХИЛЕНИЙ, а не прийнятий.
        d = Store(os.path.join(tmp, "device-bad"))
        with open(d.records_path, "w", encoding="utf-8", newline="\n") as fh:
            broken = rec(42, eid="e42")
            broken["paths"] = ["C:/Users/x/leak.py"]   # абсолютний шлях
            fh.write(json.dumps(broken) + "\n")
            fh.write("{не json}\n")
        res5 = a.merge_from(d.root)
        ok5 = res5["added"] == 0 and res5["rejected"] == 2
        bad += 0 if ok5 else 1
        print(f"непридатні записи: додано {res5['added']}, "
              f"відхилено {res5['rejected']} -> {'ok' if ok5 else 'ПРОВАЛ'}")

        # ПОЗИТИВНИЙ КОНТРОЛЬ: прилад мусить уміти додавати. Без цього
        # усі нулі вище означали б лише, що merge нічого не робить.
        f = Store(os.path.join(tmp, "device-new"))
        f.append([rec(77, eid="e77")])
        res6 = a.merge_from(f.root)
        ok6 = res6["added"] == 1
        bad += 0 if ok6 else 1
        print(f"контроль непорожності: додано {res6['added']} "
              f"-> {'ok' if ok6 else 'ПРОВАЛ'}")

        print()
        print("ВЕРДИКТ:", "синхронізація коректна" if bad == 0 else f"ПРОВАЛІВ {bad}")
        return 0 if bad == 0 else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
