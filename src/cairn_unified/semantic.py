"""Семантичні об'єкти, твердження, зв'язки, bi-temporal запит."""

from __future__ import annotations

import json

from .canon import prefixed
from .errors import CairnError
from .ontology import AUTHORITATIVE, RELATION_REGISTRY, TRUST_CLASSES
from .store import require_human


def put_object(store, *, object_type: str, title: str, summary: str, actor: dict,
               stable_key: str | None = None, lifecycle: str = "accepted",
               trust: str | None = None, semantic: dict | None = None,
               constraints: list | None = None, scope: list | None = None,
               object_id: str | None = None, idempotency_key: str | None = None,
               valid_from: str | None = None, namespace: str | None = None) -> dict:
    if trust is None:
        trust = "human_authoritative" if actor["kind"] == "human" else "agent_proposed"
    if trust not in TRUST_CLASSES:
        raise CairnError("bad_trust", f"невідомий клас довіри {trust}")
    if actor["kind"] != "human" and trust in AUTHORITATIVE:
        raise CairnError("authority_required", "агент не може надати твердженню людську або системну довіру")
    if actor["kind"] != "human" and lifecycle in {"accepted", "revoked"}:
        lifecycle = "proposed"
        trust = "agent_proposed"
    manifest = store.manifest()
    now = store.now()
    oid = object_id or stable_key or prefixed("obj")
    payload = {
        "id": oid,
        "object_type": object_type,
        "namespace": namespace or f"project:{manifest['project_id']}",
        "stable_key": stable_key or oid,
        "schema_version": "2.0",
        "lifecycle_state": lifecycle,
        "title": title,
        "summary": summary,
        "trust_class": trust,
        "valid_from": valid_from or now,
        "valid_until": None,
        "recorded_at": now,
        "updated_at": now,
        "semantic": {
            "kind": (semantic or {}).get("kind") or object_type,
            "scope": scope or [],
            "constraints": constraints or [],
            **(semantic or {}),
        },
        "provenance": {"created_by": actor, "recorded_at": now},
        "access": {"classification": "internal"},
    }

    def op(con):
        ev = store.append(con, "object.upserted", oid, payload, actor, idempotency_key)
        return {"object": ev["payload"], "event_id": ev["event_id"], "replayed": ev["replayed"]}

    return store.mutate(op)


def add_assertion(store, *, subject_id: str, predicate: str, literal, actor: dict,
                  claim_type: str = "observed", trust: str | None = None,
                  polarity: str = "asserted", scope: list | None = None,
                  idempotency_key: str | None = None) -> dict:
    if predicate.startswith("policy.") and actor["kind"] != "human":
        raise CairnError("authority_required", "агент не змінює політику своїм текстом")
    if trust is None:
        trust = "human_authoritative" if actor["kind"] == "human" else "agent_proposed"
    if actor["kind"] != "human" and trust in AUTHORITATIVE:
        raise CairnError("authority_required", "агент не підвищує довіру власного тексту")
    now = store.now()
    aid = prefixed("asr")
    payload = {
        "id": aid,
        "subject_id": subject_id,
        "predicate": predicate,
        "literal": literal,
        "claim_type": claim_type,
        "polarity": polarity,
        "trust_class": trust,
        "confidence": None if trust in AUTHORITATIVE else 0.4,
        "valid_from": now,
        "valid_until": None,
        "recorded_at": now,
        "author": actor,
        "review_state": "approved" if actor["kind"] == "human" else "unreviewed",
        "scope": scope or [],
    }

    def op(con):
        ev = store.append(con, "assertion.recorded", subject_id, payload, actor, idempotency_key)
        return {"assertion": ev["payload"], "event_id": ev["event_id"], "replayed": ev["replayed"]}

    return store.mutate(op)


def relate(store, *, from_id: str, to_id: str, relation: str, actor: dict,
           condition: dict | None = None, idempotency_key: str | None = None) -> dict:
    spec = RELATION_REGISTRY.get(relation)
    if spec is None:
        raise CairnError("unknown_relation", f"зв'язок {relation} не в реєстрі", exit_code=2)
    if spec["blocks"]:
        _reject_cycle(store, from_id, to_id)
    now = store.now()
    rid = prefixed("rel")
    payload = {
        "id": rid,
        "from_id": from_id,
        "to_id": to_id,
        "relation_type": relation,
        "execution_effect": spec["effect"],
        "valid_from": now,
        "valid_until": None,
        "condition": condition,
        "created_at": now,
        "provenance": {"created_by": actor, "effect_source": "relation_registry"},
    }

    def op(con):
        ev = store.append(con, "relation.recorded", from_id, payload, actor, idempotency_key)
        return {"relation": ev["payload"], "event_id": ev["event_id"], "replayed": ev["replayed"]}

    return store.mutate(op)


