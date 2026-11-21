r"""Регресія: витерта причина з блоку WAS компактованого досьє має потрапити в ціль.

Без цієї перевірки parser тихо зберігає лише `S`, timeline обрізає CMP до
50 символів, і питання «чому не магічні методи?» отримує 0/5 критеріїв
за 2484 BPE. Тест фіксує обидві умови: повний текст у semantic і попадання
в active context при малому бюджеті.
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

from fixture_repo import PHP_FILE, make_repo  # noqa: E402

from cairn_unified import code, context, history, nodes, semantic, work  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

# Приватного диска тут більше немає: тест стоїть у воротах CI, а виміряно,
# що без `Q:\650\home` він давав exit 1, а не пропуск.
FILE = PHP_FILE
WHOLE = f"whole:{FILE}"
H = {"kind": "human", "id": "was-human"}
S = {"kind": "system", "id": "was-sys"}
INLINE = "Явний DI для критичних сервісів — типізовані властивості, без __get()"
CLASSDOC = (
    "Критичні сервіси DI-контейнера ін'єктуються явно через конструктор у typed-властивості "
    "($session, $request, $response, $db, $router, $logger). Будь-які додаткові сервіси "
    "залишаються доступними через магічний __get(), що прозоро звертається до Registry."
)


def pack_text(with_was: bool, sha_a: str = "e492284",
              sha_b: str = "fa6edb8") -> str:
    was = (
        "  WAS <<<\n/**\n"
        f" * {CLASSDOC}\n"
        " */\n>>>\n"
    )
    was2 = f"  WAS <<<\n\t// {INLINE}\n>>>\n"
    return (
        "PACK test-was ZONE beta.loc HEAD c4d52c0d FRESH BUDGET 8000\n"
        "TASK was-rationale\n\nMAP\n"
        f"  N class:Elitty\\Engine\\Controller {FILE}:22-166 E9 P C3\n\n"
        "DOSSIER\nNODE class:Elitty\\Engine\\Controller\n"
        f" EV {sha_a} 2026-11-12 DOC BULK\n"
        "  S Improve PHPDoc linting and event dispatch\n"
        + (was if with_was else "")
        + f" EV {sha_b} 2026-07-08 DOC BULK\n"
        "  S Refactor: standardize PHPDoc comments across core\n"
        + (was2 if with_was else "")
    )


def run_variant(temp: Path, with_was: bool) -> tuple[str, list[dict], object]:
    store_dir = temp / ("yes" if with_was else "no")
    store_dir.mkdir()
    repo_path = Path(store_dir) / "repo"
    repo = make_repo(repo_path)
    sha_a, sha_b = repo["first"][:7], repo["head"][:7]
    packs = Path(store_dir) / "packs"
    packs.mkdir()
    (packs / "pack-was.txt").write_text(pack_text(with_was, sha_a, sha_b),
                                        encoding="utf-8")
    Store(store_dir).init()
    store = Store.open(store_dir)
    ident = code.git.identity(str(repo_path))
    store.mutate(lambda con: store.append(con, "repository.upserted", ident["id"],
                                          ident, H, f"repo:{ident['id']}"))
    rid = ident["id"]
    nodes.index(store, rid, S, paths=[FILE])
    sym = nodes.resolve(store, rid, FILE, address=WHOLE)[0]["stable_key"]
    wid = work.create_work(store, title="Чому не магічні методи?", actor=H,
                           scope={"repo_id": rid}, budget_limit_chars=1200)["work"]["id"]
    semantic.relate(store, from_id=wid, to_id=sym, relation="targets_symbol", actor=H)
    history.scan(store, rid, wid, sym, FILE, S)
    history.import_compactions(store, rid, wid, sym, FILE, S, packs)
    for row in store.read("SELECT id, payload_json FROM objects"):
        body = json.loads(row["payload_json"])
        sem = body.get("semantic") or {}
        if sem.get("commit") and sym in (sem.get("scope") or []):
            semantic.accept_object(store, object_id=row["id"], actor=H, rationale="was")
    pkg = context.build(store, wid, agent_id="was", purpose="review",
                        budget_chars=1200, use_cache=False)
    return ("\n".join(i["text"] for i in pkg["items"]), pkg["items"], store,
            (sha_a, sha_b))


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="verify-was-"))
    try:
        text_yes, items_yes, store_yes, shas = run_variant(temp, True)
        text_no, _, _, _ = run_variant(temp, False)
        parsed = history._parse_pack(pack_text(True, *shas))
        entries = parsed["dossiers"]["class:Elitty\\Engine\\Controller"]
        by_sha = {e["sha"]: e for e in entries}
        sha_a, sha_b = shas
        # Друге твердження liшається в сховищі (доступне при більшому бюджеті):
        # релевантна селекція віддає повну цільову відповідь, а не весь WAS.
        stored_inline = any(
            INLINE in json.dumps(json.loads(row["payload_json"]).get("semantic") or {},
                                 ensure_ascii=False)
            for row in store_yes.read("SELECT payload_json FROM objects")
        )
        checks = [
            ("WAS розібраний для першого коміту", CLASSDOC in by_sha[sha_a]["rationale"]),
            ("WAS розібраний для другого коміту", INLINE in by_sha[sha_b]["rationale"]),
            ("classdoc у контексті", CLASSDOC in text_yes),
            ("inline у сховищі", stored_inline),
            ("без WAS причини немає", CLASSDOC not in text_no),
            ("timeline присутній", any(i["kind"] == "history_timeline" for i in items_yes)),
        ]
        for label, ok in checks:
            print(f"  {'ok  ' if ok else 'FAIL'} {label}")
        good = all(ok for _label, ok in checks)
        print(f"ВЕРДИКТ: {'WAS-rationale hits target' if good else 'FAIL'}")
        return 0 if good else 1
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
