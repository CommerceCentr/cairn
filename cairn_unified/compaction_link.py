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
import json
import re
from pathlib import Path

from . import nodes, semantic
from .ontology import DEFAULTS
from .canon import canonical, sha256_text
from .errors import CairnError
from .code import git

#: Repository-relative prefix normalization. Tool paths are absolute
#: Windows paths; node paths in `code_symbols` are repo-relative POSIX.
_SEP = "\\"


#: Типове правило переносу. Береться з `DEFAULTS["path_rewrites"]`, щоб
#: значення не існувало у двох місцях.
DEFAULT_REWRITES = tuple(DEFAULTS["path_rewrites"])


def parse_rewrites(values) -> tuple[tuple[str, str], ...]:
    """`["старий/->новий/"]` -> кортеж пар. Непарні записи ігноруються."""
    out: list[tuple[str, str]] = []
    for item in values or ():
        text = str(item)
        if "->" not in text:
            continue
        old, new = text.split("->", 1)
        if old:
            out.append((old, new))
    return tuple(out)


def path_variants(path: str, repo_path: str | Path, *,
                  rewrites=DEFAULT_REWRITES) -> list[str]:
    """Історичний шлях + його варіанти після переносу теки.

    Правило переносу приходить КОНФІГОМ (`path_rewrites`), а не прошите в
    коді: раніше тут стояв `src/cairn_unified/` -> корінь, тобто правило
    одного репозиторію жило в загальному модулі й на чужому репозиторії
    нічого не означало.

    Варіант додається лише тоді, коли файл СПРАВДІ існує, — не за
    припущенням.
    """
    out = [str(path)]
    for old, new in parse_rewrites(rewrites):
        if str(path).startswith(old):
            moved = new + str(path)[len(old):]
            if moved != str(path) and (Path(repo_path) / moved).is_file():
                out.append(moved)
    return out


def normalize_path(path: str, repo_path: str | Path | tuple, *,
                   rewrites=DEFAULT_REWRITES) -> str | None:
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
            for old, new in parse_rewrites(rewrites):
                if rel.startswith(old):
                    moved = new + rel[len(old):]
                    if moved != rel and any((Path(r) / moved).is_file() for r in roots):
                        rel = moved
                        break
            return rel
    return None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def patch_lines(diff: str) -> tuple[list[str], list[str]]:
    """Read changed text only inside Git hunks, preserving prose verbatim."""
    deleted: list[str] = []
    added: list[str] = []
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
        elif line.startswith("@@ "):
            in_hunk = True
        elif in_hunk and line.startswith("-"):
            deleted.append(line[1:])
        elif in_hunk and line.startswith("+"):
            added.append(line[1:])
    return deleted, added


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


def _prior_reasoning(ev: dict, items: list[dict], seqs: list[int]) -> dict | None:
    """Найближчий попередній мотив агента. Той самий механізм, що й наміри."""
    if not seqs:
        return None
    i = bisect.bisect_right(seqs, int(ev.get("seq") or 0)) - 1
    return items[i] if i >= 0 else None


def matching_commit_content(events: list[dict], repo: str | Path,
                            commit: str) -> tuple[list[dict], int]:
    """Keep Claude Edit/Write events whose replacement survives the commit."""
    try:
        parent = git.git(str(repo), "rev-parse", f"{commit}^").strip()
    except CairnError:
        parent = ""
    cache: dict[str, tuple[str, str]] = {}
    kept = []
    excluded = 0
    for event in events:
        if event.get("kind") != "mutation":
            kept.append(event)
            continue
        rel = normalize_path(event.get("path") or "", repo)
        if not rel:
            excluded += 1
            continue
        if rel not in cache:
            try:
                final = git.git(str(repo), "show", f"{commit}:{rel}")
            except CairnError:
                final = ""
            try:
                before = git.git(str(repo), "show", f"{parent}:{rel}") if parent else ""
            except CairnError:
                before = ""
            cache[rel] = before, final
        before, final = cache[rel]
        old = str(event.get("old_string") or "")
        new = str(event.get("new_string") or "")
        tool = str(event.get("tool") or "").lower()
        matched = bool(new and final and (
            (tool == "edit" and old and old in before
             and new in final and new not in before)
            or (tool == "write" and new == final and new != before)))
        if matched:
            kept.append(event)
        else:
            excluded += 1
    return kept, excluded


