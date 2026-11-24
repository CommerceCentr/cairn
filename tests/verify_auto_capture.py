"""Автоматичний облік правок: ловить правку ОБОЛОНКИ, не лише інструмента.

Навіщо саме такі ворота. Виміряно на цьому проєкті: журнал сесії Claude
Code містив **0 мутацій** `cairn_unified/verification.py`, хоча коміт
`d820af9` змінив саме цей файл. Правки вносились `python`-скриптами через
оболонку, а джерело чесно оголошує `mutation = "partial:write_edit_only"`.
`tools/hook_record_edits.py` закриває цю діру через Git, і без воріт
обіцянка «усе підставляється автоматично» тихо згниє.

Реверс тут обовʼязковий: перевірка мусить ПАДАТИ, якщо хук не викликано.
Інакше вона доводила б лише те, що файл змінився.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fixture_repo import PY_FILE, make_repo  # noqa: E402

from cairn_unified import code, nodes  # noqa: E402
from cairn_unified.store import Store  # noqa: E402
import tools.hook_record_edits as hook  # noqa: E402


def changed_set(repo: Path) -> set[str]:
    return set(hook.changed_paths(repo))


def main() -> int:
    checks: list[tuple[str, bool, str]] = []
    temp = Path(tempfile.mkdtemp(prefix="cairn-auto-"))
    try:
        made = make_repo(temp / "repo",
                         files={PY_FILE: "def a():\n    return 1\n"})
        repo = Path(made["path"])
        root = temp / "store"
        Store(root).init()
        store = Store.open(root)
        ident = code.git.identity(repo)
        repo_id = ident["id"]
        store.mutate(lambda con: store.append(
            con, "repository.upserted", repo_id, ident,
            {"kind": "human", "id": "gate"}, f"repo:{repo_id}"))
        nodes.index(store, repo_id, {"kind": "system", "id": "test"},
                    paths=[PY_FILE])

        target = repo / PY_FILE

        # --- правка ОБОЛОНКОЮ: ніякого інструмента редагування ---
        subprocess.run([sys.executable, "-c",
                        "import pathlib,sys;p=pathlib.Path(sys.argv[1]);"
                        "p.write_text(p.read_text()+'\\ndef b():\\n    return 2\\n')",
                        str(target)], check=True)
        seen = changed_set(repo)
        checks.append(("Git бачить правку оболонки",
                       PY_FILE in seen, ",".join(sorted(seen))[:38]))

        report = hook.run(root, repo, "sess-auto-capture")
        checks.append(("хук зафіксував зміну", report.get("changed", 0) >= 1,
                       json.dumps(report, ensure_ascii=False)[:38]))
        checks.append(("хук звʼязав правку з вузлом", report.get("linked", 0) >= 1,
                       f"linked={report.get('linked')} {report.get('reason', '')}"[:38]))

        sessions = [r for r in store.read(
            "SELECT payload_json FROM objects WHERE object_type='session'")]
        from_hook = [r for r in sessions
                     if "hook:sess-auto-capture" in (r["payload_json"] or "")]
        checks.append(("сесія хука є у сховищі", len(from_hook) == 1,
                       f"{len(from_hook)} з {len(sessions)}"))

        # --- ідемпотентність: другий виклик без змін нічого не додає ---
        again = hook.run(root, repo, "sess-auto-capture")
        checks.append(("повторний виклик без змін -> 0",
                       again.get("changed") == 0
                       and again.get("reason") == "no_change_since_snapshot",
                       str(again.get("reason"))[:38]))

        # --- РЕВЕРС: правка БЕЗ виклику хука не потрапляє у сховище ---
        before = store.read("SELECT COUNT(*) c FROM relations")[0]["c"]
        target.write_text(target.read_text() + "\ndef c():\n    return 3\n",
                          encoding="utf-8")
        after = store.read("SELECT COUNT(*) c FROM relations")[0]["c"]
        checks.append(("реверс: без хука сховище НЕ росте", before == after,
                       f"{before} -> {after}"))
        # а з хуком — росте, тобто перевірка вище не тавтологія
        hook.run(root, repo, "sess-auto-capture")
        grown = store.read("SELECT COUNT(*) c FROM relations")[0]["c"]
        checks.append(("з хуком сховище росте", grown > after,
                       f"{after} -> {grown}"))

        # --- хук не має права ламати сесію навіть на битому сховищі ---
        exit_code = hook.main(["--root", str(temp / "no-such-store"),
                               "--repo", str(repo), "--session", "x"])
        checks.append(("помилка хука -> код 0, не падіння",
                       exit_code == 0, f"exit={exit_code}"))
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    good = 0
    for label, ok, detail in checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {label:44} | {detail[:40]}")
        good += int(ok)
    print(f"ВЕРДИКТ: автоматичний облік правок ({good}/{len(checks)})")
    return 0 if good == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
