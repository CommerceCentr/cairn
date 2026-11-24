"""Адаптер OpenCode: реальні події сесії з локальної SQLite (тільки читання).

Формат виміряно 2026-11-20 на `~/.local/share/opencode/opencode.db`,
сесія `ses_e0a98acebffePayGTXAEIUUJAT` (980 assistant-рядків):

  session_message.data.content[] :
      {type: reasoning, text: ...}          -- мотив, належить до тієї ж події
      {type: tool, name: write|edit,         -- CrUD
       id: chatcmpl-tool-...,
       state.input.path, state.time.created,
       state.input.oldString/newString,
       state.metadata.files[].patch}
      {type: tool, name: read|grep|glob|shell, ...}  -- НЕ мутація

Три principи, які не можна міняти без виміру:

1. CrUD визначається НАЗВОЮ інструмента (write/edit), не наявністю поля
   `path` — інакше read/grep/glob (358 викликів) стають хибними «дотиками».
   Той самий підхід, що й у `adapters/claude_code.py`.
2. Корінь «чому» — `user_intent` (поле `text` user-повідомлення), а не лише
   `reasoning` assistant-події. Виміряно 2026-11-20: seq 10564 (21:50:44)
   передує 15 мутаціям цієї правки (10587–10933, від 21:51:19); для всіх цих
   мутацій `reasoning` у тій самій події ПОРОЖНІЙ. Тобто «тільки reasoning»
   лишило би цю зміну без причини. Компактації 10024/10371 згадують коміт
   лише як стан checkout’у — причини там теж немає.
3. Сирі повідомлення НЕ імпортуються як факти: адаптер віддає лише мутації,
   компактації й user_intent. 980 assistant-рядків лишаються в БД.

Кожен запис має стабільний `event_id` — `seq:tool_id` для мутацій і
`seq:message_id` для компактації — щоб повторний імпорт був ідемпотентним.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

NAME = "opencode"

MUTATING_TOOLS = frozenset({"write", "edit"})
NON_MUTATING = frozenset({"read", "grep", "glob", "shell", "question", "skill",
                          "execute", "subagent", "task", "webfetch", "websearch"})


def default_db() -> str:
    """Шлях до БД; `OPENCODE_DB` дає змогу підмінити його fixture у тесті."""
    override = os.environ.get("OPENCODE_DB")
    if override:
        return override
    return str(Path.home() / ".local" / "share" / "opencode" / "opencode.db")


def connect(db_path: str | None = None):
    """Тільки `mode=ro`: адаптер не має права писати в журнал агента."""
    path = db_path or default_db()
    con = sqlite3.connect("file:" + path + "?mode=ro", uri=True, timeout=5)
    # БД пише живий агент у WAL. Без таймауту читач падає на блокуванні
    # замість того, щоб почекати.
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _reasoning_of(data: dict) -> str:
    parts = []
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "reasoning":
            text = " ".join(str(block.get("text") or "").split())
            if text:
                parts.append(text)
    return "\n".join(parts)


def read(db_path: str | None = None, session_id: str | None = None):
    """Віддає події сесії: compaction, user_intent, mutation, reasoning.

    Фільтрує за `session_id`, якщо вказано. Повертає генератор, щоб велика
    сесія не завантажувалась у пам’ять цілком.
    """
    con = connect(db_path)
    try:
        sql = "SELECT id, session_id, type, seq, time_created, data FROM session_message"
        args: tuple = ()
        if session_id:
            sql += " WHERE session_id = ?"
            args = (session_id,)
        sql += " ORDER BY seq"
        for mid, sid, typ, seq, ms, raw in con.execute(sql, args):
            data = json.loads(raw)
            created = (data.get("time") or {}).get("created") or ms
            if typ == "compaction":
                yield {
                    "kind": "compaction", "event_id": f"{seq}:{mid}",
                    "session_id": sid, "seq": seq, "message_id": mid,
                    "summary": str(data.get("summary") or ""), "ts": created,
                    "keep_id": str(((data.get("metadata") or {}).get("keepID")) or ""),
                    "reason": str(data.get("reason") or ""),
                }
                continue
            if typ == "user":
                # Корень «чому»: саме користувач формулює намір, який згодом
                # розгортається в assistant reasoning і мутацію. Беремо ТІЛЬКИ
                # текст повідомлення (поле `text` у цій сесії), не величезний
                # metadata з уривками файлів і токенами.
                text = " ".join(str(data.get("text") or "").split())
                if text:
                    yield {
                        "kind": "user_intent", "event_id": f"{seq}:{mid}",
                        "session_id": sid, "seq": seq, "message_id": mid,
                        "text": text, "ts": created,
                    }
                continue
            if typ != "assistant":
                continue
            content = data.get("content") or []
            reasoning = _reasoning_of(data)
            if not content:
                # Поточні сесії зберігають частини в `session_part`, а поле
                # `session_message.data.content` лишається порожнім. Без цього
                # читання завершена правка з мотивом тієї самої події не
                # потрапляє в індекс (виміряно на реальній події редагування).
                content = []
                texts = []
                for ptype, call, praw in con.execute(
                        "SELECT type, call_id, data FROM session_part "
                        "WHERE message_id = ? AND session_id = ? ORDER BY ordinal",
                        (mid, sid)):
                    part = json.loads(praw)
                    if ptype == "tool":
                        part = dict(part)
                        if not part.get("id"):
                            part["id"] = call or ""
                        content.append(part)
                    elif ptype == "reasoning":
                        text = " ".join(str(part.get("text") or "").split())
                        if text:
                            texts.append(text)
                reasoning = "\n".join(texts)
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool":
                    continue
                name = str(block.get("name") or "")
                if name not in MUTATING_TOOLS:
                    # Не-мутація: шляху немає навіть якщо він є в аргументах.
                    continue
                state = block.get("state") or {}
                inp = state.get("input") or {}
                path = str(inp.get("path") or "")
                if not path:
                    continue
                tid = str(block.get("id") or "")
                event_id = f"{seq}:{tid or mid}"
                tool_ms = (block.get("time") or {}).get("created") or created
                meta = state.get("metadata") or {}
                patch = ""
                files = meta.get("files") or []
                if files and isinstance(files[0], dict):
                    patch = str(files[0].get("patch") or "")
                yield {
                    "kind": "mutation", "event_id": event_id,
                    "session_id": sid, "seq": seq, "ts": tool_ms,
                    "tool": name, "path": path,
                    "status": str(state.get("status") or ""),
                    "old_string": str(inp.get("oldString") or ""),
                    "new_string": str(inp.get("newString") or ""),
                    "patch": patch,
                }
                # «Чому» — окремий запис із тим самим event_id, але без тексту
                # самої мутації: споживач сам вирішує, скільки взяти в бюджет.
                if reasoning:
                    yield {
                        "kind": "reasoning", "event_id": event_id,
                        "session_id": sid, "seq": seq, "ts": tool_ms,
                        "tool": name, "path": path, "text": reasoning,
                    }
    finally:
        con.close()


def compaction(db_path: str | None = None, session_id: str | None = None,
               message_id: str | None = None) -> dict | None:
    """Один рядок компактації за message_id, або останній для сесії."""
    found = None
    for rec in read(db_path, session_id):
        if rec["kind"] != "compaction":
            continue
        if message_id and rec["message_id"] != message_id:
            continue
        found = rec
        if message_id:
            return found
    return found
