"""Перевірка інваріантів сховища: нічого, що завадить перенесенню.

Запуск: python tests/check_store.py <тека сховища>

Це не юніт-тест, а ворота на реальних даних. Разом із кожною перевіркою
йде контроль, що перевірка взагалі бачить дефект: без нього «0 порушень»
не відрізняється від «нічого не перевірено».
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.console import setup as _console_setup  # noqa: E402
_console_setup()

from core import contract  # noqa: E402

LEAK_MARKERS = ("Users", "home/", ":/", ":\\")


def main(store: str) -> int:
    path = os.path.join(store, "records.jsonl")
    if not os.path.exists(path):
        print(f"сховища немає: {path}")
        return 2

    total = abs_paths = bad_records = 0
    leaks: dict[str, int] = {}

    for ln in open(path, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        total += 1
        rec = json.loads(ln)
        try:
            contract.check_record(rec, total)
        except contract.ContractError as exc:
            bad_records += 1
            if bad_records <= 3:
                print(f"  {exc}")
        for p in rec.get("paths", []):
            if contract.is_absolute(p):
                abs_paths += 1
            for m in LEAK_MARKERS:
                if m in p:
                    leaks[m] = leaks.get(m, 0) + 1

    print(f"записів:                 {total}")
    print(f"порушень контракту:      {bad_records}")
    print(f"абсолютних шляхів:       {abs_paths}")
    print(f"ознак машинних шляхів:   {leaks or 'немає'}")

    # Контроль: детектор МУСИТЬ ловити підробку. Без цього нулі вище
    # означали б лише те, що перевірка нічого не дивиться.
    probes = ["C:/Users/x/a.php", "/etc/passwd", "\\\\server\\share\\a",
              "D:\\work\\b.go"]
    caught = sum(1 for p in probes if contract.is_absolute(p))
    print(f"позитивний контроль:     {caught} з {len(probes)} підробок відхилено")
    neg = contract.is_absolute("core/Services/X.php")
    print(f"негативний контроль:     відносний шлях "
          f"{'ПОМИЛКОВО відхилено' if neg else 'пройшов — ok'}")

    ok = (bad_records == 0 and abs_paths == 0 and not leaks
          and caught == len(probes) and not neg)
    print("ВЕРДИКТ:", "сховище переносне" if ok else "Є ПРОБЛЕМИ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
