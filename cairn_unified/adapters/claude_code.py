"""ЕТАЛОННИЙ адаптер: Claude Code.

Це зразок для AGENT-ONBOARD.md, а не привілейований коннектор. Він
проходить ті самі ворота, що й адаптер, написаний чужим агентом, і
навмисно короткий: якщо адаптер не вміщується на екран, інструкція
«напиши собі такий самий» нічого не варта.

Формат виміряно 2026-11-18: ~/.claude/projects/<slug>/<session>.jsonl,
по рядку JSON на подію. Корисні поля: type, message.content[], uuid,
timestamp, sessionId, cwd, isCompactSummary.

Проза асистента НЕ читається: беремо лише шляхи з МУТАЦІЙНИХ tool_use
і позначку компакції. Виняток, доданий 2026-11-20 за уточненням користувача:
текст USER-повідомлення повертається як корінь «чому» (його намір); шляхів
він не дає. 2026-11-20 виміряно на 1096 журналах: Write 216
і Edit 61 змінюють файл, а Read 229, Grep 63, Glob 15 мають ті самі
поля шляху, але файл не змінюють. Старий фільтр за наявністю ключа
шляху давав 307 хибних «дотиків» на 277 справжніх мутацій. Bash (1800)
не має поля шляху, тож класифікувати його без читання command було б
вгадуванням — свідомо не робимо.
"""

from __future__ import annotations

import json
import os

PATH_KEYS = ("file_path", "notebook_path", "path", "filePath")

#: Тільки ті інструменти, чия назва сама означає CrUD. Розширювати
#: списком «можливо, мутує» не можна: це повернуло б Read під іншою назвою.
MUTATING_TOOLS = frozenset({"Write", "Edit"})

NAME = "claude_code"


def default_root() -> str:
    """Тека журналів: `CLAUDE_CONFIG_DIR` має перевагу над `~/.claude`.

    Не константа навмисно. `docs/INTEGRATION.md` обіцяє не прибивати
    `~/.claude` цвяхами, а стара редакція саме це й робила — ще й на час
    імпорту, тож змінна, виставлена пізніше, не діяла б. Наслідок був
    мовчазний: з перенесеним конфігом адаптер віддавав нуль журналів,
    що виглядає як «історії немає», а не як «я шукав не там».
    """
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


def discover(root: str | None = None) -> list[str]:
    """Усі журнали. Порожній список — теж відповідь, і ворота її відхилять."""
    base = root or default_root()
    out: list[str] = []
    if not os.path.isdir(base):
        return out
    for d in sorted(os.listdir(base)):
        p = os.path.join(base, d)
        if not os.path.isdir(p):
            continue
        for fn in sorted(os.listdir(p)):
            if fn.endswith(".jsonl"):
                out.append(os.path.join(p, fn))
    return out


def _text_of(rec: dict) -> str:
    """Текст повідомлення. Беремо лише блоки type=text: решта (tool_use,
    зображення, вкладення) або не текст, або нас не стосується."""
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                parts.append(str(blk.get("text", "")))
    return "\n".join(parts)


def read(path: str, offset: int = 0):
    """Події одного журналу, починаючи зі зміщення (для інкрементності).

    Віддає СИРІ події: (actor, kind, ts, абсолютні шляхи, session_id, cwd).
    Перетворення на контракт робить збирач, бо лише він знає корені репо.

    Один запис може дати ДВІ події: виклик інструмента й прозу агента, з
    тим самим `event_id`. Саме так конвеєр звʼязує мотив із мутацією.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        if offset:
            fh.seek(offset)
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue

            sid = rec.get("sessionId") or ""
            ts = rec.get("timestamp") or ""
            cwd = rec.get("cwd") or ""
            # uuid самого агента: стабільний ідентифікатор події. Саме він
            # робить злиття сховищ із різних пристроїв ідемпотентним —
            # порядковий номер для цього не годиться, бо залежить від
            # того, звідки читали файл.
            eid = rec.get("uuid") or ""

            if rec.get("isCompactSummary") is True:
                # Єдиний текст, який цей адаптер віддає. Редакцію робить
                # ядро: адаптер не має вирішувати, що безпечно.
                # Шляхи з text НЕ додаємо: згадка назви в чаті не є CrUD
                # (Spec_UA.md:603). Компактація тримається сесії через
                # session_id, а сесія — вузлів через мутації.
                yield {"actor": "agent", "kind": "compact_summary", "ts": ts,
                       "abs_paths": [], "session_id": sid, "cwd": cwd, "event_id": eid,
                       "text": _text_of(rec)}
                continue

            rtype = rec.get("type")
            if rtype not in ("user", "assistant"):
                continue

            msg = rec.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None

            found: list[str] = []
            operations: list[str] = []
            saw_tool = False
            if isinstance(content, list):
                for blk in content:
                    if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                        continue
                    saw_tool = True
                    name = blk.get("name")
                    if name not in MUTATING_TOOLS:
                        continue
                    inp = blk.get("input")
                    if not isinstance(inp, dict):
                        continue
                    for k in PATH_KEYS:
                        v = inp.get(k)
                        if isinstance(v, str) and v:
                            found.append(v)
                            operations.append(str(name))

            text = _text_of(rec)

            if found:
                yield {"actor": "tool", "kind": "tool_call", "ts": ts,
                       "abs_paths": found, "session_id": sid, "cwd": cwd,
                       "event_id": eid, "operations": operations}
            elif saw_tool:
                # Інструмент був, але не мутація. Подія лишається, шляхів
                # немає: збирач відкине її як баласт, не зробивши вигляду,
                # що сесія «торкнулася» файлу під час читання.
                yield {"actor": "tool", "kind": "tool_call", "ts": ts,
                       "abs_paths": [], "session_id": sid, "cwd": cwd,
                       "event_id": eid, "operations": []}
            elif rtype == "user":
                # Текст користувача — корінь «чому» (його намір), але він НЕ
                # дає шляхів: згадка файлу в проханні не є CrUD. Збирач
                # мапить user-повідомлення з text у `user_intent`.
                yield {"actor": "user", "kind": "message", "ts": ts,
                       "abs_paths": [], "session_id": sid, "cwd": cwd,
                       "event_id": eid, "text": text}

            if rtype == "assistant" and text:
                # Проза агента. Раніше вона викидалась, і «мотив» у
                # причинному ланцюзі завжди був «(відсутній у події)» —
                # хоча в журналі лежав. Віддаємо з ТИМ САМИМ `event_id`,
                # щоб конвеєр звʼязав мотив із мутацією тієї самої події.
                # Це слова агента, не факт: нижче за течією вони стають
                # `reasoning` під міткою `agent_reasoning`, і підняти їм
                # довіру агент не може.
                yield {"actor": "agent", "kind": "message", "ts": ts,
                       "abs_paths": [], "session_id": sid, "cwd": cwd,
                       "event_id": eid, "text": text}
