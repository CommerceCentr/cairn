"""Context Compiler: детермінований, обмежений, пояснюваний пакет контексту.

Головне правило: trusted fact і agent proposal не мають однакового статусу.
Пакет ніколи не приховує排除 omissions, trust class чи причини включення.
Однакові work/revision/policy/agent/budget дають однаковий integrity_digest.
"""

from __future__ import annotations

import json
import re

from . import semantic, work as workmod
from .canon import canonical, sha256_text
from .errors import CairnError
from .ontology import AUTHORITATIVE, TRUST_CLASSES

PINNED_TIERS = {0}
TRUST_RANK = {"human_authoritative": 3, "verified_system": 3, "source_observed": 2,
              "agent_proposed": 1, "agent_inferred": 1, "imported_untrusted": 0,
              "rejected_or_revoked": 0}


def _query_tokens(text: str) -> set[str]:
    """Морфологічно стійкі префікси: 'магічні' й 'магічний' дають 'магіч'."""
    return {word[:5] for word in re.findall(r"[^\W\d_]{4,}", (text or "").casefold())
            if len(word) >= 5}


def build(store, work_id: str, *, agent_id: str | None = None, purpose: str = "edit",
          budget_chars: int | None = None, as_of: str | None = None,
          explain: bool = True, use_cache: bool = True) -> dict:
    if purpose not in {"understand", "plan", "edit", "review", "debug", "release", "explore"}:
        raise CairnError("bad_purpose", f"невідомий purpose {purpose}", exit_code=2)
    task = workmod.get_work(store, work_id)
    limit = int(budget_chars if budget_chars is not None
                else task.get("budget_limit_chars") or store.config_value("budget_chars")["value"])
    if limit <= 0:
        raise CairnError("bad_budget", "бюджет має бути додатним", exit_code=2)

    repo_id = (task.get("scope") or {}).get("repo_id")
    repo = store.read_one("SELECT * FROM repositories WHERE id = ?", (repo_id,)) if repo_id else None
    revision = repo["head"] if repo else None
    index_row = store.read_one(
        "SELECT recorded_at, payload_json FROM events WHERE event_type='code.indexed' AND aggregate_id=? "
        "ORDER BY seq DESC LIMIT 1", (repo_id,)
    ) if repo_id else None
    semantic_revision = json.loads(index_row["payload_json"])["index_digest"] if index_row else None
    policy_version = str(store.config_value("policy_version")["value"])

    # Ключ мусить змінюватися і тоді, коли змінилася довіра/життєвий цикл
    # семантичних об'єктів. Інакше прийняте людиною рішення лишилося б
    # невидимим у вже закешованому пакеті — це була б тиха втрата authority.
    semantic_state = sha256_text(canonical({
        "objects": store.read(
            "SELECT id, lifecycle_state, trust_class, updated_at FROM objects ORDER BY id"
        ),
        "assertions": store.read(
            "SELECT id, trust_class, valid_until, recorded_at FROM assertions ORDER BY id"
        ),
        "relations": store.read("SELECT id, valid_until FROM relations ORDER BY id"),
        "evidence": store.read("SELECT id, collected_at FROM evidence ORDER BY id"),
    }))
    cache_key = sha256_text(canonical({
        "work": task.get("updated_at"), "revision": revision, "semantic": semantic_revision,
        "semantic_state": semantic_state, "policy": policy_version,
        "agent": agent_id, "purpose": purpose, "budget": limit, "as_of": as_of,
        "compiler": "2.3-compact",
    }))
    if use_cache:
        cached = store.read_one("SELECT payload_json FROM context_packages WHERE cache_key = ?", (cache_key,))
        if cached:
            return json.loads(cached["payload_json"])

    relations = store.read("SELECT * FROM relations WHERE from_id = ? OR to_id = ?", (work_id, work_id))
    targets: list[str] = []
    governing: list[str] = []
    for rel in relations:
        endpoint = rel["to_id"] if rel["from_id"] == work_id else rel["from_id"]
        if rel["relation_type"] in {"targets_symbol", "validates", "tests"}:
            targets.append(endpoint)
        elif rel["relation_type"] in {"governed_by", "implements", "supports"}:
            governing.append(endpoint)
    targets = sorted(set(targets))
    governing = sorted(set(governing))

    candidates: list[dict] = []
    excluded: list[dict] = []
    # Хронологія не бере участі в ранжуванні фактів. Її текст резервується
    # заздалегідь, факти обираються в рештку бюджету, а потім блок додається
    # поверх. Інакше (tier=1, score=27) він випереджав факти й витісняв
    # куровані: виміряно 5/7 при 3000 та 7/26 при 6000 замість 7/7 і 25/26.
    timeline_item: dict | None = None

    def add(*, oid, tier, kind, text, trust, freshness, refs, reason, score_base=0,
            direct=True, tie_order=0):
        if trust not in TRUST_CLASSES:
            trust = "imported_untrusted"
        score = round(
            3 * score_base + 3 * TRUST_RANK.get(trust, 0)
            + (2 if freshness == "current" else 0)
            + (3 if direct else 1),
            3,
        )
        candidates.append({
            "object_id": oid, "tier": tier, "kind": kind, "text": text,
            "trust_class": trust, "freshness": freshness, "source_refs": refs,
            "reason_included": reason, "score": score, "tie_order": tie_order,
        })

    # 0. Контракт задачі — critical, не викидається ранжуванням.
    acceptance = " | ".join(str(item.get("id") or item.get("kind")) for item in task.get("acceptance") or [])
    add(oid=work_id, tier=0, kind="work_contract",
        text=f"TASK {work_id}: {task['title']}\nACCEPTANCE: {acceptance}",
        trust="human_authoritative", freshness="current",
        refs=[f"event://{task.get('updated_at')}"], reason="task contract is pinned", score_base=3)

    known = semantic.query(store, as_of=as_of, known_at=as_of, active_only=True)
    objects = {row["id"]: row for row in known["objects"]}

    # 1a. Хронологія додається ТІЛЬКИ тоді, коли є прийняті компактації,
    # прив'язані до цільового вузла. Вона не підміняє пер-фактні елементи:
    # ті лишаються окремими, бо бюджет і trust вирішуються пер-фактно.
    # Рядки FACTS дають лише порядок/посилання, без повтору повного тексту.
    target_set = set(targets)
    if target_set:
        related: list[dict] = []
        for oid, obj in objects.items():
            sem = obj.get("semantic") or {}
            commit = str(sem.get("commit") or "")
            if not commit or not (set(sem.get("scope") or []) & target_set):
                continue
            trust = obj.get("trust_class") or "imported_untrusted"
            if trust not in AUTHORITATIVE:
                excluded.append({"object_id": oid, "reason": "proposed_not_accepted",
                                 "trust_class": trust})
                continue
            related.append({
                "oid": oid, "date": str(obj.get("valid_from") or "")[:10], "commit": commit,
                "kind": "CMP" if sem.get("kind") == "compaction" else "FACT",
                "summary": " ".join(str(obj.get("summary") or "").split()),
                # Повне твердження з блоку WAS компактованого досьє. Без нього
                # CMP у timeline — лише 50-символьний teaser, який не несе
                # причини (виміряно на core/Engine/Controller.php: 0/5).
                "rationale": " ".join(str(sem.get("rationale") or "").split()),
                "fresh": _freshness(obj, revision, repo, known_at=as_of),
                "refs": (obj.get("provenance") or {}).get("event_ids") or [],
            })
        compactions = [row for row in related if row["kind"] == "CMP"]
        if compactions:
            # 3 CMP + 2 FACT — найкоротший блок, що ainda несе обидві мітки.
            # CMP і FACT беруться з різних комітів, інакше дедуп за комітом
            # ховав би один із маркерів (виміряно: 13 рядків і жодного [CMP]).
            order = lambda r: (r["date"], r["commit"], r["oid"])
            # Беремо CMP за релевантністю до питання, а не перші за датою:
            # інакше 7 досьє контролера ховають обидва витерті твердження.
            query = _query_tokens(f"{task.get('title') or ''} {purpose or ''}")

            def cmp_rank(row: dict):
                text = row["rationale"] or row["summary"]
                return (-len(query & _query_tokens(text)), row["date"], row["commit"], row["oid"])

            ranked = [(len(query & _query_tokens(r["rationale"] or r["summary"])), r)
                      for r in compactions]
            matching = [row for score, row in sorted(
                ranked, key=lambda pair: (-pair[0], pair[1]["date"], pair[1]["commit"], pair[1]["oid"])
            ) if score > 0]
            # Досьє, що прямо відповідає на питання, показуємо повністю й одне:
            # три CMP за датою витісняли друге речення причини з бюджету.
            cmp_picks = matching[:3] if matching else sorted(compactions, key=order)[:3]
            cmp_commits = {r["commit"] for r in cmp_picks}
            facts = [r for r in related if r["kind"] == "FACT"]
            fact_picks = [r for r in sorted(facts, key=order)
                          if r["commit"] not in cmp_commits][:2]
            if not fact_picks and facts:
                fact_picks = sorted(facts, key=order)[:1]
            shown = sorted(cmp_picks + fact_picks, key=order)
            commits_all = {r["commit"] for r in related}
            omitted = len(commits_all - {r["commit"] for r in shown})
            # Повне твердження не повинно зникати разом із блоком, якщо воно
            # не влізло: спочатку звужуємо обріз, і тільки потім кидаємо блок.
            timeline_item = None
            for cap in (800, 600, 400, 300, 200, 120, 50):
                lines = []
                for row in shown:
                    if row["kind"] != "CMP":
                        text = row["oid"]
                    else:
                        full = row["rationale"] or row["summary"]
                        used_cap = cap if row["rationale"] else 50
                        text = full[:used_cap] + ("…" if len(full) > used_cap else "")
                    marker = f"[{row['fresh']}]" if row["fresh"] != "current" else ""
                    lines.append(
                        f"{row['date']} commit:{row['commit'][:7]} [{row['kind']}] {text} {marker}".rstrip()
                    )
                if omitted:
                    lines.append(f"... +{omitted} комітів у повній історії вузла")
                timeline_text = f"HISTORY ({len(lines)} entries)\n" + "\n".join(lines)
                # Лишаємо місце під pin-контракт задачі й хоча б один факт.
                if len(timeline_text) + 200 > limit:
                    continue
                freshness = ("historical" if all(row["fresh"] == "historical" for row in shown)
                             else "current")
                timeline_item = {
                    "object_id": "history:" + sha256_text("\n".join(lines))[:12],
                    "tier": 5, "kind": "history_timeline",
                    "text": timeline_text,
                    "trust_class": "human_authoritative", "freshness": freshness,
                    "source_refs": [ref for row in shown for ref in row["refs"]][:20],
                    "reason_included": "accepted compactions reference the ordered history of target symbol",
                    "score": None, "tie_order": 0,
                    "rationale_cap": cap,
                }
                if not explain:
                    timeline_item.pop("reason_included")
                    timeline_item.pop("score")
                break

    # 1. Чинні рішення/контракти/інваріанти, що керують задачею.
    for oid in governing:
        obj = objects.get(oid)
        if obj is None:
            continue
        # `governed_by` перевантажено: ним батч-гейт прив'язує компактації
        # до задачі (semantic.accept_batch). Компактація не є керуючим
        # рішенням — 37 таких об'єктів у tier=1 витісняли факти (c7 6/7,
        # c26 7/26 замість 7/7 і 25/26). Хронологія додається окремим блоком.
        if (obj.get("semantic") or {}).get("kind") == "compaction":
            continue
        trust = obj.get("trust_class", "imported_untrusted")
        if trust not in AUTHORITATIVE:
            excluded.append({"object_id": oid, "reason": "untrusted_trust_class", "trust_class": trust})
            continue
        semantic_body = obj.get("semantic") or {}
        constraints = ", ".join(str(x) for x in semantic_body.get("constraints") or [])
        # Пріоритет оголошує джерело факту, а не довжина рядка: довгі речення
        # з причиною/наслідком не повинні зрізатися першими при малому бюджеті.
        priority = min(2, max(0, int(semantic_body.get("priority") or 0)))
        valid_from = str(obj.get("valid_from") or "")
        tie_order = (int(valid_from[:10].replace("-", ""))
                     if len(valid_from) >= 10 and valid_from[:4].isdigit() else 0)
        # Компактний рендер: `oid` і `constraints` лишаються structural-полями
        # елемента та доступні через explain(); у text вони з'їдали 2320 chars
        # із 5937 (виміряно в cairn_decompose.json), не несучи нової інформації.
        add(oid=oid, tier=1, kind=obj.get("object_type") or "decision",
            text=f"FACT {str(semantic_body.get('commit') or '')[:7]}: "
                 f"{obj.get('summary') or obj.get('title')}",
            trust=trust, freshness=_freshness(obj, revision, repo, known_at=as_of),
            refs=(obj.get("provenance") or {}).get("event_ids") or [],
            reason="active decision governing target symbol", score_base=3 + priority,
            tie_order=tie_order)

    # 2. Поточні факти коду за stable symbol identity і revision.
    for target in targets:
        sym = store.read_one(
            "SELECT * FROM code_symbols WHERE stable_key = ? ORDER BY revision DESC LIMIT 1", (target,)
        )
        if sym is None:
            obj = objects.get(target)
            if obj is not None:
                sym = {"stable_key": obj.get("stable_key"), "qualified_name": obj.get("title") or target,
                       "path": (obj.get("semantic") or {}).get("scope", [None])[0], "revision": None}
            else:
                excluded.append({"object_id": target, "reason": "symbol_not_indexed"})
                continue
        sym_revision = sym.get("revision")
        fresh = "current" if sym_revision == revision else "stale"
        span = json.loads(sym.get("span_json") or "{}")
        add(oid=target, tier=2, kind="code_symbol",
            text=f"SYMBOL {target} @ {sym_revision or 'unknown'}\n{sym.get('qualified_name')} "
                 f"({sym.get('path')}:{span.get('start_line')}-{span.get('end_line')})",
            trust="source_observed", freshness=fresh,
            refs=[f"git://{repo_id}@{sym_revision}"], reason="target symbol at source revision", score_base=2)

    # 3. Твердження з provenance; agent-only лишається поза active instruction set.
    assertion_rows = []
    subjects = sorted(set(targets + governing + [work_id]))
    if subjects:
        marks = ",".join("?" for _ in subjects)
        assertion_rows = store.read(
            f"SELECT * FROM assertions WHERE subject_id IN ({marks}) ORDER BY recorded_at, id", tuple(subjects)
        )
    for row in assertion_rows:
        if as_of and row["recorded_at"] > as_of:
            continue
        trust = row["trust_class"]
        if trust not in AUTHORITATIVE and trust != "source_observed":
            excluded.append({"object_id": row["id"], "reason": "untrusted_trust_class", "trust_class": trust})
            continue
        if row["valid_until"] and (as_of is None or row["valid_until"] <= as_of):
            excluded.append({"object_id": row["id"], "reason": "superseded"})
            continue
        literal = json.loads(row["literal_json"]) if row["literal_json"] else None
        add(oid=row["id"], tier=3, kind="assertion",
            text=f"ASSERT {row['predicate']}: {canonical(literal)} [{trust}]",
            trust=trust, freshness="current" if not row["valid_until"] else "superseded",
            refs=[row["source_event_id"]] if row["source_event_id"] else [],
            reason="authoritative assertion about target", score_base=1)

    # 4. Докази — тільки з перевіреним ревізійним слідом.
    for ev in store.read("SELECT * FROM evidence WHERE subject_id = ? ORDER BY collected_at, id", (work_id,)):
        if as_of and ev["collected_at"] > as_of:
            continue
        meta = json.loads(ev["metadata_json"])
        if meta.get("commit") and revision and meta["commit"] != revision and purpose != "explore":
            excluded.append({"object_id": ev["id"], "reason": "evidence_for_other_revision"})
            continue
        add(oid=ev["id"], tier=4, kind="evidence",
            text=f"EVIDENCE {ev['evidence_type']}: {ev['content_hash']} "
                 f"commit={meta.get('commit')} passed={meta.get('passed')}",
            trust=ev["trust_class"] if ev["trust_class"] in AUTHORITATIVE else "source_observed",
            freshness="current", refs=[ev["source_ref"]] if ev["source_ref"] else [],
            reason="evidence attached to work item", score_base=1)

    # Резерв під хронологію вираховується до селекції: факти не змагаються
    # з нею за місце, але й не зникають через неї з ранжування.
    reserve = len(timeline_item["text"]) if timeline_item else 0
    if reserve >= limit:
        if timeline_item:
            excluded.append({"object_id": timeline_item["object_id"], "reason": "budget",
                             "bytes": reserve})
            timeline_item = None
        reserve = 0
    effective_limit = limit - reserve
    candidates.sort(key=lambda item: (item["tier"], -item["score"], -item["tie_order"], item["object_id"]))
    items: list[dict] = []
    used = 0
    for cand in candidates:
        size = len(cand["text"])
        if used + size > effective_limit:
            if cand["tier"] in PINNED_TIERS and not items:
                raise CairnError(
                    "budget_insufficient", "навіть critical context не вміщається в бюджет",
                    details={"limit": effective_limit, "required": size},
                )
            excluded.append({"object_id": cand["object_id"], "reason": "budget", "bytes": size})
            continue
        used += size
        item = dict(cand)
        if not explain:
            item.pop("reason_included", None)
            item.pop("score", None)
        items.append(item)
    if timeline_item:
        used += reserve
        items.append(timeline_item)

    # Повнота контексту — перевірна власність, а не враження. Бюджет лишається
    # стелею; якщо прийнятий факт вузла не увійшов, це видно в missing_ids.
    coverage_ids = sorted({
        oid for oid, obj in objects.items()
        if (obj.get("semantic") or {}).get("commit")
        and (set((obj.get("semantic") or {}).get("scope") or []) & target_set)
        and obj.get("trust_class") in AUTHORITATIVE
    })
    # Компактації не мають власного item: вони represented блоком
    # history_timeline з синтетичним object_id. Вважаємо їх покритими лише
    # тоді, коли блок справді увійшов у пакет (інакше missing лишається).
    compaction_ids = {
        oid for oid in coverage_ids
        if (objects[oid].get("semantic") or {}).get("kind") == "compaction"
    }
    included_ids = {item["object_id"] for item in items}
    missing_ids = [
        oid for oid in coverage_ids
        if oid not in included_ids and not (oid in compaction_ids and timeline_item)
    ]

    body = {
        "work_item_id": work_id,
        "target_revision": {"repository_id": repo_id, "commit": revision},
        "policy_version": policy_version,
        "purpose": purpose,
        "agent_id": agent_id,
        "as_of": as_of,
        "items": items,
        "excluded": excluded,
        "budget": {
            "unit": "chars", "limit": limit, "estimated": used,
            "remaining": limit - used, "confidence": "measured",
            "bytes": len(canonical(items).encode("utf-8")),
            "coverage": {
                "accepted_for_target": len(coverage_ids),
                "included": len(coverage_ids) - len(missing_ids),
                "missing_ids": missing_ids,
                "compactions_via_timeline": len(compaction_ids) if timeline_item else 0,
            },
        },
    }
    content_id = sha256_text(canonical(body))
    package = {
        "context_id": f"ctx_{content_id[:26]}",
        "cache_key": cache_key,
        **body,
        "integrity_digest": f"sha256:{content_id}",
    }
    return package


