"""Work graph: задачі, залежності, eligibility, claim, lease, fencing."""

from __future__ import annotations

import json

from .canon import prefixed
from .errors import CairnError
from .ontology import CLAIMABLE, TERMINAL, TRANSITIONS
from .semantic import relate
from .store import require_human


def create_work(store, *, title: str, actor: dict, type: str = "task", description: str = "",
                priority: int = 1, acceptance: list | None = None, scope: dict | None = None,
                labels: list | None = None, capabilities: list | None = None,
                parent_id: str | None = None, budget_limit_chars: int | None = None,
                idempotency_key: str | None = None, status: str = "open") -> dict:
    now = store.now()
    wid = prefixed("wrk")
    payload = {
        "id": wid,
        "type": type,
        "status": status,
        "priority": priority,
        "parent_id": parent_id,
        "assignee_id": None,
        "title": title,
        "description": description,
        "acceptance": acceptance or [],
        "scope": scope or {},
        "labels": labels or [],
        "capabilities": capabilities or [],
        "budget_limit_chars": budget_limit_chars,
        "base_revision": None,
        "updated_at": now,
    }

    def op(con):
        ev = store.append(con, "work.upserted", wid, payload, actor, idempotency_key)
        body = ev["payload"]
        return {"work": body, "event_id": ev["event_id"], "replayed": ev["replayed"]}

    result = store.mutate(op)
    if parent_id and not result["replayed"]:
        relate(store, from_id=parent_id, to_id=result["work"]["id"], relation="parent_of", actor=actor)
    return result


def register_agent(store, *, agent_id: str, actor: dict, provider: str = "local",
                   name: str | None = None, capabilities: list | None = None,
                   trust_class: str = "agent_proposed", idempotency_key: str | None = None) -> dict:
    """Реєстрація профілю агента. Людина встановлює trust class, агент — ні."""
    if actor["kind"] != "human" and trust_class in {"human_authoritative", "verified_system"}:
        raise CairnError("authority_required", "агент не може призначити собі authoritativeness", exit_code=2)
    payload = {
        "id": agent_id,
        "provider": provider,
        "name": name or agent_id,
        "capabilities": capabilities or [],
        "trust_class": trust_class,
    }

    def op(con):
        ev = store.append(con, "agent.upserted", agent_id, payload, actor, idempotency_key)
        return {"agent": ev["payload"], "event_id": ev["event_id"], "replayed": ev["replayed"]}

    return store.mutate(op)


def get_work(store, work_id: str) -> dict:
    row = store.read_one("SELECT payload_json FROM work_items WHERE id = ?", (work_id,))
    if row is None:
        raise CairnError("not_found", f"задача {work_id} не знайдена", exit_code=1)
    return json.loads(row["payload_json"])


def list_work(store, status: str | None = None) -> list[dict]:
    rows = store.read("SELECT payload_json, status FROM work_items ORDER BY priority, updated_at")
    out = []
    for row in rows:
        if status and row["status"] != status:
            continue
        out.append(json.loads(row["payload_json"]))
    return out


def transition(store, work_id: str, to_status: str, actor: dict, *, fencing_token: int | None = None) -> dict:
    if to_status == "completed":
        raise CairnError("authority_required", "completed виставляє лише accept із доказами")
    work = get_work(store, work_id)
    allowed = TRANSITIONS.get(work["status"], set())
    if to_status not in allowed:
        raise CairnError(
            "bad_transition",
            f"перехід {work['status']} → {to_status} заборонений",
            details={"from": work["status"], "to": to_status},
        )
    if work["status"] in {"claimed", "in_progress"} and to_status not in {"cancelled"}:
        _require_fence(store, work_id, fencing_token, actor)
    work["status"] = to_status
    work["updated_at"] = store.now()
    return _save_work(store, work, actor)


