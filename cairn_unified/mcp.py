"""MCP-інтерфейс для агентів: 11 компактних інструментів за v3 §16.2.

 Правила контракту:
 - кожен інструмент повертає provenance + trust envelope разом із даними;
 - великі об'єкти повертаються посиланнями, повний пакет — явним запитом;
 - актор походить із конфігурації хоста, а НЕ з аргументів виклику:
   агент не може сам надати собі human_authoritative;
 - жодного прихованого стану між викликами — все читається з Event Ledger.
"""

from __future__ import annotations

import json
import sys

from . import change_notes, context, evidence, gates as gates_module, handoff, semantic, workflow, work
from .canon import canonical
from .errors import CairnError
from .store import parse_actor

DEFAULT_ACTOR = {"kind": "agent", "id": "mcp-client"}


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


TOOLS = [
    {"name": "cairn_change_note",
     "description": "Записати короткий намір і мотив після правки; коміт підтвердить її пізніше.",
     "inputSchema": _schema({
         "repo_path": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}},
         "why": {"type": "string"}, "intent": {"type": "string"},
         "session_id": {"type": "string"},
         "codex_event_id": {"type": "string"},
     }, ["repo_path", "paths", "why", "intent"])},
    {"name": "cairn_context", "description": "Зібрати обмежений контекст для задачі.",
     "inputSchema": _schema({
         "work_id": {"type": "string"}, "agent_id": {"type": "string"},
         "purpose": {"type": "string"}, "budget": {"type": "integer"},
         "as_of": {"type": "string"}, "persist": {"type": "boolean"},
         "include_full": {"type": "boolean"},
     }, ["work_id"])},
    {"name": "cairn_work_next", "description": "Список готових задач; claim — окремим прапорцем.",
     "inputSchema": _schema({
         "agent_id": {"type": "string"}, "claim": {"type": "boolean"},
         "limit": {"type": "integer"},
     }, [])},
    {"name": "cairn_work_show", "description": "Задача, блокувальники та пояснення eligibility.",
     "inputSchema": _schema({"work_id": {"type": "string"}, "agent_id": {"type": "string"}}, ["work_id"])},
    {"name": "cairn_work_update", "description": "Дозволений перехід стану або коментар. Не приймає роботу.",
     "inputSchema": _schema({
         "work_id": {"type": "string"}, "status": {"type": "string"},
         "token": {"type": "integer"}, "comment": {"type": "string"},
     }, ["work_id"])},
    {"name": "cairn_semantic_query", "description": "Чинні об'єкти й твердження; as_of для історії.",
     "inputSchema": _schema({
         "text": {"type": "string"}, "object_type": {"type": "string"},
         "subject_id": {"type": "string"}, "predicate": {"type": "string"},
         "as_of": {"type": "string"}, "limit": {"type": "integer"},
     }, [])},
    {"name": "cairn_code_explore", "description": "Символи за stable identity та ревізією, зв'язки коду.",
     "inputSchema": _schema({
         "symbol": {"type": "string"}, "work_id": {"type": "string"},
         "repo_id": {"type": "string"},
     }, [])},
    {"name": "cairn_why",
     "description": "Коли і чому редагувався вузол: причинна хронологія, походження, довіра, межі.",
     "inputSchema": _schema({
         "path": {"type": "string"}, "repo_id": {"type": "string"},
         "address": {"type": "string"}, "line": {"type": "integer"},
         "commit": {"type": "string"}, "as_of": {"type": "string"},
         "budget": {"type": "integer"}, "intent_text": {"type": "boolean"},
     }, ["path", "repo_id"])},
    {"name": "cairn_decision_propose", "description": "Пропонує рішення; ніколи його не затверджує.",
     "inputSchema": _schema({
         "title": {"type": "string"}, "summary": {"type": "string"},
         "stable_key": {"type": "string"}, "scope": {"type": "array"},
         "constraints": {"type": "array"},
     }, ["title", "summary"])},
    {"name": "cairn_evidence_attach", "description": "Приєднати commit/test доказ із перевіркою ревізії.",
     "inputSchema": _schema({
         "kind": {"type": "string", "enum": ["commit", "test"]},
         "repo_id": {"type": "string"}, "commit": {"type": "string"},
         "subject_id": {"type": "string"}, "worktree": {"type": "string"},
         "command": {"type": "array"}, "cwd": {"type": "string"},
     }, ["kind"])},
    {"name": "cairn_checkpoint", "description": "Зберегти ідемпотентний checkpoint запуску.",
     "inputSchema": _schema({
         "run_id": {"type": "string"}, "checkpoint": {"type": "object"},
     }, ["run_id", "checkpoint"])},
    {"name": "cairn_handoff", "description": "Створити контрольований пакет передачі сесії.",
     "inputSchema": _schema({
         "work_id": {"type": "string"}, "session_id": {"type": "string"},
         "summary": {"type": "string"}, "next": {"type": "string"},
         "decisions": {"type": "array"}, "context_id": {"type": "string"},
         "evidence_ids": {"type": "array"},
     }, ["work_id", "session_id", "summary", "next"])},
    {"name": "cairn_status", "description": "Компактний зріз стану проєкту.",
     "inputSchema": _schema({}, [])},
]

