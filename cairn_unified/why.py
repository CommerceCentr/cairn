"""Відповідь про вузол: коли, чому, чим доведено — і чого тут немає.

Нічого не зберігає. Складає те, що вже лежить у графі: вузол -> сесія ->
наміри -> мутації -> факт коміту, плюс чинні рішення, що керують задачею.

МЕЖА, названа прямо й повторена у відповіді: звʼязок доводить **участь у
часі й шляху**, не семантичну причину. `mutated` означає «цей коміт
торкнувся цього шляху», а не «цей коміт є причиною». Коміт причиною не
оголошується ніде — ні тут, ні в `causal_history`.

Порядок хронології — за ЧАСОМ події, не за позицією в таблиці й не за
порядком ребер: позиція зсувається від власної ж правки.
"""

from __future__ import annotations

import json
from pathlib import Path

from .canon import parse_iso
from .errors import CairnError

#: Ребра, що ведуть від вузла до історії. Напрямок має значення: ключ —
#: тип ребра, значення — чи вузол є його джерелом.
NODE_EDGES = {
    "caused_by": "from",        # вузол --caused_by--> сесія
    "mutated": "to",            # commit_fact --mutated--> вузол
    "targets_symbol": "to",     # задача --targets_symbol--> вузол
}

#: Скільки намірів показувати в тексті відповіді. Решта лишається в
#: `chronology`, а не зникає.
MAX_INTENTS = 5


def _object(store, object_id: str, as_of: str | None):
    from . import semantic

    if as_of:
        body = semantic.object_as_of(store, object_id, as_of)
        if body is not None:
            return body
    row = store.read_one("SELECT payload_json FROM objects WHERE id = ?", (object_id,))
    return json.loads(row["payload_json"]) if row else None


def _edges(store, node_key: str) -> list[dict]:
    """Ребра навколо вузла, згорнуті за (джерело, ціль, тип) з лічильником.

    У реальному сховищі один вузол мав 11 однакових ребер `caused_by` до
    тієї самої сесії — по одному на мутацію. Це ІНФОРМАЦІЯ (скільки разів
    вузол змінювали в тій сесії), а не шум, тому лічильник зберігається,
    а дублікати не друкуються одинадцять разів.
    """
    rows = store.read(
        "SELECT from_id, to_id, relation_type, execution_effect, created_at, "
        "condition_json "
        "FROM relations WHERE from_id = ? OR to_id = ? ORDER BY created_at, id",
        (node_key, node_key),
    )
    sessions = sorted({str(row["to_id"]) for row in rows
                       if row["relation_type"] == "caused_by"
                       and row["from_id"] == node_key})
    scoped_sessions: set[str] = set()
    confirmed: set[tuple[str, str]] = set()
    if sessions:
        marks = ",".join("?" * len(sessions))
        for link in store.read(
                "SELECT from_id, condition_json FROM relations WHERE "
                f"relation_type = 'mutation_in_commit' AND from_id IN ({marks})",
                tuple(sessions)):
            sid = str(link["from_id"])
            scoped_sessions.add(sid)
            try:
                condition = json.loads(link["condition_json"] or "{}")
            except (TypeError, ValueError):
                continue
            if str(condition.get("node_key") or "") == node_key:
                confirmed.add((sid, str(condition.get("event_id") or "")))
    folded: dict[tuple, dict] = {}
    for row in rows:
        if row["relation_type"] == "caused_by" and row["to_id"] in scoped_sessions:
            try:
                condition = json.loads(row["condition_json"] or "{}")
            except (TypeError, ValueError):
                condition = {}
            event_id = str((condition.get("evidence") or {}).get("event_id") or "")
            if (str(row["to_id"]), event_id) not in confirmed:
                continue
        key = (row["from_id"], row["to_id"], row["relation_type"])
        item = folded.setdefault(key, {
            "from_id": row["from_id"], "to_id": row["to_id"],
            "relation": row["relation_type"], "effect": row["execution_effect"],
            "first_at": row["created_at"], "count": 0,
        })
        item["count"] += 1
        item["last_at"] = row["created_at"]
    return list(folded.values())


#: Ребра другого стрибка. Виміряно: наміри користувача лежать не на вузлі,
#: а на `auto_compaction`, який привʼязаний до СЕСІЇ через `derived_from`.
#: Один стрибок від вузла давав «ЧОМУ не встановлено» при повних даних.
SECOND_HOP = ("derived_from", "governed_by", "supersedes",
              "context_for_commit")

#: Глибина обходу. Два — бо саме стільки потрібно для ланцюга
#: вузол -> сесія -> намір. Більше означало б тягнути півграфа в кожну
#: відповідь і платити за це бюджетом.
MAX_DEPTH = 3


def _neighbours(store, object_id: str) -> list[dict]:
    """Сусіди другого стрибка: хто посилається на цей обʼєкт і куди він сам."""
    rows = store.read(
        "SELECT from_id, to_id, relation_type, created_at FROM relations "
        "WHERE (to_id = ? OR from_id = ?) AND relation_type IN "
        f"({','.join('?' * len(SECOND_HOP))}) ORDER BY created_at, id",
        (object_id, object_id, *SECOND_HOP),
    )
    seen: set[tuple] = set()
    out: list[dict] = []
    for row in rows:
        # Сусід — ІНША вершина ребра. Попередня версія для ВХІДНИХ ребер
        # (`to_id == object_id`) брала ту саму вершину, тому другий стрибок
        # не знаходив нічого: `auto_compaction --derived_from--> session`
        # лишався невидимим, і наміри користувача зникали.
        other = row["from_id"] if row["to_id"] == object_id else row["to_id"]
        key = (other, row["relation_type"])
        if other == object_id or key in seen:
            continue
        seen.add(key)
        out.append({"object_id": other, "relation": row["relation_type"],
                    "created_at": row["created_at"]})
    return out


def _work_fallback(store, work_id: str) -> dict | None:
    """WorkItem зі старих сховищ лежить лише в `work_items`, не в `objects`.

    Envelope для задач додано пізніше (ADR-016), тому для вже записаних
    сховищ треба запас: інакше `why` чесно, але без потреби каже
    «обʼєкт не знайдено» там, де задача є.
    """
    row = store.read_one(
        "SELECT id, type, status, title, description, updated_at, payload_json "
        "FROM work_items WHERE id = ?", (work_id,))
    if row is None:
        return None
    return {
        "id": row["id"],
        "object_type": "work_item",
        "title": row["title"],
        "summary": row["description"],
        "lifecycle_state": "accepted",
        "trust_class": "source_observed",
        "recorded_at": row["updated_at"],
        "observed_at": row["updated_at"],
        "semantic": {"kind": "work_item", "work_type": row["type"],
                     "status": row["status"]},
        "provenance": {"created_by": None},
        "_from_projection": "work_items",
    }


def _freshness(body: dict, node_revision: str | None) -> str:
    if body.get("lifecycle_state") in {"revoked", "rejected"}:
        return "invalidated"
    if body.get("lifecycle_state") == "superseded" or body.get("valid_until"):
        return "superseded"
    if body.get("trust_class") == "imported_untrusted":
        return "unverified"
    commit = (body.get("semantic") or {}).get("commit")
    if node_revision and commit and not str(node_revision).startswith(str(commit)[:7]):
        return "historical"
    return "current"