def eligibility(store, work_id: str, agent_id: str | None = None, *, now: str | None = None) -> dict:
    now = now or store.now()
    work = get_work(store, work_id)
    reasons = []
    verdict = "eligible"
    agent = store.read_one("SELECT * FROM agents WHERE id = ?", (agent_id,)) if agent_id else None

    def block(code: str, next_verdict: str, detail: str, refs: list | None = None):
        nonlocal verdict
        reasons.append({"code": code, "detail": detail, "refs": refs or []})
        if verdict == "eligible":
            verdict = next_verdict

    if work["status"] in TERMINAL:
        block("terminal", "blocked", f"стан {work['status']} термінальний")
    elif work["status"] == "deferred":
        block("deferred", "deferred", "задачу відкладено")
    elif work["status"] not in CLAIMABLE and work["status"] not in {"claimed", "in_progress"}:
        block("not_claimable", "blocked", f"стан {work['status']} не дозволяє старт")

    for dep in store.read(
        "SELECT * FROM work_dependencies WHERE to_id = ?", (work_id,)
    ):
        if dep["execution_effect"] == "none":
            continue
        pre = store.read_one("SELECT payload_json, status FROM work_items WHERE id = ?", (dep["from_id"],))
        if pre is None:
            block("missing_blocker", "blocked", "блокувальник відсутній", [dep["from_id"]])
            continue
        if dep["execution_effect"] == "conditional":
            condition = json.loads(dep["condition_json"] or "{}")
            if not condition.get("active"):
                continue
        if pre["status"] not in {"completed"}:
            block("hard_dependency", "blocked", f"{dep['dependency_type']} ще не завершено", [dep["from_id"]])

    gates = store.read(
        "SELECT * FROM gates WHERE subject_id = ? AND state = 'pending'", (work_id,)
    )
    for gate in gates:
        kind = "requires_human" if gate["gate_type"] == "human.approval" else "blocked"
        block("open_gate", kind, f"відкритий gate {gate['gate_type']}", [gate["id"]])

    claim = _active_claim(store, work_id, now)
    if claim and claim["agent_id"] != agent_id:
        block("foreign_claim", "conflicted", "задачу тримає інший виконавець", [claim["id"]])

    needed = set(work.get("capabilities") or [])
    if agent is None and needed:
        block("no_agent", "not_capable", "немає профілю агента для вимог " + ", ".join(sorted(needed)))
    elif agent is not None and needed:
        have = set(json.loads(agent["capability_json"]) or [])
        missing = sorted(needed - have)
        if missing:
            block("capability", "not_capable", "не вистачає можливостей: " + ", ".join(missing), missing)

    limit = work.get("budget_limit_chars")
    if limit is not None:
        spent = _spent_chars(store, work_id)
        if spent > int(limit):
            block("budget", "budget_denied", f"витрачено {spent} символів із ліміту {limit}")

    expected = work.get("base_revision")
    if expected and work.get("scope", {}).get("repo_id"):
        repo = store.read_one("SELECT head FROM repositories WHERE id = ?", (work["scope"]["repo_id"],))
        if repo and repo["head"] and repo["head"] != expected and work["status"] in CLAIMABLE:
            block("stale_base", "conflicted", "ревізія репозиторію змінилася від очікуваної")

    contradictions = store.read(
        """SELECT id FROM assertions
           WHERE subject_id = ? AND polarity = 'negated' AND trust_class IN ('human_authoritative', 'verified_system')
             AND valid_until IS NULL""",
        (work_id,),
    )
    if contradictions:
        block("semantic_conflict", "conflicted", "є авторитетне заперечення", [row["id"] for row in contradictions])

    # Політики — окремий вхід у frontier. Твердження агента тут не рахується:
    # тільки human_authoritative/verified_system і чинна (valid_until IS NULL).
    policies = store.read(
        """SELECT id, predicate, literal_json FROM assertions
           WHERE subject_id IN (?, '*') AND predicate LIKE 'policy.%'
             AND trust_class IN ('human_authoritative', 'verified_system')
             AND polarity = 'asserted' AND valid_until IS NULL""",
        (work_id,),
    )
    for row in policies:
        literal = json.loads(row["literal_json"] or "null")
        if literal is not True:
            continue
        predicate = row["predicate"]
        if predicate in {"policy.ready.denied", "policy.work.claim.denied"}:
            block("policy_denied", "blocked", f"політика {predicate} забороняє старт", [row["id"]])
        elif (predicate.startswith("policy.capability.") and predicate.endswith(".denied")
              and predicate[len("policy.capability."):-len(".denied")] in needed):
            block("policy_denied", "not_capable", f"політика забороняє {predicate}", [row["id"]])

    return {
        "work_id": work_id,
        "agent_id": agent_id,
        "verdict": verdict,
        "reasons": reasons,
        "status": work["status"],
    }


