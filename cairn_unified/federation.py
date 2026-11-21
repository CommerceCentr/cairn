"""Portable export/import — перший етап федерації за v3 §2.11/§13.

 Правило: жодного тихого last-write-wins для authority, trust, policy,
 lifecycle і security. Якщо чужий event змінює захищене поле вже наявного
 aggregate — це конфлікт, який треба показати, а не «розвʼязати» автоматично.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import ledger
from .canon import canonical, sha256_text
from .errors import CairnError
from .store import INIT_FOLDERS, Store, require_human

FORMAT = "cairn-bundle/1"
PROTECTED = frozenset({
    "trust_class", "lifecycle_state", "review_state", "resolved_by",
    "state", "policy", "fencing_token", "granted_by",
})


def bundle_events(bundle: dict) -> list[dict]:
    """Події bundle після перевірки ФОРМИ, до перевірки змісту.

    Bundle приходить із файла, тобто це недовірені дані. `verify_events`
    і `inspect` звертаються до `.get`/`["event_id"]`, тому не-обʼєкт або
    подія без `event_id` давали `AttributeError`/`KeyError` — нетипізований
    traceback замість відмови. Форму перевіряємо окремо й першою.
    """
    raw = bundle.get("events")
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise CairnError("bundle_malformed", "events мусить бути списком", exit_code=4,
                         details={"got": type(raw).__name__})
    problems: list[dict] = []
    for index, event in enumerate(raw):
        if not isinstance(event, dict):
            problems.append({"index": index, "reason": "not_an_object",
                             "got": type(event).__name__})
            continue
        for field in ("event_id", "event_type"):
            if not isinstance(event.get(field), str) or not event.get(field):
                problems.append({"index": index, "reason": f"missing_{field}"})
    if problems:
        raise CairnError("bundle_malformed", "подія бандла має неправильну форму",
                         exit_code=4, details={"problems": problems[:20]})
    return list(raw)


def export_bundle(store, *, path: str | Path | None = None) -> dict:
    events = ledger.read_events(ledger.ledger_path(store.root))
    chain = ledger.verify(ledger.ledger_path(store.root))
    if not chain["ok"]:
        raise CairnError("ledger_corrupt", "cannot export a broken chain", exit_code=4,
                         details={"problems": chain["problems"]})
    body = {
        "format": FORMAT,
        "manifest": store.manifest(),
        "events": events,
        "event_count": len(events),
        "chain_ok": True,
    }
    body["digest"] = "sha256:" + sha256_text(canonical({
        "format": FORMAT, "events": events, "manifest": body["manifest"],
    }))
    if path is not None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8", newline="\n")
    return body


def load_bundle(path: str | Path) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if raw.get("format") != FORMAT:
        raise CairnError("bad_bundle", f"невідомий формат {raw.get('format')}", exit_code=2)
    return raw


def inspect(store, bundle: dict) -> dict:
    events = bundle_events(bundle)
    local_rows = store.read(
        "SELECT event_id, aggregate_id, payload_hash, payload_json, causation_id FROM events"
    )
    local_ids = {row["event_id"] for row in local_rows}
    # Імпортовані події отримують локальний event_id, але causation_id — це
    # вихідний event_id. Без цього повторний імпорт вважав би їх новими.
    local_ids.update(
        row["causation_id"] for row in local_rows if row["causation_id"]
    )
    by_aggregate: dict[str, list[dict]] = {}
    for row in local_rows:
        key = row["aggregate_id"] or ""
        by_aggregate.setdefault(key, []).append(row)

    duplicates: list[str] = []
    conflicts: list[dict] = []
    incoming: list[dict] = []
    for event in events:
        if event.get("event_id") in local_ids:
            duplicates.append(event["event_id"])
            continue
        payload = event.get("payload") or {}
        aggregate = event.get("aggregate_id") or ""
        touched = sorted(PROTECTED.intersection(payload.keys()))
        if touched:
            for local in by_aggregate.get(aggregate, []):
                local_payload = json.loads(local["payload_json"] or "{}")
                if set(PROTECTED).intersection(local_payload) and local["payload_hash"] != event.get("payload_hash"):
                    conflicts.append({
                        "event_id": event["event_id"],
                        "aggregate_id": aggregate,
                        "fields": touched,
                        "local_event_id": local["event_id"],
                        "reason": "protected_field_divergence",
                    })
                    break
        incoming.append(event)
    return {
        "format": bundle.get("format"),
        "bundle_digest": bundle.get("digest"),
        "local_event_count": len(local_rows),
        "bundle_event_count": len(events),
        "new": [event["event_id"] for event in incoming],
        "duplicates": duplicates,
        "conflicts": conflicts,
        "safe_to_apply": not conflicts,
    }


def import_bundle(store, bundle: dict, actor: dict, *, apply: bool = False) -> dict:
    require_human(actor)
    if bundle.get("format") != FORMAT:
        raise CairnError("bad_bundle", "формат бандла не підтримується", exit_code=2)
    events = bundle_events(bundle)
    # Виміряний дефект: тут не було перевірки digest, тому підмінений
    # `digest` давав applied_count=1 і exit 0. `restore_bundle` і
    # `merge._bundle_shape` її робили — import ні.
    audit = verify_bundle(bundle)
    if not audit["ok"]:
        raise CairnError("bundle_digest_mismatch", "bundle не підтверджує власний вміст",
                         exit_code=4, details={"problems": audit["problems"]})
    chain = ledger.verify_events(events)
    if not chain["ok"]:
        raise CairnError("bundle_chain_corrupt", "ланцюжок бандла пошкоджено", exit_code=4,
                         details={"problems": chain["problems"]})
    report = inspect(store, bundle)
    if not apply:
        return {**report, "applied": False, "applied_count": 0, "dry_run": True}

    if report["conflicts"]:
        raise CairnError(
            "federation_conflict",
            "захищені поля розходяться; автоматичний import заборонений",
            exit_code=4, details={"conflicts": report["conflicts"]},
        )

    by_id = {event["event_id"]: event for event in events}
    applied = 0

    def op(con):
        nonlocal applied
        for eid in report["new"]:
            event = by_id[eid]
            key = f"fed:{bundle.get('digest', 'unknown')}:{eid}"
            stored = store.append(
                con, event["event_type"], event.get("aggregate_id"),
                event.get("payload") or {}, actor, key,
                causation_id=eid,
            )
            if not stored["replayed"]:
                applied += 1
        return {"applied": applied}

    result = store.mutate(op)
    return {**report, "applied": True, "applied_count": result["applied"], "dry_run": False}


def verify_bundle(bundle: dict) -> dict:
    """Перевірка цілого бандла перед restore:digest + ланцюжок подій."""
    events = bundle.get("events") or []
    problems: list[str] = []
    expected = "sha256:" + sha256_text(canonical({
        "format": bundle.get("format"), "events": events, "manifest": bundle.get("manifest"),
    }))
    if expected != bundle.get("digest"):
        problems.append("digest бандла не збігається")
    chain = ledger.verify_events(events)
    if not chain["ok"]:
        problems.extend(chain["problems"])
    return {"ok": not problems, "problems": problems, "events": len(events)}


def restore_bundle(bundle: dict, target_root: str | Path, actor: dict) -> dict:
    """Backup/restore: відтворити проєкт з бандла в порожню теку.

    Зберігаємо manifest(source project/replica identity) і весь журнал. Події
    отримують нові локальні event_id, але causation_id вказує на вихідні —
    таким чином повторний restore залишається ідемпотентним за змістом.
    """
    require_human(actor)
    checked = verify_bundle(bundle)
    if not checked["ok"]:
        raise CairnError("bundle_invalid", "бандл не проходить перевірку", exit_code=4,
                         details={"problems": checked["problems"]})
    target = Path(target_root).resolve()
    if target.exists() and any(target.iterdir()):
        raise CairnError("target_not_empty", "тека restore має бути порожньою", exit_code=2)
    target.mkdir(parents=True, exist_ok=True)
    cairn = target / ".cairn"
    cairn.mkdir(parents=True, exist_ok=True)
    for folder in INIT_FOLDERS:
        (cairn / folder).mkdir(parents=True, exist_ok=True)
    (cairn / ".gitignore").write_text("cache/\nruntime/\nsecrets/\n", encoding="utf-8", newline="\n")
    (cairn / "manifest.json").write_text(
        json.dumps(bundle["manifest"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    store = Store.open(target)
    imported = import_bundle(store, bundle, actor, apply=True)
    rebuilt = store.rebuild()
    health = store.doctor()
    return {
        "restored_events": imported["applied_count"],
        "source_events": checked["events"],
        "project_id": bundle["manifest"]["project_id"],
        "replica_id": bundle["manifest"]["replica_id"],
        "rebuild": rebuilt,
        "doctor_ok": health["ok"],
    }
