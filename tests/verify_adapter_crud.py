"""Адаптер віддає шляхи ТІЛЬКИ для CrUD-інструментів.

Реверс тут явний: старий фільтр «є ключ file_path/path» дав би 5 подій
із шляхами, новий — 2. Без_this числа зміну коду не відрізнити від
випадкової правки. Жоден реальний журнал не читається — fixture у temp.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified.adapters import claude_code  # noqa: E402

PATHS = {
    "Write": str(ROOT / "src" / "written.py"),
    "Edit": str(ROOT / "src" / "edited.py"),
    "Read": str(ROOT / "src" / "read_only.py"),
    "Grep": str(ROOT / "src"),
    "Glob": str(ROOT / "src"),
}


def _tool(name: str, key: str, value: str, seq: int) -> dict:
    return {
        "type": "assistant",
        "uuid": f"uuid-{seq}",
        "timestamp": f"2026-11-20T10:{seq:02d}:00Z",
        "sessionId": "ses-fixture",
        "cwd": str(ROOT),
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"tu-{seq}", "name": name,
             "input": {key: value}},
        ]},
    }


def _fixture(path: Path) -> None:
    rows: list[dict] = []
    seq = 0
    for name, key in (("Write", "file_path"), ("Edit", "file_path"),
                      ("Read", "file_path"), ("Grep", "path"), ("Glob", "path")):
        seq += 1
        rows.append(_tool(name, key, PATHS[name], seq))
    seq += 1
    rows.append({
        "type": "assistant", "uuid": f"uuid-{seq}",
        "timestamp": f"2026-11-20T10:{seq:02d}:00Z", "sessionId": "ses-fixture",
        "cwd": str(ROOT),
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"tu-{seq}", "name": "Bash",
             "input": {"command": "git commit -m x"}},
        ]},
    })
    seq += 1
    rows.append({
        "type": "assistant", "uuid": f"uuid-{seq}",
        "timestamp": f"2026-11-20T10:{seq:02d}:00Z", "sessionId": "ses-fixture",
        "cwd": str(ROOT),
        "message": {"role": "assistant", "content": [
            {"type": "text", "text": "згадка read_only.py без мутації"},
        ]},
    })
    seq += 1
    rows.append({
        "type": "user", "uuid": f"uuid-{seq}",
        "timestamp": f"2026-11-20T10:{seq:02d}:00Z", "sessionId": "ses-fixture",
        "cwd": str(ROOT),
        "message": {"role": "user", "content": [
            {"type": "text", "text": "зміни verification.py: чому лежить між повідомленнями"},
        ]},
    })
    seq += 1
    rows.append({
        "type": "user", "uuid": f"uuid-{seq}",
        "timestamp": f"2026-11-20T10:{seq:02d}:00Z", "sessionId": "ses-fixture",
        "cwd": str(ROOT), "isCompactSummary": True,
        "message": {"role": "user", "content": [
            {"type": "text", "text": "стиснутий контекст: core/Engine/Controller.php"},
        ]},
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


def _old_filter_hits(path: Path) -> int:
    """Реверс: скільки подій старий адаптер визнав би «дотиками»."""
    count = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(raw)
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for blk in content:
            if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                continue
            inp = blk.get("input") or {}
            if any(k in inp for k in claude_code.PATH_KEYS):
                count += 1
    return count


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="adapter-crud-"))
    try:
        journal = temp / "logs" / "session.jsonl"
        _fixture(journal)
        events = list(claude_code.read(str(journal)))
        with_paths = [e for e in events if e["abs_paths"]]
        old_hits = _old_filter_hits(journal)
        replay = list(claude_code.read(str(journal)))

        mutated = {e["abs_paths"][0]: e.get("operations")
                   for e in with_paths if len(e["abs_paths"]) == 1}
        checks = [
            ("реверс: старий фільтр давав 5", old_hits == 5),
            ("новий фільтр дає 2 події зі шляхами", len(with_paths) == 2),
            ("Write збережено", PATHS["Write"] in mutated),
            ("Edit збережено", PATHS["Edit"] in mutated),
            ("Read відкинуто", PATHS["Read"] not in mutated),
            ("Grep відкинуто", PATHS["Grep"] not in mutated),
            ("Glob відкинуто", PATHS["Glob"] not in mutated),
            ("Bash не вгадується", not any("git commit" in str(e.get("operations"))
                                          for e in events)),
            ("операції позначено", all(mutated.get(PATHS[k]) == [k]
                                       for k in ("Write", "Edit"))),
            ("компактація без шляхів", [e for e in events
                                       if e["kind"] == "compact_summary"][0]["abs_paths"] == []),
            # Проза асистента ТЕПЕР віддається (виміряно: 125 блоків на
            # 28 435 символів викидались мовчки, і «мотив» у причинному
            # ланцюзі завжди був порожній). Інваріант, який справді
            # стерегли, лишається: проза не несе шляхів і не стає CrUD.
            ("проза асистента не несе шляхів", all(
                e.get("abs_paths") == [] for e in events
                if e["kind"] == "message" and e["actor"] == "agent")),
            ("проза асистента не стає мутацією", all(
                e["actor"] != "agent" for e in events if e["kind"] == "tool_call")),
            ("проза асистента доступна як окрема подія", any(
                e.get("text") for e in events
                if e["kind"] == "message" and e["actor"] == "agent")),
            ("user-намір є, але без шляхів", any(
                e["kind"] == "message" and e["actor"] == "user"
                and "verification.py" in e.get("text", "")
                and e["abs_paths"] == [] for e in events)),
            ("повтор детермінований", events == replay),
        ]
        for label, ok in checks:
            print(f"  {'ok  ' if ok else 'FAIL'} {label}")
        good = all(ok for _label, ok in checks)
        verdict = "адаптер зв'язує лише CrUD" if good else "FAIL"
        print(f"ВЕРДИКТ: {verdict} "
              f"(старий={old_hits}, новий={len(with_paths)})")
        return 0 if good else 1
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