def ready(store, agent_id: str | None = None) -> list[dict]:
    items = []
    for work in list_work(store):
        decision = eligibility(store, work["id"], agent_id)
        if decision["verdict"] == "eligible":
            items.append({"work": work, "eligibility": decision})
    return items


def claim(store, work_id: str, agent_id: str, actor: dict, *, ttl_sec: int | None = None) -> dict:
    decision = eligibility(store, work_id, agent_id)
    if decision["verdict"] != "eligible":
        held = _active_claim(store, work_id, store.now())
        if held and held["agent_id"] != agent_id:
            raise CairnError(
                "conflicted", "задачу тримає інший виконавець", exit_code=2,
                details={"claim_id": held["id"], "eligibility": decision},
            )
        if not (held and held["agent_id"] == agent_id and decision["verdict"] == "conflicted"):
            raise CairnError("not_eligible", "задачу не можна взяти", exit_code=2, details=decision)
    ttl = ttl_sec if ttl_sec is not None else int(store.config_value("lease_ttl_sec")["value"])
    now = store.now()

    def op(con):
        current = _active_claim_con(con, work_id, now)
        if current and current["agent_id"] == agent_id and current["expires_at"] > now:
            return {"replayed": True, "claim": current, "work": get_work(store, work_id)}
        if current and current["expires_at"] > now:
            raise CairnError("conflicted", "задачу вже взято", exit_code=2, details={"claim_id": current["id"]})
        for stale in con.execute(
            "SELECT * FROM claims WHERE work_item_id = ? AND state = 'active' AND expires_at <= ?",
            (work_id, now),
        ):
            body = dict(stale)
            body["state"] = "expired"
            store.append(con, "claim.upserted", work_id, {
                "id": body["id"], "work_item_id": work_id, "agent_id": body["agent_id"],
                "fencing_token": body["fencing_token"], "claimed_at": body["claimed_at"],
                "expires_at": body["expires_at"], "state": "expired",
                "base_revision": body["base_revision"],
            }, actor)
        token_row = con.execute("SELECT MAX(fencing_token) AS token FROM claims WHERE work_item_id = ?", (work_id,)).fetchone()
        token = int(token_row["token"] or 0) + 1
        work = get_work(store, work_id)
        repo_id = (work.get("scope") or {}).get("repo_id")
        base = None
        if repo_id:
            repo = con.execute("SELECT head FROM repositories WHERE id = ?", (repo_id,)).fetchone()
            base = repo["head"] if repo else None
        from .canon import add_seconds
        claim_body = {
            "id": prefixed("clm"),
            "work_item_id": work_id,
            "agent_id": agent_id,
            "fencing_token": token,
            "claimed_at": now,
            "expires_at": add_seconds(now, ttl),
            "state": "active",
            "base_revision": base,
            "replica_local": True,
        }
        ev = store.append(con, "claim.upserted", work_id, claim_body, actor)
        work["status"] = "claimed"
        work["assignee_id"] = agent_id
        work["base_revision"] = base
        work["updated_at"] = now
        store.append(con, "work.upserted", work_id, work, actor, causation_id=ev["event_id"])
        return {"replayed": False, "claim": ev["payload"], "work": work}

    return store.mutate(op)


def heartbeat(store, work_id: str, fencing_token: int, actor: dict, *, ttl_sec: int | None = None) -> dict:
    now = store.now()
    ttl = ttl_sec if ttl_sec is not None else int(store.config_value("lease_ttl_sec")["value"])

    def op(con):
        row = _claim_by_token(con, work_id, fencing_token)
        if row["state"] != "active" or row["expires_at"] <= now:
            raise CairnError("stale_fence", "lease прострочено або відкликано", exit_code=4)
        from .canon import add_seconds
        body = dict(row)
        body["expires_at"] = add_seconds(now, ttl)
        ev = store.append(con, "claim.upserted", work_id, _claim_payload(body), actor)
        return {"claim": ev["payload"], "replayed": False}

    return store.mutate(op)


