"""Причинний контекст + git history в одному відтворюваному проході.

Один виклик ``link()`` будує чотири речі й не підміняє одну іншою:

1. ``node --caused_by--> session`` — участь у часі/шляху, підтверджена
   mutation-подією; корінь «чому» — слова користувача (`user_intent`).
2. ``auto_compaction --derived_from--> session`` — детермінований зріз
   тих самих подій, який НЕ є компактацією opencode.db.
3. ``commit_fact --mutated--> node`` — окремий CrUD-факт із ``git show``;
   коміт пізніший за мутацію й причиною не є.
4. ``unresolved_mutations`` і ``zone_descriptors`` — окремі факти, а не тихий
   лог; разом із сесією, комітом і автокомактацією вони входять у контекст
   як ``governed_by`` (не в HISTORY-timeline).

Автор тексту — агент/система, тому об'єкти створюються як ``agent_proposed``.
``verify_object`` може підвищити їх до ``verified_system`` лише повторним
читанням реальних джерел (opencode.db у mode=ro + git rev-parse), ніколи
не звіряючи об'єкт самого з собою. Нерезолвоване лишається в звіті.
"""

from __future__ import annotations

from pathlib import Path

from . import code, compaction_link, context, nodes, semantic, verification
from .canon import sha256_text

GENESIS = "causal_history/1.0"


def _git(repo: str | Path, *args: str) -> str:
    """Через `code.git`, не через subprocess напряму.

    Прямий виклик обходив allow-list дозволених verbs у `code/git.py` —
    тобто одні з двох дверей у git не перевіряли нічого.
    """
    return code.git.git(str(repo), *args).strip()


def _changed(repo: str | Path, commit: str) -> list[tuple[str, str]]:
    out = _git(repo, "show", "--name-status", "--format=", commit)
    result = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0][:1] in {"A", "M", "D", "R"}:
            result.append((parts[0][:1], parts[-1]))
    return result


def _anchor(events: list[dict], commit_ms: int) -> tuple[dict | None, bool]:
    """Остання компактація перед комітом; булева — чи вона справді передує.

    Якщо компактацій до коміту немає, беремо останню наявну, але позначаємо
    ``before_commit=False`` — це не вигадуємо передування, якого не було.
    """
    comps = sorted((e for e in events if e.get("kind") == "compaction"),
                   key=lambda e: (int(e.get("ts") or 0), int(e.get("seq") or 0)))
    prior = [e for e in comps if int(e.get("ts") or 0) <= commit_ms]
    if prior:
        return prior[-1], True
    return (comps[-1], False) if comps else (None, False)


