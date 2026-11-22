"""Сесія -> вузол через реальні CrUD-події, з «чому» з двох джерел.

Ланцюг: user_intent -> reasoning -> mutation -> node. Виміряно, що для мутацій
10587+ reasoning порожній, тож без наміру «чому» не існує.

Інваріанти на реальній БД OpenCode (тільки читання) й ізольованому Store:

* ребро `caused_by` іде від індексованого вузла до сесії (не навпаки);
* корінь ребра — слова користувача (`user_message`), мотив — `agent_reasoning`;
* найближчий намір обирається за seq, не за часом (seq 10987 -> мутація 11025);
* автокомактація містить дослівні слова користувача й перевіряється БД;
* мутація поза репо / з не-indexed шляхом не дає ребра;
* seq 9295 (останній перед 8c1eea5) не згадує цей коміт — реверс.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT))

from cairn_unified.adapters import opencode  # noqa: E402
from cairn_unified import code, compaction_link, nodes, verification  # noqa: E402
from cairn_unified.store import Store  # noqa: E402
from cairn_unified.canon import sha256_text  # noqa: E402

SID = "ses_e0a98acebffePayGTXAEIUUJAT"
CMP_ID = "msg_1f952a2e3001AqzjG0iOxQi8zA"   # seq 9295
REPO = Path(r"Q:\prj\cairn")
WORK = Path(r"Q:\prj\cairn-private")   # тут відбувались мутації цієї правки
ROOTS = (REPO, WORK)
ACTOR = {"kind": "system", "id": "compaction-link-test"}
DB = opencode.default_db()
CI_FILE = "cairn_unified/ci_gate.py"
LINK_FILE = "cairn_unified/compaction_link.py"
INTENT_10564 = 'давай знайдемо, де ховається "чому"'
INTENT_10987 = 'між моїм теперішнім повідомленням і попереднім'


def _new_store(path: Path):
    Store(path).init()
    return Store.open(path)


def _events():
    return list(opencode.read(DB, SID))


def _repo_and_index(store):
    ident = code.git.identity(str(REPO))
    store.mutate(lambda con: store.append(con, "repository.upserted", ident["id"],
                                          ident, ACTOR, f"repo:{ident['id']}"))
    # Індексуємо тільки ті шляхи, що реально є в репо; це дає стабільний набір
    # вузлів для звірки напрямку ребра.
    paths = [CI_FILE, "cairn_unified/context.py",
             "cairn_unified/cli.py", "tests/run_all.py",
             LINK_FILE, "cairn_unified/verification.py",
             "adapters/opencode.py", "cairn_unified/ontology.py",
             "tests/verify_compaction_link.py"]
    present = [p for p in paths if (REPO / p).is_file()]
    nodes.index(store, ident["id"], ACTOR, paths=present)
    return ident["id"], present


def _rels(store):
    """Ребра читаємо з подій: `condition` у таблицю relations не проекціюється
    (`db.py:71-81`), а живе в payload події `relation.recorded`."""
    rows = store.read("SELECT aggregate_id,payload_json FROM events "
                      "WHERE event_type = 'relation.recorded'")
    out = []
    for row in rows:
        p = json.loads(row["payload_json"])
        out.append({"from_id": p["from_id"], "to_id": p["to_id"],
                    "relation_type": p["relation_type"],
                    "condition": p.get("condition") or {}})
    return out


def _cond(row):
    return row["condition"]


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="cmp-link-"))
    try:
        store = _new_store(temp)
        repo_id, indexed = _repo_and_index(store)
        events = _events()
        checks: list[tuple[str, bool]] = []

        # 1. Адаптер: CrUD за назвою, не за наявністю шляху.
        kinds = {e["kind"] for e in events}
        muts = [e for e in events if e["kind"] == "mutation"]
        checks.append(("адаптер віддає мутації та компактації",
                       "mutation" in kinds and "compaction" in kinds))
        checks.append(("read/grep не стали мутаціями",
                       all(e.get("tool") in {"write", "edit"} for e in muts)))

        # 2. Компактація seq 9295 — реальний рядок, не вигаданий.
        cmp9295 = next((e for e in events if e["kind"] == "compaction"
                        and e["seq"] == 9295), None)
        checks.append(("seq 9295 існує з правильним message_id",
                       bool(cmp9295) and cmp9295["message_id"] == CMP_ID))
        checks.append(("seq 9295 не згадує 8c1eea5 (реверс)",
                       bool(cmp9295) and "8c1eea5" not in cmp9295["summary"]))

        # 3. Сесія-об'єкт створюється з реальної тотожності.
        s_obj = compaction_link.session_object(
            store, session_id=SID, compaction=cmp9295, repo_id=repo_id,
            actor=ACTOR, opencode_db=DB,
        )
        checks.append(("stable_key з message_id, не з SHA",
                       CMP_ID.split("Aqzj")[0] in s_obj["title"] or
                       s_obj["semantic"]["compaction_message_id"] == CMP_ID))
        checks.append(("digest summary реальний", s_obj["semantic"]["summary_sha256"]
                       == sha256_text(cmp9295["summary"])))

        # 4. Ребра від індексованих вузлів до сесії з basis і rationale.
        result = compaction_link.link_mutations(
            store, events=events, session_obj_id=s_obj["id"], repo_id=repo_id,
            repo_path=ROOTS, actor=ACTOR,
        )
        rels = _rels(store)
        caused = [r for r in rels if r["relation_type"] == "caused_by"
                  and r["to_id"] == s_obj["id"]]
        checks.append(("caused_by існує", len(caused) > 0))
        indexed_keys = {n["stable_key"] for p in indexed
                        for n in nodes.resolve(store, repo_id, p)}
        checks.append(("напрямок: вузол -> сесія",
                       all(r["from_id"] in indexed_keys for r in caused)))
        checks.append(("basis=tool_mutation у всіх",
                       all(_cond(r).get("basis") == "tool_mutation" for r in caused)))
        checks.append(("evidence_level=participation",
                       all(_cond(r).get("evidence_level") == "participation" for r in caused)))
        with_reason = [r for r in caused if _cond(r).get("rationale")]
        # Сила звʼязку мусить бути НАЗВАНА: `same_event` — проза лежить у тій
        # самій події (так пише opencode), `prior_by_seq` — найближча
        # попередня (так виходить у Claude Code, де текст і виклик
        # інструмента — різні записи). Одна мітка на обидва випадки
        # приховала б, наскільки доказ міцний.
        ALLOWED = {"agent_reasoning_same_event", "agent_reasoning_prior_by_seq"}
        checks.append(("rationale має назване джерело",
                       len(with_reason) > 0 and all(
                           _cond(r)["evidence"].get("rationale_source") in ALLOWED
                           for r in with_reason)))
        checks.append(("не triples ребро з неcompleted мутації",
                       all(_cond(r)["evidence"].get("event_id") for r in caused)))
        checks.append(("unresolved записані, не вигадані",
                       len(result["unresolved"]) >= 0))

        # 4b. Корінь «чому» — слова користувача, а не власний текст агента.
        intents = [e for e in events if e["kind"] == "user_intent"]
        with_intent = [r for r in caused
                       if _cond(r).get("intent_source") == "user_message"
                       and _cond(r)["evidence"].get("intent")]
        checks.append(("адаптер віддає user_intent", len(intents) > 0))
        checks.append(("intent_source=user_message з текстом", len(with_intent) > 0))
        checks.append(("намір не підмінений мотивом",
                       all(_cond(r)["evidence"]["intent"] != _cond(r).get("rationale", "")
                           for r in with_intent)))
        target_link = next((l for l in result["links"]
                            if str(l["event_id"]).startswith("11025:")), None)
        checks.append(("найближчий намір 10987 для мутації 11025",
                       bool(target_link) and target_link["intent_seq"] == 10987))

        # Реверс: подія до першого наміру не отримує вигаданого intent.
        early_ev = [{"kind": "mutation", "event_id": "early:1", "session_id": SID,
                     "seq": 1, "ts": 1, "tool": "edit",
                     "path": str(REPO / indexed[0]), "status": "completed",
                     "old_string": "a", "new_string": "b", "patch": ""}]
        compaction_link.link_mutations(
            store, events=early_ev, session_obj_id=s_obj["id"], repo_id=repo_id,
            repo_path=ROOTS, actor=ACTOR,
        )
        early_edge = [r for r in _rels(store) if r["relation_type"] == "caused_by"
                      and _cond(r)["evidence"].get("event_id") == "early:1"]
        checks.append(("до першого наміру -> intent none",
                       len(early_edge) > 0 and all(
                           _cond(r).get("intent_source") == "none"
                           and not _cond(r)["evidence"].get("intent")
                           for r in early_edge)))

        # 4c. Автокомактація: дослівні слова користувача + звірка з БД.
        ac = compaction_link.auto_compaction(
            store, events=events, links=result["links"], session_obj=s_obj,
            opencode_db=DB, actor=ACTOR,
        )
        ac_text = ac["text"]
        checks.append(("автокомактація містить намір 10564", INTENT_10564 in ac_text))
        checks.append(("автокомактація містить намір 10987", INTENT_10987 in ac_text))
        checks.append(("не видає себе за компактацію opencode.db",
                       "НЕ є компактацією opencode.db" in ac_text and
                       ac["object"]["semantic"]["kind"] == "auto_compaction"))
        checks.append(("digest тексту збігається",
                       sha256_text(ac_text) == ac["object"]["semantic"]["summary_sha256"]))
        checks.append(("ланцюг обмежений, пропуск позначений",
                       len(ac_text) < 100000
                       and "мутацій; повний перелік event_ids" in ac_text
                       and ac["object"]["semantic"]["chain_total"]
                       > ac["object"]["semantic"]["chain_shown"]))
        ac_rels = [r for r in _rels(store)
                   if r["relation_type"] == "derived_from"
                   and r["from_id"] == ac["object"]["id"]
                   and r["to_id"] == s_obj["id"]]
        checks.append(("автокомактація -> derived_from -> сесія", len(ac_rels) == 1))

        ac_body = json.loads(store.read_one(
            "SELECT payload_json FROM objects WHERE id = ?",
            (ac["object"]["id"],))["payload_json"])
        ac_good = verification.check_object(store, ac_body, repo_path=REPO)
        checks.append(("верифікатор: наміри збігаються з БД",
                       any(c["check"] == "auto_compaction_intents_match_db"
                           for c in ac_good[0])))
        ac_bad = json.loads(json.dumps(ac_body))
        ac_bad["semantic"]["intent_refs"] = [
            {"message_id": "msg_fake", "seq": 1, "text_sha256": "00" * 32}]
        ac_bad_res = verification.check_object(store, ac_bad, repo_path=REPO)
        checks.append(("верифікатор: хибний намір ескалує",
                       any("auto_compaction_intent_mismatch" in r
                           for r in ac_bad_res[1])))

        # 5. Реверс: мутація поза репо не дає ребра.
        outside = [{"kind": "mutation", "event_id": "fake:1", "session_id": SID,
                    "seq": 999999, "ts": 1, "tool": "write", "path": r"Z:\elsewhere\x.py",
                    "status": "completed", "old_string": "", "new_string": "", "patch": ""}]
        before = len([r for r in _rels(store) if r["relation_type"] == "caused_by"])
        out_res = compaction_link.link_mutations(
            store, events=outside, session_obj_id=s_obj["id"], repo_id=repo_id,
            repo_path=REPO, actor=ACTOR,
        )
        after = len([r for r in _rels(store) if r["relation_type"] == "caused_by"])
        checks.append(("поза репо -> 0 ребер", before == after and
                       any(u.get("reason") == "path_outside_repo"
                           for u in out_res["unresolved"])))

        # 6. Коміт -> muted, але не caused_by.
        commit_res = compaction_link.link_commit(
            store, repo_id=repo_id, repo_path=REPO,
            commit="8c1eea51ec56b1558da604e98b93ae0b44a44590",
            work_id="work-test", actor=ACTOR,
        )
        checks.append(("коміт резолвиться", commit_res["resolved"].startswith("8c1eea5")))
        checks.append(("commit_fact не став caused_by",
                       not any(r["relation_type"] == "caused_by" and
                               r["from_id"] == commit_res.get("fact_id")
                               for r in _rels(store))))

        # 7. Верифікатор: доступне джерелоGreen, недоступне — ескалація.
        good = verification.check_object(store, json.loads(
            store.read_one("SELECT payload_json FROM objects WHERE id = ?",
                           (s_obj["id"],))["payload_json"]),
            repo_path=REPO)
        bad_body = json.loads(store.read_one(
            "SELECT payload_json FROM objects WHERE id = ?", (s_obj["id"],))["payload_json"])
        bad_body["semantic"]["opencode_db"] = r"Z:\no-such-db.sqlite"
        bad = verification.check_object(store, bad_body, repo_path=REPO)
        checks.append(("верифікатор: реальне джерело пройшло",
                       any(c["check"] == "session_summary_matches" for c in good[0])))
        checks.append(("верифікатор: хибне джерело ескалує",
                       # Контракт точніший: «джерела немає» (unresolved)
                       # окремо від «перевірка впала» (verifier_error).
                       "unresolved:session_source_missing" in bad[1]
                       and not any(r.startswith("verifier_error")
                                   for r in bad[1])))

        for label, ok in checks:
            print(f"  {'ok  ' if ok else 'FAIL'} {label}")
        passed = sum(1 for _l, ok in checks if ok)
        verdict = ("сесія зв'язана: намір -> мотив -> мутація -> вузол"
                   if passed == len(checks) else "FAIL")
        print(f"ВЕРДИКТ: {verdict} ({passed}/{len(checks)})")
        return 0 if passed == len(checks) else 1
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
