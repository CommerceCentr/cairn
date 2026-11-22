"""Reindex a repository and record Git commit participation in a shared Cairn.

Git facts are agent neutral. Session causation must be imported separately from
the owning agent's structured mutation journal.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import code, compaction_link, nodes, verification  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

ACTOR = {"kind": "system", "id": "git-history-reindex"}


def run(root: Path, repo: Path, *, apply: bool, verify: bool) -> dict:
    store = Store.open(root)
    identity = code.git.identity(str(repo))
    commits = [sha for sha in code.git.git(str(repo), "rev-list", "--reverse", "HEAD")
               .splitlines() if sha]
    repo_id = identity["id"]
    before = store.read_one(
        "SELECT COUNT(*) AS symbols, COUNT(DISTINCT path) AS paths "
        "FROM code_symbols WHERE repo_id = ?", (repo_id,))
    report = {"repo_id": repo_id, "commits_in_git": len(commits),
              "before": dict(before) if before else {"symbols": 0, "paths": 0},
              "applied": apply}
    if not apply:
        return report

    store.mutate(lambda con: store.append(
        con, "repository.upserted", repo_id, identity, ACTOR,
        f"repo:{repo_id}"))
    report["index"] = nodes.index(store, repo_id, ACTOR, historical=True)
    facts: list[dict] = []
    for sha in commits:
        linked = compaction_link.link_commit(
            store, repo_id=repo_id, repo_path=repo, commit=sha,
            work_id="", actor=ACTOR)
        fact = {"commit": sha, "fact_id": linked.get("fact_id"),
                "new_edges": len(linked.get("mutated") or []),
                "patches": len(linked.get("patches") or []),
                "deleted_lines": sum(p["deleted_lines"] for p in linked.get("patches") or []),
                "unresolved": linked.get("unresolved") or []}
        if verify and fact["fact_id"]:
            check = verification.verify_object(
                store, fact["fact_id"], repo_path=repo)
            fact["verified"] = bool(check.get("verified"))
            if not fact["verified"]:
                fact["verification_reasons"] = check.get("reasons")
            patch_checks = [verification.verify_object(
                store, patch["object_id"], repo_path=repo)
                for patch in linked.get("patches") or []]
            fact["patches_verified"] = sum(bool(result.get("verified"))
                                           for result in patch_checks)
            fact["patches_failed"] = [result["object_id"] for result in patch_checks
                                      if not result.get("verified")]
        facts.append(fact)
    report["facts"] = facts
    report["new_git_edges"] = sum(item["new_edges"] for item in facts)
    report["patches"] = sum(item["patches"] for item in facts)
    report["deleted_lines"] = sum(item["deleted_lines"] for item in facts)
    report["unresolved_paths"] = sum(len(item["unresolved"]) for item in facts)
    report["doctor_ok"] = store.doctor()["ok"]
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    report = run(args.root.resolve(), args.repo.resolve(),
                 apply=args.apply, verify=args.verify)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not args.apply or report["doctor_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
