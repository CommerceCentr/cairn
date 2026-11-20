"""PHP 8.4 reference adapter: normalized symbols, no domain decisions.

Capability matrix (measured against `token_get_all`, PHP 8.4.16):

| capability | precision |
|---|---|
| namespace/class/method/function declarations | structural |
| qualified identity + structural fingerprint | structural |
| source range (line) at a given revision | structural |
| direct/static/$this call edges | structural |
| dynamic/other-object call edges | heuristic |

Unexpected parse failures never silently mark an old index as current: the
whole indexing command fails and the previous projection stays visible.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..canon import sha256_text
from ..errors import CairnError

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "adapters" / "php-parser" / "index.php"
SKIP_DIRS = {".git", "vendor", "node_modules", ".cairn", "cache", "out"}
CAPABILITIES = (
    "php84.declarations", "php84.qualified-identity", "php84.source-span",
    "php84.call-graph", "php84.structural-fingerprint",
)


def available() -> bool:
    try:
        run = subprocess.run(["php", "-v"], capture_output=True, text=True, timeout=10)
        return run.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def extract(repo_path: str, repo_id: str, rel_path: str) -> dict:
    """Повертає сирі записи адаптера. Шлях у виході лише відносний."""
    if not HELPER.exists():
        raise CairnError("parser_missing", f"відсутній {HELPER.name}", exit_code=2)
    source = Path(repo_path) / rel_path
    if not source.is_file():
        raise CairnError("source_missing", f"файл {rel_path} не знайдено", exit_code=2)
    try:
        proc = subprocess.run(
            ["php", str(HELPER), str(source), repo_id, rel_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
    except FileNotFoundError as exc:
        raise CairnError("php_unavailable", "PHP CLI недоступний у PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise CairnError("parser_timeout", f"парсер перевищив час на {rel_path}") from exc
    if proc.returncode != 0:
        raise CairnError("parse_failed", f"не вдалося розібрати {rel_path}", details={"stderr": proc.stderr[:400]})
    try:
        body = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CairnError("parser_output_invalid", f"адаптер повернув не JSON для {rel_path}") from exc
    return body


def discover(repo_path: str) -> list[str]:
    root = Path(repo_path)
    files = []
    for path in root.rglob("*.php"):
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        files.append(relative.as_posix())
    return sorted(files)


def index(store, repo_id: str, actor: dict) -> dict:
    """Індексує репозиторій і пише один event `code.indexed`."""
    from . import git  # локальний імпорт: уникаємо циклу з work.py

    repo = store.read_one("SELECT * FROM repositories WHERE id = ?", (repo_id,))
    if repo is None:
        raise CairnError("not_found", f"репозиторій {repo_id} не зареєстровано", exit_code=1)
    if not available():
        raise CairnError("php_unavailable", "PHP 8.4 CLI потрібен для індексації")
    symbols: list[dict] = []
    edges: list[dict] = []
    files = discover(repo["path"])
    for rel in files:
        parsed = extract(repo["path"], repo_id, rel)
        symbols.extend(parsed.get("symbols") or [])
        edges.extend(parsed.get("edges") or [])
    symbols.sort(key=lambda item: item["stable_key"])
    edges.sort(key=lambda item: item["id"])
    revision = git.head(repo["path"])
    payload = {
        "repo_id": repo_id,
        "revision": revision,
        "symbols": symbols,
        "edges": edges,
        "extractor": "php-token-get-all/1.0",
        "capabilities": list(CAPABILITIES),
        "files": files,
        "index_digest": sha256_text(
            json.dumps([symbols, edges], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ),
    }

    def op(con):
        ev = store.append(con, "code.indexed", repo_id, payload, actor)
        return {
            "repo_id": repo_id,
            "revision": revision,
            "symbols": len(symbols),
            "edges": len(edges),
            "files": len(files),
            "index_digest": payload["index_digest"],
            "event_id": ev["event_id"],
            "replayed": ev["replayed"],
        }

    return store.mutate(op)
