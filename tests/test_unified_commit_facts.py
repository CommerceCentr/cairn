"""Витяг фактів із commit messages: hard-wrap, гейт, пріоритет, історія.

Автономний тест: власний тимчасовий git-репозиторій, жодних зовнішніх шляхів.

Перевіряє:
  1. речення з 4 hard-wrap рядків збирається назад;
  2. витяг пропонує факти й не підвищує довіру сам;
  3. людина приймає факти окремою подією;
  4. signal-факт виграє у тісному бюджеті, хоч він довший;
  5. факт зі старого коміту позначається `historical`, не `current`;
  6. CLI `evidence facts` працює й ідемпотентний.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import code, commits, context, semantic, work  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

HUMAN = {"kind": "human", "id": "facts-human"}
AGENT = {"kind": "agent", "id": "facts-agent"}

FIRST = "Add a report about skipped patch operations"
SIGNAL = ("That is how a require line was lost from index.php in 2.0.69 "
          "while the next release reported no conflicts at all.")
MESSAGE = f"""{FIRST}

A conflicting anchor operation raises inside the applier, the loop breaks for that
file, and the file keeps its old content. The success check counted only status error, so
conflict passed through: the run finished, the version was recorded, and the file stayed
behind. That is how a require line was lost from index.php in 2.0.69 while the next release reported no
conflicts at all.
"""


def ok(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        raise AssertionError(name)


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return proc.stdout.strip()


def norm(text: str) -> str:
    return " ".join(text.split())


def in_items(text: str, fact: str) -> bool:
    return norm(fact) in norm(text)


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="cairn-facts-"))
    project = temp / "project"
    project.mkdir()
    try:
        git(project, "init", "-q")
        git(project, "config", "user.name", "Facts")
        git(project, "config", "user.email", "facts@local")
        (project / "index.php").write_text("<?php\nreturn 1;\n", encoding="utf-8")
        git(project, "add", ".")
        git(project, "commit", "-q", "-m", MESSAGE)
        fact_commit = git(project, "rev-parse", "HEAD")
        (project / "index.php").write_text("<?php\nreturn 2;\n", encoding="utf-8")
        git(project, "commit", "-qam", "next release")
        head = git(project, "rev-parse", "HEAD")

        print("1. HARD-WRAP ТА ВИТЯГ")
        sentences = commits.unwrap_sentences(MESSAGE)
        ok("речення зібрано з 4 рядків", any(SIGNAL in s for s in sentences),
           str([s[:60] for s in sentences]))
        facts = commits.select_facts(MESSAGE)
        ok("перше речення присутнє", FIRST in facts)
        ok("signal-речення присутнє", any(SIGNAL in f for f in facts))
        ok("signal-факт має вищий пріоритет", facts.index(SIGNAL) > 0)

        print("2. ГЕЙТ ДОВІРИ")
        Store(project).init()
        store = Store.open(project)
        identity = code.git.identity(str(project))
        store.mutate(lambda con: store.append(con, "repository.upserted", identity["id"],
                                              identity, HUMAN, f"repo:{identity['id']}"))
        rid = identity["id"]
        symbol = "symbol://facts/Test::run"
        wid = work.create_work(
            store, title="Витяг історії патчера", actor=HUMAN,
            scope={"repo_id": rid}, capabilities=["php84"],
            acceptance=[{"id": "AC-1", "kind": "evidence", "evidence_type": "commit"}],
            budget_limit_chars=2000,
        )["work"]["id"]
        semantic.relate(store, from_id=wid, to_id=symbol, relation="targets_symbol", actor=HUMAN)

        ingested = commits.ingest(store, rid, fact_commit, work_id=wid,
                                  symbol=symbol, actor=AGENT)
        ok("факти створені", ingested["facts"] >= 2, str(ingested["facts"]))
        ok("потрібне прийняття людиною", ingested["needs_human_accept"])
        proposed = context.build(store, wid, agent_id="facts-agent", purpose="review",
                                 budget_chars=2000, use_cache=False)
        proposed_text = "\n".join(i["text"] for i in proposed["items"])
        ok("пропозиція не потрапила в активний контекст", not in_items(proposed_text, SIGNAL))

        for oid in ingested["object_ids"]:
            semantic.accept_object(store, object_id=oid, actor=HUMAN, rationale=fact_commit)
        accepted = context.build(store, wid, agent_id="facts-agent", purpose="review",
                                 budget_chars=2000, use_cache=False)
        accepted_text = "\n".join(i["text"] for i in accepted["items"])
        ok("людина відкрила факт для контексту", in_items(accepted_text, SIGNAL))

        print("3. ПРІОРИТЕТ ЗАМІСТЬ ДОВЖИНИ")
        full = context.build(store, wid, agent_id="facts-agent", purpose="review",
                             budget_chars=20000, use_cache=False)
        tier1 = [i for i in full["items"] if i["tier"] == 1]
        signal_item = next(i for i in tier1 if in_items(i["text"], SIGNAL))
        first_item = next(i for i in tier1 if in_items(i["text"], FIRST))
        ok("signal-факт має вищий score", signal_item["score"] > first_item["score"],
           f"{signal_item['score']} > {first_item['score']}")
        ok("signal-факт довший", len(signal_item["text"]) > len(first_item["text"]))
        pinned = sum(len(i["text"]) for i in full["items"] if i["tier"] == 0)
        tight = context.build(store, wid, agent_id="facts-agent", purpose="review",
                              budget_chars=pinned + len(signal_item["text"]), use_cache=False)
        tight_text = "\n".join(i["text"] for i in tight["items"])
        ok("у тісному бюджеті виживає signal-факт", in_items(tight_text, SIGNAL))
        ok("коротший факт викидається першим", not in_items(tight_text, FIRST))

        print("4. ІСТОРИЧНЕ ПОЗНАЧЕННЯ")
        # Раніше детекція йшла за підрядком `commit:` у CONSTRAINTS-обгортці.
        # Після компактного рендеру (F2) його там немає; freshness обчислюється
        # з semantic.constraints, а не з text. Вибираємо за стабільним object_id.
        fact_ids = set(ingested["object_ids"])
        historical = [i for i in full["items"] if i["object_id"] in fact_ids]
        ok("факти зі старого коміту позначені historical",
           bool(historical) and all(i["freshness"] == "historical" for i in historical),
           str([i["freshness"] for i in historical]))
        ok("жоден старий факт не названий current",
           not any(in_items(i["text"], SIGNAL) and i["freshness"] == "current"
                   for i in full["items"]))
        ok("repo head — інший коміт", head != fact_commit)

        print("5. CLI")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        cli = subprocess.run(
            [sys.executable, "-m", "cairn_unified", "--root", str(project), "--compact",
             "evidence", "facts", rid, fact_commit, "--work", wid, "--symbol", symbol],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        ok("exit=0", cli.returncode == 0, cli.stderr.strip()[:120])
        payload = json.loads(cli.stdout.strip().splitlines()[-1]) if cli.returncode == 0 else {}
        ok("CLI повернув факти", payload.get("facts", 0) >= 2, str(payload.get("facts")))
        ok("CLI ідемпотентний", set(payload.get("object_ids", [])) == set(ingested["object_ids"]))

        print("ВЕРДИКТ: hard-wrap виправлено, гейт збережено, пріоритет і historical працюють")
        return 0
    finally:
        import shutil
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
