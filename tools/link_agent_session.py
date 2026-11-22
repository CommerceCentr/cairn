"""Звʼязати сесію Claude Code з комітом: мутації -> вузли -> причина.

    python tools/link_claude_session.py --root <сховище> --repo <репозиторій> \
        --commit <sha> [--journal <шлях.jsonl>] [--work "назва задачі"]

Той самий конвеєр, що `causal_history.link`, але джерело — журнал агента,
а не `opencode.db`. Збирач (`adapters/normalize.py`) перекладає сирі записи
в канонічну форму; далі працюють уже перевірені кроки.

Два джерела, і вони НЕ однакові за можливостями:

```text
claude_code  наміри + компактації + мутації (лише Write/Edit)
codex        наміри + компактації; мутацій НЕМА (правки всередині `exec`)
```

Матриця лежить у `adapters/normalize.CAPABILITIES` і потрапляє у
перевірку обʼєкта, тому «мутацій 0» для Codex читається як властивість
джерела, а не як «правок не було».

Що створюється (і нічого більше):

```text
session               хто й коли, з посиланням на журнал
unresolved_mutations  чому частина правок НЕ стала ребрами
commit_fact           CrUD коміту; коміт причиною НЕ оголошується
auto_compaction       детермінований семантичний опис «чому»
```

МЕЖА, яку видно вже у вимірі: Claude Code бачить мутацією лише `Write` і
`Edit`. Правка через shell (`python - <<EOF`, `sed`) структурованою
мутацією НЕ є, тож у граф не потрапляє. Codex не дає мутацій узагалі.
Це властивості джерел, а не цього інструмента, і вони потрапляють у звіт.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified.adapters import claude_code, codex, normalize  # noqa: E402

from cairn_unified import (code, compaction_link, nodes, semantic,  # noqa: E402
                           verification, work)
from cairn_unified.canon import sha256_text  # noqa: E402
from cairn_unified.store import Store, parse_actor  # noqa: E402

SYSTEM = {"kind": "system", "id": "claude-session-linker"}


READERS = {"claude_code": claude_code, "codex": codex}
SOURCE_KINDS = {"claude_code": normalize.SOURCE_CLAUDE_CODE,
                "codex": normalize.SOURCE_CODEX}


def find_journal(repo: Path, source: str) -> Path:
    """Журнал цього репозиторію. Якщо їх кілька — найсвіжіший.

    Для Codex тека не названа за репозиторієм, тому фільтруємо за `cwd`
    із шапки сесії: вона лежить у самому журналі й не вгадується.
    """
    reader = READERS[source]
    candidates = [Path(p) for p in reader.discover()]
    if not candidates:
        raise SystemExit(f"журналів {source} не знайдено")
    if source == "claude_code":
        marker = str(repo).replace(":", "").replace("\\", "-").replace("/", "-")
        named = [p for p in candidates
                 if marker.lower().lstrip("-") in str(p.parent).lower()]
    else:
        named = []
        for path in candidates[:60]:          # шапку читаємо лише у свіжих
            try:
                meta = codex.session_meta(path)
            except Exception:                 # noqa: BLE001 — стиснуте/бите
                continue
            roots = [meta.get("cwd") or "", *(meta.get("roots") or [])]
            if any(str(repo).lower() == str(r).lower().rstrip("\\/") for r in roots if r):
                named.append(path)
    if not named:
        raise SystemExit(
            f"не знайдено журнал {source} для {repo}; задайте --journal явно"
        )
    return max(named, key=lambda p: p.stat().st_mtime)


def session_descriptor(events: list[dict], journal: Path) -> dict:
    """Опис сесії як обʼєкта, коли компактації агента НЕМАЄ.

    Важливо: ми НЕ вигадуємо компактацію. Якщо агент її не робив, у журналі
    її немає, і обʼєкт сесії це каже прямо (`reason="no_agent_compaction"`).
    Семантичний опис «чому» дає окремий `auto_compaction` — детермінований,
    складений із самих подій.
    """
    compactions = [e for e in events if e["kind"] == "compaction"]
    if compactions:
        last = compactions[-1]
        return {"summary": last["summary"], "message_id": last["message_id"],
                "seq": last["seq"], "keep_id": last.get("keep_id", ""),
                "reason": "agent_compaction"}
    stamps = [e["ts"] for e in events if e.get("ts")]
    counts: dict[str, int] = {}
    for item in events:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    summary = (
        f"Сесія {journal.stem}: подій {len(events)} "
        f"({', '.join(f'{k} {v}' for k, v in sorted(counts.items()))}); "
        f"від {stamps[0][:19] if stamps else '—'} до {stamps[-1][:19] if stamps else '—'}. "
        "Компактації агента в журналі немає."
    )
    return {"summary": summary, "message_id": f"{journal.stem}:tail",
            "seq": len(events), "keep_id": "", "reason": "no_agent_compaction"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="link_agent_session")
    parser.add_argument("--root", required=True, help="корінь сховища .cairn")
    parser.add_argument("--repo", required=True, help="репозиторій коду")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--source", choices=sorted(READERS), default="claude_code")
    parser.add_argument("--journal", default=None)
    parser.add_argument("--work", default=None, help="назва задачі-якоря")
    parser.add_argument("--actor", default="human:local")
    parser.add_argument("--verify", action="store_true",
                        help="перечитати джерела й підняти довіру, якщо зійшлося")
    parser.add_argument("--index-all", action="store_true",
                        help="індексувати весь репозиторій, не лише змінені шляхи")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    repo = Path(args.repo).resolve()
    actor = parse_actor(args.actor)
    reader = READERS[args.source]
    journal = Path(args.journal) if args.journal else find_journal(repo, args.source)
    if args.source == "codex" and args.journal:
        existing = os.environ.get("CAIRN_CODEX_SESSION_ROOTS", "")
        roots = [str(journal.resolve().parent)]
        if existing:
            roots.append(existing)
        os.environ["CAIRN_CODEX_SESSION_ROOTS"] = os.pathsep.join(roots)

    if not (root / ".cairn" / "manifest.json").exists():
        Store(root).init()
    store = Store.open(root)

    ident = code.git.identity(str(repo))
    store.mutate(lambda con: store.append(con, "repository.upserted", ident["id"],
                                          ident, actor, f"repo:{ident['id']}"))
    repo_id = ident["id"]
    full = code.git.git(str(repo), "rev-parse", f"{args.commit}^{{commit}}").strip()
    changed = [line.split("\t", 1)[1] for line in
               code.git.git(str(repo), "show", "--name-status", "--format=", full).splitlines()
               if "\t" in line]

    indexed = nodes.index(store, repo_id, SYSTEM,
                          paths=None if args.index_all else changed)

    events = normalize.canonical_events(reader.read(str(journal)))
    committed_keys = compaction_link.committed_node_keys(
        store, repo_id, repo, full, changed)
    ready, unresolved = compaction_link.mutation_readiness(
        store, events, repo_id, (str(repo),), committed_keys)

    descriptor = session_descriptor(events, journal)
    session_id = next((e.get("session_id") for e in reader.read(str(journal))
                       if e.get("session_id")), journal.stem)
    # A journal without structured mutations (notably Codex) still belongs
    # in the chronology of nodes touched by the explicitly supplied commit.
    # Scope is discoverability, not a caused_by assertion: that relation is
    # reserved for verified tool mutations below.
    scope_keys = {str(node["stable_key"])
                  for _event, _path, resolved in ready for node in resolved}
    is_head = code.git.head(str(repo)) == full
    for path in changed:
        for node in compaction_link.committed_nodes(
                store, repo_id, repo, full, path, is_head=is_head):
            scope_keys.add(str(node["stable_key"]))
            scope_keys.add(str(node["address"]))
    scope = sorted(scope_keys)
    session_obj = compaction_link.session_object(
        store, session_id=str(session_id), compaction=descriptor, repo_id=repo_id,
        actor=actor, commit=full, scope=scope,
        source_kind=SOURCE_KINDS[args.source],
        session_source=SOURCE_KINDS[args.source], source_ref=journal.name,
    )

    linked = compaction_link.link_mutations(
        store, events=events, session_obj_id=session_obj["id"],
        repo_id=repo_id, repo_path=(str(repo),), actor=actor,
        allowed_node_keys=committed_keys)

    rejected = compaction_link.unresolved_object(
        store, unresolved=linked["unresolved"], session_obj=session_obj,
        commit=full, repo_id=repo_id, repo_roots=[str(repo)], scope=scope,
        opencode_db="", actor=actor, source_kind=SOURCE_KINDS[args.source],
        source_ref=journal.name, mutation_filter="committed_nodes")

    commit_link = compaction_link.link_commit(
        store, repo_id=repo_id, repo_path=repo, commit=full,
        work_id=session_obj["id"], actor=actor)

    auto = compaction_link.auto_compaction(
        store, events=events, links=linked["links"], session_obj=session_obj,
        opencode_db="", actor=actor, commit=full,
        source_kind=SOURCE_KINDS[args.source], source_ref=journal.name)

    # Автоперевірка: кожен обʼєкт перечитує СВОЄ джерело (журнал, git) і
    # піднімається до `verified_system` лише якщо зійшлося. Без неї всі
    # обʼєкти лишалися б `agent_proposed` — не через недовіру, а через
    # відсутність читача для цього журналу.
    verified: dict[str, dict] = {}
    if args.verify:
        for oid in (session_obj["id"], rejected["id"], auto["object"]["id"],
                    commit_link.get("fact_id")):
            if oid:
                verified[oid] = verification.verify_object(store, oid, repo_path=repo)

    work_id = None
    if args.work:
        work_id = work.create_work(store, title=args.work, actor=actor,
                                   scope={"repo_id": repo_id},
                                   idempotency_key=f"work:{sha256_text(args.work)[:16]}",
                                   )["work"]["id"]
        for target in (session_obj["id"], rejected["id"], auto["object"]["id"],
                       commit_link.get("fact_id")):
            if target:
                semantic.relate(store, from_id=work_id, to_id=target,
                                relation="governed_by", actor=actor,
                                idempotency_key=f"gov:{work_id}:{target}")
        for key in scope:
            semantic.relate(store, from_id=work_id, to_id=key,
                            relation="targets_symbol", actor=actor,
                            idempotency_key=f"tgt:{work_id}:{key}")

    report = {
        "source": args.source,
        "capabilities": normalize.capabilities(SOURCE_KINDS[args.source]),
        "journal": journal.name,
        "session_id": str(session_id),
        "repo_id": repo_id,
        "commit": full,
        "commit_changed_paths": len(changed),
        "nodes_indexed": indexed.get("symbols") if isinstance(indexed, dict) else None,
        "events_canonical": len(events),
        "events_by_kind": {k: sum(1 for e in events if e["kind"] == k)
                           for k in sorted({e["kind"] for e in events})},
        "mutations_total": sum(1 for e in events if e["kind"] == "mutation"),
        # Дві РІЗНІ величини, які легко сплутати під одною міткою:
        # скільки мутацій дали ребра, і скільких ребер вийшло. Один файл
        # містить багато вузлів, тож одна мутація дає багато ребер.
        "mutations_resolved": len(ready),
        "edges_created": linked["new"],
        "mutations_unresolved": len(linked["unresolved"]),
        "unresolved_by_reason": rejected["semantic"].get("unresolved_by_reason"),
        "objects": {
            "session": session_obj["id"],
            "unresolved_mutations": rejected["id"],
            "commit_fact": commit_link.get("fact_id"),
            "auto_compaction": auto["object"]["id"],
        },
        "commit_mutated_nodes": len(commit_link.get("mutated") or []),
        "intent_refs": len(auto["object"]["semantic"].get("intent_refs") or []),
        "chain_shown": auto["object"]["semantic"].get("chain_shown"),
        "chain_total": auto["object"]["semantic"].get("chain_total"),
        "work_id": work_id,
        "verified": {oid: bool(res.get("verified")) for oid, res in verified.items()},
        "verified_count": sum(1 for res in verified.values() if res.get("verified")),
        "escalated": {oid: res.get("reasons") for oid, res in verified.items()
                      if not res.get("verified")},
        "boundary": (
            "Codex не видає структурованих мутацій; зв'язок із кодом "
            "походить із Git-фактів" if args.source == "codex" else
            "мутацією вважається лише Write/Edit; правки через shell "
            "журнал структурованою мутацією не фіксує"
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
