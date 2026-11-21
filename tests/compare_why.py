"""Два шляхи до однієї відповіді, виміряні одним лічильником в одному прогоні.

    python tests/compare_why.py tests/run_all.py --commit 942cb526 \
        --repo Q:/prj/cairn --root Q:/prj/cairn-private

Друкує дві колонки — native і cairn — і відношення між ними. Відношення
подаються з НАЗВАНОЮ межею, бо їх дві й вони різні:

  * «сировина проти готової відповіді» — нативний шлях проти одного
    `why`-виклику;
  * «сировина проти всього шляху Cairn» — нативний шлях проти суми всіх
    кроків Cairn, включно з provenance-обгортками.

Жодне з двох не згортається в одне число. Жодне з них НЕ порівнює з
рівносильною відповіддю `grep`: нативний шлях тут не намагався відтворити
причину. Лічильник токенів — `chars ÷ 4`, проксі (див. `tests/measure.py`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import baseline_native  # noqa: E402
from measure import ZERO, add, measure, ratio, timed  # noqa: E402


def cairn_side(*, store_root: str, repo: str, path: str, commit: str,
               budget: int | None) -> dict:
    """Шлях Cairn. Якщо `why` ще немає — це звіт про відсутність, не нуль."""
    from cairn_unified.store import Store

    store = Store.open(store_root)
    rows: list[dict] = []
    answer_row: dict | None = None

    try:
        from cairn_unified import why as why_mod
    except ImportError:
        why_mod = None

    if why_mod is not None:
        from cairn_unified import code

        repo_id = code.git.identity(repo)["id"]
        out, ms = timed(why_mod.answer, store, repo_id=repo_id, path=path,
                        commit=commit, budget_chars=budget)
        text = json.dumps(out, ensure_ascii=False)
        answer_row = {"step": "cairn why (підсумкова відповідь)",
                      **measure(text), "ms": round(ms, 1)}
        rows.append(answer_row)
    else:
        rows.append({"step": "cairn why — КОМАНДИ НЕМАЄ", **dict(ZERO),
                     "sha256": "", "ms": 0.0, "missing": True})

    from cairn_unified import mcp

    out, ms = timed(mcp.call_tool, store, "cairn_semantic_query",
                    {"text": Path(path).stem, "limit": 20})
    rows.append({"step": "mcp cairn_semantic_query",
                 **measure(json.dumps(out, ensure_ascii=False)), "ms": round(ms, 1)})

    from cairn_unified import code as code_mod

    repo_id = code_mod.git.identity(repo)["id"]
    out, ms = timed(mcp.call_tool, store, "cairn_code_explore",
                    {"symbol": Path(path).stem, "repo_id": repo_id})
    rows.append({"step": "mcp cairn_code_explore",
                 **measure(json.dumps(out, ensure_ascii=False)), "ms": round(ms, 1)})

    total = dict(ZERO)
    total_ms = 0.0
    for row in rows:
        total = add(total, row)
        total_ms += row["ms"]
    return {
        "path": "cairn",
        "steps": rows,
        "answer": answer_row,
        "total": {**total, "ms": round(total_ms, 1)},
    }


def render(native: dict, cairn: dict) -> str:
    lines = [f"ПИТАННЯ  {native['question']}", ""]
    lines.append(f"{'крок':52} {'байти':>10} {'токени≈':>9} {'мс':>8}")
    lines.append("-" * 82)
    for row in native["steps"] + cairn["steps"]:
        mark = "!" if row.get("missing") else " "
        lines.append(f"{mark}{row['step'][:51]:51} {row['bytes']:>10} "
                     f"{row['tokens_est']:>9} {row['ms']:>8}")
    lines.append("-" * 82)
    for name, side in (("native (сировина)", native), ("cairn (усі кроки)", cairn)):
        tot = side["total"]
        lines.append(f"{name:52} {tot['bytes']:>10} {tot['tokens_est']:>9} {tot['ms']:>8}")

    lines.append("")
    answer = cairn.get("answer")
    if answer:
        lines.append("МЕЖА 1 — сировина проти готової відповіді:")
        lines.append(f"  токени ×{ratio(native['total'], answer, 'tokens_est')} · "
                     f"байти ×{ratio(native['total'], answer, 'bytes')} · "
                     f"час ×{ratio(native['total'], answer, 'ms')}")
    else:
        lines.append("МЕЖА 1 — недоступна: підсумкової відповіді `why` ще немає.")
    lines.append("МЕЖА 2 — сировина проти всього шляху Cairn:")
    lines.append(f"  токени ×{ratio(native['total'], cairn['total'], 'tokens_est')} · "
                 f"байти ×{ratio(native['total'], cairn['total'], 'bytes')} · "
                 f"час ×{ratio(native['total'], cairn['total'], 'ms')}")
    lines.append("")
    lines.append("Межі: це дві РІЗНІ межі, не одне число. Нативний шлях не пробував")
    lines.append("відтворити причину, тому його ціна — ціна сировини. Токени — chars÷4.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="compare_why")
    parser.add_argument("path")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--budget", type=int, default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    native = baseline_native.collect(repo=args.repo, store_root=args.root,
                                     path=args.path, commit=args.commit)
    cairn = cairn_side(store_root=args.root, repo=args.repo, path=args.path,
                       commit=args.commit, budget=args.budget)
    print(render(native, cairn))

    if args.out:
        report = {"question": native["question"], "target": {"path": args.path,
                  "commit": args.commit, "repo": args.repo, "store": args.root},
                  "native": native, "cairn": cairn,
                  "counter": "chars/4 proxy, див. tests/measure.py"}
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                                  encoding="utf-8")
        print(f"\nзаписано: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
