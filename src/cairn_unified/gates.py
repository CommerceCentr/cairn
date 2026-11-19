"""Gate lifecycle: жодного «зеленого прапорця» без доказу.

 Gate блокує eligibility, доки не перейде з `pending`. Людський gate не можна
 закрити агентом — навіть якщо агент стверджує, що всё готово. Системні gate
 закриваються тільки за наявності evidence_id.
"""

from __future__ import annotations

import json

from .canon import prefixed
from .errors import CairnError
from .ontology import GATE_TYPES
from .store import require_human

STATES = ("pending", "evaluating", "satisfied", "blocked", "resolved", "expired")
HUMAN_GATES = {"human.approval", "review.approved", "external.work_item.closed"}


def _row(store, gate_id: str) -> dict:
    row = store.read_one("SELECT * FROM gates WHERE id = ?", (gate_id,))
    if row is None:
        raise CairnError("not_found", f"gate {gate_id} не знайдено", exit_code=1)
    return row


def _payload(row: dict, **changes) -> dict:
    body = {
        "id": row["id"],
        "gate_type": row["gate_type"],
        "subject_id": row["subject_id"],
        "state": row["state"],
        "condition": json.loads(row["condition_json"] or "{}"),
        "evidence_id": row["evidence_id"],
        "deadline_at": row["deadline_at"],
        "resolved_by": row["resolved_by"],
    }
    body.update(changes)
    return body


def create(store, *, gate_type: str, subject_id: str | None, actor: dict,
           condition: dict | None = None, deadline_at: str | None = None,
           idempotency_key: str | None = None) -> dict:
    if gate_type not in GATE_TYPES:
        raise CairnError("unknown_gate", f"тип gate {gate_type} не відомий", exit_code=2)
    body = {
        "id": prefixed("gte"),
        "gate_type": gate_type,
        "subject_id": subject_id,
        "state": "pending",
        "condition": condition or {},
        "evidence_id": None,
        "deadline_at": deadline_at,
        "resolved_by": None,
    }

    def op(con):
        ev = store.append(con, "gate.upserted", subject_id or body["id"], body, actor, idempotency_key)
        return {"gate": ev["payload"], "event_id": ev["event_id"], "replayed": ev["replayed"]}

    return store.mutate(op)


def evaluate(store, gate_id: str, actor: dict, *, passed: bool,
             evidence_id: str | None = None) -> dict:
    row = _row(store, gate_id)
    if row["state"] not in {"pending", "evaluating"}:
        raise CairnError("gate_closed", f"gate у стані {row['state']}", exit_code=2)
    if row["gate_type"] in HUMAN_GATES:
        require_human(actor)
    if passed:
        if not evidence_id:
            raise CairnError(
                "evidence_required", "gate не можна закрити без evidence record", exit_code=2,
                details={"gate_id": gate_id},
            )
        verified = store.read_one("SELECT id FROM evidence WHERE id = ?", (evidence_id,))
        if verified is None:
            raise CairnError("evidence_missing", f"доказ {evidence_id} не знайдено", exit_code=2)
    body = _payload(
        row,
        state="satisfied" if passed else "blocked",
        evidence_id=evidence_id,
        resolved_by=actor["id"] if passed else None,
    )

    def op(con):
        ev = store.append(con, "gate.upserted", row["subject_id"] or gate_id, body, actor)
        return {"gate": ev["payload"], "event_id": ev["event_id"]}

    return store.mutate(op)


def resolve(store, gate_id: str, actor: dict, *, evidence_id: str) -> dict:
    """Фінальний перехід satisfied → resolved. Доказ обов'язковий."""
    row = _row(store, gate_id)
    if row["state"] != "satisfied":
        raise CairnError("gate_not_satisfied", "спочатку треба оцінити gate з доказом", exit_code=2)
    if row["gate_type"] in HUMAN_GATES:
        require_human(actor)
    evidence = store.read_one("SELECT id FROM evidence WHERE id = ?", (evidence_id,))
    if evidence is None:
        raise CairnError("evidence_missing", f"доказ {evidence_id} не знайдено", exit_code=2)
    body = _payload(row, state="resolved", evidence_id=evidence_id,
                    resolved_by=actor["id"], condition={**json.loads(row["condition_json"] or "{}"),
                                                        "resolved_evidence": evidence_id})

    def op(con):
        ev = store.append(con, "gate.upserted", row["subject_id"] or gate_id, body, actor)
        return {"gate": ev["payload"], "event_id": ev["event_id"]}

    return store.mutate(op)


def expire(store, gate_id: str, actor: dict) -> dict:
    row = _row(store, gate_id)
    if row["state"] in {"resolved", "expired"}:
        raise CairnError("gate_closed", "gate вже закрито", exit_code=2)
    body = _payload(row, state="expired")

    def op(con):
        ev = store.append(con, "gate.upserted", row["subject_id"] or gate_id, body, actor)
        return {"gate": ev["payload"], "event_id": ev["event_id"]}

    return store.mutate(op)


def get(store, gate_id: str) -> dict:
    row = _row(store, gate_id)
    return _payload(row)


def list_gates(store, subject_id: str | None = None) -> list[dict]:
    if subject_id:
        rows = store.read("SELECT * FROM gates WHERE subject_id = ? ORDER BY gate_type, id", (subject_id,))
    else:
        rows = store.read("SELECT * FROM gates ORDER BY gate_type, id")
    return [_payload(row) for row in rows]
