"""Дескриптори зон: почерговий запис, відсутність блокування, fence.

Автономний тест: тимчасове сховище, жодного доступу до реальних репо.
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import ontology, semantic, zones  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

A = {"kind": "agent", "id": "agent-alpha"}
B = {"kind": "agent", "id": "agent-beta"}


def main() -> int:
    checks: list[tuple[str, bool]] = []
    with tempfile.TemporaryDirectory(prefix="cairn-zones-") as tmp:
        Store(Path(tmp)).init()
        store = Store.open(Path(tmp))

        for zone, repo, spec in (("beta.loc", "a" * 40, "*.php"),
                                 ("cc.one", "b" * 40, "*.php")):
            r = zones.descriptor(store, zone, repo=repo, pathspec=spec, actor=A)
            checks.append((f"дескриптор {zone} створено",
                           r["ok"] and r["descriptor"]["head"] == 0))

        da = zones.descriptor_path(store.root, "beta.loc")
        db = zones.descriptor_path(store.root, "cc.one")
        la = zones.lock_path(store.root, "beta.loc")
        lb = zones.lock_path(store.root, "cc.one")
        checks.append(("дескриптор і лок — окремі файли на зону",
                       len({da, db, la, lb}) == 4 and da.exists() and db.exists()))

        first = zones.acquire(store, "beta.loc", A)
        checks.append(("перший агент захопив зону", first["ok"] and first["fence"] == 1))

        waits: list[float] = []
        second = None
        for _ in range(20):
            t0 = time.perf_counter()
            second = zones.acquire(store, "beta.loc", B, ttl_s=30)
            waits.append((time.perf_counter() - t0) * 1000)
        checks.append(("другий агент не блокується, а відкладається",
                       second is not None and not second["ok"]
                       and second["status"] == "deferred"))
        checks.append(("deferred повертає поточний head/fence",
                       second["head"] == 0 and second["fence"] == 1))
        checks.append(("очікування вимірюване й коротке",
                       max(waits) < 500 and statistics.median(waits) < 50))
        print(f"  acquire contention ms: median={statistics.median(waits):.3f} "
              f"p95={sorted(waits)[18]:.3f} max={max(waits):.3f}")

        other = zones.acquire(store, "cc.one", B)
        checks.append(("інша зона не блокується першою", other["ok"]))

        appended = zones.append(store, "beta.loc", A, first["fence"],
                                {"path": "core/Engine/Controller.php", "op": "edit"})
        checks.append(("запис у журнал зони", appended["ok"] and appended["seq"] == 1))

        stale = zones.append(store, "beta.loc", A, first["fence"] - 1, {"op": "edit"})
        checks.append(("прострочений fence відхиляється",
                       not stale["ok"] and stale["status"] == "stale_fence"))

        deferred_append = zones.append(store, "beta.loc", B, other["fence"], {"op": "write"})
        checks.append(("запис іншого агента у зайняту зону відкладається",
                       not deferred_append["ok"]
                       and deferred_append["status"] == "deferred"))

        released = zones.release(store, "beta.loc", A, first["fence"])
        checks.append(("звільнення зони", released["ok"] and released["head"] == 1))

        next_hold = zones.acquire(store, "beta.loc", B)
        checks.append(("наступний агент отримує новий fence",
                       next_hold["ok"] and next_hold["fence"] == first["fence"] + 2
                       and next_hold["head"] == 1))
        second_append = zones.append(store, "beta.loc", B, next_hold["fence"],
                                     {"path": "core/Engine/Router.php", "op": "write"})
        checks.append(("почерговий запис без втрати head",
                       second_append["ok"] and second_append["seq"] == 2))

        rows = zones.read_journal(store, "beta.loc")
        checks.append(("журнал append-only і монотонний",
                       len(rows) == 2 and [r["seq"] for r in rows] == [1, 2]))

        # Portability: дескриптор несе відносне посилання на сесію/summary.
        proxy = semantic.put_object(
            store, object_type="observation", title="session summary proxy",
            summary="compaction summary", actor=A, stable_key="zone-session-proxy",
            semantic={"kind": "session", "session_id": "ses-portable",
                      "summary_sha256": "ab" * 32, "compaction_message_id": "msg-portable", "opencode_db": r"C:\fake\opencode.db"},
        )["object"]
        refs = zones.object_refs(store, [proxy["id"]])
        zones.descriptor(store, "beta.loc", repo="a" * 40, pathspec="*.php", actor=A,
                         context_refs=refs,
                         context_identity={"session_id": "ses-portable",
                                           "summary_sha256": "ab" * 32})
        d = __import__("json").loads(
            zones.descriptor_path(store.root, "beta.loc").read_text(encoding="utf-8"))
        checks.append(("дескриптор несе відносне посилання на сесію/summary",
                       d["context_refs"] == refs
                       and d["context_identity"]["session_id"] == "ses-portable"
                       and all(not __import__("pathlib").Path(r["path"]).is_absolute()
                               for r in refs)))

        # Portability: стан зон переноситься одним bundle-ом усередині
        # cairn, без абсолютних шляхів і без пошуку по інших репозиторіях.
        bundle = zones.export_bundle(store, ["beta.loc", "cc.one"])
        checks.append(("бандл несе повний причинний ланцюжок подій",
                       bundle["cairn_bundle"]["chain_ok"]
                       and len(bundle["cairn_bundle"]["events"]) >= 1
                       and len(bundle["context_objects"]) == 1))
        all_refs = [r for d in bundle["descriptors"] for r in d.get("context_refs") or []]
        checks.append(("бандл самоописний; посилання відносні, докази зберігаються",
                       bundle["portable"] and bundle["relative_refs_only"]
                       and bundle["digest"] and all_refs
                       and all(not Path(r["path"]).is_absolute() for r in all_refs)
                       and any(r["path"] in bundle["context_objects"]
                               and bundle["context_objects"][r["path"]]["semantic"].get("opencode_db")
                               for r in all_refs)))
        with tempfile.TemporaryDirectory(prefix="cairn-zones-import-") as dst:
            Store(Path(dst)).init()
            target = Store.open(Path(dst))
            imported = zones.import_bundle(target, bundle, actor=B)
            checks.append(("bundle імпортується в свіже сховище",
                           imported["ok"] and imported["zones"] == 2
                           and imported["digest"] == bundle["digest"]
                           and imported["causal_events"]
                           == len(bundle["cairn_bundle"]["events"])))
            imported_body = json.loads(
                (Path(dst) / refs[0]["path"]).read_text(encoding="utf-8"))
            checks.append(("імпорт не успадковує чужу верифікацію",
                           imported_body["trust_class"] == "imported_untrusted"
                           and "verification" not in imported_body
                           and imported["reset_verification_stamps"] >= 1
                           and imported_body["semantic"]["import_provenance"]["policy"]
                           == "import-does-not-inherit-trust/1.0"))
            checks.append(("перенесений summary доступний за відносним шляхом",
                           all((Path(dst) / r["path"]).is_file() for r in refs)))
            checks.append(("після перенесення стан ідентичний",
                           zones.snapshot(target, ["beta.loc", "cc.one"])
                           == zones.snapshot(store, ["beta.loc", "cc.one"])))
            broken = dict(bundle)
            broken["digest"] = "00" * 32
            checks.append(("реверс: підміна bundle ескалує",
                           zones.import_bundle(target, broken, actor=B)["status"]
                           == "bad_digest"))

        # Паралельна спроба в одній зоні: один запис виграє, другий бачить
        # deferred/stale, journal і head не розходяться.
        hold = zones.acquire(store, "cc.one", B)
        results: list[dict] = []
        barrier = threading.Barrier(2)

        def contender(agent, fence):
            barrier.wait()
            results.append(zones.append(store, "cc.one", agent, fence,
                                        {"op": "write", "agent": agent["id"]}))

        ts = [threading.Thread(target=contender, args=(B, hold["fence"])),
              threading.Thread(target=contender, args=(A, hold["fence"]))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        cc_rows = zones.read_journal(store, "cc.one")
        desc = zones.snapshot(store, ["cc.one"])[0]
        checks.append(("два потоки не втратили й не подвоїли запис",
                       len(results) == 2
                       and sum(1 for r in results if r["ok"]) <= 1
                       and all(r.get("status") in {"appended", "deferred", "stale_fence"}
                               for r in results)
                       and len(cc_rows) == desc["head"]))
        if not any(r["ok"] for r in results):
            retry = zones.append(store, "cc.one", B, hold["fence"],
                                 {"op": "write", "agent": "agent-beta-retry"})
            cc_rows = zones.read_journal(store, "cc.one")
            desc = zones.snapshot(store, ["cc.one"])[0]
            checks.append(("повтор власника оренди дописує без втрати head",
                           retry["ok"] and retry["seq"] == 1
                           and len(cc_rows) == 1 and desc["head"] == 1))
        else:
            checks.append(("один із потоків дописав без втрати head",
                           len(cc_rows) == 1 and desc["head"] == 1))

        obj, snap = zones.context_object(
            store, zones=["beta.loc", "cc.one"], actor=A, commit="942cb5")
        checks.append(("дескриптори потрапили в семантичний об'єкт",
                       obj["semantic"]["kind"] == "zone_descriptors"
                       and len(obj["semantic"]["zones"]) == 2))
        checks.append(("об'єкт містить обидва журнали й протокол",
                       sum(z["journal_lines"] for z in snap) == 3
                       and obj["semantic"]["protocol"] == zones.PROTOCOL))
        checks.append(("без нових таблиць БД",
                       ontology.DB_SCHEMA_VERSION == 1
                       and obj["semantic"]["no_new_db_tables"] is True))

        # Реверс: зіпсований fence у дескрипторі не дає тихо перезаписати журнал.
        bad = zones.descriptor_path(store.root, "cc.one")
        payload = __import__("json").loads(bad.read_text(encoding="utf-8"))
        payload["fence"] = 999
        bad.write_text(__import__("json").dumps(payload), encoding="utf-8")
        rejected = zones.append(store, "cc.one", B, hold["fence"], {"op": "write"})
        checks.append(("реверс: підміна fence ескалує",
                       not rejected["ok"] and rejected["status"] == "stale_fence"
                       and len(zones.read_journal(store, "cc.one")) == 1))

    bad = [label for label, ok in checks if not ok]
    for label, ok in checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    print("ВЕРДИКТ: " + (f"FAIL ({len(checks) - len(bad)}/{len(checks)})"
                        if bad else f"зони не блокують і не втрачають записи ({len(checks)}/{len(checks)})"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
