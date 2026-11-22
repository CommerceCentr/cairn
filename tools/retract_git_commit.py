"""Record that an indexed commit was removed from the current Git history."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cairn_unified.code import git
from cairn_unified.store import Store


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    root, repo = args.root.resolve(), args.repo.resolve()
    store = Store.open(root)
    repo_id = git.identity(repo)["id"]
    payload = {"repo_id": repo_id, "commit": args.commit, "reason": args.reason,
               "current_head": git.git(repo, "rev-parse", "HEAD").strip(),
               "source": "git_state_and_user_correction"}
    event = store.mutate(lambda con: store.append(
        con, "git.commit_retracted", repo_id, payload,
        {"kind": "agent", "id": "codex", "account_verification": "unavailable"},
        f"git-retraction:v1:{repo_id}:{args.commit}"))
    print(json.dumps({"event_id": event["event_id"], "replayed": event["replayed"],
                      **payload}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