TOOL_NAMES = frozenset(item["name"] for item in TOOLS)


def tools_list() -> dict:
    return {"tools": [dict(item) for item in TOOLS]}


def _envelope(store, *, subjects=(), trust="source_observed", refs=(), note=None) -> dict:
    manifest = store.manifest()
    event_ids: list[str] = []
    if subjects:
        marks = ",".join("?" for _ in subjects)
        rows = store.read(
            f"SELECT event_id FROM events WHERE aggregate_id IN ({marks}) ORDER BY seq DESC LIMIT 8",
            tuple(subjects),
        )
        event_ids = [row["event_id"] for row in rows]
    tail = store.read_one("SELECT seq, event_id FROM events ORDER BY seq DESC LIMIT 1") or {}
    return {
        "schema": "cairn-provenance/1",
        "schema_version": "2.0",
        "project_id": manifest["project_id"],
        "replica_id": manifest["replica_id"],
        "source": "event-ledger",
        "ledger_seq": tail.get("seq"),
        "event_ids": event_ids,
        "trust_class": trust,
        "refs": list(refs),
        "note": note,
    }


def _result(store, data: dict, *, subjects=(), trust="source_observed", refs=(), note=None) -> dict:
    return {
        "tool_result": "cairn/1",
        "envelope": _envelope(store, subjects=subjects, trust=trust, refs=refs, note=note),
        "data": data,
    }


def _limit(args: dict, default: int = 20) -> int:
    value = int(args.get("limit", default))
    if value < 1 or value > 200:
        raise CairnError("bad_argument", "limit має бути від 1 до 200", exit_code=2)
    return value


