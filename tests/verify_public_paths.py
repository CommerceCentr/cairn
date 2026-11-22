"""Release gate: public text must not contain machine-specific paths."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The second slash check excludes URL schemes such as https://.
ABSOLUTE = re.compile(
    r"(?i)(?<![a-z])[a-z]:[\\/](?!/)|/(?:home|Users|mnt)/[^\s/]+/"
)


def main() -> int:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, check=True,
    )
    findings = []
    checked = 0
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode("utf-8", errors="surrogateescape")
        path = ROOT / relative
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # Binary assets are outside this text-only gate.
        checked += 1
        for match in ABSOLUTE.finditer(content):
            findings.append((relative, content.count("\n", 0, match.start()) + 1))
    for name, line in findings[:20]:
        print(f"FAIL {name}:{line}: absolute machine path")
    print(f"ВЕРДИКТ: публічні текстові шляхи ({checked} файлів, {len(findings)} збігів)")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
