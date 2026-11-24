"""Збирач: сирі події адаптера -> канонічна форма, яку читає конвеєр.

`adapters/claude_code.py` у власному docstring каже: «Перетворення на
контракт робить збирач, бо лише він знає корені репо», і далі —
«Збирач мапить user-повідомлення з text у `user_intent`; сам адаптер не
змінює kind, щоб не ламати вже перевірений контракт».

Цього збирача не існувало. Наслідок виміряний: `compaction_link` і
`causal_history` розуміли ЛИШЕ форму `adapters/opencode.py`, тому журнал
Claude Code (823 записи цієї сесії) у причинний граф не потрапляв узагалі —
не через дефект даних, а через відсутність перекладача.

Канонічна форма (те, що читає `compaction_link`):

```text
mutation     kind, event_id, status="completed", path, seq, tool
user_intent  kind, event_id, message_id, seq, text
reasoning    kind, event_id, text
compaction   kind, event_id, message_id, seq, summary, keep_id, reason
```

ПОРЯДОК. `seq` потрібен конвеєру, щоб знайти найближчий ПОПЕРЕДНІЙ намір
для мутації. Беремо його з часу події (`ts`), а не з номера рядка; індекс
у файлі лишається тільки тай-брейкером для подій з однаковим штампом.
Журнал append-only, тому час і порядок тут не суперечать один одному — але
якорем лишається час, бо саме він щось означає.

МЕЖА. Один запис Claude Code може містити кілька правок (кілька
`tool_use` в одному повідомленні). Кожна стає ОКРЕМОЮ мутацією з
`event_id = "<uuid>#<n>"`, інакше дві правки різних файлів злилися б в
одну подію й одна з них зникла б з графа.
"""

from __future__ import annotations

__all__ = ["canonical_events", "SOURCE_CLAUDE_CODE", "SOURCE_CODEX", "capabilities"]

SOURCE_CLAUDE_CODE = "claude_code_jsonl"
SOURCE_CODEX = "codex_rollout"

#: Що кожне джерело справді доводить. Читається кодом, а не лише людиною,
#: бо саме з цього видно, чому для Codex немає ребер «мутація -> вузол».
CAPABILITIES: dict[str, dict[str, str]] = {
    SOURCE_CLAUDE_CODE: {
        "user_intent": "supported",
        "compaction": "supported",
        # Було "unsupported:not_exported" — і це була неправда про джерело.
        # Виміряно: у журналі 125 текстових блоків агента на 28 435 символів,
        # а адаптер віддавав нуль. Тобто політика адаптера подавалась як
        # властивість даних. Тепер проза віддається, а довіру до неї
        # обмежує модель довіри, а не мовчазне викидання.
        "reasoning": "supported:agent_prose",
        "mutation": "partial:write_edit_only",
        # Виміряно 2026-11-24 на журналі 2e7f1040 (16,3 МБ, 2 356 записів
        # `user`/`assistant`, 777 блоків `tool_use`): записів, де проза
        # агента й правка лежать в ОДНОМУ записі, — **0**; правок без прози
        # в тому ж записі — 108. Проза приходить окремими записами
        # `assistant`, тому мотив цього джерела можна привʼязати лише за
        # порядком, а не до тієї самої події редагування.
        #
        # Відсутнє поле назване прямо: у блоці `tool_use` немає посилання
        # на текстовий блок (і навпаки), тож `node_attested` з цього
        # джерела не виводиться. Підвищувати `prior_by_seq` до доведеного
        # заборонено мандатом §7.2.
        "motive_binding": "unsupported:prose_in_separate_record",
    },
    SOURCE_CODEX: {
        "user_intent": "supported",
        "compaction": "partial:plaintext_or_encrypted_metadata",
        "reasoning": "unsupported:encrypted_content",
        "mutation": "partial:completed_file_change",
        # Проза Codex зашифрована, тож мотиву немає взагалі — не «є, але
        # в іншому записі». Дві різні причини, дві різні мітки.
        "motive_binding": "unsupported:encrypted_content",
    },
}


