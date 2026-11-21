"""Регресія: підроблений bundle не має бути застосований.

Виміряний дефект (2026-11-21): `federation.import_bundle` не викликав
`verify_bundle`, тому bundle із підміненим `digest` давав
`applied: true, applied_count: 1` і exit 0. `restore_bundle` і
`merge._bundle_shape` цю перевірку робили — import ні.

Кожен випадок нижче мусить ВІДМОВИТИ. Контрольний випадок A мусить
пройти, інакше тест доводить лише те, що import зламано цілком.
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import federation, semantic  # noqa: E402
from cairn_unified.errors import CairnError  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

HUMAN = {"kind": "human", "id": "tamper-human"}


def fresh(prefix: str) -> Path:
    temp = Path(tempfile.mkdtemp(prefix=prefix))
    Store(temp).init()
    return temp


def source_bundle(temp: Path) -> dict:
    store = Store.open(temp)
    semantic.put_object(store, object_type="decision", title="джерело",
                        summary="обʼєкт для перенесення", actor=HUMAN,
                        stable_key="tamper-source-1")
    return federation.export_bundle(store)


def attempt(target: Path, bundle: dict) -> tuple[bool, str]:
    """(відмовлено?, код). Відмова — це успіх для випадків B..E."""
    store = Store.open(target)
    try:
        result = federation.import_bundle(store, bundle, HUMAN, apply=True)
    except CairnError as err:
        return True, err.code
    except Exception as err:                      # noqa: BLE001 — саме це й ловимо
        return False, f"нетипізоване: {type(err).__name__}"
    return False, f"застосовано {result.get('applied_count')}"


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    source = fresh("tamper-src-")
    try:
        bundle = source_bundle(source)

        # A — контроль: цілий bundle мусить застосуватись.
        target = fresh("tamper-a-")
        try:
            refused, detail = attempt(target, copy.deepcopy(bundle))
            checks.append(("A цілий bundle застосовується", not refused, detail))
        finally:
            shutil.rmtree(target, ignore_errors=True)

        # B — підмінений digest.
        bad = copy.deepcopy(bundle)
        bad["digest"] = "sha256:" + "0" * 64
        target = fresh("tamper-b-")
        try:
            refused, detail = attempt(target, bad)
            checks.append(("B підмінений digest відмовлено", refused, detail))
        finally:
            shutil.rmtree(target, ignore_errors=True)

        # C — підмінений payload, digest лишився старим.
        bad = copy.deepcopy(bundle)
        if bad["events"]:
            bad["events"][-1].setdefault("payload", {})["title"] = "підмінено"
        target = fresh("tamper-c-")
        try:
            refused, detail = attempt(target, bad)
            checks.append(("C підмінений payload відмовлено", refused, detail))
        finally:
            shutil.rmtree(target, ignore_errors=True)

        # D — подія без event_id: мусить бути типізована відмова, не KeyError.
        bad = copy.deepcopy(bundle)
        if bad["events"]:
            bad["events"][-1].pop("event_id", None)
        target = fresh("tamper-d-")
        try:
            refused, detail = attempt(target, bad)
            checks.append(("D подія без event_id відмовлено типізовано", refused, detail))
        finally:
            shutil.rmtree(target, ignore_errors=True)

        # E — не-обʼєкт замість події.
        bad = copy.deepcopy(bundle)
        bad["events"] = [*bad["events"], "5"]
        target = fresh("tamper-e-")
        try:
            refused, detail = attempt(target, bad)
            checks.append(("E не-обʼєкт у events відмовлено типізовано", refused, detail))
        finally:
            shutil.rmtree(target, ignore_errors=True)
    finally:
        shutil.rmtree(source, ignore_errors=True)

    good = 0
    for label, ok, detail in checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {label:48} | {detail}")
        good += int(ok)
    print(f"ВЕРДИКТ: {good}/{len(checks)} — підроблений bundle не застосовується")
    return 0 if good == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
