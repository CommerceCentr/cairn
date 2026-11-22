r"""Автоперевірка для ДВОХ журналів агентів: Claude Code і Codex.

Фікстури — тимчасові файли у справжніх форматах (прочитаних із реальних
журналів), а не вигадані. `CLAUDE_CONFIG_DIR` і `CODEX_HOME` вказують у
temp, тому `discover()` знаходить саме їх.

Реверси, без яких тест нічого не доводить:

* намір, текст якого в журналі ЗМІНИЛИ, мусить дати розбіжність, а не
  тихо пройти;
* Codex не дає мутацій, і це мусить читатися як МЕЖА ДЖЕРЕЛА
  (`node_link_unsupported_by_source`), а не як `unresolved:node_absent`:
  перше лагодити нічого, друге треба лагодити;
* `reasoning` у Codex зашифрований — у матриці можливостей це `unsupported`,
  а не порожній рядок, який виглядав би як «мотиву не було».
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fixture_repo import PY_FILE, make_repo  # noqa: E402

from cairn_unified.adapters import claude_code, codex, normalize  # noqa: E402

from cairn_unified import code, compaction_link, nodes, verification  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

HUMAN = {"kind": "human", "id": "sources-human"}
SYSTEM = {"kind": "system", "id": "sources-indexer"}
INTENT = "зробити автоперевірку для обох журналів"
CC_SID = "cc-fixture-session"
CX_SID = "01a19770-0000-7000-8000-000000000001"


def write_claude_journal(path: Path, repo: Path) -> None:
    rows = [
        {"uuid": "u1", "timestamp": "2026-11-21T09:00:01Z", "sessionId": CC_SID,
         "cwd": str(repo), "type": "user",
         "message": {"role": "user", "content": [{"type": "text", "text": INTENT}]}},
        {"uuid": "t1", "timestamp": "2026-11-21T09:00:05Z", "sessionId": CC_SID,
         "cwd": str(repo), "type": "assistant",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Write",
              "input": {"file_path": str(repo / PY_FILE)}}]}},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


def write_codex_journal(path: Path, repo: Path) -> None:
    rows = [
        {"timestamp": "2026-11-21T09:10:00Z", "ordinal": 0, "type": "session_meta",
         "payload": {"session_id": CX_SID, "id": CX_SID, "cwd": str(repo),
                     "runtime_workspace_roots": [str(repo)],
                     "originator": "fixture", "cli_version": "0.0.0"}},
        # Службовий блок: у наміри потрапити НЕ має.
        {"timestamp": "2026-11-21T09:10:01Z", "ordinal": 1, "type": "response_item",
         "payload": {"type": "message", "role": "user", "id": "msg-injected",
                     "content": [{"type": "input_text",
                                  "text": "# AGENTS.md instructions for repo"}]}},
        {"timestamp": "2026-11-21T09:10:02Z", "ordinal": 2, "type": "response_item",
         "payload": {"type": "message", "role": "user", "id": "msg-real",
                     "content": [{"type": "input_text", "text": INTENT}]}},
        # Правка робиться через exec: шляху в структурі немає — саме це й
        # робить мутації недоступними для цього джерела.
        {"timestamp": "2026-11-21T09:10:03Z", "ordinal": 3, "type": "response_item",
         "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "c1",
                     "input": "text(await tools.exec_command({cmd:\"Set-Content x.py\"}))"}},
        {"timestamp": "2026-11-21T09:10:04Z", "ordinal": 4, "type": "response_item",
         "payload": {"type": "reasoning", "id": "rs1", "summary": [],
                     "encrypted_content": "gAAAA-not-readable"}},
        {"timestamp": "2026-11-21T09:10:05Z", "ordinal": 5, "type": "compacted",
         "payload": {"message": "", "window_id": "win-1",
                     "retained_context": {"user_messages": [{"order": 2}]}}},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


def link(store, repo: Path, repo_id: str, *, reader, source_kind: str,
         journal: Path, scope: list[str], commit: str) -> dict:
    events = normalize.canonical_events(reader.read(str(journal)))
    descriptor = {"summary": f"сесія {journal.stem}", "message_id": f"{journal.stem}:tail",
                  "seq": len(events), "keep_id": "", "reason": "no_agent_compaction"}
    session_id = next((e.get("session_id") for e in reader.read(str(journal))
                       if e.get("session_id")), journal.stem)
    session = compaction_link.session_object(
        store, session_id=str(session_id), compaction=descriptor, repo_id=repo_id,
        actor=HUMAN, commit=commit, scope=scope, source_kind=source_kind,
        session_source=source_kind, source_ref=journal.name)
    linked = compaction_link.link_mutations(
        store, events=events, session_obj_id=session["id"], repo_id=repo_id,
        repo_path=(str(repo),), actor=HUMAN)
    auto = compaction_link.auto_compaction(
        store, events=events, links=linked["links"], session_obj=session,
        opencode_db="", actor=HUMAN, commit=commit, source_kind=source_kind,
        source_ref=journal.name)
    return {"events": events, "session": session, "linked": linked,
            "auto": auto["object"]}


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    temp = Path(tempfile.mkdtemp(prefix="sources-"))
    saved = {k: os.environ.get(k) for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
    try:
        fixture = make_repo(temp / "repo", files={PY_FILE: "def a():\n    return 1\n"},
                            second={PY_FILE: "def a():\n    return 2\n"})
        repo, head = Path(fixture["path"]), fixture["head"]
        cc_home = temp / "cchome"
        cx_home = temp / "cxhome"
        os.environ["CLAUDE_CONFIG_DIR"] = str(cc_home)
        os.environ["CODEX_HOME"] = str(cx_home)
        cc_journal = cc_home / "projects" / "fixture" / "session.jsonl"
        cx_journal = (cx_home / "sessions" / "2026" / "11" / "21"
                      / "rollout-2026-11-21T09-10-00-fixture.jsonl")
        write_claude_journal(cc_journal, repo)
        write_codex_journal(cx_journal, repo)

        checks.append(("журнал Claude Code знайдено через discover",
                       any(Path(p).name == cc_journal.name for p in claude_code.discover()),
                       str(len(claude_code.discover()))))
        checks.append(("журнал Codex знайдено через discover",
                       any(Path(p).name == cx_journal.name for p in codex.discover()),
                       str(len(codex.discover()))))

        Store(temp / "store").init()
        store = Store.open(temp / "store")
        ident = code.git.identity(str(repo))
        store.mutate(lambda con: store.append(con, "repository.upserted", ident["id"],
                                              ident, HUMAN, f"repo:{ident['id']}"))
        repo_id = ident["id"]
        nodes.index(store, repo_id, SYSTEM, paths=[PY_FILE])
        node_key = nodes.resolve(store, repo_id, PY_FILE,
                                 address=f"whole:{PY_FILE}")[0]["stable_key"]

        # --- Claude Code: мутації стають ребрами ---
        cc = link(store, repo, repo_id, reader=claude_code,
                  source_kind=normalize.SOURCE_CLAUDE_CODE, journal=cc_journal,
                  scope=[node_key], commit=head)
        checks.append(("Claude Code: мутація стала ребром", cc["linked"]["new"] >= 1,
                       str(cc["linked"]["new"])))
        checks.append(("Claude Code: намір зібрано",
                       len(cc["auto"]["semantic"].get("intent_refs") or []) == 1,
                       str(len(cc["auto"]["semantic"].get("intent_refs") or []))))

        cc_checks, cc_reasons = verification.check_object(store, cc["session"],
                                                         repo_path=repo)
        names = {c["check"] for c in cc_checks}
        checks.append(("Claude Code: журнал перечитано", "journal_readable" in names,
                       str(sorted(names))[:40]))
        checks.append(("Claude Code: сесія без ескалацій", not cc_reasons,
                       str(cc_reasons)))

        cc_auto_checks, cc_auto_reasons = verification.check_object(
            store, cc["auto"], repo_path=repo)
        auto_names = {c["check"] for c in cc_auto_checks}
        checks.append(("Claude Code: наміри збіглися з журналом",
                       "intents_match_source" in auto_names, str(sorted(auto_names))[:40]))
        checks.append(("Claude Code: автокомактація без ескалацій",
                       not cc_auto_reasons, str(cc_auto_reasons)))

        # --- реверс: текст наміру в журналі змінили ---
        tampered = json.loads(json.dumps(cc["auto"]))
        tampered["semantic"]["intent_refs"] = [
            {**ref, "text_sha256": "0" * 64}
            for ref in tampered["semantic"]["intent_refs"]]
        _tc, t_reasons = verification.check_object(store, tampered, repo_path=repo)
        checks.append(("підміна хеша наміру ескалює",
                       any("intent_text_changed" in r for r in t_reasons),
                       str(t_reasons)[:60]))

        # --- Codex: мутацій немає, і це МЕЖА ДЖЕРЕЛА ---
        cx = link(store, repo, repo_id, reader=codex,
                  source_kind=normalize.SOURCE_CODEX, journal=cx_journal, scope=[], commit=head)
        kinds = {e["kind"] for e in cx["events"]}
        checks.append(("Codex: мутацій немає", "mutation" not in kinds, str(sorted(kinds))))
        intents = [e for e in cx["events"] if e["kind"] == "user_intent"]
        checks.append(("Codex: службовий блок відкинуто", len(intents) == 1,
                       str([i["text"][:20] for i in intents])))
        checks.append(("Codex: компактацію видно",
                       any(e["kind"] == "compaction" for e in cx["events"]), "так"))
        checks.append(("Codex: наміри зібрано без мутацій",
                       len(cx["auto"]["semantic"].get("intent_refs") or []) == 1,
                       str(cx["auto"]["semantic"].get("basis"))))
        checks.append(("Codex: підставу названо",
                       cx["auto"]["semantic"].get("basis") == "user_intents_only",
                       str(cx["auto"]["semantic"].get("basis"))))

        cx_checks, cx_reasons = verification.check_object(store, cx["auto"], repo_path=repo)
        cx_names = {c["check"] for c in cx_checks}
        checks.append(("Codex: межа джерела названа окремо",
                       "no_structured_mutations_in_session" in cx_names,
                       str(sorted(cx_names))[:60]))
        checks.append(("Codex: межа НЕ подана як unresolved",
                       not any("node_absent" in r for r in cx_reasons), str(cx_reasons)))
        checks.append(("Codex: без ескалацій", not cx_reasons, str(cx_reasons)))

        caps = normalize.capabilities(normalize.SOURCE_CODEX)
        checks.append(("Codex: reasoning позначено unsupported",
                       caps["reasoning"].startswith("unsupported"), caps["reasoning"]))
        checks.append(("Claude Code: mutation позначено partial",
                       normalize.capabilities(normalize.SOURCE_CLAUDE_CODE)["mutation"]
                       .startswith("partial"), "так"))
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(temp, ignore_errors=True)

    good = 0
    for label, ok, detail in checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {label:50} | {detail[:38]}")
        good += int(ok)
    print(f"ВЕРДИКТ: автоперевірка двох джерел ({good}/{len(checks)})")
    return 0 if good == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
