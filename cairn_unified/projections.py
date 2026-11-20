"""Застосування події до проєкції. Ідемпотентне: повтор того самого event_id нічого не змінює."""

from __future__ import annotations

import json
import sqlite3

from .canon import canonical
from .db import has_fts


def _j(value) -> str:
    return canonical(value if value is not None else {})


def _loads(text: str | None, fallback):
    if not text:
        return fallback
    return json.loads(text)


def index_text(con: sqlite3.Connection, object_id: str, title: str, body: str) -> None:
    con.execute("INSERT OR REPLACE INTO search_fallback(object_id, title, body) VALUES (?, ?, ?)",
                (object_id, title or "", body or ""))
    if has_fts(con):
        con.execute("DELETE FROM search_fts WHERE object_id = ?", (object_id,))
        con.execute(
            "INSERT INTO search_fts(object_id, title, body) VALUES (?, ?, ?)",
            (object_id, title or "", body or ""),
        )


def apply(con: sqlite3.Connection, event: dict) -> None:
    kind = event["event_type"]
    payload = event["payload"]
    handler = HANDLERS.get(kind)
    if handler is None:
        return
    handler(con, event, payload)


def _object(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO objects(
            id, object_type, namespace, stable_key, schema_version, lifecycle_state,
            title, summary, payload_json, trust_class, valid_from, valid_until,
            recorded_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            p["id"], p["object_type"], p.get("namespace"), p.get("stable_key"),
            p.get("schema_version", "2.0"), p["lifecycle_state"], p.get("title"),
            p.get("summary"), _j(p), p.get("trust_class", "source_observed"),
            p.get("valid_from"), p.get("valid_until"),
            p.get("recorded_at") or event["recorded_at"], p.get("updated_at") or event["recorded_at"],
        ),
    )
    index_text(con, p["id"], p.get("title") or "", " ".join(
        x for x in (p.get("summary"), p.get("title"), json.dumps(p.get("semantic"), ensure_ascii=False)) if x
    ))


def _assertion(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO assertions(
            id, subject_id, predicate, object_ref, literal_json, claim_type, polarity,
            trust_class, confidence, valid_from, valid_until, recorded_at, source_event_id,
            author_json, review_state, scope_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            p["id"], p["subject_id"], p["predicate"], p.get("object_ref"),
            _j(p.get("literal")), p["claim_type"], p.get("polarity", "asserted"),
            p["trust_class"], p.get("confidence"), p.get("valid_from"), p.get("valid_until"),
            p.get("recorded_at") or event["recorded_at"], event["event_id"],
            _j(p.get("author")), p.get("review_state"), _j(p.get("scope")),
        ),
    )


def _relation(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO relations(
            id, from_id, to_id, relation_type, execution_effect, valid_from, valid_until,
            provenance_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            p["id"], p["from_id"], p["to_id"], p["relation_type"], p["execution_effect"],
            p.get("valid_from"), p.get("valid_until"), _j(p.get("provenance")),
            p.get("created_at") or event["recorded_at"],
        ),
    )
    if p["relation_type"] in {"blocks", "depends_on", "waits_for", "conditional_blocks", "parent_of"}:
        con.execute(
            """INSERT OR REPLACE INTO work_dependencies(
                id, from_id, to_id, dependency_type, execution_effect, condition_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                p["id"], p["from_id"], p["to_id"], p["relation_type"],
                p["execution_effect"], _j(p.get("condition")),
            ),
        )


def _work(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO work_items(
            id, type, status, priority, parent_id, assignee_id, title, description,
            acceptance_json, scope_json, labels_json, capability_json, budget_limit_chars,
            base_revision, payload_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            p["id"], p["type"], p["status"], int(p.get("priority") or 0), p.get("parent_id"),
            p.get("assignee_id"), p["title"], p.get("description") or "",
            _j(p.get("acceptance") or []), _j(p.get("scope") or {}), _j(p.get("labels") or []),
            _j(p.get("capabilities") or []), p.get("budget_limit_chars"), p.get("base_revision"),
            _j(p), p.get("updated_at") or event["recorded_at"],
        ),
    )
    index_text(con, p["id"], p["title"], p.get("description") or "")


def _claim(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO claims(
            id, work_item_id, agent_id, fencing_token, claimed_at, expires_at, state, base_revision)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            p["id"], p["work_item_id"], p["agent_id"], int(p["fencing_token"]),
            p["claimed_at"], p["expires_at"], p["state"], p.get("base_revision"),
        ),
    )


def _agent(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO agents(id, provider, name, capability_json, trust_class)
           VALUES (?, ?, ?, ?, ?)""",
        (p["id"], p.get("provider") or "local", p.get("name") or p["id"],
         _j(p.get("capabilities") or []), p.get("trust_class") or "agent_proposed"),
    )


