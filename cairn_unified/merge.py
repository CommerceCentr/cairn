"""Злиття контексту двух агентів одного Git-репозиторію.

Інша машина може мати власний `.cairn`, власного агента й власну історію.
Об'єднання не ґрунтується на шляхах і не довіряє рядкам із bundle:

1. тотожність репозиторію беремо з ``code.git.identity`` (набір кореневих
   комітів), а не з ``descriptor["repo"]`` — рядок дескриптора лише звіряється;
2. кожен хеш коміту з bundle резолвимо локальним ``git rev-parse``; нерезолвовані
   хеші лишаються ``unlinked_commits`` і не стають ребрами;
3. захищені поля, плани зон і розбіжні об'єкти — конфлікти з явним переліком,
   а не last-write-wins; локальний стан не перезаписується мовчки.

Нових таблиць БД немає: злиття пише події ``merge.recorded`` у звичайний журнал.
"""

from __future__ import annotations

import json
import time
import re
from pathlib import Path

from . import code, federation, ledger, zones
from .canon import audit_digest, canonical, sha256_text
from .errors import CairnError
from .store import require_human

PROTOCOL = "cairn_unified.merge/1.0"
COMMIT_KEYS = frozenset({"commit", "revision", "commit_sha", "head", "base_revision"})
_SHA = re.compile(r"^[0-9a-f]{7,40}$")


def _sha(value) -> bool:
    return isinstance(value, str) and bool(_SHA.fullmatch(value.strip()))


def collect_commits(node, out: set[str] | None = None) -> set[str]:
    """Усі git-подібні хеші з будь-якої глибини bundle (лише ключі коміту)."""
    out = out if out is not None else set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key in COMMIT_KEYS and _sha(value):
                out.add(value.strip())
            else:
                collect_commits(value, out)
    elif isinstance(node, list):
        for value in node:
            collect_commits(value, out)
    return out


#: Причина, через яку хеш не резолвився. Порожній рядок означав обидві
#: різні речі: «такого коміта немає» і «git не запустився». Друге — дефект
#: середовища, і мовчки трактувати його як фальшивий хеш не можна.
GIT_UNAVAILABLE = "git_unavailable"


def _git_resolve(repo_path, value: str) -> tuple[str, str]:
    """(повний sha, причина). Причина порожня, якщо git відповів нормально."""
    try:
        return code.git.git(repo_path, "rev-parse", f"{value}^{{commit}}").strip(), ""
    except CairnError as exc:
        if exc.code == "git_failed":
            return "", "commit_not_found"
        return "", GIT_UNAVAILABLE
    except OSError:
        return "", GIT_UNAVAILABLE


def _bundle_shape(bundle: dict) -> tuple[str, list[str]]:
    """Перевірка самоописного bundle зон без побічних записів."""
    problems: list[str] = []
    if bundle.get("protocol") != zones.PROTOCOL:
        problems.append("bad_protocol")
        return "", problems
    audit = dict(bundle)
    digest = str(audit.pop("digest", ""))
    if audit_digest(audit) != digest:
        problems.append("bad_digest")
    fed = bundle.get("cairn_bundle") or {}
    checked = federation.verify_bundle(fed)
    if not checked["ok"]:
        problems.extend(checked["problems"])
    for rel in (bundle.get("context_objects") or {}):
        if not str(rel).startswith(".cairn/objects/"):
            problems.append("bad_context_ref:" + str(rel)[:40])
    return digest, problems


def _journal_plan(store, bundles_zones: dict[str, list[dict]]) -> tuple[dict, list[dict]]:
    """Об'єднання append-only журналів без перезапису й подвоєння seq."""
    merged: dict[str, list[dict]] = {}
    conflicts: list[dict] = []
    for zone, incoming in bundles_zones.items():
        local = zones.read_journal(store, zone)
        by_seq: dict[int, dict] = {}
        for row in local:
            by_seq.setdefault(int(row.get("seq") or 0), row)
        for row in incoming:
            seq = int(row.get("seq") or 0)
            known = by_seq.get(seq)
            if known is None:
                by_seq[seq] = row
            elif canonical(known) != canonical(row):
                conflicts.append({"kind": "journal_seq_divergence", "zone": zone,
                                  "seq": seq,
                                  "local": sha256_text(canonical(known))[:12],
                                  "incoming": sha256_text(canonical(row))[:12]})
        merged[zone] = [by_seq[k] for k in sorted(by_seq)]
    return merged, conflicts