def call_tool(store, name: str, arguments: dict | None, actor: dict | None = None) -> dict:
    """Чистий виклик інструмента. Актор фиксується хостом, не аргументами."""
    if name not in TOOL_NAMES:
        raise CairnError("unknown_tool", f"інструмент {name} не зареєстровано", exit_code=2)
    args = dict(arguments or {})
    actor = dict(actor or DEFAULT_ACTOR)
    known = next(item["inputSchema"]["properties"] for item in TOOLS if item["name"] == name)
    extra = sorted(set(args) - set(known))
    if extra:
        raise CairnError("bad_argument", f"невідомі аргументи: {', '.join(extra)}", exit_code=2)

    if name == "cairn_change_note":
        result = change_notes.note(store, args["repo_path"], args["paths"],
                                   args["why"], args["intent"], actor,
                                   args.get("session_id", ""),
                                   args.get("codex_event_id", ""))
        return _result(store, result, trust="agent_proposed",
                       refs=[result["note_id"]],
                       note="кандидат; зв'язок із комітом ще не доведено")

    if name == "cairn_context":
        package = context.build(
            store, args["work_id"], agent_id=args.get("agent_id", actor.get("id")),
            purpose=args.get("purpose", "edit"), budget_chars=args.get("budget"),
            as_of=args.get("as_of"), explain=True,
        )
        if args.get("persist"):
            context.persist(store, package, actor)
        items = [{
            "object_id": item["object_id"], "kind": item["kind"],
            "trust_class": item["trust_class"], "freshness": item["freshness"],
            "preview": item["text"][:160],
        } for item in package["items"]]
        data = {
            "context_id": package["context_id"], "integrity_digest": package["integrity_digest"],
            "target_revision": package["target_revision"], "budget": package["budget"],
            "items": items, "excluded": package["excluded"],
            "progressive_fetch": f"cairn context show {package['context_id']}",
        }
        if args.get("include_full"):
            data["full_package"] = package
        return _result(store, data, subjects=[args["work_id"]], trust="human_authoritative",
                       refs=[package["integrity_digest"]], note="контекст побудовано з ledger")

    if name == "cairn_work_next":
        items = []
        for item in work.ready(store, args.get("agent_id"))[:_limit(args)]:
            items.append({"work_id": item["work"]["id"], "title": item["work"]["title"],
                          "status": item["work"]["status"],
                          "reasons": item["eligibility"]["reasons"]})
        if args.get("claim"):
            if not args.get("agent_id") or len(items) != 1:
                raise CairnError("ambiguous_claim", "для claim потрібні agent_id і рівно одна кандидатура",
                                 exit_code=2)
            claimed = work.claim(store, items[0]["work_id"], args["agent_id"], actor)
            items[0]["fencing_token"] = claimed["claim"]["fencing_token"]
        return _result(store, {"ready": items}, trust="verified_system", note="ready frontier")

    if name == "cairn_work_show":
        task = work.get_work(store, args["work_id"])
        decision = work.eligibility(store, args["work_id"], args.get("agent_id"))
        blockers = store.read(
            "SELECT from_id, dependency_type FROM work_dependencies WHERE to_id = ?", (args["work_id"],)
        )
        gates = gates_module.list_gates(store, args["work_id"])
        return _result(store, {
            "work": {k: task[k] for k in ("id", "title", "status", "priority", "acceptance", "scope")},
            "eligibility": decision, "blockers": blockers, "gates": gates,
        }, subjects=[args["work_id"]], trust="verified_system")

    if name == "cairn_work_update":
        if args.get("comment") and not args.get("status"):
            result = work.comment(store, args["work_id"], args["comment"], actor)
            return _result(store, {"comment_id": result["object"]["id"]},
                           subjects=[args["work_id"]], trust=result["object"]["trust_class"])
        if not args.get("status"):
            raise CairnError("bad_argument", "потрібен status або comment", exit_code=2)
        result = work.transition(store, args["work_id"], args["status"], actor,
                                 fencing_token=args.get("token"))
        return _result(store, {"status": result["work"]["status"]},
                       subjects=[args["work_id"]], trust="agent_proposed" if actor["kind"] == "agent"
                       else "human_authoritative")

    if name == "cairn_semantic_query":
        found = semantic.query(
            store, text=args.get("text"), object_type=args.get("object_type"),
            subject_id=args.get("subject_id"), predicate=args.get("predicate"),
            as_of=args.get("as_of"),
        )
        limit = _limit(args)
        data = {
            "objects": [{"id": row["id"], "type": row["object_type"],
                         "trust_class": row["trust_class"], "freshness": row.get("freshness")}
                        for row in found["objects"][:limit]],
            "assertions": [{"id": row["id"], "predicate": row["predicate"],
                            "trust_class": row["trust_class"], "freshness": row.get("freshness")}
                           for row in found["assertions"][:limit]],
            "as_of": found["as_of"],
            "progressive_fetch": "cairn decision list / cairn assertion explain <id>",
        }
        return _result(store, data, subjects=[row["id"] for row in found["objects"][:limit]],
                       trust="source_observed")

    if name == "cairn_why":
        from . import why as why_mod

        package = why_mod.answer(
            store, repo_id=args["repo_id"], path=args["path"],
            line=args.get("line"), address=args.get("address"),
            commit=args.get("commit"), as_of=args.get("as_of"),
            budget_chars=args.get("budget"),
            intent_text=args.get("intent_text", False),
        )
        # Довіра не усереднюється в одне число: конверт несе клас кожного
        # елемента, а найнижчий з них стає класом усієї відповіді.
        classes = [item.get("trust_class") for item in package["trust"]]             if isinstance(package["trust"], list) else []
        lowest = "source_observed"
        for candidate in ("rejected_or_revoked", "imported_untrusted", "agent_inferred",
                          "agent_proposed", "source_observed", "verified_system",
                          "human_authoritative"):
            if candidate in classes:
                lowest = candidate
                break
        return {
            "tool_result": "cairn/1",
            "envelope": _envelope(
                store, subjects=[package["node"]["stable_key"]], trust=lowest,
                refs=[package["node"]["address"]],
                note=package["boundary"],
            ),
            "data": package,
        }

    if name == "cairn_code_explore":
        if args.get("symbol"):
            rows = store.read(
                "SELECT * FROM code_symbols WHERE stable_key LIKE ? ORDER BY revision DESC LIMIT ?",
                (f"%{args['symbol']}%", _limit(args)),
            )
        elif args.get("work_id"):
            task = work.get_work(store, args["work_id"])
            targets = store.read(
                "SELECT to_id FROM relations WHERE from_id = ? AND relation_type = 'targets_symbol'",
                (args["work_id"],),
            )
            keys = [row["to_id"] for row in targets]
            rows = []
            for key in keys:
                rows.extend(store.read(
                    "SELECT * FROM code_symbols WHERE stable_key = ? ORDER BY revision DESC LIMIT 1", (key,)
                ))
            if not rows and task.get("scope", {}).get("repo_id"):
                rows = store.read(
                    "SELECT * FROM code_symbols WHERE repo_id = ? ORDER BY stable_key LIMIT ?",
                    (task["scope"]["repo_id"], _limit(args)),
                )
        else:
            rows = store.read("SELECT * FROM code_symbols ORDER BY stable_key LIMIT ?", (_limit(args),))
        edges = []
        if rows:
            keys = sorted({row["stable_key"] for row in rows})
            marks = ",".join("?" for _ in keys)
            edges = store.read(
                f"SELECT from_key, to_key, relation_type FROM code_edges WHERE from_key IN ({marks}) "
                f"OR to_key IN ({marks}) LIMIT 50", tuple(keys) + tuple(keys),
            )
        data = {
            "symbols": [{"stable_key": row["stable_key"], "revision": row["revision"],
                         "path": row["path"], "qualified_name": row["qualified_name"],
                         "precision": row["precision"]} for row in rows],
            "edges": edges,
        }
        return _result(store, data, subjects=[row["stable_key"] for row in rows],
                       trust="source_observed", refs=[row["revision"] for row in rows])

    if name == "cairn_decision_propose":
        result = semantic.put_object(
            store, object_type="decision", title=args["title"], summary=args["summary"],
            stable_key=args.get("stable_key"), scope=args.get("scope") or [],
            constraints=args.get("constraints") or [], actor=actor,
        )
        obj = result["object"]
        return _result(store, {
            "object_id": obj["id"], "lifecycle_state": obj["lifecycle_state"],
            "trust_class": obj["trust_class"], "approved": False,
            "approval_hint": "потрібен human vote: decision accept <object_id>",
        }, subjects=[obj["id"]], trust=obj["trust_class"],
            note="пропозиція агента не є затвердженим рішенням")

    if name == "cairn_evidence_attach":
        kind = args["kind"]
        if kind == "commit":
            result = evidence.record_commit(
                store, args["repo_id"], args["commit"], actor, subject_id=args.get("subject_id")
            )
        elif kind == "test":
            if not args.get("command") or not args.get("cwd"):
                raise CairnError("bad_argument", "для test потрібні command і cwd", exit_code=2)
            result = evidence.record_test(
                store, command=args["command"], cwd=args["cwd"], actor=actor,
                subject_id=args.get("subject_id"), repo_id=args.get("repo_id"),
                commit=args.get("commit"), worktree=args.get("worktree"),
            )
        else:
            raise CairnError("bad_argument", f"невідомий kind {kind}", exit_code=2)
        ev = result["evidence"]
        return _result(store, {"evidence_id": ev["id"], "evidence_type": ev["evidence_type"],
                               "content_hash": ev["content_hash"], "metadata": ev["metadata"]},
                       subjects=[args.get("subject_id") or ev["id"]], trust=ev["trust_class"],
                       refs=[ev["source_ref"]])

    if name == "cairn_checkpoint":
        run = workflow.get(store, args["run_id"])
        body = {
            "run_id": args["run_id"], "state": run["state"],
            "checkpoint": args["checkpoint"], "recorded_at": store.now(),
        }
        key = f"checkpoint:{args['run_id']}:{canonical(args['checkpoint'])}"

        def op(con):
            ev = store.append(con, "workflow_checkpoint.recorded", args["run_id"], body, actor, key)
            return ev

        ev = store.mutate(op)
        return _result(store, {"event_id": ev["event_id"], "replayed": ev["replayed"],
                               "checkpoint": args["checkpoint"]},
                       subjects=[args["run_id"]], trust="verified_system" if ev["replayed"]
                       else ("agent_proposed" if actor["kind"] == "agent" else "human_authoritative"))

    if name == "cairn_handoff":
        result = handoff.create(
            store, work_id=args["work_id"], session_id=args["session_id"], actor=actor,
            summary=args["summary"], next_command=args["next"],
            decisions=args.get("decisions") or [], context_id=args.get("context_id"),
            evidence_ids=args.get("evidence_ids") or [],
        )
        packet = result["handoff"]
        return _result(store, {"handoff_id": packet["id"], "checksum": packet["checksum"],
                               "next_command": packet["next_command"],
                               "transcript_ref": packet["transcript_ref"]},
                       subjects=[args["work_id"]], trust="verified_system",
                       note="пакет перевіряється контрольною сумою; transcript не потрібен")

    tables = ("work_items", "objects", "assertions", "evidence", "gates", "code_symbols",
              "context_packages", "handoffs", "workflow_runs")
    counts = {}
    for table in tables:
        counts[table] = store.read_one(f"SELECT COUNT(*) AS n FROM {table}")["n"]
    tail = store.read_one("SELECT seq, event_type FROM events ORDER BY seq DESC LIMIT 1") or {}
    return _result(store, {"counts": counts, "last_event_seq": tail.get("seq"),
                           "last_event_type": tail.get("event_type")}, trust="source_observed")


