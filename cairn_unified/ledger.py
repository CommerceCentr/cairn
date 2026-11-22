"""Append-only журнал подій із ланцюжком хешів."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .canon import canonical, sha256_text
from .errors import CairnError


def ledger_path(root: Path) -> Path:
    return root / ".cairn" / "events" / "ledger.jsonl"


def read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            raw = line.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                raise CairnError("ledger_corrupt", f"рядок {line_no} не є JSON") from exc
    return out


def append_line(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = canonical(event) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def append_lines(path: Path, events: list[dict]) -> None:
    """Persist one transaction's contiguous events with a single sync."""
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for event in events:
            fh.write(canonical(event) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def chain_hash(prev: str, seq: int, event_id: str, event_type: str, phash: str,
               event: dict | None = None) -> str:
    if event and int(event.get("integrity_version") or 1) >= 2:
        envelope = {key: event.get(key) for key in (
            "integrity_version", "schema_version", "event_id", "seq", "event_type", "aggregate_id",
            "actor", "occurred_at", "recorded_at", "causation_id",
            "correlation_id", "idempotency_key", "payload_hash")}
        return sha256_text(canonical({"prev_hash": prev, "envelope": envelope}))
    return sha256_text(f"{prev}|{seq}|{event_id}|{event_type}|{phash}")


def verify(path: Path) -> dict:
    return verify_events(read_events(path))


def verify_events(events: list[dict]) -> dict:
    """Перевірка ланцюжка для вже завантажених подій (експорт/імпорт)."""
    prev = "0" * 64
    seen: set[str] = set()
    problems: list[str] = []
    for index, ev in enumerate(events, 1):
        eid = ev.get("event_id")
        if eid in seen:
            problems.append(f"дубль event_id {eid}")
            continue
        seen.add(eid)
        expect_seq = index if not problems else ev.get("seq")
        phash = sha256_text(canonical(ev.get("payload")))
        if phash != ev.get("payload_hash"):
            problems.append(f"{eid}: payload_hash не збігається")
        expected = chain_hash(prev, int(ev.get("seq") or 0), eid,
                              ev.get("event_type", ""), phash, ev)
        if ev.get("prev_hash") != prev or ev.get("chain_hash") != expected:
            problems.append(f"{eid}: розрив ланцюжка")
        if problems:
            break
        if int(ev.get("seq") or 0) != expect_seq:
            problems.append(f"{eid}: seq {ev.get('seq')} замість {expect_seq}")
            break
        prev = ev["chain_hash"]
    return {"ok": not problems, "count": len(events), "problems": problems, "head": prev}
