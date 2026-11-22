"""Поверхні схеми: оголошене мусить збігатися з реальністю, в обидва боки.

Перед релізом виміряно: пʼять обробників проєкцій не мали жодного
продюсера подій, тож пʼять таблиць схеми лишалися порожніми назавжди. Зі
схеми це не видно: `usage_records` виглядає так, ніби облік витрат працює.

`ontology.RESERVED_SURFACES` оголошує такі таблиці з причиною. Цей тест
стереже контракт у ДВА боки, бо помилитися можна в обидва:

* таблиця без продюсера, якої немає в списку -> тиха порожня обіцянка;
* таблиця в списку, яка насправді заповнюється -> застаріла межа, що
  применшує те, що вже працює.

Перевірка йде по КОДУ (пошук продюсерів `store.append(..., "тип.події")`),
а не по вмісту конкретного сховища: порожня таблиця у свіжому сховищі
нічого не доводить.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import db, ontology  # noqa: E402

PKG = ROOT / "cairn_unified"

#: Таблиці, які заповнює не проєкція, а сам `Store.append`.
DIRECT_WRITES = {"events", "schema_meta"}


def handlers() -> dict[str, str]:
    """{тип події -> імʼя обробника} з реєстру проєкцій."""
    text = (PKG / "projections.py").read_text(encoding="utf-8")
    return dict(re.findall(r'"([a-z_]+\.[a-z_]+)":\s*(_\w+)', text))


def produced_event_types() -> set[str]:
    """Типи подій, які код справді десь створює."""
    found: set[str] = set()
    for path in PKG.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        found.update(re.findall(r'append\([^,]+,\s*"([a-z_]+\.[a-z_]+)"', text))
        found.update(re.findall(r'"event_type":\s*"([a-z_]+\.[a-z_]+)"', text))
    return found


def _bodies() -> dict[str, str]:
    """{імʼя функції -> її текст} у модулі проєкцій."""
    text = (PKG / "projections.py").read_text(encoding="utf-8")
    blocks = re.split(r"\ndef (\w+)\(", text)
    return {blocks[i]: blocks[i + 1] for i in range(1, len(blocks), 2)}


def tables_written_by(handler_names: set[str]) -> set[str]:
    """Таблиці, у які пишуть ці обробники — прямо або через помічників.

    Перша версія дивилась лише в тіло обробника й оголосила
    `search_fallback` мертвою: її заповнює помічник `index_text`, якого
    викликає `_object`. Дефект був у парсері, не в схемі, тому йдемо на
    рівень глибше за викликами всередині модуля.
    """
    bodies = _bodies()
    seen: set[str] = set()
    queue = list(handler_names)
    out: set[str] = set()
    while queue:
        name = queue.pop()
        if name in seen or name not in bodies:
            continue
        seen.add(name)
        body = bodies[name]
        out.update(re.findall(r"INSERT (?:OR REPLACE )?INTO (\w+)", body))
        for called in re.findall(r"\b(\w+)\(", body):
            if called in bodies and called not in seen:
                queue.append(called)
    return out


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    registry = handlers()
    produced = produced_event_types()
    orphan_handlers = {event for event in registry if event not in produced}
    live_handlers = {registry[e] for e in registry if e in produced}

    schema_tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", db.SCHEMA))
    live_tables = tables_written_by(live_handlers) | DIRECT_WRITES
    dead_tables = {t for t in schema_tables if t not in live_tables}
    declared = set(ontology.RESERVED_SURFACES)

    checks.append(("реєстр проєкцій не порожній", len(registry) > 10, str(len(registry))))
    checks.append(("кожна оголошена поверхня має причину",
                   all(str(v).strip() for v in ontology.RESERVED_SURFACES.values()),
                   str(len(declared))))

    missing = sorted(dead_tables - declared)
    checks.append(("немає НЕоголошених порожніх таблиць", not missing,
                   str(missing) or "жодної"))

    stale = sorted(declared - dead_tables)
    checks.append(("немає застарілих оголошень", not stale,
                   str(stale) or "жодного"))

    checks.append(("усі оголошені є в схемі",
                   declared <= schema_tables,
                   str(sorted(declared - schema_tables)) or "так"))

    # Обробники-сироти мусять відповідати оголошеним таблицям, інакше в
    # реєстрі лежить код, який ніколи не викличеться, і про це ніде не
    # сказано.
    checks.append(("обробники-сироти пояснені оголошеними таблицями",
                   bool(orphan_handlers) == bool(declared),
                   f"сиріт {len(orphan_handlers)}, оголошено {len(declared)}"))

    # `doctor` мусить це показувати: інакше контракт існує лише в коді.
    import shutil
    import tempfile

    from cairn_unified.store import Store

    temp = Path(tempfile.mkdtemp(prefix="surfaces-"))
    try:
        Store(temp).init()
        report = Store.open(temp).doctor()
        checks.append(("doctor показує reserved_surfaces",
                       set(report.get("reserved_surfaces") or {}) == declared,
                       str(sorted(report.get("reserved_surfaces") or {}))[:40]))
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    good = 0
    for label, ok, detail in checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {label:48} | {detail[:38]}")
        good += int(ok)
    print(f"ВЕРДИКТ: поверхні схеми оголошені чесно ({good}/{len(checks)})")
    return 0 if good == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
