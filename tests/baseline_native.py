"""Нативний шлях агента до відповіді про вузол: ціна сировини.

Відтворює рівно ті пʼять кроків, що названі в §11 `docs/MEASUREMENTS.md`:
рядковий скан журналу + `git log -1` + `git show --name-status` +
`git log -p -12` + `git blame`. Нічого не змінює: усі команди читальні.

    python tests/baseline_native.py tests/run_all.py --commit 942cb526 \
        --repo <REPO> --root <STORE_ROOT>

МЕЖА: це ціна СИРОВИНИ, не ціна рівносильної відповіді. Нативний шлях тут
не намагається відтворити причину — він лише дає матеріал, з якого людина
чи агент її відтворювали б. Порівнювати з Cairn-шляхом можна лише назвавши
цю межу (див. `tests/compare_why.py`).
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


def ledger_scan(store_root: str, needle: str) -> str:
    """Те, що робить агент без Cairn: лінійний скан журналу за підрядком.

    Підрядок не розрізняє код від коментаря і не має напрямку ребра — саме
    тому це сировина. Якщо журналу немає, повертає порожньо, і вимір це
    покаже як 0 байт, а не як успіх.
    """
    path = Path(store_root) / ".cairn" / "events" / "ledger.jsonl"
    if not path.exists():
        return ""
    hits = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if needle in line:
                hits.append(line)
    return "".join(hits)


def collect(*, repo: str, store_root: str, path: str, commit: str) -> dict:
    needle = Path(path).name
    steps: list[tuple[str, str, float]] = []

    text, ms = timed(ledger_scan, store_root, needle)
    steps.append((f"ledger scan .cairn ~ {needle}", text, ms))
    text, ms = timed(git, repo, "log", "-1", commit)
    steps.append((f"git log -1 {commit[:7]}", text, ms))
    text, ms = timed(git, repo, "show", "--name-status", "--format=%H%n%cI%n%s%n%b", commit)
    steps.append((f"git show --name-status {commit[:7]}", text, ms))
    text, ms = timed(git, repo, "log", "-p", "-12", "--", path)
    steps.append((f"git log -p -12 -- {path}", text, ms))
    text, ms = timed(git, repo, "blame", "--", path)
    steps.append((f"git blame -- {path}", text, ms))

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
        "boundary": "ціна сировини, не ціна рівносильної відповіді",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baseline_native")
    parser.add_argument("path")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--root", default=str(ROOT), help="корінь проєкту зі сховищем .cairn")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    report = collect(repo=args.repo, store_root=args.root, path=args.path, commit=args.commit)
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["total"]["bytes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