def release(store, work_id: str, fencing_token: int, actor: dict) -> dict:
    def op(con):
        row = _claim_by_token(con, work_id, fencing_token)
        if row["state"] != "active":
            raise CairnError("stale_fence", "claim уже не активний", exit_code=4)
        body = dict(row)
        body["state"] = "released"
        ev = store.append(con, "claim.upserted", work_id, _claim_payload(body), actor)
        work = get_work(store, work_id)
        work["status"] = "ready"
        work["assignee_id"] = None
        work["updated_at"] = store.now()
        store.append(con, "work.upserted", work_id, work, actor, causation_id=ev["event_id"])
        return {"claim": ev["payload"], "work": work}

    return store.mutate(op)


def verify(store, work_id: str) -> dict:
    """Acceptance Evaluator: лише перевіряє, не змінює стан задачі."""
    task = get_work(store, work_id)
    evidence_rows = store.read("SELECT * FROM evidence WHERE subject_id = ?", (work_id,))
    missing: list[dict] = []
    failed: list[dict] = []
    for rule in task.get("acceptance") or []:
        if rule.get("kind") == "evidence":
            if not any(row["evidence_type"] == rule.get("evidence_type") for row in evidence_rows):
                missing.append(rule)
        elif rule.get("kind") == "source_absent":
            commit = next((row for row in evidence_rows if row["evidence_type"] == "commit"), None)
            if commit is None:
                missing.append(rule)
            else:
                meta = json.loads(commit["metadata_json"])
                text = _show(store, meta.get("commit"), rule.get("path"), task)
                if rule.get("pattern") and rule["pattern"] in text:
                    failed.append({"rule": rule, "reason": "forbidden_pattern_in_commit"})
        else:
            failed.append({"rule": rule, "reason": "unknown_acceptance_kind"})
    commit_row = next((row for row in evidence_rows if row["evidence_type"] == "commit"), None)
    committed_meta = json.loads(commit_row["metadata_json"]) if commit_row else {}
    committed = committed_meta.get("commit")
    test_rows = [row for row in evidence_rows if row["evidence_type"] == "test"]
    bound_tests = []
    for row in test_rows:
        meta = json.loads(row["metadata_json"])
        binding = meta.get("revision_binding") or {}
        if (binding.get("bound") and binding.get("commit") == committed
                and binding.get("tree") == committed_meta.get("tree") and meta.get("passed")):
            bound_tests.append(row)
    if commit_row is None:
        missing.append({"kind": "evidence", "evidence_type": "commit"})
    if not test_rows:
        missing.append({"kind": "evidence", "evidence_type": "test"})
    elif not bound_tests:
        failed.append({"rule": {"kind": "evidence", "evidence_type": "test"},
                       "reason": "test_not_bound_to_committed_revision"})
    return {
        "work_id": work_id,
        "ok": not missing and not failed,
        "missing": missing,
        "failed": failed,
        "commit": committed,
        "tree": committed_meta.get("tree"),
        "bound_tests": [row["id"] for row in bound_tests],
        "acceptance_rules": task.get("acceptance") or [],
    }


def accept(store, work_id: str, fencing_token: int, actor: dict) -> dict:
    require_human(actor)
    now = store.now()
    _require_fence(store, work_id, fencing_token, actor, now=now)
    task = get_work(store, work_id)
    if task["status"] in TERMINAL:
        raise CairnError("not_active", "задача вже закрита", exit_code=2)
    report = verify(store, work_id)
    if not report["ok"]:
        raise CairnError(
            "acceptance_failed",
            "Acceptance Evaluator не пропустив задачу",
            details={"missing": report["missing"], "failed": report["failed"]},
        )
    _guard_base(store, task, committed=report["commit"])
    task["status"] = "completed"
    task["updated_at"] = now
    saved = _save_work(store, task, actor)
    return {"work": saved["work"], "accepted_by": actor, "verification": report}