def _session(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO sessions(
            id, agent_id, project_id, state, started_at, ended_at, transcript_ref)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (p["id"], p.get("agent_id"), p.get("project_id"), p["state"],
         p["started_at"], p.get("ended_at"), p.get("transcript_ref")),
    )


def _evidence(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO evidence(
            id, evidence_type, source_ref, content_hash, collected_at, trust_class,
            subject_id, metadata_json, verifier_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            p["id"], p["evidence_type"], p.get("source_ref"), p["content_hash"],
            p["collected_at"], p.get("trust_class") or "verified_system", p.get("subject_id"),
            _j(p.get("metadata") or {}), _j(p.get("verifier") or {}),
        ),
    )


def _code(con, event, p):
    repo = p["repo_id"]
    con.execute("DELETE FROM code_symbols WHERE repo_id = ?", (repo,))
    con.execute("DELETE FROM code_edges WHERE repo_id = ?", (repo,))
    for sym in p.get("symbols") or []:
        con.execute(
            """INSERT OR REPLACE INTO code_symbols(
                id, repo_id, stable_key, language, qualified_name, fingerprint,
                body_fingerprint, revision, path, span_json, precision)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sym["id"], repo, sym["stable_key"], sym["language"], sym["qualified_name"],
                sym["fingerprint"], sym.get("body_fingerprint") or sym["fingerprint"],
                p["revision"], sym["path"], _j(sym.get("span") or {}), sym.get("precision") or "structural",
            ),
        )
        obj = {
            "id": sym["id"],
            "object_type": "symbol",
            "namespace": f"repo:{repo}",
            "stable_key": sym["stable_key"],
            "schema_version": "2.0",
            "lifecycle_state": "current",
            "title": sym["qualified_name"],
            "summary": sym["path"],
            "trust_class": "source_observed",
            "valid_from": event["recorded_at"],
            "valid_until": None,
            "recorded_at": event["recorded_at"],
            "updated_at": event["recorded_at"],
            "semantic": {"kind": "code_symbol", "scope": [sym["path"]], "precision": sym.get("precision")},
            "provenance": {"revision": p["revision"], "source": "code-index"},
        }
        _object(con, event, obj)
    for edge in p.get("edges") or []:
        con.execute(
            """INSERT INTO code_edges(
                id, repo_id, from_key, to_key, relation_type, confidence, revision, precision)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                edge["id"], repo, edge["from_key"], edge["to_key"], edge["relation_type"],
                float(edge.get("confidence") or 1), p["revision"], edge.get("precision") or "heuristic",
            ),
        )


def _repo(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO repositories(id, path, fingerprint, head, label)
           VALUES (?, ?, ?, ?, ?)""",
        (p["id"], p["path"], p["fingerprint"], p.get("head"), p.get("label")),
    )


def _gate(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO gates(
            id, gate_type, subject_id, state, condition_json, evidence_id, deadline_at, resolved_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            p["id"], p["gate_type"], p.get("subject_id"), p["state"],
            _j(p.get("condition") or {}), p.get("evidence_id"), p.get("deadline_at"), p.get("resolved_by"),
        ),
    )


def _wdef(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO workflow_definitions(
            id, name, version, definition_json, content_hash)
           VALUES (?, ?, ?, ?, ?)""",
        (p["id"], p["name"], int(p["version"]), _j(p["definition"]), p["content_hash"]),
    )


