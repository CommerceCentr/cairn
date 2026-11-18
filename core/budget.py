"""Бюджет виводу: оцінка НАПЕРЕД, потім рішення, і межа, що тримається.

Причина існування цього модуля виміряна на чужому інструменті, який
робить те саме: з 16 прогонів бюджет було перевищено у 14, найгірше —
у 15.6 разу (замовлено 2 000, видано 31 224). Інструмент, що мовчки
видає пʼятнадцять бюджетів, не можна ставити в конвеєр із обмеженим
вікном: агент не може спланувати те, чого не знає наперед.

Тому порядок тут обернений до звичайного:

    1  ОЦІНИТИ, скільки вийде, НЕ будуючи вивід
    2  порівняти з бюджетом
    3  застосувати названу політику
    4  і лише тоді віддати

ТРИ ПОЛІТИКИ, бо випадки різні:

    trim   обрізати до межі й СКАЗАТИ, що саме не влізло. Умовчання:
           передбачуваність важливіша за повноту, а пропущене видно
    ask    спитати користувача, показавши оцінку. Для дорогих запитів,
           де рішення не наше
    allow  пропустити понад межу, але надрукувати перевищення. Коли
           повнота важливіша за ціну, і це свідомий вибір

ОДИНИЦЯ — СИМВОЛИ, не токени. Токенізатора під рукою немає, а називати
оцінку токенами означало б видати здогад за вимір. Приблизне число
токенів друкується окремо й позначене як приблизне.

ЩЕ ОДНА МЕЖА, названа відразу: символ не дорівнює байту. Кирилиця в
UTF-8 займає два байти, тож вивід на 10 000 символів може важити 20 000
байтів. Бюджет рахує САМЕ СИМВОЛИ, бо токенізація ближча до символів,
ніж до байтів. Хто міряє вивід у байтах, побачить більше — і це не
перевищення межі, а інша одиниця.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Скільки символів на токен. Груба стала для ОРІЄНТИРУ, не для обіцянок.
CHARS_PER_TOKEN = 4

POLICIES = ("trim", "ask", "allow")

#: Типові бюджети за пріоритетом задачі. Агент обирає пріоритет, а не
#: число: число залежить від моделі й вікна, пріоритет — від задачі.
PRIORITY = {
    "low": 2_000,        # побіжно глянути
    "normal": 8_000,     # звичайна правка
    "high": 24_000,      # розбір складного місця
    "max": 100_000,      # усе, що є
}


@dataclass
class Estimate:
    """Скільки вийде, якщо нічого не різати."""

    chars: int
    items: int
    detail: dict

    @property
    def tokens(self) -> int:
        return self.chars // CHARS_PER_TOKEN


@dataclass
class Decision:
    """Що робимо з оцінкою. `allowed_chars` — тверда межа для виводу."""

    policy: str
    budget: int
    estimate: Estimate
    fits: bool
    allowed_chars: int
    note: str

    @property
    def overflow(self) -> float:
        return self.estimate.chars / self.budget if self.budget else 0.0


def budget_for(priority: str | None, explicit: int | None) -> int:
    """Явне число перемагає пріоритет; пріоритет перемагає умовчання."""
    if explicit is not None and explicit > 0:
        return explicit
    if priority and priority in PRIORITY:
        return PRIORITY[priority]
    return PRIORITY["normal"]


def estimate(parts: list[tuple[str, int]]) -> Estimate:
    """Оцінка за переліком (назва, довжина) БЕЗ побудови виводу.

    Саме «без побудови» робить її корисною: якщо спершу зібрати все, а
    потім зміряти, то дорогу роботу вже виконано.
    """
    total = sum(max(0, n) for _, n in parts)
    detail: dict[str, int] = {}
    for name, n in parts:
        detail[name] = detail.get(name, 0) + max(0, n)
    return Estimate(chars=total, items=len(parts), detail=detail)


def decide(est: Estimate, budget: int, policy: str = "trim") -> Decision:
    if policy not in POLICIES:
        raise ValueError(f"невідома політика {policy!r}, дозволені {POLICIES}")

    fits = est.chars <= budget
    if fits:
        return Decision(policy, budget, est, True, est.chars, "у межах")

    if policy == "allow":
        return Decision(policy, budget, est, False, est.chars,
                        f"ПОНАД МЕЖУ ×{est.chars / budget:.1f} — дозволено явно")
    if policy == "ask":
        return Decision(policy, budget, est, False, 0,
                        f"потрібне рішення: оцінка {est.chars} символів "
                        f"(~{est.tokens} токенів) проти бюджету {budget}")
    return Decision(policy, budget, est, False, budget,
                    f"обрізано до {budget} із {est.chars}")


def fit(blocks: list[tuple[str, str]], allowed: int) -> tuple[list[tuple[str, str]], list[str]]:
    """Відібрати блоки, що вміщаються. Повертає (взяті, назви пропущених).

    Блоки беруться В ПОРЯДКУ подання: першим іде те, що вважається
    важливішим. Частковий блок не віддається — обрізаний посередині текст
    гірший за його відсутність, бо виглядає повним.
    """
    taken: list[tuple[str, str]] = []
    skipped: list[str] = []
    used = 0
    for name, text in blocks:
        need = len(text)
        if used + need <= allowed:
            taken.append((name, text))
            used += need
        else:
            skipped.append(name)
    return taken, skipped


def report(dec: Decision) -> str:
    """Рядок для людини й для журналу. Завжди друкується — межа, про яку
    не сказали, нічим не краща за її відсутність."""
    e = dec.estimate
    head = (f"БЮДЖЕТ {dec.budget} символів (~{dec.budget // CHARS_PER_TOKEN} токенів) · "
            f"оцінка {e.chars} (~{e.tokens}) · політика {dec.policy}")
    return head + "\n  " + dec.note
