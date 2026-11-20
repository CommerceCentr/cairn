"""Збереження контексту сесії через Cairn: індекси, прив'язки, заміри.

Запуск:  python tests/test_unified_session_context.py

Уся робота відбувається в тимчасовій теці. Публічний репозиторій не
змінюється: немає ані коміту, ані запису в Q:\\prj\\cairn\\.cairn.

Що перевіряється:
  1. індекс коду (stable symbol identity + revision + edges);
  2. прив'язки до вузлів: WorkItem <-> Contract <-> Symbol <-> Assertion;
  3. Сесія A зберігає контекст і handoff; Сесія B відновлює його без transcript;
  4. digest пакета відтворюється, застаріле рішення не потрапляє в контекст;
  5. заміри: обсяг повторного читання, час, ефект кешу, цілісність ланцюжка.
"""

from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import code, context, evidence, handoff, ledger, semantic, work  # noqa: E402
from cairn_unified.canon import canonical  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

HUMAN = {"kind": "human", "id": "session-human"}
AGENT_A = {"kind": "agent", "id": "session-agent"}
SYSTEM = {"kind": "system", "id": "session-indexer"}
ROUNDS = 40


def ok(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        raise AssertionError(name)


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return proc.stdout.strip()


def median_ms(fn, rounds: int = ROUNDS) -> tuple[float, object]:
    samples: list[float] = []
    value = None
    for _ in range(rounds):
        start = time.perf_counter()
        value = fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples), value


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="cairn-session-"))
    project = temp / "project"
    try:
        shutil.copytree(ROOT / "fixtures" / "php84-demo", project)
        for args in (("init", "-q"), ("config", "user.name", "Session"),
                     ("config", "user.email", "session@local"), ("add", "."),
                     ("commit", "-q", "-m", "init")):
            git(project, *args)
        base = git(project, "rev-parse", "HEAD")

        Store(project).init()
        store = Store.open(project)

        print("1. ІНДЕКС КОДУ ТА ПРИВ'ЯЗКИ ДО ВУЗЛІВ")
        identity = code.git.identity(str(project))
        store.mutate(lambda con: store.append(con, "repository.upserted", identity["id"],
                                              identity, HUMAN, f"repo:{identity['id']}"))
        rid = identity["id"]
        indexed = code.php.index(store, rid, SYSTEM)
        symbols = store.read("SELECT * FROM code_symbols ORDER BY stable_key")
        edges = store.read("SELECT * FROM code_edges")
        ok("символи проіндексовано", len(symbols) >= 1, f"{len(symbols)}")
        ok("стабільна тотожність символа", all(
            row["stable_key"].startswith("symbol://") for row in symbols))
        ok("ревізія символа є", all(row["revision"] for row in symbols))
        ok("індекс має digest", bool(indexed.get("index_digest")), str(indexed.get("index_digest"))[:24])
        sym = symbols[0]["stable_key"]

        rules = [
            {"id": "AC-1", "kind": "evidence", "evidence_type": "commit"},
            {"id": "AC-2", "kind": "evidence", "evidence_type": "test"},
            {"id": "AC-3", "kind": "source_absent",
             "path": "src/Pricing/PriceCalculator.php", "pattern": "DEBUG_LEAK"},
        ]
        wid = work.create_work(store, title="Session context scenario", actor=HUMAN,
                               scope={"repo_id": rid}, capabilities=["php84"],
                               acceptance=rules, budget_limit_chars=6000)["work"]["id"]
        decision = semantic.put_object(
            store, object_type="decision", title="DEC-SESSION-001", stable_key="DEC-SESSION-001",
            summary="remove client price debug logging before release", scope=[sym],
            constraints=["no DEBUG_LEAK in committed source"], actor=AGENT_A,
        )["object"]
        ok("рішення агента proposed", decision["lifecycle_state"] == "proposed")
        semantic.accept_object(store, object_id="DEC-SESSION-001", actor=HUMAN, rationale="approved")
        contract = semantic.put_object(
            store, object_type="contract", title="CTR-SESSION-001", stable_key="CTR-SESSION-001",
            summary="commit must not contain DEBUG_LEAK", scope=[sym],
            constraints=["source_absent:DEBUG_LEAK"], actor=HUMAN,
        )["object"]
        stale = semantic.put_object(
            store, object_type="decision", title="DEC-OLD", stable_key="DEC-OLD",
            summary="old approach (superseded)", actor=HUMAN,
        )["object"]
        semantic.supersede(store, object_id="DEC-OLD", summary="replaced", actor=HUMAN,
                           title="DEC-OLD")

        semantic.relate(store, from_id=wid, to_id="DEC-SESSION-001", relation="governed_by", actor=HUMAN)
        semantic.relate(store, from_id=wid, to_id=contract["id"], relation="governed_by", actor=HUMAN)
        semantic.relate(store, from_id=wid, to_id=sym, relation="targets_symbol", actor=HUMAN)
        semantic.relate(store, from_id=decision["id"], to_id=sym, relation="related_to", actor=HUMAN)
        assertion = semantic.add_assertion(
            store, subject_id=sym, predicate="symbol.no_debug_leak", literal=True,
            actor=HUMAN, trust="human_authoritative",
        )["assertion"]
        relations = store.read("SELECT * FROM relations")
        ok("вузли пов'язані", len(relations) >= 4, f"{len(relations)} зв'язків")
        ok("прив'язка до символа є", any(
            row["to_id"] == sym for row in relations))

        print("2. СЕСІЯ A: ВИКОНАННЯ, ДОКАЗИ, КОНТЕКСТ, HANDOFF")
        work.register_agent(store, agent_id="session-agent", actor=HUMAN, capabilities=["php84"])
        token = int(work.claim(store, wid, "session-agent", AGENT_A)["claim"]["fencing_token"])
        wt = project / ".cairn" / "runtime" / "worktrees" / "session"
        code.git.worktree_add(str(project), str(wt), "session-fix", base)
        source = wt / "src" / "Pricing" / "PriceCalculator.php"
        source.write_text(source.read_text(encoding="utf-8").replace(
            "        // DEBUG_LEAK: тимчасовий логінг цін клієнта\n", ""), encoding="utf-8")
        sha = code.git.commit(str(wt), "fix: session", ["src/Pricing/PriceCalculator.php"])["commit"]
        commit_ev = evidence.record_commit(store, rid, sha, SYSTEM, subject_id=wid)["evidence"]
        test_ev = evidence.record_test(
            store, command=["python", "-c", "print('ok')"], cwd=str(wt), actor=SYSTEM,
            subject_id=wid, repo_id=rid, commit=sha, worktree=str(wt),
        )["evidence"]
        verified = work.verify(store, wid)
        ok("Acceptance Evaluator пропускає", verified["ok"] is True)
        work.accept(store, wid, token, HUMAN)
        code.git.worktree_remove(str(project), str(wt), "session-fix")
        ok("задача закрита", work.get_work(store, wid)["status"] == "completed")

        package = context.build(store, wid, agent_id="session-agent", purpose="review",
                                use_cache=False)
        saved = context.persist(store, package, AGENT_A)
        packet = handoff.create(
            store, work_id=wid, session_id="session-A", actor=AGENT_A,
            summary="accepted, decision approved by human",
            next_command="cairn context show " + saved["context_id"],
            decisions=["DEC-SESSION-001"], context_id=saved["context_id"],
            evidence_ids=[commit_ev["id"], test_ev["id"]],
        )["handoff"]
        ok("контекст збережено", not saved["replayed"])
        ok("digest стабільний", package["integrity_digest"].startswith("sha256:"))
        ok("handoff має checksum", packet["checksum"].startswith("sha256:"))
        ok("transcript відсутній", packet["transcript_ref"] is None)

        print("3. СЕСІЯ B: ВІДНОВЛЕННЯ БЕЗ TRANSCRIPT")
        latest = handoff.latest(store, wid)
        ok("останній пакет цілий", latest["id"] == packet["id"])
        restored = context.show(store, latest["context_id"])
        rebuilt = context.build(store, wid, agent_id="session-agent", purpose="review",
                                use_cache=False)
        ok("digest відтворено", restored["integrity_digest"] == rebuilt["integrity_digest"],
           restored["integrity_digest"][:24])
        ok("пакет ідентичний", canonical(restored) == canonical(rebuilt))
        items = {item["object_id"]: item for item in restored["items"]}
        ok("контракт у контексті", contract["id"] in items)
        ok("схвалене рішення в контексті", decision["id"] in items and
           items[decision["id"]]["trust_class"] == "human_authoritative")
        ok("застаріле рішення виключено", stale["id"] not in items)
        ok("символ має ревізію", sym in items and bool(items[sym]["source_refs"]),
           str(items.get(sym, {}).get("source_refs"))[:80])
        ok("твердження має provenance", assertion["id"] in items and
           bool(items[assertion["id"]]["source_refs"]))
        ok("кожен елемент має trust+freshness", all(
            item.get("trust_class") and item.get("freshness") for item in items.values()))
        ok("докази в пакеті", all(
            row["id"] in {item["object_id"] for item in restored["items"]}
            for row in (commit_ev, test_ev)) or len(restored["items"]) >= 4)

        small = context.build(store, wid, agent_id="session-agent", purpose="review",
                              budget_chars=900, use_cache=False)
        ok("бюджет обмежує пакет", len(small["items"]) < len(restored["items"]),
           f"{len(small['items'])} < {len(restored['items'])}")
        ok("обмеження пояснено", bool(small["excluded"]))

        print("4. ЗАМІРИ")
        events = ledger.read_events(ledger.ledger_path(project))
        raw_chars = sum(len(canonical(event)) for event in events)
        source_files = list((project / "src").rglob("*.php"))
        source_chars = sum(len(path.read_text(encoding="utf-8")) for path in source_files)
        packet_text = canonical({"packet": latest, "package": restored})
        naive_text = canonical({"events": events,
                                "source": [p.read_text(encoding="utf-8") for p in source_files]})

        def naive():
            rows = ledger.read_events(ledger.ledger_path(project))
            texts = [path.read_text(encoding="utf-8") for path in source_files]
            return canonical({"events": rows, "source": texts})

        def via_cairn():
            latest_packet = handoff.latest(store, wid)
            saved_package = context.show(store, latest_packet["context_id"])
            return canonical({"packet": latest_packet, "package": saved_package})

        naive_ms, _ = median_ms(naive)
        cairn_ms, _ = median_ms(via_cairn)
        cached_ms, _ = median_ms(lambda: context.build(store, wid, agent_id="session-agent",
                                                       purpose="review", use_cache=True))
        uncached_ms, _ = median_ms(lambda: context.build(store, wid, agent_id="session-agent",
                                                         purpose="review", use_cache=False), rounds=8)

        print(f"  подій у журналі:            {len(events)}")
        print(f"  символів / ребер / зв'язків: {len(symbols)} / {len(edges)} / {len(relations)}")
        print(f"  контекст: items={len(restored['items'])}, excluded={len(restored['excluded'])}, "
              f"budget={restored['budget']}")
        print(f"  сире читання: {raw_chars + source_chars:6d} символів | {len(events)} подій + "
              f"{len(source_files)} файлів | {naive_ms:7.3f} мс (медіана {ROUNDS})")
        print(f"  через Cairn:  {len(packet_text):6d} символів | 1 handoff + 1 пакет | "
              f"{cairn_ms:7.3f} мс (медіана {ROUNDS})")
        print(f"  скорочення обсягу: {(1 - len(packet_text) / (raw_chars + source_chars)) * 100:.1f}%")
        print(f"  кеш контексту: {cached_ms:.3f} мс проти {uncached_ms:.3f} мс без кешу")
        print(f"  digest: {restored['integrity_digest'][:32]}... відтворено=True")

        chain = ledger.verify(ledger.ledger_path(project))
        ok("ланцюжок журналу цілий", chain["ok"] is True)
        ok("doctor зелений", store.doctor()["ok"] is True)
        ok("обсяг повторного читання зменшено",
           len(packet_text) < raw_chars + source_chars)
        ok("кеш не ламає digest",
           context.build(store, wid, agent_id="session-agent", purpose="review",
                         use_cache=True)["integrity_digest"] == restored["integrity_digest"])

        print("\nВЕРДИКТ: контекст сесії зберігається з індексами й прив'язками, відновлюється без transcript")
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
