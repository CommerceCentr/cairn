"""Авто-гейт: валідна пропозиція стає verified_system, хибна — 'не встановлено'."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT / "tests"))

from fixture_repo import PHP_FILE, make_packs, make_repo  # noqa: E402

from cairn_unified import code, nodes, semantic, verification  # noqa: E402
from cairn_unified.canon import sha256_text  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

# Раніше тут стояв приватний диск автора, і цей тест був у воротах CI.
# Виміряно: без того диска він давав exit 1, тож ворота на чужій машині
# зеленими бути не могли. Тепер усе створюється в temp.
FILE = PHP_FILE
WHOLE = f"whole:{FILE}"
PACK_NAME = "pack-fixture.txt"
PACK_TEXT = "PACK fixture ZONE fixture HEAD 0000000 FRESH BUDGET 8000\nTASK auto-gate\n"
ACTOR = {"kind": "system", "id": "gate-test"}


def make_object(store, *, key: str, commit: str, summary: str, pack: str = "",
                digest: str = "") -> str:
    sem = {"kind": "compaction", "commit": commit, "scope": [WHOLE],
           "node_address": WHOLE, "pack": pack, "pack_digest": digest}
    return semantic.put_object(
        store, object_type="decision", title=key, summary=summary, actor=ACTOR,
        lifecycle="proposed", trust="agent_proposed", stable_key=key,
        semantic=sem, constraints=[f"commit:{commit[:7]}"], scope=[WHOLE],
        idempotency_key=f"gate-test:{key}",
    )["object"]["id"]


def count_events(store, kind: str) -> int:
    return len(store.read("SELECT event_id FROM events WHERE event_type = ?", (kind,)))


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="autogate-"))
    try:
        repo = make_repo(temp / "repo")
        REPO = Path(repo["path"])
        PACKS = make_packs(temp / "packs", {PACK_NAME: PACK_TEXT})
        Store(temp / "store").init()
        store = Store.open(temp / "store")
        ident = code.git.identity(str(REPO))
        store.mutate(lambda con: store.append(con, "repository.upserted", ident["id"],
                                              ident, ACTOR, f"repo:{ident['id']}"))
        nodes.index(store, ident["id"], ACTOR, paths=[FILE])

        pack_name = PACK_NAME
        real_commit = repo["head"]
        good_digest = sha256_text((PACKS / pack_name).read_text(encoding="utf-8"))

        bad = make_object(store, key="gate-bad", commit="0" * 40,
                          summary="не встановлено", pack="no-such-pack.txt", digest="0" * 64)
        good = make_object(store, key="gate-good", commit=real_commit,
                           summary="перевірене твердження", pack=pack_name,
                           digest=good_digest)

        bad_result = verification.verify_object(store, bad, repo_path=REPO, packs_dir=PACKS)
        good_result = verification.verify_object(store, good, repo_path=REPO, packs_dir=PACKS)
        first_events = (count_events(store, "object.verified"),
                        count_events(store, "verification.escalated"))
        replay = verification.verify_object(store, good, repo_path=REPO, packs_dir=PACKS)
        second_events = (count_events(store, "object.verified"),
                         count_events(store, "verification.escalated"))

        bad_body = store.read_one("SELECT payload_json FROM objects WHERE id = ?", (bad,))
        bad_trust = __import__("json").loads(bad_body["payload_json"])["trust_class"]
        good_body = store.read_one("SELECT payload_json FROM objects WHERE id = ?", (good,))
        good_trust = __import__("json").loads(good_body["payload_json"])["trust_class"]

        checks = [
            ("хибний об'єкт не прийнято", bad_result["verified"] is False),
            ("причини названо", any(r.startswith("unresolved:") for r in bad_result["reasons"])),
            ("хибний лишається agent_proposed", bad_trust == "agent_proposed"),
            ("ескалація записана", first_events[1] == 1),
            ("валідний прийнято", good_result["verified"] is True),
            ("валідний став verified_system", good_trust == "verified_system"),
            ("подія верифікації записана", first_events[0] == 1),
            ("повтор без нових подій", first_events == second_events and replay["replayed"]),
        ]
        for label, ok in checks:
            print(f"  {'ok  ' if ok else 'FAIL'} {label}")
        good_all = all(ok for _label, ok in checks)
        print(f"ВЕРДИКТ: {'auto-gate ескалує й верифікує детерміновано' if good_all else 'FAIL'}")
        return 0 if good_all else 1
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
