"""Федерація та конфігурація: явний конфлікт замість тихої перезаписі.

Запуск:  python tests/test_unified_federation.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import compaction_link, federation, ledger, nodes, semantic, why  # noqa: E402
from cairn_unified.code import git as git_adapter  # noqa: E402
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
        tracked = git_adapter.git(a, "ls-files").splitlines()[0]
        source_path = a / tracked
        original = source_path.read_bytes()
        source_path.write_bytes(original + b"\nUNCOMMITTED\n")
        committed = nodes.discover_commit(str(a), "fixture")
        dirty = nodes.discover(str(a), "fixture", [tracked])
        whole = f"whole:{tracked}"
        ok("Git-індекс читає commit, а не брудний worktree",
           next(x for x in committed if x["path"] == tracked and x["qualified_name"] == whole)["fingerprint"]
           != next(x for x in dirty if x["qualified_name"] == whole)["fingerprint"])
        source_path.write_bytes(original)

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
        identity = git_adapter.identity(a)
        sa.mutate(lambda con: sa.append(con, "repository.upserted", identity["id"],
                                         identity, HUMAN, f"repo:{identity['id']}"))
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
        changed_actor = [dict(e) for e in loaded["events"]]
        changed_actor[-1]["actor"] = {"kind": "human", "id": "impostor"}
        ok("підміна автора розриває новий ланцюг",
           ledger.verify_events(changed_actor)["ok"] is False)
        changed_time = [dict(e) for e in loaded["events"]]
        changed_time[-1]["occurred_at"] = "1999-01-01T00:00:00Z"
        ok("підміна вихідного часу розриває новий ланцюг",
           ledger.verify_events(changed_time)["ok"] is False)

        print("3. ІМПОРТ БЕЗ КОНФЛІКТІВ")
        report = federation.import_bundle(sb, loaded, HUMAN)
        ok("dry-run нічого не пише", report["applied"] is False)
        ok("є нові події", len(report["new"]) > 0, str(len(report["new"])))
        ok("без конфліктів", report["safe_to_apply"] is True)
        applied = federation.import_bundle(sb, loaded, HUMAN, apply=True)
        ok("події застосовано", applied["applied_count"] == len(report["new"]),
           f"{applied['applied_count']}/{len(report['new'])}")
        source = next(e for e in loaded["events"] if e["aggregate_id"] == "DEC-FED-001")
        copied = sb.read_one("SELECT actor_json, occurred_at, recorded_at, causation_id "
                             "FROM events WHERE causation_id = ?", (source["event_id"],))
        ok("походження має вихідний час і автора",
           copied is not None and copied["occurred_at"] == source["occurred_at"]
           and json.loads(copied["actor_json"]) == source["actor"]
           and copied["causation_id"] == source["event_id"])
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
        c = make_replica(temp, "replica-c")
        sc = Store.open(c)
        federation.import_bundle(sc, return_bundle, HUMAN, apply=True)
        third = sc.read_one("SELECT actor_json, occurred_at FROM events WHERE causation_id = ?",
                            (source["event_id"],))
        ok("другий перенос зберігає первісний час і автора",
           third is not None and third["occurred_at"] == source["occurred_at"]
           and json.loads(third["actor_json"]) == source["actor"])
        c_identity = git_adapter.identity(c)
        sc.mutate(lambda con: sc.append(con, "repository.upserted", c_identity["id"],
                                        c_identity, HUMAN, f"repo:{c_identity['id']}"))
        nodes.index(sc, c_identity["id"], HUMAN, paths=[tracked])
        session = semantic.put_object(sc, object_type="session", title="two edits",
                                      summary="checkpoint", stable_key="grouped-test-session",
                                      actor=HUMAN, semantic={"kind": "session"})["object"]
        edits = [{"kind": "mutation", "event_id": f"edit-{i}", "status": "completed",
                  "path": str(c / tracked), "seq": i, "ts": "2026-01-01T00:00:00Z",
                  "tool": "edit", "evidence_scope": "file"} for i in (1, 2)]
        grouped = compaction_link.link_mutations(
            sc, events=edits, session_obj_id=session["id"], repo_id=c_identity["id"],
            repo_path=(str(c),), actor=HUMAN, compact_relations=True)
        file_node = next(n for n in nodes.resolve(sc, c_identity["id"], tracked)
                         if n["address"] == f"whole:{tracked}")
        ok("одне ребро містить доказ кожної з двох правок",
           grouped["new"] == 1 and len(grouped["all_links"]) == 2
           and len(why._node_chain(sc, file_node["stable_key"])) == 2)
        later = {**edits[-1], "event_id": "edit-3", "seq": 3}
        incremental = compaction_link.link_mutations(
            sc, events=edits + [later], session_obj_id=session["id"],
            repo_id=c_identity["id"], repo_path=(str(c),), actor=HUMAN,
            compact_relations=True)
        conditions = [json.loads(row["condition_json"]) for row in sc.read(
            "SELECT condition_json FROM relations WHERE from_id = ? AND to_id = ? "
            "AND relation_type = 'caused_by'",
            (file_node["stable_key"], session["id"]))]
        ok("нова правка не дублює старі докази",
           incremental["new"] == 1 and len(conditions) == 2
           and sorted(len(item["events"]) for item in conditions) == [1, 2]
           and len(why._node_chain(sc, file_node["stable_key"])) == 3)

        updated_source = {**source["payload"], "summary": "new source summary"}
        sa.mutate(lambda con: sa.append(
            con, "object.upserted", "DEC-FED-001", updated_source, HUMAN,
            "test:source-summary-update"))
        update_bundle = federation.export_bundle(sa)
        update_result = federation.import_bundle(sb, update_bundle, HUMAN, apply=True)
        ok("лінійне оновлення джерела переноситься без конфлікту",
           update_result["applied_count"] == 1 and not update_result["conflicts"])

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
        ok("відновлена машина має власну репліку",
           restored["replica_id"] != backup["manifest"]["replica_id"])
        restored_store = Store.open(temp / "restored")
        ok("об'єкти відтворено",
           sorted(row["id"] for row in restored_store.read("SELECT id FROM objects")) == src_objects)
        ok("doctor після restore зелений", restored["doctor_ok"] is True)
        ok("ланцюжок після restore цілий", ledger.verify(
            ledger.ledger_path(temp / "restored"))["ok"] is True)
        checkout = temp / "checkout"
        subprocess.run(["git", "clone", "-q", str(a), str(checkout)], check=True,
                       capture_output=True)
        restored_checkout = federation.restore_bundle(backup, checkout, HUMAN)
        bound = Store.open(checkout).read_one("SELECT path FROM repositories WHERE id = ?",
                                             (identity["id"],))
        ok("restore у Git checkout прив'язує локальний шлях",
           restored_checkout["doctor_ok"] and bound is not None
           and Path(bound["path"]).resolve() == checkout.resolve())
        git(checkout, "config", "user.name", "Replica")
        git(checkout, "config", "user.email", "replica@local")
        (checkout / "later.txt").write_text("later\n", encoding="utf-8")
        git(checkout, "add", "later.txt")
        old_date = "2020-01-01T00:00:00+00:00"
        subprocess.run(["git", "-C", str(checkout), "commit", "-q", "-m", "later"],
                       check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_DATE": old_date,
                            "GIT_COMMITTER_DATE": old_date})
        first = git_adapter.git(checkout, "rev-parse", "HEAD^").strip()
        second = git_adapter.git(checkout, "rev-parse", "HEAD").strip()
        facts = [{"id": f"f-{sha}", "semantic": {"kind": "commit_fact",
                  "repo_id": identity["id"], "commit": sha, "committed_at": stamp}}
                 for sha, stamp in ((second, old_date), (first, "2026-01-01T00:00:00+00:00"))]
        ordered = why._commit_history(Store.open(checkout), facts, {}, set())
        ok("Git DAG має пріоритет над переставленим часом комітів",
           [row["commit"] for row in ordered] == [first, second])
        checkout_store = Store.open(checkout)
        checkout_store.mutate(lambda con: checkout_store.append(
            con, "git.commit_indexed", identity["id"],
            {"repo_id": identity["id"], "commit": second, "parents": [first],
             "committed_at": old_date, "topo_index": 1, "message": "later",
             "files": [{"path": "later.txt", "status": "M",
                        "deleted_lines": ["historical reason"]}]},
            HUMAN, "git-archive:test", occurred_at=old_date))
        archived = why._archive_commit_history(checkout_store, identity["id"], "later.txt")
        ok("компактний Git-журнал повертає коміт і вилучену прозу",
           len(archived) == 1 and archived[0]["commit"] == second
           and archived[0]["patches"][0]["deleted_lines"] == ["historical reason"])
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
