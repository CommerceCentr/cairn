"""Hardening v3 §27: policies, contract, stop, concurrency, offline, redaction.

Запуск:  python tests/test_unified_hardening.py

Кожна перевірка має бути здатна впасти: наприклад, offline блокує socket,
а concurrency запускає два реальні потоки з barrier, а не перевіряє підпис.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import code, context, handoff, ledger, semantic, workflow, work  # noqa: E402
from cairn_unified.errors import CairnError  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

HUMAN = {"kind": "human", "id": "hard-human"}
AGENT = {"kind": "agent", "id": "capable"}
AGENT_B = {"kind": "agent", "id": "capable-b"}
SYSTEM = {"kind": "system", "id": "hard-runner"}
SECRET = "sk-abcdefghijklmnopqrstuvwx"


def ok(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        raise AssertionError(name)


def raises(name: str, expected: str, fn) -> None:
    try:
        fn()
    except CairnError as exc:
        ok(name, exc.code == expected, f"отримано {exc.code}")
        return
    ok(name, False, "омилка не виникла")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   text=True, encoding="utf-8", errors="replace")


def add_repo(store: Store, repo: Path) -> str:
    identity = code.git.identity(str(repo))
    store.mutate(lambda con: store.append(con, "repository.upserted", identity["id"],
                                          identity, HUMAN, f"repo:{identity['id']}"))
    return identity["id"]


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="cairn-hard-"))
    repo = temp / "project"
    try:
        shutil.copytree(ROOT / "fixtures" / "php84-demo", repo)
        for args in (("init", "-q"), ("config", "user.name", "Hard"),
                     ("config", "user.email", "hard@local"), ("add", "."),
                     ("commit", "-q", "-m", "init")):
            git(repo, *args)
        Store(repo).init()
        store = Store.open(repo)
        rid = add_repo(store, repo)
        code.php.index(store, rid, SYSTEM)
        sym = store.read_one("SELECT stable_key FROM code_symbols LIMIT 1")["stable_key"]

        wid = work.create_work(store, title="Contract-governed task", actor=HUMAN,
                               scope={"repo_id": rid}, capabilities=["php84"])["work"]["id"]
        work.register_agent(store, agent_id="capable", actor=HUMAN, capabilities=["php84"])
        work.register_agent(store, agent_id="capable-b", actor=HUMAN, capabilities=["php84"])

        print("1. CONTRACT У ГРАФІ ТА provenance КОЖНОГО ФАКТУ")
        contract = semantic.put_object(
            store, object_type="contract", title="AC-1", stable_key="CTR-AC-1",
            summary="коміт не містить DEBUG_LEAK", actor=HUMAN,
            scope=[sym], constraints=["source_absent:DEBUG_LEAK"],
        )["object"]
        ok("contract має стабільний ID", contract["id"] == "CTR-AC-1")
        semantic.relate(store, from_id=wid, to_id=contract["id"], relation="governed_by", actor=HUMAN)
        semantic.relate(store, from_id=wid, to_id=sym, relation="targets_symbol", actor=HUMAN)
        assertion = semantic.add_assertion(store, subject_id=sym, predicate="policy.no_debug_leak",
                                           literal=True, actor=HUMAN,
                                           trust="human_authoritative")["assertion"]
        package = context.build(store, wid, agent_id="capable", purpose="review")
        items = {item["object_id"]: item for item in package["items"]}
        ok("contract в активному контексті", contract["id"] in items and items[contract["id"]]["kind"] == "contract")
        ok("symbol має revision", sym in items and items[sym]["freshness"] in {"current", "stale"})
        ok("assertion має provenance", assertion["id"] in items and bool(items[assertion["id"]]["source_refs"]),
           str(items.get(assertion["id"], {}).get("source_refs")))
        ok("кожен важливий факт має trust і freshness", all(
            item.get("trust_class") and item.get("freshness")
            for oid, item in items.items() if oid in {contract["id"], sym, assertion["id"]}))

        print("2. ПОЛІТИКА БЛОКУЄ FRONTIER, АГЕНТ ЇЇ НЕ СТВОРЮЄ")
        blocked_wid = work.create_work(store, title="Policy-blocked", actor=HUMAN,
                                       capabilities=["php84"])["work"]["id"]
        semantic.add_assertion(store, subject_id=blocked_wid, predicate="policy.ready.denied",
                               literal=True, actor=HUMAN, trust="human_authoritative")
        decision = work.eligibility(store, blocked_wid, "capable")
        ok("політика блокує", decision["verdict"] == "blocked"
           and any(r["code"] == "policy_denied" for r in decision["reasons"]), decision["verdict"])
        ok("основна задача лишається готовою",
           work.eligibility(store, wid, "capable")["verdict"] == "eligible")
        agent_wid = work.create_work(store, title="Agent-policy rejected", actor=HUMAN,
                                     capabilities=["php84"])["work"]["id"]
        raises("агент не створює політику", "authority_required",
               lambda: semantic.add_assertion(store, subject_id=agent_wid,
                                              predicate="policy.ready.denied",
                                              literal=True, actor=AGENT))
        ok("агентська 'політика' не діє",
           work.eligibility(store, agent_wid, "capable")["verdict"] == "eligible")
        global_wid = work.create_work(store, title="Global policy", actor=HUMAN,
                                      capabilities=["special"])["work"]["id"]
        semantic.add_assertion(store, subject_id="*", predicate="policy.capability.special.denied",
                               literal=True, actor=HUMAN, trust="human_authoritative")
        ok("глобальна політика за capability",
           work.eligibility(store, global_wid, "capable")["verdict"] == "not_capable")

        print("3. WORKFLOW STOP ЗБЕРІГАЄ ДУРАБЕЛЬНІ ПОДІЇ")
        definition = workflow.define(store, name="hard", actor=HUMAN, definition={"steps": [
            {"key": "one", "kind": "mark", "label": "one"},
            {"key": "two", "kind": "mark", "label": "two"},
        ]})["definition"]["id"]
        run = workflow.start(store, definition, AGENT)["run"]["id"]
        workflow.advance(store, run, AGENT)
        workflow.pause(store, run, AGENT)
        workflow.resume(store, run, AGENT)
        stopped = workflow.stop(store, run, AGENT)
        ok("stop перевів у stopped", stopped["run"]["state"] == "stopped")
        raises("після stop крок заборонений", "run_not_running",
               lambda: workflow.advance(store, run, AGENT))
        store.rebuild()
        ok("стан stopped пережив rebuild", workflow.get(store, run)["state"] == "stopped")
        stop_events = [event for event in ledger.read_events(ledger.ledger_path(repo))
                       if event["event_type"] == "workflow_run.upserted"
                       and event["payload"].get("state") == "stopped"]
        ok("подія stop у журналі", len(stop_events) == 1, str(len(stop_events)))

        print("4. РЕАЛЬНА КОНКУРЕНЦІЯ ЗА CLAIM ТА TTL")
        race_wid = work.create_work(store, title="Race", actor=HUMAN, capabilities=["php84"])["work"]["id"]
        barrier = threading.Barrier(2)
        outcomes: list[tuple[str, str | None]] = []

        def attempt(agent: dict):
            barrier.wait()
            try:
                result = work.claim(store, race_wid, agent["id"], agent)
                return ("ok", str(result["claim"]["fencing_token"]))
            except CairnError as exc:
                return (exc.code, None)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, [AGENT, AGENT_B]))
        ok("рівно один claim виграв", sum(1 for code, _ in outcomes if code == "ok") == 1,
           str(outcomes))
        ok("другий бачить конфлікт", any(code == "conflicted" for code, _ in outcomes), str(outcomes))
        ttl_wid = work.create_work(store, title="Expired lease", actor=HUMAN,
                                   capabilities=["php84"])["work"]["id"]
        expired = work.claim(store, ttl_wid, "capable", AGENT, ttl_sec=0)
        token = int(expired["claim"]["fencing_token"])
        ok("lease одразу прострочено", expired["claim"]["expires_at"] <= store.now())
        raises("прострочений токен блокує приймання", "stale_fence",
               lambda: work.accept(store, ttl_wid, token, HUMAN))

        print("5. OFFLINE: СОКЕТИ ЗАБЛОКОВАНО")
        original_socket = socket.socket
        original_connect = socket.create_connection

        def deny(*args, **kwargs):
            raise AssertionError("мережевий виклик заборонений у local-only режимі")

        socket.socket = deny  # type: ignore[assignment]
        socket.create_connection = deny  # type: ignore[assignment]
        try:
            offline_package = context.build(store, wid, agent_id="capable", purpose="review",
                                            use_cache=False)
            semantic.query(store, object_type="decision")
            chain = ledger.verify(ledger.ledger_path(repo))
        finally:
            socket.socket = original_socket  # type: ignore[assignment]
            socket.create_connection = original_connect  # type: ignore[assignment]
        ok("контекст побудовано без мережі", bool(offline_package["items"]))
        ok("ланцюжок перевірено без мережі", chain["ok"] is True)

        print("6. СЕКРЕТИ НЕ ПОТРАПЛЯЮТЬ У ЖУРНАЛ")
        semantic.put_object(store, object_type="note", title="secret probe",
                            summary=f"ключ {SECRET} не має лишитися в journal", actor=HUMAN)
        events = ledger.read_events(ledger.ledger_path(repo))
        raw = "\n".join(str(event) for event in events)
        ok("сирий ключ відсутній у журналі", SECRET not in raw)
        ok("redaction позначено", "REDACTED:OPENAI_KEY" in raw)
        ok("doctor не знаходить секретів",
           next(check for check in store.doctor()["checks"] if check["name"] == "secrets")["ok"] is True)

        print("\nВЕРДИКТ: hardening-перевірки пройдені — policies, contract, stop, race, offline, redaction")
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
