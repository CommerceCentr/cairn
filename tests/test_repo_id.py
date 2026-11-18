"""Тотожність репозиторію: чи розводить вона форки зі спільним зародком.

Привід виміряний, не вигаданий: два справжні продукти мають однаковий
ПЕРШИЙ коміт і нуль спільних комітів у HEAD — один колись відгалужували
від другого. Стара редакція ключа склеювала їх в одну тотожність.

Репозиторії тест не знає наперед і НЕ зберігає чиїхось шляхів: їх
передають аргументами або змінною `CAIRN_TEST_REPOS` (роздільник —
системний, `;` у Windows, `:` в Unix):

    python tests/test_repo_id.py <repo1> <repo2> [<repo3> …]

Потрібно щонайменше два: тест перевіряє, що РІЗНІ проєкти дістають різні
тотожності, а на одному репозиторії таке твердження беззмістовне. Без
них тест пропускається — це чесніше, ніж вдавати, що він щось перевірив.
"""

from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.console import setup as _console_setup  # noqa: E402
_console_setup()

from core import paths as P  # noqa: E402

#: Жодного вшитого шляху: публічний репозиторій не має нести мапу
#: чужого диска. Аргументи мають перевагу над змінною середовища.
REPOS = sys.argv[1:] or [
    p for p in os.environ.get("CAIRN_TEST_REPOS", "").split(os.pathsep) if p
]


def old_id(root: str) -> str | None:
    """Редакція ДО правки: лише НАЙМЕНШИЙ корінь. Для реверсу."""
    fp = P.fingerprint(root)
    return fp[0] if fp else None


def main() -> int:
    present = [r for r in REPOS if os.path.isdir(r)]
    if len(present) < 2:
        print("пропущено: репозиторіїв для порівняння немає на цій машині")
        return 0

    print(f"{'проєкт':<22} {'СТАРИЙ ключ':<14} {'НОВИЙ ключ':<14} коренів")
    old: dict[str, list[str]] = {}
    new: dict[str, list[str]] = {}
    for r in present:
        root = P.repo_root(r)
        if not root:
            continue
        o = old_id(root) or "—"
        n = P.repo_id(root) or "—"
        fp = P.fingerprint(root) or ()
        old.setdefault(o, []).append(os.path.basename(r))
        new.setdefault(n, []).append(os.path.basename(r))
        print(f"{os.path.basename(r):<22} {o[:12]:<14} {n[:12]:<14} "
              f"{len(fp)}: {' '.join(c[:8] for c in fp)}")

    print()
    o_coll = {k: v for k, v in old.items() if len(v) > 1}
    n_coll = {k: v for k, v in new.items() if len(v) > 1}
    print(f"РЕВЕРС  старий ключ склеював: {o_coll or 'нікого'}")
    print(f"        новий ключ склеює:    {n_coll or 'нікого'}")

    # Позитивний контроль: той самий репозиторій, записаний інакше,
    # мусить дати ТОЙ САМИЙ ключ. Інакше ми просто зробили ключ випадковим.
    a = P.repo_id(P.repo_root(present[0]) or present[0])
    b = P.repo_id(P.repo_root(os.path.join(present[0], ".")) or present[0])
    stable = a == b
    print(f"        стабільність ключа:   {'ok' if stable else 'ПРОВАЛ'}")

    # Негативний контроль приладу: явно різні входи — різні ключі.
    h1 = hashlib.sha256(b"a\nb").hexdigest()[:40]
    h2 = hashlib.sha256(b"a\nc").hexdigest()[:40]
    print(f"        прилад розрізняє:     {'ok' if h1 != h2 else 'СЛІПИЙ'}")

    ok = not n_coll and stable and h1 != h2
    print()
    print("ВЕРДИКТ:", "тотожність розводить проєкти" if ok else "Є ПРОБЛЕМИ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
