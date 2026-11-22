"""Acceptance measurements for a VEC Cairn pilot store."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified.code import git  # noqa: E402
from cairn_unified.store import Store  # noqa: E402


def measure(root: Path, repo: Path, path: str = "README.md") -> dict:
    store = Store.open(root)
    identity = git.identity(repo)
    events = store.read("SELECT event_type, payload_json FROM events")
    types = Counter(e["event_type"] for e in events)
    retracted = {json.loads(e["payload_json"]).get("commit") for e in events
                 if e["event_type"] == "git.commit_retracted"}
    archive = [json.loads(e["payload_json"]) for e in events
               if e["event_type"] == "git.commit_indexed"
               and json.loads(e["payload_json"]).get("commit") not in retracted]
    commit_ids = {p["commit"] for p in archive}
    node = store.read_one(
        "SELECT COUNT(*) AS nodes, COUNT(DISTINCT path) AS paths, "
        "SUM(precision = 'historical_file') AS historical_paths "
        "FROM code_symbols WHERE repo_id = ?", (identity["id"],))
    session_rows = store.read(
        "SELECT payload_json FROM objects WHERE object_type = 'session'")
    sessions = [json.loads(r["payload_json"]) for r in session_rows]
    source_counts = Counter(str((p.get("semantic") or {}).get("source_kind") or "unknown")
                            for p in sessions)
    doctor = store.doctor()
    expected = set(git.git(repo, "rev-list", "--all").splitlines())
    native_path = set(git.git(repo, "log", "--all", "--format=%H", "--", path).splitlines())
    archived_path = {p["commit"] for p in archive
                     if any(f.get("path") == path for f in p.get("files") or [])}
    return {
        "repo_id": identity["id"], "git_commits": len(expected),
        "git_facts": len(commit_ids), "missing_commits": sorted(expected - commit_ids),
        "extra_commits": sorted(commit_ids - expected),
        "nodes": int(node["nodes"] or 0), "paths": int(node["paths"] or 0),
        "historical_paths": int(node["historical_paths"] or 0),
        "session_objects": len(sessions), "session_sources": dict(source_counts),
        "direct_mutation_edges": store.read_one(
            "SELECT COUNT(*) AS n FROM relations WHERE relation_type = 'caused_by'")["n"],
        "deleted_lines_stored": sum(len(f.get("deleted_lines") or [])
                                    for p in archive for f in p.get("files") or []),
        "unsigned_git_commits": sum(p.get("signature_status") == "N" for p in archive),
        "path_probe": {"path": path, "native_commits": len(native_path),
                       "cairn_commits": len(archived_path),
                       "missing_in_cairn": sorted(native_path - archived_path),
                       "extra_in_cairn": sorted(archived_path - native_path)},
        "event_count": len(events), "event_types": dict(types),
        "ledger_bytes": (root / ".cairn" / "events" / "ledger.jsonl").stat().st_size,
        "doctor_ok": doctor["ok"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--path", default="README.md")
    args = parser.parse_args()
    report = measure(args.root.resolve(), args.repo.resolve(), args.path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["doctor_ok"] and not report["missing_commits"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