#: Одне джерело — одна канонічна назва. Довгі написання лишились від
#: ранішого інструмента й трапляються у вже записаних сховищах, тому їх
#: приймаємо, але не породжуємо.
SOURCE_ALIASES: dict[str, str] = {
    "claude_code": SOURCE_CLAUDE_CODE,
    "claude_code_jsonl": SOURCE_CLAUDE_CODE,
    "codex": SOURCE_CODEX,
    "codex_rollout": SOURCE_CODEX,
    "opencode": "opencode",
    "opencode_db": "opencode",
}


def canonical_source(source_kind: str | None) -> str:
    """Канонічна назва джерела. Невідоме повертається як є, не вгадується."""
    if not source_kind:
        return ""
    return SOURCE_ALIASES.get(str(source_kind), str(source_kind))


def capabilities(source_kind: str) -> dict[str, str]:
    """Матриця можливостей джерела. Невідоме джерело — `unknown`, не `supported`."""
    return CAPABILITIES.get(canonical_source(source_kind)) or {
        "user_intent": "unknown", "compaction": "unknown",
        "reasoning": "unknown", "mutation": "unknown",
        "motive_binding": "unknown",
    }


def _sortable(record: dict, index: int) -> tuple:
    return (str(record.get("ts") or ""), index)


def canonical_events(records, *, include_reasoning: bool = True) -> list[dict]:
    """Канонічні події з сирих записів `adapters.claude_code.read`.

    `include_reasoning` лишено явним, бо цей адаптер прози агента НЕ
    віддає (`text` для agent завжди порожній): прапорець існує, щоб було
    видно, що reasoning тут відсутній за властивістю джерела, а не
    загублений збирачем.
    """
    raw = [(index, record) for index, record in enumerate(records)
           if isinstance(record, dict)]
    raw.sort(key=lambda pair: _sortable(pair[1], pair[0]))

    out: list[dict] = []
    for seq, (_index, record) in enumerate(raw, start=1):
        kind = record.get("kind")
        event_id = str(record.get("event_id") or "")
        ts = str(record.get("ts") or "")

        if kind == "compact_summary":
            text = str(record.get("text") or "")
            out.append({
                "kind": "compaction", "event_id": event_id,
                "message_id": event_id, "seq": seq, "ts": ts,
                "summary": text, "keep_id": "",
                "reason": str(record.get("reason") or "compact_summary"),
            })
            continue

        if kind == "message" and record.get("actor") == "user":
            text = str(record.get("text") or "")
            if not text.strip():
                continue
            out.append({
                "kind": "user_intent", "event_id": event_id,
                "message_id": event_id, "seq": seq, "ts": ts, "text": text,
            })
            continue

        if kind == "message" and record.get("actor") == "agent":
            text = str(record.get("text") or "")
            if include_reasoning and text.strip():
                out.append({"kind": "reasoning", "event_id": event_id,
                            "seq": seq, "ts": ts, "text": text})
            continue

        if kind == "tool_call":
            paths = list(record.get("abs_paths") or [])
            tools = list(record.get("operations") or [])
            before = list(record.get("old_strings") or [])
            after = list(record.get("new_strings") or [])
            patches = list(record.get("patches") or [])
            for position, path in enumerate(paths):
                out.append({
                    "kind": "mutation",
                    # Кілька правок в одному повідомленні -> кілька подій.
                    "event_id": f"{event_id}#{position}" if len(paths) > 1 else event_id,
                    "seq": seq, "ts": ts, "status": "completed",
                    "path": str(path),
                    "tool": str(tools[position]) if position < len(tools) else "edit",
                    "evidence_scope": record.get("evidence_scope") or "node",
                    "old_string": str(before[position]) if position < len(before) else "",
                    "new_string": str(after[position]) if position < len(after) else "",
                    "patch": str(patches[position]) if position < len(patches) else "",
                })
    return out