def inspect_merge(store, bundle: dict, *, repo_path: str | Path) -> dict:
    """Доповідь без записів: чи можна зливати й які хеші справді збігаються."""
    digest, shape_problems = _bundle_shape(bundle)
    ident = code.git.identity(repo_path)
    declared = sorted({str(d.get("repo") or "") for d in bundle.get("descriptors") or []
                       if str(d.get("repo") or "")})
    repo_match = bool(declared) and declared == [ident["id"]]
    if not declared:
        shape_problems.append("repo_identity_absent")
    elif not repo_match:
        shape_problems.append("repo_identity_mismatch")

    links = []
    for value in sorted(collect_commits(bundle)):
        full, failure = _git_resolve(repo_path, value)
        links.append({"requested": value, "resolved": full, "in_repo": bool(full),
                      "full": full[:12] if full else "", "failure": failure})
    unlinked = [item["requested"] for item in links if not item["in_repo"]]
    # «git не запустився» — не той самий факт, що «такого коміта немає».
    # Перше означає, що звіт неповний, і це мусить бути видно окремо.
    git_unavailable = [item["requested"] for item in links
                       if item["failure"] == GIT_UNAVAILABLE]
    if git_unavailable:
        shape_problems.append("git_unavailable")

    fed = bundle.get("cairn_bundle") or {}
    fed_report = federation.inspect(store, fed)
    conflicts: list[dict] = [
        {"kind": "protected_field_divergence", **item}
        for item in fed_report.get("conflicts") or []
    ]

    incoming_zones = {str(d.get("zone") or ""): rows
                      for d in bundle.get("descriptors") or []
                      for zone, rows in [(str(d.get("zone") or ""),
                                          (bundle.get("journals") or {}).get(str(d.get("zone") or ""), []))]}
    journal, journal_conflicts = _journal_plan(store, incoming_zones)
    conflicts.extend(journal_conflicts)

    object_plan: dict[str, str] = {}
    for rel, payload in (bundle.get("context_objects") or {}).items():
        target = Path(store.root) / str(rel)
        if not target.is_file():
            object_plan[rel] = "write"
            continue
        try:
            local = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            local = None
        if local is not None and canonical(local) == canonical(payload):
            object_plan[rel] = "duplicate"
        else:
            object_plan[rel] = "conflict"
            conflicts.append({"kind": "context_object_divergence", "path": rel,
                              "local": sha256_text(canonical(local or {}))[:12],
                              "incoming": sha256_text(canonical(payload))[:12]})

    for d in bundle.get("descriptors") or []:
        zone = str(d.get("zone") or "")
        if not zone or "/" in zone or "\\" in zone:
            conflicts.append({"kind": "bad_zone_name", "zone": zone})
            continue
        local_path = zones.descriptor_path(store.root, zone)
        if not local_path.is_file():
            continue
        local = json.loads(local_path.read_text(encoding="utf-8"))
        if canonical(local) == canonical(d):
            continue
        if str(local.get("context_identity") or "") != str(d.get("context_identity") or ""):
            conflicts.append({"kind": "zone_identity_divergence", "zone": zone,
                              "local": sha256_text(canonical(local.get("context_identity")))[:12],
                              "incoming": sha256_text(canonical(d.get("context_identity")))[:12]})
        owner = str(local.get("owner") or "")
        expires = int(local.get("lease_expires_ms") or 0)
        if owner and expires > int(time.time() * 1000):
            conflicts.append({"kind": "zone_lease_active", "zone": zone, "owner": owner})

    return {
        "ok": not shape_problems and repo_match,
        "protocol": PROTOCOL,
        "status": "ready" if not shape_problems and repo_match else "refused",
        "problems": shape_problems,
        "repo_id": ident["id"], "repo_head": ident["head"],
        "root_commits": [line for line in ident["fingerprint"].splitlines() if line],
        "declared_repos": declared,
        "bundle_digest": digest,
        "causal_digest": fed.get("digest"),
        "commit_links": links,
        "unlinked_commits": unlinked,
        "git_unavailable": git_unavailable,
        "new_events": len(fed_report.get("new") or []),
        "duplicate_events": len(fed_report.get("duplicates") or []),
        "zone_journal_plan": {z: len(rows) for z, rows in journal.items()},
        "object_plan": object_plan,
        "conflicts": conflicts,
        "safe_to_apply": not shape_problems and repo_match and not conflicts,
    }


