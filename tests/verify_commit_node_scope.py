"""A committed edit must not mark unchanged sibling symbols as mutated."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cairn_unified import nodes
from cairn_unified.code import git
from cairn_unified.compaction_link import committed_nodes, mutation_readiness
from cairn_unified.store import Store


def run(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True)
    return proc.stdout.strip()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="cairn-commit-scope-") as temp:
        repo = Path(temp) / "repo"
        repo.mkdir()
        run(repo, "init", "-q")
        run(repo, "config", "user.email", "test@example.invalid")
        run(repo, "config", "user.name", "Test")
        source = repo / "sample.py"
        source.write_text("def changed():\n    return 1\n\ndef untouched():\n    return 2\n")
        run(repo, "add", ".")
        run(repo, "commit", "-qm", "initial")
        source.write_text("def changed():\n    return 3\n\ndef untouched():\n    return 2\n")
        run(repo, "add", ".")
        run(repo, "commit", "-qm", "edit one function")
        store = Store(Path(temp) / "project")
        store.init()
        actor = {"kind": "system", "id": "test"}
        ident = git.identity(str(repo))
        store.mutate(lambda con: store.append(
            con, "repository.upserted", ident["id"], ident, actor,
            f"repo:{ident['id']}"))
        nodes.index(store, ident["id"], actor)
        selected = committed_nodes(store, ident["id"], repo, git.head(repo),
                                   "sample.py")
        addresses = {item["address"] for item in selected}
        assert "function:sample.changed" in addresses, addresses
        assert "function:sample.untouched" not in addresses, addresses
        ready, rejected = mutation_readiness(
            store, [{"kind": "mutation", "event_id": "file-change-1",
                     "status": "completed", "path": str(source),
                     "evidence_scope": "file"}], ident["id"], repo,
            {item["stable_key"] for item in selected})
        assert not rejected and len(ready) == 1, rejected
        assert [node["address"] for node in ready[0][2]] == ["whole:sample.py"]
        source.write_text("\n" + source.read_text())
        dirty = committed_nodes(store, ident["id"], repo, git.head(repo),
                                "sample.py")
        assert {item["address"] for item in dirty} == {"whole:sample.py"}
        print("ВЕРДИКТ: Git hunk selects changed function, excludes sibling")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
