"""MCP-контракт v3 §16.2: 11 інструментів, provenance, trust, compact results.

Запуск:  python tests/test_unified_mcp.py
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

from cairn_unified import code, mcp, semantic, workflow, work  # noqa: E402
from cairn_unified.errors import CairnError  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

HUMAN = {"kind": "human", "id": "alice"}
AGENT = {"kind": "agent", "id": "worker-a"}


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


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="cairn-mcp-"))
    repo = temp / "project"
    try:
        shutil.copytree(ROOT / "fixtures" / "php84-demo", repo)
        for args in (("init", "-q"), ("config", "user.name", "MCP"),
                     ("config", "user.email", "mcp@local"), ("add", "."),
                     ("commit", "-q", "-m", "init")):
            git(repo, *args)
        Store(repo).init()
        store = Store.open(repo)
        identity = code.git.identity(str(repo))
        store.mutate(lambda con: store.append(con, "repository.upserted", identity["id"],
                                              identity, HUMAN, f"repo:{identity['id']}"))
        rid = identity["id"]
        code.php.index(store, rid, {"kind": "system", "id": "indexer"})
        wid = work.create_work(store, title="MCP сценарій", actor=HUMAN,
                               scope={"repo_id": rid}, capabilities=["php84"])["work"]["id"]
        work.register_agent(store, agent_id="worker-a", actor=HUMAN, capabilities=["php84"])

        print("1. РЕЄСТР І КОНТРАКТ")
        catalog = mcp.tools_list()["tools"]
        ok("13 інструментів", len(catalog) == 13, str(len(catalog)))
        expected = ("cairn_context", "cairn_work_next", "cairn_work_show", "cairn_work_update",
                    "cairn_semantic_query", "cairn_code_explore", "cairn_decision_propose",
                    "cairn_evidence_attach", "cairn_checkpoint", "cairn_handoff", "cairn_status")
        # Одинадцять назв зі §16.2 мусять бути НА МІСЦІ й точними. Розширення
        # дозволене, але лише НАЗВАНЕ: інакше «збігається зі spec» означало б
        # дві різні речі — «spec виконано» і «нічого не додано».
        extensions = ("cairn_why", "cairn_change_note")
        names = tuple(t["name"] for t in catalog)
        ok("усі 11 назв зі spec на місці", set(expected) <= set(names),
           str(sorted(set(expected) - set(names))))
        ok("порядок назв зі spec збережено",
           tuple(n for n in names if n in expected) == expected)
        ok("розширення лише названі", set(names) - set(expected) == set(extensions),
           str(sorted(set(names) - set(expected) - set(extensions))))
        ok("schemas валідні й компактні", all(
            t["inputSchema"]["type"] == "object" and t.get("description")
            and set(t["inputSchema"].get("required", [])) <= set(t["inputSchema"]["properties"])
            and len(t["inputSchema"]["properties"]) <= 8 for t in catalog))

        print("2. АГЕНТСЬКА ПРОПОЗИЦІЯ НЕ Є РІШЕННЯМ")
        proposed = mcp.call_tool(store, "cairn_decision_propose", {
            "title": "DEC-MCP-001", "summary": "changing pricing logic",
            "stable_key": "DEC-MCP-001", "scope": ["Pricing"], "constraints": ["no debug log"],
        }, AGENT)
        data = proposed["data"]
        ok("статус proposed", data["lifecycle_state"] == "proposed" and data["approved"] is False)
        ok("довіра agent_proposed", data["trust_class"] == "agent_proposed")
        ok("підказка про людське схвалення", "human" in data["approval_hint"])
        ok("envelope має provenance", bool(proposed["envelope"]["event_ids"])
           and proposed["envelope"]["project_id"] and proposed["envelope"]["replica_id"])
        semantic.accept_object(store, object_id="DEC-MCP-001", actor=HUMAN, rationale="ok")
        semantic.relate(store, from_id=wid, to_id="DEC-MCP-001", relation="governed_by", actor=HUMAN)
        sym = store.read_one("SELECT stable_key FROM code_symbols LIMIT 1")["stable_key"]
        semantic.relate(store, from_id=wid, to_id=sym, relation="targets_symbol", actor=HUMAN)

        print("3. КОНТЕКСТ, ROBUST ПРОГRESIVE FETCH")
        packed = mcp.call_tool(store, "cairn_context", {
            "work_id": wid, "agent_id": "worker-a", "persist": True,
        }, AGENT)
        ok("digest стабільний", packed["data"]["integrity_digest"].startswith("sha256:"))
        ok("елементи мають preview, не повний текст",
           all("preview" in item and "text" not in item for item in packed["data"]["items"]))
        ok("є інструкція progressive fetch", bool(packed["data"]["progressive_fetch"]))
        ok("envelopeTrust", packed["envelope"]["trust_class"] == "human_authoritative")
        full = mcp.call_tool(store, "cairn_context", {"work_id": wid, "include_full": True}, AGENT)
        ok("повний пакет тільки за запитом", "full_package" in full["data"])

        print("4. WORK TOOLS")
        nxt = mcp.call_tool(store, "cairn_work_next", {"agent_id": "worker-a"}, AGENT)
        ok("задача в ready", any(item["work_id"] == wid for item in nxt["data"]["ready"]))
        shown = mcp.call_tool(store, "cairn_work_show", {"work_id": wid, "agent_id": "worker-a"}, AGENT)
        ok("show показує eligibility", shown["data"]["eligibility"]["verdict"] == "eligible")
        claim = mcp.call_tool(store, "cairn_work_next", {"agent_id": "worker-a", "claim": True}, AGENT)
        token = claim["data"]["ready"][0]["fencing_token"]
        ok("claim видав fencing token", token == 1, str(token))
        updated = mcp.call_tool(store, "cairn_work_update", {
            "work_id": wid, "status": "in_progress", "token": token,
        }, AGENT)
        ok("перехід стану дозволений", updated["data"]["status"] == "in_progress")
        commented = mcp.call_tool(store, "cairn_work_update", {
            "work_id": wid, "comment": "почав роботу",
        }, AGENT)
        ok("коментар має id", bool(commented["data"]["comment_id"]))

        print("5. SEMANTIC / CODE / STATUS")
        queried = mcp.call_tool(store, "cairn_semantic_query", {"object_type": "decision"}, AGENT)
        ok("рішення знайдено", any(o["id"] == "DEC-MCP-001" for o in queried["data"]["objects"]))
        hist = mcp.call_tool(store, "cairn_semantic_query", {
            "object_type": "decision", "as_of": "2000-01-01T00:00:00Z",
        }, AGENT)
        ok("історичний запит порожній", not hist["data"]["objects"])
        explored = mcp.call_tool(store, "cairn_code_explore", {"work_id": wid}, AGENT)
        ok("символ за work деталь", any(s["stable_key"] == sym for s in explored["data"]["symbols"]))
        by_symbol = mcp.call_tool(store, "cairn_code_explore", {"symbol": "calculateTotal"}, AGENT)
        ok("символ за ім'ям", any(s["stable_key"] == sym for s in by_symbol["data"]["symbols"]))
        status = mcp.call_tool(store, "cairn_status", {}, AGENT)
        ok("статус має лічильники", status["data"]["counts"]["work_items"] >= 1
           and status["data"]["last_event_seq"])

        print("6. EVIDENCE / CHECKPOINT / HANDOFF")
        wt = repo / ".cairn" / "runtime" / "worktrees" / "mcp"
        base = git(repo, "rev-parse", "HEAD")
        code.git.worktree_add(str(repo), str(wt), "mcp-fix", base)
        source = wt / "src" / "Pricing" / "PriceCalculator.php"
        source.write_text(source.read_text(encoding="utf-8").replace("// DEBUG_LEAK: тимчасовий логінг цін клієнта",
                                                                    "// cleaned"))
        sha = code.git.commit(str(wt), "fix: mcp", ["src/Pricing/PriceCalculator.php"])["commit"]
        attached = mcp.call_tool(store, "cairn_evidence_attach", {
            "kind": "commit", "repo_id": rid, "commit": sha, "subject_id": wid,
        }, AGENT)
        ok("commit evidence приєднано", attached["data"]["evidence_type"] == "commit")
        tested = mcp.call_tool(store, "cairn_evidence_attach", {
            "kind": "test", "command": ["python", "-c", "print('ok')"], "cwd": str(wt),
            "subject_id": wid, "repo_id": rid, "commit": sha, "worktree": str(wt),
        }, AGENT)
        ok("test evidence приєднано", tested["data"]["metadata"]["passed"] is True)
        definition = workflow.define(store, name="mcp", actor=HUMAN,
                                     definition={"steps": [{"key": "a", "kind": "mark", "label": "x"}]})["definition"]["id"]
        run = workflow.start(store, definition, AGENT)["run"]["id"]
        cp = mcp.call_tool(store, "cairn_checkpoint", {"run_id": run, "checkpoint": {"index": 1}}, AGENT)
        ok("checkpoint записано", not cp["data"]["replayed"])
        cp2 = mcp.call_tool(store, "cairn_checkpoint", {"run_id": run, "checkpoint": {"index": 1}}, AGENT)
        ok("checkpoint ідемпотентний", cp2["data"]["replayed"] and
           cp2["data"]["event_id"] == cp["data"]["event_id"])
        handed = mcp.call_tool(store, "cairn_handoff", {
            "work_id": wid, "session_id": "mcp-session", "summary": "progress",
            "next": "python tests/test_unified_vs01.py", "decisions": ["DEC-MCP-001"],
            "context_id": packed["data"]["context_id"],
            "evidence_ids": [attached["data"]["evidence_id"]],
        }, AGENT)
        ok("handoff має checksum", handed["data"]["checksum"].startswith("sha256:"))
        ok("transcript відсутній", handed["data"]["transcript_ref"] is None)

        print("7. JSON-RPC ПРОТОКОЛ")
        init = mcp.handle_request(store, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                          "params": {"protocolVersion": "cairn-test/1"}}, AGENT)
        ok("initialize повертає capabilities", init["result"]["capabilities"]["tools"]["listChanged"] is False)
        ok("протокол echoed", init["result"]["protocolVersion"] == "cairn-test/1")
        catalog_rpc = mcp.handle_request(store, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, AGENT)
        ok("tools/list через RPC віддає той самий каталог",
           len(catalog_rpc["result"]["tools"]) == len(catalog))
        rpc = mcp.handle_request(store, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                         "params": {"name": "cairn_status", "arguments": {}}}, AGENT)
        payload = json.loads(rpc["result"]["content"][0]["text"])
        envelope = payload["envelope"]
        ok("результат має envelope", envelope["schema"] == "cairn-provenance/1"
           and bool(envelope["project_id"]) and envelope["ledger_seq"] > 0
           and isinstance(envelope["event_ids"], list) and not rpc["result"]["isError"])
        bad = mcp.handle_request(store, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                         "params": {"name": "nope", "arguments": {}}}, AGENT)
        ok("невідомий інструмент — isError", bad["result"]["isError"] is True)
        notice = mcp.handle_request(store, {"jsonrpc": "2.0", "method": "notifications/initialized"}, AGENT)
        ok("нотифікація без відповіді", notice is None)
        try:
            mcp.call_tool(store, "cairn_status", {"unexpected": 1}, AGENT)
        except CairnError as exc:
            ok("невідомий аргумент відхилено", exc.code == "bad_argument", exc.code)
        else:
            ok("невідомий аргумент відхилено", False, "помилки не було")

        print("\nВЕРДИКТ: MCP-інструменти компактні, з provenance, без само-схвалення агента")
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
