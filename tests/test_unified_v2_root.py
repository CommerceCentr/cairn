"""Коренева міграція: v2 як єдиний пакет, жодних v1-залежностей.

Автономний тест: перевіряє розкладку кореня, відсутність імпортів `core`,
версію, редакцію секретів і те, що бюджет контексту лишається явним.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import context, redact, work  # noqa: E402
import cairn_unified  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

H = {"kind": "human", "id": "root-observer"}


def main() -> int:
    checks: list[tuple[str, bool]] = []

    checks.append(("пакет cairn_unified лежить у корені",
                   (ROOT / "cairn_unified" / "__init__.py").is_file()))
    checks.append(("src/ і v1 CLI відсутні",
                   not (ROOT / "src").exists() and not (ROOT / "cairn.py").exists()))
    checks.append(("v1-каталоги core/extract/bind відсутні",
                   all(not (ROOT / d).exists() for d in ("core", "extract", "bind"))))

    stale = []
    for p in (ROOT / "cairn_unified").rglob("*.py"):
        for line in p.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("from core", "import core")):
                stale.append(f"{p.name}: {stripped}")
    checks.append(("жодного імпорту core у v2", not stale))

    checks.append(("версія — 2.0.0 (v3 лише специфікація)",
                   cairn_unified.__version__ == "2.0.0"))

    secret = "sk-ant-" + "A" * 20
    cleaned, counts = redact.redact_text(f"KEY={secret} PATH={ROOT}")
    checks.append(("секрет ріжеться, шлях лишається координатою",
                   secret not in cleaned and "ANTHROPIC_KEY" in counts
                   and str(ROOT) in cleaned))

    with tempfile.TemporaryDirectory(prefix="cairn-root-") as tmp:
        store = Store(Path(tmp))
        store.init()
        store = Store.open(Path(tmp))
        wi = work.create_work(
            store, title="root migration", actor=H,
            scope={"repo_id": "repo-root-check"})["work"]
        tiny = context.build(store, wi["id"], purpose="understand",
                             budget_chars=80, use_cache=False)
        wide = context.build(store, wi["id"], purpose="understand",
                             budget_chars=8000, use_cache=False)
        checks.append(("бюджет контексту явний і змінює пакет",
                       tiny["budget"]["limit"] == 80
                       and wide["budget"]["limit"] == 8000
                       and tiny["context_id"] != wide["context_id"]))

    bad = [label for label, ok in checks if not ok]
    for label, ok in checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if stale:
        print("  stale imports:", stale[:5])
    print("ВЕРДИКТ: " + (f"FAIL ({len(checks) - len(bad)}/{len(checks)})"
                        if bad else f"корінь належить v2 ({len(checks)}/{len(checks)})"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
