"""Прив’язана до Cairn історія вузла: Git + факти + компактовані досьє.

Усе робиться з Python: Git викликається лише дозволеним адаптером
`code.git`, а packs читаються як текстові дані. Жодного PHP, AST чи
зовнішнього інструменту. Подія `history.scanned` фіксує впорядкований
перелік комітів, хеш і стан індексу на момент сканування.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import commits, semantic
from .canon import canonical, sha256_text
from .code import git
from .errors import CairnError

LOG_FORMAT = "%H%x09%cI%x09%s"
EV_RE = re.compile(
    r"^\s*EV\s+([0-9a-f]{7,40})\s+(\d{4}-\d{2}-\d{2})\s+(\S+)(?:\s+.*)?$"
)
MAP_RE = re.compile(r"^\s*N\s+(\S+)\s+(\S+):(\d+)-(\d+)")
NODE_RE = re.compile(r"^NODE\s+(\S+)")


def commits_for_path(repo_path: str, path: str, *, follow: bool = False) -> list[dict]:
    """Коміти, що торкаються шляху; детермінований порядок за датою й hash."""
    args = ["log", "--format=" + LOG_FORMAT]
    if follow:
        args.append("--follow")
    args.extend(["--", path])
    rows = []
    for line in git.git(repo_path, *args).splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            rows.append({"commit": parts[0], "date": parts[1], "subject": parts[2]})
    return sorted(rows, key=lambda row: (row["date"], row["commit"]))


def scan(store, repo_id: str, work_id: str, symbol: str, path: str, actor: dict,
         *, follow: bool = False) -> dict:
    """Витягти факти з усієї історії шляху й записати один scan-event."""
    repo = store.read_one("SELECT * FROM repositories WHERE id = ?", (repo_id,))
    if repo is None:
        raise CairnError("not_found", f"репозиторій {repo_id} не зареєстровано", exit_code=1)
    rows = commits_for_path(repo["path"], path, follow=follow)
    object_ids: list[str] = []
    for row in rows:
        result = commits.ingest(store, repo_id, row["commit"], work_id=work_id,
                                symbol=symbol, actor=actor)
        object_ids.extend(result["object_ids"])
    digest = sha256_text(canonical(rows))
    payload = {
        "repo_id": repo_id, "work_id": work_id, "symbol": symbol, "path": path,
        "commits": rows, "commit_count": len(rows), "digest": digest,
        "object_ids": object_ids, "extractor": "python-git-history/1.0",
    }
    event = store.mutate(lambda con: store.append(
        con, "history.scanned", work_id, payload, actor,
        idempotency_key=f"history:{work_id}:{symbol}:{digest}",
    ))
    return {
        "commits": len(rows), "facts": len(object_ids), "object_ids": object_ids,
        "digest": digest, "event_id": event["event_id"], "replayed": event["replayed"],
        "needs_human_accept": bool(object_ids),
    }


def _parse_pack(text: str) -> dict:
    mapping: dict[str, dict] = {}
    dossiers: dict[str, list[dict]] = {}
    section = ""
    address = ""
    current: dict | None = None
    for line in text.splitlines():
        if line.startswith("MAP"):
            section = "map"
            continue
        if line.startswith("DOSSIER"):
            section = "dossier"
            continue
        if section == "map":
            match = MAP_RE.match(line)
            if match:
                mapping[match.group(1)] = {
                    "path": match.group(2), "start": int(match.group(3)),
                    "end": int(match.group(4)),
                }
            continue
        node = NODE_RE.match(line)
        if node:
            address = node.group(1)
            dossiers.setdefault(address, [])
            current = None
            continue
        match = EV_RE.match(line)
        if match and address:
            current = {"sha": match.group(1), "date": match.group(2),
                       "kind": match.group(3), "subject": "", "body": "",
                       "was": [], "in_was": False}
            dossiers[address].append(current)
            continue
        if not current:
            continue
        stripped = line.strip()
        if stripped.startswith("S ") and not current["subject"]:
            current["subject"] = stripped[2:].strip()[:400]
            current["in_was"] = False
            continue
        if stripped.startswith("B ") and not current["body"]:
            current["body"] = stripped[2:].strip()[:1200]
            current["in_was"] = False
            continue
        if stripped == "WAS <<<":
            current["in_was"] = True
            continue
        if stripped == ">>>":
            current["in_was"] = False
            continue
        if current["in_was"]:
            current["was"].append(line)
            if sum(len(x) + 1 for x in current["was"]) > 4000:
                current["in_was"] = False
    for entries in dossiers.values():
        for entry in entries:
            prose = []
            for raw in entry["was"]:
                clean = raw.strip().lstrip("/**").strip()
                clean = clean.lstrip("*").strip()
                if clean and not clean.startswith(">>>"):
                    prose.append(clean)
            entry["rationale"] = " ".join(prose)[:800] or entry["body"]
    return {"map": mapping, "dossiers": dossiers}


def import_compactions(store, repo_id: str, work_id: str, symbol: str, path: str,
                       actor: dict, packs_dir: str | Path,
                       *, start_line: int | None = None, end_line: int | None = None,
                       auto_verify: bool = False, repo_path: str | Path | None = None) -> dict:
    """Імпортувати досьє arch pack як пропозиції, пов’язані з вузлом.

    Pack читається як дані: він не виконується і не вимагає PHP. Кожен рядок
    EV резолвиться в повний Git-hash, щоб компактація мала той самий `commit`,
    що й факти з `commits.ingest`. Без прийняття людиною об’єкт лишається
    `agent_proposed` і в активний контекст не потрапляє.
    """
    root = Path(packs_dir)
    repo = store.read_one("SELECT * FROM repositories WHERE id = ?", (repo_id,))
    if repo is None:
        raise CairnError("not_found", f"репозиторій {repo_id} не зареєстровано", exit_code=1)
    if not root.is_dir():
        return {"packs": 0, "objects": 0, "relations": 0, "path": str(root), "reason": "packs_dir_missing"}

    created: list[str] = []
    relations = 0
    packs = 0
    for pack in sorted(root.glob("*.txt")):
        packs += 1
        pack_text = pack.read_text(encoding="utf-8", errors="replace")
        parsed = _parse_pack(pack_text)
        pack_digest = sha256_text(pack_text)
        for address, entries in parsed["dossiers"].items():
            geo = parsed["map"].get(address)
            if not geo or geo["path"] != path:
                continue
            if start_line is not None and end_line is not None:
                if geo["end"] < start_line or geo["start"] > end_line:
                    continue
            for entry in entries:
                try:
                    full = git.git(repo["path"], "rev-parse", f"{entry['sha']}^{{commit}}").strip()
                    # Обʼєкти того самого коміту. `full` різний на кожен
                    # запис досьє, тому запит лишається на запис — але
                    # тепер він ФІЛЬТРОВАНИЙ, а не читає таблицю цілком
                    # і не розбирає JSON усіх обʼєктів. LIKE звужує, а
                    # точну рівність перевіряє наступний рядок: підрядок
                    # не розрізняє поле від вмісту.
                    same_commit = [
                        {"id": row["id"], "body": json.loads(row["payload_json"])}
                        for row in store.read(
                            "SELECT id, payload_json FROM objects WHERE payload_json LIKE ?",
                            (f'%"commit": "{full}"%',))
                    ]
                    same_commit = [item for item in same_commit
                                   if (item["body"].get("semantic") or {}).get("commit") == full]
                except CairnError:
                    continue
                if not full:
                    continue
                summary = entry["subject"] or f"{entry['kind']} {entry['sha']}"
                key = "cp-" + sha256_text(pack_digest + address + full)[:16]
                result = semantic.put_object(
                    store, object_type="decision",
                    title=f"compaction {entry['sha']} {entry['kind']}",
                    summary=summary, actor=actor, lifecycle="proposed", trust="agent_proposed",
                    stable_key=key,
                    idempotency_key=f"compaction:{key}",
                    semantic={"kind": "compaction", "commit": full, "scope": [symbol],
                              "covers": [entry["sha"]], "pack": pack.name,
                              "pack_digest": pack_digest, "node_address": address,
                              "rationale": entry.get("rationale") or "",
                              "body": entry.get("body") or ""},
                    constraints=[f"commit:{full[:7]}"], scope=[symbol],
                    valid_from=entry["date"],
                )
                created.append(result["object"]["id"])
                if auto_verify:
                    from . import verification
                    verification.verify_object(
                        store, result["object"]["id"],
                        repo_path=repo_path or repo["path"], packs_dir=root,
                    )
                semantic.relate(
                    store, from_id=work_id, to_id=result["object"]["id"],
                    relation="governed_by", actor=actor,
                    idempotency_key=f"cmp-governed:{key}:{work_id}",
                )
                # Було: `SELECT id, payload_json FROM objects` без умови —
                # уся таблиця плюс розбір JSON кожного обʼєкта, на кожен
                # запис досьє. Тепер фільтрований набір, зібраний вище.
                for fact in same_commit:
                    if fact["id"] == result["object"]["id"]:
                        continue
                    body = fact["body"]
                    sem = body.get("semantic") or {}
                    if sem.get("kind") == "compaction":
                        continue
                    if symbol not in (sem.get("scope") or []) \
                            and symbol not in (sem.get("node_refs") or []):
                        continue
                    relation = semantic.relate(
                        store, from_id=result["object"]["id"], to_id=fact["id"],
                        relation="derived_from", actor=actor,
                        idempotency_key=f"cmp-rel:{key}:{fact['id']}",
                    )
                    relations += 0 if relation.get("replayed") else 1
    return {
        "packs": packs, "objects": len(created), "relations": relations,
        "object_ids": created, "needs_human_accept": bool(created),
    }