def mutation_readiness(store, events: list[dict], repo_id: str,
                       repo_path: str | Path | tuple,
                       allowed_node_keys: set[str] | None = None) -> tuple[list[tuple], list[dict]]:
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
        eligible = ([node for node in resolved
                     if str(node["stable_key"]) in allowed_node_keys]
                    if allowed_node_keys is not None else resolved)
        if allowed_node_keys is not None and not eligible:
            unresolved.append({"event_id": ev.get("event_id"),
                               "reason": "node_not_changed_in_commit",
                               "path": rel})
            continue
        if ev.get("evidence_scope") == "file":
            resolved = [node for node in resolved
                        if str(node.get("address") or "").startswith("whole:")]
            if not resolved:
                unresolved.append({"event_id": ev.get("event_id"),
                                   "reason": "file_node_not_indexed", "path": rel})
                continue
        else:
            resolved = eligible
        ready.append((ev, rel, resolved))
    return ready, unresolved


def unresolved_digest(unresolved: list[dict]) -> str:
    """Стабільний відтисок відкинутого: (event_id, причина, шлях)."""
    rows = sorted((str(u.get("event_id") or ""), str(u.get("reason") or ""),
                   str(u.get("path") or "")) for u in unresolved)
    return sha256_text(canonical(rows))


def session_object(store, *, session_id: str, compaction: dict,
                   repo_id: str, actor: dict, commit: str = "",
                   opencode_db: str = "", scope: list[str] | None = None,
                   source_kind: str = "opencode",
                   session_source: str = "opencode_db",
                   source_ref: str = "", source_after: str = "",
                   commit_scoped: bool = False) -> dict:
    """Створити/отримати session-об'єкт для однієї компактації.

    Тотожність — реальний рядок компактації (`message_id`), НЕ SHA коміту.
    `session_id` береться з БД, а не виводиться з diff. `opencode_db` — шлях
    до того самого рядка, який верифікатор перечитає у mode=ro. `scope` —
    адреси вузлів, знайдені з мутацій; без них контекст не покаже сесію
    в історії цього вузла.
    """
    summary = compaction.get("summary") or ""
    key = f"ses:{session_id}:cmp:{compaction.get('message_id') or compaction.get('event_id')}"
    if commit_scoped:
        key += f":commit:{commit}"
    stable = _digest(key)
    return semantic.put_object(
        store, object_type="session",
        title=f"compaction {compaction.get('seq')} {session_id}",
        summary=summary, actor=actor, lifecycle="proposed", trust="agent_proposed",
        stable_key=stable,
        idempotency_key=f"session-cmp:{stable}",
        semantic={
            # Звідки саме ця сесія. Було прошито "opencode_db" для будь-якого
            # джерела: обʼєкт сесії Claude Code брехав би про власне
            # походження, а верифікатор шукав би її в чужій БД.
            "kind": "session", "session_source": session_source,
            "session_id": session_id,
            "compaction_message_id": compaction.get("message_id") or "",
            "compaction_seq": compaction.get("seq"),
            "source_occurred_at": compaction.get("ts") or "",
            "account_verification": "unavailable_in_source_journal",
            "keep_id": compaction.get("keep_id") or "",
            "summary_sha256": sha256_text(summary),
            "reason": compaction.get("reason") or "",
            "commit": commit,
            # Абсолютний шлях у payload розкривав імʼя користувача й їхав
            # у bundle. Розташування джерела — локальна річ: його дає
            # викликач, а не збережений обʼєкт. `source_ref` — ВІДНОСНЕ
            # імʼя (назва файла журналу), щоб було видно, що саме читати.
            "source_kind": source_kind,
            "source_ref": source_ref,
            "source_after": source_after,
        },
        scope=list(scope or []),
    )["object"]


