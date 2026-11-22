"""Read-only acceptance report for a shared Cairn repository."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cairn_unified.adapters import opencode
from cairn_unified.code import git
from cairn_unified.store import Store
from cairn_unified.why import answer
from tools.index_agent_history import run as survey_agents
from tools.link_mutations_to_git import verify_stored
from tools.measure_vec_mvp import measure


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path, action="append")
    parser.add_argument("--path", default="cairn_unified/why.py")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    root, repo = args.root.resolve(), args.repo.resolve()
    store = Store.open(root)
    repo_id = git.identity(repo)["id"]
    baseline = measure(root, repo, args.path)
    node = answer(store, repo_id=repo_id, path=args.path)
    source_repos = [p.resolve() for p in (args.source_repo or [repo])]
    surveys = [survey_agents(root, p, Path(opencode.default_db()), False)
               for p in source_repos]
    report = {
        "repo_id": repo_id, "head": git.git(repo, "rev-parse", "HEAD").strip(),
        "git_commits": baseline["git_commits"],
        "git_facts": baseline["git_facts"],
        "missing_commits": baseline["missing_commits"],
        "nodes": baseline["nodes"], "paths": baseline["paths"],
        "deleted_lines_stored": baseline["deleted_lines_stored"],
        "session_objects": baseline["session_objects"],
        "session_sources": baseline["session_sources"],
        "session_surveys": [{"repo": str(p), **s}
                            for p, s in zip(source_repos, surveys)],
        "zone_context": node["zone_context"],
        "path_probe": baseline["path_probe"],
        "node_history": [{"commit": h["commit"], "evidence": h["evidence"],
                          "direct_mutations": h["direct_mutations"]}
                         for h in node["commit_history"]],
        "node_coverage": node["coverage"],
        "witness_verification": verify_stored(store, repo, repo_id),
        "events": baseline["event_count"], "ledger_bytes": baseline["ledger_bytes"],
        "doctor_ok": baseline["doctor_ok"],
    }
    if args.out:
        target = args.out.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["doctor_ok"] and not report["missing_commits"] \
        and not report["witness_verification"]["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