def accept_object(store, *, object_id: str, actor: dict, rationale: str | None = None) -> dict:
    """Людина приймає агентську пропозицію: окрема authority chain, не само-підвищення.

    Агент може створювати лише `proposed`/`agent_proposed`. Підвищення до
    `accepted`/`human_authoritative` можливе тільки цим викликом, і воно
    завжди пише окрему подію з актором-людиною.
    """
    require_human(actor)
    current = _object(store, object_id)
    body = json.loads(current["payload_json"])
    if body["lifecycle_state"] in {"revoked", "superseded", "rejected"}:
        raise CairnError("not_active", "не можна прийняти неактивний об'єкт")
    if body["lifecycle_state"] == "accepted" and body["trust_class"] == "human_authoritative":
        return {"object": body, "event_id": None, "replayed": True}
    now = store.now()
    body["lifecycle_state"] = "accepted"
    body["trust_class"] = "human_authoritative"
    body["review_state"] = "approved"
    body["updated_at"] = now
    body["summary"] = (body.get("summary") or "")
    authority = {
        "id": prefixed("ath"),
        "object_id": object_id,
        "granted_to": object_id,
        "from_state": current["lifecycle_state"],
        "rationale": rationale or "",
        "granted_by": actor,
        "recorded_at": now,
    }

    def op(con):
        store.append(con, "object.upserted", object_id, body, actor)
        ev = store.append(con, "assertion.recorded", object_id, {
            "id": prefixed("asr"),
            "subject_id": object_id,
            "predicate": "authority.accepted_by_human",
            "literal": authority,
            "claim_type": "governance",
            "polarity": "asserted",
            "trust_class": "human_authoritative",
            "confidence": None,
            "valid_from": now,
            "valid_until": None,
            "recorded_at": now,
            "author": actor,
            "review_state": "approved",
            "scope": [],
        }, actor)
        return {"object": body, "authority": authority, "event_id": ev["event_id"], "replayed": False}

    return store.mutate(op)


def supersede(store, *, object_id: str, summary: str, actor: dict, title: str | None = None) -> dict:
    require_human(actor)
    current = _object(store, object_id)
    if current["lifecycle_state"] in {"superseded", "revoked"}:
        raise CairnError("not_active", "об'єкт уже не чинний")
    now = store.now()
    new_id = prefixed("obj")
    closed = json.loads(current["payload_json"])
    closed["lifecycle_state"] = "superseded"
    closed["valid_until"] = now
    closed["updated_at"] = now
    closed["superseded_by"] = new_id
    opened = {
        **{k: closed[k] for k in ("object_type", "namespace", "trust_class", "semantic", "access")},
        "id": new_id,
        "stable_key": closed.get("stable_key"),
        "schema_version": "2.0",
        "lifecycle_state": "accepted",
        "title": title or closed.get("title"),
        "summary": summary,
        "valid_from": now,
        "valid_until": None,
        "recorded_at": now,
        "updated_at": now,
        "provenance": {"created_by": actor, "supersedes": object_id},
    }

    def op(con):
        store.append(con, "object.upserted", object_id, closed, actor)
        ev = store.append(con, "object.upserted", new_id, opened, actor)
        store.append(con, "relation.recorded", new_id, {
            "id": prefixed("rel"),
            "from_id": new_id,
            "to_id": object_id,
            "relation_type": "supersedes",
            "execution_effect": "none",
            "valid_from": now,
            "valid_until": None,
            "created_at": now,
            "provenance": {"created_by": actor},
        }, actor)
        return {"object": ev["payload"], "event_id": ev["event_id"], "replayed": False}

    return store.mutate(op)


def revoke(store, *, object_id: str, actor: dict, reason: str) -> dict:
    require_human(actor)
    current = _object(store, object_id)
    now = store.now()
    body = json.loads(current["payload_json"])
    body["lifecycle_state"] = "revoked"
    body["trust_class"] = "rejected_or_revoked"
    body["valid_until"] = now
    body["updated_at"] = now
    body["summary"] = (body.get("summary") or "") + f"\n[revoked] {reason}"

    def op(con):
        ev = store.append(con, "object.upserted", object_id, body, actor)
        return {"object": ev["payload"], "event_id": ev["event_id"], "replayed": False}

    return store.mutate(op)