def link_mutations(store, *, events: list[dict], session_obj_id: str,
                   repo_id: str, repo_path: str | Path | tuple, actor: dict,
                   allowed_node_keys: set[str] | None = None,
                   compact_relations: bool = False) -> dict:
    """Побудувати `caused_by` для кожної завершеної мутації з відомим вузлом.

    Повертає звіт: links ( нові ребра), replayed, unresolved (список причин).
    """
    reasons_by_event: dict[str, str] = {}
    for ev in events:
        if ev.get("kind") == "reasoning" and ev.get("text"):
            reasons_by_event[str(ev["event_id"])] = str(ev["text"])
    intents, intent_seqs = _intents(events)

    links: list[dict] = []
    all_links: list[dict] = []
    pending: list[tuple[dict, dict, str]] = []
    replayed = 0
    seen: set[tuple[str, str]] = set()
    ready, unresolved = mutation_readiness(
        store, events, repo_id, repo_path, allowed_node_keys)

    # Мотиви, впорядковані за seq: Claude Code пише прозу й виклик
    # інструмента ОКРЕМИМИ записами, тому збіг за `event_id` там не буває
    # ніколи (виміряно: 126 мотивів, 0 привʼязок). Беремо найближчий
    # попередній — і називаємо це інакше, бо доказ слабший.
    reasoning_items = sorted(
        (e for e in events if e.get("kind") == "reasoning" and e.get("text")),
        key=lambda e: int(e.get("seq") or 0))
    reasoning_seqs = [int(e.get("seq") or 0) for e in reasoning_items]

    for ev, rel, resolved in ready:
        rationale = reasons_by_event.get(str(ev["event_id"]), "")
        rationale_source = "agent_reasoning_same_event" if rationale else "none"
        if not rationale:
            near = _prior_reasoning(ev, reasoning_items, reasoning_seqs)
            if near:
                rationale = str(near.get("text") or "")
                rationale_source = "agent_reasoning_prior_by_seq"
        intent = _prior_intent(ev, intents, intent_seqs)
        before = ev.get("old_string") or ""
        after = ev.get("new_string") or ""
        evidence = {
            "event_id": ev.get("event_id"), "tool": ev.get("tool"),
            "evidence_scope": ev.get("evidence_scope") or "node",
            "ts": ev.get("ts"), "path": rel,
            "before_sha256": sha256_text(before) if before else "",
            "after_sha256": sha256_text(after) if after else "",
            "rationale_source": rationale_source,
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
            condition = {
                    "basis": "tool_mutation", "evidence_level": "participation",
                    # This is a session edit candidate. Git content evidence
                    # promotes it later; stash/discard never enter node history.
                    "commit_state": "pending",
                    "evidence": evidence,
                    "rationale": rationale or "",
                    "intent_source": evidence["intent_source"],
                }
            link_info = {"event_id": ev["event_id"], "address": node["address"],
                         "stable_key": str(node["stable_key"]),
                         "path": rel, "rationale_chars": len(rationale),
                         "intent_seq": evidence["intent_seq"]}
            all_links.append(link_info)
            pending.append((link_info, condition,
                            f"caused-by:{pair[0]}:{pair[1]}"))

    def write_batch(con, batch):
        nonlocal replayed
        for link_info, condition, key in batch:
            result = semantic.relate(
                store, from_id=link_info["stable_key"], to_id=session_obj_id,
                relation="caused_by", actor=actor, condition=condition,
                idempotency_key=key, con=con)
            if result.get("replayed"):
                replayed += 1
            else:
                links.append(link_info)

    if compact_relations:
        grouped: dict[str, list[tuple[dict, dict, str]]] = {}
        for row in pending:
            grouped.setdefault(row[0]["stable_key"], []).append(row)
        for stable_key, rows in grouped.items():
            recorded = set()
            for prior in store.read(
                    "SELECT condition_json FROM relations WHERE from_id = ? "
                    "AND to_id = ? AND relation_type = 'caused_by'",
                    (stable_key, session_obj_id)):
                condition = json.loads(prior["condition_json"] or "{}")
                for entry in condition.get("events") or [condition]:
                    evidence = entry.get("evidence") or {}
                    if evidence.get("event_id"):
                        recorded.add(str(evidence["event_id"]))
            rows = [row for row in rows
                    if str(row[0]["event_id"]) not in recorded]
            if not rows:
                replayed += 1
                continue
            event_ids = sorted(str(row[0]["event_id"]) for row in rows)
            digest = sha256_text(canonical(event_ids))[:24]
            condition = {"basis": "tool_mutations_grouped",
                         "evidence_level": "participation",
                         "events": [row[1] for row in rows]}
            result = semantic.relate(
                store, from_id=stable_key, to_id=session_obj_id,
                relation="caused_by", actor=actor, condition=condition,
                idempotency_key=f"caused-by-group:{session_obj_id}:{stable_key}:{digest}")
            if result.get("replayed"):
                replayed += 1
            else:
                links.append(rows[0][0])
    else:
        for start in range(0, len(pending), 200):
            batch = pending[start:start + 200]
            store.mutate(lambda con, batch=batch: write_batch(con, batch),
                         batch_ledger=True)
    return {"links": links, "all_links": all_links,
            "new": len(links), "replayed": replayed,
            "unresolved": unresolved}


def unresolved_object(store, *, unresolved: list[dict], session_obj: dict,
                      commit: str, repo_id: str, repo_roots: list[str],
                      scope: list[str], opencode_db: str, actor: dict,
                      source_kind: str = "opencode", source_ref: str = "",
                      mutation_filter: str = "", source_until: str = "",
                      source_after: str = "") -> dict:
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
    # Empty mutation lists are common for Codex. The old key collapsed every
    # such session at the same commit into one object and kept the first
    # journal's source_ref, making later sessions impossible to verify.
    session_key = str(session_obj["semantic"].get("session_id") or session_obj["id"])
    stable = _digest(
        f"unresolved:v2:{session_key}:{source_kind}:{commit}:"
        f"{mutation_filter}:"
        f"{unresolved_digest(unresolved)}"
    )
    return semantic.put_object(
        store, object_type="observation",
        title=f"unresolved mutations {commit[:7]}",
        summary=text, actor=actor, lifecycle="proposed", trust="agent_proposed",
        stable_key=stable, idempotency_key=f"unresolved-obj:{stable}",
        semantic={
            "kind": "unresolved_mutations", "commit": commit, "repo_id": repo_id,
            "session_id": str(session_obj["semantic"].get("session_id") or ""),
            "session_obj_id": str(session_obj["id"]),
            "source_kind": source_kind, "source_ref": source_ref,
            "source_until": source_until,
            "source_after": source_after,
            "mutation_filter": mutation_filter,
            "repo_roots": list(repo_roots),
            "unresolved_count": len(unresolved),
            "unresolved_by_reason": counts,
            "unresolved_digest": unresolved_digest(unresolved),
            "unresolved_rows": sorted(
                (str(u.get("event_id") or ""), str(u.get("reason") or ""),
                 str(u.get("path") or "")) for u in unresolved),
        },
        scope=scope,
    )["object"]


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def committed_nodes(store, repo_id: str, repo_path: str | Path,
                    commit: str, path: str, *, is_head: bool | None = None) -> list[dict]:
    """Nodes with changed lines in a committed file; whole-file fallback.

    The index describes HEAD. For an older revision its symbol line numbers
    are not evidence, so only the stable whole-file address is returned.
    """
    grouped: dict[str, dict] = {}
    for variant in path_variants(path, repo_path):
        for node in nodes.resolve(store, repo_id, variant):
            grouped[str(node["stable_key"])] = node
    all_nodes = list(grouped.values())
    whole = [node for node in all_nodes
             if str(node["address"]).startswith("whole:")]
    if is_head is None:
        is_head = git.head(repo_path) == commit
    if not is_head:
        return whole
    if git.git(str(repo_path), "status", "--porcelain", "--", path).strip():
        # The index reads the worktree. Dirty content can move line numbers
        # away from the committed patch, so only file-level evidence survives.
        return whole
    diff = git.git(str(repo_path), "show", "--format=", "--unified=0",
                   commit, "--", path)
    ranges = []
    for line in diff.splitlines():
        match = _HUNK.match(line)
        if match:
            start = int(match.group(1))
            count = int(match.group(2) or "1")
            if count:
                ranges.append((start, start + count - 1))
    if not ranges:
        return whole
    changed = [node for node in all_nodes
               if not str(node["address"]).startswith("whole:")
               and any(int(node.get("start_line") or 1) <= end
                           and int(node.get("end_line") or 0) >= start
                           for start, end in ranges)]
    return changed or whole


def committed_node_keys(store, repo_id: str, repo_path: str | Path,
                        commit: str, paths: list[str]) -> set[str]:
    is_head = git.head(repo_path) == commit
    return {str(node["stable_key"]) for path in paths
            for node in committed_nodes(store, repo_id, repo_path, commit,
                                        path, is_head=is_head)}


def link_commit(store, *, repo_id: str, repo_path: str | Path, commit: str,
                work_id: str, actor: dict) -> dict:
    """CrUD коміту -> `commit_fact` і `mutated`; коміт не є причиною сесії.

    Повертає `resolved`, `mutated`, `unresolved`. Нерезолвований SHA в
    звіт, не в граф: 4 пакові digest'и з seq 9295 сюди не потраплять.
    """
    try:
        full = git.git(str(repo_path), "rev-parse", f"{commit}^{{commit}}").strip()
    except CairnError as exc:
        # `git_failed` означає, що git відповів і коміта немає. Будь-що інше
        # означає, що git не запустився, і це інша причина з іншим лікуванням.
        reason = "commit_not_found" if exc.code == "git_failed" else "git_unavailable"
        return {"resolved": "", "mutated": [], "unresolved": [reason]}
    except OSError:
        return {"resolved": "", "mutated": [], "unresolved": ["git_unavailable"]}
    if not full:
        return {"resolved": "", "mutated": [], "unresolved": ["commit_not_found"]}
    committed_at = git.git(str(repo_path), "show", "-s", "--format=%cI", full).strip()
    parents = git.git(str(repo_path), "show", "-s", "--format=%P", full).strip().split()
    message = git.git(str(repo_path), "show", "-s", "--format=%B", full).strip()
    status = git.git(str(repo_path), "show", "--name-status", "--format=", full)
    changed = []
    for line in status.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0][:1] in {"A", "M", "D", "R"}:
            changed.append((parts[0][:1], parts[-1]))
    found: list[tuple[str, str, dict]] = []
    unresolved = []
    scope: set[str] = set()
    is_head = git.head(repo_path) == full
    for status_code, path in changed:
        # Після переносу пакета в корінь старий шлях коміту не резолвиться.
        # Резолвимо обидва варіанти й дедупликуємо за stable_key, інакше
        # той самий вузол дав би два `mutated`-ребра з різними адресами.
        matching = committed_nodes(store, repo_id, repo_path, full, path,
                                   is_head=is_head)
        if not matching:
            unresolved.append({"path": path, "reason": "node_not_indexed"})
            continue
        for node in matching:
            found.append((status_code, path, node))
            scope.add(str(node["address"]))
            scope.add(str(node["stable_key"]))
    fact = semantic.put_object(
        store, object_type="fact",
        title=f"commit {full[:7]}",
        summary=f"commit {full[:7]} CrUD: {len(changed)} paths; {message}",
        actor=actor, lifecycle="proposed", trust="agent_proposed",
        stable_key=_digest(f"commit:{repo_id}:{full}"),
        idempotency_key=f"commit-fact-obj:v3:{full}",
        semantic={"kind": "commit_fact", "commit": full, "repo_id": repo_id,
                  "parents": parents,
                  "committed_at": committed_at, "message": message,
                  "basis": "git_diff"},
        scope=sorted(scope),
    )["object"]
    mutated = []
    patches = []
    for status_code, path in changed:
        diff = git.git(str(repo_path), "show", "--format=", "--unified=0",
                       full, "--", path)
        if "Binary files " in diff or "GIT binary patch" in diff:
            continue
        deleted, added = patch_lines(diff)
        if not deleted and not added:
            continue
        patch = semantic.put_object(
            store, object_type="fact", title=f"patch {full[:7]} {path}",
            summary=f"Git diff {full[:7]} {path}: -{len(deleted)} +{len(added)}",
            actor=actor, lifecycle="proposed", trust="agent_proposed",
            stable_key=_digest(f"commit-patch:{repo_id}:{full}:{path}"),
            idempotency_key=f"commit-patch:v1:{repo_id}:{full}:{path}",
            semantic={"kind": "commit_patch", "commit": full, "repo_id": repo_id,
                      "path": path, "status": status_code,
                      "deleted_lines": deleted, "added_lines": added,
                      "diff_sha256": sha256_text(diff)},
            scope=[path],
        )["object"]
        semantic.relate(
            store, from_id=fact["id"], to_id=patch["id"],
            relation="documents_change", actor=actor,
            condition={"basis": "git_diff", "commit": full, "path": path},
            idempotency_key=f"commit-patch-link:v1:{repo_id}:{full}:{path}",
        )
        patches.append({"path": path, "object_id": patch["id"],
                        "deleted_lines": len(deleted), "added_lines": len(added)})
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
            "patches": patches,
            "unresolved": unresolved}


