"""Create a publishable, text-free acceptance record for one Cairn zone."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cairn_unified.code import git
from cairn_unified.federation import export_bundle, load_bundle, verify_bundle
from cairn_unified.store import Store
from tools.causal_scope import require_exclusive_store
from tools.link_mutations_to_git import verify_stored
from tools.measure_vec_mvp import measure


def public_history_exposure(repo: Path) -> dict:
    """Count historical commits with absolute path markers, without printing them."""
    commits = git.git(repo, "rev-list", "--all").splitlines()
    exposed = 0
    pattern = r"[A-Z]:[/\\]|/" + r"(home|Users|mnt)/"
    for sha in commits:
        result = subprocess.run(
            ["git", "-C", str(repo), "grep", "-I", "-l", "-E", "-e", pattern, sha],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        if result.returncode not in (0, 1):
            raise RuntimeError(f"historical path scan failed for {sha[:12]}")
        exposed += result.returncode == 0
    return {"commits_scanned": len(commits), "commits_with_machine_paths": exposed}


def foreign_path_count(value, excluded_roots: list[Path]) -> int:
    forbidden = [str(path.resolve()).replace("\\", "/").lower().rstrip("/") + "/"
                 for path in excluded_roots]

    def scan(item) -> int:
        if isinstance(item, dict):
            return sum(scan(child) for child in item.values())
        if isinstance(item, list):
            return sum(scan(child) for child in item)
        if isinstance(item, str):
            normalized = item.replace("\\", "/").lower()
            return sum(normalized.count(root) for root in forbidden)
        return 0

    return scan(value)


def audit(root: Path, repo: Path, bundle_path: Path, node_path: str,
          excluded_roots: list[Path] | None = None) -> dict:
    store = Store.open(root)
    repo_id = git.identity(repo)["id"]
    scope = require_exclusive_store(store, repo_id)
    bundle = load_bundle(bundle_path)
    integrity = verify_bundle(bundle)
    baseline = measure(root, repo, node_path)
    witnesses = verify_stored(store, repo, repo_id)
    bundle_repo_ids = {str(e.get("payload", {}).get("id") or "")
                       for e in bundle.get("events") or []
                       if e.get("event_type") == "repository.upserted"}
    foreign_paths = foreign_path_count(bundle.get("events") or [], excluded_roots or [])
    public_history = public_history_exposure(repo)
    event_count = int(baseline["event_count"])
    checks = {
        "exclusive_zone": scope["exclusive"] and bundle_repo_ids == {repo_id},
        "no_excluded_repo_paths": foreign_paths == 0,
        "public_git_history_clean": public_history["commits_with_machine_paths"] == 0,
        "bundle_integrity": integrity["ok"],
        "bundle_matches_store": (bundle.get("event_count") == event_count
                                 and bundle.get("digest") == export_bundle(store)["digest"]),
        "all_git_commits_indexed": (baseline["git_commits"] == baseline["git_facts"]
                                    and not baseline["missing_commits"]),
        "node_commit_coverage": (baseline["path_probe"]["native_commits"]
                                 == baseline["path_probe"]["cairn_commits"]
                                 and not baseline["path_probe"]["missing_in_cairn"]),
        "witnesses_verify": witnesses["failed"] == 0,
        "doctor": baseline["doctor_ok"],
    }
    return {
        "format": "cairn-release-evidence/1",
        "scope": "one_repository_zone",
        "repo_id": repo_id,
        "head": git.head(repo),
        "node_path": node_path,
        "checks": checks,
        "passed": all(checks.values()),
        "git_commits": baseline["git_commits"],
        "indexed_git_commits": baseline["git_facts"],
        "node_git_commits": baseline["path_probe"]["native_commits"],
        "node_indexed_commits": baseline["path_probe"]["cairn_commits"],
        "events": event_count,
        "witness_edges": witnesses["checked"],
        "unique_witnessed_edits": witnesses["unique_edits"],
        "duplicate_witness_edges": witnesses["duplicate_links"],
        "bundle_digest": bundle.get("digest"),
        "excluded_repo_path_occurrences": foreign_paths,
        "public_git_history": public_history,
        "privacy": "numeric_allowlist_only; no session text or raw bundle",
        "limits": ["content transition proves participation, not semantic motive",
                   "account ownership is not cryptographically verified",
                   "second-device merge is not verified by this local run"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--path", default="cairn_unified/why.py")
    parser.add_argument("--exclude-root", type=Path, action="append")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.root.resolve(), args.repo.resolve(),
                   args.bundle.resolve(), args.path, args.exclude_root)
    target = args.out.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
