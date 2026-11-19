"""Session handoff: відновлення роботи без копіювання transcript.

Recovery packet — це не transcript. Це контрольована, контрольовано мала
сума поточного стану: посилання на work item, рішення, контекст, докази й
наступна команда. Нова сесія відтворює стан із Event Ledger, а не з того,
що «розказав» попередній агент. Контрольна сума не дає підмінити пакет
при збереженні чи реплікації.
"""

from __future__ import annotations

import json

from .canon import canonical, prefixed, sha256_text
from .errors import CairnError
from .store import require_human


def _checksum(body: dict) -> str:
    return "sha256:" + sha256_text(canonical(body))


def create(store, *, work_id: str, session_id: str, actor: dict, summary: str,
           next_command: str, decisions: list | None = None,
           context_id: str | None = None, evidence_ids: list | None = None,
           transcript_ref: str | None = None, idempotency_key: str | None = None) -> dict:
    if actor["kind"] == "agent" and transcript_ref:
        # Агент не може видати шматок transcript за офіційний пакет.
        transcript_ref = None
    body = {
        "id": prefixed("hnd"),
        "work_item_id": work_id,
        "session_id": session_id,
        "summary": summary,
        "decisions": decisions or [],
        "context_id": context_id,
        "evidence_ids": evidence_ids or [],
        "next_command": next_command,
        "transcript_ref": transcript_ref,
        "created_by": actor,
        "created_at": store.now(),
    }
    body["checksum"] = _checksum(body)

    def op(con):
        ev = store.append(con, "handoff.recorded", work_id, body, actor, idempotency_key)
        return {"handoff": ev["payload"], "event_id": ev["event_id"], "replayed": ev["replayed"]}

    return store.mutate(op)


def recover(store, handoff_id: str, *, compile_context: bool = False,
            actor: dict | None = None) -> dict:
    row = store.read_one("SELECT payload_json FROM handoffs WHERE id = ?", (handoff_id,))
    if row is None:
        raise CairnError("not_found", f"handoff {handoff_id} не знайдено", exit_code=1)
    packet = json.loads(row["payload_json"])
    expected = packet.get("checksum")
    actual = _checksum({k: v for k, v in packet.items() if k != "checksum"})
    if expected != actual:
        raise CairnError("handoff_corrupt", "контрольна сума пакета не збігається", exit_code=4)
    result = {
        "packet": packet,
        "verified": True,
        "source": "event_ledger",
        "transcript_required": False,
    }
    if compile_context:
        from . import context, work
        task = work.get_work(store, packet["work_item_id"])
        result["work"] = task
        result["context"] = context.build(
            store, packet["work_item_id"],
            agent_id=(actor or {}).get("id"), purpose="edit",
        )
    return result


def latest(store, work_id: str) -> dict | None:
    rows = store.read(
        "SELECT payload_json FROM handoffs WHERE work_item_id = ? ORDER BY created_at DESC, id DESC",
        (work_id,),
    )
    if not rows:
        return None
    packet = json.loads(rows[0]["payload_json"])
    expected = packet.get("checksum")
    actual = _checksum({k: v for k, v in packet.items() if k != "checksum"})
    if expected != actual:
        raise CairnError("handoff_corrupt", "контрольна сума останнього пакета не збігається", exit_code=4)
    return packet


def seal(store, handoff_id: str, actor: dict) -> dict:
    """Людина фіксує, що пакет прийнято як офіційний запис сесії."""
    require_human(actor)
    packet = recover(store, handoff_id)["packet"]
    body = {
        "id": prefixed("ath"),
        "object_id": handoff_id,
        "subject": packet["session_id"],
        "action": "handoff.sealed",
        "granted_by": actor,
        "recorded_at": store.now(),
    }

    def op(con):
        ev = store.append(con, "assertion.recorded", handoff_id, {
            "id": body["id"],
            "subject_id": handoff_id,
            "predicate": "authority.handoff_sealed",
            "literal": body,
            "claim_type": "governance",
            "polarity": "asserted",
            "trust_class": "human_authoritative",
            "confidence": None,
            "valid_from": body["recorded_at"],
            "valid_until": None,
            "recorded_at": body["recorded_at"],
            "author": actor,
            "review_state": "approved",
            "scope": [],
        }, actor)
        return {"authority": body, "event_id": ev["event_id"]}

    return store.mutate(op)
