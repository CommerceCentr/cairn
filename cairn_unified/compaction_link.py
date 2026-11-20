"""Зв'язок зжатої сесії з вузлами через реальні події CrUD.

Ланцюг «чому» має два Coryня, і обидва з реальних подій:

1. `user_intent` — повідомлення користувача, яке сформулювало намір
   (виміряно: seq 10564 о 21:50:44, мутації цієї правки — від 21:51:19);
2. `reasoning` тієї ж assistant-події, що й виклик `edit`/`write`
   (виміряно: 18 з 29 релевантних подій мають мотив).

Компактації 10024/10371 згадують коміт лише як стан checkout’у, тож не є
джерелом причини. Ребро несе `intent` і `rationale` окремо, з маркерами
походження — це закриває ланцюг зверху, не імпортуючи сирі повідомлення.

Правила, закладені у коді:

* `caused_by` ставиться ТІЛЬКИ для `status == "completed"` мутацій, чий шлях
  резолвиться в індексований вузол. `read`/`grep`/`glob` адаптер не віддає.
* Коміт ніколи не є причиною: він пізніший за мутацію (виміряно ~19 год для
  `8c1eea5`). Коміт дає окреме ребро `mutated` через `git show --name-status`.
* Нерезолвований шлях/вузол/коміт — не вигадане ребро, а `unresolved` у
  звіті; людина втручається тільки тут.
* Всі створення ідемпотентні за `event_id` + address: повтор не подвоює.
"""

from __future__ import annotations

import bisect
import hashlib
from pathlib import Path

from . import nodes, semantic
from .canon import canonical, sha256_text
from .code import git

#: Repository-relative prefix normalization. Tool paths are absolute
#: Windows paths; node paths in `code_symbols` are repo-relative POSIX.
_SEP = "\\"


def path_variants(path: str, repo_path: str | Path) -> list[str]:
    """Історичний шлях + його канонічний варіант після переносу в корінь.

    Коміти й події зберегли `src/cairn_unified/...`, але пакет тепер лежить
    у корені. Варіант додається лише тоді, коли файл справді існує, — не
    за припущенням. Це дає змогу резолвити той самий вузол на обох машинах.
    """
    out = [str(path)]
    if str(path).startswith("src/cairn_unified/"):
        moved = str(path)[len("src/"):]
        if (Path(repo_path) / moved).is_file():
            out.append(moved)
    return out


def normalize_path(path: str, repo_path: str | Path | tuple) -> str | None:
    """Абсолютний шлях події -> repo-відносний, або None якщо поза репо.

    Приймає кілька коренів: одна сесія може правити публічний і приватний
    checkout того самого repo_id (виміряно: 8c1eea5 у `cairn`, а мутації цієї
    правки — у `cairn-private`). Відносний шлях однаковий, тож обидва корені
    мають зіставлятися з тим самим індексом вузлів.
    """
    raw = str(path).replace("/", _SEP)
    roots = repo_path if isinstance(repo_path, (tuple, list)) else (repo_path,)
    for root in roots:
        prefix = str(root).replace("/", _SEP).rstrip(_SEP) + _SEP
        if raw.lower().startswith(prefix.lower()):
            rel = raw[len(prefix):].replace(_SEP, "/")
            # Після переносу пакета з src/ у корінь історичні події все ще
            # несуть старий префікс. Перевіряємо файл у ВСІХ коренях, а не
            # лише в корені події: мутація сталася в приватному checkoutі зі
            # старою розкладкою, а індекс вузлів будується за новою (публічною).
            # Виміряно: інакше 11025 випадав з індексу й намір 10987 губився.
            if rel.startswith("src/cairn_unified/"):
                moved = rel[len("src/"):]
                if any((Path(r) / moved).is_file() for r in roots):
                    rel = moved
            return rel
    return None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _intents(events: list[dict]) -> tuple[list[dict], list[int]]:
    """User-намири, відсортовані за seq, + масив seq для бісекту."""
    ints = sorted((e for e in events if e.get("kind") == "user_intent"),
                  key=lambda e: (int(e.get("seq") or 0), str(e.get("event_id") or "")))
    return ints, [int(e.get("seq") or 0) for e in ints]


