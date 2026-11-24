"""Вузлова причина в житті: пізній коміт, паралельні сесії, перенос.

Мандат §7.5 вимагає перевірити саме ці три випадки окремо. Файлові рівні
вже покриті (`verify_change_notes.py`, `verify_auto_capture.py`), а
`verify_adapter_node_cause.py` фіксує межу доказу на один адаптер у
статиці. Тут перевіряється те, чого в них немає: що статус вузлової
причини правильно ЗМІНЮЄТЬСЯ в часі й ПЕРЕЖИВАЄ перенос.

1. **Пізній коміт.** Поки унікального Git-переходу немає, кандидат не
   входить у підтверджену історію: `node_attested` = 0. Коміт з'являється
   пізніше — статус підвищується до 1, а первинний час правки
   зберігається, бо час синку не є часом події.
2. **Паралельні сесії.** Дві сесії різних агентів торкаються того самого
   вузла. Доведеною стає лише та, що має унікальний перехід вмісту;
   сусідня не підвищується ні за часом, ні за згадкою шляху.
3. **Перенос.** Пакет відновлюється в ЧИСТЕ сховище на тій самій
   Git-історії: `node_attested` і текст мотиву мусять лишитися без
   локального журналу агента.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fixture_repo import PY_FILE, make_repo  # noqa: E402

from cairn_unified import code, compaction_link, federation, nodes, semantic, why  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

ACTOR = {"kind": "system", "id": "node-cause-lifecycle"}
HUMAN = {"kind": "human", "id": "gate-human"}
MOTIVE = "звузити відповідь до зміненого вузла"
SOURCE_TIME = "2026-11-20T10:00:00+02:00"


def prepare(base: Path, name: str):
    """Репозиторій із двома станами + сховище з проіндексованим вузлом."""
    repo = make_repo(base / name,
                     files={PY_FILE: "def run():\n    return 1\n"},
                     second={PY_FILE: "def run():\n    return 2\n"})
    Store(base / f"{name}-store").init()
    store = Store.open(base / f"{name}-store")
    ident = code.git.identity(repo["path"])
    store.mutate(lambda con: store.append(
        con, "repository.upserted", ident["id"], ident, ACTOR,
        f"repo:{ident['id']}"))
    nodes.index(store, ident["id"], ACTOR, paths=[PY_FILE])
    found = nodes.resolve(store, ident["id"], PY_FILE)
    whole = next(node["stable_key"] for node in found
                 if str(node["address"]).startswith("whole:"))
    return repo, store, ident["id"], whole


def add_session(store, repo_id: str, source: str, commit: str):
    return semantic.put_object(
        store, object_type="session", title=source, summary="", actor=ACTOR,
        stable_key=f"lifecycle-{source}",
        semantic={"kind": "session", "session_id": source,
                  "source_kind": source, "commit": commit,
                  "source_occurred_at": SOURCE_TIME})["object"]["id"]


def add_candidate(store, node_key: str, session: str, event_id: str,
                  rationale_source: str = "agent_reasoning_same_event",
                  motive: str = MOTIVE):
    """Правка з мотивом, але БЕЗ Git-свідка: кандидат, не доведене."""
    semantic.relate(
        store, from_id=node_key, to_id=session, relation="caused_by",
        actor=ACTOR, condition={
            "basis": "tool_mutation",
            # `ts` — саме те поле, яке `why` віддає як час події
            # (why.py:256). Воно мусить лишитися часом ПРАВКИ, а не синку.
            "evidence": {"event_id": event_id, "path": PY_FILE,
                         "rationale_source": rationale_source,
                         "ts": SOURCE_TIME},
            "rationale": motive})


def witness(store, session: str, fact: str, node_key: str, event_id: str):
    """Унікальний перехід вмісту — те, що підвищує кандидата."""
    semantic.relate(store, from_id=session, to_id=fact,
                    relation="mutation_in_commit", actor=ACTOR, condition={
                        "event_id": event_id, "node_key": node_key,
                        "path": PY_FILE,
                        "evidence_level": "content_transition"})


def attested(store, repo_id: str, repo, commit: str) -> dict:
    answer = why.answer(store, repo_id=repo_id, path=PY_FILE, commit=commit,
                        repo_path=repo["path"])
    return {"count": answer["coverage"]["node_attested_causes"],
            "answer": answer["answer"],
            "detail": answer["why_detail"]}


def case_late_commit(base: Path) -> list[str]:
    repo, store, repo_id, whole = prepare(base, "late")
    session = add_session(store, repo_id, "opencode", "")
    add_candidate(store, whole, session, "edit-late")
    errors = []

    before = attested(store, repo_id, repo, repo["head"])
    if before["count"] != 0:
        errors.append(f"пізній коміт: до свідка node_attested={before['count']}, очікувано 0")

    # Коміт з'являється ПІЗНІШЕ — окремою дією, як в іншій сесії.
    fact = compaction_link.link_commit(
        store, repo_id=repo_id, repo_path=repo["path"], commit=repo["head"],
        work_id="", actor=ACTOR)["fact_id"]
    semantic.relate(store, from_id=session, to_id=fact,
                    relation="context_for_commit", actor=ACTOR)
    witness(store, session, fact, whole, "edit-late")

    after = attested(store, repo_id, repo, repo["head"])
    if after["count"] != 1:
        errors.append(f"пізній коміт: після свідка node_attested={after['count']}, очікувано 1")
    if MOTIVE not in after["answer"]:
        errors.append("пізній коміт: текст мотиву зник з відповіді")
    # Первинний час мусить лишитися часом ПРАВКИ, а не синку.
    kept = [item for item in after["detail"]
            if item.get("semantic_cause_status") == "node_attested"]
    if not kept:
        errors.append("пізній коміт: доведеної причини немає в деталях")
    elif any(item.get("ts") != SOURCE_TIME for item in kept):
        errors.append("пізній коміт: час події підмінено (очікувано "
                      f"{SOURCE_TIME}, отримано {[i.get('ts') for i in kept]})")

    # Повторне підвищення мусить бути ідемпотентним.
    witness(store, session, fact, whole, "edit-late")
    again = attested(store, repo_id, repo, repo["head"])
    if again["count"] != 1:
        errors.append(f"пізній коміт: повторний синк дав {again['count']}, очікувано 1")
    return errors


def case_parallel(base: Path) -> list[str]:
    repo, store, repo_id, whole = prepare(base, "parallel")
    fact = compaction_link.link_commit(
        store, repo_id=repo_id, repo_path=repo["path"], commit=repo["head"],
        work_id="", actor=ACTOR)["fact_id"]
    first = add_session(store, repo_id, "opencode", repo["head"])
    second = add_session(store, repo_id, "codex", repo["head"])
    for session in (first, second):
        semantic.relate(store, from_id=session, to_id=fact,
                        relation="context_for_commit", actor=ACTOR)
    # Обидві сесії торкаються ТОГО САМОГО вузла в тому самому коміті.
    add_candidate(store, whole, first, "edit-par-a")
    add_candidate(store, whole, second, "edit-par-b",
                  rationale_source="agent_reasoning_prior_by_seq",
                  motive="сусідній мотив іншої сесії")
    # Унікальний перехід має ЛИШЕ перша.
    witness(store, first, fact, whole, "edit-par-a")

    out = attested(store, repo_id, repo, repo["head"])
    errors = []
    if out["count"] != 1:
        errors.append(f"паралельні сесії: node_attested={out['count']}, очікувано 1")
    proven = [item for item in out["detail"]
              if item.get("semantic_cause_status") == "node_attested"]
    if any(str(item.get("event_id") or "") == "edit-par-b" for item in proven):
        errors.append("паралельні сесії: сусідній мотив підвищено без свідка")
    if "сусідній мотив іншої сесії" in out["answer"] and len(proven) == 1:
        joint = [item for item in proven
                 if "сусідній" in str(item.get("motive") or item.get("text") or "")]
        if joint:
            errors.append("паралельні сесії: чужий мотив приписано доведеній правці")
    return errors


def case_transfer(base: Path) -> list[str]:
    repo, store, repo_id, whole = prepare(base, "transfer")
    fact = compaction_link.link_commit(
        store, repo_id=repo_id, repo_path=repo["path"], commit=repo["head"],
        work_id="", actor=ACTOR)["fact_id"]
    session = add_session(store, repo_id, "opencode", repo["head"])
    semantic.relate(store, from_id=session, to_id=fact,
                    relation="context_for_commit", actor=ACTOR)
    add_candidate(store, whole, session, "edit-move")
    witness(store, session, fact, whole, "edit-move")

    source_state = attested(store, repo_id, repo, repo["head"])
    errors = []
    if source_state["count"] != 1:
        errors.append(f"перенос: у джерелі node_attested={source_state['count']}, очікувано 1")

    bundle_path = base / "transfer.bundle.json"
    exported = federation.export_bundle(store, path=bundle_path)
    bundle = federation.load_bundle(bundle_path)
    checked = federation.verify_bundle(bundle)
    if not (checked.get("ok") or checked.get("digest_ok")):
        errors.append(f"перенос: пакет не перевірено: {checked}")

    # ЧИСТЕ сховище, жодного локального журналу агента.
    target = base / "transfer-target"
    federation.restore_bundle(bundle, target, HUMAN)
    moved = Store.open(target)
    after = attested(moved, repo_id, repo, repo["head"])
    if after["count"] != source_state["count"]:
        errors.append(f"перенос: node_attested {source_state['count']} -> {after['count']}")
    if MOTIVE not in after["answer"]:
        errors.append("перенос: текст мотиву не пережив перенос")
    if exported.get("event_count") and exported["event_count"] < 1:
        errors.append("перенос: пакет порожній")
    return errors


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="node-cause-life-") as temp:
        base = Path(temp)
        results = (("пізній коміт", case_late_commit(base)),
                   ("паралельні сесії", case_parallel(base)),
                   ("перенос пакета", case_transfer(base)))
    bad = 0
    for label, errors in results:
        print(f"  {'ok  ' if not errors else 'FAIL'} {label}")
        for error in errors:
            print(f"       {error}")
        bad += len(errors)
    total = len(results)
    print(f"ВЕРДИКТ: життєвий цикл вузлової причини ({total - sum(1 for _, e in results if e)}/{total})")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
