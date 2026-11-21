"""Семантичні об'єкти, твердження, зв'язки, bi-temporal запит."""

from __future__ import annotations

import json

from .canon import prefixed
from .errors import CairnError
from .ontology import (AUTHORITATIVE, LIFECYCLE_STATES, OBJECT_TYPES,
                       RELATION_REGISTRY, TRUST_CLASSES)
from .db import has_fts
from .store import require_human


#: Адреси вузлів кодової бази. Виміряно: у реальному обʼєкті `semantic.scope`
#: містив ~50 таких ідентифікаторів, хоча за v3 §4.3 `scope` — це ОБЛАСТЬ ДІЇ
#: (project/repo/path/symbol/environment), а не перелік вмісту.
NODE_PREFIX = "nd-"


def split_scope(scope: list | None) -> tuple[list, list]:
    """(область дії, адреси вузлів). Розділення за змістом, не за позицією."""
    values = [str(v) for v in (scope or [])]
    nodes = [v for v in values if v.startswith(NODE_PREFIX)]
    rest = [v for v in values if not v.startswith(NODE_PREFIX)]
    return rest, nodes


def put_object(store, *, object_type: str, title: str, summary: str, actor: dict,
               stable_key: str | None = None, lifecycle: str = "accepted",
               trust: str | None = None, semantic: dict | None = None,
               constraints: list | None = None, scope: list | None = None,
               object_id: str | None = None, idempotency_key: str | None = None,
               valid_from: str | None = None, namespace: str | None = None,
               observed_at: str | None = None, session_id: str | None = None,
               task_id: str | None = None, commit_refs: list | None = None,
               evidence_refs: list | None = None) -> dict:
    if trust is None:
        trust = "human_authoritative" if actor["kind"] == "human" else "agent_proposed"
    if object_type not in OBJECT_TYPES:
        raise CairnError("bad_object_type", f"тип обʼєкта {object_type} не в онтології",
                         exit_code=2, details={"allowed": list(OBJECT_TYPES)})
    if lifecycle not in LIFECYCLE_STATES:
        raise CairnError("bad_lifecycle", f"стан {lifecycle} не в онтології",
                         exit_code=2, details={"allowed": list(LIFECYCLE_STATES)})
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
    scope_only, node_refs = split_scope(scope)
    extra_scope, extra_nodes = split_scope((semantic or {}).get("scope"))
    if extra_nodes:
        scope_only = scope_only or extra_scope
        node_refs = sorted(set(node_refs) | set(extra_nodes))
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
        # Два часи окремо (v3 §4.7): `observed_at` — коли факт спостережено,
        # `recorded_at` — коли записано. Раніше був лише другий, тож
        # bi-temporal на рівні обʼєкта був неповний.
        "observed_at": observed_at or valid_from or now,
        "recorded_at": now,
        "updated_at": now,
        # Ланцюг заміщення тримається в самому обʼєкті, бо саме його читає
        # той, хто питає «чи це ще чинне».
        "supersedes": [],
        "superseded_by": [],
        "semantic": {
            "kind": (semantic or {}).get("kind") or object_type,
            "scope": scope_only,
            "node_refs": node_refs,
            "constraints": constraints or [],
            **(semantic or {}),
        },
        # `source_events` тут НЕ дублюється: він лежить у
        # `object_revisions.source_event_id` — одне джерело, а не дві копії,
        # які розійдуться. Решта посилань — нова інформація, тому в payload.
        "provenance": {
            "created_by": actor,
            "recorded_at": now,
            "session_id": session_id,
            "task_id": task_id,
            "commit_refs": list(commit_refs or []),
            "evidence_refs": list(evidence_refs or []),
        },
        "access": {"classification": "internal"},
    }

    def op(con):
        ev = store.append(con, "object.upserted", oid, payload, actor, idempotency_key)
        return {"object": ev["payload"], "event_id": ev["event_id"], "replayed": ev["replayed"]}

    return store.mutate(op)


