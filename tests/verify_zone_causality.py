"""A node's history keeps evidence from different agent sessions separate."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fixture_repo import PY_FILE, make_repo  # noqa: E402
from cairn_unified import code, compaction_link, nodes, semantic, why  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

ACTOR = {"kind": "system", "id": "zone-fixture"}


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="zone-causality-"))
    try:
        repo = make_repo(temp / "repo", files={PY_FILE: "def run():\n    return 1\n"},
                         second={PY_FILE: "def run():\n    return 2\n"})
        Store(temp / "store").init()
        store = Store.open(temp / "store")
        ident = code.git.identity(repo["path"])
        store.mutate(lambda con: store.append(
            con, "repository.upserted", ident["id"], ident, ACTOR,
            f"repo:{ident['id']}"))
        nodes.index(store, ident["id"], ACTOR, historical=True)
        whole = nodes.resolve(store, ident["id"], PY_FILE,
                              address=f"whole:{PY_FILE}")[0]["stable_key"]
        facts = {}
        for sha in (repo["first"], repo["head"]):
            facts[sha] = compaction_link.link_commit(
                store, repo_id=ident["id"], repo_path=repo["path"],
                commit=sha, work_id="", actor=ACTOR)["fact_id"]

        for sha, source in ((repo["first"], "claude_code_jsonl"),
                            (repo["head"], "codex_rollout")):
            sid = semantic.put_object(
                store, object_type="session", title=source, summary="",
                actor=ACTOR, stable_key=f"fixture-{source}",
                semantic={"kind": "session", "session_id": source,
                          "source_kind": source, "commit": sha})["object"]["id"]
            semantic.relate(store, from_id=sid, to_id=facts[sha],
                            relation="context_for_commit", actor=ACTOR)
            auto = semantic.put_object(
                store, object_type="decision", title="intent", summary="intent",
                actor=ACTOR, stable_key=f"auto-{source}",
                semantic={"kind": "auto_compaction", "commit": sha,
                          "session_obj_id": sid,
                          "intent_refs": [{"message_id": source, "seq": 1,
                                           "text": f"intent from {source}"}]})["object"]["id"]
            semantic.relate(store, from_id=auto, to_id=sid,
                            relation="derived_from", actor=ACTOR)
            if source == "claude_code_jsonl":
                semantic.relate(
                    store, from_id=whole, to_id=sid, relation="caused_by",
                    actor=ACTOR, condition={"basis": "tool_mutation",
                                            "evidence": {"event_id": "edit-1",
                                                         "path": PY_FILE,
                                                         "intent": "intent from Claude"}})
                semantic.relate(
                    store, from_id=sid, to_id=facts[sha],
                    relation="mutation_in_commit", actor=ACTOR,
                    condition={"event_id": "edit-1", "node_key": whole,
                               "path": PY_FILE, "evidence_level": "participation"})

        all_history = why.answer(store, repo_id=ident["id"], path=PY_FILE)
        first = why.answer(store, repo_id=ident["id"], path=PY_FILE,
                           commit=repo["first"])
        second = why.answer(store, repo_id=ident["id"], path=PY_FILE,
                            commit=repo["head"])
        actual = {x["commit"]: x["direct_mutations"] for x in
                  all_history["commit_history"]}
        assert actual == {repo["first"]: 1, repo["head"]: 0}, actual
        assert first["coverage"]["node_changes"] == 1
        assert second["coverage"]["node_changes"] == 0
        assert "intent from claude_code_jsonl" in first["answer"]
        assert "intent from codex_rollout" in second["answer"]
        assert "intent from claude_code_jsonl" not in second["answer"]
        patch = second["commit_history"][0]["patches"][0]
        assert patch["deleted_lines"] == ["    return 1"]
        assert patch["added_lines"] == ["    return 2"]
        assert "вилучено" in second["answer"]
        print("PASS: two commits, agent intents separated, direct mutation scoped")
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