def _wrun(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO workflow_runs(
            id, definition_id, definition_hash, state, input_json, checkpoint_json, retention, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            p["id"], p["definition_id"], p["definition_hash"], p["state"],
            _j(p.get("input") or {}), _j(p.get("checkpoint") or {}),
            p.get("retention") or "durable", p.get("updated_at") or event["recorded_at"],
        ),
    )


def _wstep(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO workflow_steps(
            id, run_id, step_key, state, attempt, idempotency_key, result_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            p["id"], p["run_id"], p["step_key"], p["state"], int(p.get("attempt") or 1),
            p["idempotency_key"], _j(p.get("result") or {}),
        ),
    )


def _lock(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO resource_locks(
            id, resource_key, holder_id, fencing_token, expires_at, mode, state)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            p["id"], p["resource_key"], p["holder_id"], int(p["fencing_token"]),
            p["expires_at"], p.get("mode") or "exclusive", p["state"],
        ),
    )


def _usage(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO usage_records(
            id, run_id, session_id, provider, input_tokens, output_tokens, cost_value, cost_status, recorded_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            p["id"], p.get("run_id"), p.get("session_id"), p.get("provider") or "local",
            int(p.get("input_tokens") or 0), int(p.get("output_tokens") or 0),
            p.get("cost_value"), p["cost_status"], p.get("recorded_at") or event["recorded_at"],
        ),
    )


def _replica(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO replicas(
            id, replica_key, topology, authority_scope, trust_state, last_sync_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (p["id"], p["replica_key"], p.get("topology") or "local",
         p.get("authority_scope") or "local", p.get("trust_state") or "local_authority",
         p.get("last_sync_at")),
    )


def _sync(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO sync_records(
            id, peer_id, direction, cursor, status, conflict_count, started_at, finished_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            p["id"], p.get("peer_id"), p["direction"], p.get("cursor"), p["status"],
            int(p.get("conflict_count") or 0), p["started_at"], p.get("finished_at"),
        ),
    )


def _config(con, event, p):
    con.execute(
        """INSERT OR REPLACE INTO config_values(key, scope, value_json, source, revision)
           VALUES (?, ?, ?, ?, ?)""",
        (p["key"], p["scope"], _j(p.get("value")), p.get("source") or "command", int(p["revision"])),
    )


def _handoff(con, event, p):
    con.execute(
        "INSERT OR REPLACE INTO handoffs(id, work_item_id, session_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (p["id"], p.get("work_item_id"), p.get("session_id"), _j(p), p.get("created_at") or event["recorded_at"]),
    )


def _context(con, event, p):
    con.execute(
        "INSERT OR REPLACE INTO context_packages(id, cache_key, payload_json, created_at) VALUES (?, ?, ?, ?)",
        (p["id"], p["cache_key"], _j(p["package"]), p.get("created_at") or event["recorded_at"]),
    )


HANDLERS = {
    "object.upserted": _object,
    "assertion.recorded": _assertion,
    "relation.recorded": _relation,
    "work.upserted": _work,
    "claim.upserted": _claim,
    "agent.upserted": _agent,
    "session.upserted": _session,
    "evidence.recorded": _evidence,
    "code.indexed": _code,
    "repository.upserted": _repo,
    "gate.upserted": _gate,
    "workflow_definition.upserted": _wdef,
    "workflow_run.upserted": _wrun,
    "workflow_step.upserted": _wstep,
    "lock.upserted": _lock,
    "usage.recorded": _usage,
    "replica.created": _replica,
    "sync.recorded": _sync,
    "config.recorded": _config,
    "handoff.recorded": _handoff,
    "context.recorded": _context,
}