def add_assertion(store, *, subject_id: str, predicate: str, literal=None, actor: dict,
                  claim_type: str = "observed", trust: str | None = None,
                  polarity: str = "asserted", scope: list | None = None,
                  idempotency_key: str | None = None,
                  object_ref: str | None = None, observed_at: str | None = None,
                  evidence_refs: list | None = None,
                  supersedes_assertion_id: str | None = None,
                  confidence: float | None = None) -> dict:
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
        # Звʼязок «обʼєкт -> обʼєкт» (v3 §4.4). Колонка `object_ref` у таблиці
        # була від початку, але API її не приймав, тому виразити таке
        # твердження було нічим — лишався тільки літерал.
        "object_ref": object_ref,
        "claim_type": claim_type,
        "polarity": polarity,
        "trust_class": trust,
        # Було жорстко 0.4 для всього неавторитетного. Число, яке не могло
        # бути іншим, є означенням, не виміром, тому тепер або його задає
        # викликач (бо знає шкалу), або його немає.
        "confidence": confidence,
        "valid_from": now,
        "valid_until": None,
        "observed_at": observed_at or now,
        "recorded_at": now,
        "evidence_refs": list(evidence_refs or []),
        "supersedes_assertion_id": supersedes_assertion_id,
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


def accept_batch(store, *, work_id: str, commit: str, actor: dict,
                 rationale: str | None = None) -> dict:
    """Одна дія людини приймає всі пропозиції коміту, пов'язані з цією задачею.

    Кожен об'єкт і далі отримує власну подію authority через `accept_object` —
    батчиться лише кількість CLI-викликів, не ланцюг довіри.
    """
    require_human(actor)
    rows = store.read(
        "SELECT o.id, o.payload_json FROM objects o "
        "JOIN relations r ON r.to_id = o.id AND r.relation_type = 'governed_by' "
        "WHERE r.from_id = ? ORDER BY o.id", (work_id,),
    )
    accepted: list[str] = []
    replayed: list[str] = []
    for row in rows:
        body = json.loads(row["payload_json"])
        sem = body.get("semantic") or {}
        current = str(sem.get("commit") or "")
        if not current or not (current.startswith(commit) or commit.startswith(current[:7])):
            continue
        if body.get("trust_class") in AUTHORITATIVE:
            replayed.append(row["id"])
            continue
        result = accept_object(store, object_id=row["id"], actor=actor,
                               rationale=rationale or f"batch accept {commit}")
        (replayed if result.get("replayed") else accepted).append(row["id"])
    return {"work_id": work_id, "commit": commit, "accepted": accepted,
            "replayed": replayed, "count": len(accepted)}


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
    # Списком, не рядком: обʼєкт можуть замістити більше ніж один раз, і
    # одна форма поля в усіх місцях дешевша за дві.
    closed["superseded_by"] = sorted({*(closed.get("superseded_by") or []), new_id})
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
        "observed_at": closed.get("observed_at") or now,
        "recorded_at": now,
        "updated_at": now,
        "supersedes": [object_id],
        "superseded_by": [],
        "provenance": {
            **(closed.get("provenance") or {}),
            "created_by": actor,
            "recorded_at": now,
            "supersedes": object_id,
        },
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


