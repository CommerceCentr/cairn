r"""Журнал Codex: `~/.codex/sessions/<Y>/<M>/<D>/rollout-*.jsonl[.zst]`.

Формат прочитано з РЕАЛЬНОГО файла, не з памʼяті. Кожен рядок —
`{timestamp, ordinal, type, payload, [metadata]}`.

## Матриця можливостей (ADR-013: адаптер повідомляє свій стан честно)

```text
session_meta   supported    session_id, cwd, runtime_workspace_roots
user_intent    supported    response_item/message, role=user
compaction     supported    type=compacted: retained_context (message часто "")
reasoning      UNSUPPORTED  payload.summary порожній, текст у encrypted_content
mutation       PARTIAL      завершені FileChange; shell-правки не видно
```

`FileChange` дає структуровану участь у правці файла. Shell-команди
не інтерпретуються як мутації. Конкретний символ усередині файла цей
запис не доводить, тому зв'язок ставиться лише на вузол whole:file.
"""

from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path

from ..errors import CairnError

NAME = "codex"
SOURCE_KIND = "codex_rollout"

#: Що це джерело справді доводить. Читається кодом, не лише людиною.
CAPABILITIES: dict[str, str] = {
    "session_meta": "supported",
    "user_intent": "supported",
    "compaction": "partial:plaintext_or_encrypted_metadata",
    "reasoning": "unsupported:encrypted_content",
    "mutation": "partial:completed_file_change",
}

#: Блоки, які Codex вставляє в user-повідомлення сам. Це НЕ слова людини,
#: тому в наміри вони не йдуть: інакше «чому» показувало б службовий текст.
INJECTED_PREFIXES = (
    "# AGENTS.md instructions",
    "<external_codex_apps_open_page>",
    "<user_instructions>",
    "<environment_context>",
    "<permissions instructions>",
    "<skills_instructions>",
    "<app-context>",
)


def default_roots() -> list[Path]:
    """Усі теки сесій, а не одна.

    Виміряно: `CODEX_HOME` може вказувати на диск, відмінний від домашньої теки,
    тоді як частина журналів лежить у домашній теці (`~/.codex`). Якщо
    шукати лише в одній, обʼєкт із чесно записаним імʼям файла стає
    «журнал не знайдено» — хоча файл на місці.
    """
    roots: list[Path] = []
    # Additional local archives are configured at the reader, never saved
    # as absolute paths in portable semantic objects.
    for value in os.environ.get("CAIRN_CODEX_SESSION_ROOTS", "").split(os.pathsep):
        if value.strip():
            roots.append(Path(value.strip()))
    override = os.environ.get("CODEX_HOME")
    if override:
        roots.append(Path(override) / "sessions")
    roots.append(Path.home() / ".codex" / "sessions")
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def default_root() -> Path:
    """Перша наявна тека сесій; потрібна для повідомлень і сумісності."""
    for root in default_roots():
        if root.is_dir():
            return root
    return default_roots()[0]


def discover(root: str | Path | None = None) -> list[str]:
    """Усі rollout-журнали з усіх тек, найсвіжіші першими."""
    bases = [Path(root)] if root else default_roots()
    found: list[Path] = []
    for base in bases:
        if not base.is_dir():
            continue
        found.extend(base.rglob("rollout-*.jsonl"))
        found.extend(base.rglob("rollout-*.jsonl.zst"))
    return [str(p) for p in sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)]


def _lines(path: str | Path, offset: int = 0):
    """Рядки журналу; `.zst` розпаковується на льоту.

    `compression.zstd` є в стандартній бібліотеці з Python 3.14. На старших
    версіях пробуємо `zstandard`; якщо нема й того — кажемо прямо, а не
    повертаємо порожньо, бо порожньо виглядало б як «сесія без подій».
    """
    path = Path(path)
    if path.suffix != ".zst":
        with path.open(encoding="utf-8", errors="replace") as handle:
            if offset:
                handle.seek(offset)
            yield from handle
        return
    if offset:
        raise ValueError("compressed Codex journals do not support offsets")
    try:
        from compression import zstd as _zstd

        with _zstd.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            yield from handle
        return
    except ImportError:
        pass
    try:
        import zstandard
    except ImportError as exc:
        raise RuntimeError(
            f"{path.name}: стиснутий журнал, а розпакувати нічим "
            "(потрібен Python 3.14+ або пакет zstandard)"
        ) from exc
    with path.open("rb") as raw:
        with zstandard.ZstdDecompressor().stream_reader(raw) as stream:
            for line in stream.read().decode("utf-8", errors="replace").splitlines():
                yield line


