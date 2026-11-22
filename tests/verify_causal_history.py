"""Причинний контекст + git history одним проходом, без зовнішніх шляхів.

Fixtures: тимчасовий git-репозиторій і тимчасовий sqlite у форматі
`session_message`. Жодного реального журналу, PHP чи мережі. Реверси:
коміт не стає причиною, хибне джерело ескалує, повтор не подвоює ребра.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT))

from cairn_unified.adapters import opencode  # noqa: E402
from cairn_unified import causal_history, code, semantic, verification, work  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

SID = "ses-fixture-causal"
ACTOR = {"kind": "system", "id": "causal-history-test"}
FILE = "src/cairn_unified/causal_history.py"
INTENT = "зв'язати причину з мутацією та історією коміту"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


def _fixture_repo(path: Path) -> str:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "fixture@local")
    _git(path, "config", "user.name", "fixture")
    target = path / FILE
    target.parent.mkdir(parents=True)
    target.write_text("def first():\n    return 1\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "initial")
    target.write_text("def first():\n    return 2\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "link causal context and history")
    return _git(path, "rev-parse", "HEAD")


def _fixture_db(path: Path, repo: Path, commit_ms: int) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE session_message ("
                    "id TEXT, session_id TEXT, type TEXT, seq INTEGER, "
                    "time_created INTEGER, data TEXT)")
        base = commit_ms - 60_000

        def add(seq: int, typ: str, data: dict, ms: int) -> None:
            con.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?)",
                        (f"m{seq}", SID, typ, seq, ms,
                         json.dumps(data, ensure_ascii=False)))

        add(1, "compaction", {
            "time": {"created": base + 1000}, "summary": "compaction fixture",
            "metadata": {"keepID": "keep-fixture"}, "reason": "test",
        }, base + 1000)
        add(10, "user", {"time": {"created": base + 2000}, "text": INTENT},
            base + 2000)
        add(11, "assistant", {
            "time": {"created": base + 3000},
            "content": [
                {"type": "reasoning", "text": "agent expands the user intent"},
                {"type": "tool", "name": "edit", "id": "tool-11", "state": {
                    "status": "completed",
                    "input": {"path": str(repo / FILE), "oldString": "return 1",
                              "newString": "return 2"},
                    "time": {"created": base + 3000},
                    "metadata": {"files": [{"patch": "@@ -1 +1 @@"}]},
                }},
            ],
        }, base + 3000)
        con.commit()
    finally:
        con.close()


def _rels(store):
    out = []
    for row in store.read("SELECT payload_json FROM events "
                          "WHERE event_type = 'relation.recorded'"):
        p = json.loads(row["payload_json"])
        out.append((p["from_id"], p["relation_type"], p["to_id"]))
    return out


def _object(store, oid: str) -> dict:
    return json.loads(store.read_one(
        "SELECT payload_json FROM objects WHERE id = ?", (oid,))["payload_json"])


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="causal-history-"))
    try:
        repo = temp / "repo"
        commit = _fixture_repo(repo)
        commit_ms = int(_git(repo, "show", "-s", "--format=%ct", commit)) * 1000
        db = temp / "opencode.db"
        _fixture_db(db, repo, commit_ms)

        store_path = temp / "store"
        Store(store_path).init()
        store = Store.open(store_path)
        ident = code.git.identity(str(repo))
        wi = work.create_work(
            store, title="link causal context and history", actor=ACTOR,
            scope={"repo_id": ident["id"]})["work"]
        wid = wi["id"]

        report = causal_history.link(
            store, repo_path=repo, session_id=SID, commit=commit, work_id=wid,
            actor=ACTOR, opencode_db=str(db))
        second = causal_history.link(
            store, repo_path=repo, session_id=SID, commit=commit, work_id=wid,
            actor=ACTOR, opencode_db=str(db))

        rels = _rels(store)
        objects = report["objects"]
        verified = report["verified"]
        checks: list[tuple[str, bool]] = []
        checks.append(("коміт резолвиться й історія фото",
                       report["ok"] and report["commit"] == commit))
        checks.append(("якір — компактація перед комітом",
                       report["anchor_seq"] == 1 and report["anchor_before_commit"]))
        checks.append(("причинні ребра з реальної мутації",
                       report["causal_links"] >= 1 and not report["causal_unresolved"]))
        checks.append(("git history дає mutated", len(report["history_mutated"]) >= 1))
        checks.append(("намір користувача в автокомактації",
                       INTENT in report["auto_compaction_text"]))
        checks.append(("п'ять об'єктів створені", len(objects) == 5))
        checks.append(("усі об'єкти верифіковані джерелами",
                       len(verified) == 5 and all(v["verified"] for v in verified.values())))
        checks.append(("вузол -> сесія через caused_by",
                       any(r[1] == "caused_by" and r[2] == objects[0] for r in rels)))
        checks.append(("commit_fact -> вузол через mutated",
                       any(r[1] == "mutated" and r[0] == objects[3] for r in rels)))
        checks.append(("автокомактація -> сесія через derived_from",
                       any(r[1] == "derived_from" and r[0] == objects[4]
                           and r[2] == objects[0] for r in rels)))
        checks.append(("коміт не став причиною",
                       not any(r[1] == "caused_by" and r[0] == objects[3] for r in rels)))
        checks.append(("«чому відкинуто» має власний об'єкт",
                       report["unresolved_object_id"] == objects[1]
                       and _object(store, objects[1])["semantic"]["kind"]
                       == "unresolved_mutations"))
        checks.append(("дескриптор зони має власний об'єкт",
                       report["zone_object_id"] == objects[2]
                       and _object(store, objects[2])["semantic"]["kind"]
                       == "zone_descriptors"
                       and len(report["zone_descriptors"]) == 1))
        checks.append(("контекст містить причину, відкинуте, зону й коміт",
                       report["context_digest"] is not None
                       and len(report["context_shown"]) >= 3
                       and objects[1] in report["context_shown"]))
        checks.append(("повтор не подвоює ребра",
                       second["causal_replayed"] == report["causal_links"]
                       and second["governed_new"] == 0
                       and second["context_digest"] == report["context_digest"]))

        # Реверс: хибне джерело не підтверджує сесію.
        bad = json.loads(json.dumps(_object(store, objects[0])))
        bad["semantic"]["opencode_db"] = str(temp / "no-such-db.sqlite")
        bad_res = verification.check_object(store, bad, repo_path=repo)
        # Контракт став точнішим: «джерела немає» (unresolved) окремо від
        # «перевірка впала» (verifier_error). Тут файла немає -> unresolved.
        checks.append(("хибне джерело ескалує як unresolved",
                       any(r == "unresolved:session_source_missing" for r in bad_res[1])
                       and not any(r.startswith("verifier_error") for r in bad_res[1])))

        # Реверс другого боку тієї самої межі: джерело ІСНУЄ, але це не БД.
        # Мусить бути verifier_error (дефект перевірки), а не unresolved
        # (відсутнє джерело) — інакше дві причини знову під однією міткою.
        notdb = temp / "not-a-database.bin"
        notdb.write_bytes(b"this is not sqlite")
        broken = json.loads(json.dumps(_object(store, objects[0])))
        broken["semantic"]["opencode_db"] = str(notdb)
        broken_res = verification.check_object(store, broken, repo_path=repo)
        checks.append(("наявне, але нечитне джерело -> verifier_error",
                       any(r.startswith("verifier_error:session_source_")
                           for r in broken_res[1])
                       and not any(r == "unresolved:session_source_missing"
                                   for r in broken_res[1])))

        # Реверс: підміна причини відкидання не проходить перевірку.
        bad_u = json.loads(json.dumps(_object(store, objects[1])))
        bad_u["semantic"]["unresolved_digest"] = "00" * 32
        bad_u_res = verification.check_object(store, bad_u, repo_path=repo)
        checks.append(("підміна причини відкидання ескалує",
                       any("unresolved_reasons_mismatch" in r for r in bad_u_res[1])))

        # Реверс: підміна digest дескрипторів не проходить перевірку.
        bad_z = json.loads(json.dumps(_object(store, objects[2])))
        bad_z["semantic"]["digest"] = "00" * 32
        bad_z_res = verification.check_object(store, bad_z, repo_path=repo)
        checks.append(("підміна дескрипторів зони ескалує",
                       any("zone_descriptors_mismatch" in r for r in bad_z_res[1])))

        for label, ok in checks:
            print(f"  {'ok  ' if ok else 'FAIL'} {label}")
        passed = sum(1 for _l, ok in checks if ok)
        verdict = ("причинний контекст + git history зв'язані"
                   if passed == len(checks) else "FAIL")
        print(f"ВЕРДИКТ: {verdict} ({passed}/{len(checks)})")
        return 0 if passed == len(checks) else 1
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
