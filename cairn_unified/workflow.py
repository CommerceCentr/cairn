"""Workflow Runtime: довговічні запуски, checkpoint, pause/stop/resume.

 Кроки не виконують довільний код. Вони записують детермінований результат у
 журнал — саме це дає змогу відтворити стан після збою process без дублювання
 побічних ефектів. Реальні дії (коміт, тест, приймання) живуть у відповідних
 модулях і вносять власні події з idempotency key.
"""

from __future__ import annotations

import json

from .canon import canonical, prefixed, sha256_text
from .errors import CairnError

RUN_STATES = ("running", "paused", "stopped", "completed")
STEP_STATES = ("succeeded", "skipped", "failed")


def _run(con, run_id: str) -> dict:
    row = con.execute("SELECT * FROM workflow_runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise CairnError("not_found", f"запуск {run_id} не знайдено", exit_code=1)
    return dict(row)


def define(store, *, name: str, definition: dict, actor: dict,
           version: int = 1, idempotency_key: str | None = None) -> dict:
    digest = sha256_text(canonical(definition))
    body = {
        "id": prefixed("wfd"),
        "name": name,
        "version": int(version),
        "definition": definition,
        "content_hash": f"sha256:{digest}",
    }

    def op(con):
        ev = store.append(con, "workflow_definition.upserted", body["id"], body, actor, idempotency_key)
        return {"definition": ev["payload"], "event_id": ev["event_id"], "replayed": ev["replayed"]}

    return store.mutate(op)


def start(store, definition_id: str, actor: dict, *, input: dict | None = None,
          retention: str = "durable", idempotency_key: str | None = None) -> dict:
    row = store.read_one("SELECT * FROM workflow_definitions WHERE id = ?", (definition_id,))
    if row is None:
        raise CairnError("not_found", f"визначення {definition_id} не знайдено", exit_code=1)
    if retention not in {"durable", "ephemeral", "promotable", "retention_locked"}:
        raise CairnError("bad_retention", f"невідомий режим {retention}", exit_code=2)
    body = {
        "id": prefixed("wfr"),
        "definition_id": definition_id,
        "definition_hash": row["content_hash"],
        "state": "running",
        "input": input or {},
        "checkpoint": {"index": 0, "history": []},
        "retention": retention,
        "updated_at": store.now(),
    }

    def op(con):
        ev = store.append(con, "workflow_run.upserted", body["id"], body, actor, idempotency_key)
        return {"run": ev["payload"], "event_id": ev["event_id"], "replayed": ev["replayed"]}

    return store.mutate(op)


def _definition(con, definition_id: str) -> dict:
    row = con.execute(
        "SELECT definition_json FROM workflow_definitions WHERE id = ?", (definition_id,)
    ).fetchone()
    if row is None:
        raise CairnError("not_found", "визначення запуску зникло", exit_code=1)
    return json.loads(row["definition_json"])


def _result(step: dict, checkpoint: dict, input: dict) -> dict:
    kind = step.get("kind") or "mark"
    if kind == "work_ref":
        return {"work_item_id": step.get("work_item_id")}
    if kind == "gate":
        return {"gate_id": step.get("gate_id"), "awaits": "external_resolution"}
    if kind == "decision":
        return {"decision_id": step.get("decision_id"), "action": step.get("action", "reference")}
    if kind == "analyze":
        return {"summary": step.get("summary", ""), "input_keys": sorted(input.keys())}
    return {"label": step.get("label") or step.get("key"), "index": checkpoint["index"]}


def advance(store, run_id: str, actor: dict) -> dict:
    def op(con):
        run = _run(con, run_id)
        if run["state"] != "running":
            raise CairnError("run_not_running", f"запуск у стані {run['state']}", exit_code=2)
        definition = _definition(con, run["definition_id"])
        steps = list(definition.get("steps") or [])
        checkpoint = json.loads(run["checkpoint_json"] or "{}")
        checkpoint.setdefault("index", 0)
        checkpoint.setdefault("history", [])
        index = int(checkpoint["index"])
        inp = json.loads(run["input_json"] or "{}")

        if index >= len(steps):
            body = _run_body(run, state="completed", checkpoint=checkpoint)
            ev = store.append(con, "workflow_run.upserted", run_id, body, actor)
            return {"run": ev["payload"], "step": None, "completed": True, "event_id": ev["event_id"]}

        step = steps[index]
        key = f"step:{run_id}:{index}:{step.get('key', 'step')}"
        result = {"index": index, "key": step.get("key"), "kind": step.get("kind") or "mark",
                  "output": _result(step, checkpoint, inp)}
        step_body = {
            "id": prefixed("wst"),
            "run_id": run_id,
            "step_key": step.get("key") or f"step-{index}",
            "state": "succeeded",
            "attempt": 1,
            "idempotency_key": key,
            "result": result,
        }
        ev = store.append(con, "workflow_step.upserted", run_id, step_body, actor, key)
        checkpoint["index"] = index + 1
        checkpoint["history"] = checkpoint["history"] + [step_body["step_key"]]
        state = "completed" if checkpoint["index"] >= len(steps) else "running"
        run_ev = store.append(con, "workflow_run.upserted", run_id,
                              _run_body(run, state=state, checkpoint=checkpoint), actor)
        return {"run": run_ev["payload"], "step": ev["payload"], "completed": state == "completed",
                "event_id": run_ev["event_id"]}

    return store.mutate(op)


def _run_body(run: dict, *, state: str, checkpoint: dict) -> dict:
    # updated_at НЕ задаємо: проєкція візьме recorded_at події. Це робить
    # live-проєкцію й rebuild ідентичними навіть для часу оновлення.
    return {
        "id": run["id"],
        "definition_id": run["definition_id"],
        "definition_hash": run["definition_hash"],
        "state": state,
        "input": json.loads(run["input_json"] or "{}"),
        "checkpoint": checkpoint,
        "retention": run["retention"],
    }


def _set_state(store, run_id: str, state: str, actor: dict) -> dict:
    if state not in RUN_STATES:
        raise CairnError("bad_run_state", f"невідомий стан {state}", exit_code=2)

    def op(con):
        run = _run(con, run_id)
        if run["state"] == state:
            return {"run": {**run, "checkpoint": json.loads(run["checkpoint_json"] or "{}")},
                    "event_id": None, "replayed": True}
        if run["state"] in {"completed", "stopped"} and state != "stopped":
            raise CairnError("run_closed", "запуск уже завершено", exit_code=2)
        checkpoint = json.loads(run["checkpoint_json"] or "{}")
        body = {
            "id": run["id"],
            "definition_id": run["definition_id"],
            "definition_hash": run["definition_hash"],
            "state": state,
            "input": json.loads(run["input_json"] or "{}"),
            "checkpoint": checkpoint,
            "retention": run["retention"],
        }
        ev = store.append(con, "workflow_run.upserted", run_id, body, actor)
        return {"run": ev["payload"], "event_id": ev["event_id"], "replayed": False}

    return store.mutate(op)


def pause(store, run_id: str, actor: dict) -> dict:
    return _set_state(store, run_id, "paused", actor)


def resume(store, run_id: str, actor: dict) -> dict:
    return _set_state(store, run_id, "running", actor)


def stop(store, run_id: str, actor: dict) -> dict:
    return _set_state(store, run_id, "stopped", actor)


def get(store, run_id: str) -> dict:
    row = store.read_one("SELECT * FROM workflow_runs WHERE id = ?", (run_id,))
    if row is None:
        raise CairnError("not_found", f"запуск {run_id} не знайдено", exit_code=1)
    steps = store.read(
        "SELECT step_key, state, attempt, idempotency_key, result_json FROM workflow_steps "
        "WHERE run_id = ? ORDER BY id", (run_id,)
    )
    return {
        "id": row["id"],
        "definition_id": row["definition_id"],
        "definition_hash": row["definition_hash"],
        "state": row["state"],
        "input": json.loads(row["input_json"] or "{}"),
        "checkpoint": json.loads(row["checkpoint_json"] or "{}"),
        "retention": row["retention"],
        "updated_at": row["updated_at"],
        "steps": [{**step, "result": json.loads(step.pop("result_json") or "{}")} for step in steps],
    }


def list_runs(store, state: str | None = None) -> list[dict]:
    rows = store.read("SELECT id, state, definition_id, updated_at FROM workflow_runs ORDER BY updated_at")
    if state:
        rows = [row for row in rows if row["state"] == state]
    return rows
