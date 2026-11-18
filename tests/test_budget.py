"""Бюджет: перелік обходу, написаний ДО перевірки.

Привід виміряний на чужому інструменті, який робить те саме: з 16
прогонів бюджет перевищено у 14, найгірше в 15.6 разу. Тому тут
перевіряється не «працює», а саме те, що там зламалось:

    межа тримається НА ВСІХ бюджетах        -> вивід ніколи не більший
    коли вміщається — нічого не ріжеться    -> інакше межа псує корисне
    політика ask без згоди нічого не дає    -> рішення лишається за людиною
    політика allow пропускає, але КАЖЕ      -> мовчазне перевищення гірше
    частковий блок не віддається            -> обрізаний текст виглядає
                                               повним і тому бреше
    пріоритет дає число, якщо його не дали
    контроль непорожності: щось таки видано

Останній рядок обовʼязковий: без нього «нічого не перевищило» означало б
лише, що інструмент не друкує нічого.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.console import setup as _console_setup  # noqa: E402
_console_setup()

from core import budget  # noqa: E402


def blocks(n: int, size: int) -> list[tuple[str, str]]:
    return [(f"b{i}", "x" * size) for i in range(n)]


def main() -> int:
    bad = 0

    def check(name: str, ok: bool, extra: str = "") -> None:
        nonlocal bad
        if not ok:
            bad += 1
        print(f"  {'ok ' if ok else 'ПРОВАЛ'} {name}{(' — ' + extra) if extra else ''}")

    print("МЕЖА ТРИМАЄТЬСЯ НА ВСІХ БЮДЖЕТАХ:")
    bs = blocks(10, 1000)                       # 10 000 символів усього
    est = budget.estimate([(n, len(t)) for n, t in bs])
    check("оцінка точна", est.chars == 10000, f"{est.chars}")

    worst = 0.0
    for b in (100, 500, 1000, 2500, 5000, 9999, 10000, 20000):
        dec = budget.decide(est, b, "trim")
        taken, skipped = budget.fit(bs, dec.allowed_chars)
        out = sum(len(t) for _, t in taken)
        ratio = out / b
        worst = max(worst, ratio)
        check(f"budget={b:<6} видано={out:<6}", out <= b, f"×{ratio:.2f}")
    print(f"  найгірше відношення виданого до бюджету: ×{worst:.2f} "
          f"(у чужому інструменті ×15.6)")

    print("\nКОЛИ ВМІЩАЄТЬСЯ — НЕ РІЖЕМО:")
    dec = budget.decide(est, 50000, "trim")
    taken, skipped = budget.fit(bs, dec.allowed_chars)
    check("усі блоки віддано", len(taken) == 10 and not skipped)
    check("позначено «у межах»", dec.fits and dec.note == "у межах")

    print("\nПОЛІТИКИ:")
    d_ask = budget.decide(est, 1000, "ask")
    check("ask не дозволяє нічого без згоди", d_ask.allowed_chars == 0)
    d_allow = budget.decide(est, 1000, "allow")
    check("allow дозволяє все", d_allow.allowed_chars == est.chars)
    check("allow КАЖЕ про перевищення", "ПОНАД МЕЖУ" in d_allow.note,
          d_allow.note[:38])
    try:
        budget.decide(est, 1000, "вигадана")
        check("невідома політика відхиляється", False)
    except ValueError:
        check("невідома політика відхиляється", True)

    print("\nЧАСТКОВИЙ БЛОК НЕ ВІДДАЄТЬСЯ:")
    one = [("big", "y" * 5000)]
    e1 = budget.estimate([(n, len(t)) for n, t in one])
    d1 = budget.decide(e1, 1000, "trim")
    t1, s1 = budget.fit(one, d1.allowed_chars)
    check("великий блок пропущено цілком", t1 == [] and s1 == ["big"])

    print("\nПРІОРИТЕТ:")
    check("явне число перемагає", budget.budget_for("low", 777) == 777)
    check("пріоритет дає число", budget.budget_for("high", None) == budget.PRIORITY["high"])
    check("умовчання normal", budget.budget_for(None, None) == budget.PRIORITY["normal"])

    print("\nКОНТРОЛЬ НЕПОРОЖНОСТІ:")
    dec = budget.decide(est, 3500, "trim")
    taken, skipped = budget.fit(bs, dec.allowed_chars)
    check("щось таки видано", len(taken) == 3 and len(skipped) == 7,
          f"взято {len(taken)}, пропущено {len(skipped)}")

    print()
    print("ВЕРДИКТ:", "бюджет є межею" if bad == 0 else f"ПРОВАЛІВ {bad}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
