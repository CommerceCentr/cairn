"""Сховище Unified: журнал, проєкція, конфігурація, doctor, rebuild."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

from . import ledger, projections
from .canon import canonical, iso, payload_hash, prefixed, utcnow
from .db import connect, ensure
from .errors import CairnError
from .ledger import chain_hash
from .ontology import DB_SCHEMA_VERSION, DEFAULTS, SCHEMA_VERSION
from .redact import redact_value, secret_hits


INIT_FOLDERS = (
    "config", "events", "evidence", "exports", "objects",
    "cache", "runtime", "secrets", "runtime/locks", "runtime/sessions",
    "runtime/worktrees", "runtime/checkpoints",
)


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.cairn = self.root / ".cairn"
        self.db_path = self.cairn / "runtime" / "cairn.sqlite"
        self.correlation_id: str | None = None

    def clock(self):
        fn = getattr(self, "_clock", None)
        return fn() if fn else utcnow()

    def now(self) -> str:
        return iso(self.clock())

    @classmethod
    def open(cls, root: Path) -> "Store":
        store = cls(root)
        if not (store.cairn / "manifest.json").exists():
            raise CairnError("not_initialized", "немає .cairn/manifest.json — спочатку project init", exit_code=2)
        store.reconcile()
        return store

    def manifest(self) -> dict:
        path = self.cairn / "manifest.json"
        if not path.exists():
            raise CairnError("not_initialized", "manifest відсутній", exit_code=2)
        return json.loads(path.read_text(encoding="utf-8"))

    def init(self) -> dict:
        path = self.cairn / "manifest.json"
        if path.exists():
            self.reconcile()
            return {"replayed": True, **self.manifest()}
        for folder in INIT_FOLDERS:
            (self.cairn / folder).mkdir(parents=True, exist_ok=True)
        gitignore = self.cairn / ".gitignore"
        gitignore.write_text("cache/\nruntime/\nsecrets/\n", encoding="utf-8", newline="\n")
        project_id = prefixed("prj")
        replica_id = prefixed("rep")
        created = self.now()
        body = {
            "schema": "cairn-unified/2.0",
            "schema_version": SCHEMA_VERSION,
            "db_schema_version": DB_SCHEMA_VERSION,
            "project_id": project_id,
            "replica_id": replica_id,
            "created_at": created,
        }
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        (self.cairn / "config" / "project.json").write_text("{}\n", encoding="utf-8", newline="\n")
        self.mutate(lambda con: self.append(con, "replica.created", replica_id, {
            "id": replica_id,
            "replica_key": replica_id,
            "topology": "local",
            "authority_scope": "local",
            "trust_state": "local_authority",
        }, {"kind": "system", "id": "init"}))
        return {"replayed": False, **body}

    def connect(self) -> sqlite3.Connection:
        con = connect(self.db_path)
        ensure(con, self.now())
        return con

    def mutate(self, fn):
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                result = fn(con)
                con.execute("COMMIT")
                return result
            except Exception:
                con.execute("ROLLBACK")
                raise
        finally:
            con.close()

    def read(self, sql: str, params: tuple = ()):
        con = self.connect()
        try:
            return [dict(row) for row in con.execute(sql, params).fetchall()]
        finally:
            con.close()

    def read_one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.read(sql, params)
        return rows[0] if rows else None

    def append(self, con, event_type: str, aggregate_id: str | None, payload: dict,
               actor: dict, idempotency_key: str | None = None, causation_id: str | None = None) -> dict:
        payload = redact_value(payload)
        if idempotency_key:
            prev = con.execute(
                "SELECT * FROM events WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if prev:
                return {"replayed": True, **_event_from_row(prev)}
        last = con.execute("SELECT seq, chain_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        seq = 1 if last is None else int(last["seq"]) + 1
        prev_hash = "0" * 64 if last is None else last["chain_hash"]
        stamp = self.now()
        event_id = prefixed("evt")
        phash = payload_hash(payload)
        chash = chain_hash(prev_hash, seq, event_id, event_type, phash)
        event = {
            "event_id": event_id,
            "seq": seq,
            "event_type": event_type,
            "schema_version": SCHEMA_VERSION,
            "aggregate_id": aggregate_id,
            "actor": actor,
            "occurred_at": stamp,
            "recorded_at": stamp,
            "causation_id": causation_id,
            "correlation_id": self.correlation_id,
            "idempotency_key": idempotency_key,
            "payload": payload,
            "payload_hash": phash,
            "prev_hash": prev_hash,
            "chain_hash": chash,
        }
        ledger.append_line(ledger.ledger_path(self.root), event)
        con.execute(
            """INSERT INTO events(
                event_id, seq, event_type, aggregate_id, actor_json, occurred_at, recorded_at,
                causation_id, correlation_id, idempotency_key, payload_json, payload_hash,
                prev_hash, chain_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id, seq, event_type, aggregate_id, canonical(actor), stamp, stamp,
                causation_id, self.correlation_id, idempotency_key, canonical(payload),
                phash, prev_hash, chash,
            ),
        )
        projections.apply(con, event)
        self.materialize(event)
        event["replayed"] = False
        return event

    def materialize(self, event: dict) -> None:
        kind = event["event_type"]
        payload = event["payload"]
        folder = {
            "object.upserted": payload.get("object_type", "object"),
            "work.upserted": "work",
            "evidence.recorded": "evidence",
            "gate.upserted": "gate",
            "workflow_definition.upserted": "workflow",
        }.get(kind)
        if not folder or "id" not in payload:
            return
        dest = self.cairn / "objects" / folder
        dest.mkdir(parents=True, exist_ok=True)
        (dest / f"{payload['id']}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )

    def reconcile(self) -> dict:
        events = ledger.read_events(ledger.ledger_path(self.root))
        con = self.connect()
        applied = 0
        try:
            con.execute("BEGIN IMMEDIATE")
            have = {row[0] for row in con.execute("SELECT event_id FROM events")}
            for ev in events:
                if ev["event_id"] in have:
                    continue
                con.execute(
                    """INSERT OR IGNORE INTO events(
                        event_id, seq, event_type, aggregate_id, actor_json, occurred_at, recorded_at,
                        causation_id, correlation_id, idempotency_key, payload_json, payload_hash,
                        prev_hash, chain_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ev["event_id"], int(ev["seq"]), ev["event_type"], ev.get("aggregate_id"),
                        canonical(ev.get("actor") or {}), ev["occurred_at"], ev["recorded_at"],
                        ev.get("causation_id"), ev.get("correlation_id"), ev.get("idempotency_key"),
                        canonical(ev.get("payload") or {}), ev["payload_hash"], ev["prev_hash"], ev["chain_hash"],
                    ),
                )
                projections.apply(con, ev)
                self.materialize(ev)
                applied += 1
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()
        return {"applied": applied, "ledger": len(events)}

    def rebuild(self) -> dict:
        """Видаляє проєкцію і відтворює її з журналу."""
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.db_path) + suffix)
            if path.exists():
                path.unlink()
        objects = self.cairn / "objects"
        if objects.exists():
            shutil.rmtree(objects)
        objects.mkdir(parents=True, exist_ok=True)
        return self.reconcile()

    def config_value(self, key: str, cli_value=None) -> dict:
        if key not in DEFAULTS:
            raise CairnError("unknown_config", f"невідомий ключ {key}", exit_code=2)
        value = DEFAULTS[key]
        source = "builtin"
        scope = "default"
        found: list[dict] = [{
            "scope": "default", "source": "builtin", "value": value,
        }]
        files = (
            ("project.shared", self.cairn / "config" / "project.json"),
            ("project.local", self.cairn / "config" / "project.local.json"),
            ("machine.local", self.cairn / "config" / "machine.local.json"),
            ("user.local", self.cairn / "config" / "user.local.json"),
        )
        for scope_name, file in files:
            if not file.exists():
                continue
            try:
                data = json.loads(file.read_text(encoding="utf-8-sig") or "{}")
            except json.JSONDecodeError as exc:
                raise CairnError("config_invalid", f"{file.name}: {exc}") from exc
            if key in data:
                found.append({"scope": scope_name, "source": str(file.relative_to(self.root)),
                              "value": data[key]})
                value = data[key]
                source = str(file.relative_to(self.root))
                scope = scope_name
        env_name = "CAIRN_" + key.upper()
        if env_name in os.environ and os.environ[env_name] != "":
            raw = os.environ[env_name]
            value = _coerce(DEFAULTS[key], raw)
            found.append({"scope": "runtime.env", "source": "env:" + env_name, "value": value})
            source = "env:" + env_name
            scope = "runtime.env"
        if cli_value is not None:
            value = cli_value
            found.append({"scope": "runtime.cli", "source": "cli", "value": value})
            source = "cli"
            scope = "runtime.cli"
        # Порядок: останній виграє. Усі попередні — ті, що затьмарені.
        shadowed = [item for item in found if item is not found[-1]]
        return {
            "key": key,
            "value": value,
            "source": source,
            "scope": scope,
            "precedence": "cli > env > user.local > machine.local > project.local > project.shared > default",
            "secret_class": "none",
            "shadowed": shadowed,
        }

    def doctor(self) -> dict:
        checks = []

        def add(name: str, ok: bool, detail: str):
            checks.append({"name": name, "ok": ok, "detail": detail})

        add("manifest", (self.cairn / "manifest.json").exists(), "manifest.json")
        chain = ledger.verify(ledger.ledger_path(self.root))
        add("ledger_chain", chain["ok"], f"подій {chain['count']}; {'; '.join(chain['problems'])}")
        con = self.connect()
        try:
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            add("sqlite", integrity == "ok", str(integrity))
            version = con.execute("SELECT version FROM schema_meta").fetchone()
            add("schema", version is not None and int(version["version"]) == DB_SCHEMA_VERSION,
                f"версія {version['version'] if version else 'немає'}")
            secrets = []
            for ev in ledger.read_events(ledger.ledger_path(self.root)):
                secrets.extend(secret_hits(ev.get("payload")))
            add("secrets", not secrets, "чисто" if not secrets else ",".join(sorted(set(secrets))))
        finally:
            con.close()
        repos = self.read("SELECT id, path, head FROM repositories")
        if not repos:
            add("git", True, "репозиторіїв ще немає")
        else:
            for repo in repos:
                exists = Path(repo["path"]).exists()
                add(f"git:{repo['id'][:12]}", exists, repo["path"])
        ok = all(item["ok"] for item in checks)
        return {"ok": ok, "checks": checks, "schema_version": SCHEMA_VERSION}


def parse_actor(value: str | dict | None, *, default: str = "human:local") -> dict:
    if isinstance(value, dict):
        if value.get("kind") not in {"human", "agent", "system"}:
            raise CairnError("bad_actor", "актор має kind human|agent|system")
        return {"kind": value["kind"], "id": value.get("id") or value["kind"]}
    raw = value or default
    kind, _, ident = raw.partition(":")
    if kind not in {"human", "agent", "system"} or not ident:
        raise CairnError("bad_actor", "актор у формі human:id, agent:id або system:id", exit_code=2)
    return {"kind": kind, "id": ident}


def require_human(actor: dict) -> None:
    if actor["kind"] != "human":
        raise CairnError(
            "authority_required",
            "цю дію записує лише людина через CLI або панель, не агент",
            details={"actor": actor},
        )


def _event_from_row(row: sqlite3.Row) -> dict:
    return {
        "event_id": row["event_id"],
        "seq": row["seq"],
        "event_type": row["event_type"],
        "aggregate_id": row["aggregate_id"],
        "actor": json.loads(row["actor_json"]),
        "occurred_at": row["occurred_at"],
        "recorded_at": row["recorded_at"],
        "payload": json.loads(row["payload_json"]),
        "payload_hash": row["payload_hash"],
        "idempotency_key": row["idempotency_key"],
    }


def _coerce(sample, raw: str):
    if isinstance(sample, bool):
        return raw.lower() in {"1", "true", "yes"}
    if isinstance(sample, int) and not isinstance(sample, bool):
        return int(raw)
    if isinstance(sample, list):
        return [part.strip() for part in raw.split(",") if part.strip()]
    return raw
