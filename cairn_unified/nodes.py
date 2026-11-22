"""Мовно-нейтральна ідентичність вузла: тільки Python + Git.

Логіка перенесена з v1 (`extract/__init__.py`, `extract/code_python.py`):
адреса вузла — `symbol:`, `section:`, `page:`, `segment:` або `whole:`;
невідомий тип не є помилкою; бінарні файли не читаються в пам'ять.

Відмінність від v1: `whole:` тепер містить шлях (`whole:<path>`), бо інакше
усі файли мали одну адресу. Для Python використовується stdlib `ast`,
для решти мов — лексичний якір (без AST, без інтерпретатора мови).
Формат події `code.indexed` лишається сумісним із `projections._code()`.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from . import code
from .canon import sha256_text
from .errors import CairnError

KINDS = ("symbol", "section", "page", "segment", "whole")
UNITS = ("byte", "page", "second", "none")
SKIP_DIRS = {
    ".git", ".cairn", "vendor", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "out", "cache", ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
}
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".mp3", ".wav",
    ".ogg", ".flac", ".m4a", ".mp4", ".mov", ".avi", ".mkv", ".webm", ".pdf", ".zip",
    ".gz", ".7z", ".rar", ".exe", ".dll", ".so", ".xlsx", ".docx", ".pptx", ".odt", ".ods",
}
MAX_FILE_BYTES = 2_000_000
MAX_NODES_PER_FILE = 200
DECL = re.compile(
    r"^\s*(?:public\s+|private\s+|protected\s+|static\s+|async\s+|abstract\s+|"
    r"export\s+|final\s+|override\s+)*"
    r"(?:function|def|class|interface|struct|enum|trait|impl|fn|sub)\s+([A-Za-z_$][\w$]*)"
)
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
COMMENT_PREFIXES = ("//", "#", "/*", "*", "--", ";", "<!--")
LANG = {
    ".py": "python", ".php": "php", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust",
    ".java": "java", ".rb": "ruby", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp",
    ".cs": "csharp", ".sql": "sql", ".sh": "shell", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".md": "markdown", ".txt": "text",
}


def normalize(text: str, limit: int = 160) -> str:
    line = " ".join(str(text).split())
    while line.startswith(COMMENT_PREFIXES):
        line = line.split(" ", 1)[1].strip() if " " in line else ""
    return line[:limit]


def whole_address(path: str) -> str:
    """v1 мав константне `whole:` — тут адреса включає шлях."""
    return f"whole:{path}"


def node_key(repo_id: str, path: str, address: str) -> str:
    return "nd-" + sha256_text(repo_id + "\n" + path + "\n" + address)[:20]


def language_of(path: str) -> str:
    return LANG.get(Path(path).suffix.lower(), "text")


def _text_files(root: Path) -> list[str]:
    out: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        out.append(rel.as_posix())
    return sorted(out)


def _offsets(text: str) -> list[int]:
    out = [0]
    for line in text.splitlines(keepends=True):
        out.append(out[-1] + len(line.encode("utf-8")))
    return out


def _python_nodes(path: str, text: str) -> list[dict]:
    """stdlib ast: точні межі класів/функцій, ті самі адреси, що й у v1."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    offsets = _offsets(text)
    lines = text.splitlines()
    mod = Path(path).stem
    seen: dict[str, int] = {}
    out: list[dict] = []

    def addr(raw: str) -> str:
        seen[raw] = seen.get(raw, 0) + 1
        return raw if seen[raw] == 1 else f"{raw}#{seen[raw]}"

    def span(node: ast.AST) -> tuple[int, int]:
        start_line = max(1, int(getattr(node, "lineno", 1)))
        end_line = max(start_line, int(getattr(node, "end_lineno", start_line)))
        return offsets[start_line - 1], offsets[min(end_line, len(offsets) - 1)]

    def walk(node: ast.AST, owner: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                fqn = f"{mod}.{child.name}" if owner is None else f"{owner}.{child.name}"
                start, end = span(child)
                out.append({"address": addr(f"class:{fqn}"), "kind": "symbol", "unit": "byte",
                            "start": start, "end": end, "label": child.name, "precision": "ast"})
                walk(child, fqn)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start, end = span(child)
                raw = (f"method:{owner}.{child.name}" if owner else f"function:{mod}.{child.name}")
                out.append({"address": addr(raw), "kind": "symbol", "unit": "byte",
                            "start": start, "end": end, "label": child.name, "precision": "ast"})
                walk(child, owner)

    walk(tree, None)
    if not out or not lines:
        return out
    return out


def _lexical_nodes(path: str, text: str) -> list[dict]:
    """Будь-який текст: абзац із оголошенням або заголовок документа."""
    offsets = _offsets(text)
    lines = text.splitlines()
    out: list[dict] = []
    seen: set[str] = set()
    buf: list[str] = []
    start = 1
    for number, line in enumerate(lines, 1):
        heading = HEADING.match(line) if path.endswith((".md", ".markdown")) else None
        if heading:
            address = "section:" + normalize(heading.group(1), 80)
            if address not in seen and len(out) < MAX_NODES_PER_FILE:
                seen.add(address)
                out.append({"address": address, "kind": "section", "unit": "byte",
                            "start": offsets[number - 1], "end": offsets[min(number, len(offsets) - 1)],
                            "label": normalize(heading.group(1), 80), "precision": "lexical"})
            continue
        if line.strip():
            if not buf:
                start = number
            buf.append(line)
        elif buf:
            first = normalize(buf[0], 80)
            match = DECL.match(buf[0])
            if match and len(buf) >= 2:
                address = "symbol:" + normalize(match.group(1) or first, 80)
                if address not in seen and len(out) < MAX_NODES_PER_FILE:
                    seen.add(address)
                    end_line = start + len(buf) - 1
                    out.append({"address": address, "kind": "symbol", "unit": "byte",
                                "start": offsets[start - 1],
                                "end": offsets[min(end_line, len(offsets) - 1)],
                                "label": match.group(1) or first, "precision": "lexical"})
            buf = []
    return out


def nodes_of(path: str, data: bytes) -> list[dict]:
    """Деградація замість винятку: завжди повертає хоча б `whole:<path>`."""
    size = len(data)
    base = {"address": whole_address(path), "kind": "whole", "unit": "none",
            "start": 0, "end": 0, "label": Path(path).name, "precision": "file"}
    if Path(path).suffix.lower() in BINARY_EXT or size > MAX_FILE_BYTES or b"\0" in data:
        return [base]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [base]
    nodes = _python_nodes(path, text) if path.endswith(".py") else _lexical_nodes(path, text)
    if not nodes:
        return [base]
    return nodes[:MAX_NODES_PER_FILE] + [base]


def discover(repo_path: str, repo_id: str, paths: list[str] | None = None) -> list[dict]:
    root = Path(repo_path)
    files = list(paths) if paths is not None else _text_files(root)
    symbols: list[dict] = []
    for rel in sorted(set(files)):
        source = root / rel
        try:
            data = source.read_bytes()
        except OSError:
            continue
        for node in nodes_of(rel, data):
            chunk = data[int(node["start"]):int(node["end"])] if node["unit"] == "byte" else data
            fingerprint_text = (chunk or data).decode("utf-8", "replace")
            lines = data[:int(node["start"])].count(b"\n") + 1 if node["unit"] == "byte" else 1
            end_line = lines + chunk.count(b"\n") if node["unit"] == "byte" else max(1, data.count(b"\n") + 1)
            address = node["address"]
            symbols.append({
                "id": node_key(repo_id, rel, address),
                "stable_key": node_key(repo_id, rel, address),
                "language": language_of(rel),
                "qualified_name": address,
                "fingerprint": sha256_text(fingerprint_text),
                "body_fingerprint": sha256_text(fingerprint_text),
                "precision": node["precision"],
                "path": rel,
                "span": {"start_line": lines, "end_line": max(lines, end_line),
                         "start_byte": node["start"], "end_byte": node["end"]},
            })
    symbols.sort(key=lambda item: item["stable_key"])
    return symbols


def historical_files(repo_path: str, repo_id: str,
                     current_paths: set[str]) -> list[dict]:
    """Whole-file identities for paths that existed in Git but not at HEAD."""
    history = code.git.git(repo_path, "log", "--all", "--name-status",
                           "--format=COMMIT:%H", "--diff-filter=AMR")
    latest: dict[str, str] = {}
    commit = ""
    for line in history.splitlines():
        if line.startswith("COMMIT:"):
            commit = line[7:].strip()
            continue
        parts = line.split("\t")
        if len(parts) < 2 or not commit:
            continue
        rel = parts[-1]
        if rel not in current_paths and rel not in latest:
            latest[rel] = commit
    out = []
    for rel, revision in sorted(latest.items()):
        try:
            blob = code.git.git(repo_path, "rev-parse",
                                f"{revision}:{rel}").strip()
        except CairnError:
            continue
        address = whole_address(rel)
        key = node_key(repo_id, rel, address)
        out.append({"id": key, "stable_key": key, "language": language_of(rel),
                    "qualified_name": address, "fingerprint": blob,
                    "body_fingerprint": blob, "precision": "historical_file",
                    "revision": revision, "path": rel, "span": {}})
    return out


def index(store, repo_id: str, actor: dict, paths: list[str] | None = None,
          historical: bool = False) -> dict:
    repo = store.read_one("SELECT * FROM repositories WHERE id = ?", (repo_id,))
    if repo is None:
        raise CairnError("not_found", f"репозиторій {repo_id} не зареєстровано", exit_code=1)
    fresh = discover(repo["path"], repo_id, paths)
    if paths is None and historical:
        fresh += historical_files(repo["path"], repo_id,
                                  {item["path"] for item in fresh})
    # Проєкція `code.indexed` видаляє всі символи репозиторію перед вставкою
    # (projections.py:168-169). Частковий `--paths` без злиття стер би решту,
    # тому повертаємо в пейлоад і ті символи, яких ми не переіндексували.
    if paths is not None:
        touched = set(paths)
        kept = []
        for row in store.read("SELECT * FROM code_symbols WHERE repo_id = ?", (repo_id,)):
            if row["path"] in touched and not (
                    row["precision"] == "historical_file"
                    and not (Path(repo["path"]) / row["path"]).exists()):
                continue
            span = json.loads(row["span_json"] or "{}")
            kept.append({
                "id": row["stable_key"], "stable_key": row["stable_key"],
                "language": row["language"], "qualified_name": row["qualified_name"],
                "fingerprint": row["fingerprint"], "body_fingerprint": row["body_fingerprint"],
                "precision": row["precision"], "path": row["path"],
                "revision": row["revision"], "span": span,
            })
        symbols = kept + fresh
        symbols.sort(key=lambda item: item["stable_key"])
    else:
        symbols = fresh
    revision = code.git.head(repo["path"])
    payload = {
        "repo_id": repo_id, "revision": revision, "symbols": symbols, "edges": [],
        "extractor": "python-generic-v1-grammar/1.0",
        "capabilities": ("text.utf8", "v1.address-grammar", "python.ast", "git.revision", "file.span"),
        "files": sorted({s["path"] for s in symbols}),
        "index_digest": sha256_text(
            json.dumps([symbols, []], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ),
    }

    def op(con):
        # Re-importing the same session must not append an identical full
        # projection event to the shared ledger on every run.
        key = f"code-index:{repo_id}:{revision}:{payload['index_digest']}"
        ev = store.append(con, "code.indexed", repo_id, payload, actor, key)
        return {
            "repo_id": repo_id, "revision": revision, "symbols": len(symbols),
            "edges": 0, "files": len(payload["files"]),
            "index_digest": payload["index_digest"], "event_id": ev["event_id"],
            "replayed": ev["replayed"], "extractor": payload["extractor"],
        }

    return store.mutate(op)


def resolve(store, repo_id: str, path: str, line: int | None = None,
            address: str | None = None) -> list[dict]:
    rows = store.read(
        "SELECT stable_key, qualified_name, path, span_json, revision, precision "
        "FROM code_symbols WHERE repo_id = ? AND path = ? ORDER BY stable_key",
        (repo_id, path),
    )
    out = []
    for row in rows:
        if address and row["qualified_name"] != address:
            continue
        span = json.loads(row["span_json"] or "{}")
        if line is not None and not (int(span.get("start_line") or 1)
                                     <= line <= int(span.get("end_line") or 1)):
            continue
        out.append({
            "stable_key": row["stable_key"], "address": row["qualified_name"],
            "path": row["path"], "revision": row["revision"],
            "start_line": span.get("start_line"), "end_line": span.get("end_line"),
            "precision": row["precision"],
        })
    return out
