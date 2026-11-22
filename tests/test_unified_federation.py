"""Федерація та конфігурація: явний конфлікт замість тихої перезаписі.

Запуск:  python tests/test_unified_federation.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import federation, ledger, semantic  # noqa: E402
from cairn_unified.errors import CairnError  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

HUMAN = {"kind": "human", "id": "alice"}
AGENT = {"kind": "agent", "id": "worker"}


def ok(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        raise AssertionError(name)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   text=True, encoding="utf-8", errors="replace")


def make_replica(parent: Path, name: str) -> Path:
    repo = parent / name
    shutil.copytree(ROOT / "fixtures" / "php84-demo", repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Replica")
    git(repo, "config", "user.email", "replica@local")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    Store(repo).init()
    return repo


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="cairn-fed-"))
    try:
        a = make_replica(temp, "replica-a")
        b = make_replica(temp, "replica-b")
        sa, sb = Store.open(a), Store.open(b)

        print("1. КОНФІГУРАЦІЯ: ХТО ЗАТЬМАРЕНИЙ")
        (a / ".cairn" / "config" / "project.json").write_text(
            json.dumps({"budget_chars": 1000}), encoding="utf-8")
        (a / ".cairn" / "config" / "user.local.json").write_text(
            json.dumps({"budget_chars": 2000}), encoding="utf-8")
        cfg = sa.config_value("budget_chars", cli_value=None)
        ok("перемагає user.local", cfg["value"] == 2000 and cfg["scope"] == "user.local")
        ok("проєкт і замовчування затьмарені",
           {row["scope"] for row in cfg["shadowed"]} == {"project.shared", "default"},
           str([row["scope"] for row in cfg["shadowed"]]))
        cli_cfg = sa.config_value("budget_chars", cli_value=3000)
        ok("cli перемагає env і файли", cli_cfg["value"] == 3000 and len(cli_cfg["shadowed"]) == 3)

        print("2. ЕКСПОРТ БАНДЛА")
        semantic.put_object(sa, object_type="decision", title="DEC-FED-001",
                            summary="share this decision", stable_key="DEC-FED-001", actor=HUMAN)
        semantic.put_object(sa, object_type="session", title="portable checkpoint",
                            summary="cause text stored inside Cairn", actor=HUMAN,
                            stable_key="portable-session",
                            semantic={"kind": "session", "source_kind": "fixture"})
        semantic.relate(sa, from_id="portable-session", to_id="DEC-FED-001",
                        relation="derived_from", actor=HUMAN)
        bundle = federation.export_bundle(sa, path=temp / "bundle.json")
        ok("ланцюжок бандла цілий", bundle["chain_ok"] is True)
        ok("digest бандла стабільний",
           bundle["digest"] == federation.export_bundle(sa)["digest"])
        loaded = federation.load_bundle(temp / "bundle.json")
        ok("ланцюжок при імпорті перевіряється",
           ledger.verify_events(loaded["events"])["ok"] is True)

        print("3. ІМПОРТ БЕЗ КОНФЛІКТІВ")
        report = federation.import_bundle(sb, loaded, HUMAN)
        ok("dry-run нічого не пише", report["applied"] is False)
        ok("є нові події", len(report["new"]) > 0, str(len(report["new"])))
        ok("без конфліктів", report["safe_to_apply"] is True)
        applied = federation.import_bundle(sb, loaded, HUMAN, apply=True)
        ok("події застосовано", applied["applied_count"] == len(report["new"]),
           f"{applied['applied_count']}/{len(report['new'])}")
        imported = sb.read_one("SELECT * FROM objects WHERE id = ?", ("DEC-FED-001",))
        ok("об'єкт з'явився в іншій репліці", imported is not None)
        portable = sb.read_one("SELECT summary FROM objects WHERE id = ?",
                               ("portable-session",))
        relation = sb.read_one("SELECT id FROM relations WHERE from_id = ? AND to_id = ?",
                               ("portable-session", "DEC-FED-001"))
        ok("текст і зв'язок checkpoint фізично перенесено",
           portable and portable["summary"] == "cause text stored inside Cairn"
           and relation is not None)
        ok("довіра збереглась", imported["trust_class"] == "human_authoritative")
        again = federation.import_bundle(sb, loaded, HUMAN)
        ok("повторний імпорт — лише дублікати",
           len(again["new"]) == 0 and len(again["duplicates"]) == len(report["new"]))
        return_bundle = federation.export_bundle(sb)
        back = federation.import_bundle(sa, return_bundle, HUMAN)
        ok("повернення з B не імпортує відлуння подій A",
           len(back["new"]) == 1 and len(back["duplicates"]) == len(bundle["events"]))
        federation.import_bundle(sa, return_bundle, HUMAN, apply=True)
        round_trip = federation.import_bundle(sb, federation.export_bundle(sa), HUMAN)
        ok("повторний двосторонній обмін ідемпотентний",
           len(round_trip["new"]) == 0)

        print("4. КОНФЛІКТ ЗАХИЩЕНОГО ПОЛЯ")
        semantic.put_object(sb, object_type="decision", title="DEC-FED-002",
                            summary="local divergent decision", stable_key="DEC-FED-001", actor=AGENT)
        # Створюємо в A іншу версію того самого aggregate з іншим change.
        semantic.put_object(sa, object_type="decision", title="DEC-FED-003",
                            summary="remote authoritative decision", stable_key="DEC-FED-001", actor=HUMAN)
        fresh = federation.export_bundle(sa)
        inspect = federation.inspect(sb, fresh)
        ok("розходження за trust/class названо",
           any(item["aggregate_id"] == "DEC-FED-001" for item in inspect["conflicts"]),
           str(inspect["conflicts"][:1]))
        try:
            federation.import_bundle(sb, fresh, HUMAN, apply=True)
        except CairnError as exc:
            ok("конфліктне застосування відхилено", exc.code == "federation_conflict", exc.code)
        else:
            ok("конфліктне застосування відхилено", False, "помилки не було")

        print("5. BACKUP / RESTORE В ПОРОЖНЮ TEKY")
        backup = federation.export_bundle(sb, path=temp / "backup.json")
        src_events = len(backup["events"])
        src_objects = sorted(row["id"] for row in sb.read("SELECT id FROM objects"))
        restored = federation.restore_bundle(backup, temp / "restored", HUMAN)
        ok("відтворено всі події", restored["restored_events"] == src_events,
           f"{restored['restored_events']}/{src_events}")
        ok("ідентичність проєкту збережена", restored["project_id"] == backup["manifest"]["project_id"])
        restored_store = Store.open(temp / "restored")
        ok("об'єкти відтворено",
           sorted(row["id"] for row in restored_store.read("SELECT id FROM objects")) == src_objects)
        ok("doctor після restore зелений", restored["doctor_ok"] is True)
        ok("ланцюжок після restore цілий", ledger.verify(
            ledger.ledger_path(temp / "restored"))["ok"] is True)
        tampered = dict(backup)
        tampered["events"] = list(backup["events"])[:-1]
        try:
            federation.restore_bundle(tampered, temp / "tampered", HUMAN)
        except CairnError as exc:
            ok("пошкоджений бандл відхилено", exc.code == "bundle_invalid", exc.code)
        else:
            ok("пошкоджений бандл відхилено", False, "помилки не було")

        print("\nВЕРДИКТ: федерація не перезаписує authority тихо, backup/restore перевірені")
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
