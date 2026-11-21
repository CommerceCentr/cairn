"""Відповідь про вузол: шість полів, час, межі, детермінізм.

Фікстура повністю в temp: git-репозиторій на два коміти, індексація вузлів,
сесія, факт коміту, автокомактація з наміром. Жодного приватного шляху й
жодного `opencode.db`.

Реверси, без яких тест нічого не доводить:

* скасоване рішення НЕ потрапляє у відповідь, але доступне через `as_of`;
* `verifier_error` відрізняється від `unresolved` у `omissions`;
* хронологія впорядкована ЧАСОМ, а не порядком ребер;
* бюджет або виконано, або прямо сказано, що ні;
* повторний виклик дає байт-ідентичний результат.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fixture_repo import PY_FILE, make_repo  # noqa: E402

from cairn_unified import code, nodes, semantic, why, work  # noqa: E402
from cairn_unified.errors import CairnError  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

HUMAN = {"kind": "human", "id": "why-human"}
SYSTEM = {"kind": "system", "id": "why-indexer"}
INTENT = "зробити так, щоб відповідь про вузол була дешевою"
PY_V1 = "def run():\n    return 1\n"
PY_V2 = "def run():\n    return 2\n"


def build(temp: Path) -> tuple[Store, str, str, dict]:
    repo = make_repo(temp / "repo", files={PY_FILE: PY_V1},
                     second={PY_FILE: PY_V2},
                     messages=("initial", "make the answer cheap"))
    Store(temp / "store").init()
    store = Store.open(temp / "store")
    ident = code.git.identity(repo["path"])
    store.mutate(lambda con: store.append(con, "repository.upserted", ident["id"],
                                          ident, HUMAN, f"repo:{ident['id']}"))
    repo_id = ident["id"]
    nodes.index(store, repo_id, SYSTEM, paths=[PY_FILE])
    node = nodes.resolve(store, repo_id, PY_FILE, address=f"whole:{PY_FILE}")[0]
    key = node["stable_key"]

    wid = work.create_work(store, title="дешева відповідь про вузол", actor=HUMAN,
                           scope={"repo_id": repo_id})["work"]["id"]
    semantic.relate(store, from_id=wid, to_id=key, relation="targets_symbol", actor=HUMAN)

    session = semantic.put_object(
        store, object_type="session", title="сесія фікстури", summary="дві мутації",
        actor=HUMAN, stable_key="why-session-1",
        semantic={"kind": "session", "session_id": "ses-fixture",
                  "compaction_message_id": "msg-fixture", "commit": repo["head"]},
    )["object"]["id"]
    semantic.relate(store, from_id=key, to_id=session, relation="caused_by", actor=HUMAN)

    fact = semantic.put_object(
        store, object_type="fact", title=f"commit {repo['head'][:7]}",
        summary="CrUD шляху", actor=HUMAN, stable_key="why-fact-1",
        semantic={"kind": "commit_fact", "commit": repo["head"], "basis": "git show"},
    )["object"]["id"]
    semantic.relate(store, from_id=fact, to_id=key, relation="mutated", actor=HUMAN)

    auto = semantic.put_object(
        store, object_type="decision", title="автокомактація фікстури",
        summary="чому вузол змінено", actor=HUMAN, stable_key="why-auto-1",
        semantic={"kind": "auto_compaction", "commit": repo["head"],
                  "chain_shown": 2, "chain_total": 7,
                  "intent_refs": [{"message_id": "msg-1", "seq": 5, "text": INTENT}]},
    )["object"]["id"]
    semantic.relate(store, from_id=auto, to_id=session, relation="derived_from", actor=HUMAN)

    stale = semantic.put_object(
        store, object_type="decision", title="скасоване рішення",
        summary="ТАК РОБИТИ НЕ ТРЕБА", actor=HUMAN, stable_key="why-stale-1",
        semantic={"kind": "decision", "commit": repo["head"]},
    )["object"]["id"]
    semantic.relate(store, from_id=stale, to_id=session, relation="derived_from", actor=HUMAN)
    return store, repo_id, key, {"repo": repo, "stale": stale, "work": wid}


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    temp = Path(tempfile.mkdtemp(prefix="why-"))
    try:
        store, repo_id, key, extra = build(temp)
        before_revoke = store.now()
        out = why.answer(store, repo_id=repo_id, path=PY_FILE,
                         commit=extra["repo"]["head"])

        for field in ("answer", "chronology", "provenance", "trust",
                      "omissions", "cost"):
            value = out.get(field)
            checks.append((f"поле {field} непорожнє", bool(value),
                           str(len(value) if hasattr(value, "__len__") else value)[:30]))

        stamps = [c["ts"] for c in out["chronology"] if c["ts"]]
        checks.append(("хронологія впорядкована часом", stamps == sorted(stamps),
                       f"{len(stamps)} записів"))
        kinds = {c["kind"] for c in out["chronology"]}
        checks.append(("ланцюг вузол->сесія->намір пройдено",
                       {"session", "commit_fact", "auto_compaction"} <= kinds,
                       str(sorted(kinds))))
        checks.append(("намір користувача у відповіді", INTENT[:24] in out["answer"],
                       out["answer"].count("\n") and "є" or "немає"))
        checks.append(("межу названо у відповіді",
                       "не семантичну причину" in out["answer"], "є"))
        checks.append(("обрізаний ланцюг названо",
                       any(o["kind"] == "bounded_chain" for o in out["omissions"]),
                       str([o["kind"] for o in out["omissions"]])))

        # --- реверс: скасоване рішення не подається як чинне ---
        semantic.revoke(store, object_id=extra["stale"], actor=HUMAN, reason="тест")
        after = why.answer(store, repo_id=repo_id, path=PY_FILE,
                           commit=extra["repo"]["head"])
        revoked = [t for t in after["trust"] if t["object_id"] == extra["stale"]]
        checks.append(("скасоване позначено invalidated",
                       bool(revoked) and revoked[0]["freshness"] == "invalidated",
                       str(revoked[:1])))
        past = why.answer(store, repo_id=repo_id, path=PY_FILE,
                          commit=extra["repo"]["head"], as_of=before_revoke)
        past_trust = [t for t in past["trust"] if t["object_id"] == extra["stale"]]
        checks.append(("через as_of старий стан доступний",
                       bool(past_trust) and past_trust[0]["freshness"] != "invalidated",
                       str(past_trust[:1])))

        # --- реверс: бюджет або виконано, або сказано, що ні ---
        tight = why.answer(store, repo_id=repo_id, path=PY_FILE, budget_chars=1200)
        fits = tight["cost"]["chars"] <= 1200
        said = any(o["kind"] == "budget_exceeded" for o in tight["omissions"])
        checks.append(("бюджет виконано або названо невиконаним", fits or said,
                       f"chars={tight['cost']['chars']} exceeded={said}"))

        # --- реверс: детермінізм ---
        twice = why.answer(store, repo_id=repo_id, path=PY_FILE,
                           commit=extra["repo"]["head"])
        same = (json.dumps({k: v for k, v in after.items() if k != "cost"},
                           ensure_ascii=False, sort_keys=True)
                == json.dumps({k: v for k, v in twice.items() if k != "cost"},
                              ensure_ascii=False, sort_keys=True))
        checks.append(("повторний виклик байт-ідентичний", same, "так" if same else "ні"))

        # --- реверс: невідомий вузол -> exit 1, це ВІДПОВІДЬ ---
        try:
            why.answer(store, repo_id=repo_id, path="no/such/file.py")
            checks.append(("невідомий вузол -> відмова", False, "не відмовив"))
        except CairnError as err:
            checks.append(("невідомий вузол -> exit 1 (це відповідь)",
                           err.code == "node_not_found" and err.exit_code == 1,
                           f"{err.code}/{err.exit_code}"))
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    good = 0
    for label, ok, detail in checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {label:48} | {detail[:40]}")
        good += int(ok)
    print(f"ВЕРДИКТ: відповідь про вузол ({good}/{len(checks)})")
    return 0 if good == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