def _prior_intent(ev: dict, intents: list[dict], seqs: list[int]) -> dict | None:
    """Найближчий попередній намір: найбільший seq, строго менший за seq події.

    Не використовуємо час: `seq` у журналі монотонний, а час мутації може
    розходитись із часом повідомлення через буферизацію. Це прибирає
    евристику з порога й робить вибір відтворюваним.
    """
    i = bisect.bisect_right(seqs, int(ev.get("seq") or 0)) - 1
    return intents[i] if i >= 0 else None


def mutation_readiness(store, events: list[dict], repo_id: str,
                       repo_path: str | Path | tuple) -> tuple[list[tuple], list[dict]]:
    """Єдиний класифікатор мутацій: готові до ребра й відкинуті з причиною.

    Використовується і при побудові ребер, і верифікатором. Інакше «чому
    відкинуто» обчислювалося б двома різними шляхами й розійшлося б.
    """
    ready: list[tuple] = []
    unresolved: list[dict] = []
    for ev in events:
        if ev.get("kind") != "mutation":
            continue
        if str(ev.get("status")) != "completed":
            unresolved.append({"event_id": ev.get("event_id"),
                               "reason": "mutation_not_completed"})
            continue
        rel = normalize_path(ev.get("path") or "", repo_path)
        if not rel:
            unresolved.append({"event_id": ev.get("event_id"),
                               "reason": "path_outside_repo", "path": ev.get("path")})
            continue
        resolved = nodes.resolve(store, repo_id, rel)
        if not resolved:
            unresolved.append({"event_id": ev.get("event_id"),
                               "reason": "node_not_indexed", "path": rel})
            continue
        ready.append((ev, rel, resolved))
    return ready, unresolved


def unresolved_digest(unresolved: list[dict]) -> str:
    """Стабільний відтисок відкинутого: (event_id, причина, шлях)."""
    rows = sorted((str(u.get("event_id") or ""), str(u.get("reason") or ""),
                   str(u.get("path") or "")) for u in unresolved)
    return sha256_text(canonical(rows))


def session_object(store, *, session_id: str, compaction: dict,
                   repo_id: str, actor: dict, commit: str = "",
                   opencode_db: str = "", scope: list[str] | None = None) -> dict:
    """Створити/отримати session-об'єкт для однієї компактації.

    Тотожність — реальний рядок компактації (`message_id`), НЕ SHA коміту.
    `session_id` береться з БД, а не виводиться з diff. `opencode_db` — шлях
    до того самого рядка, який верифікатор перечитає у mode=ro. `scope` —
    адреси вузлів, знайдені з мутацій; без них контекст не покаже сесію
    в історії цього вузла.
    """
    summary = compaction.get("summary") or ""
    key = f"ses:{session_id}:cmp:{compaction.get('message_id') or compaction.get('event_id')}"
    stable = _digest(key)
    return semantic.put_object(
        store, object_type="session",
        title=f"compaction {compaction.get('seq')} {session_id}",
        summary=summary, actor=actor, lifecycle="proposed", trust="agent_proposed",
        stable_key=stable,
        idempotency_key=f"session-cmp:{stable}",
        semantic={
            "kind": "session", "session_source": "opencode_db",
            "session_id": session_id,
            "compaction_message_id": compaction.get("message_id") or "",
            "compaction_seq": compaction.get("seq"),
            "keep_id": compaction.get("keep_id") or "",
            "summary_sha256": sha256_text(summary),
            "reason": compaction.get("reason") or "",
            "commit": commit,
            "opencode_db": opencode_db,
        },
        scope=list(scope or []),
    )["object"]


