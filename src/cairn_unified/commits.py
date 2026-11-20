"""Локальний витяг фактів із commit messages.

Git ламає рядки повідомлення на ~76 символів, тому речення не можна брати
по рядку: рішення про втрачений `require` розкидане на чотири рядки. Спершу
згортаємо абзаци, потім ділимо на речення, потім відбираємо перше речення
і речення зі змістом причини/насліду.

Витяг лише пропонує факти. Їхня довіра — `agent_proposed`, доки людина не
виконає `decision accept`; Cairn не підвищує довіру власного тексту сам.
"""

from __future__ import annotations

import re

from . import semantic
from .canon import sha256_text
from .code import git
from .errors import CairnError

SIGNALS = (
    "require line", "a conflict now fails", "no-op", "silent",
    "migrations ran before", "post-apply verification previously",
    "already absent", "true errors as blocking", "false verification",
    "skipped", "rolled back", "reported success", "fails the run",
)


def unwrap_sentences(body: str) -> list[str]:
    """Згорнути hard-wrap Git у абзаци й повернути речення завдовжки ≥20."""
    paragraphs: list[str] = []
    buf: list[str] = []
    for line in body.splitlines():
        if line.strip():
            buf.append(line.strip())
        elif buf:
            paragraphs.append(" ".join(buf))
            buf = []
    if buf:
        paragraphs.append(" ".join(buf))
    out: list[str] = []
    for para in paragraphs:
        if para.startswith("Co-Authored-By:"):
            continue
        out.extend(
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+(?=[A-ZА-ЯІЇЄҐ])", para)
            if len(s.strip()) >= 20
        )
    return out


def select_facts(body: str) -> list[str]:
    """Перше речення + signal-речення; за відсутності — subject коміту."""
    sentences = unwrap_sentences(body)
    if not sentences:
        # Коміт може мати лише короткий subject (`reclean`, 7 символів).
        # Поріг ≥20 у unwrap_sentences вилучав би його повністю: факт є
        # в історії, але відсутній у контексті (25/26 при будь-якому бюджеті,
        # виміряно в cairn_grouped.json). Subject несе INFO навіть короткий.
        subject = " ".join(body.splitlines()[0].split()) if body.strip() else ""
        return [subject] if subject else []
    picked = [sentences[0]]
    seen = {sentences[0]}
    for sentence in sentences[1:]:
        if sentence in seen:
            continue
        if any(word in sentence.lower() for word in SIGNALS):
            picked.append(sentence)
            seen.add(sentence)
    return picked


def ingest(store, repo_id: str, commit: str, *, work_id: str, symbol: str, actor: dict) -> dict:
    """Додати факти коміту як пропозиції, пов'язані з задачею й символом."""
    repo = store.read_one("SELECT * FROM repositories WHERE id = ?", (repo_id,))
    if repo is None:
        raise CairnError("not_found", f"репозиторій {repo_id} не зареєстровано", exit_code=1)
    if not git.exists(repo["path"], commit):
        raise CairnError("commit_missing", f"коміт {commit} не існує локально", exit_code=2)
    full = git.git(repo["path"], "rev-parse", commit).strip()
    body = git.git(repo["path"], "show", "-s", "--format=%B", commit)
    valid_from = git.git(repo["path"], "show", "-s", "--format=%cI", commit).strip() or None
    facts = select_facts(body)
    ids: list[str] = []
    for number, fact in enumerate(facts):
        key = f"cf-{sha256_text(repo_id + full + fact)[:16]}"
        obj = semantic.put_object(
            store, object_type="decision",
            title=f"commit {full[:7]} fact {number + 1}",
            summary=fact, actor=actor, lifecycle="proposed", trust="agent_proposed",
            stable_key=key,
            semantic={"priority": 0 if number == 0 else 1, "commit": full},
            constraints=[f"commit:{full[:7]}"], scope=[symbol], valid_from=valid_from,
            idempotency_key=f"commit-fact:{key}",
        )["object"]
        semantic.relate(store, from_id=work_id, to_id=obj["id"],
                        relation="governed_by", actor=actor,
                        idempotency_key=f"commit-fact-rel:{work_id}:{obj['id']}")
        ids.append(obj["id"])
    return {"commit": full, "facts": len(facts), "object_ids": ids, "needs_human_accept": bool(ids)}
