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
        "SELECT from_id, to_id, relation_type, execution_effect, created_at "
        "FROM relations WHERE from_id = ? OR to_id = ? ORDER BY created_at, id",
        (node_key, node_key),
    )
    folded: dict[tuple, dict] = {}
    for row in rows:
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
SECOND_HOP = ("derived_from", "governed_by", "supersedes")

#: Глибина обходу. Два — бо саме стільки потрібно для ланцюга
#: вузол -> сесія -> намір. Більше означало б тягнути півграфа в кожну
#: відповідь і платити за це бюджетом.
MAX_DEPTH = 2


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


#: Скільки правок вузла показувати. Не «скільки влізе»: `--budget` ріже
#: цей блок ПЕРШИМ, тому брати більше безпечно, а менше — означає
#: мовчки відкинути частину історії вузла.
NODE_CHAIN_LIMIT = 24


def _node_chain(store, node_key: str, limit: int = NODE_CHAIN_LIMIT) -> list[dict]:
    """Ланцюг «намір -> мотив -> правка» САМЕ цього вузла.

    Докази лежать на ребрах `caused_by` (`relations.condition_json`), які
    писав `compaction_link.link_mutations`. Раніше `why` показував замість
    них сесійну автокомактацію — ланцюг усієї сесії, що цього вузла міг і
    не стосуватись. Виміряно: 12 ребер вузла проти перших 12 мутацій сесії.

    `rationale_source` зберігається дослівно, бо розрізняє силу доказу:
    `agent_reasoning_same_event` проти `agent_reasoning_prior_by_seq`.
    """
    rows = store.read(
        "SELECT condition_json, created_at FROM relations "
        "WHERE from_id = ? AND relation_type = 'caused_by' "
        "AND condition_json IS NOT NULL ORDER BY created_at, id",
        (node_key,),
    )
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        try:
            cond = json.loads(row["condition_json"])
        except (TypeError, ValueError):
            continue
        evidence = cond.get("evidence") or {}
        key = str(evidence.get("event_id") or "")
        if key and key in seen:
            continue
        seen.add(key)
        out.append({
            "kind": "node_change",
            "ts": evidence.get("ts"),
            "tool": evidence.get("tool"),
            "path": evidence.get("path"),
            "intent": evidence.get("intent"),
            "intent_seq": evidence.get("intent_seq"),
            "intent_source": evidence.get("intent_source"),
            "motive": cond.get("rationale") or "",
            "motive_source": evidence.get("rationale_source"),
            "evidence_level": cond.get("evidence_level"),
            "note": "мотив — слова агента, не доведене твердження",
        })
    return out[-limit:]


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


def _answer_text(node: dict, groups: dict, intents: list[dict]) -> str:
    lines: list[str] = []
    commit_facts = groups.get("commit_fact") or []
    sessions = groups.get("session") or []
    if commit_facts:
        fact = commit_facts[0]
        sem = fact.get("semantic") or {}
        lines.append(f"КОЛИ   {_time_of(fact)[:19] or '—'} — коміт "
                     f"{str(sem.get('commit') or '')[:7]}: {fact.get('title') or ''}")
    else:
        lines.append("КОЛИ   не встановлено: факту коміту для цього вузла немає")
    if sessions:
        sess = sessions[0]
        sem = sess.get("semantic") or {}
        lines.append(f"СЕСІЯ  {sem.get('session_id') or '—'}, компактація "
                     f"{sem.get('compaction_message_id') or '—'}")
    else:
        lines.append("СЕСІЯ  не встановлено: жодна сесія не торкалася вузла")
    if intents:
        lines.append(f"ЧОМУ   намірів користувача: {len(intents)}; "
                     f"перші {min(MAX_INTENTS, len(intents))}:")
        for item in intents[:MAX_INTENTS]:
            text = " ".join(str(item.get("text") or "").split())[:140]
            lines.append(f"       [{item.get('seq')}] {text or '(текст не збережено)'}")
    else:
        lines.append("ЧОМУ   не встановлено: намірів користувача не записано")
    autos = groups.get("auto_compaction") or []
    if autos:
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
           opencode_db: str | None = None) -> dict:
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

    edges = _edges(store, node_key)
    groups: dict[str, list[dict]] = {}
    chrono: list[dict] = []
    bodies: dict[str, dict] = {}
    for edge in edges:
        other = edge["to_id"] if edge["from_id"] == node_key else edge["from_id"]
        if other == node_key:
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
            "direction": "from_node" if edge["from_id"] == node_key else "to_node",
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
                if other in bodies or other == node_key:
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
    if commit:
        wanted = str(commit)[:7]
        for kind in ("commit_fact", "auto_compaction", "unresolved_mutations", "session"):
            matched = [b for b in groups.get(kind, [])
                       if str((b.get("semantic") or {}).get("commit") or "").startswith(wanted)]
            if matched:
                groups[kind] = matched

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

    text = _answer_text(node, groups, intents)
    # Текст «чому» (ланцюг намір -> мотив -> мутація) лежить у
    # `auto_compaction.summary`. Без нього відповідь називала джерело, але
    # не показувала його: виміряно приладом `answer_two_ways` — 2 факти з 7,
    # і обидва пропущені «чому» були саме тут.
    detail = _node_chain(store, node_key)
    if not detail:
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
            "node_changes": len([x for x in detail if x.get("kind") == "node_change"]),
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

    out["omissions"] = [*out["omissions"], *notes]
    size = _size(out)
    if size > budget_chars:
        out["omissions"] = [*out["omissions"], {
            "object_id": None, "kind": "budget_exceeded",
            "reason": f"бюджет {budget_chars} символів НЕ виконано: {size}; "
                      "менше вже не буває без втрати межі відповіді",
        }]
    return out