def merge_bundle(store, bundle: dict, *, repo_path: str | Path,
                 actor: dict, apply: bool = False) -> dict:
    """Злити bundle в наявне сховище; ``apply=False`` — лише доповідь."""
    report = inspect_merge(store, bundle, repo_path=repo_path)
    if not apply:
        return {**report, "applied": False, "merge_event_id": None}
    require_human(actor)
    if report["status"] == "refused":
        raise CairnError("merge_refused", "bundle не належить цьому репозиторію або пошкоджений",
                         exit_code=4, details={"problems": report["problems"]})
    blocking = [item for item in report["conflicts"]
                if item.get("kind") in {"protected_field_divergence",
                                        "journal_seq_divergence",
                                        "context_object_divergence"}]
    if blocking:
        raise CairnError("merge_conflict", "злиття потребує розв'язання конфліктів",
                         exit_code=4, details={"conflicts": blocking})

    fed = bundle.get("cairn_bundle") or {}
    by_id = {e.get("event_id"): e for e in fed.get("events") or []}
    new_ids = [eid for eid in (federation.inspect(store, fed).get("new") or [])
               if eid in by_id]
    applied_events = 0
    merged_journals, _ = _journal_plan(
        store, {str(d.get("zone") or ""):
                (bundle.get("journals") or {}).get(str(d.get("zone") or ""), [])
                for d in bundle.get("descriptors") or []})
    local_before = zones.export_bundle(store, sorted(merged_journals))
    written_objects = 0
    written_zones = 0

    def op(con):
        nonlocal applied_events, written_objects, written_zones
        for eid in new_ids:
            event = by_id[eid]
            stored = store.append(
                con, event.get("event_type"), event.get("aggregate_id"),
                event.get("payload") or {}, actor,
                f"fed:{fed.get('digest', 'unknown')}:{eid}",
                causation_id=federation._origin_id(event))
            if not stored.get("replayed"):
                applied_events += 1
        for rel, payload in (bundle.get("context_objects") or {}).items():
            if report["object_plan"].get(rel) != "write":
                continue
            target = Path(store.root) / str(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
            written_objects += 1
        for d in bundle.get("descriptors") or []:
            zone = str(d.get("zone") or "")
            if not zone or "/" in zone or "\\" in zone:
                continue
            incoming_head = int(d.get("head") or 0)
            current_path = zones.descriptor_path(store.root, zone)
            current = json.loads(current_path.read_text(encoding="utf-8")) \
                if current_path.is_file() else dict(d)
            seqs = [int(current.get("head") or 0), incoming_head]
            seqs.extend(int(r.get("seq") or 0) for r in merged_journals.get(zone, []))
            current["head"] = max(seqs)
            current["fence"] = max(int(current.get("fence") or 0), int(d.get("fence") or 0))
            current["owner"] = None
            current["lease_expires_ms"] = None
            if current.get("context_identity") in ({}, None):
                current["context_identity"] = d.get("context_identity") or {}
            zones._write(current_path, current)
            jpath = zones.journal_path(store.root, zone)
            jpath.parent.mkdir(parents=True, exist_ok=True)
            jpath.write_text("".join(
                json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                for r in merged_journals.get(zone, [])), encoding="utf-8")
            written_zones += 1
        merge_payload = {
            "protocol": PROTOCOL,
            "bundle_digest": report["bundle_digest"],
            "causal_digest": report["causal_digest"],
            "repo_id": report["repo_id"],
            "git_head": report["repo_head"],
            "root_commits": report["root_commits"],
            "commit_links": report["commit_links"],
            "unlinked_commits": report["unlinked_commits"],
            "conflicts": report["conflicts"],
            "counts": {"events": applied_events, "objects": written_objects,
                       "zones": written_zones,
                       "journal_rows": sum(len(v) for v in merged_journals.values())},
            "local_bundle_digest_before": zones.state_digest(local_before),
            "recorded_at": store.now(),
        }
        event = store.append(con, "merge.recorded", report["repo_id"], merge_payload,
                             actor, f"merge:{report['repo_id']}:{report['bundle_digest']}")
        return event

    event = store.mutate(op)
    return {**report, "applied": True, "merge_event_id": event["event_id"],
            "applied_events": applied_events, "written_objects": written_objects,
            "written_zones": written_zones}