def _text_match(store, text: str) -> list[str]:
    """Пошук за текстом через FTS5, із запасом на збірки без нього.

    Таблиця `search_fts` ЗАПОВНЮВАЛАСЬ і ніколи не читалася: пошук робив
    підрядкове порівняння в Python, яке до того ж не розрізняє код від
    коментаря. `has_fts()` каже, чи є FTS у цій збірці SQLite; якщо немає,
    працює `search_fallback` — той самий підрядок, але хоча б в SQL.
    """
    needle = (text or "").strip()
    if not needle:
        return []
    con = store.connect()
    try:
        if has_fts(con):
            # `MATCH` із префіксом: користувач пише слово, не FTS-синтаксис.
            escaped = needle.replace('"', " ")
            rows = con.execute(
                'SELECT object_id FROM search_fts WHERE search_fts MATCH ? LIMIT 500',
                (f'"{escaped}"*',),
            ).fetchall()
            if rows:
                return [row["object_id"] for row in rows]
        like = f"%{needle.lower()}%"
        rows = con.execute(
            "SELECT object_id FROM search_fallback "
            "WHERE lower(title) LIKE ? OR lower(body) LIKE ? LIMIT 500",
            (like, like),
        ).fetchall()
        return [row["object_id"] for row in rows]
    finally:
        con.close()


def query(store, *, object_type: str | None = None, trust: str | None = None,
          as_of: str | None = None, known_at: str | None = None,
          text: str | None = None, active_only: bool = True,
          predicate: str | None = None, subject_id: str | None = None) -> dict:
    # Фільтри — у SQL, не повним скануванням у Python. Індекси
    # `idx_objects_type` і `idx_assert_subject` існували від початку й не
    # використовувалися: `SELECT *` читав таблицю цілком, а потім Python
    # відкидав зайве. На 151 обʼєкті це не видно, на 10k — буде.
    sql = ["SELECT * FROM objects WHERE 1=1"]
    params: list = []
    if object_type:
        sql.append("AND object_type = ?")
        params.append(object_type)
    if trust:
        sql.append("AND trust_class = ?")
        params.append(trust)
    ids = _text_match(store, text) if text else None
    if ids is not None:
        if not ids:
            objects = []
            sql = None
        else:
            marks = ",".join("?" * len(ids))
            sql.append(f"AND id IN ({marks})")
            params.extend(ids)
    if sql is not None:
        objects = store.read(" ".join(sql), tuple(params))
    objects = [row for row in objects if _visible(row, as_of, known_at, active_only)]

    asql = ["SELECT * FROM assertions WHERE 1=1"]
    aparams: list = []
    if subject_id:
        asql.append("AND subject_id = ?")
        aparams.append(subject_id)
    if predicate:
        asql.append("AND predicate = ?")
        aparams.append(predicate)
    assertions = store.read(" ".join(asql), tuple(aparams))
    assertions = [row for row in assertions if _visible(row, as_of, known_at, active_only)]
    if active_only and as_of is None:
        assertions = [row for row in assertions if row["trust_class"] != "rejected_or_revoked"]
    return {
        "as_of": as_of,
        "known_at": known_at,
        "objects": [_public_object(row) for row in objects],
        "assertions": [_public_assertion(row) for row in assertions],
    }


def revisions(store, object_id: str) -> list[dict]:
    """Усі ревізії обʼєкта, від першої до останньої."""
    return store.read(
        "SELECT revision, payload_hash, valid_from, valid_until, recorded_at, "
        "source_event_id FROM object_revisions WHERE object_id = ? ORDER BY revision",
        (object_id,),
    )


def object_as_of(store, object_id: str, when: str | None = None) -> dict | None:
    """Обʼєкт, яким він БУВ на момент `when` (за `recorded_at` ревізії).

    Без ревізій відповідь на «що тут було раніше» спиралася лише на поточний
    рядок `objects`, тобто показувала теперішнє під виглядом минулого.
    `when=None` означає останню ревізію.
    """
    if when:
        row = store.read_one(
            "SELECT payload_json, revision FROM object_revisions "
            "WHERE object_id = ? AND recorded_at <= ? ORDER BY revision DESC LIMIT 1",
            (object_id, when),
        )
    else:
        row = store.read_one(
            "SELECT payload_json, revision FROM object_revisions "
            "WHERE object_id = ? ORDER BY revision DESC LIMIT 1",
            (object_id,),
        )
    if row is None:
        return None
    body = json.loads(row["payload_json"])
    body["_revision"] = row["revision"]
    return body


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
