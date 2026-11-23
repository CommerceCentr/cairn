"""A reason enters node history only after the exact edited file is committed."""

from __future__ import annotations

import sys
import json
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fixture_repo import PY_FILE, git, make_repo  # noqa: E402
from cairn_unified import change_notes, federation, mcp, nodes, why  # noqa: E402
from cairn_unified.code import git as cairn_git  # noqa: E402
from cairn_unified.store import Store  # noqa: E402
from tools.index_git_archive import run as index_git  # noqa: E402

ACTOR = {"kind": "agent", "id": "fixture-agent"}
HUMAN = {"kind": "human", "id": "fixture-owner"}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="cairn-change-note-") as temp:
        base = Path(temp)
        fixture = make_repo(base / "repo", files={PY_FILE: "value = 1\n"},
                            second={PY_FILE: "value = 2\n"})
        repo = Path(fixture["path"])
        Store(base / "store").init()
        store = Store.open(base / "store")
        identity = cairn_git.identity(repo)
        store.mutate(lambda con: store.append(
            con, "repository.upserted", identity["id"], identity, ACTOR,
            f"repo:{identity['id']}"))
        nodes.index(store, identity["id"], ACTOR, paths=[PY_FILE])

        (repo / PY_FILE).write_text("value = 3\n", encoding="utf-8")
        response = mcp.call_tool(store, "cairn_change_note", {
            "repo_path": str(repo), "paths": [PY_FILE],
            "why": "remove an artificial limit", "intent": "retain full node history",
            "session_id": "fixture-session"}, ACTOR)
        note_id = response["data"]["note_id"]
        assert change_notes.status(store, repo, note_id)["notes"][0]["status"] == "unlinked"
        cli = subprocess.run([
            sys.executable, "-m", "cairn_unified", "--root", str(base / "store"),
            "--actor", "agent:fixture-agent", "change", "note",
            "--repo-path", str(repo), "--path", PY_FILE,
            "--why", "remove an artificial limit",
            "--intent", "retain full node history",
            "--session-id", "fixture-session",
        ], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        assert cli.returncode == 0, cli.stderr
        replay = json.loads(cli.stdout)
        assert replay["replayed"] and replay["note_id"] == note_id
        assert change_notes.sync(store, repo)["linked"] == 0
        portable_bundle = federation.export_bundle(store)
        federation.restore_bundle(portable_bundle, base / "portable", HUMAN)
        portable = Store.open(base / "portable")

        git(repo, "stash", "push", "-q", "--", PY_FILE)
        assert change_notes.sync(store, repo)["linked"] == 0
        git(repo, "stash", "pop", "-q")
        (repo / "unrelated.txt").write_text("other work\n", encoding="utf-8")
        git(repo, "add", "unrelated.txt")
        git(repo, "commit", "-qm", "unrelated commit before noted file")
        unrelated = git(repo, "rev-parse", "HEAD")
        git(repo, "add", PY_FILE)
        git(repo, "commit", "-qm", "keep full history")
        committed = git(repo, "rev-parse", "HEAD")
        index_git(base / "store", repo)
        result = change_notes.sync(store, repo)
        assert result["linked"] == 1 and result["pending"] == 0
        assert unrelated not in change_notes.linked(store, identity["id"], PY_FILE)
        assert change_notes.status(store, repo, note_id)["notes"][0]["status"] == "linked"
        assert change_notes.status(store, repo, note_id)["notes"][0]["status_at_capture"] == "pending_commit"
        status_cli = subprocess.run([
            sys.executable, "-m", "cairn_unified", "--root", str(base / "store"),
            "change", "status", "--repo-path", str(repo), "--note-id", note_id,
        ], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        assert status_cli.returncode == 0
        assert json.loads(status_cli.stdout)["notes"][0]["status"] == "linked"
        assert change_notes.sync(portable, repo)["linked"] == 1
        assert change_notes.linked(portable, identity["id"], PY_FILE)[committed][0]["why"] == "remove an artificial limit"
        assert change_notes.sync(store, repo)["linked"] == 0
        answer = why.answer(store, repo_id=identity["id"], path=PY_FILE,
                            commit=committed, repo_path=str(repo), compact=True)
        assert answer["coverage"]["node_attested_causes"] == 0
        assert answer["coverage"]["file_state_attested_reasons"] == 1
        assert "remove an artificial limit" in answer["answer"]
        assert answer["commit_history"][0]["change_reasons"][0]["intent"] == "retain full node history"
        assert answer["commit_history"][0]["change_reasons"][0]["scope"] == "file"

        (repo / PY_FILE).write_text("value = 4\n", encoding="utf-8")
        discarded = change_notes.note(store, repo, [PY_FILE],
                                      "temporary experiment", "try alternate value",
                                      ACTOR, "fixture-session")
        git(repo, "restore", "--", PY_FILE)
        assert change_notes.sync(store, repo)["pending"] == 1
        assert change_notes.status(store, repo, discarded["note_id"])["notes"][0]["status"] == "unlinked"
        assert not any(discarded["note_id"] == item["note_id"]
                       for reasons in change_notes.linked(store, identity["id"], PY_FILE).values()
                       for item in reasons)

        (repo / PY_FILE).write_text("value = 5\n", encoding="utf-8")
        first_claim = change_notes.note(store, repo, [PY_FILE],
                                        "reason A", "intent A", ACTOR)
        second_claim = change_notes.note(store, repo, [PY_FILE],
                                         "reason B", "intent B", ACTOR)
        git(repo, "add", PY_FILE)
        git(repo, "commit", "-qm", "ambiguous rationale")
        ambiguity = change_notes.sync(store, repo)
        assert ambiguity["linked"] == 0 and ambiguity["ambiguous"] >= 1
        linked_ids = {item["note_id"] for reasons in
                      change_notes.linked(store, identity["id"], PY_FILE).values()
                      for item in reasons}
        assert first_claim["note_id"] not in linked_ids
        assert second_claim["note_id"] not in linked_ids

        # The note describes a transition from value 5 to value 6. A later
        # commit returning to value 6 from value 7 has a different cause.
        (repo / PY_FILE).write_text("value = 6\n", encoding="utf-8")
        earlier_reason = change_notes.note(store, repo, [PY_FILE],
                                           "move from five to six", "use six", ACTOR)
        (repo / PY_FILE).write_text("value = 7\n", encoding="utf-8")
        git(repo, "add", PY_FILE)
        git(repo, "commit", "-qm", "intervening change to seven")
        (repo / PY_FILE).write_text("value = 6\n", encoding="utf-8")
        git(repo, "add", PY_FILE)
        git(repo, "commit", "-qm", "return to six for another reason")
        assert change_notes.sync(store, repo)["linked"] == 0
        linked_ids = {item["note_id"] for reasons in
                      change_notes.linked(store, identity["id"], PY_FILE).values()
                      for item in reasons}
        assert earlier_reason["note_id"] not in linked_ids
        assert store.doctor()["ok"]
    print("PASS: note, portable restore, stash, late commit, replay, why, discard, ambiguity, revert")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
