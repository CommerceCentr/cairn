"""Збирач: сирі записи Claude Code -> канонічна форма конвеєра.

Фікстура — тимчасовий `.jsonl` у форматі журналу Claude Code. Жодного
справжнього журналу й жодного приватного шляху.

Реверси, без яких тест нічого не доводить:

* порядок — за ЧАСОМ, а не за рядком у файлі (запис із раннім штампом,
  записаний пізніше, мусить стати раніше);
* два `tool_use` в одному повідомленні дають ДВІ мутації, не одну;
* не-мутуючий інструмент (`Read`, `Bash`) мутацією НЕ стає — саме через
  це журнал не бачить правок, зроблених через shell;
* порожнє повідомлення користувача не стає наміром;
* `isCompactSummary` стає компактацією з текстом.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified.adapters import claude_code, normalize  # noqa: E402

SID = "sess-fixture"


def record(uuid: str, ts: str, rtype: str, *, text: str = "",
           tools: list[tuple[str, str]] | None = None,
           compact: bool = False) -> dict:
    content: list[dict] = []
    for name, path in tools or []:
        content.append({"type": "tool_use", "name": name, "input": {"file_path": path}})
    if text and not content:
        content.append({"type": "text", "text": text})
    body = {
        "uuid": uuid, "timestamp": ts, "sessionId": SID, "cwd": "/repo",
        "type": rtype, "message": {"role": rtype, "content": content},
    }
    if compact:
        body["isCompactSummary"] = True
        body["message"]["content"] = [{"type": "text", "text": text}]
    return body


def write_journal(path: Path) -> None:
    rows = [
        # Навмисно НЕ в порядку часу: 10:00:05 записано перед 10:00:01.
        record("u2", "2026-11-21T10:00:05Z", "user", text="другий намір"),
        record("u1", "2026-11-21T10:00:01Z", "user", text="перший намір"),
        record("t1", "2026-11-21T10:00:10Z", "assistant",
               tools=[("Write", "/repo/a.py"), ("Edit", "/repo/b.py")]),
        record("t2", "2026-11-21T10:00:11Z", "assistant",
               tools=[("Read", "/repo/c.py")]),
        record("t3", "2026-11-21T10:00:12Z", "assistant",
               tools=[("Bash", "/repo/d.py")]),
        record("u3", "2026-11-21T10:00:13Z", "user", text="   "),
        record("c1", "2026-11-21T10:00:20Z", "assistant",
               text="стислий підсумок сесії", compact=True),
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    temp = Path(tempfile.mkdtemp(prefix="normalize-"))
    try:
        journal = temp / "fixture.jsonl"
        write_journal(journal)
        events = normalize.canonical_events(claude_code.read(str(journal)))
        by_kind: dict[str, list[dict]] = {}
        for item in events:
            by_kind.setdefault(item["kind"], []).append(item)

        stamps = [item["ts"] for item in events]
        checks.append(("порядок за часом, не за рядком", stamps == sorted(stamps),
                       f"{stamps[0][-10:]} … {stamps[-1][-10:]}"))

        intents = by_kind.get("user_intent", [])
        checks.append(("два наміри, порожній відкинуто", len(intents) == 2,
                       str([i["text"] for i in intents])))
        checks.append(("перший за часом намір має менший seq",
                       intents[0]["text"] == "перший намір"
                       and intents[0]["seq"] < intents[1]["seq"],
                       f"{intents[0]['seq']} < {intents[1]['seq']}"))

        muts = by_kind.get("mutation", [])
        checks.append(("два tool_use -> дві мутації", len(muts) == 2, str(len(muts))))
        checks.append(("event_id різні для двох правок",
                       len({m["event_id"] for m in muts}) == 2,
                       str(sorted(m["event_id"] for m in muts))))
        checks.append(("інструменти збережені",
                       sorted(m["tool"] for m in muts) == ["Edit", "Write"],
                       str(sorted(m["tool"] for m in muts))))
        checks.append(("усі мутації completed",
                       all(m["status"] == "completed" for m in muts), "так"))

        paths = {m["path"] for m in muts}
        checks.append(("Read не став мутацією", "/repo/c.py" not in paths, "так"))
        checks.append(("Bash не став мутацією (межа джерела)",
                       "/repo/d.py" not in paths, "так"))

        comps = by_kind.get("compaction", [])
        checks.append(("компактація з текстом", len(comps) == 1
                       and comps[0]["summary"] == "стислий підсумок сесії",
                       str(len(comps))))
        checks.append(("компактація має message_id",
                       bool(comps and comps[0]["message_id"]), "так"))

        # Реверс: конвеєр справді споживає цю форму.
        from cairn_unified.compaction_link import mutation_readiness
        from cairn_unified.store import Store

        store_dir = temp / "store"
        Store(store_dir).init()
        store = Store.open(store_dir)
        ready, unresolved = mutation_readiness(store, events, "repo-x", ("/repo",))
        checks.append(("конвеєр приймає форму: немає вузлів -> усі unresolved",
                       not ready and len(unresolved) == 2,
                       f"ready={len(ready)} unresolved={len(unresolved)}"))
        checks.append(("причина названа node_not_indexed",
                       all(u["reason"] == "node_not_indexed" for u in unresolved),
                       str({u["reason"] for u in unresolved})))
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    good = 0
    for label, ok, detail in checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {label:48} | {detail[:34]}")
        good += int(ok)
    print(f"ВЕРДИКТ: збирач Claude Code -> канонічна форма ({good}/{len(checks)})")
    return 0 if good == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
