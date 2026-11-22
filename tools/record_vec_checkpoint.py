"""Record a Git checkpoint and local artifact inventory in Cairn."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cairn_unified.code import git
from cairn_unified.store import Store


def _names(repo: Path, *args: str) -> list[str]:
    raw = subprocess.check_output(["git", *args], cwd=repo)
    return [part.decode("utf-8", "surrogateescape") for part in raw.split(b"\0") if part]


def run(root: Path, repo: Path) -> dict:
    store = Store.open(root)
    repo_id = git.identity(repo)["id"]
    commit = git.git(repo, "rev-parse", "HEAD").strip()
    changed = _names(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD")
    untracked = _names(repo, "ls-files", "--others", "--exclude-standard", "-z", "--",
                       "artifacts", "scratchpad")
    modified = _names(repo, "diff", "--name-only", "-z", "--", "artifacts", "scratchpad")
    untracked_set = set(untracked)
    paths = sorted(untracked_set | set(modified))
    entries = []
    total_bytes = 0
    for name in paths:
        try:
            size = (repo / name).stat().st_size
        except OSError:
            size = None
        if size is not None:
            total_bytes += size
        entries.append({"path": name, "bytes": size,
                        "git_state": "untracked" if name in untracked_set else "modified"})
    manifest_digest = hashlib.sha256(json.dumps(entries, ensure_ascii=False,
                                                 sort_keys=True).encode("utf-8")).hexdigest()
    payload = {
        "repo_id": repo_id, "commit": commit,
        "intent": "Record the current Git checkpoint and inventory excluded local artifacts.",
        "source": "current_codex_user_instruction", "scope": "git_checkpoint_and_local_inventory",
        "committed_paths": len(changed), "excluded_paths": len(entries),
        "excluded_bytes": total_bytes, "excluded_manifest_sha256": manifest_digest,
        "excluded_entries": entries,
        "limitations": ["Excluded file contents are not in Git or Cairn.",
                        "Inventory is a local observation, not an authenticated agent account or file-content proof."],
    }
    event = store.mutate(lambda con: store.append(
        con, "git.commit_context", repo_id, payload,
        {"kind": "agent", "id": "codex", "account_verification": "unavailable"},
        f"git-checkpoint-context:v1:{repo_id}:{commit}"))
    return {"commit": commit, "committed_paths": len(changed),
            "excluded_paths": len(entries), "excluded_bytes": total_bytes,
            "inventory_sha256": manifest_digest, "event_id": event["event_id"],
            "replayed": event["replayed"], "doctor_ok": store.doctor()["ok"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.root.resolve(), args.repo.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["doctor_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