def link_mutations(store, *, events: list[dict], session_obj_id: str,
                   repo_id: str, repo_path: str | Path | tuple, actor: dict) -> dict:
    """Побудувати `caused_by` для кожної завершеної мутації з відомим вузлом.

    Повертає звіт: links ( нові ребра), replayed, unresolved (список причин).
    """
    reasons_by_event: dict[str, str] = {}
    for ev in events:
        if ev.get("kind") == "reasoning" and ev.get("text"):
            reasons_by_event[str(ev["event_id"])] = str(ev["text"])
    intents, intent_seqs = _intents(events)

    links: list[dict] = []
    replayed = 0
    seen: set[tuple[str, str]] = set()
    ready, unresolved = mutation_readiness(store, events, repo_id, repo_path)

    for ev, rel, resolved in ready:
        rationale = reasons_by_event.get(str(ev["event_id"]), "")
        intent = _prior_intent(ev, intents, intent_seqs)
        before = ev.get("old_string") or ""
        after = ev.get("new_string") or ""
        evidence = {
            "event_id": ev.get("event_id"), "tool": ev.get("tool"),
            "ts": ev.get("ts"), "path": rel,
            "before_sha256": sha256_text(before) if before else "",
            "after_sha256": sha256_text(after) if after else "",
            "rationale_source": "agent_reasoning" if rationale else "none",
            # Корінь «чому» — слова користувача, не агента. Якщо наміру немає,
            # поле порожнє з явним маркером: не підставляємо reasoning замість
            # нього, бо це підвищило б довіру власного тексту.
            "intent": str(intent.get("text") or "") if intent else "",
            "intent_message_id": str(intent.get("message_id") or "") if intent else "",
            "intent_seq": intent.get("seq") if intent else None,
            "intent_source": "user_message" if intent else "none",
        }
        for node in resolved:
            pair = (str(ev["event_id"]), str(node["stable_key"]))
            if pair in seen:
                continue
            seen.add(pair)
            result = semantic.relate(
                store, from_id=str(node["stable_key"]), to_id=session_obj_id,
                relation="caused_by", actor=actor,
                condition={
                    "basis": "tool_mutation", "evidence_level": "participation",
                    "evidence": evidence,
                    "rationale": rationale or "",
                    "intent_source": evidence["intent_source"],
                },
                idempotency_key=f"caused-by:{pair[0]}:{pair[1]}",
            )
            if result.get("replayed"):
                replayed += 1
            else:
                links.append({"event_id": ev["event_id"], "address": node["address"],
                              "stable_key": str(node["stable_key"]),
                              "path": rel, "rationale_chars": len(rationale),
                              "intent_seq": evidence["intent_seq"]})
    return {"links": links, "new": len(links), "replayed": replayed,
            "unresolved": unresolved}


