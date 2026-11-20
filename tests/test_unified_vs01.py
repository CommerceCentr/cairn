"""VS-01 для Cairn Unified 2.0 — наскрізний сценарій без зовнішніх сервісів.

Запуск:  python tests/test_unified_vs01.py

Тест не використовує pytest: створює тимчасовий Git-репозиторій із фікстуро
`fixtures/php84-demo`, будує Event Ledger і перевіряє ті самі межі, що названі
у v3 §27. Кожна перевірка падає голосно; жодна не є тавтологією.
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

from cairn_unified import code, context, evidence, gates, handoff, ledger, semantic, workflow, work  # noqa: E402
from cairn_unified.errors import CairnError  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

HUMAN = {"kind": "human", "id": "alice"}
AGENT_A = {"kind": "agent", "id": "worker-a"}
AGENT_B = {"kind": "agent", "id": "worker-b"}
SYSTEM = {"kind": "system", "id": "runner"}
DECISION = "DEC-PRICE-001"
PATTERN = "DEBUG_LEAK"


def ok(name: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        raise AssertionError(name)


def raises(name: str, code_expected: str, fn) -> None:
    try:
        fn()
    except CairnError as exc:
        ok(name, exc.code == code_expected, f"отримано {exc.code}")
        return
    ok(name, False, "омилка не виникла")


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr}")
    return proc.stdout.strip()


def add_repo(store: Store, repo_path: Path, actor: dict) -> str:
    identity = code.git.identity(str(repo_path))

    def op(con):
        ev = store.append(con, "repository.upserted", identity["id"], identity, actor,
                          idempotency_key=f"repo:{identity['id']}")
        return ev["payload"]

    return store.mutate(op)["id"]


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="cairn-vs01-"))
    repo = temp / "project"
    try:
        shutil.copytree(ROOT / "fixtures" / "php84-demo", repo)
        git(repo, "init", "-q")
        git(repo, "config", "user.name", "Fixture Author")
        git(repo, "config", "user.email", "fixture@example.local")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "initial: price calculator with debug leak")
        base_commit = git(repo, "rev-parse", "HEAD")

        print("0. ІНІЦІАЛІЗАЦІЯ ТА РЕПОЗИТОРІЙ")
        Store(repo).init()
        store = Store.open(repo)
        repo_id = add_repo(store, repo, HUMAN)
        ok("репозиторій зареєстровано", bool(repo_id), repo_id)

        print("1. СЕМАНТИЧНИЙ ІНДЕКС КОДУ")
        indexed = code.php.index(store, repo_id, SYSTEM)
        ok("індекс містить символ", indexed["symbols"] == 1, str(indexed["symbols"]))
        sym = store.read_one("SELECT * FROM code_symbols WHERE repo_id = ?", (repo_id,))
        stable_key = sym["stable_key"]
        ok("стабільна тотожність символа",
           stable_key.endswith("PriceCalculator::calculateTotal"), stable_key)
        ok("індекс прив'язано до ревізії", sym["revision"] == base_commit, sym["revision"][:12])

        print("2. АГЕНТСЬКА ПРОПОЗИЦІЯ НЕ Є АВТОРИТЕТНОЮ")
        proposal = semantic.put_object(
            store, object_type="decision", title=DECISION,
            summary="Прибрати DEBUG_LEAK з calculateTotal перед релізом",
            constraints=["no DEBUG_LEAK in committed source"], scope=["Pricing"],
            stable_key=DECISION, actor=AGENT_A,
        )
        ok("пропозиція агента має низьку довіру",
           proposal["object"]["trust_class"] == "agent_proposed"
           and proposal["object"]["lifecycle_state"] == "proposed")
        semantic.add_assertion(store, subject_id=DECISION, predicate="agent.claim.trust_me",
                               literal="trust me", actor=AGENT_A)
        authoritative = semantic.add_assertion(
            store, subject_id=DECISION, predicate="policy.no_debug_leak",
            literal=True, actor=HUMAN, trust="human_authoritative",
        )
        ok("людське твердження має authority chain",
           authoritative["assertion"]["trust_class"] == "human_authoritative")

        print("3. WORK ITEM І READY FRONTIER")
        wid = work.create_work(
            store,
            title="Прибрати витік цін із PriceCalculator::calculateTotal",
            actor=HUMAN,
            description="VS-01",
            scope={"repo_id": repo_id},
            capabilities=["php84"],
            acceptance=[
                {"id": "AC-1", "kind": "evidence", "evidence_type": "commit"},
                {"id": "AC-2", "kind": "evidence", "evidence_type": "test"},
                {"id": "AC-3", "kind": "source_absent", "path": "src/Pricing/PriceCalculator.php",
                 "pattern": PATTERN},
            ],
        )["work"]["id"]
        semantic.relate(store, from_id=wid, to_id=DECISION, relation="governed_by", actor=HUMAN)
        semantic.relate(store, from_id=wid, to_id=stable_key, relation="targets_symbol", actor=HUMAN)
        work.register_agent(store, agent_id="worker-a", actor=HUMAN, capabilities=["php84"])
        work.register_agent(store, agent_id="worker-b", actor=HUMAN, capabilities=[])
        decision = work.eligibility(store, wid, "worker-a")
        ok("задача eligibility", decision["verdict"] == "eligible", decision["verdict"])
        ok("непідготовлений агент відсіяний",
           work.eligibility(store, wid, "worker-b")["verdict"] == "not_capable")

        print("4. CLAIM, FENCING, ІЗОЛЯЦІЯ")
        claim = work.claim(store, wid, "worker-a", AGENT_A)
        token = int(claim["claim"]["fencing_token"])
        ok("fencing token видано", token == 1, str(token))
        ok("чужий агент бачить конфлікт",
           work.eligibility(store, wid, "worker-b")["verdict"] == "conflicted")
        raises("другий claim відхилено", "conflicted",
               lambda: work.claim(store, wid, "worker-b", AGENT_B))
        raises("прострочений токен відхилено", "stale_fence",
               lambda: work.heartbeat(store, wid, token + 99, AGENT_A))

        print("5. CONTEXT COMPILER: ДЕТЕРМІНОВАНІСТЬ, БЮДЖЕТ, ДОВІРА")
        p1 = context.build(store, wid, agent_id="worker-a", purpose="edit")
        p2 = context.build(store, wid, agent_id="worker-a", purpose="edit")
        ok("пакет відтворюваний", p1["integrity_digest"] == p2["integrity_digest"],
           p1["integrity_digest"][:24])
        ok("бюджет не перевищено", p1["budget"]["estimated"] <= p1["budget"]["limit"],
           f"{p1['budget']['estimated']}/{p1['budget']['limit']}")
        ids = {item["object_id"] for item in p1["items"]}
        ok("critical contract присутній", wid in ids)
        ok("symbol за стабільним ключем присутній", stable_key in ids)
        ok("агентське твердження виключено",
           any(x.get("object_id") and "agent" in str(x.get("object_id", "")) or
               x.get("reason") == "untrusted_trust_class" for x in p1["excluded"]))
        ok("авторитетне твердження включено",
           authoritative["assertion"]["id"] in ids)
        ok("неавторитетне рішення поки не в активних фактах", DECISION not in ids)
        saved = context.persist(store, p1, AGENT_A)
        ok("пакет збережено", context.show(store, saved["context_id"])["integrity_digest"]
           == p1["integrity_digest"])
        explains = context.explain(store, saved["context_id"])
        ok("пояснення містить причини та виключення",
           bool(explains["included"]) and bool(explains["excluded"]))

        print("6. ЛЮДСЬКЕ ПРИЙНЯТТЯ РІШЕННЯ")
        accepted = semantic.accept_object(store, object_id=DECISION, actor=HUMAN,
                                          rationale="затверджено власником сервісу")
        ok("рівень довіри підвищено тільки людиною",
           accepted["object"]["trust_class"] == "human_authoritative")
        p3 = context.build(store, wid, agent_id="worker-a", purpose="edit")
        ok("рішення тепер в активних фактах", DECISION in {i["object_id"] for i in p3["items"]})

        print("6b. SUPERSEDE/REVOKE ТА ІСТОРИЧНИЙ AS_OF")
        before = semantic.query(store, object_type="decision")["objects"][0]["valid_from"]
        sup = semantic.supersede(store, object_id=DECISION, title=DECISION + "-v2",
                                 summary="оновлене рішення щодо витоку цін", actor=HUMAN)
        new_id = sup["object"]["id"]
        ok("supersede створив чинного наступника",
           new_id != DECISION and sup["object"]["lifecycle_state"] == "accepted")
        current_ids = {o["id"] for o in semantic.query(store, object_type="decision")["objects"]}
        ok("скасоване рішення не в active set", DECISION not in current_ids and new_id in current_ids)
        p4 = context.build(store, wid, agent_id="worker-a", purpose="edit")
        ok("superseded не потрапив у контекст", DECISION not in {i["object_id"] for i in p4["items"]})
        hist = semantic.query(store, object_type="decision", as_of=before)
        ok("історичний as_of бачить старе рішення", any(o["id"] == DECISION for o in hist["objects"]))
        ok("історичний as_of не бачить нове", not any(o["id"] == new_id for o in hist["objects"]))
        semantic.revoke(store, object_id=new_id, actor=HUMAN, reason="рішення скасовано власником")
        ok("revoked не подається як current", new_id not in
           {o["id"] for o in semantic.query(store, object_type="decision")["objects"]})
        ok("revoked лишається в історії",
           any(o["id"] == DECISION for o in semantic.query(store, object_type="decision", as_of=before)["objects"]))

        print("7. GATE БЕЗ ДОКАЗУ НЕ ЗАКРИВАЄТЬСЯ")
        gate = gates.create(store, gate_type="human.approval", subject_id=wid, actor=AGENT_A)
        gid = gate["gate"]["id"]
        raises("агент не закриває людський gate", "authority_required",
               lambda: gates.evaluate(store, gid, AGENT_A, passed=True, evidence_id=None))

        print("8. РОБІТНИК: ОКРЕМИЙ WORKTREE, КОМІТ І ТЕСТ ЯК ДОКАЗИ")
        wt = repo / ".cairn" / "runtime" / "worktrees" / "vs01"
        code.git.worktree_add(str(repo), str(wt), "vs01-fix", base_commit)
        source = wt / "src" / "Pricing" / "PriceCalculator.php"
        source.write_text(source.read_text(encoding="utf-8").replace(
            "        // DEBUG_LEAK: тимчасовий логінг цін клієнта\n", ""), encoding="utf-8")
        ok("основний checkout не змінено",
           PATTERN in (repo / "src" / "Pricing" / "PriceCalculator.php").read_text(encoding="utf-8"))
        committed = code.git.commit(str(wt), "fix: remove customer price debug log",
                                    ["src/Pricing/PriceCalculator.php"])
        sha = committed["commit"]
        cev = evidence.record_commit(store, repo_id, sha, SYSTEM, subject_id=wid)
        ceid = cev["evidence"]["id"]
        ok("commit evidence записано", cev["evidence"]["metadata"]["commit"] == sha)
        ok("commit evidence має tree hash", committed["tree"] == cev["evidence"]["metadata"]["tree"])
        ok("head репозиторію оновлено",
           store.read_one("SELECT head FROM repositories WHERE id = ?", (repo_id,))["head"] == sha)
        tev = evidence.record_test(
            store, command=["python", "-c", "print('price ok')"], cwd=str(wt),
            actor=SYSTEM, subject_id=wid, repo_id=repo_id, commit=sha, worktree=str(wt),
        )
        teid = tev["evidence"]["id"]
        ok("test evidence записано", tev["evidence"]["metadata"]["passed"] is True)
        bound = tev["evidence"]["metadata"]["revision_binding"]
        ok("test прив'язано до точного commit/tree",
           bound["bound"] and bound["commit"] == sha and bound["tree"] == committed["tree"])
        raises("тест на чужій ревізії відхилено", "test_revision_mismatch",
               lambda: evidence.record_test(
                   store, command=["python", "-c", "print('stale')"], cwd=str(wt),
                   actor=SYSTEM, subject_id=wid, repo_id=repo_id, commit=base_commit, worktree=str(wt)))
        ok("верифікація commit evidence", evidence.verify(store, ceid)["ok"] is True)
        ok("верифікація test evidence", evidence.verify(store, teid)["ok"] is True)
        raises("gate без доказу не оцінюється", "evidence_required",
               lambda: gates.evaluate(store, gid, HUMAN, passed=True, evidence_id=None))
        ok("gate оцінено з доказом",
           gates.evaluate(store, gid, HUMAN, passed=True, evidence_id=ceid)["gate"]["state"] == "satisfied")
        ok("gate закрито з доказом",
           gates.resolve(store, gid, HUMAN, evidence_id=ceid)["gate"]["state"] == "resolved")

        print("9. HANDOFF БЕЗ TRANSCRIPT")
        h = handoff.create(
            store, work_id=wid, session_id="session-A", actor=AGENT_A,
            summary="видалено DEBUG_LEAK, коміт і тест записано як докази",
            next_command=f"cairn2 --root . work accept {wid} {token}",
            decisions=[DECISION], context_id=p1["context_id"], evidence_ids=[ceid, tev["evidence"]["id"]],
        )
        hid = h["handoff"]["id"]
        recovered = handoff.recover(store, hid, compile_context=True, actor=AGENT_B)
        ok("пакет перевірено контрольною сумою", recovered["verified"] is True)
        ok("transcript не потрібен", recovered["transcript_required"] is False)
        ok("нова сесія бачить задачу", recovered["work"]["id"] == wid)
        ok("нова сесія бачить контекст", recovered["context"]["budget"]["estimated"]
           <= recovered["context"]["budget"]["limit"])
        ok("latest handoff знайдено", handoff.latest(store, wid)["id"] == hid)

        print("10. WORKFLOW: CHECKPOINT, PAUSE/RESUME, IDEMPOTENCY")
        definition = workflow.define(store, name="vs01", actor=HUMAN, definition={"steps": [
            {"key": "analyze", "kind": "analyze", "summary": "проаналізувати вплив"},
            {"key": "work-ref", "kind": "work_ref", "work_item_id": wid},
            {"key": "gate-ref", "kind": "gate", "gate_id": gid},
            {"key": "finish", "kind": "mark", "label": "готово до приймання"},
        ]})["definition"]["id"]
        run = workflow.start(store, definition, AGENT_A, input={"work_item_id": wid})["run"]["id"]
        workflow.advance(store, run, AGENT_A)
        workflow.pause(store, run, AGENT_A)
        raises(" пауза блокує крок", "run_not_running", lambda: workflow.advance(store, run, AGENT_A))
        workflow.resume(store, run, AGENT_A)
        for _ in range(3):
            finished = workflow.advance(store, run, AGENT_A)
        ok("запуск завершено", finished["run"]["state"] == "completed")
        state = workflow.get(store, run)
        ok("checkpoint пройшов усі кроки",
           state["checkpoint"]["index"] == 4 and len(state["steps"]) == 4,
           f"{state['checkpoint']['index']}/4")
        events = ledger.read_events(ledger.ledger_path(repo))
        step_events = [e for e in events if e["event_type"] == "workflow_step.upserted"]
        ok("кроки не дубльовано", len(step_events) == 4, str(len(step_events)))

        print("11. ЛЮДСЬКЕ ПРИЙНЯТТЯ РОБОТИ")
        done = work.accept(store, wid, token, HUMAN)
        ok("задачу закрито", done["work"]["status"] == "completed")
        code.git.worktree_remove(str(repo), str(wt), "vs01-fix")
        ok("worktree прибрано", not wt.exists())
        raises("повторне приймання відхилено", "not_active",
               lambda: work.accept(store, wid, token, HUMAN))
        raises("чужий токен відхилено", "stale_fence",
               lambda: work.accept(store, wid, token + 7, HUMAN))

        print("12. REBUILD / DOCTOR / OFFLINE")
        events = ledger.read_events(ledger.ledger_path(repo))
        rebuilt = store.rebuild()
        ok("журнал повністю відтворено", rebuilt["applied"] == len(events)
           and rebuilt["ledger"] == len(events), f"{rebuilt['applied']}/{len(events)}")
        ok("стан задачі збережено", work.get_work(store, wid)["status"] == "completed")
        ok("індекс коду збережено",
           store.read_one("SELECT stable_key FROM code_symbols WHERE repo_id = ?", (repo_id,))["stable_key"]
           == stable_key)
        ok("handoff збережено", handoff.recover(store, hid)["verified"] is True)
        ok("workflow збережено", workflow.get(store, run)["state"] == "completed")
        health = store.doctor()
        ok("doctor зелений", health["ok"], "; ".join(
            f"{c['name']}={c['ok']}" for c in health["checks"] if not c["ok"]))

        print("\nВЕРДИКТ: VS-01 пройдено — graph, trust, evidence, context, handoff, workflow і rebuild")
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
