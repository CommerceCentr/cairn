"""Автоматичний запис правок у сховище: хук, а не дисциплінa агента.

Проблема, виміряна на цьому ж проєкті: у журналі сесії Claude Code було
**0 мутацій `cairn_unified/verification.py`**, хоча коміт `d820af9` змінив
саме цей файл. Причина не в Cairn: правки вносились `python`-скриптами
через оболонку, а матриця можливостей джерела чесно оголошує
`claude_code: mutation = "partial:write_edit_only"`. Журнал бачить лише
правки інструментами редагування, а не те, що зробила оболонка.

Вимагати від людини «редагуй лише інструментом» — перекласти дефект
обліку на користувача. Тому облік бере на себе Git: він знає про зміну
файла незалежно від того, хто і чим її зробив.

Запускається хуком після кожного виклику інструмента. Порівнює робоче
дерево з попереднім зрізом і кладе знайдені правки ТИМ САМИМ конвеєром,
що й `tools/index_agent_history.py` (`compaction_link.link_mutations`), а
не власним шляхом запису: два шляхи розійшлися б.

Ніколи не ламає роботу агента: будь-яка помилка -> код 0 і запис у
`.cairn/runtime/hook_edits.log`.

    python tools/hook_record_edits.py --root . --repo . --verbose
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SNAPSHOT = "hook_snapshot.json"
LOG = "hook_edits.log"
SEQ_KEY = "__seq__"


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return done.stdout if done.returncode == 0 else ""


def changed_paths(repo: Path) -> list[str]:
    """Шляхи, які Git вважає зміненими або невідстежуваними.

    Беремо саме у Git, а не з аргументів інструмента: аргумент знає лише
    про `Edit`, а `--porcelain` бачить і те, що записав скрипт.
    """
    out = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    paths = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        name = line[3:].strip().strip('"')
        if " -> " in name:                      # перейменування
            name = name.split(" -> ", 1)[1]
        paths.append(name)
    return paths


def digest(repo: Path, rel: str) -> str:
    target = repo / rel
    try:
        if not target.is_file():
            return "absent"
        return hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def load_snapshot(runtime: Path) -> dict:
    try:
        return json.loads((runtime / SNAPSHOT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_snapshot(runtime: Path, data: dict) -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    tmp = runtime / (SNAPSHOT + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(runtime / SNAPSHOT)


def canonical_events(repo: Path, diffs: list[str], session_id: str,
                     base_seq: int) -> list[dict]:
    """Канонічні події мутацій — та сама форма, що в `adapters.normalize`."""
    stamp = time.time()
    events = []
    for index, rel in enumerate(sorted(diffs)):
        seq = base_seq + index
        events.append({
            "event_id": f"hook:{session_id}:{seq}",
            "kind": "mutation",
            "path": str((repo / rel).resolve()),
            "seq": seq,
            "ts": stamp,
            "session_id": session_id,
            "message_id": f"hook:{session_id}:{seq}",
            "text": "",
            # Правка вже на диску — зріз порівнює вміст ПІСЛЯ запису, тож
            # незавершених мутацій цей шлях не породжує. Без цього поля
            # класифікатор віддавав `mutation_not_completed` для всіх.
            "status": "completed",
            # Межа названа в ДАНИХ, не лише в документації: хук знає, ЩО
            # змінилось, і не знає, який саме фрагмент тексту переписано.
            "evidence_scope": "file",
            "evidence_origin": "git_worktree_hook",
        })
    return events


def run(root: Path, repo: Path, session_id: str) -> dict:
    from cairn_unified import compaction_link
    from cairn_unified.code import git as gitmod
    from cairn_unified.store import Store

    store = Store.open(root)
    repo_id = gitmod.identity(repo)["id"]
    runtime = Path(root) / ".cairn" / "runtime"
    snapshot = load_snapshot(runtime)

    current = {rel: digest(repo, rel) for rel in changed_paths(repo)}
    diffs = [rel for rel, sha in current.items() if snapshot.get(rel) != sha]
    raw_seq = snapshot.get(SEQ_KEY)
    base = int(raw_seq) if isinstance(raw_seq, int) else 0
    if not diffs:
        # Зріз усе одно наздоганяє стан: файл, що зник із переліку змінених,
        # або закомічено, або повернуто, і запису це не потребує.
        save_snapshot(runtime, {**current, SEQ_KEY: base})
        return {"changed": 0, "linked": 0, "reason": "no_change_since_snapshot"}

    events = canonical_events(repo, diffs, session_id, base + 1)
    save_snapshot(runtime, {**current, SEQ_KEY: base + len(events)})

    ready, unresolved = compaction_link.mutation_readiness(
        store, events, repo_id, (str(repo),))
    scope = sorted({str(node["stable_key"])
                    for _event, _path, resolved in ready for node in resolved})
    if not scope:
        return {"changed": len(diffs), "linked": 0,
                "reason": "no_indexed_node_for_changed_paths",
                "unresolved": len(unresolved)}
    actor = {"kind": "agent", "id": "claude_code"}
    descriptor = {"message_id": f"hook:{session_id}:tail",
                  "seq": base + len(events),
                  "summary": "",
                  "keep_id": "",
                  # Починається з `no_`, тому верифікатор знає: компактації
                  # в джерелі немає за побудовою, а не загублена.
                  "reason": "no_compaction_hook_recorded"}
    session = compaction_link.session_object(
        store, session_id=session_id, compaction=descriptor, repo_id=repo_id,
        actor=actor, scope=scope, source_kind="claude_code",
        session_source="claude_code", source_ref=f"{session_id}.jsonl",
        opencode_db="")
    linked = compaction_link.link_mutations(
        store, events=events, session_obj_id=session["id"], repo_id=repo_id,
        repo_path=(str(repo),), actor=actor, compact_relations=True)
    return {"changed": len(diffs), "linked": len(linked.get("links") or []),
            "unresolved": len(unresolved), "session": session["id"][:16]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hook_record_edits")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--session", default="")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    session_id = args.session
    if not session_id and not sys.stdin.isatty():
        try:
            payload = json.loads(sys.stdin.read() or "{}")
            session_id = str(payload.get("session_id") or "")
        except ValueError:
            session_id = ""
    session_id = session_id or "unknown-session"

    root, repo = args.root.resolve(), args.repo.resolve()
    try:
        report = run(root, repo, session_id)
        if args.verbose:
            print(json.dumps(report, ensure_ascii=False))
    except Exception:              # noqa: BLE001 — хук не має права ламати сесію
        runtime = root / ".cairn" / "runtime"
        try:
            runtime.mkdir(parents=True, exist_ok=True)
            with (runtime / LOG).open("a", encoding="utf-8") as fh:
                fh.write("--- " + time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
                fh.write(traceback.format_exc())
        except OSError:
            pass
        if args.verbose:
            print(json.dumps({"error": "logged"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
