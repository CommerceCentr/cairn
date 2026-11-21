"""SQLite WAL — операційна проєкція. Джерело істини подій — JSONL."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .ontology import DB_SCHEMA_VERSION

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    aggregate_id TEXT,
    actor_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    causation_id TEXT,
    correlation_id TEXT,
    idempotency_key TEXT,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    chain_hash TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_idem
    ON events(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_events_agg ON events(aggregate_id, seq);
CREATE TABLE IF NOT EXISTS objects (
    id TEXT PRIMARY KEY,
    object_type TEXT NOT NULL,
    namespace TEXT,
    stable_key TEXT,
    schema_version TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    title TEXT,
    summary TEXT,
    payload_json TEXT NOT NULL,
    trust_class TEXT NOT NULL,
    valid_from TEXT,
    valid_until TEXT,
    recorded_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_objects_type ON objects(object_type, lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_objects_key ON objects(stable_key);
CREATE TABLE IF NOT EXISTS object_revisions (
    id TEXT PRIMARY KEY,
    object_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    valid_from TEXT,
    valid_until TEXT,
    recorded_at TEXT NOT NULL,
    source_event_id TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_obj_rev
    ON object_revisions(object_id, revision);
CREATE INDEX IF NOT EXISTS idx_obj_rev_time
    ON object_revisions(object_id, valid_from);
CREATE TABLE IF NOT EXISTS assertions (
    id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object_ref TEXT,
    literal_json TEXT,
    claim_type TEXT NOT NULL,
    polarity TEXT NOT NULL,
    trust_class TEXT NOT NULL,
    confidence REAL,
    valid_from TEXT,
    valid_until TEXT,
    recorded_at TEXT NOT NULL,
    source_event_id TEXT,
    author_json TEXT NOT NULL,
    review_state TEXT,
    scope_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_assert_subject ON assertions(subject_id, predicate);
CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    execution_effect TEXT NOT NULL,
    valid_from TEXT,
    valid_until TEXT,
    provenance_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rel_from ON relations(from_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_rel_to ON relations(to_id, relation_type);
CREATE TABLE IF NOT EXISTS work_items (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL,
    parent_id TEXT,
    assignee_id TEXT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    acceptance_json TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    labels_json TEXT NOT NULL,
    capability_json TEXT NOT NULL,
    budget_limit_chars INTEGER,
    base_revision TEXT,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_status ON work_items(status, priority);
CREATE TABLE IF NOT EXISTS work_dependencies (
    id TEXT PRIMARY KEY,
    from_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    dependency_type TEXT NOT NULL,
    execution_effect TEXT NOT NULL,
    condition_json TEXT
);
CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    claimed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    state TEXT NOT NULL,
    base_revision TEXT
);
CREATE INDEX IF NOT EXISTS idx_claims_work ON claims(work_item_id, state);
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    name TEXT NOT NULL,
    capability_json TEXT NOT NULL,
    trust_class TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT,
    project_id TEXT,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    transcript_ref TEXT
);
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    evidence_type TEXT NOT NULL,
    source_ref TEXT,
    content_hash TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    trust_class TEXT NOT NULL,
    subject_id TEXT,
    metadata_json TEXT NOT NULL,
    verifier_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS code_symbols (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    stable_key TEXT NOT NULL,
    language TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    body_fingerprint TEXT NOT NULL,
    revision TEXT NOT NULL,
    path TEXT NOT NULL,
    span_json TEXT NOT NULL,
    precision TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_symbols_key ON code_symbols(repo_id, stable_key);
CREATE TABLE IF NOT EXISTS code_edges (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    from_key TEXT NOT NULL,
    to_key TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    revision TEXT NOT NULL,
    precision TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repositories (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    head TEXT,
    label TEXT
);
CREATE TABLE IF NOT EXISTS gates (
    id TEXT PRIMARY KEY,
    gate_type TEXT NOT NULL,
    subject_id TEXT,
    state TEXT NOT NULL,
    condition_json TEXT NOT NULL,
    evidence_id TEXT,
    deadline_at TEXT,
    resolved_by TEXT
);
CREATE TABLE IF NOT EXISTS workflow_definitions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    definition_json TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_runs (
    id TEXT PRIMARY KEY,
    definition_id TEXT NOT NULL,
    definition_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    input_json TEXT NOT NULL,
    checkpoint_json TEXT NOT NULL,
    retention TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_key TEXT NOT NULL,
    state TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resource_locks (
    id TEXT PRIMARY KEY,
    resource_key TEXT NOT NULL,
    holder_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage_records (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    session_id TEXT,
    provider TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_value REAL,
    cost_status TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS replicas (
    id TEXT PRIMARY KEY,
    replica_key TEXT NOT NULL,
    topology TEXT NOT NULL,
    authority_scope TEXT NOT NULL,
    trust_state TEXT NOT NULL,
    last_sync_at TEXT
);
CREATE TABLE IF NOT EXISTS sync_records (
    id TEXT PRIMARY KEY,
    peer_id TEXT,
    direction TEXT NOT NULL,
    cursor TEXT,
    status TEXT NOT NULL,
    conflict_count INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS config_values (
    key TEXT NOT NULL,
    scope TEXT NOT NULL,
    value_json TEXT NOT NULL,
    source TEXT NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY (key, scope, revision)
);
CREATE TABLE IF NOT EXISTS handoffs (
    id TEXT PRIMARY KEY,
    work_item_id TEXT,
    session_id TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS context_packages (
    id TEXT PRIMARY KEY,
    cache_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_fallback (
    object_id TEXT PRIMARY KEY,
    title TEXT,
    body TEXT
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=5, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def stored_version(con: sqlite3.Connection) -> int | None:
    """Версія схеми в базі, або None для щойно створеної. Нічого не змінює."""
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'"
    ).fetchone()
    if row is None:
        return None
    row = con.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
    return None if row is None else int(row["version"])


def set_version(con: sqlite3.Connection, version: int, applied_at: str) -> None:
    con.execute("DELETE FROM schema_meta")
    con.execute("INSERT INTO schema_meta(version, applied_at) VALUES (?, ?)",
                (version, applied_at))


def ensure(con: sqlite3.Connection, applied_at: str) -> int | None:
    """Створити схему; повернути версію, яка БУЛА в базі до цього.

    Повернене значення — вхід для міграції: `None` означає нову базу,
    менше за `DB_SCHEMA_VERSION` — базу, яку треба перебудувати з журналу.
    Раніше функція нічого не повертала, тому дізнатися про старішу схему
    було нізвідки, і міграції не існувало.
    """
    was = stored_version(con)
    con.executescript(SCHEMA)
    if was is None:
        set_version(con, DB_SCHEMA_VERSION, applied_at)
    elif was > DB_SCHEMA_VERSION:
        raise sqlite3.DatabaseError(
            f"схема бази {was} новіша за читач {DB_SCHEMA_VERSION}"
        )
    try:
        con.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS search_fts "
            "USING fts5(object_id UNINDEXED, title, body)"
        )
    except sqlite3.OperationalError:
        # FTS5 немає у цій збірці SQLite: пошук падає назад на
        # `search_fallback`. Тихо — бо це не дефект, а властивість збірки;
        # побачити це можна через `has_fts()`.
        pass
    return was


def has_fts(con: sqlite3.Connection) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_fts'"
    ).fetchone()
    return row is not None
