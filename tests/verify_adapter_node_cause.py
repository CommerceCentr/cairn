"""Per-adapter boundary for a node-level cause (mandate §7.5).

The journal readers are covered by verify_sources.py. This test fixes the
decision each adapter's evidence must produce once a Git content witness is
present: same-event reasoning attests the node, a neighbouring rationale or an
encrypted/missing rationale does not, and a candidate without a commit witness
never enters confirmed history.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fixture_repo import PY_FILE, make_repo  # noqa: E402
from cairn_unified import code, compaction_link, nodes, semantic, why  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

ACTOR = {"kind": "system", "id": "adapter-cause-fixture"}
CASES = (
    ("opencode", "agent_reasoning_same_event", "same-event motive", 1),
    ("claude_code", "agent_reasoning_prior_by_seq", "neighbouring motive", 0),
    ("codex", "none", "", 0),
)


def check_case(base: Path, source: str, rationale_source: str,
               motive: str, expected: int) -> list[str]:
    repo = make_repo(base / source,
                     files={PY_FILE: "def run():\n    return 1\n"},
                     second={PY_FILE: "def run():\n    return 2\n"})
    Store(base / f"{source}-store").init()
    store = Store.open(base / f"{source}-store")
    ident = code.git.identity(repo["path"])
    store.mutate(lambda con: store.append(
        con, "repository.upserted", ident["id"], ident, ACTOR,
        f"repo:{ident['id']}"))
    nodes.index(store, ident["id"], ACTOR, paths=[PY_FILE])
    found = nodes.resolve(store, ident["id"], PY_FILE)
    whole = next(node["stable_key"] for node in found
                 if str(node["address"]).startswith("whole:"))
    symbol = next(node["stable_key"] for node in found
                  if not str(node["address"]).startswith("whole:"))
    fact = compaction_link.link_commit(
        store, repo_id=ident["id"], repo_path=repo["path"],
        commit=repo["head"], work_id="", actor=ACTOR)["fact_id"]
    session = semantic.put_object(
        store, object_type="session", title=source, summary="", actor=ACTOR,
        stable_key=f"fixture-{source}",
        semantic={"kind": "session", "session_id": source,
                  "source_kind": source, "commit": repo["head"]})["object"]["id"]
    semantic.relate(store, from_id=session, to_id=fact,
                    relation="context_for_commit", actor=ACTOR)
    event_id = f"edit-{source}"
    for node_key in (whole, symbol):
        semantic.relate(
            store, from_id=node_key, to_id=session, relation="caused_by",
            actor=ACTOR, condition={
                "basis": "tool_mutation",
                "evidence": {"event_id": event_id, "path": PY_FILE,
                             "rationale_source": rationale_source,
                             "intent": "user intent"},
                "rationale": motive})
    semantic.relate(store, from_id=session, to_id=fact,
                    relation="mutation_in_commit", actor=ACTOR, condition={
                        "event_id": event_id, "node_key": whole,
                        "path": PY_FILE, "evidence_level": "content_transition"})
    semantic.relate(
        store, from_id=whole, to_id=session, relation="caused_by", actor=ACTOR,
        condition={"basis": "tool_mutation",
                   "evidence": {"event_id": f"discarded-{source}",
                                "path": PY_FILE,
                                "rationale_source": "agent_reasoning_same_event",
                                "commit_state": "discarded"},
                   "rationale": "a discarded edit keeps its own reason"})
    answer = why.answer(store, repo_id=ident["id"], path=PY_FILE,
                        commit=repo["head"], repo_path=repo["path"])
    coverage = answer["coverage"]
    attested = [item for item in answer["why_detail"]
                if item.get("semantic_cause_status") == "node_attested"]
    errors = []
    if coverage["node_attested_causes"] != expected:
        errors.append(f"{source}: node_attested {coverage['node_attested_causes']} != {expected}")
    if any(item.get("node_key") != whole for item in attested):
        errors.append(f"{source}: attested a node other than the changed whole file")
    if any(str(item.get("event_id") or "").startswith("discarded-") for item in attested):
        errors.append(f"{source}: a discarded candidate became attested")
    if expected and motive not in answer["answer"]:
        errors.append(f"{source}: motive text missing from the answer")
    return errors


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="adapter-node-cause-") as temp:
        errors = []
        for source, rationale_source, motive, expected in CASES:
            errors.extend(check_case(Path(temp), source, rationale_source,
                                     motive, expected))
        for error in errors:
            print(error)
        if errors:
            return 1
        print("PASS: opencode=1 node_attested; claude_code/codex=0; "
              "only the changed node; discarded candidate excluded")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
