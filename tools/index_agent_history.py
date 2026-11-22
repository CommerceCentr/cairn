"""Import repository-scoped session checkpoints and structured file mutations.

The source journals are read locally; only compacted context and mutation
evidence are written to Cairn. No commit relation is guessed from timestamps.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import code, compaction_link, nodes  # noqa: E402
from cairn_unified.adapters import claude_code, codex, normalize, opencode  # noqa: E402
from cairn_unified.errors import CairnError  # noqa: E402
from cairn_unified.store import Store  # noqa: E402


def _iso(value) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
    return str(value or "")


def _inside(path: str, repo: Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(repo.resolve())
    except (OSError, ValueError):
        return False


def mixed_mutation_paths(events: list[dict], repo: Path,
                         allowed_roots: list[Path] | None = None) -> bool:
    """Reject edits in another Git repository; ignore non-repository sidecars."""
    roots = allowed_roots or [repo]
    target_id = code.git.identity(repo)["id"]
    checked: dict[str, bool] = {}
    for event in events:
        if event.get("kind") != "mutation" or event.get("status") != "completed":
            continue
        path = str(event.get("path") or "")
        if not path or any(_inside(path, root) for root in roots):
            continue
        parent = Path(path).resolve().parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        key = str(parent)
        if key not in checked:
            try:
                checked[key] = code.git.identity(parent)["id"] != target_id
            except (CairnError, OSError, ValueError):
                checked[key] = False
        if checked[key]:
            return True
    return False


def _sources(repo: Path, db: Path | None):
    marker = str(repo).replace(":", "-").replace("\\", "-").replace("/", "-").lower()
    claude_root = Path.home() / ".claude" / "projects"
    if claude_root.is_dir():
        for folder in claude_root.iterdir():
            if folder.is_dir() and folder.name.lower().startswith(marker):
                for journal in folder.glob("*.jsonl"):
                    yield "claude_code", journal, None
    for raw in codex.discover():
        journal = Path(raw)
        try:
            meta = codex.session_meta(journal)
        except (OSError, ValueError):
            continue
        roots = [meta.get("cwd") or "", *(meta.get("roots") or [])]
        if any(_inside(r, repo) for r in roots if r):
            yield "codex", journal, None
    if db and db.is_file():
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            for sid, directory in con.execute("SELECT id, directory FROM session_v2"):
                if _inside(str(directory or ""), repo):
                    yield "opencode", db, str(sid)
        finally:
            con.close()


def _events(source: str, journal: Path, session_id: str | None):
    if source == "opencode":
        return list(opencode.read(str(journal), session_id))
    reader = claude_code if source == "claude_code" else codex
    return normalize.canonical_events(reader.read(str(journal)))


def _descriptor(events: list[dict], name: str) -> dict:
    mutations = [e for e in events if e.get("kind") == "mutation"]
    last_edit = max((int(e.get("seq") or 0) for e in mutations), default=0)
    later = sorted((e for e in events if e.get("kind") == "compaction"
                    and int(e.get("seq") or 0) > last_edit),
                   key=lambda e: int(e.get("seq") or 0))
    if later:
        selected = later[0]
        return {"summary": selected.get("summary") or "",
                "message_id": selected.get("message_id") or selected.get("event_id"),
                "seq": selected.get("seq"), "ts": _iso(selected.get("ts")),
                "keep_id": selected.get("keep_id") or "",
                "reason": "agent_compaction_after_last_mutation"}
    latest = max(mutations, key=lambda e: int(e.get("seq") or 0))
    return {"summary": f"Сесія {name}: {len(mutations)} структурованих правок. "
                       "Компактації після останньої правки немає.",
            "message_id": f"{name}:after-edit-missing", "seq": latest.get("seq"),
            "ts": _iso(latest.get("ts")), "keep_id": "",
            "reason": "no_compaction_after_last_mutation"}


def run(root: Path, repo: Path, db: Path | None, apply: bool,
        allowed_roots: list[Path] | None = None) -> dict:
    started = time.perf_counter()
    store = Store.open(root) if apply else None
    identity = code.git.identity(repo)
    repo_id = identity["id"]
    report = {"repo_id": repo_id, "sources": {}, "sessions_seen": 0,
              "sessions_with_mutations": 0, "sessions_indexed": 0,
              "mixed_sessions_skipped": 0,
              "mutations_seen": 0,
              "mutations_resolved": 0, "mutations_unresolved": 0,
              "compactions_after_last_mutation": 0,
              "automatic_commit_links": 0,
              "commit_link_boundary": "requires explicit or content-matched evidence",
              "apply": apply}
    for source, journal, sid in _sources(repo, db):
        report["sessions_seen"] += 1
        report["sources"][source] = report["sources"].get(source, 0) + 1
        try:
            events = _events(source, journal, sid)
        except (OSError, ValueError, RuntimeError) as exc:
            report.setdefault("errors", []).append({"journal": journal.name,
                                                     "source": source, "error": str(exc)[:200]})
            continue
        if mixed_mutation_paths(events, repo, allowed_roots):
            report["mixed_sessions_skipped"] += 1
            continue
        mutations = [e for e in events if e.get("kind") == "mutation"
                     and _inside(str(e.get("path") or ""), repo)]
        if not mutations:
            continue
        report["mutations_seen"] += len(mutations)
        # A journal edit names a file. Without a verified text span, it must
        # not claim that every symbol in that file was edited.
        for mutation in mutations:
            mutation["evidence_scope"] = "file"
        report["sessions_with_mutations"] += 1
        relevant = {id(e) for e in mutations}
        events = [e for e in events if e.get("kind") != "mutation" or id(e) in relevant]
        descriptor = _descriptor(events, sid or journal.stem)
        if descriptor["reason"] == "agent_compaction_after_last_mutation":
            report["compactions_after_last_mutation"] += 1
        if not apply:
            continue
        ready, unresolved = compaction_link.mutation_readiness(
            store, events, repo_id, (str(repo),))
        report["mutations_unresolved"] += len(unresolved)
        scope = sorted({str(node["stable_key"])
                        for _event, _path, resolved in ready for node in resolved})
        if not scope:
            continue
        actual_sid = sid or next((str(e.get("session_id")) for e in events
                                  if e.get("session_id")), journal.stem)
        actor = {"kind": "agent", "id": source}
        session = compaction_link.session_object(
            store, session_id=actual_sid, compaction=descriptor, repo_id=repo_id,
            actor=actor, scope=scope, source_kind=source,
            session_source=source, source_ref=journal.name,
            opencode_db="")
        linked = compaction_link.link_mutations(
            store, events=events, session_obj_id=session["id"], repo_id=repo_id,
            repo_path=(str(repo),), actor=actor, compact_relations=True)
        compaction_link.auto_compaction(
            store, events=events, links=linked["all_links"], session_obj=session,
            opencode_db="", actor=actor, source_kind=source,
            source_ref=journal.name)
        report["sessions_indexed"] += 1
        report["mutations_resolved"] += len(ready)
        if report["sessions_seen"] % 10 == 0:
            print(f"sessions {report['sessions_seen']}, indexed {report['sessions_indexed']}",
                  file=sys.stderr, flush=True)
    if apply:
        report["doctor_ok"] = store.doctor()["ok"]
    report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--opencode-db", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.opencode_db or Path(opencode.default_db())
    report = run(args.root.resolve(), args.repo.resolve(), db, args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("doctor_ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
