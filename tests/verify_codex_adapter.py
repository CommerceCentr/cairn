"""Regression checks for Codex rollout normalization and source boundaries."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cairn_unified.adapters import codex, normalize

FIXTURE_REPO = Path(__file__).resolve().parents[1].as_posix()


def main() -> int:
    rows = [
        {"timestamp": "2026-11-22T00:00:00Z", "type": "session_meta",
         "payload": {"session_id": "test-session", "cwd": FIXTURE_REPO}},
        {"timestamp": "2026-11-22T00:00:01Z", "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"text": "Actual user intent"}]}},
        {"timestamp": "2026-11-22T00:00:02Z", "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"text": "<environment_context>internal"}]}},
        {"timestamp": "2026-11-22T00:00:03Z", "type": "compacted",
         "payload": {"retained_context": {"summary": "first"}}},
        {"timestamp": "2026-11-22T00:00:04Z", "type": "compacted",
         "payload": {"retained_context": {"summary": "second"}}},
        {"timestamp": "2026-11-22T00:00:05Z", "type": "response_item",
         "payload": {"type": "custom_tool_call", "name": "exec",
                     "input": "Set-Content file.py value"}},
        {"timestamp": "2026-11-22T00:00:06Z", "type": "response_item",
         "payload": {"type": "compaction", "id": "cmp-encrypted",
                     "encrypted_content": "opaque"}},
        {"timestamp": "2026-11-22T00:00:07Z", "type": "event_msg",
         "payload": {"type": "item_completed", "item": {"id": "edit-1",
                     "type": "FileChange", "status": "completed",
                     "changes": {f"{FIXTURE_REPO}/a.py": {
                         "type": "update", "unified_diff": "@@ -1 +1 @@\n-old\n+new\n"}}}}},
        {"timestamp": "2026-11-22T00:00:08Z", "type": "event_msg",
         "payload": {"type": "item_completed", "item": {"id": "edit-failed",
                     "type": "FileChange", "status": "failed",
                     "changes": {f"{FIXTURE_REPO}/b.py": {"type": "update"}}}}},
    ]
    original = codex._lines
    codex._lines = lambda _path: iter(json.dumps(row) for row in rows)
    try:
        raw = list(codex.read("rollout-fixture.jsonl"))
        events = normalize.canonical_events(raw)
    finally:
        codex._lines = original
    assert len(raw) == 5
    assert len({event["event_id"] for event in raw}) == 5
    assert [event["kind"] for event in events] == [
        "user_intent", "compaction", "compaction", "compaction", "mutation"]
    assert events[0]["text"] == "Actual user intent"
    assert events[-2]["summary"] == ""
    assert events[-2]["reason"] == "encrypted_compaction"
    assert events[-1]["evidence_scope"] == "file"
    assert events[-1]["patch"] == "@@ -1 +1 @@\n-old\n+new\n"
    original_discover = codex.discover
    original_lines = codex._lines
    codex.discover = lambda: ["rollout-fixture.jsonl"]
    codex._lines = lambda _path: iter(json.dumps(row) for row in rows)
    try:
        recent = codex.recent_edits(FIXTURE_REPO, "a.py")
    finally:
        codex.discover = original_discover
        codex._lines = original_lines
    assert len(recent) == 1
    assert recent[0]["session_id"] == "test-session"
    assert recent[0]["event_id"] == "edit-1"
    assert recent[0]["path"] == "a.py"
    assert normalize.capabilities(normalize.SOURCE_CODEX)["mutation"].startswith(
        "partial")
    print("ВЕРДИКТ: Codex IDs, user intent, compactions and shell boundary pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
