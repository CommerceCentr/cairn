"""Connect verified OpenCode session edits to their explicit Git commits.

Only existing caused_by evidence that also passes the current committed-node
filter is promoted to mutation_in_commit. No journal read is a mutation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import code, compaction_link, semantic  # noqa: E402
from cairn_unified.adapters import opencode  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

ACTOR = {"kind": "system", "id": "opencode-commit-link"}


def run(root: Path, repo: Path, *, apply: bool) -> dict:
    store = Store.open(root)
    repo_id = code.git.identity(str(repo))["id"]
    bodies = [json.loads(row["payload_json"])
              for row in store.read("SELECT payload_json FROM objects")]
    facts = {str((body.get("semantic") or {}).get("commit") or ""): body
             for body in bodies
             if (body.get("semantic") or {}).get("kind") == "commit_fact"}
    result = []
    for body in bodies:
        sem = body.get("semantic") or {}
        if (sem.get("kind") != "session"
                or sem.get("session_source") != "opencode_db"):
            continue
        commit = str(sem.get("commit") or "")
        fact = facts.get(commit)
        if not fact:
            continue
        cutoff = int(code.git.git(str(repo), "show", "-s", "--format=%ct",
                                  commit).strip()) * 1000
        changed = [line.split("\t", 1)[1] for line in
                   code.git.git(str(repo), "show", "--name-status", "--format=",
                                commit).splitlines() if "\t" in line]
        allowed = compaction_link.committed_node_keys(
            store, repo_id, repo, commit, changed)
        events = [event for event in opencode.read(opencode.default_db(), sem["session_id"])
                  if event.get("ts") and int(event["ts"]) <= cutoff]
        ready, _rejected = compaction_link.mutation_readiness(
            store, events, repo_id, repo, allowed)
        existing = set()
        for row in store.read(
                "SELECT from_id, condition_json FROM relations WHERE to_id = ? "
                "AND relation_type = 'caused_by'", (body["id"],)):
            condition = json.loads(row["condition_json"] or "{}")
            event_id = (condition.get("evidence") or {}).get("event_id")
            if event_id:
                existing.add((str(event_id), str(row["from_id"])))
        matched = [(event, rel, node) for event, rel, group in ready
                   for node in group
                   if (str(event["event_id"]), str(node["stable_key"])) in existing]
        entry = {"commit": commit, "session_object_id": body["id"],
                 "candidate_edges": sum(len(group) for _, _, group in ready),
                 "verified_existing_edges": len(matched), "applied": apply}
        if apply:
            semantic.relate(
                store, from_id=body["id"], to_id=fact["id"],
                relation="context_for_commit", actor=ACTOR,
                condition={"basis": "session_commit_record",
                           "evidence_level": "association", "commit": commit,
                           "source_kind": "opencode"},
                idempotency_key=f"session-commit:{body['id']}:{commit}")
            for event, rel, node in matched:
                semantic.relate(
                    store, from_id=body["id"], to_id=fact["id"],
                    relation="mutation_in_commit", actor=ACTOR,
                    condition={"basis": "existing_verified_mutation_and_git_path",
                               "evidence_level": "participation",
                               "event_id": event["event_id"],
                               "node_key": node["stable_key"],
                               "path": rel, "commit": commit,
                               "source_kind": "opencode"},
                    idempotency_key=(f"mutation-commit:{commit}:"
                                     f"{body['id']}:{event['event_id']}:"
                                     f"{node['stable_key']}"))
        result.append(entry)
    return {"repo_id": repo_id, "sessions": result,
            "matched_edges": sum(x["verified_existing_edges"] for x in result),
            "doctor_ok": store.doctor()["ok"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = run(args.root.resolve(), args.repo.resolve(), apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["doctor_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