def query(store, *, object_type: str | None = None, trust: str | None = None,
          as_of: str | None = None, known_at: str | None = None,
          text: str | None = None, active_only: bool = True,
          predicate: str | None = None, subject_id: str | None = None) -> dict:
    objects = store.read("SELECT * FROM objects")
    if object_type:
        objects = [row for row in objects if row["object_type"] == object_type]
    if trust:
        objects = [row for row in objects if row["trust_class"] == trust]
    objects = [row for row in objects if _visible(row, as_of, known_at, active_only)]
    if text:
        needle = text.lower()
        objects = [row for row in objects if needle in ((row.get("title") or "") + (row.get("summary") or "")).lower()
                   or needle in (row.get("payload_json") or "").lower()]
    assertions = store.read("SELECT * FROM assertions")
    if subject_id:
        assertions = [row for row in assertions if row["subject_id"] == subject_id]
    if predicate:
        assertions = [row for row in assertions if row["predicate"] == predicate]
    assertions = [row for row in assertions if _visible(row, as_of, known_at, active_only)]
    if active_only and as_of is None:
        assertions = [row for row in assertions if row["trust_class"] != "rejected_or_revoked"]
    return {
        "as_of": as_of,
        "known_at": known_at,
        "objects": [_public_object(row) for row in objects],
        "assertions": [_public_assertion(row) for row in assertions],
    }


def explain_trust(store, assertion_id: str) -> dict:
    row = store.read_one("SELECT * FROM assertions WHERE id = ?", (assertion_id,))
    if row is None:
        raise CairnError("not_found", "твердження не знайдено", exit_code=1)
    return {
        "assertion": _public_assertion(row),
        "promotion": "людське рішення або системний доказ; повторення агента довіру не підвищує",
        "authoritative": row["trust_class"] in AUTHORITATIVE,
    }


def _object(store, object_id: str) -> dict:
    row = store.read_one("SELECT * FROM objects WHERE id = ?", (object_id,))
    if row is None:
        raise CairnError("not_found", f"об'єкт {object_id} не знайдено", exit_code=1)
    return row


def _visible(row: dict, as_of: str | None, known_at: str | None, active_only: bool) -> bool:
    if known_at and row.get("recorded_at") and row["recorded_at"] > known_at:
        return False
    if as_of:
        if row.get("valid_from") and row["valid_from"] > as_of:
            return False
        if row.get("valid_until") and row["valid_until"] <= as_of:
            return False
        return True
    if active_only and row.get("valid_until"):
        return False
    if active_only and row.get("lifecycle_state") in {"superseded", "revoked", "rejected"}:
        return False
    return True


def _public_object(row: dict) -> dict:
    body = json.loads(row["payload_json"])
    body["freshness"] = _freshness(row)
    return body


def _public_assertion(row: dict) -> dict:
    return {
        "id": row["id"],
        "subject_id": row["subject_id"],
        "predicate": row["predicate"],
        "literal": json.loads(row["literal_json"]) if row["literal_json"] else None,
        "claim_type": row["claim_type"],
        "polarity": row["polarity"],
        "trust_class": row["trust_class"],
        "valid_from": row["valid_from"],
        "valid_until": row["valid_until"],
        "recorded_at": row["recorded_at"],
        "review_state": row["review_state"],
        "freshness": _freshness(row),
        "author": json.loads(row["author_json"]),
    }


def _freshness(row: dict) -> str:
    if row.get("lifecycle_state") == "revoked" or row.get("trust_class") == "rejected_or_revoked":
        return "invalidated"
    if row.get("valid_until") or row.get("lifecycle_state") == "superseded":
        return "superseded"
    return "current"


def _reject_cycle(store, prerequisite: str, dependent: str) -> None:
    """Ребро prerequisite -> dependent. Цикл у блокувальному графі відхиляється."""
    graph: dict[str, list[str]] = {}
    for row in store.read(
        "SELECT from_id, to_id FROM work_dependencies WHERE execution_effect IN ('hard', 'conditional')"
    ):
        graph.setdefault(row["from_id"], []).append(row["to_id"])
    graph.setdefault(prerequisite, []).append(dependent)
    stack = [dependent]
    seen = set()
    while stack:
        node = stack.pop()
        if node == prerequisite:
            raise CairnError("cycle", "блокувальний цикл відхилено", details={"from": prerequisite, "to": dependent})
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, []))