def _time_of(body: dict) -> str:
    sem = body.get("semantic") or {}
    if sem.get("kind") == "commit_fact" and sem.get("committed_at"):
        return str(sem["committed_at"])
    if sem.get("source_occurred_at"):
        return str(sem["source_occurred_at"])
    return str(body.get("observed_at") or body.get("recorded_at") or "")


def _omissions(body: dict) -> list[dict]:
    """Причини, які обʼєкт сам про себе повідомляє. Дві мітки, не одна.

    `unresolved:*` — джерела немає. `verifier_error:*` — перевірка впала.
    Раніше це була одна мітка, через що зламаний верифікатор був
    невідрізненний від відсутнього джерела.
    """
    out: list[dict] = []
    ver = body.get("verification") or {}
    for reason in ver.get("reasons") or []:
        kind = "verifier_error" if str(reason).startswith("verifier_error") else "unresolved"
        out.append({"object_id": body.get("id"), "kind": kind, "reason": reason})
    sem = body.get("semantic") or {}
    shown, total = sem.get("chain_shown"), sem.get("chain_total")
    if shown and total and int(total) > int(shown):
        out.append({
            "object_id": body.get("id"), "kind": "bounded_chain",
            "reason": f"ланцюжок обрізано: показано {shown} із {total}",
        })
    if sem.get("unresolved_count"):
        out.append({
            "object_id": body.get("id"), "kind": "unresolved_mutations",
            "reason": f"мутацій без вузла: {sem['unresolved_count']}",
            "by_reason": sem.get("unresolved_by_reason"),
        })
    return out


def _node_chain(store, node_key: str,
                allowed_events: set[tuple[str, str, str]] | None = None) -> list[dict]:
    """Ланцюг «намір -> мотив -> правка» САМЕ цього вузла.

    Докази лежать на ребрах `caused_by` (`relations.condition_json`), які
    писав `compaction_link.link_mutations`. Раніше `why` показував замість
    них сесійну автокомактацію — ланцюг усієї сесії, що цього вузла міг і
    не стосуватись. Виміряно: 12 ребер вузла проти перших 12 мутацій сесії.

    `rationale_source` зберігається дослівно, бо розрізняє силу доказу:
    `agent_reasoning_same_event` проти `agent_reasoning_prior_by_seq`.
    """
    rows = store.read(
        "SELECT to_id, condition_json, created_at FROM relations "
        "WHERE from_id = ? AND relation_type = 'caused_by' "
        "AND condition_json IS NOT NULL ORDER BY created_at, id",
        (node_key,),
    )
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        try:
            cond = json.loads(row["condition_json"])
        except (TypeError, ValueError):
            continue
        for entry in cond.get("events") or [cond]:
            evidence = entry.get("evidence") or {}
            key = str(evidence.get("event_id") or "")
            identity = (key, node_key, str(row["to_id"]))
            if allowed_events is not None and identity not in allowed_events:
                continue
            seen_key = (key, str(row["to_id"]))
            if key and seen_key in seen:
                continue
            seen.add(seen_key)
            out.append({
                "kind": "node_change",
                "node_key": node_key,
                "session_object_id": row["to_id"],
                "event_id": key,
                "ts": evidence.get("ts"),
                "tool": evidence.get("tool"),
                "path": evidence.get("path"),
                "intent": evidence.get("intent"),
                "intent_seq": evidence.get("intent_seq"),
                "intent_source": evidence.get("intent_source"),
                "motive": entry.get("rationale") or "",
                "motive_source": evidence.get("rationale_source"),
                "evidence_level": cond.get("evidence_level"),
                "note": "мотив — слова агента, не доведене твердження",
            })
    return out


def _origins(store, event_ids: list[str]) -> list[str]:
    """Вихідні ідентифікатори подій: `causation_id`, якщо він є, інакше свій.

    На машині, де подія народилася, `causation_id` порожній — тоді
    походженням є сам ідентифікатор. Після переносу він вказує на вихідну
    подію, тому це поле однакове на обох машинах.
    """
    if not event_ids:
        return []
    marks = ",".join("?" * len(event_ids))
    rows = store.read(
        f"SELECT event_id, causation_id FROM events WHERE event_id IN ({marks})",
        tuple(event_ids))
    by_id = {row["event_id"]: row["causation_id"] for row in rows}
    return [by_id.get(eid) or eid for eid in event_ids]


def _claude_journal(source_ref: str) -> Path | None:
    """Знайти журнал Claude Code за ВІДНОСНИМ імʼям із обʼєкта.

    В обʼєкті лежить лише назва файла (`source_ref`), не шлях: абсолютний
    шлях розкривав би імʼя користувача й їхав у bundle. Корінь журналів —
    локальна річ, тому його дає адаптер.
    """
    if not source_ref:
        return None
    try:
        from .adapters import claude_code
    except ImportError:
        return None
    for candidate in claude_code.discover():
        if Path(candidate).name == source_ref:
            return Path(candidate)
    return None


def _intent_text_from_journal(intents: list[dict], source_ref: str) -> list[dict]:
    """Текст намірів із журналу Claude Code, звірений за sha256.

    Та сама вимога, що для opencode: розбіжність хеша -> `source_changed`,
    а не тихе підставляння поточного тексту замість того, що був.
    """
    journal = _claude_journal(source_ref)
    if journal is None:
        for item in intents:
            item["text_source"] = "unavailable:journal_missing"
        return intents
    from .adapters import claude_code, normalize

    from .canon import sha256_text

    found = {str(event.get("message_id")): str(event.get("text") or "")
             for event in normalize.canonical_events(claude_code.read(str(journal)))
             if event.get("kind") == "user_intent"}
    for item in intents:
        text = found.get(str(item.get("message_id")))
        if text is None:
            item["text_source"] = "unavailable:intent_absent"
            continue
        if item.get("text_sha256") and sha256_text(text) != item["text_sha256"]:
            item["text_source"] = "source_changed"
            continue
        item["text"] = text
        item["text_source"] = f"{journal.name}:verified"
    return intents


def _resolve_intent_text(intents: list[dict], *, opencode_db: str | None) -> list[dict]:
    """Дочитати текст намірів із джерела, звіривши sha256.

    У сховищі лежить лише `text_sha256` — свідомо: сирий текст сесії не
    імпортується як факт. Тому `why` за замовчуванням каже «текст не
    збережено» і НЕ вгадує його.

    Якщо викликач просить текст, він береться з `opencode.db` і кожен
    рядок звіряється з хешем. Розбіжність -> `source_changed`, а не тихе
    підставляння: інакше ми б видали поточний текст за той, що був.
    """
    if not intents:
        return intents
    try:
        from .adapters import opencode
    except ImportError:
        for item in intents:
            item["text_source"] = "unavailable:adapter_missing"
        return intents

    wanted = {str(item.get("message_id")): item for item in intents
              if item.get("message_id")}
    if not wanted:
        return intents
    db = opencode_db or opencode.default_db()
    try:
        events = list(opencode.read(db))
    except Exception as exc:                      # noqa: BLE001 — класифікуємо
        reason = ("unavailable:source_missing" if not Path(db).exists()
                  else f"verifier_error:{type(exc).__name__}")
        for item in intents:
            item["text_source"] = reason
        return intents

    from .canon import sha256_text

    found: dict[str, str] = {}
    for event in events:
        if event.get("kind") != "user_intent":
            continue
        mid = str(event.get("message_id") or (event.get("event_id") or "").split(":")[-1])
        if mid in wanted:
            found[mid] = str(event.get("text") or "")
    for mid, item in wanted.items():
        text = found.get(mid)
        if text is None:
            item["text_source"] = "unavailable:intent_absent"
            continue
        if item.get("text_sha256") and sha256_text(text) != item["text_sha256"]:
            item["text_source"] = "source_changed"
            continue
        item["text"] = text
        item["text_source"] = "opencode.db:verified"
    return intents


