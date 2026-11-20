"""Детермінований верифікатор пропозицій.

Людина в цій моделі — спостерігач. Авторитет надається не актором тексту, а
перевіркою: коміт має резолвитися локально, пак — мати незмінний digest, вузол —
бути індексованим, а суперечних пропозицій не існувати. Якщо перевірка не
встановила факту, об'єкт лишається `agent_proposed` і отримує ескалацію
`не встановлено`; тільки тут потрібна людина.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import semantic
from .canon import canonical, sha256_text
from .code import git
from .ontology import AUTHORITATIVE

POLICY_VERSION = "auto-verify/1.0"
VERIFIER = {"kind": "system", "id": "cairn-verifier"}


def _object(store, object_id: str) -> dict:
    row = store.read_one("SELECT payload_json FROM objects WHERE id = ?", (object_id,))
    if row is None:
        from .errors import CairnError
        raise CairnError("not_found", f"об'єкт {object_id} не знайдено", exit_code=1)
    return json.loads(row["payload_json"])


def _contradictions(store, body: dict, object_id: str) -> list[str]:
    """Конфлікт — різні резюме тієї самої стабільної ідентичності.

    Факт коміту й компактація з того самого коміту мають різні резюме за
    призначенням; це не суперечність. Порівнюємо лише `stable_key` + kind.
    """
    key = str(body.get("stable_key") or "")
    kind = str((body.get("semantic") or {}).get("kind") or "")
    if not key:
        return []
    found = []
    for row in store.read("SELECT id, payload_json FROM objects WHERE id != ?", (object_id,)):
        other = json.loads(row["payload_json"])
        if other.get("lifecycle_state") in {"revoked", "rejected", "superseded"}:
            continue
        if str(other.get("stable_key") or "") != key:
            continue
        if str((other.get("semantic") or {}).get("kind") or "") != kind:
            continue
        if sha256_text(other.get("summary") or "") != sha256_text(body.get("summary") or ""):
            found.append(row["id"])
    return sorted(found)


def check_object(store, body: dict, *, repo_path: str | Path | None = None,
                 packs_dir: str | Path | None = None) -> tuple[list[dict], list[str]]:
    """Повернути (пройдені перевірки, причини ескалації). Без записів."""
    sem = body.get("semantic") or {}
    checks: list[dict] = []
    reasons: list[str] = []
    commit = str(sem.get("commit") or "")
    if not commit:
        reasons.append("unresolved:commit_absent")
    else:
        try:
            full = git.git(str(repo_path), "rev-parse", f"{commit}^{{commit}}").strip() if repo_path else ""
        except Exception:
            full = ""
        if full:
            checks.append({"check": "commit_resolves", "value": full[:12]})
        else:
            reasons.append("unresolved:commit_not_found")

    if sem.get("kind") == "compaction":
        pack_name = sem.get("pack") or ""
        pack_file = Path(packs_dir) / pack_name if packs_dir and pack_name else None
        if pack_file and pack_file.is_file():
            digest = sha256_text(pack_file.read_text(encoding="utf-8", errors="replace"))
            if digest == sem.get("pack_digest"):
                checks.append({"check": "pack_digest_matches", "value": digest[:12]})
            else:
                reasons.append("unresolved:pack_digest_mismatch")
        else:
            reasons.append("unresolved:pack_missing")

    addresses = [str(x) for x in (sem.get("scope") or [])]
    if sem.get("node_address"):
        addresses.append(str(sem["node_address"]))
    if not addresses:
        reasons.append("unresolved:node_absent")
    else:
        found = False
        for addr in addresses:
            row = store.read_one(
                "SELECT stable_key FROM code_symbols WHERE qualified_name = ? OR stable_key = ? LIMIT 1",
                (addr, addr),
            )
            if row:
                found = True
                break
        if found:
            checks.append({"check": "node_indexed", "value": addresses[0][:60]})
        else:
            reasons.append("unresolved:node_not_indexed")

    clashes = _contradictions(store, body, str(body.get("id") or ""))
    if clashes:
        reasons.append("ambiguous:" + ",".join(clashes[:3]))
    else:
        checks.append({"check": "no_contradictions", "value": "ok"})

    return checks, reasons


def verify_object(store, object_id: str, *, repo_path: str | Path | None = None,
                  packs_dir: str | Path | None = None,
                  actor: dict | None = None) -> dict:
    """Прийняти об'єкт за перевіркою або ескалувати як 'не встановлено'."""
    actor = actor or VERIFIER
    body = _object(store, object_id)
    current_trust = body.get("trust_class")
    current = body.get("verification") or {}
    if current_trust in AUTHORITATIVE and current.get("policy_version") == POLICY_VERSION:
        return {"object_id": object_id, "verified": True, "replayed": True,
                "reasons": [], "checks": current.get("checks") or []}
    if body.get("lifecycle_state") in {"revoked", "rejected", "superseded"}:
        return {"object_id": object_id, "verified": False, "reasons": ["unresolved:not_active"],
                "checks": [], "escalated": False}

    checks, reasons = check_object(store, body, repo_path=repo_path, packs_dir=packs_dir)
    digest = sha256_text(canonical({"checks": checks, "reasons": reasons}))

    def op(con):
        if not reasons:
            updated = dict(body)
            updated["lifecycle_state"] = "accepted"
            updated["trust_class"] = "verified_system"
            updated["review_state"] = "verified"
            updated["updated_at"] = store.now()
            updated["verification"] = {
                "policy_version": POLICY_VERSION, "checks": checks,
                "verifier": actor, "verified_at": store.now(), "digest": digest,
            }
            store.append(con, "object.upserted", object_id, updated, actor)
            store.append(con, "object.verified", object_id,
                         {"object_id": object_id, "trust_class": "verified_system",
                          "checks": checks, "policy_version": POLICY_VERSION},
                         actor, idempotency_key=f"verify-ok:{object_id}:{digest}")
            store.append(con, "assertion.recorded", object_id, {
                "id": "asr-" + digest[:12], "subject_id": object_id,
                "predicate": "authority.verified_by_system", "claim_type": "governance",
                "polarity": "asserted", "literal": updated["verification"],
                "trust_class": "verified_system", "confidence": None,
                "valid_from": store.now(), "valid_until": None,
                "recorded_at": store.now(), "author": actor, "review_state": "verified",
                "scope": [],
            }, actor, idempotency_key=f"verify-authority:{object_id}:{digest}")
            return {"object_id": object_id, "verified": True, "replayed": False,
                    "checks": checks, "reasons": []}

        store.append(con, "verification.escalated", object_id, {
            "object_id": object_id, "decision": "не встановлено", "reasons": reasons,
            "policy_version": POLICY_VERSION,
        }, actor, idempotency_key=f"verify-escalate:{object_id}:{digest}")
        store.append(con, "assertion.recorded", object_id, {
            "id": "asr-needs-" + digest[:12], "subject_id": object_id,
            "predicate": "needs.human.clarification", "claim_type": "governance",
            "polarity": "asserted", "literal": {"reasons": reasons, "object_id": object_id},
            "trust_class": "agent_proposed", "confidence": 0.4,
            "valid_from": store.now(), "valid_until": None,
            "recorded_at": store.now(), "author": actor, "review_state": "unreviewed",
            "scope": [],
        }, actor, idempotency_key=f"verify-needs:{object_id}:{digest}")
        return {"object_id": object_id, "verified": False, "replayed": False,
                "checks": checks, "reasons": reasons, "escalated": True,
                "trust_class": current_trust}

    return store.mutate(op)


def verify_pending(store, work_id: str, *, repo_path: str | Path | None = None,
                   packs_dir: str | Path | None = None) -> dict:
    """Верифікувати всі пропозиції задачі; повернути звіт для спостерігача."""
    rows = store.read(
        "SELECT o.id FROM objects o "
        "JOIN relations r ON r.to_id = o.id AND r.relation_type = 'governed_by' "
        "WHERE r.from_id = ? ORDER BY o.id", (work_id,),
    )
    report = {"work_id": work_id, "total": len(rows), "verified": [], "escalated": [],
              "checks": 0}
    for row in rows:
        result = verify_object(store, row["id"], repo_path=repo_path, packs_dir=packs_dir)
        if result["verified"]:
            report["verified"].append(row["id"])
        else:
            report["escalated"].append({"object_id": row["id"], "reasons": result["reasons"]})
        report["checks"] += len(result["checks"])
    report["verified_count"] = len(report["verified"])
    report["escalated_count"] = len(report["escalated"])
    return report


def _j(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
