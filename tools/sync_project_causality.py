"""Explicit write-side sync of Git, agent edits and portable Cairn evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cairn_unified import change_notes, nodes
from cairn_unified.adapters import opencode
from cairn_unified.code import git
from cairn_unified.federation import export_bundle
from cairn_unified.store import Store
from tools.index_agent_history import run as index_agents
from tools.index_git_archive import run as index_git
from tools.link_mutations_to_git import run as link_evidence, verify_stored
from tools.causal_scope import require_exclusive_store, require_sources


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path, action="append")
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    root, repo = args.root.resolve(), args.repo.resolve()
    sources = [path.resolve() for path in (args.source_repo or [repo])]
    store = Store.open(root)
    repo_id = require_sources(repo, sources)
    if not store.read_one("SELECT id FROM repositories WHERE id = ?", (repo_id,)):
        raise SystemExit("repository not registered; run `repo add` first")
    if args.bundle:
        require_exclusive_store(store, repo_id)
    report = {"repo_id": repo_id, "head": git.git(repo, "rev-parse", "HEAD").strip()}
    report["nodes"] = nodes.index(
        store, repo_id, {"kind": "system", "id": "project-causality-sync"},
        historical=True, tracked_only=True)
    report["git"] = index_git(root, repo)
    report["agent_sources"] = []
    for source_repo in sources:
        result = index_agents(root, source_repo, Path(opencode.default_db()), True,
                              allowed_roots=sources)
        report["agent_sources"].append({"repo": str(source_repo), **result})
    report["witnesses"] = link_evidence(root, repo, sources, True)
    report["change_notes"] = change_notes.sync(store, repo)
    report["witness_verification"] = verify_stored(store, repo, repo_id)
    report["doctor_ok"] = store.doctor()["ok"]
    if args.bundle:
        report["zone_gate"] = require_exclusive_store(store, repo_id)
        bundle = export_bundle(store, path=args.bundle.resolve())
        report["bundle"] = {"path": str(args.bundle.resolve()),
                            "events": bundle["event_count"],
                            "digest": bundle["digest"]}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["doctor_ok"] and not report["witness_verification"]["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
