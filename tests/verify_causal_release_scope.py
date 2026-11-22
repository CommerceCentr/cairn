"""Release gate: foreign sources fail closed and stale answers say so."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fixture_repo import PY_FILE, make_repo  # noqa: E402
from cairn_unified import code, nodes, why  # noqa: E402
from cairn_unified.errors import CairnError  # noqa: E402
from cairn_unified.store import Store  # noqa: E402
from tools.causal_scope import require_exclusive_store, require_sources  # noqa: E402
from tools.audit_release_evidence import foreign_path_count  # noqa: E402
from tools.index_agent_history import mixed_mutation_paths  # noqa: E402

ACTOR = {"kind": "system", "id": "scope-test"}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="cairn-release-scope-") as temp:
        base = Path(temp)
        repo = make_repo(base / "cairn", files={PY_FILE: "x = 1\n"},
                         second={PY_FILE: "x = 2\n"})
        foreign = make_repo(base / "foreign", files={PY_FILE: "y = 1\n"},
                            second={PY_FILE: "y = 2\n"})
        target = repo["path"]
        other = foreign["path"]
        Store(base / "store").init()
        store = Store.open(base / "store")
        ident = code.git.identity(target)
        store.mutate(lambda con: store.append(
            con, "repository.upserted", ident["id"], ident, ACTOR,
            f"repo:{ident['id']}"))
        assert require_sources(target, [target]) == ident["id"]
        assert require_exclusive_store(store, ident["id"])["exclusive"]
        try:
            require_sources(target, [other])
            raise AssertionError("foreign source accepted")
        except CairnError as exc:
            assert exc.code == "source_repo_mismatch"
        foreign_ident = code.git.identity(other)
        store.mutate(lambda con: store.append(
            con, "repository.upserted", foreign_ident["id"], foreign_ident,
            ACTOR, f"repo:{foreign_ident['id']}"))
        try:
            require_exclusive_store(store, ident["id"])
            raise AssertionError("mixed-zone export accepted")
        except CairnError as exc:
            assert exc.code == "bundle_zone_mismatch"
        assert mixed_mutation_paths([
            {"kind": "mutation", "status": "completed",
             "path": str(Path(other) / PY_FILE)}], Path(target))
        assert not mixed_mutation_paths([
            {"kind": "mutation", "status": "completed",
             "path": str(Path(target) / PY_FILE)}], Path(target))
        assert foreign_path_count({"events": [{"path": str(Path(other) / PY_FILE)}]},
                                  [Path(other)]) == 1
        assert foreign_path_count({"events": [{"path": str(Path(target) / PY_FILE)}]},
                                  [Path(other)]) == 0
        nodes.index(store, ident["id"], ACTOR, paths=[PY_FILE])
        answer = why.answer(store, repo_id=ident["id"], path=PY_FILE,
                            commit=repo["head"], repo_path=str(target))
        assert answer["freshness"]["status"] == "stale"
        assert answer["answer"].startswith("СХОВИЩЕ ЗАСТАРІЛЕ")
        # A large deleted patch must fit a bounded answer while retaining
        # the total line count and an explicit truncation note.
        bounded = why._apply_budget({
            "answer": "КОЛИ Git доводить правку\nМЕЖА участь не є мотивом",
            "boundary": "участь не є мотивом", "omissions": [],
            "chronology": [], "provenance": [], "trust": [], "why_detail": [],
            "commit_history": [{"commit": repo["head"], "message": "reason",
                                "patches": [{"path": PY_FILE,
                                             "deleted_lines": ["old " + "x" * 120] * 80,
                                             "added_lines": ["new " + "y" * 120] * 80}],
                                "node_changes": [], "mutations_for_node": [],
                                "direct_mutations": 1}],
        }, 8000)
        assert len(__import__("json").dumps(bounded, ensure_ascii=False)) < 7750
        assert bounded["commit_history"][0]["patches"][0]["deleted_lines_total"] == 80
        assert not any(x["kind"] == "budget_exceeded" for x in bounded["omissions"])
        proc = subprocess.run(
            [sys.executable, "-m", "cairn_unified", "--root", str(base / "store"),
             "project", "doctor"], cwd=ROOT, capture_output=True,
            env={**__import__("os").environ, "PYTHONIOENCODING": "cp1251"})
        assert proc.returncode == 0, proc.stderr.decode("ascii", errors="replace")
        assert b'"ok": true' in proc.stdout
    print("PASS: source identity, mixed sessions, export, stale why, budget, CP1251")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
