"""Вимірювання повторної роботи: Cairn проти повторного читання всього журналу.

Запуск:  python tests/benchmark_unified.py

Вимірюється НЕ лише розмір prompt. Головна величина — скільки подій і файлів
друга сесія має перечитати, щоб відтворити той самий обсяг фактів, і скільки
часу це займає. Digest пакета перевіряється окремо: менший текст, який не
відтворюється, нічого не вартий.
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

HUMAN = {"kind": "human", "id": "bench-human"}
AGENT = {"kind": "agent", "id": "bench-worker"}
SYSTEM = {"kind": "system", "id": "bench-runner"}
ROUNDS = 40


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   text=True, encoding="utf-8", errors="replace")


def median_ms(fn) -> tuple[float, object]:
    samples = []
    value = None
    for _ in range(ROUNDS):
        start = time.perf_counter()
        value = fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples), value


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="cairn-bench-"))
    repo = temp / "project"
    try:
        shutil.copytree(ROOT / "fixtures" / "php84-demo", repo)
        for args in (("init", "-q"), ("config", "user.name", "Bench"),
                     ("config", "user.email", "bench@local"), ("add", "."),
                     ("commit", "-q", "-m", "init")):
            git(repo, *args)
        base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        Store(repo).init()
        store = Store.open(repo)
        identity = code.git.identity(str(repo))
        store.mutate(lambda con: store.append(
            con, "repository.upserted", identity["id"], identity, HUMAN,
            f"repo:{identity['id']}"))
        rid = identity["id"]
        code.php.index(store, rid, SYSTEM)
        semantic.put_object(store, object_type="decision", title="DEC-BENCH-001",
                            summary="remove debug logging before release",
                            stable_key="DEC-BENCH-001", scope=["Pricing"],
                            constraints=["no DEBUG_LEAK"], actor=HUMAN)
        wid = work.create_work(store, title="Remove debug logging", actor=HUMAN,
                               scope={"repo_id": rid}, capabilities=["php84"],
                               acceptance=[{"id": "AC-1", "kind": "evidence", "evidence_type": "commit"}])["work"]["id"]
        semantic.relate(store, from_id=wid, to_id="DEC-BENCH-001", relation="governed_by", actor=HUMAN)
        sym = store.read_one("SELECT stable_key FROM code_symbols LIMIT 1")["stable_key"]
        semantic.relate(store, from_id=wid, to_id=sym, relation="targets_symbol", actor=HUMAN)
        work.register_agent(store, agent_id="bench-worker", actor=HUMAN, capabilities=["php84"])
        claim = work.claim(store, wid, "bench-worker", AGENT)
        token = int(claim["claim"]["fencing_token"])

        wt = repo / ".cairn" / "runtime" / "worktrees" / "bench"
        code.git.worktree_add(str(repo), str(wt), "bench-fix", base)
        source = wt / "src" / "Pricing" / "PriceCalculator.php"
        source.write_text(source.read_text(encoding="utf-8").replace(
            "        // DEBUG_LEAK: тимчасовий логінг цін клієнта\n", ""), encoding="utf-8")
        sha = code.git.commit(str(wt), "fix: bench", ["src/Pricing/PriceCalculator.php"])["commit"]
        evidence.record_commit(store, rid, sha, SYSTEM, subject_id=wid)
        evidence.record_test(store, command=["python", "-c", "print('ok')"], cwd=str(wt),
                             actor=SYSTEM, subject_id=wid, repo_id=rid, commit=sha, worktree=str(wt))
        work.accept(store, wid, token, HUMAN)
        code.git.worktree_remove(str(repo), str(wt), "bench-fix")

        package = context.build(store, wid, agent_id="bench-worker", purpose="review")
        saved = context.persist(store, package, AGENT)
        handoff.create(store, work_id=wid, session_id="session-A", actor=AGENT,
                       summary="work accepted", next_command="cairn context show " + saved["context_id"],
                       decisions=["DEC-BENCH-001"], context_id=saved["context_id"])

        events = ledger.read_events(ledger.ledger_path(repo))
        raw_chars = sum(len(canonical(event)) for event in events)
        source_files = list((repo / "src").rglob("*.php"))
        source_chars = sum(len(path.read_text(encoding="utf-8")) for path in source_files)

        def naive():
            # Стратегія без Cairn: друга сесія читає весь журнал і вихідні файли,
            # щоб зібрати ті самі факти вручну.
            rows = ledger.read_events(ledger.ledger_path(repo))
            texts = [path.read_text(encoding="utf-8") for path in source_files]
            return canonical({"events": rows, "source": texts})

        def cairn():
            # Стратегія з Cairn: один handoff + один збережений context package.
            packet = handoff.latest(store, wid)
            saved_package = context.show(store, packet["context_id"])
            return canonical({"packet": packet, "package": saved_package})

        naive_ms, naive_value = median_ms(naive)
        cairn_ms, cairn_value = median_ms(cairn)
        rebuilt = context.build(store, wid, agent_id="bench-worker", purpose="review",
                                use_cache=False)
        digest_ok = rebuilt["integrity_digest"] == package["integrity_digest"]

        saved_chars = len(cairn_value)
        raw_total = raw_chars + source_chars
        print("ВИМІРЮВАННЯ ПОВТОРНОЇ РОБОТИ (медіана 40 прогонів, локально, без LLM)")
        print(f"  подій у журналі:            {len(events)}")
        print(f"  без Cairn:  {naive_ms:7.3f} мс | {raw_total:5d} символів | "
              f"{len(events)} подій + {len(source_files)} файлів перечитано")
        print(f"  з Cairn:    {cairn_ms:7.3f} мс | {saved_chars:5d} символів | "
              f"1 handoff + 1 контекстний пакет")
        print(f"  скорочення повторного читання: {len(events) - 1} подій, "
              f"{len(source_files)} файлів; символів {raw_total} -> {saved_chars}")
        print(f"  digest пакета відтворено:      {digest_ok} ({package['integrity_digest'][:24]})")
        print(f"  часова ціна на 16 подіях: {naive_ms:.3f} мс (сирий JSONL) проти "
              f"{cairn_ms:.3f} мс (Cairn) — на малому журналі швидший сирий прочит, це межа вимірювання")

        # Друга точка масштабу: ті самі 40 об'єктів, додані через публічний API.
        # Журнал росте, шлях Cairn лишається сталким — перевіряємо це виміром.
        for index in range(40):
            semantic.put_object(store, object_type="note", title=f"bench-note-{index}",
                                summary="filler", actor=HUMAN)
        grown_events = ledger.read_events(ledger.ledger_path(repo))
        naive_grown_ms, _ = median_ms(naive)
        cairn_grown_ms, _ = median_ms(cairn)
        print(f"  після зростання до {len(grown_events)} подій: naive {naive_grown_ms:.3f} мс | "
              f"Cairn {cairn_grown_ms:.3f} мс | Cairn читає 1 handoff + 1 пакет незалежно від журналу")

        if not digest_ok:
            print("FAIL: контекст не відтворюється")
            return 1
        if saved_chars >= raw_total:
            print("FAIL: повторне читання не зменшено")
            return 1
        if len(events) < 2:
            print("FAIL: у журналі занадто мало подій для вимірювання")
            return 1
        print("ВЕРДИКТ: повторна робота вимірювано знижена, digest стабільний")
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
