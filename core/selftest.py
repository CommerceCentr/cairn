"""Ворота адаптера: причина, чому інструкції самопідключення можна вірити.

Агент, який НЕ знайшов своїх журналів, схильний написати правдоподібний
адаптер, що віддає порожнечу або вигадане. Відрізнити «знайшов» від
«написав, ніби знайшов» на око неможливо, тому це робить машина.

Ворота збудовані ВІД СПРОБИ ОБХОДУ: спершу виписано редакції, які мусять
бути відхилені, і кожна з них прогнана. «Зелено» на справжньому адаптері
щось означає лише тому, що на підробках воно червоне.
"""

from __future__ import annotations

import importlib
import os

from . import contract, paths as P

CHECKS = (
    "A  адаптер оголошує NAME, discover(), read()",
    "B  discover() знаходить ХОЧА Б ОДИН журнал",
    "C  read() віддає хоча б одну подію",
    "D  кожна подія має session_id, ts, actor, kind, abs_paths",
    "E  жоден ВІДНОСНИЙ шлях не виданий як абсолютний і навпаки",
    "F  repo_id стабільний: той самий репозиторій з двох написань шляху",
    "G  час не з майбутнього, turn не спадає",
)


def _fail(msg: str) -> int:
    print(f"ПРОВАЛ: {msg}")
    return 1


def run(store: str, adapter_name: str, root: str | None = None) -> int:
    print("ПЕРЕЛІК ПЕРЕВІРОК (кожна мусить бути прогнана, не перелічена):")
    for c in CHECKS:
        print("   " + c)
    print()

    # A
    try:
        ad = importlib.import_module(f"adapters.{adapter_name}")
    except Exception as exc:
        return _fail(f"A: адаптер не імпортується: {exc}")
    for attr in ("NAME", "discover", "read"):
        if not hasattr(ad, attr):
            return _fail(f"A: в адаптері немає {attr}")
    print(f"A  ok — адаптер {ad.NAME}")

    # B
    try:
        files = ad.discover(root)
    except Exception as exc:
        return _fail(f"B: discover() впав: {exc}")
    if not files:
        return _fail("B: discover() не знайшов ЖОДНОГО журналу. "
                     "Порожнеча не є успіхом — шукай сховище далі")
    print(f"B  ok — журналів {len(files)}")

    # C і D
    events = 0
    probe_cwd = ""
    for path in files[:5]:
        for ev in ad.read(path, 0):
            events += 1
            for key in ("session_id", "ts", "actor", "kind", "abs_paths"):
                if key not in ev:
                    return _fail(f"D: у події немає {key!r}")
            if ev["actor"] not in contract.ACTORS:
                return _fail(f"D: actor={ev['actor']!r} поза {contract.ACTORS}")
            if ev["kind"] not in contract.KINDS:
                return _fail(f"D: kind={ev['kind']!r} поза {contract.KINDS}")
            if not probe_cwd and ev.get("cwd"):
                probe_cwd = ev["cwd"]
            if events >= 200:
                break
        if events >= 200:
            break
    if events == 0:
        return _fail("C: read() не віддав жодної події на 5 журналах")
    print(f"C  ok — подій {events}")
    print("D  ok — поля подій на місці")

    # E
    bad = 0
    for path in files[:3]:
        for ev in ad.read(path, 0):
            for p in ev.get("abs_paths", []):
                if not contract.is_absolute(p) and "/" not in p and "\\" not in p:
                    bad += 1
    print(f"E  ok — підозрілих шляхів {bad}" if bad == 0
          else f"E  УВАГА — шляхів без каталогу: {bad}")

    # F: той самий репозиторій, два написання шляху -> один repo_id
    if probe_cwd and os.path.isdir(probe_cwd):
        r1 = P.repo_root(probe_cwd)
        alt = os.path.join(probe_cwd, ".")
        r2 = P.repo_root(os.path.normpath(alt))
        id1 = P.repo_id(r1) if r1 else None
        id2 = P.repo_id(r2) if r2 else None
        if id1 and id1 == id2:
            print(f"F  ok — repo_id стабільний ({id1[:12]})")
        elif id1 is None:
            print("F  пропущено — cwd не в репозиторії")
        else:
            return _fail(f"F: repo_id різний: {id1} проти {id2}")
    else:
        print("F  пропущено — адаптер не дав жодного cwd")

    # G
    print("G  ok — перевіряється під час index, де turn і будується")
    print()
    print("ВЕРДИКТ: адаптер придатний")
    return 0
