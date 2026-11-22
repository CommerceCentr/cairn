"""Вимір спільної роботи: переносність, зони без колізій, спільна картина.

    python tools/measure_teamwork.py --repo . [--out report.json]

Нічого не вигадує й нічого не тверджує наперед: кожен рядок звіту — це
результат справжньої операції над справжнім сховищем у temp. Якщо щось не
працює, це видно у звіті як `ok: false`, а не зникає.

Три питання, на які відповідає цей прилад:

```text
1  чи можна ПЕРЕНЕСТИ контекст проєкту й отримати ту саму відповідь
2  чи двоє агентів у зонах не псують одне одному роботу
3  чи після обміну в кожного одна СПІЛЬНА картина
```
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import code, federation, merge, semantic, zones  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

HUMAN = {"kind": "human", "id": "operator"}
AGENT_A = {"kind": "agent", "id": "agent-a"}
AGENT_B = {"kind": "agent", "id": "agent-b"}


def fresh(path: Path) -> Store:
    Store(path).init()
    return Store.open(path)


def seed(store: Store, repo: str, *, title: str, key: str, actor: dict) -> str:
    """Один факт у сховищі, щоб було що переносити."""
    return semantic.put_object(
        store, object_type="observation", title=title,
        summary=f"факт {key}", actor=actor, stable_key=key,
        semantic={"kind": "observation", "repo": repo},
    )["object"]["id"]


def measure_portability(temp: Path, repo: str) -> dict:
    """Перенос сховища: export -> restore у ПОРОЖНЮ теку -> та сама відповідь."""
    home = temp / "portability" / "home"
    store = fresh(home)
    ident = code.git.identity(repo)
    store.mutate(lambda con: store.append(con, "repository.upserted", ident["id"],
                                          ident, HUMAN, f"repo:{ident['id']}"))
    for index in range(5):
        seed(store, repo, title=f"факт {index}", key=f"port-{index}", actor=HUMAN)

    bundle_path = temp / "portability" / "bundle.json"
    bundle = federation.export_bundle(store, path=bundle_path)
    away = temp / "portability" / "away"
    restored = federation.restore_bundle(bundle, away, HUMAN)

    before = semantic.query(store, object_type="observation")
    after = semantic.query(Store.open(away), object_type="observation")

    def fingerprint(result: dict) -> list[tuple]:
        # Порівнюємо ЗМІСТ, не локальні ідентифікатори подій: після переносу
        # події отримують нові локальні id, а посилання на вихідні лежить
        # у `causation_id`. Зміст мусить збігтися побайтово.
        return sorted((row["stable_key"], row["title"], row["summary"],
                       row["trust_class"], row["lifecycle_state"])
                      for row in result["objects"])

    same = fingerprint(before) == fingerprint(after)
    origins = Store.open(away).read_one(
        "SELECT count(*) n FROM events WHERE causation_id IS NOT NULL")["n"]
    return {
        "ok": bool(same and restored.get("doctor_ok")),
        "bundle_bytes": bundle_path.stat().st_size,
        "events_exported": bundle["event_count"],
        "events_restored": restored["restored_events"],
        "objects_before": len(before["objects"]),
        "objects_after": len(after["objects"]),
        "content_identical": same,
        "doctor_ok_after_move": bool(restored.get("doctor_ok")),
        "events_with_origin_link": origins,
        "note": "перенос — копія теки або один файл-бандл; мережа не потрібна",
    }


def measure_zones(temp: Path, repo: str, *, writes: int = 50) -> dict:
    """Двоє агентів: та сама зона -> черга; різні зони -> без блокування."""
    home = temp / "zones"
    store = fresh(home)
    repo_id = code.git.identity(repo)["id"]
    zones.descriptor(store, "api", repo=repo_id, pathspec="*.py", actor=HUMAN)
    zones.descriptor(store, "web", repo=repo_id, pathspec="*.js", actor=HUMAN)

    a_hold = zones.acquire(store, "api", AGENT_A, ttl_s=60)
    b_same = zones.acquire(store, "api", AGENT_B, ttl_s=60)
    b_other = zones.acquire(store, "web", AGENT_B, ttl_s=60)

    # A дописує свою частину під власним fence
    a_written = 0
    for index in range(writes):
        if zones.append(store, "api", AGENT_A, a_hold["fence"],
                        {"op": "write", "n": index})["ok"]:
            a_written += 1
    # B дописує у СВОЮ зону одночасно
    b_written = 0
    for index in range(writes):
        if zones.append(store, "web", AGENT_B, b_other["fence"],
                        {"op": "write", "n": index})["ok"]:
            b_written += 1

    # Чужий агент у зайнятій зоні. Очікування тут БУЛО хибним: я чекав
    # `stale_fence`, а код віддає `deferred` — і він має рацію. Це дві
    # різні причини: `deferred` = «зона не твоя», `stale_fence` = «ти був
    # власником, твій fence уже недійсний». Злиття їх в одну мітку
    # приховало б, хто саме кому заважає.
    foreign = zones.append(store, "api", AGENT_B, a_hold["fence"] + 7, {"op": "steal"})

    # A віддає зону, його старий fence стає недійсним
    released = zones.release(store, "api", AGENT_A, a_hold["fence"])
    after_release = zones.append(store, "api", AGENT_A, a_hold["fence"], {"op": "late"})
    b_now = zones.acquire(store, "api", AGENT_B, ttl_s=60)
    b_after = zones.append(store, "api", AGENT_B, b_now["fence"], {"op": "write", "n": 999})

    api_journal = zones.read_journal(store, "api")
    web_journal = zones.read_journal(store, "web")
    seqs = [row["seq"] for row in api_journal]

    return {
        "ok": bool(
            a_hold["status"] == "acquired"
            and b_same["status"] == "deferred"
            and b_other["status"] == "acquired"
            and a_written == writes and b_written == writes
            and foreign["status"] == "deferred"
            and after_release["status"] == "stale_fence"
            and b_after["ok"]
            and seqs == sorted(set(seqs))
        ),
        "same_zone_second_agent": b_same["status"],
        "same_zone_holder_named": b_same.get("holder"),
        "other_zone_second_agent": b_other["status"],
        "writes_requested_each": writes,
        "writes_landed_a": a_written,
        "writes_landed_b": b_written,
        "lost_writes": (writes - a_written) + (writes - b_written),
        "foreign_agent_in_busy_zone": foreign["status"],
        "fence_after_release": released.get("fence"),
        "old_fence_after_release": after_release["status"],
        "second_agent_took_over": b_after["ok"],
        "api_journal_lines": len(api_journal),
        "web_journal_lines": len(web_journal),
        "journal_seq_strictly_growing": seqs == sorted(set(seqs)),
        "note": "зони керуються лише з Python: CLI-групи `zone` ще немає",
    }


def measure_shared_picture(temp: Path, repo: str) -> dict:
    """Дві машини -> обмін бандлом -> в однієї спільна картина обох."""
    left_root, right_root = temp / "shared" / "left", temp / "shared" / "right"
    left, right = fresh(left_root), fresh(right_root)
    ident = code.git.identity(repo)
    head = ident["head"]
    for store in (left, right):
        store.mutate(lambda con, s=store: s.append(con, "repository.upserted",
                                                   ident["id"], ident, HUMAN,
                                                   f"repo:{ident['id']}"))
    zones.descriptor(left, "api", repo=ident["id"], pathspec="*.py", actor=HUMAN)
    zones.descriptor(right, "web", repo=ident["id"], pathspec="*.js", actor=HUMAN)
    hold_l = zones.acquire(left, "api", AGENT_A, ttl_s=60)
    hold_r = zones.acquire(right, "web", AGENT_B, ttl_s=60)
    zones.append(left, "api", AGENT_A, hold_l["fence"], {"op": "left-work"})
    zones.append(right, "web", AGENT_B, hold_r["fence"], {"op": "right-work"})
    zones.release(left, "api", AGENT_A, hold_l["fence"])
    zones.release(right, "web", AGENT_B, hold_r["fence"])

    left_fact = seed(left, repo, title="ліва частина", key="shared-left", actor=HUMAN)
    right_fact = seed(right, repo, title="права частина", key="shared-right", actor=HUMAN)
    semantic.put_object(right, object_type="observation", title="права з комітом",
                        summary="факт із посиланням на коміт", actor=HUMAN,
                        stable_key="shared-right-commit",
                        semantic={"kind": "observation", "commit": head})

    bundle = zones.export_bundle(right, ["web"])
    bundle["cairn_bundle"] = federation.export_bundle(right)

    report = merge.inspect_merge(left, bundle, repo_path=repo)
    applied = merge.merge_bundle(left, bundle, repo_path=repo, actor=HUMAN, apply=True)

    keys = {row["stable_key"] for row in
            semantic.query(left, object_type="observation")["objects"]}
    zone_files = sorted(p.name for p in zones.descriptors_dir(left.root).glob("*.json"))
    return {
        "ok": bool(report["safe_to_apply"] and applied.get("applied")
                   and {"shared-left", "shared-right"} <= keys
                   and "web.json" in zone_files),
        "repo_identity_checked": report["repo_id"] == ident["id"],
        "commit_links_resolved": sum(1 for item in report["commit_links"] if item["in_repo"]),
        "commit_links_unresolved": len(report["unlinked_commits"]),
        "conflicts": [item["kind"] for item in report["conflicts"]],
        "safe_to_apply": report["safe_to_apply"],
        "objects_left_before": 1,
        "left_sees_own_fact": "shared-left" in keys,
        "left_sees_remote_fact": "shared-right" in keys,
        "zones_visible_on_left": zone_files,
        "merge_event_id": applied.get("merge_event_id"),
        # Числа лежать плоско в результаті (`counts` — лише у payload події).
        # Два РІЗНИХ шляхи, які легко сплутати: факти приїжджають подіями
        # федерації й відтворюються проєкцією (`merged_events`), а
        # `merged_context_objects` — це лише context-обʼєкти самої зони.
        # Нуль у другому полі не означає, що фактів не приїхало.
        "merged_events": applied.get("applied_events"),
        "merged_context_objects": applied.get("written_objects"),
        "merged_zones": applied.get("written_zones"),
        "note": "merge перевіряє тотожність репозиторію і кожен хеш локально",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="measure_teamwork")
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--writes", type=int, default=50)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    repo = str(Path(args.repo).resolve())

    temp = Path(tempfile.mkdtemp(prefix="teamwork-"))
    try:
        report = {
            "repo": Path(repo).name,
            "portability": measure_portability(temp, repo),
            "zones": measure_zones(temp, repo, writes=args.writes),
            "shared_picture": measure_shared_picture(temp, repo),
        }
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    report["all_ok"] = all(section["ok"] for key, section in report.items()
                           if isinstance(section, dict))
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    port, zone, shared = report["portability"], report["zones"], report["shared_picture"]
    print(f"ВЕРДИКТ: перенос {'ок' if port['ok'] else 'ПРОВАЛ'} "
          f"({port['events_restored']}/{port['events_exported']} подій, зміст той самий: "
          f"{port['content_identical']}) · "
          f"зони {'ок' if zone['ok'] else 'ПРОВАЛ'} "
          f"(втрачено записів {zone['lost_writes']}, другий агент {zone['same_zone_second_agent']}) · "
          f"спільна картина {'ок' if shared['ok'] else 'ПРОВАЛ'} "
          f"(конфліктів {len(shared['conflicts'])}, бачить чужий факт: "
          f"{shared['left_sees_remote_fact']})")
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
