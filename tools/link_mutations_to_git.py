"""Link agent edits to Git commits only when a unique content transition proves it."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cairn_unified import compaction_link, semantic
from cairn_unified.adapters import opencode
from cairn_unified.code import git
from cairn_unified.store import Store
from tools.causal_scope import require_sources
from tools.index_agent_history import _events, _sources, mixed_mutation_paths


def _time(value: str):
    try:
        if value.isdigit():
            stamp = int(value)
            return datetime.fromtimestamp(stamp / (1000 if stamp > 10**11 else 1),
                                          timezone.utc)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def _blob(repo: Path, commit: str, path: str) -> str | None:
    if not commit:
        return None
    result = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=repo,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if result.returncode:
        return None
    return result.stdout.decode("utf-8", "replace").replace("\r\n", "\n")


def _transition(old: str, new: str, before: str | None, after: str | None,
                tool: str) -> bool:
    if after is None:
        after = ""
    if before is None:
        before = ""
    old = old.replace("\r\n", "\n")
    new = new.replace("\r\n", "\n")
    if old == new:
        return False
    if tool.lower() in {"write", "write_file"} and new and not old:
        return after == new and before != after
    if old and new:
        return (old in before and new in after
                and before.count(old) > after.count(old)
                and after.count(new) > before.count(new))
    if old:
        return before.count(old) > after.count(old)
    return bool(new and after.count(new) > before.count(new))


def _patch_hunks(patch: str) -> list[tuple[str, str]]:
    hunks = []
    old_lines: list[str] = []
    new_lines: list[str] = []
    changed = False
    for line in patch.splitlines():
        if line.startswith("@@"):
            if changed:
                hunks.append(("\n".join(old_lines), "\n".join(new_lines)))
            old_lines, new_lines, changed = [], [], False
        elif line.startswith(" "):
            old_lines.append(line[1:])
            new_lines.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            old_lines.append(line[1:])
            changed = True
        elif line.startswith("+") and not line.startswith("+++"):
            new_lines.append(line[1:])
            changed = True
    if changed:
        hunks.append(("\n".join(old_lines), "\n".join(new_lines)))
    return hunks


def _patch_witness(patch: str, before: str | None,
                   after: str | None) -> list[dict] | None:
    hunks = _patch_hunks(patch)
    if not hunks or sum(len(a) + len(b) for a, b in hunks) < 24:
        return None
    before, after = before or "", after or ""
    witnesses = []
    for old, new in hunks:
        old_changed = bool(old and before.count(old) > after.count(old))
        new_changed = bool(new and after.count(new) > before.count(new))
        if (old == new or (old and old not in before)
                or (new and new not in after)
                or not (old_changed or new_changed)):
            return None
        witnesses.append({
            "old_sha256": compaction_link.sha256_text(old) if old else "",
            "new_sha256": compaction_link.sha256_text(new) if new else "",
            "old_offset": before.find(old) if old else -1,
            "new_offset": after.find(new) if new else -1,
            "old_length": len(old), "new_length": len(new),
        })
    return witnesses


def verify_stored(store: Store, repo: Path, repo_id: str) -> dict:
    rows = store.read(
        "SELECT r.condition_json, o.payload_json FROM relations r "
        "JOIN objects o ON o.id = r.to_id "
        "WHERE r.relation_type = 'mutation_in_commit'")
    checked = 0
    unique = set()
    failures = []
    for row in rows:
        fact = json.loads(row["payload_json"])
        if (fact.get("semantic") or {}).get("repo_id") != repo_id:
            continue
        condition = json.loads(row["condition_json"] or "{}")
        if condition.get("basis") != "unique_git_content_transition":
            continue
        checked += 1
        unique.add((condition.get("commit"), condition.get("event_id"),
                    condition.get("path"), condition.get("source_kind")))
        commit = str(condition.get("commit") or "")
        parent = str(condition.get("parent") or "")
        path = str(condition.get("path") or "")
        if not commit or not path or git.git(repo, "show", "-s", "--format=%P", commit).split()[:1] != ([parent] if parent else []):
            failures.append({"commit": commit, "path": path, "reason": "wrong_parent"})
            continue
        fragments = condition.get("witnesses") or [condition]
        for fragment in fragments:
          for side, revision in (("old", parent), ("new", commit)):
            digest = str(fragment.get(f"{side}_sha256") or "")
            if not digest:
                continue
            content = _blob(repo, revision, path)
            offset = int(fragment.get(f"{side}_offset", -1))
            length = int(fragment.get(f"{side}_length", -1))
            if (content is None or offset < 0 or length <= 0
                    or compaction_link.sha256_text(content[offset:offset + length]) != digest):
                failures.append({"commit": commit, "path": path,
                                 "reason": f"{side}_fragment_mismatch"})
    return {"checked": checked, "unique_edits": len(unique),
            "duplicate_links": checked - len(unique), "failed": len(failures),
            "failures": failures[:20]}


def run(root: Path, repo: Path, sources: list[Path], apply: bool) -> dict:
    store = Store.open(root)
    repo_id = require_sources(repo, sources)
    archive = [json.loads(row["payload_json"]) for row in store.read(
        "SELECT payload_json FROM events WHERE event_type = 'git.commit_indexed' "
        "AND aggregate_id = ?", (repo_id,))]
    retracted = {json.loads(row["payload_json"]).get("commit") for row in store.read(
        "SELECT payload_json FROM events WHERE event_type = 'git.commit_retracted' "
        "AND aggregate_id = ?", (repo_id,))}
    by_path = defaultdict(list)
    for commit in archive:
        if commit["commit"] in retracted:
            continue
        for file in commit.get("files") or []:
            by_path[file["path"]].append(commit)
    sessions = {}
    for row in store.read("SELECT id, payload_json FROM objects WHERE object_type = 'session'"):
        body = json.loads(row["payload_json"])
        meta = body.get("semantic") or {}
        sessions[(str(meta.get("source_kind")), str(meta.get("session_id")))] = row["id"]
    cache = {}
    counts = Counter()
    matched = []
    seen = set()
    for source_repo in sources:
        for source, journal, source_sid in _sources(
                source_repo, Path(opencode.default_db())):
            events = _events(source, journal, source_sid)
            if mixed_mutation_paths(events, source_repo, sources):
                counts["mixed_sessions_skipped"] += 1
                continue
            for event in events:
                if event.get("kind") != "mutation" or event.get("status") != "completed":
                    continue
                event_id = str(event.get("event_id") or "")
                key = (source, event_id)
                if key in seen:
                    continue
                seen.add(key)
                counts["mutations"] += 1
                sid = str(source_sid or event.get("session_id") or journal.stem)
                session_id = sessions.get((source, sid))
                path = compaction_link.normalize_path(event.get("path") or "", source_repo)
                if not session_id or not path:
                    counts["no_indexed_session_or_path"] += 1
                    continue
                old, new = str(event.get("old_string") or ""), str(event.get("new_string") or "")
                patch = str(event.get("patch") or "")
                if not old and not new and not patch:
                    counts["path_only"] += 1
                    continue
                candidates = []
                event_time = _time(str(event.get("ts") or ""))
                for item in by_path.get(path, []):
                    commit_time = _time(str(item.get("committed_at") or ""))
                    if event_time and commit_time and commit_time < event_time:
                        continue
                    sha = item["commit"]
                    parent = (item.get("parents") or [""])[0]
                    pair = (sha, path)
                    if pair not in cache:
                        cache[pair] = (_blob(repo, parent, path), _blob(repo, sha, path))
                    before, after = cache[pair]
                    if old or new:
                        witness = ([{"old_sha256": compaction_link.sha256_text(old.replace("\r\n", "\n")) if old else "",
                                     "new_sha256": compaction_link.sha256_text(new.replace("\r\n", "\n")) if new else "",
                                     "old_offset": before.find(old.replace("\r\n", "\n")) if old and before else -1,
                                     "new_offset": after.find(new.replace("\r\n", "\n")) if new and after else -1,
                                     "old_length": len(old.replace("\r\n", "\n")),
                                     "new_length": len(new.replace("\r\n", "\n"))}]
                                   if _transition(old, new, before, after,
                                                  str(event.get("tool") or "")) else None)
                    else:
                        witness = _patch_witness(patch, before, after)
                    if witness:
                        candidates.append((item, before, after, witness))
                if len(candidates) != 1:
                    counts["ambiguous" if candidates else "no_unique_transition"] += 1
                    continue
                item, before, after, witness = candidates[0]
                nodes = store.read(
                    "SELECT stable_key FROM code_symbols WHERE repo_id = ? "
                    "AND path = ? AND qualified_name LIKE 'whole:%'", (repo_id, path))
                if len(nodes) != 1:
                    counts["no_whole_node"] += 1
                    continue
                matched.append({"source": source, "session_id": session_id,
                                "event_id": event_id, "path": path,
                                "node_key": nodes[0]["stable_key"],
                                "commit": item["commit"],
                                "parent": (item.get("parents") or [""])[0],
                                "committed_at": item.get("committed_at"),
                                "witnesses": witness,
                                "patch_sha256": compaction_link.sha256_text(patch) if patch else "",
                                "old_sha256": compaction_link.sha256_text(
                                    old.replace("\r\n", "\n")) if old else "",
                                "new_sha256": compaction_link.sha256_text(
                                    new.replace("\r\n", "\n")) if new else "",
                                "old_offset": before.find(old.replace("\r\n", "\n")) if old and before else -1,
                                "new_offset": after.find(new.replace("\r\n", "\n")) if new and after else -1,
                                "old_length": len(old.replace("\r\n", "\n")),
                                "new_length": len(new.replace("\r\n", "\n"))})
                counts["matched"] += 1
                counts[f"matched_{source}"] += 1
    by_witness = defaultdict(list)
    for row in matched:
        fingerprint = (row["commit"], row["path"],
                       tuple((part["old_sha256"], part["new_sha256"])
                             for part in row["witnesses"]))
        by_witness[fingerprint].append(row)
    duplicates = {id(row) for group in by_witness.values() if len(group) > 1
                  for row in group}
    if duplicates:
        matched = [row for row in matched if id(row) not in duplicates]
        counts["shared_witness_rejected"] = len(duplicates)
        counts["matched"] -= len(duplicates)
        for group in by_witness.values():
            if len(group) > 1:
                for row in group:
                    counts[f"matched_{row['source']}"] -= 1
    if apply:
        facts = {}
        actor = {"kind": "system", "id": "git-content-witness"}
        existing = set()
        for prior in store.read(
                "SELECT condition_json FROM relations "
                "WHERE relation_type = 'mutation_in_commit'"):
            condition = json.loads(prior["condition_json"] or "{}")
            existing.add((str(condition.get("commit") or ""),
                          str(condition.get("event_id") or ""),
                          str(condition.get("path") or ""),
                          str(condition.get("source_kind") or "")))
        for row in matched:
            sha = row["commit"]
            identity = (sha, row["event_id"], row["path"], row["source"])
            if identity in existing:
                counts["replayed"] += 1
                continue
            if sha not in facts:
                facts[sha] = compaction_link.link_commit(
                    store, repo_id=repo_id, repo_path=repo, commit=sha,
                    work_id="", actor=actor)["fact_id"]
            if not facts[sha]:
                counts["commit_fact_failed"] += 1
                continue
            result = semantic.relate(
                store, from_id=row["session_id"], to_id=facts[sha],
                relation="mutation_in_commit", actor=actor,
                condition={"basis": "unique_git_content_transition",
                           "evidence_level": "content_transition",
                           "event_id": row["event_id"], "node_key": row["node_key"],
                           "path": row["path"], "commit": sha,
                           "parent": row["parent"],
                           "old_sha256": row["old_sha256"],
                           "new_sha256": row["new_sha256"],
                           "old_offset": row["old_offset"],
                           "new_offset": row["new_offset"],
                           "old_length": row["old_length"],
                           "new_length": row["new_length"],
                           "witnesses": row["witnesses"],
                           "patch_sha256": row["patch_sha256"],
                           "source_kind": row["source"]},
                idempotency_key=(f"content-witness:v2:{repo_id}:{sha}:"
                                 f"{row['source']}:{row['event_id']}:{row['node_key']}"))
            counts["linked" if not result.get("replayed") else "replayed"] += 1
            existing.add(identity)
    return {"repo_id": repo_id, "apply": apply, "counts": dict(counts),
            "matched_commits": len({row["commit"] for row in matched}),
            "matches": [{key: row[key] for key in
                         ("source", "event_id", "path", "commit", "committed_at")}
                        for row in matched] if not apply else [],
            "doctor_ok": store.doctor()["ok"] if apply else None}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source-repo", type=Path, action="append")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    repo = args.repo.resolve()
    if args.verify:
        store = Store.open(args.root.resolve())
        result = verify_stored(store, repo, git.identity(repo)["id"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not result["failed"] else 1
    sources = [path.resolve() for path in (args.source_repo or [repo])]
    result = run(args.root.resolve(), repo, sources, args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["doctor_ok"] is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
