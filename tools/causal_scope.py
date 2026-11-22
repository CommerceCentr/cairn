"""Preflight guards for a repository-specific causal evidence bundle."""

from __future__ import annotations

import json
from pathlib import Path

from cairn_unified.code import git
from cairn_unified.errors import CairnError


def require_sources(repo: Path, sources: list[Path]) -> str:
    target = git.identity(repo)["id"]
    for source in sources:
        actual = git.identity(source)["id"]
        if actual != target:
            raise CairnError(
                "source_repo_mismatch",
                "source-repo належить іншій Git-історії",
                exit_code=4,
                details={"expected_repo_id": target, "source_repo_id": actual},
            )
    return target


def require_exclusive_store(store, repo_id: str) -> dict:
    """Fail closed: an unfiltered federation bundle must contain one zone."""
    registered = {str(row["id"]) for row in store.read("SELECT id FROM repositories")}
    observed = set(registered)
    for row in store.read(
            "SELECT event_type, aggregate_id, payload_json FROM events WHERE "
            "event_type IN ('repository.upserted','git.commit_indexed',"
            "'git.commit_retracted','code.indexed','object.upserted')"):
        event_type = str(row["event_type"])
        payload = json.loads(row["payload_json"] or "{}")
        if event_type == "repository.upserted":
            observed.add(str(payload.get("id") or row["aggregate_id"] or ""))
        elif event_type in {"git.commit_indexed", "git.commit_retracted", "code.indexed"}:
            observed.add(str(payload.get("repo_id") or row["aggregate_id"] or ""))
        elif event_type == "object.upserted":
            semantic = payload.get("semantic") or {}
            if semantic.get("repo_id"):
                observed.add(str(semantic["repo_id"]))
    observed.discard("")
    if observed != {repo_id}:
        raise CairnError(
            "bundle_zone_mismatch",
            "сховище містить іншу зону; повний пакет експортувати не можна",
            exit_code=4,
            details={"expected_repo_id": repo_id, "observed_repo_ids": sorted(observed)},
        )
    return {"repo_id": repo_id, "observed_repo_ids": sorted(observed),
            "exclusive": True}
