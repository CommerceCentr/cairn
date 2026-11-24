# Cairn

> [Українською](README.uk.md) · [For agents](#a-working-rule-for-any-agent) · [Evidence](docs/CAIRN_RELEASE_EVIDENCE.md) · [Apache-2.0](LICENSE)

**Cairn keeps the history behind a code change available to the next agent.**

Git tells you what changed. A chat may explain why, but the agent that wrote
the code might be gone, its context compacted, or its session on another
device. Cairn joins the Git history, project nodes, and available agent
sessions in a local `.cairn` store. An agent can ask about a file before
changing it and see earlier decisions, confirmed edits, and removed text.

The tool works with any Git repository. The numbers below come from the Cairn
repository itself, used as the first release test zone.

## What changes for a team of agents

```text
Human and agent agree on an edit
    → agent changes a file → Git commit, now or later
    → session may be compacted → Cairn records the available evidence
    → another agent asks about the same file before editing it
```

OpenCode, Claude Code, and Codex can write into the same project store through
their journal adapters. Cairn reads **completed edits**, not `grep`, file
reads, or a command that merely says an edit will happen. It stores a session
summary when one exists, and says when it does not. A content match can link
an edit to a later commit even when the commit was made in another session.

For example, `cairn_unified/why.py` has seven Git commits in the measured
history. For `dc0f95e`, Cairn found one confirmed session edit and five
removed lines. For `17dfc19`, it found four confirmed edits and 33 removed
lines. The next agent can inspect those changes together with the recorded
intent and see where the evidence stops. A content match proves that an edit
participated in a commit; it does not prove the author's motive or who ran
`git commit`.

## Measured on this repository

Snapshot: `dc0f95eee70f0ba31e18555e96b3f64adeca6ef5`, 2026-11-22.
The full method and machine-readable results are in
[the release evidence](docs/CAIRN_RELEASE_EVIDENCE.md) and
[its JSON record](docs/CAIRN_RELEASE_EVIDENCE.json).

| Check | Result |
|---|---:|
| Git commits present in `.cairn` | **26/26** |
| Historical commits for `cairn_unified/why.py` | **7/7** |
| Commits with confirmed session edits | **10/26** |
| Commits with Git context only | **16/26** |
| Unique edits linked to a commit | **94** |
| Stored edit-to-commit links verified against Git | **115/115**, including 21 older duplicate links |
| Repeating the sync without new source changes | **0** new events; same bundle digest |
| Full test suite | **31/31** passed; one private optional test skipped |

The `10/26` figure is **not** a count of Claude Code commits. Among those
ten commits, two have confirmed Claude Code edits, four have OpenCode edits,
and seven have Codex edits. Some commits include edits from more than one
agent, so these counts overlap. The other 16 commits are real Git changes,
but no completed journal edit has been uniquely matched to them. Git author
names do not identify which agent made an edit.

Here is the amount of text returned in two fixed investigations of `why.py`.
The token count is the same rough proxy for both paths: **characters ÷ 4**.

| Question | Without Cairn: Git output read by the script | With Cairn: all measured calls | Cairn's final `why` answer |
|---|---:|---:|---:|
| Why `dc0f95e` changed the file | ~52,113 tokens | ~2,351 tokens | ~1,546 tokens |
| Why `17dfc19` changed the file | ~52,277 tokens | ~2,705 tokens | ~1,900 tokens |

The **with-Cairn path returned about 19–22 times less text** than that
particular Git investigation. The Git path read `log -1`, file status, 12
patches, and `blame`; it produced raw evidence, **not** an independently
written answer of equal quality. A careful agent could read less Git output.
This measurement therefore shows the size of these two paths, not a universal
token saving. Cairn took roughly 0.83–0.91 seconds locally; the Git commands
took about 0.19 seconds. We do not claim a speed gain.

The larger benefit is continuity: the next agent gets a typed history from
all indexed agents instead of reconstructing which chat, edit, and commit
belong together. Coverage is visible rather than implied: **16 of 26 commits**
still have only Git context.

## A working rule for any agent

Use these names in the commands below:

- `<REPO>`: the Git project whose history you want;
- `<ROOT>`: the folder containing that project's `.cairn` store;
- `<CAIRN>`: a checkout of the Cairn code, with `tools/`;
- `<BUNDLE>`: a **private** export path outside public Git;
- `<WORKTREE>`: an optional second checkout of the **same** Git repository.

1. **Before editing a file**, check the Git HEAD and read its history. If
   `freshness.status` is `stale`, sync first. Read the current code too: Cairn
   does not return it.

   ```text
   git -C <REPO> rev-parse HEAD
   python -m cairn_unified --root <ROOT> why node <relative/path> --repo-path <REPO>
   ```

   The default response is a compact overview of commits, reasons, and evidence limits.
   The compact `answer` already shows some deleted lines. Add `--full`
   for every deleted line in structured `patches` and evidence provenance.

2. **After an edit in the current session**, record one short reason per
   related file set. Use CLI `change note` or MCP `cairn_change_note`; Cairn
   stores the text in `.cairn`. The journal indexer separately captures
   structured edits. Stash or discard does not confirm these candidates.
   **After a commit**, run the full sync. Only an exact, unambiguous Git
   blob match admits the reason to node history.
   Add each available worktree as another `--source-repo`.

   ```text
   python -m cairn_unified --root <ROOT> project init
   python -m cairn_unified --root <ROOT> repo add <REPO>
   python -m cairn_unified --root <ROOT> --actor agent:<AGENT> change note --repo-path <REPO> --path <relative/path> --why "<why>" --intent "<intent>"
   python tools/index_agent_history.py --root <ROOT> --repo <REPO> --apply
   python tools/sync_project_causality.py --root <ROOT> --repo <REPO> --source-repo <REPO> --bundle <BUNDLE>
   ```

   Record the note **after the edit and before the commit**. A later edit
   to the same file needs a new note. The commit must transition from the
   file state at the note's base HEAD to the captured state; a later revert
   to the same text does not inherit that reason. Multiple reasons for
   one exact file state remain ambiguous.

   For a Codex `FileChange`, use `change codex-events --repo-path <REPO>
   --path <relative/path>` to read its `session_id` and `event_id`. Add
   `--session-id <ID> --codex-event-id <EVENT_ID>` to a single-file
   `change note`. Cairn treats this as a node-level *agent statement* only
   when the same session's edit has a unique Git content witness in that
   commit. Without that event reference, the note remains file-scoped.

   `why node` shows confirmed history; `--include-pending --full`
   separately exposes all candidates without a proven commit link.
   `change status --repo-path <REPO>` reports the current state per path.
   `unlinked` means no recorded link; a commit may already exist if sync
   has not run since that commit.

3. **Check what was proved.** A Git-only commit still changed the file, but
   no agent edit has been tied to it. Do not invent a missing session or
   treat a later compaction as proof of the earlier motive.

   ```text
   python tools/link_mutations_to_git.py --root <ROOT> --repo <REPO> --verify
   python -m cairn_unified --root <ROOT> project doctor
   python -m cairn_unified --root <ROOT> why node <relative/path> --repo-path <REPO> --commit <SHA>
   ```

4. **On another device**, restore the private bundle into a clone of the
   same Git history, then sync that device's local agent journals. Keep the
   bundle private: it contains human and agent text. A full bundle export is
   refused when a store contains more than one repository zone.

   ```text
   python -m cairn_unified federation restore <BUNDLE> <CLEAN_CLONE>
   python -m cairn_unified --root <CLEAN_CLONE> project doctor
   ```

The [agent mandate](MANDATE-AGENT-CAIRN-CAUSAL-HISTORY.md) gives the exact
evidence rules. [Agent onboarding](AGENT-ONBOARD.md) and
[integration notes](docs/INTEGRATION.md) cover CLI and MCP setup. On Windows
PowerShell, set `PYTHONIOENCODING=utf-8` if the console cannot print Unicode.

## How it is stored

`.cairn` is local. An append-only event ledger is the source of truth;
SQLite is a rebuildable read model. Git supplies commit identity and file
diffs. Agent adapters read local journals and keep only the available causal
evidence. Normal reading and search do not add history events. The private
federation bundle carries the event data to another device, not symbolic links
to local journal files. The package uses Python's standard library and exposes
a CLI plus 12 MCP tools.

## Current limits

- Session evidence is incomplete: only 10 of the measured 26 commits have
  confirmed agent edits. For the others, Cairn reports Git facts without
  guessing a cause.
- An agent account name in a journal is not cryptographic proof of identity.
- Local restore and repeat import were checked. A real two-device merge with
  different new commits and the supported Unix platform still need release
  tests.
- A public release contains the numeric evidence report. Raw sessions and the
  portable bundle remain private. A store with several repository zones needs
  a separate scoped exporter before its full bundle can be shared safely.
- The current text tree contains no machine-specific paths, but all 26 older
  Git commits still contain them. Publishing the full Git history requires
  a separate history-cleanup decision; the release audit currently fails this
  gate. A source snapshot without `.git` avoids distributing that history.

See [architecture](docs/ARCHITECTURE.md) and the
[release evidence](docs/CAIRN_RELEASE_EVIDENCE.md) for details.

## License and support

[Apache-2.0](LICENSE). Developed with the support of
[CommerCentr](https://commercentr.com/en).