def to_mcp(result: dict) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False,
                                                         separators=(",", ":"), default=str)}],
        "isError": False,
    }


def to_mcp_error(exc: CairnError) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(
            {"error": exc.code, "message": exc.message, "details": exc.details},
            ensure_ascii=False, separators=(",", ":"), default=str)}],
        "isError": True,
    }


def handle_request(store, request: dict, actor: dict) -> dict | None:
    """JSON-RPC 2.0 за рядковим протоколом stdio. Повертає None для нотифікацій."""
    method = request.get("method")
    req_id = request.get("id")
    if method == "notifications/initialized" or method == "initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "initialize":
        requested = (request.get("params") or {}).get("protocolVersion")
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": requested or "cairn-local/1",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "cairn-unified", "version": "2.0.0"},
            "instructions": "Актор фіксується хостом; рішення агента завжди proposed.",
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": tools_list()}
    if method == "tools/call":
        params = request.get("params") or {}
        try:
            result = call_tool(store, params.get("name"), params.get("arguments"), actor)
            payload = to_mcp(result)
        except CairnError as exc:
            payload = to_mcp_error(exc)
        return {"jsonrpc": "2.0", "id": req_id, "result": payload}
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"method {method} not found"}}


def serve(store, actor: dict | None = None, stdin=None, stdout=None) -> int:
    source = sys.stdin if stdin is None else stdin
    target = sys.stdout if stdout is None else stdout
    actor = dict(actor or DEFAULT_ACTOR)
    for line in source:
        raw = line.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            target.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                     "error": {"code": -32700, "message": "parse error"}}) + "\n")
            target.flush()
            continue
        response = handle_request(store, request, actor)
        if response is not None:
            target.write(json.dumps(response, ensure_ascii=False, separators=(",", ":"),
                                    default=str) + "\n")
            target.flush()
    return 0
