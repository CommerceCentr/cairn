"""Одне питання, дві відповіді: без Cairn і з Cairn. З вимірами.

    python tools/answer_two_ways.py --root <сховище> --repo <репозиторій>

Питання задано про вузол, який агент редагував САМ і нещодавно. Воно
вибране так, щоб відповідь не можна було вгадати з коду: питають не «що
тут написано», а «чому саме так».

Метод. Відповідь розкладена на ПЕРЕВІРЯНІ факти. Перша версія цього
приладу питала «чи є рядок у матеріалі» — і нативний шлях дав 7 із 7.
Це правда: сире перечитування містить усе, бо воно й ЄСТЬ джерелом.
Питання було легшим за те, що важить, тому прилад переписано.

Тепер для кожного факту міряється:

```text
tokens_to_reach  скільки токенів матеріалу треба пройти до першої згадки
typed            чи сказано, ЩО це: намір людини / код / факт коміту
```

Чого вимір НЕ доводить: що нативний шлях «не зміг би». За досить часу
агент прочитає сирий матеріал і дійде висновку сам. Вимір показує ціну
доступу й наявність типу.

Окремо, без прикрас: частину «чому» Cairn із цього джерела дати НЕ може.
Журнал Claude Code не експортує прозу агента, тому міркування, яке
привело до правки, у графі відсутнє — у матриці можливостей це
`reasoning: unsupported:not_exported`. Наміри КОРИСТУВАЧА там є, дослівно
й звірені за sha256.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from measure import ZERO, add, measure, norm, timed  # noqa: E402

NODE = "cairn_unified/why.py"
COMMIT = "9feecdf9081108a3a4b33d2a5052f8bca15eace9"

QUESTION = (
    "Чому у `cairn_unified/why.py` функція `_apply_budget` ріже хронологію "
    "й походження, але ніколи не прибирає `omissions`, і звідки там узявся "
    "`budget_exceeded`?"
)

#: Факти, з яких складається повна відповідь. Кожен — рядок, наявність
#: якого в тексті можна перевірити механічно, без тлумачення.
FACTS: list[tuple[str, str, str]] = [
    ("що", "budget_exceeded", "у коді є окремий стан «бюджет не виконано»"),
    ("що", "omissions", "список пропусків існує як окреме поле"),
    ("що", "згорнуто до лічильників", "довіра згортається, а не зникає"),
    ("чому", "6 600", "перша версія рапортувала 2 000 і віддавала 6 600"),
    ("чому", "приховав межу", "правило: контекст, що приховав межу, гірший за порожній"),
    ("хто", "бюджет", "хто саме цього просив — намір користувача"),
    ("коли", "9feecdf", "коміт, у якому це зʼявилось"),
]


def git(repo: str, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "safe.directory=*", "-c", "core.pager=cat", "-C", repo, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.stdout if done.returncode == 0 else ""


def native(repo: str, store_root: str) -> dict:
    """Те, що має нативний агент: код, git і лінійний скан журналу."""
    steps: list[tuple[str, str, float]] = []
    name = Path(NODE).name

    text, ms = timed(lambda: (Path(repo) / NODE).read_text(encoding="utf-8"))
    steps.append((f"читання файла {NODE}", text, ms))
    text, ms = timed(git, repo, "log", "-1", COMMIT)
    steps.append(("git log -1", text, ms))
    text, ms = timed(git, repo, "log", "-p", "-12", "--", NODE)
    steps.append((f"git log -p -12 -- {NODE}", text, ms))
    text, ms = timed(git, repo, "blame", "--", NODE)
    steps.append(("git blame", text, ms))

    def scan() -> str:
        path = Path(store_root) / ".cairn" / "events" / "ledger.jsonl"
        if not path.exists():
            return ""
        with path.open(encoding="utf-8", errors="replace") as handle:
            return "".join(line for line in handle if name in line)

    text, ms = timed(scan)
    steps.append((f"лінійний скан журналу ~ {name}", text, ms))

    total, total_ms, rows, blob = dict(ZERO), 0.0, [], []
    for label, body, ms in steps:
        one = measure(body)
        rows.append({"step": label, **one, "ms": round(ms, 1)})
        total = add(total, one)
        total_ms += ms
        blob.append(body)
    return {"path": "native", "steps": rows,
            "total": {**total, "ms": round(total_ms, 1)},
            "text": "\n".join(blob)}


def with_cairn(store_root: str, repo: str) -> dict:
    """Те, що має агент із Cairn: одна відповідь `why`."""
    from cairn_unified import code, why
    from cairn_unified.store import Store

    store = Store.open(store_root)
    repo_id = code.git.identity(repo)["id"]
    answer, ms = timed(why.answer, store, repo_id=repo_id, path=NODE,
                       commit=COMMIT, intent_text=True)
    body = json.dumps(answer, ensure_ascii=False, indent=1)
    return {"path": "cairn", "answer": answer,
            "total": {**measure(body), "ms": round(ms, 1)},
            "text": body}


def found(text: str, facts: list[tuple[str, str, str]]) -> list[dict]:
    """Для кожного факту: чи є в матеріалі й за скільки токенів до нього дійти.

    Друга версія цього приладу позначала ще й «типізованість» через пошук
    маркерів у тексті. Це було фальшиво: маркери трапляються і в сирому
    блобі, бо блоб містить сам текст сесії. Структуру міряє окрема
    функція `typed_edges` — парсингом, а не підрядком.
    """
    low = norm(text).lower()
    out = []
    for kind, needle, means in facts:
        at = low.find(norm(needle).lower())
        out.append({
            "kind": kind, "needle": needle, "means": means,
            "present": at >= 0,
            # Ціна доступу: скільки матеріалу треба пройти до першої згадки.
            "tokens_to_reach": (-(-at // 4)) if at >= 0 else None,
        })
    return out


def typed_edges(payload) -> int:
    """Скільки звʼязків доступні ЯК ДАНІ, з назвою типу.

    Нативний матеріал — текст: у ньому немає поля, яке каже «це намір
    людини», «це факт коміту», «це мутація». Тому для нього тут нуль не
    тому, що звʼязків не було, а тому, що їх нічим прочитати, крім
    тлумачення людиною.
    """
    if not isinstance(payload, dict):
        return 0
    return sum(1 for item in payload.get("chronology") or []
               if isinstance(item, dict) and item.get("relation"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="answer_two_ways")
    parser.add_argument("--root", required=True)
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    repo = str(Path(args.repo).resolve())

    left = native(repo, args.root)
    right = with_cairn(args.root, repo)
    left_facts = found(left["text"], FACTS)
    right_facts = found(right["text"], FACTS)

    print("ПИТАННЯ")
    print(" ", QUESTION)
    print()
    print(f"{'крок':46} {'байти':>9} {'токени≈':>9} {'мс':>7}")
    print("-" * 74)
    for row in left["steps"]:
        print(f"{row['step'][:45]:46} {row['bytes']:>9} {row['tokens_est']:>9} {row['ms']:>7}")
    print("-" * 74)
    print(f"{'БЕЗ CAIRN — усього':46} {left['total']['bytes']:>9} "
          f"{left['total']['tokens_est']:>9} {left['total']['ms']:>7}")
    print(f"{'З CAIRN — одна відповідь why':46} {right['total']['bytes']:>9} "
          f"{right['total']['tokens_est']:>9} {right['total']['ms']:>7}")
    print()
    def cell(item: dict) -> str:
        return "—" if not item["present"] else f"{item['tokens_to_reach']}т"

    print(f"{'факт (за скільки токенів до нього дійти)':40} "
          f"{'без Cairn':>13} {'з Cairn':>13}")
    print("-" * 70)
    for a, b in zip(left_facts, right_facts):
        print(f"[{a['kind']:4}] {a['means'][:32]:33} {cell(a):>13} {cell(b):>13}")
    print("-" * 70)
    hits_left = sum(1 for item in left_facts if item["present"])
    hits_right = sum(1 for item in right_facts if item["present"])
    reach_left = [i["tokens_to_reach"] for i in left_facts if i["present"]]
    reach_right = [i["tokens_to_reach"] for i in right_facts if i["present"]]
    print(f"{'фактів присутньо':40} {hits_left:>9}/{len(FACTS)} "
          f"{hits_right:>9}/{len(FACTS)}")
    print(f"{'найдальший факт, токенів':40} {max(reach_left or [0]):>13} "
          f"{max(reach_right or [0]):>13}")
    edges_right = typed_edges(right["answer"])
    print(f"{'звʼязків доступно ЯК ДАНІ (з типом)':40} {0:>13} {edges_right:>13}")
    print()
    print("ВІДПОВІДЬ З CAIRN:")
    for line in str(right["answer"]["answer"]).splitlines():
        print("  " + line)

    report = {
        "question": QUESTION, "node": NODE, "commit": COMMIT,
        "native": {"total": left["total"], "steps": left["steps"],
                   "facts": left_facts, "facts_found": hits_left},
        "cairn": {"total": right["total"], "facts": right_facts,
                  "facts_found": hits_right,
                  "answer_text": right["answer"]["answer"],
                  "chronology": right["answer"]["chronology"],
                  "omissions": right["answer"]["omissions"]},
        "facts_total": len(FACTS),
        "native_reach_max": max([i["tokens_to_reach"] for i in left_facts
                                  if i["present"]] or [0]),
        "cairn_reach_max": max([i["tokens_to_reach"] for i in right_facts
                                if i["present"]] or [0]),
        "typed_edges_native": 0,
        "typed_edges_cairn": typed_edges(right["answer"]),
        "boundary": "наявність рядка — легша перевірка, ніж відповідь; тут "
                    "міряється ціна доступу й типізованість, і вимір НЕ "
                    "доводить, що нативний шлях не зміг би",
        "known_gap": "прозу агента журнал Claude Code не експортує, тому "
                     "мотив правки у графі відсутній (reasoning: unsupported)",
    }
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                                  encoding="utf-8")
        print(f"\nзаписано: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