def comment(store, work_id: str, text: str, actor: dict) -> dict:
    get_work(store, work_id)
    from .semantic import put_object, relate as link
    note = put_object(
        store, object_type="note", title="comment", summary=text, actor=actor,
        lifecycle="accepted" if actor["kind"] == "human" else "proposed",
        trust="source_observed" if actor["kind"] == "human" else "agent_proposed",
    )
    link(store, from_id=note["object"]["id"], to_id=work_id, relation="replies_to", actor=actor)
    return note


def _save_work(store, work: dict, actor: dict) -> dict:
    def op(con):
        ev = store.append(con, "work.upserted", work["id"], work, actor)
        return {"work": ev["payload"], "event_id": ev["event_id"], "replayed": ev["replayed"]}
    return store.mutate(op)


def _spent_chars(store, work_id: str) -> int:
    rows = store.read(
        "SELECT input_tokens, cost_status FROM usage_records WHERE run_id = ?", (work_id,)
    )
    return sum(int(row["input_tokens"]) for row in rows if row["cost_status"] in {"estimated", "reported"})


def _active_claim(store, work_id: str, now: str) -> dict | None:
    rows = store.read(
        """SELECT * FROM claims WHERE work_item_id = ? AND state = 'active' AND expires_at > ?
           ORDER BY fencing_token DESC""",
        (work_id, now),
    )
    return rows[0] if rows else None


def _active_claim_con(con, work_id: str, now: str) -> dict | None:
    row = con.execute(
        """SELECT * FROM claims WHERE work_item_id = ? AND state = 'active' AND expires_at > ?
           ORDER BY fencing_token DESC LIMIT 1""",
        (work_id, now),
    ).fetchone()
    return dict(row) if row else None


def _claim_by_token(con, work_id: str, token: int) -> dict:
    row = con.execute(
        "SELECT * FROM claims WHERE work_item_id = ? AND fencing_token = ?",
        (work_id, token),
    ).fetchone()
    if row is None:
        raise CairnError("stale_fence", "fencing token не знайдено", exit_code=4)
    return dict(row)


def _claim_payload(row: dict) -> dict:
    return {
        "id": row["id"],
        "work_item_id": row["work_item_id"],
        "agent_id": row["agent_id"],
        "fencing_token": int(row["fencing_token"]),
        "claimed_at": row["claimed_at"],
        "expires_at": row["expires_at"],
        "state": row["state"],
        "base_revision": row.get("base_revision"),
        "replica_local": True,
    }


def _require_fence(store, work_id: str, token: int | None, actor: dict, now: str | None = None) -> dict:
    if token is None:
        raise CairnError("stale_fence", "потрібен fencing token", exit_code=4)
    now = now or store.now()
    row = store.read_one(
        "SELECT * FROM claims WHERE work_item_id = ? AND fencing_token = ?",
        (work_id, token),
    )
    if row is None or row["state"] != "active" or row["expires_at"] <= now:
        raise CairnError("stale_fence", "запис відхилено: lease недійсний", exit_code=4)
    return row


def _guard_base(store, work: dict, *, committed: str | None) -> None:
    """Приймаємо тільки коміт, який зараз є head репозиторію.

    Не покладаємося на base_revision із claim: виконавець міг зрушити
    ревізію власним комітом. Натомість звіряємо commit evidence з поточним
    head. Якщо хтось інший запушив новіший коміт — приймання блокується.
    """
    repo_id = (work.get("scope") or {}).get("repo_id")
    if not committed or not repo_id:
        return
    repo = store.read_one("SELECT path, head FROM repositories WHERE id = ?", (repo_id,))
    if repo and repo["head"] and repo["head"] != committed:
        raise CairnError(
            "stale_base",
            "коміт із доказом більше не є head; приймання заборонене без повторної перевірки",
            exit_code=4,
            details={"expected": committed, "head": repo["head"]},
        )


def _show(store, commit: str | None, path: str | None, work: dict) -> str:
    if not commit or not path:
        return ""
    repo_id = (work.get("scope") or {}).get("repo_id")
    repo = store.read_one("SELECT path FROM repositories WHERE id = ?", (repo_id,)) if repo_id else None
    if repo is None:
        return ""
    from .code import git
    try:
        return git.git(repo["path"], "show", f"{commit}:{path}")
    except CairnError:
        return ""
