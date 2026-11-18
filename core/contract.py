"""Контракт запису — єдине, чого інструмент вимагає від будь-якого агента.

Адаптер під конкретного агента знає СВІЙ формат і більше нічого. Усе, що
він мусить віддати — послідовність записів цієї форми. Перевірку робить
core.selftest, і поки вона не зелена, адаптера не існує.

Чому саме ці поля, а не більше: все, що тут є, можна дістати з будь-якого
журналу розмови. Усе, чого тут немає (вміст повідомлень, токени, моделі),
або не переноситься між агентами, або небезпечне.
"""

from __future__ import annotations

ACTORS = ("user", "agent", "tool")
KINDS = ("message", "tool_call", "compact_summary")

#: Поля, без яких запис не є записом.
REQUIRED = ("repo_id", "session_id", "turn", "ts", "actor", "kind", "paths")


class ContractError(ValueError):
    """Запис не відповідає контракту. Повідомлення каже, чим саме."""


def check_record(rec: object, index: int = -1) -> None:
    """Одна перевірка одного запису. Кидає ContractError або мовчить.

    Навмисно БЕЗ «м'якого» режиму: запис або придатний, або ні. М'який
    режим перетворив би ворота на пораду.
    """
    where = f"запис #{index}" if index >= 0 else "запис"

    if not isinstance(rec, dict):
        raise ContractError(f"{where}: не словник, а {type(rec).__name__}")

    for key in REQUIRED:
        if key not in rec:
            raise ContractError(f"{where}: немає поля {key!r}")

    if not isinstance(rec["repo_id"], str) or not rec["repo_id"]:
        raise ContractError(f"{where}: repo_id порожній")

    if not isinstance(rec["session_id"], str) or not rec["session_id"]:
        raise ContractError(f"{where}: session_id порожній")

    if not isinstance(rec["turn"], int) or rec["turn"] < 0:
        raise ContractError(f"{where}: turn мусить бути невід'ємним цілим")

    if not isinstance(rec["ts"], str) or len(rec["ts"]) < 10:
        raise ContractError(f"{where}: ts мусить бути рядком часу UTC")

    if rec["actor"] not in ACTORS:
        raise ContractError(f"{where}: actor={rec['actor']!r}, дозволені {ACTORS}")

    if rec["kind"] not in KINDS:
        raise ContractError(f"{where}: kind={rec['kind']!r}, дозволені {KINDS}")

    paths = rec["paths"]
    if not isinstance(paths, list):
        raise ContractError(f"{where}: paths мусить бути списком")

    for p in paths:
        if not isinstance(p, str) or not p:
            raise ContractError(f"{where}: порожній шлях у paths")
        if is_absolute(p):
            raise ContractError(
                f"{where}: АБСОЛЮТНИЙ шлях {p!r}. Сховище з абсолютними "
                f"шляхами не переноситься між машинами — це його сенс"
            )

    # Текст дозволений ЛИШЕ згорткам компакції: вони вже є стиснутим
    # «чому», написаним агентом. Сире листування у сховище не йде.
    text = rec.get("text")
    if text is not None:
        if rec["kind"] != "compact_summary":
            raise ContractError(
                f"{where}: text дозволений лише для compact_summary, "
                f"а тут kind={rec['kind']!r}"
            )
        if not isinstance(text, str):
            raise ContractError(f"{where}: text мусить бути рядком")
        # Ворота, а не прибирання: якщо сканер щось БАЧИТЬ, запис не
        # приймається. Чистити мовчки означало б покладатись на те, що
        # редактор нічого не пропустив.
        from . import redact
        hits = redact.find_secrets(text)
        if hits:
            kinds = sorted({n for n, _ in hits})
            raise ContractError(
                f"{where}: у тексті лишились нередаговані дані {kinds}. "
                f"Запис відхилено — текст мусить пройти redact ДО сховища"
            )


def is_absolute(p: str) -> bool:
    """Абсолютний шлях у будь-якій із систем, не лише в поточній.

    os.path.isabs знає тільки свою платформу: на Linux 'C:/x' не
    абсолютний, і запис із Windows проліз би крізь ворота.
    """
    if p.startswith("/") or p.startswith("\\"):
        return True
    if len(p) >= 2 and p[1] == ":" and p[0].isalpha():
        return True
    return p.startswith("\\\\")
