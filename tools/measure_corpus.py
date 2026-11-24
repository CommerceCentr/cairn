"""Емпіричні числа на повному сховищі: розподіл, а не одне зручне число.

    python tools/measure_corpus.py --root ./store --sample 40

Бере випадкову (із фіксованим зерном) вибірку вузлів, які МАЮТЬ історію,
і для кожного міряє обидва шляхи одним лічильником. Друкує медіану,
мінімум і максимум, а не середнє: один гігантський файл зміщує середнє
так, що воно перестає щось означати.

Чому вибірка, а не всі вузли: нативний шлях на кожен вузол читає
`git log -p` і `git blame`, і на десятках тисяч вузлів це години. Зерно
фіксоване, тож прогін відтворюваний.

Межа та сама, що в `compare_why.py`: нативна колонка — ціна СИРОВИНИ, не
ціна рівносильної відповіді.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from measure import measure, timed  # noqa: E402

from cairn_unified import why as why_mod  # noqa: E402
from cairn_unified.errors import CairnError  # noqa: E402
from cairn_unified.store import Store  # noqa: E402


def git(repo: str, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "safe.directory=*", "-c", "core.pager=cat", "-C", repo, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.stdout if done.returncode == 0 else ""


def candidates(store: Store, level: str = "caused_by") -> list[dict]:
    """Вузли, у яких Є історія: інакше міряли б порожнечу.

    Два рівні, і їх не можна плутати (мандат §7.3):

    * `caused_by` — участь сесії в часі й шляху;
    * `mutation_in_commit` — унікальний текстовий перехід у коміті,
      єдиний рівень, який проєкт вважає ДОПУСТИМИМ.

    Вимір на `caused_by` відповідає на «де взагалі є історія», вимір на
    `mutation_in_commit` — на «де історія доведена». Це різні питання й
    різні числа.
    """
    if level == "caused_by":
        return store.read(
            "SELECT DISTINCT s.repo_id, s.path, s.stable_key, r2.path AS repo_path "
            "FROM code_symbols s "
            "JOIN relations rel ON rel.from_id = s.stable_key "
            "     AND rel.relation_type = 'caused_by' "
            "JOIN repositories r2 ON r2.id = s.repo_id "
            "WHERE s.qualified_name LIKE 'whole:%' "
            "ORDER BY s.stable_key")
    # Допустимий рівень живе НЕ на ребрі вузла: `mutation_in_commit` йде
    # від СЕСІЇ до факту коміту, а вузол названий в умові ребра
    # (`condition.node_key`). Перший варіант цього запиту шукав вузол на
    # кінцях ребра й повертав нуль при 180 наявних ребрах.
    keys: set[str] = set()
    for row in store.read(
            "SELECT condition_json FROM relations "
            "WHERE relation_type = 'mutation_in_commit' AND condition_json IS NOT NULL"):
        try:
            key = json.loads(row["condition_json"]).get("node_key")
        except (TypeError, ValueError):
            continue
        if key:
            keys.add(str(key))
    if not keys:
        return []
    marks = ",".join("?" * len(keys))
    return store.read(
        "SELECT DISTINCT s.repo_id, s.path, s.stable_key, r2.path AS repo_path "
        "FROM code_symbols s JOIN repositories r2 ON r2.id = s.repo_id "
        f"WHERE s.stable_key IN ({marks}) AND s.qualified_name LIKE 'whole:%' "
        "ORDER BY s.stable_key", tuple(sorted(keys)))


def native_cost(repo: str, path: str) -> dict:
    """Ціна сировини для одного вузла: файл + історія + blame."""
    total = {"bytes": 0, "chars": 0, "tokens_est": 0}
    ms = 0.0
    target = Path(repo) / path
    text, took = timed(lambda: target.read_text(encoding="utf-8", errors="replace")
                       if target.is_file() else "")
    for body, spent in ((text, took),
                        *(timed(git, repo, *args) for args in (
                            ("log", "-p", "-12", "--", path),
                            ("blame", "--", path)))):
        one = measure(body)
        for key in total:
            total[key] += one[key]
        ms += spent
    return {**total, "ms": round(ms, 1)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="measure_corpus")
    parser.add_argument("--root", required=True)
    parser.add_argument("--sample", type=int, default=40)
    parser.add_argument("--budget", type=int, default=8000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--level", choices=("caused_by", "mutation_in_commit"),
                        default="caused_by",
                        help="рівень атрибуції: участь чи доведений перехід")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    store = Store.open(args.root)
    pool = candidates(store, args.level)
    if not pool:
        print("вузлів з історією немає — міряти нічого")
        return 1
    random.Random(args.seed).shuffle(pool)
    chosen = pool[: args.sample]

    rows: list[dict] = []
    failures: list[dict] = []
    for item in chosen:
        repo = str(item["repo_path"])
        if not Path(repo).is_dir():
            failures.append({"path": item["path"], "reason": "repo_missing"})
            continue
        try:
            answer, ms = timed(why_mod.answer, store, repo_id=item["repo_id"],
                               path=item["path"], budget_chars=args.budget)
        except CairnError as exc:
            failures.append({"path": item["path"], "reason": exc.code})
            continue
        rows.append({
            "repo": Path(repo).name,
            "path": item["path"],
            "native": native_cost(repo, item["path"]),
            "cairn": {**answer["cost"], "ms": round(ms, 1)},
            "coverage": answer.get("coverage", {}),
            "omissions": [o["kind"] for o in answer.get("omissions") or []],
        })

    if not rows:
        print("жоден вузол не виміряно:", failures[:5])
        return 1

    def stat(side: str, key: str) -> dict:
        values = [row[side][key] for row in rows]
        return {"median": int(statistics.median(values)),
                "min": min(values), "max": max(values)}

    ratios = [row["native"]["tokens_est"] / row["cairn"]["tokens_est"]
              for row in rows if row["cairn"]["tokens_est"]]
    summary = {
        # Публічний звіт не несе машинних шляхів: ворота
        # `tests/verify_public_paths.py` це перевіряють, і вони спіймали
        # саме цей рядок. Назви теки достатньо, щоб упізнати сховище.
        "store": Path(args.root).name,
        "level": args.level,
        "nodes_with_history": len(pool),
        "sampled": len(rows),
        "failed": len(failures),
        "failure_reasons": sorted({f["reason"] for f in failures}),
        "budget_chars": args.budget,
        "native_tokens": stat("native", "tokens_est"),
        "cairn_tokens": stat("cairn", "tokens_est"),
        "native_ms": stat("native", "ms"),
        "cairn_ms": stat("cairn", "ms"),
        "ratio_tokens": {"median": round(statistics.median(ratios), 1),
                         "min": round(min(ratios), 1), "max": round(max(ratios), 1)},
        "boundary": "нативна колонка — ціна сировини, не ціна рівносильної відповіді; "
                    "токени — chars/4",
    }

    print(f"рівень атрибуції: {args.level}")
    print(f"вузлів із таким звʼязком: {summary['nodes_with_history']}")
    print(f"виміряно: {summary['sampled']} (не вдалося {summary['failed']})")
    print()
    print(f"{'':22} {'медіана':>10} {'мін':>10} {'макс':>10}")
    print("-" * 56)
    for label, data in (("нативно, токени", summary["native_tokens"]),
                        ("cairn, токени", summary["cairn_tokens"]),
                        ("нативно, мс", summary["native_ms"]),
                        ("cairn, мс", summary["cairn_ms"])):
        print(f"{label:22} {data['median']:>10} {data['min']:>10} {data['max']:>10}")
    print("-" * 56)
    ratio = summary["ratio_tokens"]
    print(f"{'відношення токенів':22} {ratio['median']:>10} {ratio['min']:>10} {ratio['max']:>10}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps({"summary": summary, "rows": rows, "failures": failures},
                       ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\nзаписано: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