def link(store, *, repo_path: str | Path, session_id: str, commit: str,
         work_id: str, actor: dict, opencode_db: str,
         repo_roots: tuple[str | Path, ...] = (),
         auto_verify: bool = True, build_context: bool = True,
         budget_chars: int | None = None,
         zone_specs: tuple[tuple[str, str, str], ...] | None = None) -> dict:
    """Зв'язати сесію, мутації, коміт і контекст.Ідемпотентно за ключами ребер."""
    from .adapters import opencode  # локальний імпорт: без нього ядро чисте
    from . import zones as zone_mod

    full = _git(repo_path, "rev-parse", f"{commit}^{{commit}}")
    commit_ms = int(_git(repo_path, "show", "-s", "--format=%ct", full)) * 1000
    changed = _changed(repo_path, full)

    ident = code.git.identity(str(repo_path))
    store.mutate(lambda con: store.append(
        con, "repository.upserted", ident["id"], ident, actor, f"repo:{ident['id']}"))
    repo_id = ident["id"]

    present: list[str] = []
    for _st, p in changed:
        for variant in compaction_link.path_variants(p, repo_path):
            if (Path(repo_path) / variant).is_file() and variant not in present:
                present.append(variant)
    indexed = nodes.index(store, repo_id, actor, paths=present)
    resolved: dict[str, list[dict]] = {p: nodes.resolve(store, repo_id, p) for p in present}
    addresses = sorted({n["address"] for group in resolved.values() for n in group})
    stable_keys = sorted({str(n["stable_key"]) for group in resolved.values()
                          for n in group})
    scope = sorted(set(addresses) | set(stable_keys))

    events = list(opencode.read(opencode_db, session_id))
    committed_keys = compaction_link.committed_node_keys(
        store, repo_id, repo_path, full, [path for _status, path in changed])
    anchor, anchor_before = _anchor(events, commit_ms)
    if anchor is None:
        return {"ok": False, "reason": "no_compaction_in_session",
                "session_id": session_id, "commit": full}

    session_obj = compaction_link.session_object(
        store, session_id=session_id, compaction=anchor, repo_id=repo_id,
        actor=actor, commit=full, opencode_db=opencode_db, scope=scope)
    roots = (Path(repo_path), *[Path(r) for r in repo_roots])
    mutations = compaction_link.link_mutations(
        store, events=events, session_obj_id=session_obj["id"], repo_id=repo_id,
        repo_path=roots, actor=actor, allowed_node_keys=committed_keys)
    rejected = compaction_link.unresolved_object(
        store, unresolved=mutations["unresolved"], session_obj=session_obj,
        commit=full, repo_id=repo_id, repo_roots=[str(r) for r in roots],
        scope=scope, opencode_db=opencode_db, actor=actor,
        mutation_filter="committed_nodes")
    commit_link = compaction_link.link_commit(
        store, repo_id=repo_id, repo_path=repo_path, commit=full,
        work_id=work_id, actor=actor)
    auto = compaction_link.auto_compaction(
        store, events=events, links=mutations["links"], session_obj=session_obj,
        opencode_db=opencode_db, actor=actor, commit=full)

    # Було: дефолтна зона з іменем "cairn" і маскою "*.py" — назва ЦЬОГО
    # репозиторію в загальному модулі. Тепер зона береться з імені теки
    # репозиторію, а маска — з розширення файлів, що змінилися: ні те,
    # ні інше не вигадується.
    if zone_specs:
        specs = tuple(zone_specs)
    else:
        suffixes = {Path(path).suffix for _status, path in changed if Path(path).suffix}
        mask = f"*{sorted(suffixes)[0]}" if len(suffixes) == 1 else "*"
        specs = ((Path(repo_path).name, str(repo_path), mask),)
    refs = zone_mod.object_refs(
        store, [session_obj["id"], str(rejected["id"]),
                str(auto["object"]["id"])]
        + ([str(commit_link["fact_id"])] if commit_link.get("fact_id") else []))
    sem = session_obj.get("semantic") or {}
    identity = {
        "session_id": str(sem.get("session_id") or session_id),
        "compaction_message_id": str(sem.get("compaction_message_id") or ""),
        "summary_sha256": str(sem.get("summary_sha256") or ""),
        "keep_id": str(sem.get("keep_id") or ""),
    }
    zone_names: list[str] = []
    for name, _repo, spec in specs:
        # Portability: дескриптор несе стабільну ідентичність репозиторію
        # й відносні посилання на сесію/summary, а не абсолютний шлях машини.
        zone_mod.descriptor(store, name, repo=repo_id, pathspec=spec, actor=actor,
                            context_refs=refs, context_identity=identity)
        zone_names.append(name)
    zone_obj, zone_rows = zone_mod.context_object(
        store, zones=zone_names, actor=actor, commit=full, scope=scope)

    objects = [session_obj["id"], str(rejected["id"]), str(zone_obj["id"])]
    if commit_link.get("fact_id"):
        objects.append(commit_link["fact_id"])
    objects.append(str(auto["object"]["id"]))
    governed = 0
    for oid in objects:
        result = semantic.relate(
            store, from_id=work_id, to_id=oid, relation="governed_by", actor=actor,
            idempotency_key=f"causal-governed:{GENESIS}:{work_id}:{oid}")
        governed += 0 if result.get("replayed") else 1
    for key in stable_keys:
        semantic.relate(
            store, from_id=work_id, to_id=key, relation="targets_symbol", actor=actor,
            idempotency_key=f"causal-target:{GENESIS}:{work_id}:{key}")

    verified: dict[str, dict] = {}
    if auto_verify:
        for oid in objects:
            # Шлях до БД передається явно: у payload його більше немає.
            verified[oid] = verification.verify_object(
                store, oid, repo_path=repo_path, opencode_db=opencode_db)

    package = context.build(store, work_id, purpose="understand",
                            budget_chars=budget_chars,
                            use_cache=False) if build_context else None
    included = {str(item.get("object_id") or "")
                for item in (package or {}).get("items") or []}
    shown = [oid for oid in objects if oid in included]

    return {
        "ok": True, "generator": GENESIS, "commit": full, "commit_head": ident["head"],
        "session_id": session_id, "anchor_seq": anchor.get("seq"),
        "anchor_message_id": anchor.get("message_id"), "anchor_before_commit": anchor_before,
        "repo_id": repo_id, "indexed_paths": present, "index_digest": indexed.get("index_digest"),
        "causal_links": mutations["new"], "causal_replayed": mutations["replayed"],
        "causal_unresolved": mutations["unresolved"],
        "unresolved_count": len(mutations["unresolved"]),
        "unresolved_by_reason": rejected["semantic"]["unresolved_by_reason"],
        "unresolved_object_id": str(rejected["id"]),
        "zone_descriptors": zone_rows, "zone_object_id": str(zone_obj["id"]),
        "history_mutated": commit_link["mutated"], "history_unresolved": commit_link["unresolved"],
        "auto_compaction_intents": auto["intents"], "auto_compaction_text": auto["text"],
        "objects": objects, "governed_new": governed, "verified": verified,
        "context_id": (package or {}).get("context_id"),
        "context_digest": (package or {}).get("integrity_digest"),
        "context_shown": shown,
        "summary_sha256": sha256_text("|".join(objects) + full),
    }
