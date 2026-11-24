# Cairn

> [Українською](README.uk.md) · [For agents](#for-agents) · [Support the project](#support-the-project) · [Apache-2.0](LICENSE)

### An agent changes the code. Cairn keeps the reason.

In a new session, another agent can see the code and commits, but may not
know why an earlier change was made. The explanation may be in an old chat,
a compacted session, or on another device. The team then spends time
reconstructing the same decision, or repeats an old mistake.

**Cairn gathers the available evidence in one `.cairn` store.** It connects
a project node, a completed agent edit, its stated reason, and a Git commit.
Before editing, an agent can ask: *“How did this node change, and why?”*

```text
conversation and intent → node edit → Git commit
          ↓                  ↓            ↓
          └──────────── .cairn ──────────┘ → history for the next agent
```

Reading files, `grep`, and search do not create causal events. An
uncommitted edit remains a candidate. A commit confirms an edit's
participation only when its content transition can be matched uniquely in
Git.

## What the store contains today

In Cairn's development repository, **OpenCode, Claude Code, and Codex**
measured the history and contributed evidence from their sessions to the
same store. This snapshot was taken on **24 November 2026** at commit
`3159a6d`.

| Measure | Result |
|---|---:|
| Git commits in the store | **40/40** |
| Commits with confirmed session edits | **22** |
| Commits with Git context only | **18** |
| Unique edits linked to commits | **184** |
| Edit-to-commit links checked against Git | **205/205**, 0 failures |
| Events in `.cairn` and the verified private bundle | **1,839/1,839** |
| Full test suite | **37/37** |

For the control file `cairn_unified/why.py`, Cairn keeps 78 edit candidates.
Sixteen have a Git witness, two have a reason tied to a specific node edit,
and two have a reason for the file state. The other 62 have no unique commit
link. Cairn shows both the explanations and **where evidence is missing**.

These are participants' explanations tied to verified edits. They do not
prove that a decision was correct or identify who ran `git commit`. Cairn
does not invent a motive for old commits whose agent journals are missing.

## For agents

1. **Before editing**, read the current code and ask `why node` for its
   history. If the store is stale, sync it first.
2. **After an edit, before committing**, record a short `why` and `intent`
   with `change note`. Codex can refer to its completed `FileChange` event;
   other agents record only what their journals support.
3. **After committing**, run the causality sync. Cairn links an edit to Git
   through a unique content transition. A stash or discarded edit does not
   become confirmed node history.
4. **On another device**, carry the private event bundle and inspect it
   before importing. It contains the evidence itself, not links to local
   journal files.

The [agent mandate](MANDATE-AGENT-CAIRN-CAUSAL-HISTORY.md) has the commands
and evidence rules. [Agent onboarding](AGENT-ONBOARD.md) covers CLI and MCP.
The core package needs Python and has no external runtime dependencies.

## Release boundary

The current source tree has no machine-specific absolute paths. Twenty-six
older commits in the full Git history still do, so that history should not
be published without cleanup and migration of Cairn's Git identity. A clean
source snapshot without `.git` or `.cairn` is ready for publication. The
private bundle, which contains human and agent text, is not included.

## License

**[Apache-2.0](LICENSE)** permits commercial use. Keep the license and
`NOTICE` with the code.

## Support the project

If Cairn helps you and you would like to support its development:

```text
Network: TRON (TRC-20)
Address: TZ5veYdk67o3dWNHHLRaBpdXw3fJHt3w5V
```

<img src="docs/donate-tron.png" alt="TRON QR code to support Cairn" width="180">

Developed with the support of [CommerCentr](https://commercentr.com/en).
