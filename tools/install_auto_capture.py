"""Підключити автоматичний облік правок: два хуки, нуль дій від людини.

Облік не має залежати від того, чи агент скористався «правильним»
інструментом. Тому його тримають два незалежні хуки:

1. `PostToolUse` (Claude Code) -> `tools/hook_record_edits.py`
   Після кожного виклику інструмента порівнює робоче дерево зі зрізом і
   записує знайдені правки. Ловить і те, що зробила оболонка: виміряно,
   що журнал Claude Code мав **0 мутацій** `cairn_unified/verification.py`
   при комітi, який цей файл змінив.

2. `post-commit` (Git) -> `tools/sync_project_causality.py`
   Після кожного коміту вносить сам коміт і звʼязує правки з ним. Цей
   хук не залежить від агента взагалі: Git бачить зміну незалежно від
   того, хто її зробив, тому навіть ручна правка в редакторі потрапить
   в історію.

Обидва ніколи не ламають роботу: помилка -> код 0 і запис у
`.cairn/runtime/`.

    python tools/install_auto_capture.py --repo . --root .
    python tools/install_auto_capture.py --repo . --root . --check
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

MARK = "cairn-auto-capture"

POST_COMMIT = """#!/bin/sh
# {mark}: вносить коміт і звʼязує правки з ним. Не блокує коміт НІКОЛИ.
"{python}" "{tool}" --root "{root}" --repo "{repo}" \\
    >>"{root}/.cairn/runtime/post_commit.log" 2>&1 || true
exit 0
"""


def hook_entry(python: str, tool: str, root: str, repo: str) -> dict:
    command = (f'"{python}" "{tool}" --root "{root}" --repo "{repo}"')
    return {"matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
            "hooks": [{"type": "command", "command": command, "timeout": 20}]}


def install_settings(settings: Path, entry: dict, check: bool) -> str:
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    hooks = data.setdefault("hooks", {})
    bucket = hooks.setdefault("PostToolUse", [])
    mine = [item for item in bucket
            if any("hook_record_edits" in str(h.get("command", ""))
                   for h in item.get("hooks") or [])]
    if mine:
        return "вже підключено"
    if check:
        return "НЕ підключено"
    bucket.append(entry)
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return "підключено"


def install_git_hook(repo: Path, body: str, check: bool) -> str:
    hook = repo / ".git" / "hooks" / "post-commit"
    if hook.is_file() and MARK in hook.read_text(encoding="utf-8", errors="replace"):
        return "вже підключено"
    if hook.is_file():
        return "НЕ чіпаю: post-commit уже існує й не наш"
    if check:
        return "НЕ підключено"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(body, encoding="utf-8", newline="\n")
    if os.name != "nt":
        hook.chmod(hook.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return "підключено"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="install_auto_capture")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--check", action="store_true",
                        help="лише показати стан, нічого не писати")
    args = parser.parse_args(argv)

    here = Path(__file__).resolve().parents[1]
    root = args.root.resolve()
    repo = args.repo.resolve()
    python = sys.executable.replace("\\", "/")
    edits = str(here / "tools" / "hook_record_edits.py").replace("\\", "/")
    sync = str(here / "tools" / "sync_project_causality.py").replace("\\", "/")
    root_s = str(root).replace("\\", "/")
    repo_s = str(repo).replace("\\", "/")

    (root / ".cairn" / "runtime").mkdir(parents=True, exist_ok=True)
    report = {
        "PostToolUse -> hook_record_edits": install_settings(
            repo / ".claude" / "settings.json",
            hook_entry(python, edits, root_s, repo_s), args.check),
        "git post-commit -> sync_project_causality": install_git_hook(
            repo, POST_COMMIT.format(mark=MARK, python=python, tool=sync,
                                     root=root_s, repo=repo_s), args.check),
    }
    for label, state in report.items():
        print(f"  {label:44} {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
