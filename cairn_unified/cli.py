"""CLI для Unified-зрізу. Один вхід для всіх bounded contexts.

 Усі команди повертають JSON на stdout. Помилка — теж JSON з полем `error`
 і стабільним exit code. Ніякого прихованого стану між викликами: стан
 відтворюється з Event Ledger.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import code, context, evidence, gates, handoff, semantic, store as storemod, workflow, work
from .errors import CairnError
from .store import Store


def _json_arg(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    text = str(value)
    if text.startswith("@"):
        # utf-8-sig: файли, записані Windows PowerShell, мають BOM.
        return json.loads(Path(text[1:]).read_text(encoding="utf-8-sig"))
    return json.loads(text)


def _emit(obj, compact: bool = False) -> None:
    if compact:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)
    else:
        text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    try:
        print(text)
    except UnicodeEncodeError:
        # Windows consoles often use CP1251; keep stdout valid JSON there.
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, errors="backslashreplace").decode(encoding))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cairn2", description="Cairn Unified 2.0 — локальний CLI")
    p.add_argument("--root", default=".", help="корінь проєкту")
    p.add_argument("--actor", default="human:local", help="human:id | agent:id | system:id")
    p.add_argument("--compact", action="store_true", help="один рядок JSON")
    groups = p.add_subparsers(dest="group", required=True)

    def group(name: str):
        g = groups.add_parser(name)
        return g, g.add_subparsers(dest="cmd", required=True)

    g, c = group("project")
    c.add_parser("init"); c.add_parser("doctor"); c.add_parser("rebuild")
    gc = c.add_parser("config"); gc.add_argument("key")

    g, c = group("repo")
    ca = c.add_parser("add"); ca.add_argument("path")
    c.add_parser("list")

    g, c = group("change")
    note = c.add_parser("note", help="записати мотив поточної правки до коміту")
    note.add_argument("--repo-path", required=True)
    note.add_argument("--path", action="append", required=True)
    note.add_argument("--why", required=True)
    note.add_argument("--intent", required=True)
    note.add_argument("--session-id", default="")
    trigger = c.add_parser("sync", help="зв'язати записи причин із Git-комітами")
    trigger.add_argument("--repo-path", required=True)
    state = c.add_parser("status", help="поточний стан нотаток і кожного шляху")
    state.add_argument("--repo-path", required=True)
    state.add_argument("--note-id", default="")

    g, c = group("code")
    ci = c.add_parser("index"); ci.add_argument("repo_id")
    cn = c.add_parser("nodes"); cn.add_argument("repo_id")
    cn.add_argument("--paths", default=None, help="шляхи через кому; за замовчуванням усі текстові")

    g, c = group("node")
    nr = c.add_parser("resolve"); nr.add_argument("repo_id"); nr.add_argument("path")
    nr.add_argument("--line", type=int); nr.add_argument("--address")
    np_ = c.add_parser("prepare")
    np_.add_argument("repo_id"); np_.add_argument("work_id"); np_.add_argument("path")
    np_.add_argument("--line", type=int); np_.add_argument("--address")
    np_.add_argument("--packs", default=None); np_.add_argument("--follow", action="store_true")

    g, c = group("work")
    cc = c.add_parser("create")
    cc.add_argument("title")
    cc.add_argument("--type", default="task")
    cc.add_argument("--priority", type=int, default=1)
    cc.add_argument("--description", default="")
    cc.add_argument("--acceptance-json", default="[]")
    cc.add_argument("--scope-json", default="{}")
    cc.add_argument("--capabilities", default="")
    cc.add_argument("--parent")
    cc.add_argument("--budget", type=int)
    c.add_parser("list").add_argument("--status")
    c.add_parser("show").add_argument("work_id")
    cr = c.add_parser("ready"); cr.add_argument("--agent")
    ce = c.add_parser("eligibility"); ce.add_argument("work_id"); ce.add_argument("--agent")
    cclaim = c.add_parser("claim"); cclaim.add_argument("work_id"); cclaim.add_argument("agent_id")
    cadd = c.add_parser("agent")
    cadd.add_argument("agent_id")
    cadd.add_argument("--provider", default="local")
    cadd.add_argument("--capabilities", default="")
    cadd.add_argument("--trust", default="agent_proposed")
    ctoken = c.add_parser("release"); ctoken.add_argument("work_id"); ctoken.add_argument("token", type=int)
    ctrans = c.add_parser("transition"); ctrans.add_argument("work_id"); ctrans.add_argument("status")
    ctrans.add_argument("--token", type=int)
    cacc = c.add_parser("accept"); cacc.add_argument("work_id"); cacc.add_argument("token", type=int)
    c.add_parser("verify").add_argument("work_id")
    cclose = c.add_parser("close"); cclose.add_argument("work_id"); cclose.add_argument("token", type=int)
    ccom = c.add_parser("comment"); ccom.add_argument("work_id"); ccom.add_argument("text")

    g, c = group("decision")
    cp = c.add_parser("propose"); cp.add_argument("title")
    cp.add_argument("--summary", required=True)
    cp.add_argument("--constraints", default="[]")
    cp.add_argument("--scope", default="[]")
    ca2 = c.add_parser("accept"); ca2.add_argument("object_id", nargs="?")
    ca2.add_argument("--rationale"); ca2.add_argument("--work"); ca2.add_argument("--commit")
    cs = c.add_parser("supersede"); cs.add_argument("object_id"); cs.add_argument("--summary", required=True)
    cv = c.add_parser("revoke"); cv.add_argument("object_id"); cv.add_argument("--reason", required=True)
    cq = c.add_parser("query"); cq.add_argument("--text"); cq.add_argument("--type")
    c.add_parser("list")

    g, c = group("assertion")
    ca3 = c.add_parser("add"); ca3.add_argument("subject"); ca3.add_argument("predicate")
    ca3.add_argument("literal_json"); ca3.add_argument("--trust")
    c.add_parser("query").add_argument("--subject")
    c.add_parser("explain").add_argument("assertion_id")

    g, c = group("relation")
    cr2 = c.add_parser("add"); cr2.add_argument("from_id"); cr2.add_argument("to_id"); cr2.add_argument("relation")

    g, c = group("evidence")
    ccm = c.add_parser("commit"); ccm.add_argument("repo_id"); ccm.add_argument("commit")
    ccm.add_argument("--subject")
    ccf = c.add_parser("facts"); ccf.add_argument("repo_id"); ccf.add_argument("commit")
    ccf.add_argument("--work", required=True); ccf.add_argument("--symbol", required=True)
    cte = c.add_parser("test"); cte.add_argument("--cwd", default=None); cte.add_argument("--subject")
    cte.add_argument("--repo-id"); cte.add_argument("--commit")
    cte.add_argument("command", nargs=argparse.REMAINDER)
    cl = c.add_parser("list"); cl.add_argument("--subject")
    cv2 = c.add_parser("verify"); cv2.add_argument("evidence_id")

    g, c = group("gate")
    gc2 = c.add_parser("create"); gc2.add_argument("gate_type"); gc2.add_argument("--subject")
    gc2.add_argument("--condition-json", default="{}"); gc2.add_argument("--deadline")
    ge = c.add_parser("evaluate"); ge.add_argument("gate_id"); ge.add_argument("--evidence")
    ge.add_argument("--failed", action="store_true")
    gr = c.add_parser("resolve"); gr.add_argument("gate_id"); gr.add_argument("--evidence", required=True)
    c.add_parser("list").add_argument("--subject")

    g, c = group("context")
    for verb in ("compile", "build"):
        ccompile = c.add_parser(verb); ccompile.add_argument("work_id")
        ccompile.add_argument("--agent"); ccompile.add_argument("--purpose", default="edit")
        ccompile.add_argument("--budget", type=int); ccompile.add_argument("--as-of")
        ccompile.add_argument("--persist", action="store_true")
    c.add_parser("show").add_argument("context_id")
    c.add_parser("explain").add_argument("context_id")

    g, c = group("handoff")
    ch = c.add_parser("create"); ch.add_argument("work_id"); ch.add_argument("session_id")
    ch.add_argument("--summary", required=True); ch.add_argument("--next", required=True)
    ch.add_argument("--decisions-json", default="[]"); ch.add_argument("--context-id")
    ch.add_argument("--evidence-json", default="[]")
    chr_ = c.add_parser("recover"); chr_.add_argument("handoff_id")
    chr_.add_argument("--compile", action="store_true")
    c.add_parser("latest").add_argument("work_id")
    c.add_parser("seal").add_argument("handoff_id")

    g, c = group("workflow")
    cdef = c.add_parser("define"); cdef.add_argument("name"); cdef.add_argument("--definition-json", required=True)
    cst = c.add_parser("start"); cst.add_argument("definition_id")
    cst.add_argument("--input-json", default="{}"); cst.add_argument("--retention", default="durable")
    for verb in ("advance", "pause", "resume", "stop", "show"):
        c.add_parser(verb).add_argument("run_id")
    c.add_parser("list").add_argument("--state")

    g, c = group("replica")
    c.add_parser("list")

    g, c = group("federation")
    c.add_parser("export").add_argument("path")
    ci2 = c.add_parser("import")
    ci2.add_argument("path")
    ci2.add_argument("--apply", action="store_true")
    cr2 = c.add_parser("restore")
    cr2.add_argument("path")
    cr2.add_argument("target")

    g, c = group("why")
    cw = c.add_parser("node")
    cw.add_argument("path")
    cw.add_argument("--repo-id", default=None,
                    help="типово — тотожність репозиторію в --repo-path")
    cw.add_argument("--repo-path", default=".")
    cw.add_argument("--line", type=int, default=None)
    cw.add_argument("--address", default=None)
    cw.add_argument("--commit", default=None)
    cw.add_argument("--as-of", dest="as_of", default=None)
    cw.add_argument("--budget", type=int, default=None, help="символів, не токенів")
    cw.add_argument("--full", action="store_true",
                    help="повні докази, тексти правок і походження без стислого огляду")
    cw.add_argument("--include-pending", action="store_true",
                    help="показати правки без доведеного зв'язку з Git-комітом")
    cw.add_argument("--no-chronology", dest="chronology", action="store_false")
    cw.add_argument("--intent-text", dest="intent_text", action="store_true",
                    help="дочитати текст намірів із джерела, звіривши sha256")
    cw.add_argument("--opencode-db", dest="opencode_db", default=None)

    g, c = group("mcp")
    c.add_parser("tools")
    c.add_parser("serve")
    return p


def dispatch(args, actor: dict):
    root = Path(args.root).resolve()
    group, cmd = args.group, args.cmd

    if group == "project" and cmd == "init":
        return Store(root).init()
    if group == "project" and cmd == "doctor":
        return Store(root).doctor()
    if group == "federation" and cmd == "restore":
        from . import federation
        return federation.restore_bundle(federation.load_bundle(args.path), args.target, actor)
    store = Store.open(root)

    if group == "project":
        if cmd == "rebuild":
            return store.rebuild()
        if cmd == "config":
            return store.config_value(args.key)

    if group == "repo":
        if cmd == "add":
            from .code import git
            identity = git.identity(args.path)
            key = identity["id"]

            def op(con):
                ev = store.append(con, "repository.upserted", key, identity, actor,
                                  idempotency_key=f"repo:{key}")
                return {"repository": ev["payload"], "event_id": ev["event_id"], "replayed": ev["replayed"]}
            return store.mutate(op)
        return store.read("SELECT * FROM repositories")

    if group == "change":
        from . import change_notes
        if cmd == "note":
            return change_notes.note(store, args.repo_path, args.path, args.why,
                                     args.intent, actor, args.session_id)
        if cmd == "status":
            return change_notes.status(store, args.repo_path, args.note_id)
        return change_notes.sync(store, args.repo_path)

    if group == "code":
        if cmd == "nodes":
            from . import nodes
            paths = [p.strip() for p in args.paths.split(",") if p.strip()] if args.paths else None
            return nodes.index(store, args.repo_id, actor, paths)
        return code.php.index(store, args.repo_id, actor)

    if group == "node":
        from . import history, nodes
        repo = store.read_one("SELECT * FROM repositories WHERE id = ?", (args.repo_id,))
        if repo is None:
            raise CairnError("not_found", f"репозиторій {args.repo_id} не зареєстровано", exit_code=1)
        found = nodes.resolve(store, args.repo_id, args.path, line=args.line,
                              address=args.address)
        if cmd == "resolve":
            return {"repo_id": args.repo_id, "path": args.path, "nodes": found}
        if not found:
            return {"error": "node_not_found", "path": args.path,
                    "hint": "спочатку `code nodes <repo_id>`", "nodes": []}
        node = found[0]
        result = history.scan(store, args.repo_id, args.work_id, node["stable_key"],
                              args.path, actor, follow=args.follow)
        result["node"] = node
        if args.packs:
            result["compactions"] = history.import_compactions(
                store, args.repo_id, args.work_id, node["stable_key"], args.path, actor,
                args.packs, start_line=node.get("start_line"), end_line=node.get("end_line"),
            )
        return result

    if group == "work":
        if cmd == "create":
            return work.create_work(
                store, title=args.title, actor=actor, type=args.type, priority=args.priority,
                description=args.description, acceptance=_json_arg(args.acceptance_json),
                scope=_json_arg(args.scope_json),
                capabilities=[x.strip() for x in args.capabilities.split(",") if x.strip()],
                parent_id=args.parent, budget_limit_chars=args.budget,
            )
        if cmd == "list":
            return work.list_work(store, args.status)
        if cmd == "show":
            return work.get_work(store, args.work_id)
        if cmd == "ready":
            return work.ready(store, args.agent)
        if cmd == "eligibility":
            return work.eligibility(store, args.work_id, args.agent)
        if cmd == "claim":
            return work.claim(store, args.work_id, args.agent_id, actor)
        if cmd == "agent":
            return work.register_agent(
                store, agent_id=args.agent_id, actor=actor, provider=args.provider,
                capabilities=[x.strip() for x in args.capabilities.split(",") if x.strip()],
                trust_class=args.trust,
            )
        if cmd == "release":
            return work.release(store, args.work_id, args.token, actor)
        if cmd == "transition":
            return work.transition(store, args.work_id, args.status, actor, fencing_token=args.token)
        if cmd == "accept" or cmd == "close":
            return work.accept(store, args.work_id, args.token, actor)
        if cmd == "verify":
            return work.verify(store, args.work_id)
        if cmd == "comment":
            return work.comment(store, args.work_id, args.text, actor)

    if group == "decision":
        if cmd == "propose":
            return semantic.put_object(
                store, object_type="decision", title=args.title, summary=args.summary,
                actor=actor, constraints=_json_arg(args.constraints), scope=_json_arg(args.scope),
            )
        if cmd == "accept":
            if args.object_id:
                return semantic.accept_object(store, object_id=args.object_id,
                                              actor=actor, rationale=args.rationale)
            if not (args.work and args.commit):
                raise CairnError("bad_input", "потрібен object_id або --work з --commit", exit_code=2)
            return semantic.accept_batch(store, work_id=args.work, commit=args.commit,
                                         actor=actor, rationale=args.rationale)
        if cmd == "supersede":
            return semantic.supersede(store, object_id=args.object_id, summary=args.summary, actor=actor)
        if cmd == "revoke":
            return semantic.revoke(store, object_id=args.object_id, actor=actor, reason=args.reason)
        if cmd == "query":
            return semantic.query(store, text=args.text, object_type=args.type)
        return semantic.query(store, object_type="decision")

    if group == "assertion":
        if cmd == "add":
            return semantic.add_assertion(
                store, subject_id=args.subject, predicate=args.predicate,
                literal=_json_arg(args.literal_json), actor=actor, trust=args.trust,
            )
        if cmd == "query":
            return semantic.query(store, subject_id=args.subject)
        return semantic.explain_trust(store, args.assertion_id)

    if group == "relation" and cmd == "add":
        return semantic.relate(store, from_id=args.from_id, to_id=args.to_id,
                               relation=args.relation, actor=actor)

    if group == "evidence":
        if cmd == "facts":
            from . import commits
            return commits.ingest(store, args.repo_id, args.commit, work_id=args.work,
                                  symbol=args.symbol, actor=actor)
        if cmd == "commit":
            return evidence.record_commit(store, args.repo_id, args.commit, actor, subject_id=args.subject)
        if cmd == "test":
            return evidence.record_test(store, command=args.command, cwd=args.cwd or str(root),
                                        actor=actor, subject_id=args.subject,
                                        repo_id=args.repo_id, commit=args.commit)
        if cmd == "list":
            return evidence.show(store, args.subject)
        return evidence.verify(store, args.evidence_id)

    if group == "gate":
        if cmd == "create":
            return gates.create(store, gate_type=args.gate_type, subject_id=args.subject,
                                condition=_json_arg(args.condition_json), deadline_at=args.deadline, actor=actor)
        if cmd == "evaluate":
            return gates.evaluate(store, args.gate_id, actor, passed=not args.failed, evidence_id=args.evidence)
        if cmd == "resolve":
            return gates.resolve(store, args.gate_id, actor, evidence_id=args.evidence)
        return gates.list_gates(store, args.subject)

    if group == "context":
        if cmd in ("compile", "build"):
            package = context.build(store, args.work_id, agent_id=args.agent, purpose=args.purpose,
                                    budget_chars=args.budget, as_of=args.as_of)
            if args.persist:
                context.persist(store, package, actor)
            return package
        if cmd == "show":
            return context.show(store, args.context_id)
        return context.explain(store, args.context_id)

    if group == "handoff":
        if cmd == "create":
            return handoff.create(
                store, work_id=args.work_id, session_id=args.session_id, actor=actor,
                summary=args.summary, next_command=args.next,
                decisions=_json_arg(args.decisions_json), context_id=args.context_id,
                evidence_ids=_json_arg(args.evidence_json),
            )
        if cmd == "recover":
            return handoff.recover(store, args.handoff_id, compile_context=args.compile, actor=actor)
        if cmd == "latest":
            packet = handoff.latest(store, args.work_id)
            if packet is None:
                raise CairnError("not_found", "для задачі немає handoff", exit_code=1)
            return packet
        return handoff.seal(store, args.handoff_id, actor)

    if group == "workflow":
        if cmd == "define":
            return workflow.define(store, name=args.name, definition=_json_arg(args.definition_json), actor=actor)
        if cmd == "start":
            return workflow.start(store, args.definition_id, actor, input=_json_arg(args.input_json),
                                  retention=args.retention)
        if cmd == "advance":
            return workflow.advance(store, args.run_id, actor)
        if cmd == "pause":
            return workflow.pause(store, args.run_id, actor)
        if cmd == "resume":
            return workflow.resume(store, args.run_id, actor)
        if cmd == "stop":
            return workflow.stop(store, args.run_id, actor)
        if cmd == "show":
            return workflow.get(store, args.run_id)
        return workflow.list_runs(store, args.state)

    if group == "replica" and cmd == "list":
        return store.read("SELECT * FROM replicas")

    if group == "federation":
        from . import federation
        if cmd == "export":
            return federation.export_bundle(store, path=args.path)
        if cmd == "restore":
            return federation.restore_bundle(federation.load_bundle(args.path), args.target, actor)
        bundle = federation.load_bundle(args.path)
        return federation.import_bundle(store, bundle, actor, apply=args.apply)

    if group == "why" and cmd == "node":
        from . import code as code_mod, why as why_mod
        repo_id = args.repo_id or code_mod.git.identity(args.repo_path)["id"]
        return why_mod.answer(
            store, repo_id=repo_id, path=args.path, line=args.line,
            address=args.address, commit=args.commit, as_of=args.as_of,
            budget_chars=args.budget, chronology=args.chronology,
            intent_text=args.intent_text, opencode_db=args.opencode_db,
            repo_path=args.repo_path, compact=not args.full,
            include_pending=args.include_pending,
        )

    if group == "mcp":
        from . import mcp
        if cmd == "tools":
            return mcp.tools_list()
        return mcp.serve(store, actor)

    raise CairnError("unknown_command", f"команду {group} {cmd} не реалізовано", exit_code=2)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        actor = storemod.parse_actor(args.actor)
        if args.group == "mcp" and args.cmd == "serve":
            # Чистий stdio-протокол: жодного додаткового рядка в stdout.
            from .mcp import DEFAULT_ACTOR, serve
            return serve(Store(Path(args.root).resolve()), actor or DEFAULT_ACTOR)
        result = dispatch(args, actor)
    except CairnError as exc:
        _emit({"error": exc.code, "message": exc.message, "details": exc.details}, args.compact)
        return exc.exit_code
    except (json.JSONDecodeError, OSError, ValueError, LookupError, TypeError) as exc:
        # LookupError/TypeError: дані з файла (bundle, --*-json) можуть не мати
        # очікуваного ключа або типу. Без них виходив сирий traceback замість
        # документованого JSON-конверта помилки.
        _emit({"error": "bad_input", "message": f"{type(exc).__name__}: {exc}"}, args.compact)
        return 2
    _emit(result, args.compact)
    return 0


if __name__ == "__main__":
    sys.exit(main())