def auto_compaction(store, *, events: list[dict], links: list[dict],
                    session_obj: dict, opencode_db: str, actor: dict,
                    commit: str = "", source_kind: str = "opencode",
                    source_ref: str = "", source_until: str = "",
                    source_after: str = "", commit_scoped: bool = False) -> dict:
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
    # Мотиви, впорядковані за seq, для випадку, коли точного збігу події
    # немає (Claude Code пише прозу й виклик інструмента окремими записами).
    reasoning_items = sorted(
        (e for e in events if e.get("kind") == "reasoning" and e.get("text")),
        key=lambda e: int(e.get("seq") or 0))
    reasoning_seqs = [int(e.get("seq") or 0) for e in reasoning_items]
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
                "text": str(prior.get("text") or ""),
            })
    # Джерело БЕЗ мутацій (Codex: правки всередині `exec`). Тоді ланцюга
    # «намір -> мутація» не існує, але самі наміри є, і саме вони є «чому».
    # Без цієї гілки `intent_refs` виходив порожнім, і причина зникала —
    # не тому, що її не було, а тому, що її ні до чого було прив'язати.
    if not ordered and intents:
        basis_without_mutations = True
        for prior in intents:
            key = str(prior.get("message_id") or "")
            if key and key not in seen:
                seen.add(key)
                intent_refs.append({
                    "message_id": key, "seq": prior["seq"],
                    "text_sha256": sha256_text(str(prior.get("text") or "")),
                    "text": str(prior.get("text") or ""),
                })
    else:
        basis_without_mutations = False

    for link in shown_chain:
        ev = muts.get(str(link["event_id"]), {})
        prior = _prior_intent(ev, intents, seqs)
        reason = reasons.get(str(link["event_id"]), "")
        if reason:
            motive = f"  мотив (agent_reasoning, та сама подія): {reason}"
        else:
            near = _prior_reasoning(ev, reasoning_items, reasoning_seqs)
            motive = (
                f"  мотив (agent_reasoning, попередній за часом, seq "
                f"{near['seq']}): {near['text']}"
                if near else "  мотив: (у журналі немає)"
            )
        chain.extend([
            f"[{link['event_id']}]",
            f"  намір (user, seq {prior['seq'] if prior else '-'}): "
            f"{prior['text'] if prior else '(не встановлено)'}",
            motive,
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
    if commit_scoped:
        stable = _digest(f"auto-cmp:v1.1:{session_id}:{cmp_msg}:commit:{commit}")
    obj = semantic.put_object(
        store, object_type="decision",
        title=f"автокомактація чому {session_id}",
        summary=text, actor=actor, lifecycle="proposed", trust="agent_proposed",
        stable_key=stable,
        idempotency_key=f"auto-cmp:v1.4:{stable}:{sha256_text(text)[:16]}",
        semantic={
            "kind": "auto_compaction", "session_id": session_id,
            "session_obj_id": str(session_obj["id"]),
            "commit": commit,
            "compaction_message_id": cmp_msg,
            # Підстава називається, а не припускається: там, де джерело не дає
            # мутацій, ланцюга «намір -> мутація» немає, і це видно з поля.
            "basis": ("user_intents_only" if basis_without_mutations
                      else "session_events"),
            "generator": "compaction_link/auto_compaction/1.2",
            "chain_shown": len(shown_chain), "chain_total": len(ordered),
            "summary_sha256": sha256_text(text), "source_kind": source_kind,
            "source_ref": source_ref,
            "source_until": source_until,
            "source_after": source_after,
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