def persist(store, package: dict, actor: dict) -> dict:
    body = {
        "id": package["context_id"],
        "cache_key": package["cache_key"],
        "package": package,
    }

    def op(con):
        ev = store.append(con, "context.recorded", package["context_id"], body, actor)
        return {"context_id": package["context_id"], "event_id": ev["event_id"], "replayed": ev["replayed"]}

    return store.mutate(op)


def show(store, context_id: str) -> dict:
    row = store.read_one("SELECT payload_json FROM context_packages WHERE id = ?", (context_id,))
    if row is None:
        raise CairnError("not_found", f"контекст {context_id} не знайдено", exit_code=1)
    return json.loads(row["payload_json"])


def explain(store, context_id: str) -> dict:
    package = show(store, context_id)
    return {
        "context_id": context_id,
        "integrity_digest": package["integrity_digest"],
        "included": [{"object_id": item["object_id"], "reason": item.get("reason_included"),
                      "trust": item["trust_class"], "freshness": item["freshness"], "score": item.get("score")}
                     for item in package["items"]],
        "excluded": package["excluded"],
        "budget": package["budget"],
    }


def _object_commit(obj: dict) -> str | None:
    for constraint in (obj.get("semantic") or {}).get("constraints") or []:
        if isinstance(constraint, str) and constraint.startswith("commit:"):
            return constraint.split(":", 1)[1]
    return None


def _freshness(obj: dict, revision: str | None, repo: dict | None, *, known_at: str | None) -> str:
    if obj.get("lifecycle_state") in {"revoked", "superseded", "rejected"}:
        return "superseded"
    if known_at and obj.get("recorded_at", "") > known_at:
        return "historical"
    commit = _object_commit(obj)
    if commit and revision and not (commit.startswith(revision) or revision.startswith(commit)):
        return "historical"
    return "current"
