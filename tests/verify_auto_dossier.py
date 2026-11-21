"""Кінець-в-кінець: згенероване досьє → авто-верифікація → контекст без人的гейту."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from cairn_unified import code, context, history, nodes, semantic, work  # noqa: E402
from cairn_unified.store import Store  # noqa: E402
# Цей тест НЕ переносний, і це названо прямо, а не приховано.
# Він потребує `full_measurement_suite` (приватний, залежить від `tokenizers`
# і кешу HuggingFace) та зон на приватному диску. У публічному репозиторії
# модуля немає — раніше це давало ModuleNotFoundError, і оскільки тест не
# стояв у воротах, падіння було беззвучним. Тепер він пропускається з
# НАЗВАНОЮ причиною: пропуск, про який сказано, кращий за падіння, якого
# ніхто не бачить, і кращий за «зелено», якого не заслужили.
try:
    from full_measurement_suite import TOK, measure, norm  # noqa: E402
except ModuleNotFoundError as exc:                          # noqa: F401
    print(f"SKIP verify_auto_dossier: немає {exc.name} "
          "(приватний вимірювальний набір)")
    raise SystemExit(0) from None

REPO = Path(os.environ.get("CAIRN_DOSSIER_REPO", r"Q:\650\home\beta.loc"))
FILE = "core/Engine/Controller.php"
WHOLE = f"whole:{FILE}"
PACKS = Path(os.environ.get("CAIRN_DOSSIER_PACKS",
                           r"Q:\650\home\_archivist\packs_python"))

if not REPO.exists() or not PACKS.exists():
    print(f"SKIP verify_auto_dossier: немає {REPO} або {PACKS} "
          "(задати CAIRN_DOSSIER_REPO / CAIRN_DOSSIER_PACKS)")
    raise SystemExit(0)
H = {"kind": "human", "id": "observer"}
S = {"kind": "system", "id": "auto-indexer"}
CLASSDOC = (
    "Критичні сервіси DI-контейнера ін'єктуються явно через конструктор у typed-властивості "
    "($session, $request, $response, $db, $router, $logger). Будь-які додаткові сервіси "
    "залишаються доступними через магічний __get(), що прозоро звертається до Registry."
)
INLINE = "Явний DI для критичних сервісів — типізовані властивості, без __get()"


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="autodossier-"))
    try:
        Store(temp).init()
        store = Store.open(temp)
        ident = code.git.identity(str(REPO))
        store.mutate(lambda con: store.append(con, "repository.upserted", ident["id"],
                                              ident, H, f"repo:{ident['id']}"))
        rid = ident["id"]
        nodes.index(store, rid, S, paths=[FILE])
        sym = nodes.resolve(store, rid, FILE, address=WHOLE)[0]["stable_key"]
        wid = work.create_work(store, title="Чому не магічні методи?", actor=H,
                               scope={"repo_id": rid}, budget_limit_chars=1200)["work"]["id"]
        semantic.relate(store, from_id=wid, to_id=sym, relation="targets_symbol", actor=H)

        t0 = time.perf_counter()
        history.scan(store, rid, wid, sym, FILE, S)
        scan_ms = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        imp = history.import_compactions(store, rid, wid, sym, FILE, S, PACKS,
                                         auto_verify=True, repo_path=REPO)
        imp_ms = (time.perf_counter() - t0) * 1000

        report = store.read(
            "SELECT event_type, payload_json FROM events "
            "WHERE event_type IN ('object.verified','verification.escalated')"
        )
        for row in report:
            if row["event_type"] == "verification.escalated":
                print("ESCALATION:", json.loads(row["payload_json"]).get("reasons"))
        verified = sum(1 for r in report if r["event_type"] == "object.verified")
        escalated = sum(1 for r in report if r["event_type"] == "verification.escalated")
        human_events = store.read(
            "SELECT COUNT(*) n FROM events WHERE event_type = 'assertion.recorded' "
            "AND payload_json LIKE '%authority.accepted_by_human%'"
        )
        trusted = store.read(
            "SELECT COUNT(*) n FROM objects WHERE trust_class = 'verified_system'"
        )[0]["n"]

        pkg = context.build(store, wid, agent_id="observer-agent", purpose="review",
                            budget_chars=1200, use_cache=False)
        text = "\n".join(i["text"] for i in pkg["items"])

        print(f"scan {scan_ms:.0f} мс | імпорт+верифікація {imp_ms:.0f} мс | "
              f"об'єктів {imp['objects']}")
        print(f"verified_event={verified} escalated_event={escalated} "
              f"trusted_objects={trusted} human_events={human_events[0]['n']}")
        print(f"контекст: {measure(text)}")
        print(f"classdoc у цілі: {norm(CLASSDOC) in norm(text)} | "
              f"inline у цілі: {norm(INLINE) in norm(text)}")
        for item in pkg["items"]:
            if item["kind"] == "history_timeline":
                print("--- TIMELINE ---")
                print(item["text"])
        ok = (verified >= 1 and escalated == 0 and trusted >= imp["objects"]
              and human_events[0]["n"] == 0 and norm(CLASSDOC) in norm(text))
        print(f"ВЕРДИКТ: {'авто-досьє попадає в ціль без людини' if ok else 'FAIL'}")
        return 0 if ok else 1
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
