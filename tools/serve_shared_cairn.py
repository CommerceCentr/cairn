"""Launch Cairn's stdio MCP server from any Codex working directory.

Usage: python Q:/prj/cairn/tools/serve_shared_cairn.py Q:/prj/cairn-private
The source package is resolved relative to this file, not the agent's cwd.
"""

from __future__ import annotations

import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

from cairn_unified.cli import main  # noqa: E402


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: serve_shared_cairn.py <project-root>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(["--root", str(Path(sys.argv[1]).resolve()),
                           "mcp", "serve"]))
