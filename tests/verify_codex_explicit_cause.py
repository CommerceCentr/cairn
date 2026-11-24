"""Codex FileChange + explicit note: only the exact event earns node scope."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fixture_repo import PY_FILE, git, make_repo  # noqa: E402
from cairn_unified import change_notes, code, compaction_link, nodes, semantic, why  # noqa: E402
from cairn_unified.adapters import codex, normalize  # noqa: E402
from cairn_unified.store import Store  # noqa: E402
from cairn_unified.errors import CairnError  # noqa: E402
from tools.index_git_archive import run as index_git  # noqa: E402
from tools import link_mutations_to_git as witness_linker  # noqa: E402

ACTOR = {"kind": "agent", "id": "codex-fixture"}


def case(base: Path, *, referenced_event: str, note_session: str,
         witness: bool, expected: int, reject_ref: bool = False,
         journal_name: str = "fixture.jsonl") -> None:
    repo = make_repo(base / "repo",
                     files={PY_FILE: "value = 'original causal context one'\n"},
                     second={PY_FILE: "value = 'original causal context two'\n"})
    path = Path(repo["path"])
    (path / PY_FILE).write_text("value = 'original causal context three'\n",
                                encoding="utf-8")
    store_root = base / "store"
    Store(store_root).init()
    store = Store.open(store_root)
    ident = code.git.identity(path)
    store.mutate(lambda con: store.append(
        con, "repository.upserted", ident["id"], ident, ACTOR,
        f"repo:{ident['id']}"))

    journal = [
        {"timestamp": "2026-11-24T10:00:00Z", "type": "session_meta",
         "payload": {"session_id": "codex-session", "cwd": str(path)}},
        {"timestamp": "2026-11-24T10:00:01Z", "type": "event_msg",
         "payload": {"type": "item_completed", "item": {
             "id": "codex-edit-1", "type": "FileChange", "status": "completed",
             "changes": {str(path / PY_FILE): {"type": "update",
                 "unified_diff": ("@@ -1 +1 @@\n"
                                  "-value = 'original causal context two'\n"
                                  "+value = 'original causal context three'\n")}}}}},
    ]
    original = codex._lines
    codex._lines = lambda _path: iter(json.dumps(row) for row in journal)
    try:
        events = normalize.canonical_events(list(codex.read(journal_name)))
    finally:
        codex._lines = original
    edit = next(event for event in events if event["kind"] == "mutation")
    assert edit["event_id"] == "codex-edit-1"
    assert edit["evidence_scope"] == "file"

    original_discover = codex.discover
    original_lines = codex._lines
    codex.discover = lambda: [journal_name]
    codex._lines = lambda _path: iter(json.dumps(row) for row in journal)
    try:
        try:
            reason = change_notes.note(store, path, [PY_FILE],
                                       "Remove the old limit for this edit",
                                       "use value three", ACTOR,
                                       note_session, referenced_event)
        except CairnError as exc:
            if reject_ref and exc.code == "codex_event_not_found":
                return
            raise
    finally:
        codex.discover = original_discover
        codex._lines = original_lines
    assert not reject_ref, "invalid event reference was accepted"
    assert reason["status"] == "unlinked"
    git(path, "add", PY_FILE)
    git(path, "commit", "-qm", "use value three")
    commit = git(path, "rev-parse", "HEAD")
    nodes.index(store, ident["id"], ACTOR, paths=[PY_FILE])
    whole = next(node["stable_key"] for node in nodes.resolve(store, ident["id"], PY_FILE)
                 if str(node["address"]).startswith("whole:"))
    fact = compaction_link.link_commit(
        store, repo_id=ident["id"], repo_path=path,
        commit=commit, work_id="", actor=ACTOR)["fact_id"]
    session = semantic.put_object(
        store, object_type="session", title="Codex fixture", summary="", actor=ACTOR,
        stable_key="codex-session-object",
        semantic={"kind": "session", "source_kind": "codex",
                  "session_id": Path(journal_name).stem,
                  "commit": commit})["object"]["id"]
    semantic.relate(store, from_id=whole, to_id=session, relation="caused_by",
                    actor=ACTOR, condition={"basis": "tool_mutation",
                        "evidence": {"event_id": edit["event_id"],
                                     "path": PY_FILE, "rationale_source": "none"},
                        "rationale": ""})
    if witness:
        index_git(store_root, path)
        original_sources = witness_linker._sources
        original_lines = codex._lines
        witness_linker._sources = lambda _repo, _db: [
            ("codex", Path(journal_name), None)]
        codex._lines = lambda _path: iter(json.dumps(row) for row in journal)
        try:
            linked = witness_linker.run(store_root, path, [path], True)
        finally:
            witness_linker._sources = original_sources
            codex._lines = original_lines
        assert linked["counts"].get("linked", 0) == 1, linked
        assert witness_linker.verify_stored(store, path, ident["id"])["failed"] == 0
    assert change_notes.sync(store, path)["linked"] == 1
    result = why.answer(store, repo_id=ident["id"], path=PY_FILE,
                        commit=commit, repo_path=str(path))
    assert result["coverage"]["node_attested_causes"] == expected
    assert result["coverage"]["file_state_attested_reasons"] == 1 - expected
    if expected:
        assert "Remove the old limit for this edit" in result["answer"]
        assert any(x["motive_source"] == "explicit_codex_change_note"
                   for x in result["why_detail"])
        assert result["commit_history"][-1]["change_reasons"][0]["scope"] == "node"
    else:
        assert all(x.get("semantic_cause_status") != "node_attested"
                   for x in result["why_detail"])


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="codex-explicit-cause-") as temp:
        root = Path(temp)
        case(root / "exact", referenced_event="codex-edit-1",
             note_session="fixture", witness=True, expected=1)
        case(root / "legacy-raw-id", referenced_event="codex-edit-1",
             note_session="codex-session", witness=True, expected=1,
             journal_name="rollout-2026-11-24-codex-session.jsonl")
        case(root / "wrong-event", referenced_event="other-edit",
             note_session="fixture", witness=True, expected=0,
             reject_ref=True)
        case(root / "wrong-session", referenced_event="codex-edit-1",
             note_session="other-session", witness=True, expected=0,
             reject_ref=True)
        case(root / "no-witness", referenced_event="codex-edit-1",
             note_session="fixture", witness=False, expected=0)
    print("PASS: Codex explicit event note, session, and Git witness gates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