def unresolved_object(store, *, unresolved: list[dict], session_obj: dict,
                      commit: str, repo_id: str, repo_roots: list[str],
                      scope: list[str], opencode_db: str, actor: dict) -> dict:
    """«Чому відкинуто» — окремий перевірений об'єкт, не тихий лог.

    Причини рахуються з того самого класифікатора, що й ребра; верифікатор
    перераховує їх із реальної БД і звіряє digest. Об'єкт не може свідчити
    сам за себе.
    """
    counts: dict[str, int] = {}
    examples: dict[str, list[str]] = {}
    for item in unresolved:
        reason = str(item.get("reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
        if len(examples.setdefault(reason, [])) < 3:
            examples[reason].append(str(item.get("path") or item.get("event_id") or ""))
    lines = [
        "Відкинуті мутації сесії (не є ребрами й не є причинами).",
        f"Коміт: {commit[:12]}",
        f"Сесія: {str(session_obj['semantic'].get('session_id') or '')}",
        "Кожна причина — результат перевірки статусу/шляху/індексації вузла.",
        "Повний перелік (event_id, причина, шлях) у semantic.unresolved_rows.",
        "",
    ]
    for reason in sorted(counts):
        lines.append(f"[{reason}] {counts[reason]}: " + ", ".join(examples[reason]))
    text = "\n".join(lines) + "\n"
    stable = _digest(f"unresolved:v1:{commit}:{unresolved_digest(unresolved)}")
    return semantic.put_object(
        store, object_type="observation",
        title=f"unresolved mutations {commit[:7]}",
        summary=text, actor=actor, lifecycle="proposed", trust="agent_proposed",
        stable_key=stable, idempotency_key=f"unresolved-obj:{stable}",
        semantic={
            "kind": "unresolved_mutations", "commit": commit, "repo_id": repo_id,
            "session_id": str(session_obj["semantic"].get("session_id") or ""),
            "session_obj_id": str(session_obj["id"]),
            "opencode_db": opencode_db, "repo_roots": list(repo_roots),
            "unresolved_count": len(unresolved),
            "unresolved_by_reason": counts,
            "unresolved_digest": unresolved_digest(unresolved),
            "unresolved_rows": sorted(
                (str(u.get("event_id") or ""), str(u.get("reason") or ""),
                 str(u.get("path") or "")) for u in unresolved),
        },
        scope=scope,
    )["object"]


def link_commit(store, *, repo_id: str, repo_path: str | Path, commit: str,
                work_id: str, actor: dict) -> dict:
    """CrUD коміту -> `commit_fact` і `mutated`; коміт не є причиною сесії.

    Повертає `resolved`, `mutated`, `unresolved`. Нерезолвований SHA в
    звіт, не в граф: 4 пакові digest'и з seq 9295 сюди не потраплять.
    """
    try:
        full = git.git(str(repo_path), "rev-parse", f"{commit}^{{commit}}").strip()
    except Exception:
        return {"resolved": "", "mutated": [], "unresolved": ["commit_not_found"]}
    if not full:
        return {"resolved": "", "mutated": [], "unresolved": ["commit_not_found"]}
    status = git.git(str(repo_path), "show", "--name-status", "--format=", full)
    changed = []
    for line in status.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0][:1] in {"A", "M", "D", "R"}:
            changed.append((parts[0][:1], parts[-1]))
    found: list[tuple[str, str, dict]] = []
    unresolved = []
    scope: set[str] = set()
    for status_code, path in changed:
        # Після переносу пакета в корінь старий шлях коміту не резолвиться.
        # Резолвимо обидва варіанти й дедупликуємо за stable_key, інакше
        # той самий вузол дав би два `mutated`-ребра з різними адресами.
        grouped: dict[str, dict] = {}
        for variant in path_variants(path, repo_path):
            for node in nodes.resolve(store, repo_id, variant):
                grouped[str(node["stable_key"])] = node
        if not grouped:
            unresolved.append({"path": path, "reason": "node_not_indexed"})
            continue
        for node in grouped.values():
            found.append((status_code, path, node))
            scope.add(str(node["address"]))
            scope.add(str(node["stable_key"]))
    fact = semantic.put_object(
        store, object_type="fact",
        title=f"commit {full[:7]}",
        summary=f"commit {full[:7]} CrUD: {len(changed)} paths",
        actor=actor, lifecycle="proposed", trust="agent_proposed",
        stable_key=_digest(f"commit:{repo_id}:{full}"),
        idempotency_key=f"commit-fact-obj:{full}",
        semantic={"kind": "commit_fact", "commit": full, "basis": "git_diff"},
        scope=sorted(scope),
    )["object"]
    mutated = []
    for status_code, path, node in found:
        result = semantic.relate(
            store, from_id=fact["id"], to_id=str(node["stable_key"]),
            relation="mutated", actor=actor,
            condition={"basis": "git_diff", "status": status_code, "commit": full},
            idempotency_key=f"mutated:{full}:{node['stable_key']}",
        )
        if not result.get("replayed"):
            mutated.append({"path": path, "address": node["address"],
                            "status": status_code})
    return {"resolved": full, "fact_id": fact["id"], "mutated": mutated,
            "unresolved": unresolved}


def auto_compaction(store, *, events: list[dict], links: list[dict],
                    session_obj: dict, opencode_db: str, actor: dict,
                    commit: str = "") -> dict:
    """Детермінована автокомактація: «чому» дослівно з реальних подій сесії.

    НЕ є компактацією opencode.db і не видає себе за неї: окремий
    `object_type="decision"`, `kind="auto_compaction"`, явний `generator`.
    Текст складається лише з полів уже зчитаних подій — жодного власного
    тексту агента як доказу. Слова користувача й мотив агента лежать окремо
    з маркерами `user` / `agent_reasoning`, щоб довіра не підвищувалась
    мовчки. Джерело перевіряється верифікатором повторним читанням БД.
    """
    intents, seqs = _intents(events)
    reasons = {str(e["event_id"]): str(e.get("text") or "")
               for e in events if e.get("kind") == "reasoning" and e.get("text")}
    muts = {str(e["event_id"]): e for e in events if e.get("kind") == "mutation"}
    session_id = str(session_obj["semantic"].get("session_id") or "")
    cmp_msg = str(session_obj["semantic"].get("compaction_message_id") or "")

    ordered = sorted(links, key=lambda l: (
        int(muts.get(str(l["event_id"]), {}).get("seq") or 0),
        str(l["event_id"]), str(l["address"])))
    # Текст мусить лишатися обмеженим: 482 ребра з повним наміром у кожному
    # дали 620 KB й не влізли б у жоден контекст. Усі наміри лишаються
    # дослівно один раз у шапці, усі event_ids — у semantic, а тут лише
    # перші ребра ланцюга + явна позначка пропуску.
    MAX_CHAIN = 12
    shown_chain = ordered[:MAX_CHAIN]

    intent_refs: list[dict] = []
    seen: set[str] = set()
    chain: list[str] = []
    # Наміри збираємо з УСІХ ребер: інакше обмеження ланцюга викинуло б
    # пізні наміри, яких немає серед перших 12 мутацій (виміряно: 10564/10987).
    for link in ordered:
        ev = muts.get(str(link["event_id"]), {})
        prior = _prior_intent(ev, intents, seqs)
        if prior and str(prior.get("message_id") or "") not in seen:
            seen.add(str(prior["message_id"]))
            intent_refs.append({
                "message_id": str(prior["message_id"]), "seq": prior["seq"],
                "text_sha256": sha256_text(str(prior.get("text") or "")),
            })
    for link in shown_chain:
        ev = muts.get(str(link["event_id"]), {})
        prior = _prior_intent(ev, intents, seqs)
        reason = reasons.get(str(link["event_id"]), "")
        chain.extend([
            f"[{link['event_id']}]",
            f"  намір (user, seq {prior['seq'] if prior else '-'}): "
            f"{prior['text'] if prior else '(не встановлено)'}",
            f"  мотив (agent_reasoning): {reason or '(відсутній у події)'}",
            f"  мутація: {ev.get('tool', '?')} {link['path']} -> {link['address']}",
        ])

    header = [
        "Автокомактація причини (generated: compaction_link/auto_compaction/1.1)",
        "НЕ є компактацією opencode.db; джерело — реальні події сесії (mode=ro).",
        f"Сесія: {session_id}",
        f"Компактація-якір: {cmp_msg}",
        "Підстава: user_intent + reasoning тієї самої події; коміт і зжата "
        "компактація причиною не є.",
        "",
        "ЧОМУ ПРИВ'ЯЗАНО REASONING (дослівно з повідомлень користувача):",
    ]
    by_id = {str(i.get("message_id") or ""): i for i in intents}
    for ref in intent_refs:
        prior = by_id[ref["message_id"]]
        header.append(f"[seq {ref['seq']}, {ref['message_id']}] {prior['text']}")
    header += ["", "ЛАНЦЮГ НАМІР -> МОТИВ -> МУТАЦІЯ -> ВУЗОЛ:"]
    if len(ordered) > len(shown_chain):
        chain.append(f"... +{len(ordered) - len(shown_chain)} мутацій; "
                     "повний перелік event_ids у semantic.mutation_event_ids")
    text = "\n".join(header + chain) + "\n"

    stable = _digest(f"auto-cmp:v1.1:{session_id}:{cmp_msg or session_obj['id']}")
    obj = semantic.put_object(
        store, object_type="decision",
        title=f"автокомактація чому {session_id}",
        summary=text, actor=actor, lifecycle="proposed", trust="agent_proposed",
        stable_key=stable, idempotency_key=f"auto-cmp:{stable}",
        semantic={
            "kind": "auto_compaction", "session_id": session_id,
            "session_obj_id": str(session_obj["id"]),
            "commit": commit,
            "compaction_message_id": cmp_msg,
            "basis": "opencode_events", "generator": "compaction_link/auto_compaction/1.1",
            "chain_shown": len(shown_chain), "chain_total": len(ordered),
            "summary_sha256": sha256_text(text), "opencode_db": opencode_db,
            "intent_refs": intent_refs,
            "mutation_event_ids": [str(l["event_id"]) for l in ordered],
            "linked_nodes": len(ordered),
        },
        scope=sorted({str(l["address"]) for l in ordered}
                     | {str(l.get("stable_key") or "") for l in ordered
                        if l.get("stable_key")}),
    )["object"]
    relation = semantic.relate(
        store, from_id=str(obj["id"]), to_id=str(session_obj["id"]),
        relation="derived_from", actor=actor,
        condition={"basis": "auto_compaction", "source": "opencode_events"},
        idempotency_key=f"auto-cmp-derived:{obj['id']}",
    )
    return {"object": obj, "text": text, "intents": intent_refs,
            "relation": relation, "links": len(ordered)}