def _intents(bodies: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for body in bodies:
        for ref in (body.get("semantic") or {}).get("intent_refs") or []:
            key = str(ref.get("text_sha256") or ref.get("message_id") or "")
            if key and key in seen:
                continue
            seen.add(key)
            out.append({
                "message_id": ref.get("message_id"),
                "seq": ref.get("seq"),
                "text": ref.get("text"),
                "text_sha256": ref.get("text_sha256"),
                "source_object": body.get("id"),
            })
    out.sort(key=lambda item: (item.get("seq") is None, item.get("seq") or 0))
    return out


def _commit_history(store, facts: list[dict], bodies: dict[str, dict],
                    node_keys: set[str]) -> list[dict]:
    """Per-commit evidence for this node, across all agent sessions."""
    history = []
    seen_facts: set[str] = set()
    order: dict[str, int] = {}
    repo_ids = {str((fact.get("semantic") or {}).get("repo_id")) for fact in facts
                if (fact.get("semantic") or {}).get("repo_id")}
    if len(repo_ids) == 1:
        from .code import git
        repo = store.read_one("SELECT path FROM repositories WHERE id = ?", (next(iter(repo_ids)),))
        if repo:
            try:
                shas = git.git(repo["path"], "rev-list", "--topo-order", "--reverse", "--all")
                order = {sha: index for index, sha in enumerate(shas.splitlines())}
            except (CairnError, OSError):
                pass
    for fact in sorted(facts, key=lambda item: (
            0, order[(item.get("semantic") or {}).get("commit")]
        ) if (item.get("semantic") or {}).get("commit") in order else (1, _time_of(item))):
        if str(fact["id"]) in seen_facts:
            continue
        seen_facts.add(str(fact["id"]))
        sem = fact.get("semantic") or {}
        sha = str(sem.get("commit") or "")
        rows = store.read(
            "SELECT from_id, relation_type, condition_json FROM relations "
            "WHERE to_id = ? AND relation_type IN "
            "('context_for_commit','mutation_in_commit') ORDER BY id",
            (fact["id"],))
        session_ids = {str(row["from_id"]) for row in rows}
        # Older stores have node->session evidence without the newer
        # session->commit relation. Its session commit is still explicit.
        session_ids.update(
            oid for oid, body in bodies.items()
            if (body.get("semantic") or {}).get("kind") == "session"
            and (body.get("semantic") or {}).get("commit") == sha)
        events: list[dict] = []
        seen_mutations: set[tuple[str, str, str]] = set()
        for row in rows:
            if row["relation_type"] != "mutation_in_commit":
                continue
            try:
                condition = json.loads(row["condition_json"] or "{}")
            except (TypeError, ValueError):
                continue
            if str(condition.get("node_key") or "") in node_keys:
                mutation_key = (str(condition.get("event_id") or ""),
                                str(condition.get("node_key") or ""),
                                str(condition.get("path") or ""))
                if mutation_key in seen_mutations:
                    continue
                seen_mutations.add(mutation_key)
                events.append({"event_id": condition.get("event_id"),
                               "node_key": condition.get("node_key"),
                               "path": condition.get("path"),
                               "session_object_id": row["from_id"],
                               "evidence_level": condition.get("evidence_level")})
        sessions = []
        for sid in sorted(session_ids):
            body = bodies.get(sid) or _object(store, sid, None)
            if body is None:
                continue
            source = body.get("semantic") or {}
            sessions.append({"object_id": sid,
                             "session_id": source.get("session_id"),
                             "source_kind": source.get("source_kind"),
                             "verified": body.get("trust_class") == "verified_system"})
        source_by_session = {s["object_id"]: s["source_kind"] for s in sessions}
        for session in sessions:
            session["direct_mutations_for_node"] = sum(
                1 for event in events
                if event["session_object_id"] == session["object_id"])
        session_intents: list[dict] = []
        for body in bodies.values():
            source = body.get("semantic") or {}
            if (source.get("kind") != "auto_compaction"
                    or source.get("commit") != sha
                    or (source.get("session_obj_id") not in session_ids
                        and not (not source.get("session_obj_id")
                                 and len(session_ids) == 1))):
                continue
            for ref in source.get("intent_refs") or []:
                if str(ref.get("text") or "").startswith("Base directory for this skill:"):
                    continue
                session_intents.append({"session_object_id": source.get("session_obj_id"),
                                        "source_kind": source_by_session.get(source.get("session_obj_id")),
                                        "message_id": ref.get("message_id"),
                                        "seq": ref.get("seq"),
                                        "text": ref.get("text") or "",
                                        "text_sha256": ref.get("text_sha256")})
        legacy_direct = 0
        has_scoped_commit_evidence = any(
            row["relation_type"] == "mutation_in_commit" for row in rows)
        if not events and session_ids and not has_scoped_commit_evidence:
            for key in node_keys:
                legacy_direct += sum(1 for row in store.read(
                    "SELECT to_id FROM relations WHERE from_id = ? "
                    "AND relation_type = 'caused_by'", (key,))
                    if str(row["to_id"]) in session_ids)
        patch_rows = store.read(
            "SELECT to_id FROM relations WHERE from_id = ? "
            "AND relation_type = 'documents_change'", (fact["id"],))
        patches = []
        node_paths = {str(node["path"]) for key in node_keys
                      for node in store.read(
                          "SELECT path FROM code_symbols WHERE stable_key = ?", (key,))}
        for row in patch_rows:
            patch_body = _object(store, str(row["to_id"]), None)
            patch_sem = (patch_body or {}).get("semantic") or {}
            if patch_sem.get("path") in node_paths:
                patches.append({"path": patch_sem.get("path"),
                                "deleted_lines": patch_sem.get("deleted_lines") or [],
                                "added_lines": patch_sem.get("added_lines") or [],
                                "object_id": row["to_id"]})
        history.append({"commit": sha, "committed_at": sem.get("committed_at"),
                        "parents": sem.get("parents") or [],
                        "message": sem.get("message") or "",
                        "git_fact_id": fact["id"], "sessions": sessions,
                        "session_intents": session_intents,
                        "patches": patches,
                        "mutations_for_node": events,
                        "direct_mutations": len(events) or legacy_direct,
                        "evidence": ("structured_mutation" if events else
                                     "legacy_node_session_relation" if legacy_direct else
                                     "git_and_session_context" if sessions else "git_only")})
    return history


def history_order(item: dict) -> tuple[str, int]:
    """Порядок хронології: ЗМІСТ (дата коміту), не позиція в чужому зрізі.

    `topo_index` приходить лише з архіву Git і є не в усіх елементів, тому
    сортування за ним першим викидало в кінець коміти, яких в архівному
    зрізі цього шляху немає. Виміряно на `cairn_unified/verification.py`:
    коміт від 2026-11-20 стояв ПІСЛЯ коміта від 2026-11-24. Тепер
    `topo_index` лишається тільки розвʼязувачем нічиїх.
    """
    topo = item.get("topo_index")
    return (str(item.get("committed_at") or ""),
            topo if isinstance(topo, int) else 1 << 30)


def _archive_commit_history(store, repo_id: str, path: str) -> list[dict]:
    """Git facts recorded as one portable event per commit."""
    out = []
    retracted = {
        str(json.loads(row["payload_json"]).get("commit") or "")
        for row in store.read(
            "SELECT payload_json FROM events WHERE event_type = 'git.commit_retracted' "
            "AND aggregate_id = ?", (repo_id,))
    }
    contexts = {}
    for row in store.read(
            "SELECT payload_json FROM events WHERE event_type = 'git.commit_context' "
            "AND aggregate_id = ?", (repo_id,)):
        context = json.loads(row["payload_json"])
        contexts[str(context.get("commit") or "")] = context
    for row in store.read(
            "SELECT payload_json FROM events WHERE event_type = 'git.commit_indexed' "
            "AND aggregate_id = ? ORDER BY seq", (repo_id,)):
        item = json.loads(row["payload_json"])
        if item["commit"] in retracted:
            continue
        files = [entry for entry in item.get("files") or []
                 if entry.get("path", "").replace("\\", "/") == path.replace("\\", "/")]
        if not files:
            continue
        out.append({
            "commit": item["commit"], "committed_at": item.get("committed_at"),
            "parents": item.get("parents") or [], "message": item.get("message") or "",
            "git_author": item.get("author"), "git_committer": item.get("committer"),
            "signature_status": item.get("signature_status"),
            "patch_omission": item.get("patch_omission") or "",
            "topo_index": item.get("topo_index"), "git_fact_id": "",
            "sessions": [], "session_intents": [],
            "patches": [{"path": entry["path"],
                         "deleted_lines": entry.get("deleted_lines") or [],
                         "added_lines": []} for entry in files],
            "mutations_for_node": [], "direct_mutations": 0,
            "evidence": "git_only",
            "checkpoint_context": contexts.get(item["commit"]),
        })
    return out


def _zone_context(store, repo_id: str) -> dict:
    """Coverage of the shared graph for this repository, across agents."""
    symbol_counts = store.read_one(
        "SELECT COUNT(*) AS nodes, COUNT(DISTINCT path) AS paths, "
        "SUM(CASE WHEN precision = 'historical_file' THEN 1 ELSE 0 END) "
        "AS historical_files FROM code_symbols WHERE repo_id = ?", (repo_id,))
    facts = {str(row["from_id"]) for row in store.read(
        "SELECT DISTINCT r.from_id FROM relations r JOIN code_symbols c "
        "ON c.stable_key = r.to_id WHERE r.relation_type = 'mutated' "
        "AND c.repo_id = ?", (repo_id,))}
    archived = store.read_one(
        "SELECT COUNT(*) AS n FROM events WHERE event_type = 'git.commit_indexed' "
        "AND aggregate_id = ?", (repo_id,))
    retracted = store.read_one(
        "SELECT COUNT(*) AS n FROM events WHERE event_type = 'git.commit_retracted' "
        "AND aggregate_id = ?", (repo_id,))
    total_git = max(len(facts), int((archived or {}).get("n") or 0)
                    - int((retracted or {}).get("n") or 0))
    linked_commits: set[str] = set()
    mutation_commits: set[str] = set()
    sources: set[str] = set()
    mutation_keys: set[tuple[str, str, str, str]] = set()
    raw_mutation_edges = 0
    if facts:
        marks = ",".join("?" * len(facts))
        for row in store.read(
                "SELECT id, from_id, to_id, relation_type, condition_json FROM relations "
                f"WHERE to_id IN ({marks}) AND relation_type IN "
                "('context_for_commit','mutation_in_commit')", tuple(facts)):
            linked_commits.add(str(row["to_id"]))
            session = _object(store, str(row["from_id"]), None)
            if session:
                sem = session.get("semantic") or {}
                sources.add(str(sem.get("source_kind") or
                                sem.get("session_source") or "unknown"))
            if row["relation_type"] == "context_for_commit":
                continue
            else:
                mutation_commits.add(str(row["to_id"]))
                raw_mutation_edges += 1
                condition = json.loads(row["condition_json"] or "{}")
                mutation_keys.add((str(row["to_id"]),
                                   str(condition.get("event_id") or row["id"]),
                                   str(condition.get("node_key") or ""),
                                   str(condition.get("path") or "")))
    return {"indexed_nodes": int((symbol_counts or {}).get("nodes") or 0),
            "indexed_paths": int((symbol_counts or {}).get("paths") or 0),
            "historical_file_nodes": int((symbol_counts or {}).get("historical_files") or 0),
            "git_facts": total_git, "commits_with_session": len(linked_commits),
            "commits_with_structured_mutation": len(mutation_commits),
            "git_only_commits": max(0, total_git - len(linked_commits)),
            "mutation_in_commit_edges": len(mutation_keys),
            "raw_mutation_in_commit_edges": raw_mutation_edges,
            "duplicate_mutation_edges": raw_mutation_edges - len(mutation_keys),
            "agent_sources": sorted(sources)}


def _answer_text(node: dict, groups: dict, intents: list[dict],
                 has_direct_mutation: bool = False,
                 history: list[dict] | None = None,
                 zone: dict | None = None,
                 include_pending: bool = False) -> str:
    lines: list[str] = []
    if zone:
        lines.append(f"ЗОНА   Git-комітів {zone['git_facts']}; із сесіями "
                     f"{zone['commits_with_session']}; з підтвердженими "
                     f"мутаціями {zone['commits_with_structured_mutation']}; "
                     f"лише Git {zone['git_only_commits']}")
    commit_facts = groups.get("commit_fact") or []
    sessions = groups.get("session") or []
    if history:
        lines.append(f"ІСТОРІЯ {node.get('path')}: комітів {len(history)}")
        for item in history:
            first = (item["message"].splitlines() or [""])[0]
            lines.append(f"КОМІТ  {item['committed_at'] or 'час невідомий'} "
                         f"{item['commit'][:7]} — {first or 'без опису'}")
            sources = ", ".join(sorted({str(s['source_kind']) for s in item['sessions']}))
            lines.append(f"        сесії: {len(item['sessions'])}"
                         + (f" ({sources})" if sources else "")
                         + f"; підтверджених мутацій вузла: {item['direct_mutations']}")
            for session in item["sessions"]:
                lines.append(f"        {session['source_kind']}: "
                             f"{session['direct_mutations_for_node']} прямих правок вузла")
            if item["evidence"] == "git_and_session_context":
                lines.append("        наміри сесії є контекстом коміту; її правка "
                             "цього вузла не доведена")
            elif item["evidence"] == "git_only":
                lines.append("        Git доводить зміну вузла; сесію не встановлено")
            if item.get("checkpoint_context"):
                lines.append("        намір зафіксувати цей стан: "
                             + str(item["checkpoint_context"].get("intent") or ""))
            for reason in item.get("change_reasons") or []:
                lines.append("        ЧОМУ (заява учасника; стан файла підтверджено комітом): "
                             + " ".join(reason["why"].split())[:400])
                lines.append("        НАМІР: " + " ".join(reason["intent"].split())[:300])
            for intent in item["session_intents"][:3]:
                intent_text = " ".join(str(intent.get("text") or "").split())
                direct = next((s["direct_mutations_for_node"] for s in item["sessions"]
                               if s["object_id"] == intent.get("session_object_id")), 0)
                label = ("намір сесії перед правкою (вузлова причина не доведена)"
                         if direct else "контекст сесії")
                lines.append(f"        {label} [{intent.get('seq')}]: "
                             f"{intent_text[:260] or '(текст не збережено)'}")
            for patch in item.get("patches") or []:
                removed = patch["deleted_lines"]
                if removed:
                    lines.append(f"        вилучено з {patch['path']} ({len(removed)} рядків):")
                    lines.extend(f"          - {line}" for line in removed[:12])
            if item.get("patch_omission"):
                lines.append(f"        вилучені рядки не зчитані: {item['patch_omission']}")
            motives = [change for change in item.get("node_changes") or []
                       if change.get("motive")]
            if motives:
                motive = " ".join(str(motives[0]["motive"]).split())
                lines.append(f"        пояснення агента ({motives[0].get('motive_source')}): "
                             f"{motive[:260]}")
    elif commit_facts:
        fact = commit_facts[0]
        sem = fact.get("semantic") or {}
        stamp_kind = "коміт" if sem.get("committed_at") else "записано в Cairn"
        lines.append(f"КОЛИ   {_time_of(fact)[:19] or '—'} ({stamp_kind}) — коміт "
                     f"{str(sem.get('commit') or '')[:7]}: {fact.get('title') or ''}")
    else:
        lines.append("КОЛИ   не встановлено: факту коміту для цього вузла немає")
    if sessions and not history:
        sess = sessions[0]
        sem = sess.get("semantic") or {}
        lines.append(f"СЕСІЯ  {sem.get('session_id') or '—'}, компактація "
                     f"{sem.get('compaction_message_id') or '—'}")
    elif not history:
        lines.append("СЕСІЯ  не встановлено: жодна сесія не торкалася вузла")
    if intents and not history:
        if not has_direct_mutation:
            lines.append("ЗВʼЯЗОК  наміри належать сесії коміту; участь цієї "
                         "сесії у правці саме цього вузла не підтверджена")
        lines.append(f"ЧОМУ   намірів користувача: {len(intents)}; "
                     f"перші {min(MAX_INTENTS, len(intents))}:")
        for item in intents[:MAX_INTENTS]:
            text = " ".join(str(item.get("text") or "").split())[:140]
            lines.append(f"       [{item.get('seq')}] {text or '(текст не збережено)'}")
    elif not history:
        lines.append("ЧОМУ   не встановлено: намірів користувача не записано")
    autos = groups.get("auto_compaction") or []
    if history and sessions and include_pending:
        linked_ids = {str(s.get("object_id")) for item in history
                      for s in item.get("sessions") or []}
        unlinked = [s for s in sessions if str(s.get("id")) not in linked_ids]
        if unlinked:
            lines.append(f"СЕСІЇ ВУЗЛА  {len(unlinked)}; звʼязок із конкретним "
                         "комітом не доведено")
        for sess in sorted(unlinked, key=_time_of):
            sem = sess.get("semantic") or {}
            lines.append(f"СЕСІЯ  {_time_of(sess)[:19] or 'час невідомий'} "
                         f"{sem.get('source_kind') or 'agent'} "
                         f"{sem.get('session_id') or ''}")
            summary = " ".join(str(sess.get("summary") or "").split())
            if summary:
                lines.append(f"        компактація: {summary[:600]}")
            for auto in autos:
                if (auto.get("semantic") or {}).get("session_obj_id") == sess.get("id"):
                    cause = " ".join(str(auto.get("summary") or "").split())
                    if cause:
                        lines.append(f"        намір і мотив: {cause[:900]}")
    if autos and not history:
        sem = autos[0].get("semantic") or {}
        shown, total = sem.get("chain_shown"), sem.get("chain_total")
        lines.append(f"МОТИВ  слова АГЕНТА (не доведене твердження), ланок "
                     f"{shown or 0} із {total or 0}; підстава: "
                     f"{sem.get('basis') or '—'}")
    lines.append("МЕЖА   звʼязок доводить участь у часі й шляху, не семантичну "
                 "причину; коміт причиною не є")
    return "\n".join(lines)


def answer(store, *, repo_id: str, path: str, line: int | None = None,
           address: str | None = None, commit: str | None = None,
           as_of: str | None = None, budget_chars: int | None = None,
           chronology: bool = True, intent_text: bool = False,
           opencode_db: str | None = None, repo_path: str | None = None,
           compact: bool = False, include_pending: bool = False) -> dict:
    """Шість полів: answer, chronology, provenance, trust, omissions, cost."""
    from . import nodes

    found = nodes.resolve(store, repo_id, path, line=line, address=address)
    if not found:
        raise CairnError(
            "node_not_found",
            f"вузол {address or path} не індексований — це ВІДПОВІДЬ, не збій",
            exit_code=1,
            details={"hint": f"спочатку `code nodes {repo_id}`", "path": path},
        )
    # `nodes.resolve` віддає поле `address`; `qualified_name` — це колонка
    # таблиці `code_symbols`. Це різні поля, а не синоніми.
    whole = [item for item in found if str(item["address"]).startswith("whole:")]
    node = (whole or found)[0]
    node_key = node["stable_key"]
    # A file question covers its indexed symbols as well as the whole-file
    # node. A precise address/line question remains scoped to one node.
    query_nodes = found if line is None and address is None else [node]
    query_keys = {str(item["stable_key"]) for item in query_nodes}
    edges = [edge for key in query_keys for edge in _edges(store, key)]
    groups: dict[str, list[dict]] = {}
    chrono: list[dict] = []
    bodies: dict[str, dict] = {}
    for edge in edges:
        other = edge["to_id"] if edge["from_id"] in query_keys else edge["from_id"]
        if other in query_keys:
            continue
        body = bodies.get(other) or _object(store, other, as_of)
        if body is None:
            body = _work_fallback(store, other)
        if body is None:
            chrono.append({"ts": "", "relation": edge["relation"], "object_id": other,
                           "kind": "missing", "note": "обʼєкт не знайдено в проєкції"})
            continue
        bodies[other] = body
        kind = (body.get("semantic") or {}).get("kind") or body.get("object_type") or "object"
        groups.setdefault(kind, []).append(body)
        chrono.append({
            "ts": _time_of(body),
            "relation": edge["relation"],
            "direction": "from_node" if edge["from_id"] in query_keys else "to_node",
            "count": edge["count"],
            "object_id": other,
            "kind": kind,
            "title": body.get("title"),
            "actor": (body.get("provenance") or {}).get("created_by"),
            "source": (body.get("semantic") or {}).get("session_source")
                      or (body.get("semantic") or {}).get("basis")
                      or "graph",
        })

    # Якір — ЧАС, не позиція: порядок ребер зсувається від власної ж правки.
    def when(item: dict):
        stamp = item.get("ts") or ""
        parsed = parse_iso(stamp) if stamp else None
        return (parsed is None, parsed or stamp)

    chrono.sort(key=when)

    # Другий стрибок: від сесії до того, що з неї виведено. Без нього
    # наміри користувача недосяжні, бо вони лежать на `auto_compaction`.
    frontier = sorted(bodies)
    for _depth in range(MAX_DEPTH - 1):
        next_frontier: list[str] = []
        for origin in frontier:
            for link in _neighbours(store, origin):
                other = link["object_id"]
                if other in bodies or other in query_keys:
                    continue
                body = _object(store, other, as_of) or _work_fallback(store, other)
                if body is None:
                    continue
                bodies[other] = body
                kind = ((body.get("semantic") or {}).get("kind")
                        or body.get("object_type") or "object")
                groups.setdefault(kind, []).append(body)
                chrono.append({
                    "ts": _time_of(body),
                    "relation": link["relation"],
                    "direction": "second_hop",
                    "via": origin,
                    "count": 1,
                    "object_id": other,
                    "kind": kind,
                    "title": body.get("title"),
                    "actor": (body.get("provenance") or {}).get("created_by"),
                    "source": (body.get("semantic") or {}).get("basis") or "graph",
                })
                next_frontier.append(other)
        frontier = next_frontier
    chrono.sort(key=when)

    if commit:
        wanted = str(commit)
        bodies = {oid: body for oid, body in bodies.items()
                  if not (body.get("semantic") or {}).get("commit")
                  or str((body.get("semantic") or {}).get("commit")).startswith(wanted)}
        groups = {kind: [body for body in items if body.get("id") in bodies]
                  for kind, items in groups.items()}
        chrono = [item for item in chrono if item["object_id"] in bodies]
    chrono = _fold_chronology(chrono)
    history = _commit_history(
        store, groups.get("commit_fact") or [], bodies, query_keys)
    archive_history = _archive_commit_history(store, repo_id, path)
    if commit:
        archive_history = [item for item in archive_history
                           if item["commit"].startswith(str(commit))]
    by_commit = {item["commit"]: item for item in history}
    for item in archive_history:
        prior = by_commit.get(item["commit"])
        if prior:
            prior.setdefault("topo_index", item.get("topo_index"))
            prior.setdefault("git_author", item.get("git_author"))
            prior.setdefault("git_committer", item.get("git_committer"))
            prior.setdefault("signature_status", item.get("signature_status"))
            prior.setdefault("patch_omission", item.get("patch_omission"))
            prior.setdefault("checkpoint_context", item.get("checkpoint_context"))
            if not prior.get("patches"):
                prior["patches"] = item["patches"]
        else:
            history.append(item)
            by_commit[item["commit"]] = item
    # Якір — дата коміту, бо вона є в УСІХ елементів. `topo_index` лишається
    # лише розвʼязувачем нічиїх: він приходить тільки з архіву Git, і
    # сортування за ним першим викидало в кінець коміти, яких у архівному
    # зрізі цього шляху немає (виміряно: 8c1eea5 від 2026-11-20 стояв після
    # 2026-11-24).
    history.sort(key=history_order)
    from . import change_notes
    reasons_by_commit = change_notes.linked(store, repo_id, path.replace("\\", "/"))
    for item in history:
        item["change_reasons"] = reasons_by_commit.get(item["commit"], [])
    intents = _intents(list(bodies.values()))
    if intent_text:
        # Джерело беремо з ОБʼЄКТІВ, а не припускаємо opencode: сесія могла
        # прийти з журналу Claude Code, і тоді читати треба його.
        sources = {str((body.get("semantic") or {}).get("source_kind") or ""):
                   str((body.get("semantic") or {}).get("source_ref") or "")
                   for body in bodies.values()}
        journal_ref = sources.get("claude_code_jsonl", "")
        if journal_ref:
            intents = _intent_text_from_journal(intents, journal_ref)
        else:
            intents = _resolve_intent_text(intents, opencode_db=opencode_db)

    provenance = []
    for oid in sorted(bodies):
        rows = store.read(
            "SELECT revision, source_event_id, recorded_at FROM object_revisions "
            "WHERE object_id = ? ORDER BY revision", (oid,))
        local = [r["source_event_id"] for r in rows if r["source_event_id"]][:8]
        provenance.append({
            "object_id": oid,
            "revisions": len(rows),
            # Єдине джерело для source_events — таблиця ревізій, не payload.
            "event_ids": local,
            # Походження, що ПЕРЕЖИВАЄ перенос сховища. Виміряно: після
            # `federation restore` усі 1160 подій отримують нові локальні
            # `event_id`, а посилання на вихідні лежить у `causation_id`.
            # Без цього поля дві машини з тим самим змістом давали різне
            # походження й порівняти їх було нічим.
            "origin_event_ids": _origins(store, local),
            "first_recorded_at": rows[0]["recorded_at"] if rows else None,
        })
    head = store.read_one("SELECT MAX(seq) AS seq FROM events")
    trust = [{
        "object_id": body.get("id"),
        "trust_class": body.get("trust_class"),
        "lifecycle_state": body.get("lifecycle_state"),
        "freshness": _freshness(body, node.get("revision")),
        "review_state": body.get("review_state"),
    } for body in (bodies[k] for k in sorted(bodies))]

    omissions: list[dict] = []
    for body in (bodies[k] for k in sorted(bodies)):
        omissions.extend(_omissions(body))
    if not any(item["kind"] == "unresolved" for item in omissions) and not bodies:
        omissions.append({"object_id": node_key, "kind": "unresolved",
                          "reason": "вузол індексований, але історії до нього немає"})

    # Текст «чому» (ланцюг намір -> мотив -> мутація) лежить у
    # `auto_compaction.summary`. Без нього відповідь називала джерело, але
    # не показувала його: виміряно приладом `answer_two_ways` — 2 факти з 7,
    # і обидва пропущені «чому» були саме тут.
    session_identities: dict[str, tuple[str, str]] = {}

    def session_identity(object_id: str) -> tuple[str, str]:
        object_id = str(object_id or "")
        if object_id not in session_identities:
            source = (_object(store, object_id, as_of) or {}).get("semantic") or {}
            session_identities[object_id] = (
                str(source.get("source_kind") or ""),
                str(source.get("session_id") or object_id))
        return session_identities[object_id]

    def change_key(event: dict) -> tuple[str, str, tuple[str, str]]:
        return (str(event.get("event_id") or ""),
                str(event.get("node_key") or ""),
                session_identity(str(event.get("session_object_id") or "")))

    linked_events = {change_key(event) for item in history
                     for event in item["mutations_for_node"]}
    witnessed_events = {change_key(event) for item in history
                        for event in item["mutations_for_node"]
                        if event.get("evidence_level") == "content_transition"}
    all_detail = [item for key in sorted(query_keys)
                  for item in _node_chain(store, key)]
    unique_detail: dict[tuple, dict] = {}
    for item in all_detail:
        key = change_key(item)
        if key not in unique_detail or (item.get("motive") and
                                        not unique_detail[key].get("motive")):
            unique_detail[key] = item
    all_detail = list(unique_detail.values())
    if commit:
        all_detail = [item for item in all_detail if change_key(item) in linked_events]
    for item in history:
        event_ids = {change_key(event) for event in item["mutations_for_node"]}
        item["node_changes"] = [change for change in all_detail
                                if change_key(change) in event_ids]
    for change in all_detail:
        change["semantic_cause_status"] = (
            "node_attested" if change_key(change) in witnessed_events
            and change.get("motive")
            and change.get("motive_source") == "agent_reasoning_same_event"
            else "agent_proposed" if change.get("motive") else "not_established")
    detail = (all_detail if include_pending else
              [item for item in all_detail if change_key(item) in linked_events])
    direct = any(item.get("relation") == "caused_by"
                 and item.get("object_id") in bodies for item in chrono)
    zone = _zone_context(store, repo_id)
    text = _answer_text(node, groups, intents, direct, history, zone,
                        include_pending)
    freshness = {"status": "local_git_unavailable"}
    if not as_of:
        from .code import git as git_mod
        stored_repo = store.read_one(
            "SELECT path FROM repositories WHERE id = ?", (repo_id,))
        candidate = repo_path or str(stored_repo["path"] if stored_repo else "")
        if candidate:
            try:
                if git_mod.identity(candidate)["id"] == repo_id:
                    local_head = git_mod.head(candidate)
                    indexed = {str(row["commit"]) for row in (
                        json.loads(item["payload_json"]) for item in store.read(
                            "SELECT payload_json FROM events WHERE event_type = 'git.commit_indexed' "
                            "AND aggregate_id = ?", (repo_id,)))}
                    missing = local_head not in indexed or (
                        bool(commit) and not any(sha.startswith(str(commit)) for sha in indexed))
                    freshness = {"status": "stale" if missing else "current",
                                 "local_head": local_head, "indexed_head": local_head in indexed,
                                 "requested_commit_indexed": (None if not commit else any(
                                     sha.startswith(str(commit)) for sha in indexed))}
                else:
                    freshness = {"status": "repo_identity_mismatch"}
            except (OSError, ValueError, CairnError):
                pass
    if freshness["status"] == "stale":
        text = ("СХОВИЩЕ ЗАСТАРІЛЕ: локальний Git містить коміт, якого немає "
                "в Cairn; запусти sync_project_causality перед висновком.\n" + text)
    unlinked_changes = sum(change_key(item) not in linked_events
                           for item in all_detail)
    if unlinked_changes:
        text += (f"\nКАНДИДАТИ ПОЗА ІСТОРІЄЮ ВУЗЛА  {unlinked_changes}; "
                 "звʼязок із комітом не доведено"
                 + ("; показано нижче" if include_pending else
                    "; деталі через --include-pending"))
    if not all_detail:
        # Запас для сховищ, записаних до схеми 3: там докази ребра ще не
        # проєктувались, тому беремо сесійну автокомактацію. Вона ширша за
        # цей вузол, і це сказано в полі `scope`.
        for item in groups.get("auto_compaction") or []:
            detail.append({
                "object_id": item.get("id"),
                "trust_class": item.get("trust_class"),
                "kind": "agent_reasoning_chain",
                "scope": "session",
                "note": "ланцюг усієї сесії, не лише цього вузла",
                "text": str(item.get("summary") or ""),
            })
    body = {
        "question": f"коли і чому редагувався {path}"
                    + (f", з комітом {str(commit)[:7]}" if commit else ""),
        "node": {"stable_key": node_key, "address": node["address"],
                 "path": node["path"], "revision": node.get("revision"),
                 "precision": node.get("precision")},
        "answer": text,
        "chronology": chrono if chronology else [],
        "why_detail": detail,
        "commit_history": history,
        "zone_context": zone,
        "freshness": freshness,
        "provenance": provenance,
        "ledger_seq": (head or {}).get("seq"),
        "trust": trust,
        "omissions": omissions,
        "as_of": as_of,
        # Повнота, названа числом. Відповідь мусить казати, НАСКІЛЬКИ вона
        # повна, інакше читач домислює це сам. Окремо позначено те, чого
        # тут немає ЗА ЗАДУМОМ: код не повертається ніколи, його читають
        # напряму (інакше відповідь дублювала б файл).
        "coverage": {
            "node_changes": len([x for x in all_detail if x.get("kind") == "node_change"]),
            "committed_node_changes": len([x for x in all_detail
                                            if change_key(x) in linked_events]),
            "node_attested_causes": len([x for x in all_detail
                                         if x.get("semantic_cause_status") == "node_attested"]),
            "file_state_attested_reasons": sum(
                len(item.get("change_reasons") or []) for item in history),
            "unlinked_node_changes": unlinked_changes,
            "user_intents": len(intents),
            "motives_same_event": len([x for x in detail
                                       if x.get("motive_source") == "agent_reasoning_same_event"]),
            "motives_prior_by_seq": len([x for x in detail
                                         if x.get("motive_source") == "agent_reasoning_prior_by_seq"]),
            "objects_linked": len(bodies),
            "typed_edges": len(chrono),
            "excluded_by_design": ["source_code"],
            "note": "вихідний код не входить у відповідь: читати файл напряму",
        },
        "boundary": "участь у часі й шляху, не семантична причина",
    }
    _share_repeated_intents(body)
    if compact:
        body = _compact_answer(body)
    if budget_chars:
        body = _apply_budget(body, budget_chars)
    serialized = json.dumps(body, ensure_ascii=False)
    body["cost"] = {
        "bytes": len(serialized.encode("utf-8")),
        "chars": len(serialized),
        # Той самий проксі, що в `tests/measure.py`: chars ÷ 4. Це НЕ
        # токенізатор цільової моделі, і порівнювати можна лише два виміри
        # одним лічильником.
        "tokens_est": -(-len(serialized) // 4),
    }
    return body


def _size(body: dict) -> int:
    return len(json.dumps(body, ensure_ascii=False))


def _share_repeated_intents(body: dict) -> None:
    """Keep each repeated intent once while retaining exact text by reference."""
    import hashlib

    catalog: dict[str, str] = {}
    changes = [*body.get("why_detail", [])]
    for commit in body.get("commit_history", []):
        changes.extend(commit.get("node_changes") or [])
    for change in changes:
        value = change.get("intent")
        if not isinstance(value, str) or not value:
            continue
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        catalog[digest] = value
        change.pop("intent")
        change["intent_ref"] = digest
    if catalog:
        body["intent_catalog"] = catalog


def _fold_chronology(items: list[dict]) -> list[dict]:
    """One displayed relation per object and direction; retain edge count."""
    folded: dict[tuple[str, str, str, str], dict] = {}
    for item in items:
        key = (str(item.get("object_id")), str(item.get("relation")),
               str(item.get("direction")), str(item.get("via")))
        if key in folded:
            folded[key]["count"] += int(item.get("count") or 1)
        else:
            folded[key] = dict(item)
    return list(folded.values())


def _compact_answer(body: dict) -> dict:
    """CLI overview; the full graph and deleted patch remain available."""
    out = dict(body)
    out["commit_history"] = [{
        "commit": item["commit"], "committed_at": item.get("committed_at"),
        "message": (item.get("message") or "").splitlines()[0],
        "evidence": item.get("evidence"),
        "direct_mutations": item.get("direct_mutations", 0),
        "agent_sources": sorted({str(s.get("source_kind"))
                                 for s in item.get("sessions") or []}),
        "change_reasons": item.get("change_reasons") or [],
        "deleted_lines": sum(len(p.get("deleted_lines") or [])
                             for p in item.get("patches") or []),
    } for item in body.get("commit_history") or []]
    detail = body.get("why_detail") or []
    unique: dict[tuple[str, str, str], dict] = {}
    for item in detail:
        key = (str(item.get("intent_ref") or ""),
               str(item.get("motive") or item.get("text") or ""),
               str(item.get("motive_source") or ""))
        if key not in unique:
            unique[key] = {k: item.get(k) for k in
                           ("kind", "intent_ref", "motive", "motive_source",
                            "evidence_level", "text", "note", "event_id", "ts")
                           if item.get(k)}
            unique[key]["semantic_cause_status"] = (
                item.get("semantic_cause_status") or
                ("agent_proposed" if item.get("motive") else "not_established"))
            if not item.get("motive"):
                unique[key]["note"] = (
                    "Намір є контекстом сесії; семантичну причину правки не встановлено")
            unique[key]["occurrences"] = 0
        unique[key]["occurrences"] += 1
    out["why_detail"] = list(unique.values())[:8]
    used = {item.get("intent_ref") for item in out["why_detail"]}
    out["intent_catalog"] = {key: value for key, value in
                             (body.get("intent_catalog") or {}).items() if key in used}
    out["provenance"] = []
    out["trust"] = {"objects": len(body.get("trust") or []),
                    "classes": sorted({str(item.get("trust_class"))
                                       for item in body.get("trust") or []})}
    out["omissions"] = [*body.get("omissions", []), {
        "kind": "compact_view", "object_id": None,
        "reason": (f"показано {len(out['why_detail'])} із {len(unique)} різних "
                   "контекстів правок; це не кількість доведених причин; "
                   "повні докази й вилучені рядки: --full"),
    }]
    return out


def _apply_budget(body: dict, budget_chars: int) -> dict:
    """Урізати відповідь під бюджет і НАЗВАТИ кожне урізання.

    Що не зникає за жодного бюджету: `answer`, `boundary`, `omissions` і
    факт наявності довіри. Контекст, який приховав межу власної повноти,
    гірший за порожній.

    Порядок зрізання — від найменш потрібного до найбільш: хронологія,
    походження, потім згортання довіри до лічильників за класами. Якщо
    навіть так не вміщається, це сказано прямо (`budget_exceeded`), а не
    замовчано: виміряно, що перша версія цієї функції рапортувала бюджет
    2 000 символів і віддавала 6 600 — бюджет був оголошений, але не
    виконувався.
    """
    # Leave room for the cost envelope appended by answer().
    budget_chars = max(0, budget_chars - 250)
    out = dict(body)
    notes: list[dict] = []

    if _size(out) > budget_chars and out.get("why_detail"):
        before = sum(len(item.get("text") or "") for item in out["why_detail"])
        kept: list[dict] = []
        for item in out["why_detail"]:
            room = max(0, budget_chars - _size({**out, "why_detail": kept}) - 400)
            trimmed = str(item.get("text") or "")[:room]
            if trimmed:
                kept.append({**item, "text": trimmed,
                             "truncated": len(trimmed) < len(item.get("text") or "")})
        out["why_detail"] = kept
        after = sum(len(item.get("text") or "") for item in kept)
        if after < before:
            notes.append({"object_id": None, "kind": "budget",
                          "reason": f"текст «чому» скорочено з {before} до {after} символів"})

    dropped = 0
    while _size(out) > budget_chars and out["chronology"]:
        out["chronology"] = out["chronology"][:-1]
        dropped += 1
    if dropped:
        notes.append({"object_id": None, "kind": "budget",
                      "reason": f"з хронології прибрано {dropped} записів"})

    if _size(out) > budget_chars and out.get("provenance"):
        kept = len(out["provenance"])
        out["provenance"] = []
        notes.append({"object_id": None, "kind": "budget",
                      "reason": f"походження ({kept} обʼєктів) прибрано; "
                                "читати через `why` без --budget"})

    if _size(out) > budget_chars and isinstance(out.get("trust"), list) and out["trust"]:
        counts: dict[str, int] = {}
        fresh: dict[str, int] = {}
        for item in out["trust"]:
            counts[str(item.get("trust_class"))] = counts.get(str(item.get("trust_class")), 0) + 1
            fresh[str(item.get("freshness"))] = fresh.get(str(item.get("freshness")), 0) + 1
        out["trust"] = {"objects": len(body["trust"]), "by_trust_class": counts,
                        "by_freshness": fresh, "detail": "згорнуто через бюджет"}
        notes.append({"object_id": None, "kind": "budget",
                      "reason": "довіру згорнуто до лічильників за класами"})

    if _size(out) > budget_chars and out.get("commit_history"):
        compact_history = []
        for item in out["commit_history"]:
            reduced = dict(item)
            patches = []
            for patch in item.get("patches") or []:
                copy = dict(patch)
                for key in ("deleted_lines", "added_lines"):
                    lines = list(copy.get(key) or [])
                    copy[f"{key}_total"] = len(lines)
                    copy[key] = lines[:2]
                patches.append(copy)
            reduced["patches"] = patches
            reduced["node_changes"] = [
                {key: value for key, value in change.items()
                 if key not in {"text", "motive", "intent"}}
                for change in item.get("node_changes") or []]
            compact_history.append(reduced)
        out["commit_history"] = compact_history
        notes.append({"object_id": None, "kind": "budget",
                      "reason": "історію коміту скорочено; повні вилучені й додані рядки "
                                "доступні без --budget"})

    if _size(out) + len(json.dumps(notes, ensure_ascii=False)) > budget_chars:
        lines = str(out.get("answer") or "").splitlines()
        shown = 0
        kept = []
        for line in lines:
            if line.startswith("          - "):
                shown += 1
                if shown > 2:
                    continue
            kept.append(line)
        if len(kept) < len(lines):
            out["answer"] = "\n".join(kept)
            notes.append({"object_id": None, "kind": "budget",
                          "reason": "у короткій відповіді показано лише 2 вилучені рядки; "
                                    "повний текст доступний без --budget"})

    if (_size(out) + len(json.dumps(notes, ensure_ascii=False)) > budget_chars
            and out.get("commit_history")):
        for item in out["commit_history"]:
            item["message"] = str(item.get("message") or "")[:160]
            item["mutations_for_node"] = [
                {key: value for key, value in mutation.items()
                 if key in {"event_id", "node_key"}}
                for mutation in item.get("mutations_for_node") or []]
            item["node_changes"] = [
                {key: value for key, value in change.items()
                 if key in {"event_id", "motive_source", "intent_source"}}
                for change in item.get("node_changes") or []]
            for patch in item.get("patches") or []:
                patch["deleted_lines"] = patch.get("deleted_lines", [])[:1]
                patch["added_lines"] = patch.get("added_lines", [])[:1]

    out["omissions"] = [*out["omissions"], *notes]
    size = _size(out)
    if size > budget_chars:
        out["omissions"] = [*out["omissions"], {
            "object_id": None, "kind": "budget_exceeded",
            "reason": f"бюджет {budget_chars} символів НЕ виконано: {size}; "
                      "менше вже не буває без втрати межі відповіді",
        }]
    return out
