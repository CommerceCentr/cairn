"""Portable, agent-stated reasons captured after an edit and gated by Git."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath

from .canon import canonical
from .code import git
from .errors import CairnError


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, check=False)


def _path(repo: Path, value: str) -> str:
    raw = str(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if (not raw or path.is_absolute() or ":" in raw or
            any(part in {"", ".", "..", ".git", ".cairn"} for part in path.parts)):
        raise CairnError("bad_change_path", "потрібен відносний шлях вузла", exit_code=2)
    relative = path.as_posix()
    target = (repo / relative).resolve()
    if not target.is_relative_to(repo.resolve()) or not target.is_file():
        raise CairnError("bad_change_path", "файл поза репозиторієм або відсутній", exit_code=2)
    return relative


def _blob_at_worktree(repo: Path, path: str) -> str:
    result = _run(repo, "hash-object", "--path", path, "--", path)
    if result.returncode:
        raise CairnError("blob_unavailable", "не вдалося зняти стан файла", exit_code=2)
    return result.stdout.decode("ascii").strip()


def note(store, repo_path: str | Path, paths: list[str], why: str, intent: str,
         actor: dict, session_id: str = "") -> dict:
    """Record one explanation for a changed file set; no commit claim yet."""
    repo = Path(repo_path).resolve()
    repo_id = git.identity(repo)["id"]
    if not store.read_one("SELECT id FROM repositories WHERE id = ?", (repo_id,)):
        raise CairnError("repo_not_registered", "спочатку додайте репозиторій у Cairn", exit_code=2)
    why, intent = str(why).strip(), str(intent).strip()
    if not why or not intent or not paths:
        raise CairnError("missing_change_reason", "потрібні paths, why та intent", exit_code=2)
    if len(why) > 4000 or len(intent) > 4000 or len(paths) > 100:
        raise CairnError("change_note_too_large", "запис причини завеликий", exit_code=2)
    snapshots = []
    for path in sorted({_path(repo, item) for item in paths}):
        status = _run(repo, "status", "--porcelain", "--untracked-files=all", "--", path)
        if status.returncode or not status.stdout.strip():
            raise CairnError("file_not_changed", f"немає незакоміченої правки: {path}", exit_code=2)
        snapshots.append({"path": path, "blob": _blob_at_worktree(repo, path)})
    base = git.head(repo)
    claim = {"repo_id": repo_id, "base_commit": base, "files": snapshots,
             "why": why, "intent": intent, "session_id": str(session_id or ""),
             "source": "explicit_agent_or_human_note", "status": "pending_commit"}
    digest = hashlib.sha256(canonical({**claim, "actor": actor}).encode("utf-8")).hexdigest()
    claim["note_id"] = digest
    event = store.mutate(lambda con: store.append(
        con, "change.note", repo_id, claim, actor,
        idempotency_key=f"change-note:v1:{repo_id}:{digest}"))
    return {"note_id": digest, "status": "pending_commit", "paths": [x["path"] for x in snapshots],
            "event_id": event["event_id"], "replayed": event["replayed"]}


def _notes(store, repo_id: str) -> list[dict]:
    return [json.loads(row["payload_json"]) for row in store.read(
        "SELECT payload_json FROM events WHERE event_type = 'change.note' "
        "AND aggregate_id = ? ORDER BY seq", (repo_id,))]


def linked(store, repo_id: str, path: str) -> dict[str, list[dict]]:
    """Read committed explanations grouped by SHA for one repository path."""
    notes = {item["note_id"]: item for item in _notes(store, repo_id)}
    out: dict[str, list[dict]] = {}
    for row in store.read(
            "SELECT payload_json FROM events WHERE event_type = 'change.linked' "
            "AND aggregate_id = ? ORDER BY seq", (repo_id,)):
        link = json.loads(row["payload_json"])
        if link.get("path") != path:
            continue
        source = notes.get(link.get("note_id"))
        if source:
            out.setdefault(link["commit"], []).append({
                "note_id": link["note_id"], "why": source["why"],
                "intent": source["intent"], "session_id": source.get("session_id") or "",
                "status": "file_state_attested", "scope": "file",
                "basis": "exact_git_blob_after_edit",
                "source_event_id": link.get("note_event_id"),
            })
    return out


def sync(store, repo_path: str | Path) -> dict:
    """Promote only unique note/commit/path matches with the exact Git blob."""
    repo = Path(repo_path).resolve()
    repo_id = git.identity(repo)["id"]
    notes = _notes(store, repo_id)
    existing = {(x["note_id"], x["commit"], x["path"]) for x in (
        json.loads(row["payload_json"]) for row in store.read(
            "SELECT payload_json FROM events WHERE event_type = 'change.linked' "
            "AND aggregate_id = ?", (repo_id,)))}
    by_path: dict[str, list[dict]] = {}
    already_linked = {(note_id, path) for note_id, _sha, path in existing}
    for item in notes:
        for file in item["files"]:
            if (item["note_id"], file["path"]) not in already_linked:
                by_path.setdefault(file["path"], []).append({**item, "blob": file["blob"]})
    linked_count = 0
    ambiguous = 0
    ancestry: dict[tuple[str, str], bool] = {}
    for path, candidates in by_path.items():
        # --all includes refs/stash: a stash must never trigger promotion.
        commits = _run(repo, "log", "HEAD", "--branches", "--tags",
                       "--format=%H", "--", path)
        if commits.returncode:
            continue
        matches_by_commit: dict[str, tuple[str, dict[str, dict]]] = {}
        commits_by_note: dict[str, set[str]] = {}
        for sha in commits.stdout.decode("ascii", "replace").splitlines():
            blob = _run(repo, "rev-parse", f"{sha}:{path}")
            if blob.returncode:
                continue
            blob_id = blob.stdout.decode("ascii", "replace").strip()
            matches = []
            for item in candidates:
                if item["blob"] != blob_id:
                    continue
                pair = (item["base_commit"], sha)
                if pair not in ancestry:
                    ancestry[pair] = _run(repo, "merge-base", "--is-ancestor",
                                          *pair).returncode == 0
                if ancestry[pair]:
                    matches.append(item)
            distinct = {item["note_id"]: item for item in matches}
            matches_by_commit[sha] = (blob_id, distinct)
            for note_id in distinct:
                commits_by_note.setdefault(note_id, set()).add(sha)
        for sha, (blob_id, distinct) in matches_by_commit.items():
            if len(distinct) > 1:
                ambiguous += 1
                continue
            if not distinct:
                continue
            item = next(iter(distinct.values()))
            if len(commits_by_note[item["note_id"]]) != 1:
                ambiguous += 1
                continue
            key = (item["note_id"], sha, path)
            if key in existing:
                continue
            if any(x[0] == item["note_id"] and x[2] == path for x in existing):
                ambiguous += 1
                continue
            source = store.read_one(
                "SELECT event_id FROM events WHERE event_type='change.note' "
                "AND idempotency_key=?", (f"change-note:v1:{repo_id}:{item['note_id']}",))
            payload = {"note_id": item["note_id"], "commit": sha, "path": path,
                       "blob": blob_id, "note_event_id": source["event_id"] if source else None,
                       "basis": "exact_git_blob_after_edit"}
            result = store.mutate(lambda con: store.append(
                con, "change.linked", repo_id, payload,
                {"kind": "system", "id": "commit-trigger"},
                idempotency_key=f"change-linked:v1:{repo_id}:{item['note_id']}:{sha}:{path}"))
            linked_count += int(not result["replayed"])
            existing.add(key)
    return {"notes": len(notes), "linked": linked_count, "ambiguous": ambiguous,
            "pending": sum(1 for item in notes if not any(x[0] == item["note_id"] for x in existing))}
