# Cairn

> [Українською](README.uk.md) · [Agent mandate](MANDATE-AGENT-CAIRN-CAUSAL-HISTORY.md) · [Apache-2.0](LICENSE)

Cairn keeps the reasons behind code changes available across agents and
devices. It connects Git commits, project nodes, completed agent edits, and
available session context in a local `.cairn` store. Reading and searching
files do not create causal events.

## Measured in Cairn's development repository

Snapshot: **2026-11-24**, HEAD `807b252`. **OpenCode, Claude Code, and Codex**
measured this repository and contributed evidence from their sessions to the
same store.

| Check | Current result |
|---|---:|
| Git commits indexed | **39/39** |
| Commits with confirmed session edits / Git context only | **22 / 17** |
| Unique edits linked to commits | **184** |
| Stored edit-to-commit links verified | **205/205**, 0 failures; 21 duplicate links |
| Events in the store and verified private bundle | **1,831 / 1,831** |
| Session records: Claude Code / Codex / OpenCode | **3 / 2 / 3** |
| Full test suite | **37/37** passed |

For the control file `cairn_unified/why.py`, Cairn records 78 edit candidates:
16 have a Git witness, 2 have an attested event-level reason, 2 have a
file-state reason, and 62 remain unlinked. Another node, `ontology.py`, has
one attested event-level reason. These are **participant statements tied to
verified edits**, not proof that a decision was correct or that the agent
executed `git commit`. The 17 Git-only commits remain visible without an
invented session or motive.

## Agent workflow

1. Before editing, read the current code and ask `why node` for its history.
   If `freshness.status` is `stale`, sync first.
2. After a completed edit and before the commit, record a short `change note`
   with `why` and `intent`. A Codex agent can obtain its completed
   `FileChange` reference with `change codex-events` and attach `session_id`
   and `event_id` to a single-file note. Other adapters use the evidence
   their journals actually contain.
3. After the commit, run the project causality sync. Only a unique Git content
   transition promotes an edit into confirmed history. Stashed or discarded
   edits remain candidates. Check `why node`, `change status`, `project doctor`,
   and the Git witness verifier.
4. Export the updated private bundle for another device. It carries the
   recorded evidence, not links to local journal files. Keep it outside public
   Git because it contains human and agent text.

The [agent mandate](MANDATE-AGENT-CAIRN-CAUSAL-HISTORY.md) has the commands
and evidence rules. [Agent onboarding](AGENT-ONBOARD.md) covers CLI and MCP.

## Release boundary

The current source tree has no machine-specific absolute paths. **26 older
commits in the full Git history still contain them**, so the full `main`
history is not cleared for public release. A clean source snapshot without
`.git` or `.cairn` is available as the release route. Publishing a cleaned
full history would also require migrating Cairn's Git identity and evidence.
The private bundle is not part of the public source snapshot.

## License

[Apache-2.0](LICENSE). Developed with the support of
[CommerCentr](https://commercentr.com/en).
