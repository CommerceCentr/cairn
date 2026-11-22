"""Index every reachable Git commit in compact, portable Cairn events."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified.code import git  # noqa: E402
from cairn_unified.errors import CairnError  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

ACTOR = {"kind": "system", "id": "git-archive-indexer"}


def _metadata(repo: Path) -> list[dict]:
    fmt = "%H%x1f%P%x1f%cI%x1f%an <%ae>%x1f%cn <%ce>%x1f%G?%x1f%B%x1e"
    raw = git.git(repo, "log", "--all", "--topo-order", "--reverse",
                  f"--format={fmt}", timeout=180)
    out = []
    for chunk in raw.split("\x1e"):
        fields = chunk.strip("\r\n").split("\x1f", 6)
        if len(fields) != 7:
            continue
        sha, parents, committed_at, author, committer, signature, message = fields
        out.append({"commit": sha, "parents": parents.split(),
                    "committed_at": committed_at, "author": author,
                    "committer": committer, "signature_status": signature,
                    "message": message.strip()})
    return out


def _files(repo: Path, sha: str) -> tuple[list[dict], str]:
    status = git.git(repo, "show", "--name-status", "--format=", sha, timeout=120)
    files = []
    for line in status.splitlines():
        columns = line.split("\t")
        if len(columns) >= 2 and columns[0][:1] in {"A", "M", "D", "R", "C", "T"}:
            files.append({"path": columns[-1], "status": columns[0],
                          "deleted_lines": []})
    if not files:
        return files, ""
    by_path = {item["path"]: item for item in files}
    try:
        patch = git.git(repo, "show", "--format=", "--unified=0", "--no-ext-diff",
                        "--no-textconv", sha, timeout=20)
    except CairnError as exc:
        return files, exc.code
    old_path = ""
    new_path = ""
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            old_path = new_path = ""
            in_hunk = False
        elif line.startswith("--- "):
            old_path = line[4:].strip('"')
            old_path = old_path[2:] if old_path.startswith("a/") else old_path
        elif line.startswith("+++ "):
            new_path = line[4:].strip('"')
            new_path = new_path[2:] if new_path.startswith("b/") else new_path
        elif line.startswith("@@ "):
            in_hunk = True
        elif in_hunk and line.startswith("-") and not line.startswith("---"):
            path = new_path if new_path != "/dev/null" else old_path
            if path in by_path:
                by_path[path]["deleted_lines"].append(line[1:])
    return files, ""


def run(root: Path, repo: Path) -> dict:
    start = time.perf_counter()
    store = Store.open(root)
    identity = git.identity(repo)
    repo_id = identity["id"]
    metadata = _metadata(repo)
    existing = {row["idempotency_key"] for row in store.read(
        "SELECT idempotency_key FROM events WHERE event_type = 'git.commit_indexed'")}
    report = {"repo_id": repo_id, "git_commits": len(metadata),
              "indexed": 0, "replayed": 0, "changed_paths": 0,
              "deleted_lines": 0, "unsigned_commits": 0,
              "patch_omissions": []}
    for position, item in enumerate(metadata):
        key = f"git-archive:v1:{repo_id}:{item['commit']}"
        if key in existing:
            report["replayed"] += 1
            continue
        files, patch_omission = _files(repo, item["commit"])
        payload = {**item, "repo_id": repo_id, "topo_index": position,
                   "files": files, "identity_evidence": "git_object_only",
                   "patch_omission": patch_omission}
        store.mutate(lambda con: store.append(
            con, "git.commit_indexed", repo_id, payload, ACTOR, key,
            occurred_at=item["committed_at"]))
        report["indexed"] += 1
        report["changed_paths"] += len(files)
        report["deleted_lines"] += sum(len(f["deleted_lines"]) for f in files)
        report["unsigned_commits"] += item["signature_status"] == "N"
        if patch_omission:
            report["patch_omissions"].append({"commit": item["commit"],
                                               "reason": patch_omission})
        if report["indexed"] % 100 == 0:
            print(f"git archive {report['indexed']}/{len(metadata)}", file=sys.stderr,
                  flush=True)
    report["doctor_ok"] = store.doctor()["ok"]
    report["elapsed_seconds"] = round(time.perf_counter() - start, 3)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.root.resolve(), args.repo.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["doctor_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
