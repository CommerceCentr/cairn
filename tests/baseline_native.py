"""Нативний шлях агента до відповіді про вузол: ціна сировини.

Відтворює рівно ті пʼять кроків, що названі в §11 `docs/MEASUREMENTS.md`:
рядковий скан журналу + `git log -1` + `git show --name-status` +
`git log -p -12` + `git blame`. Нічого не змінює: усі команди читальні.

    python tests/baseline_native.py tests/run_all.py --commit 942cb526 \
        --repo <REPO> --root <STORE_ROOT>

ВИПРАВЛЕНО 2026-11-24. Раніше сюди входив лінійний скан
`.cairn/events/ledger.jsonl`, і він давав 90% усієї «нативної» ціни
(204 070 токенів із 224 529 на одному вузлі). Це була тавтологія: журнал
Cairn існує ЛИШЕ тоді, коли Cairn уже є, тож його не можна рахувати
ціною роботи БЕЗ Cairn. Тепер нативний шлях — файл, історія git і blame.

Сирий журнал САМОГО АГЕНТА (`~/.claude/projects/*.jsonl`) існує без Cairn,
тому його скан — законний нативний крок. Він рахується окремим рядком і
вмикається прапорцем `--journal`, бо це інша стратегія з іншою ціною.

МЕЖА: це ціна СИРОВИНИ, не ціна рівносильної відповіді. Нативний шлях тут
не намагається відтворити причину — він лише дає матеріал, з якого людина
чи агент її відтворювали б.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from measure import ZERO, add, measure, timed  # noqa: E402


def git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "safe.directory=*", "-c", "core.pager=cat", "-C", repo, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout


def journal_scan(needle: str) -> str:
    """Скан СИРОГО журналу агента — єдиний скан, законний без Cairn.

    Журнали Claude Code лежать у `~/.claude/projects/` незалежно від того,
    чи встановлений Cairn, тому шукати в них — чесна нативна стратегія.
    Скан `.cairn/events/ledger.jsonl` чесною НЕ є: цей файл створює Cairn.
    """
    try:
        from cairn_unified.adapters import claude_code
    except ImportError:
        return ""
    hits: list[str] = []
    for path in claude_code.discover():
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                hits.extend(line for line in handle if needle in line)
        except OSError:
            continue
    return "".join(hits)


def collect(*, repo: str, store_root: str, path: str, commit: str,
            with_journal: bool = False) -> dict:
    needle = Path(path).name
    steps: list[tuple[str, str, float]] = []

    text, ms = timed(lambda: (Path(repo) / path).read_text(encoding="utf-8", errors="replace")
                     if (Path(repo) / path).is_file() else "")
    steps.append((f"читання файла {path}", text, ms))
    text, ms = timed(git, repo, "log", "-1", commit)
    steps.append((f"git log -1 {commit[:7]}", text, ms))
    text, ms = timed(git, repo, "show", "--name-status", "--format=%H%n%cI%n%s%n%b", commit)
    steps.append((f"git show --name-status {commit[:7]}", text, ms))
    text, ms = timed(git, repo, "log", "-p", "-12", "--", path)
    steps.append((f"git log -p -12 -- {path}", text, ms))
    text, ms = timed(git, repo, "blame", "--", path)
    steps.append((f"git blame -- {path}", text, ms))
    if with_journal:
        text, ms = timed(journal_scan, needle)
        steps.append((f"скан сирих журналів агента ~ {needle}", text, ms))

    total = dict(ZERO)
    total_ms = 0.0
    rows = []
    for label, text, ms in steps:
        one = measure(text)
        rows.append({"step": label, **one, "ms": round(ms, 1)})
        total = add(total, one)
        total_ms += ms
    return {
        "path": "native",
        "question": f"коли і чому редагувався {path}, з комітом {commit[:7]}",
        "steps": rows,
        "total": {**total, "ms": round(total_ms, 1)},
        "boundary": "ціна сировини, не ціна рівносильної відповіді; скан журналу Cairn сюди НЕ входить — він існує лише разом із Cairn",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baseline_native")
    parser.add_argument("path")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--root", default=str(ROOT), help="корінь проєкту зі сховищем .cairn")
    parser.add_argument("--journal", action="store_true",
                        help="додати скан сирих журналів агента (інша стратегія)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    report = collect(repo=args.repo, store_root=args.root, path=args.path,
                     commit=args.commit, with_journal=args.journal)
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["total"]["bytes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
