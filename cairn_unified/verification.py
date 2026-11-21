"""Детермінований верифікатор пропозицій.

Людина в цій моделі — спостерігач. Авторитет надається не актором тексту, а
перевіркою: коміт має резолвитися локально, пак — мати незмінний digest, вузол —
бути індексованим, а суперечних пропозицій не існувати. Якщо перевірка не
встановила факту, об'єкт лишається `agent_proposed` і отримує ескалацію
`не встановлено`; тільки тут потрібна людина.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import code, merge, semantic
from .canon import audit_digest, canonical, sha256_text
from .code import git
from .ontology import AUTHORITATIVE

POLICY_VERSION = "auto-verify/1.0"
VERIFIER = {"kind": "system", "id": "cairn-verifier"}


def _object(store, object_id: str) -> dict:
    row = store.read_one("SELECT payload_json FROM objects WHERE id = ?", (object_id,))
    if row is None:
        from .errors import CairnError
        raise CairnError("not_found", f"об'єкт {object_id} не знайдено", exit_code=1)
    return json.loads(row["payload_json"])


def _contradictions(store, body: dict, object_id: str) -> list[str]:
    """Конфлікт — різні резюме тієї самої стабільної ідентичності.

    Факт коміту й компактація з того самого коміту мають різні резюме за
    призначенням; це не суперечність. Порівнюємо лише `stable_key` + kind.
    """
    key = str(body.get("stable_key") or "")
    kind = str((body.get("semantic") or {}).get("kind") or "")
    if not key:
        return []
    found = []
    for row in store.read("SELECT id, payload_json FROM objects WHERE id != ?", (object_id,)):
        other = json.loads(row["payload_json"])
        if other.get("lifecycle_state") in {"revoked", "rejected", "superseded"}:
            continue
        if str(other.get("stable_key") or "") != key:
            continue
        if str((other.get("semantic") or {}).get("kind") or "") != kind:
            continue
        if sha256_text(other.get("summary") or "") != sha256_text(body.get("summary") or ""):
            found.append(row["id"])
    return sorted(found)


def _db_path(sem: dict, override: str | None) -> str:
    """Де лежить БД агента. У payload її більше немає — і правильно.

    Порядок: явний параметр -> застаріле поле обʼєкта (сховища, створені до
    цієї правки) -> `OPENCODE_DB`/домашня тека. Застаріле поле читаємо, але
    НЕ пишемо: інакше воно б жило вічно.
    """
    if override:
        return str(override)
    legacy = str(sem.get("opencode_db") or "")
    if legacy:
        return legacy
    try:
        from adapters import opencode
        return opencode.default_db()
    except Exception:                             # noqa: BLE001 — немає адаптера
        return ""


def _source_failure(source: str, exc: BaseException, what: str) -> str:
    """Причина, що розрізняє ДВІ різні речі під однією міткою.

    Було: будь-яке виключення ставало `unresolved:*_source_unavailable`,
    тобто зламаний верифікатор був невідрізненний від відсутнього джерела,
    і обʼєкт ніколи не позначався ХИБНИМ — лише НЕПЕРЕВІРЕНИМ.

    Якір — ЗМІСТ, а не тип виключення: відсутній файл БД дає
    `sqlite3.OperationalError`, а не `FileNotFoundError`, тому вгадувати по
    типу не можна. Питаємо диск: джерела немає -> `unresolved`; джерело є,
    а читання впало -> `verifier_error`, бо це дефект перевірки.
    """
    if not source or not Path(source).exists():
        return f"unresolved:{what}_source_missing"
    return f"verifier_error:{what}_source_{type(exc).__name__}"

def check_object(store, body: dict, *, repo_path: str | Path | None = None,
                 packs_dir: str | Path | None = None,
                 opencode_db: str | None = None) -> tuple[list[dict], list[str]]:
    """Повернути (пройдені перевірки, причини ескалації). Без записів."""
    sem = body.get("semantic") or {}
    checks: list[dict] = []
    reasons: list[str] = []
    # Зміна може бути нескоміченою: тоді резолвимо base_revision, але НЕ
    # називаємо її комітом-причиною. Злиття взагалі не має одного коміту-причини:
    # воно несе git_head і перевірений список commit_links (див. гілку нижче).
    # Для всіх інших видів поле не змінилося.
    # merge_context не має ані commit, ані base_revision: його часова прив'язка
    # — git_head + commit_links, які перевіряються в Own гілці нижче.
    skip_commit = sem.get("kind") == "merge_context"
    base_revision_only = not sem.get("commit") and bool(
        sem.get("kind") == "user_requested_change" and sem.get("base_revision"))
    commit = str(sem.get("base_revision") if base_revision_only
                 else sem.get("commit") or "")
    if skip_commit:
        pass  # часова прив'язка злиття перевіряється в гілці merge_context
    elif not commit:
        reasons.append("unresolved:commit_absent")
    else:
        try:
            full = git.git(str(repo_path), "rev-parse", f"{commit}^{{commit}}").strip() if repo_path else ""
        except Exception:
            full = ""
        if full:
            checks.append({
                "check": "base_revision_resolves" if base_revision_only else "commit_resolves",
                "value": full[:12],
            })
        else:
            reasons.append("unresolved:base_revision_not_found" if base_revision_only
                           else "unresolved:commit_not_found")

    if sem.get("kind") == "compaction":
        pack_name = sem.get("pack") or ""
        pack_file = Path(packs_dir) / pack_name if packs_dir and pack_name else None
        if pack_file and pack_file.is_file():
            digest = sha256_text(pack_file.read_text(encoding="utf-8", errors="replace"))
            if digest == sem.get("pack_digest"):
                checks.append({"check": "pack_digest_matches", "value": digest[:12]})
            else:
                reasons.append("unresolved:pack_digest_mismatch")
        else:
            reasons.append("unresolved:pack_missing")

    if sem.get("kind") == "session":
        # Тотожність сесії — реальний рядок компактації в opencode.db, а не
        # SHA коміту й не власний текст агента. Джерело читається з БД
        # адаптером у режимі mode=ro; НЕ з payload об’єкта, бо звірка самого
        # себе з собою була б тавтологією (виміряний клас помилок №13).
        db_path = _db_path(sem, opencode_db)
        msg_id = str(sem.get("compaction_message_id") or "")
        source = None
        source_failure = ""
        if db_path and msg_id:
            try:
                from adapters import opencode
                source = opencode.compaction(db_path, str(sem.get("session_id") or "") or None,
                                             message_id=msg_id)
            except Exception as exc:              # noqa: BLE001 — класифікуємо нижче
                source = None
                source_failure = _source_failure(db_path, exc, "session")
        if source is not None:
            if sha256_text(str(source.get("summary") or "")) == sem.get("summary_sha256"):
                checks.append({"check": "session_summary_matches",
                               "value": str(sem.get("summary_sha256"))[:12]})
            else:
                reasons.append("unresolved:session_summary_mismatch")
            keep_id = str(source.get("keep_id") or "")
            if keep_id == str(sem.get("keep_id") or ""):
                checks.append({"check": "session_keep_id_matches", "value": keep_id[-12:]})
            else:
                reasons.append("unresolved:session_keep_id_mismatch")
        else:
            reasons.append(source_failure or "unresolved:session_source_unavailable")

    if sem.get("kind") == "unresolved_mutations":
        # «Чому відкинуто» перевіряється тим самим класифікатором, що й
        # ребра: повторне читання opencode.db + поточний індекс вузлів.
        # Об'єкт не звіряється з власним текстом (тавтологія).
        db_path = _db_path(sem, opencode_db)
        try:
            from adapters import opencode
            from . import compaction_link
            events = list(opencode.read(db_path, str(sem.get("session_id") or "") or None))
            _ready, recomputed = compaction_link.mutation_readiness(
                store, events, str(sem.get("repo_id") or ""),
                tuple(sem.get("repo_roots") or ()))
            if compaction_link.unresolved_digest(recomputed) == sem.get("unresolved_digest"):
                checks.append({"check": "unresolved_reasons_match_db",
                               "value": str(len(recomputed))})
            else:
                reasons.append("unresolved:unresolved_reasons_mismatch")
        except Exception as exc:                  # noqa: BLE001 — класифікуємо
            reasons.append(_source_failure(db_path, exc, "unresolved"))

    if sem.get("kind") == "zone_descriptors":
        # Дескриптор зони не звіряється з власним текстом. Перечитуємо файли
        # дескрипторів і журналів: якщо head/journal_lines розійшлися або
        # локи двох зон збіглися, об'єкт ескалується, а не стає фактом.
        try:
            from . import zones as zone_mod
            declared = [str(r.get("zone") or "") for r in (sem.get("zones") or [])]
            recomputed = zone_mod.snapshot(store, declared)
            if audit_digest(recomputed) == sem.get("digest"):
                checks.append({"check": "zone_descriptors_match_files",
                               "value": str(len(recomputed))})
                locks = {r["lock_file"] for r in recomputed}
                if len(locks) == len(recomputed):
                    checks.append({"check": "zone_locks_distinct",
                                   "value": str(len(locks))})
                else:
                    reasons.append("unresolved:zone_locks_shared")
                if sem.get("portable") and sem.get("bundle_digest"):
                    bundle = zone_mod.export_bundle(store, declared)
                    fed = bundle.get("cairn_bundle") or {}
                    chain_ok = bool(fed.get("chain_ok")) \
                        and len(fed.get("events") or []) >= int(sem.get("causal_event_count") or 0)
                    if zone_mod.state_digest(bundle) == sem.get("bundle_digest") \
                            and chain_ok \
                            and not any(Path(str(d.get("repo") or "")).is_absolute()
                                        for d in bundle.get("descriptors") or []):
                        checks.append({"check": "zone_bundle_portable",
                                       "value": bundle["digest"][:12]})
                        broken = []
                        for ref in (bundle.get("descriptors") or [{}])[0].get("context_refs") or []:
                            rel = str(ref.get("path") or "")
                            target = Path(str(store.root)) / rel
                            payload = bundle.get("context_objects") or {}
                            if not rel.startswith(".cairn/objects/") \
                                    or not target.is_file() or rel not in payload \
                                    or str(payload[rel].get("id")) != str(ref.get("object_id")):
                                broken.append(rel)
                        if broken:
                            reasons.append("unresolved:zone_context_refs_broken:"
                                           + ",".join(broken[:2]))
                        else:
                            checks.append({"check": "zone_context_refs_resolve",
                                           "value": str(len(bundle.get("context_objects") or {}))})
                    else:
                        reasons.append("unresolved:zone_bundle_mismatch")
            else:
                reasons.append("unresolved:zone_descriptors_mismatch")
        except Exception as exc:                  # noqa: BLE001 — класифікуємо
            reasons.append(_source_failure(str(store.root), exc, "zone"))

    if sem.get("kind") == "auto_compaction":
        # Автокомактація не має права свідчити сама за себе: кожен намір
        # перечитується з opencode.db у mode=ro і звіряється за digest тексту.
        # Джерело — user-повідомлення, а не згенерований агентами текст.
        db_path = _db_path(sem, opencode_db)
        refs = sem.get("intent_refs") or []
        source_texts: dict[str, str] = {}
        if db_path:
            try:
                from adapters import opencode
                for rec in opencode.read(db_path,
                                         str(sem.get("session_id") or "") or None):
                    if rec["kind"] == "user_intent":
                        source_texts[str(rec["message_id"])] = str(rec["text"])
            except Exception:
                source_texts = {}
        if not refs:
            reasons.append("unresolved:auto_compaction_no_intent_refs")
        else:
            bad = [str(r.get("message_id")) for r in refs
                   if sha256_text(source_texts.get(str(r.get("message_id") or ""), ""))
                   != r.get("text_sha256")]
            if bad:
                reasons.append("unresolved:auto_compaction_intent_mismatch:"
                               + ",".join(bad[:2]))
            else:
                checks.append({"check": "auto_compaction_intents_match_db",
                               "value": str(len(refs))})

    if sem.get("kind") == "user_requested_change":
        # Зміна, яку попросив користувач, не доводиться текстом агента.
        # Перевіряємо три речі: намір у mode=ro БД агента, базову ревізію git
        # і наявність файлів, які справді змінено. Коміт НЕ вимагаємо —
        #Changes можуть бути нескоміченими, і це не має видаватися комітом.
        db_path = _db_path(sem, opencode_db)
        source_texts: dict[str, str] = {}
        if db_path:
            try:
                from adapters import opencode
                for rec in opencode.read(db_path,
                                         str(sem.get("session_id") or "") or None):
                    if rec["kind"] == "user_intent":
                        source_texts[str(rec["message_id"])] = str(rec["text"])
            except Exception:
                source_texts = {}
        refs = sem.get("intent_refs") or []
        if not refs:
            reasons.append("unresolved:change_no_intent_refs")
        else:
            bad = [str(r.get("message_id")) for r in refs
                   if sha256_text(source_texts.get(str(r.get("message_id") or ""), ""))
                   != r.get("text_sha256")]
            if bad:
                reasons.append("unresolved:change_intent_mismatch:" + ",".join(bad[:2]))
            else:
                checks.append({"check": "change_intents_match_db",
                               "value": str(len(refs))})
        paths = [str(p) for p in (sem.get("changed_paths") or [])]
        if not paths:
            reasons.append("unresolved:change_paths_absent")
        else:
            missing = [p for p in paths
                       if not (Path(str(repo_path)) / p).is_file()] if repo_path else paths
            if missing:
                reasons.append("unresolved:change_paths_absent:" + ",".join(missing[:2]))
            else:
                checks.append({"check": "change_paths_present", "value": str(len(paths))})

    if sem.get("kind") == "merge_context":
        # Злиття не свідчить саме за себе: тотожність репозиторію, HEAD і кожен
        # хеш коміту перечитується локальним git; подія merge.recorded шукається
        # в журналі. Текст самого об'єкта участі не бере.
        repo = str(repo_path) if repo_path else ""
        if not repo:
            reasons.append("unresolved:merge_repo_path_absent")
        else:
            try:
                ident = code.git.identity(repo)
            except Exception:
                ident = {}
            if ident.get("id") and ident.get("id") == sem.get("repo_id"):
                checks.append({"check": "merge_repo_identity_matches",
                               "value": str(ident["id"])[:12]})
            else:
                reasons.append("unresolved:merge_repo_identity_mismatch")
            wanted = str(sem.get("git_head") or "")
            full_head = ""
            if wanted:
                try:
                    full_head = git.git(repo, "rev-parse", f"{wanted}^{{commit}}").strip()
                except Exception:
                    full_head = ""
            if full_head and full_head == wanted:
                checks.append({"check": "merge_git_head_resolves",
                               "value": full_head[:12]})
            else:
                reasons.append("unresolved:merge_git_head_unresolved")
        good_links = 0
        bad_links: list[str] = []
        for item in (sem.get("commit_links") or []):
            requested = str((item or {}).get("requested") or "")
            resolved = ""
            if requested and repo:
                try:
                    resolved = git.git(repo, "rev-parse", f"{requested}^{{commit}}").strip()
                except Exception:
                    resolved = ""
            if resolved and resolved.startswith(requested):
                good_links += 1
            else:
                bad_links.append(requested)
        if good_links and not bad_links:
            checks.append({"check": "merge_git_links_resolved", "value": str(good_links)})
        else:
            reasons.append("unresolved:merge_git_link_unresolved:"
                           + ",".join(bad_links[:2]))
        recorded = None
        for row in store.read("SELECT payload_json FROM events "
                              "WHERE event_type = 'merge.recorded'"):
            candidate = json.loads(row["payload_json"])
            if candidate.get("bundle_digest") == sem.get("source_bundle_digest") \
                    and candidate.get("git_head") == sem.get("git_head"):
                recorded = candidate
                break
        if recorded is None:
            reasons.append("unresolved:merge_event_absent")
        else:
            checks.append({"check": "merge_event_present",
                           "value": str(recorded.get("git_head") or "")[:12]})
            if sha256_text(canonical(recorded.get("conflicts") or [])) \
                    == sha256_text(canonical(sem.get("conflicts") or [])):
                checks.append({"check": "merge_conflicts_recorded",
                               "value": str(len(recorded.get("conflicts") or []))})
            else:
                reasons.append("unresolved:merge_conflicts_mismatch")

    addresses = [str(x) for x in (sem.get("scope") or [])]
    if sem.get("node_address"):
        addresses.append(str(sem["node_address"]))
    # Сесія не має одного scope: вона пов'язана з багатьма вузлами ребрами
    # `caused_by`, і кожне ребро вже перевірено через nodes.resolve на момент
    # створення. Тому для сесії перевіряємо наявність хоча б одного такого
    # ребра з індексованим вузлом, а не вимагаємо `scope` (інакше сесія з 29
    # підтвердженими мутаціями завжди ескалувала б з node_absent).
    if sem.get("kind") == "session" and not addresses:
        linked = store.read_one(
            "SELECT r.from_id FROM relations r "
            "JOIN code_symbols c ON c.stable_key = r.from_id "
            "WHERE r.to_id = ? AND r.relation_type = 'caused_by' LIMIT 1",
            (str(body.get("id") or ""),),
        )
        if linked:
            checks.append({"check": "session_has_indexed_node",
                           "value": str(linked["from_id"])[:40]})
            addresses = [str(linked["from_id"])]
        else:
            reasons.append("unresolved:session_no_indexed_node")
    if not addresses:
        # dup: злиття прив'язане до git-хешів (гілка merge_context), а не до
        # одного вузла індексу; вимагати scope тут було б хибною перевіркою.
        if sem.get("kind") not in {"session", "merge_context"}:
            reasons.append("unresolved:node_absent")
    else:
        found = False
        for addr in addresses:
            row = store.read_one(
                "SELECT stable_key FROM code_symbols WHERE qualified_name = ? OR stable_key = ? LIMIT 1",
                (addr, addr),
            )
            if row:
                found = True
                break
        if found:
            checks.append({"check": "node_indexed", "value": addresses[0][:60]})
        else:
            reasons.append("unresolved:node_not_indexed")

    clashes = _contradictions(store, body, str(body.get("id") or ""))
    if clashes:
        reasons.append("ambiguous:" + ",".join(clashes[:3]))
    else:
        checks.append({"check": "no_contradictions", "value": "ok"})

    return checks, reasons


def verify_object(store, object_id: str, *, repo_path: str | Path | None = None,
                  packs_dir: str | Path | None = None,
                  opencode_db: str | None = None,
                  actor: dict | None = None) -> dict:
    """Прийняти об'єкт за перевіркою або ескалувати як 'не встановлено'."""
    actor = actor or VERIFIER
    body = _object(store, object_id)
    current_trust = body.get("trust_class")
    current = body.get("verification") or {}
    if current_trust in AUTHORITATIVE and current.get("policy_version") == POLICY_VERSION:
        return {"object_id": object_id, "verified": True, "replayed": True,
                "reasons": [], "checks": current.get("checks") or []}
    if body.get("lifecycle_state") in {"revoked", "rejected", "superseded"}:
        return {"object_id": object_id, "verified": False, "reasons": ["unresolved:not_active"],
                "checks": [], "escalated": False}

    checks, reasons = check_object(store, body, repo_path=repo_path, packs_dir=packs_dir,
                                 opencode_db=opencode_db)
    digest = sha256_text(canonical({"checks": checks, "reasons": reasons}))

    def op(con):
        if not reasons:
            updated = dict(body)
            updated["lifecycle_state"] = "accepted"
            updated["trust_class"] = "verified_system"
            updated["review_state"] = "verified"
            updated["updated_at"] = store.now()
            updated["verification"] = {
                "policy_version": POLICY_VERSION, "checks": checks,
                "verifier": actor, "verified_at": store.now(), "digest": digest,
            }
            store.append(con, "object.upserted", object_id, updated, actor)
            store.append(con, "object.verified", object_id,
                         {"object_id": object_id, "trust_class": "verified_system",
                          "checks": checks, "policy_version": POLICY_VERSION},
                         actor, idempotency_key=f"verify-ok:{object_id}:{digest}")
            store.append(con, "assertion.recorded", object_id, {
                "id": "asr-" + digest[:12], "subject_id": object_id,
                "predicate": "authority.verified_by_system", "claim_type": "governance",
                "polarity": "asserted", "literal": updated["verification"],
                "trust_class": "verified_system", "confidence": None,
                "valid_from": store.now(), "valid_until": None,
                "recorded_at": store.now(), "author": actor, "review_state": "verified",
                "scope": [],
            }, actor, idempotency_key=f"verify-authority:{object_id}:{digest}")
            return {"object_id": object_id, "verified": True, "replayed": False,
                    "checks": checks, "reasons": []}

        store.append(con, "verification.escalated", object_id, {
            "object_id": object_id, "decision": "не встановлено", "reasons": reasons,
            "policy_version": POLICY_VERSION,
        }, actor, idempotency_key=f"verify-escalate:{object_id}:{digest}")
        store.append(con, "assertion.recorded", object_id, {
            "id": "asr-needs-" + digest[:12], "subject_id": object_id,
            "predicate": "needs.human.clarification", "claim_type": "governance",
            "polarity": "asserted", "literal": {"reasons": reasons, "object_id": object_id},
            "trust_class": "agent_proposed", "confidence": 0.4,
            "valid_from": store.now(), "valid_until": None,
            "recorded_at": store.now(), "author": actor, "review_state": "unreviewed",
            "scope": [],
        }, actor, idempotency_key=f"verify-needs:{object_id}:{digest}")
        return {"object_id": object_id, "verified": False, "replayed": False,
                "checks": checks, "reasons": reasons, "escalated": True,
                "trust_class": current_trust}

    return store.mutate(op)


def verify_pending(store, work_id: str, *, repo_path: str | Path | None = None,
                   packs_dir: str | Path | None = None,
                   opencode_db: str | None = None) -> dict:
    """Верифікувати всі пропозиції задачі; повернути звіт для спостерігача."""
    rows = store.read(
        "SELECT o.id FROM objects o "
        "JOIN relations r ON r.to_id = o.id AND r.relation_type = 'governed_by' "
        "WHERE r.from_id = ? ORDER BY o.id", (work_id,),
    )
    report = {"work_id": work_id, "total": len(rows), "verified": [], "escalated": [],
              "checks": 0}
    for row in rows:
        result = verify_object(store, row["id"], repo_path=repo_path, packs_dir=packs_dir,
                               opencode_db=opencode_db)
        if result["verified"]:
            report["verified"].append(row["id"])
        else:
            report["escalated"].append({"object_id": row["id"], "reasons": result["reasons"]})
        report["checks"] += len(result["checks"])
    report["verified_count"] = len(report["verified"])
    report["escalated_count"] = len(report["escalated"])
    return report