def _text_of(payload: dict) -> str:
    parts = []
    for block in payload.get("content") or []:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def session_meta(path: str | Path) -> dict:
    """Шапка сесії: id, тека, корені. Перший рядок журналу."""
    for line_number, line in enumerate(_lines(path), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") == "session_meta":
            payload = record.get("payload") or {}
            return {
                "session_id": str(payload.get("session_id") or payload.get("id") or ""),
                "cwd": str(payload.get("cwd") or ""),
                "roots": [str(r) for r in payload.get("runtime_workspace_roots") or []],
                "originator": str(payload.get("originator") or ""),
                "cli_version": str(payload.get("cli_version") or ""),
                "started_at": str(record.get("timestamp") or ""),
            }
        break
    return {}


def read(path: str | Path, offset: int = 0):
    """Сирі наміри, компактації та завершені структуровані FileChange.

    Форма збігається з `adapters/claude_code.py`: `kind`, `ts`, `event_id`,
    `session_id`, `cwd`, плюс `text`. Мутацій тут немає за властивістю
    джерела — див. `CAPABILITIES`.
    """
    meta = session_meta(path)
    sid = meta.get("session_id") or Path(path).stem
    cwd = meta.get("cwd") or ""
    source_lines = _lines(path, offset) if offset else _lines(path)
    for line_number, line in enumerate(source_lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        payload = record.get("payload") or {}
        ts = str(record.get("timestamp") or "")
        ordinal = record.get("ordinal")
        # Newer desktop rollouts omit ordinal. The line number is stable in
        # append-only JSONL and prevents several compacted events sharing an id.
        event_position = ordinal if ordinal is not None else (
            f"{offset}:{line_number}" if offset else line_number)

        # Desktop journals expose completed edits as structured FileChange
        # items. Shell text and failed items are never mutation evidence.
        item = payload.get("item") or {}
        if (record.get("type") == "event_msg"
                and payload.get("type") == "item_completed"
                and item.get("type") == "FileChange"
                and item.get("status") == "completed"):
            changes = item.get("changes") or {}
            if isinstance(changes, dict):
                paths = [p for p, change in changes.items()
                         if isinstance(p, str) and Path(p).is_absolute()
                         and isinstance(change, dict)
                         and change.get("type") in {"add", "update", "delete", "move"}]
                if paths:
                    yield {"actor": "agent", "kind": "tool_call", "ts": ts,
                           "abs_paths": paths,
                           "operations": [str(changes[p]["type"]) for p in paths],
                           "patches": [str(changes[p].get("unified_diff") or "")
                                       for p in paths],
                           "evidence_scope": "file",
                           "session_id": sid, "cwd": cwd,
                           "event_id": str(item.get("id") or f"{sid}:edit:{event_position}"),
                           "ordinal": event_position}
            continue

        encrypted_compaction = (
            record.get("type") == "response_item"
            and payload.get("type") == "compaction"
        )
        if record.get("type") == "compacted" or encrypted_compaction:
            retained = payload.get("retained_context")
            # `message` у реальних сесіях часто порожній, а зміст лежить у
            # `retained_context`. Беремо те, що є насправді.
            text = str(payload.get("message") or "")
            if not text and retained is not None:
                text = json.dumps(retained, ensure_ascii=False, sort_keys=True)
            reason = ("encrypted_compaction" if encrypted_compaction
                      and not text else "compact_summary")
            yield {"actor": "agent", "kind": "compact_summary", "ts": ts,
                   "abs_paths": [], "session_id": sid, "cwd": cwd,
                   "event_id": str(payload.get("id") or payload.get("window_id")
                                   or f"{sid}:cmp:{event_position}"),
                   "ordinal": event_position, "text": text, "reason": reason}
            continue

        if payload.get("type") == "message" and payload.get("role") == "user":
            text = _text_of(payload)
            stripped = text.lstrip()
            if any(stripped.startswith(prefix) for prefix in INJECTED_PREFIXES):
                continue
            if not stripped:
                continue
            yield {"actor": "user", "kind": "message", "ts": ts,
                   "abs_paths": [], "session_id": sid, "cwd": cwd,
                   "event_id": str(payload.get("id") or f"{sid}:msg:{event_position}"),
                   "ordinal": event_position, "text": text}


def recent_edits(repo_path: str | Path, path: str = "", limit: int = 20) -> list[dict]:
    """Read-only project-scoped Codex FileChange references for `change note`.

    Return ids and patch hashes, never full journal prose. The event id is
    the explicit reference an agent may attach to a post-edit reason.
    """
    repo = Path(repo_path).resolve()
    if not repo.is_dir() or not 1 <= limit <= 100:
        raise CairnError("bad_codex_events_query",
                         "потрібен наявний репозиторій і limit 1..100", exit_code=2)
    wanted = (repo / path).resolve() if path else None
    if wanted and not wanted.is_relative_to(repo):
        raise CairnError("bad_codex_events_query",
                         "шлях має бути всередині репозиторію", exit_code=2)
    result: list[dict] = []
    for journal in discover()[:100]:
        for event in read(journal):
            if event.get("kind") != "tool_call":
                continue
            for absolute, operation, patch in zip(
                    event.get("abs_paths") or [], event.get("operations") or [],
                    event.get("patches") or []):
                target = Path(absolute).resolve()
                if not target.is_relative_to(repo) or (wanted and target != wanted):
                    continue
                result.append({
                    "session_id": event["session_id"], "event_id": event["event_id"],
                    "ts": event.get("ts"), "path": target.relative_to(repo).as_posix(),
                    "operation": operation,
                    "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest()
                    if patch else "",
                })
    result.sort(key=lambda item: (item["ts"] or "", item["event_id"]), reverse=True)
    return result[:limit]
