"""Генерація компактованих досьє Python-інструментарієм.

Джерело — локальна Git-історія та робочі файли. Жодного PHP, AST чи зовнішнього
парсера мови. Формат виводу сумісний із `history._parse_pack`: MAP, DOSSIER,
EV/S/B і блок WAS із прибраною прозою (коментарів, які зникли з коду).

Пак детермінований: той самий HEAD і та сама історія дають той самий текст,
тому повторна генерація не змінює digest і не породжує зайвих подій.
"""

from __future__ import annotations

import re
from pathlib import Path

from .canon import sha256_text
from .code import git

PROSE_MARKERS = ("//", " * ", "/*", "#")
COMMIT_SEP = "@@@"
EV_RE = re.compile(r"^\s*EV\s+([0-9a-f]{7,40})\s+(\d{4}-\d{2}-\d{2})\s+(\S+)")
FILE_RE = re.compile(r"^\+\+\+ b/(.+)$")
MAX_WAS_CHARS = 800
MAX_BODY_CHARS = 300
MAX_EV_PER_NODE = 40


def _one_line(text: str, cap: int = MAX_BODY_CHARS) -> str:
    return " ".join(text.split())[:cap]


def _clean_prose(raw: str) -> str:
    line = raw.strip()
    if not line or line in {"/**", "*/", "/*", "//", "#"}:
        return ""
    for marker in (" * ", "// ", "# ", "/* ", "* "):
        if line.startswith(marker):
            line = line[len(marker):]
            break
    if line.startswith("*"):
        line = line[1:].strip()
    if line.startswith("/") or line.startswith("#"):
        line = line.lstrip("/# ").strip()
    line = line.strip()
    # PHPDoc-теги — це типи, а не причина; у WAS вони лише витрачають бюджет.
    if line.startswith("@") or line.startswith("{@"):
        return ""
    return line


def _is_prose(raw: str) -> bool:
    stripped = raw.strip()
    return stripped.startswith(PROSE_MARKERS) or stripped.startswith("*")


def _changed_files(repo: Path) -> dict[str, list[str]]:
    """commit -> список PHP-файлів, яких він торкнувся."""
    out: dict[str, list[str]] = {}
    sha = ""
    for line in git.git(str(repo), "log", "--format=" + COMMIT_SEP + "%H",
                        "--name-only", "--", "*.php").splitlines():
        if line.startswith(COMMIT_SEP):
            sha = line[len(COMMIT_SEP):].strip()
            out.setdefault(sha, [])
        elif sha and line.strip().endswith(".php"):
            out[sha].append(line.strip())
    return out


def _commits(repo: Path) -> list[dict]:
    raw = git.git(str(repo), "log",
                  "--format=" + COMMIT_SEP + "%H%x09%cI%x09%s%x09%b%x00",
                  "--", "*.php")
    out: list[dict] = []
    for record in raw.split("\x00"):
        record = record.strip()
        if not record.startswith(COMMIT_SEP):
            continue
        parts = record[len(COMMIT_SEP):].split("\t", 3)
        if len(parts) < 4:
            continue
        out.append({"sha": parts[0], "date": parts[1][:10],
                    "subject": _one_line(parts[2]), "body": _one_line(parts[3])})
    return sorted(out, key=lambda row: (row["date"], row["sha"]))


def _removed_prose(repo: Path) -> dict[tuple[str, str], list[str]]:
    """(file, commit) -> витерті рядки прози з diff."""
    out: dict[tuple[str, str], list[str]] = {}
    sha = ""
    path = ""
    used = 0
    for line in git.git(str(repo), "log", "-p", "--format=" + COMMIT_SEP + "%H",
                        "--", "*.php").splitlines():
        if line.startswith(COMMIT_SEP):
            sha = line[len(COMMIT_SEP):].strip()
            path = ""
            used = 0
            continue
        match = FILE_RE.match(line)
        if match and match.group(1) != "/dev/null":
            path = match.group(1)
            used = 0
            continue
        if not (sha and path and line.startswith("-") and not line.startswith("---")):
            continue
        clean = _clean_prose(line[1:])
        if not clean or not _is_prose(line[1:]):
            continue
        key = (path, sha)
        bucket = out.setdefault(key, [])
        if used + len(clean) > MAX_WAS_CHARS or clean in bucket:
            continue
        bucket.append(clean)
        used += len(clean)
    return out


def build_pack(repo: Path, zone: str, *, min_events: int = 3,
               head: str | None = None) -> tuple[str, dict]:
    """Сформувати текст пака й статистику для однієї зони."""
    head = head or git.git(str(repo), "rev-parse", "--short=7", "HEAD").strip()
    commits = _commits(repo)
    changed = _changed_files(repo)
    prose = _removed_prose(repo)
    by_sha = {row["sha"]: row for row in commits}

    nodes: dict[str, list[dict]] = {}
    for sha, paths in changed.items():
        meta = by_sha.get(sha)
        if not meta:
            continue
        for path in paths:
            nodes.setdefault(path, []).append(meta)

    kept: dict[str, list[dict]] = {}
    omitted = 0
    for path, rows in nodes.items():
        rows = sorted(rows, key=lambda r: (r["date"], r["sha"]))
        if len(rows) >= min_events:
            kept[path] = rows[-MAX_EV_PER_NODE:]
        else:
            omitted += 1

    lines = [f"PACK py-{zone} ZONE {zone} HEAD {head} FRESH BUDGET 8000",
             "TASK auto-dossier", "", "MAP"]
    for path, rows in sorted(kept.items()):
        try:
            n_lines = len((repo / path).read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            n_lines = 1
        lines.append(f"  N whole:{path} {path}:1-{max(n_lines, 1)} E{len(rows)} P C{len(rows)}")
    lines.extend(["", "DOSSIER"])
    was_blocks = 0
    for path, rows in sorted(kept.items()):
        lines.append(f"NODE whole:{path}")
        for row in rows:
            sha = row["sha"]
            lines.append(f" EV {sha} {row['date']} DOC BULK")
            lines.append(f"  S {row['subject'] or sha[:7]}")
            if row["body"]:
                lines.append(f"  B {row['body']}")
            snippet = prose.get((path, sha)) or []
            if snippet:
                lines.append("  WAS <<<")
                lines.extend(" * " + item for item in snippet)
                lines.append(">>>")
                was_blocks += 1
        lines.append("")
    if omitted:
        lines.append(f"OMITTED {omitted} файлів (<{min_events} подій)")
    text = "\n".join(lines) + "\n"
    stats = {
        "zone": zone, "head": head, "commits": len(commits), "files": len(nodes),
        "nodes": len(kept), "omitted": omitted, "was_blocks": was_blocks,
        "chars": len(text), "sha256": sha256_text(text),
    }
    return text, stats


def write_zone(repo: Path, zone: str, out_dir: Path, **kwargs) -> dict:
    """Записати пак, якщо він змінився. Повертає статистику й прапорець change."""
    out_dir.mkdir(parents=True, exist_ok=True)
    text, stats = build_pack(repo, zone, **kwargs)
    path = out_dir / f"pack-py-{zone}.txt"
    existed = path.exists()
    current = path.read_text(encoding="utf-8") if existed else ""
    if current == text:
        stats.update({"path": str(path), "changed": False})
        return stats
    path.write_text(text, encoding="utf-8")
    stats.update({"path": str(path), "changed": True or not existed})
    return stats
