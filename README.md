# Cairn

> 🇺🇦 **Українською: [README.uk.md](README.uk.md)**
> &nbsp;&nbsp;|&nbsp;&nbsp; License: [Apache-2.0](LICENSE)
> &nbsp;&nbsp;|&nbsp;&nbsp; Zero dependencies
> &nbsp;&nbsp;|&nbsp;&nbsp; [For agents](#for-agents)
> &nbsp;&nbsp;|&nbsp;&nbsp; [Support](#support-the-project)

### Your AI agent forgets everything between sessions. Cairn remembers.

Close the terminal, and tomorrow your agent starts from zero. It re-reads
the same files, suggests the thing you rejected yesterday, and spends
tokens rebuilding what it already knew.

**Cairn keeps those conversations and gives them back when they matter.**
Ask *"why is this file like this?"* and get the answer in **300–1 700
tokens** instead of the 12 000–240 000 it takes to dig through git.

Developed with the support of
**[CommerCentr](https://commercentr.com/en)**.

---

## The problem, and the price

History is the first thing cut from a context window — not because it is
useless, but because it is enormous. Both files below are real.

| To answer *"why is this like this"* the agent reads | ≈ tokens | with Cairn | saved |
|---|---:|---:|---:|
| **large file** (632 KB, 166 commits) — the file itself | 162 000 | — | *reading the code is still yours* |
| &nbsp;&nbsp;its commit messages (`git log`) | 42 000 | 290 | **99.3 %** |
| &nbsp;&nbsp;full diffs (`git log -p`) — where removed prose lives | 240 000 | 1 660 | **99.3 %** |
| **typical file** (10.7 KB, 48 commits) — `git log` | 11 700 | 384 | **96.7 %** |
| &nbsp;&nbsp;`git log -p` | 41 000 | 1 660 | **96.0 %** |

The `-p` rows are the whole problem: the place where the explanation
actually lives does not fit into any mainstream context window. So the
agent skims, guesses, or re-derives a decision that was already made and
reverted. On one class we counted **26 edits in four months**, four of
them fixing the same defect, none recording why.

Cairn's cost **does not grow with history** — the budget bounds it. At
five lookups per session (measured: 21 files touched per session, 2
sessions per week) that is ≈**57 K tokens a session, 113 K a week,
1.36 M a quarter** — window returned to the task, not money saved.

*(Tokens are estimated as characters ÷ 4; byte counts are measured.)*

---

## How it works

```text
   agent session journals ----+
                              |
   git history ---------------+--->  .cairn/  --->  cairn2 why node <path>
                                     Event Ledger + SQLite  cairn2 why node <path>
                                     local, yours           cairn2 federation import
```

```bash
python -m cairn_unified project init
python -m cairn_unified repo add <path>
python -m cairn_unified code index <repo_id>
python -m cairn_unified context compile <work_id> --budget 8000
python -m cairn_unified why node <path> --commit <sha>   # when and why
python -m cairn_unified mcp tools                  # 12 agent tools
```

**Python standard library only.** The Event Ledger is the source of truth;
SQLite is a projection rebuilt from it. No daemon, no cloud.

```text
1  ADDRESS CONTENT, NOT POSITION
   history attaches to `method:Ns\Class::name`, a heading path, a
   timecode — things that survive edits, unlike line numbers

2  STORE THE COMPACTED CONTEXT, NOT THE TRANSCRIPT
   agents already compact their own context when the window fills.
   That compacted context IS the "why", pre-written and pre-shrunk

3  BOUND THE ANSWER BEFORE PRODUCING IT
   every query estimates its own cost first, then applies a policy:
   trim, ask, or allow-and-say-so
```

---

## For agents

Structural facts an agent needs before analysing a repository with Cairn.
Full onboarding: [`AGENT-ONBOARD.md`](AGENT-ONBOARD.md).

```text
WHEN TO ASK   before editing an unfamiliar node; when the code looks
              wrong but deliberate; when a fix keeps coming back
WHAT YOU GET  a bounded context package: past sessions' compacted contexts,
              active decisions and governing evidence, newest first
WHAT YOU DO   NOT get: current code (read it yourself), documentation,
              or a semantic-causal claim — the link proves time/path only
```

| kind | unit | example address | what it is |
|---|---|---|---|
| `symbol` | byte | `symbol:run_all` | class, method, function (normalized name) |
| `section` | byte | `section:Intro/Limits` | heading and its text |
| `page` | page | `page:` | page of a document |
| `segment` | second | `segment:00:04:12` | audio or video range |
| `whole` | none | `whole:src/app.py` | the file; v2 includes the path |

```bash
python -m cairn_unified project doctor                     # store health
python -m cairn_unified context compile <work_id> --budget 2000
python -m cairn_unified code nodes <repo_id>               # what is indexed
python -m cairn_unified mcp tools                          # 12 agent tools
```

Semantic queries go through `cairn_semantic_query` (MCP); the amount of
context is bounded by `--budget` in **characters**, not tokens.

```text
--budget   characters, not tokens; default 8 000
           context compile also accepts --agent, --purpose, --as-of, --persist

exit 0  success
exit 1  not found — an ANSWER, not a failure
exit 2  usage / contract error (unknown argument, unknown tool)
exit 3  domain error (default CairnError)
exit 4  refusal: stale_fence, corrupted bundle, merge_refused
```

Exit 1 is the one to get right. It does not mean the tool broke; it
means the history is silent, and silence must not be filled with a
guess.

---

## When and why was this node edited

> **Plain-language benefits, with measurements:**
> [`docs/WHY-CAIRN.md`](docs/WHY-CAIRN.md) — what Cairn gives you
> compared with using an agent on its own.


```bash
python -m cairn_unified why node tests/run_all.py --commit 942cb526 --budget 8000
```

```text
answer       КОЛИ / СЕСІЯ / ЧОМУ / МЕЖА, assembled from stored objects
chronology   events in time order, each with its edge type and count
provenance   event ids and ledger_seq per object
trust        trust class and freshness PER ITEM, never averaged into one number
omissions    what was left out, and which of TWO reasons:
             unresolved:*     the source is absent
             verifier_error:* the check itself broke
cost         bytes, chars, tokens_est of this answer
```

Measured 2026-11-21 on a private store (1 876 events), both paths by the
same counter in one run (`tests/compare_why.py`):

| path | bytes | tokens ≈ | ms |
|---|---:|---:|---:|
| native: ledger scan + `git log -1` + `show --name-status` + `log -p -12` + `blame` | 3 111 792 | 685 099 | 114 |
| `why --budget 8000` | 8 326 | 1 983 | 56 |
| `why --budget 2000` | 2 498 | 507 | 52 |

**Two different boundaries, not one number.** Raw material vs a finished
answer: **×345** by tokens at budget 8 000, **×1 351** at 2 000. Raw
material vs the whole Cairn path (`why` + two MCP calls): **×220** and
**×417**. Neither compares against an equivalent `grep` answer — the
native path never tried to reconstruct the cause, so its cost is the cost
of **raw material**. Tokens are `chars ÷ 4`, a proxy, not the target
model's tokenizer.

The link proves **participation in time and path**, not semantic cause.
A commit is never declared a cause.

---

## Measured results

Every figure below has a named reference and a stated limit in
[`docs/MEASUREMENTS.md`](docs/MEASUREMENTS.md).

| What was measured | Reference | Result |
|---|---|---|
| Python extraction, 977 files | `ast`, the language's own parser | **100.0 %** exact (15 669 nodes) |
| Markdown, 28 files | independent heading counter | **100.0 %** exact (299 sections) |
| PHP, 4 × 120 files | an index built by another tool, in another language | recall **88.7–93.9 %** |
| PHP files lost entirely | declarations present outside strings | **0 of 797** fallbacks |
| Secret redaction | 14 planted secrets + 5 clean sentences | **14/14** caught, **5/5** untouched |
| Budget overruns, 12 runs | a comparable external tool (87.5 % overruns) | **0 %** |
| Merge, repeated | the same store merged twice | **0** duplicates added |
| Portability | the store folder copied elsewhere | **byte-identical** output |

The table above is an earlier dry-measurement state and is kept as-is. A
later measurement answers the exact question *"when was `tests/run_all.py`
edited and why, with commit `942cb526…`?"* on the private store
(151 objects, 1 800 relations, 1 876 events):

| Path | What it returns | Bytes | Tokens ≈ | Time |
|---|---|---:|---:|---:|
| Line scan of `.cairn` + `git log -1` + `git show --name-status` + `git log -p -12` + `git blame` | 307 raw JSONL lines and 12 full diffs | **3 908 929** | **856 582** | 180.1 ms |
| Cairn: `cairn_semantic_query` | 20 objects + 20 assertions in a provenance envelope | 4 064 | 1 016 | 27.6 ms |
| Cairn: `cairn_code_explore` | symbols by stable identity | 374 | 94 | 8.4 ms |
| Cairn: causal edges `nd-* → session → commit_fact` | typed edges | 3 583 | 896 | 4.7 ms |
| **Cairn: final answer** (4 objects) | date, session, 9 user-intents, 221 mutation events, why | **1 462** | **284** | **2.3 ms** |

The final answer is **≈3 016× smaller in tokens** than the raw material;
the full measured Cairn path (all four rows) is 2 290 tokens and 43.0 ms
— **≈374× and ≈4.2×**. These are two different comparison boundaries,
not one combined figure. Neither compares against an equivalent grep
answer. A separate qualitative result: a line scan returns commit facts
and user intent as undifferentiated lines, with no typed edges
(`caused_by`, `mutated`, `derived_from`) — the distinction has to be
reconstructed by the reader, and that labour is not in the token count.
This is a property of grep's output, not proof that an answer is
impossible. Full method and limits: §11 of
[`docs/MEASUREMENTS.md`](docs/MEASUREMENTS.md).

```text
input     2.5 GB of journals · 1 088 files
store     7.2 MB · 13 068 records · 68 sessions · 119 compacted contexts
          2.32 MB of text ≈ 608 000 tokens of compressed history
coverage  symbol 29.2 % · whole 70.2 % · section 0.6 %
privacy   418 redactions on live data (incl. a REAL connection string
          with a password) · 0 absolute paths · 0 user names
```

The `store` figures above belong to that earlier state. The later private
store has 151 objects, 1 800 relations and 1 876 events — not a revision of
the 13 068-record figure, but a different measurement.

---

## Who gains what

| | Without Cairn | With Cairn |
|---|---|---|
| **Developer** | code, then `git log`, then `git log -p` — 70–120 KB per node | one command, the reason for that exact method |
| **Agent** | re-reads files, re-proposes reverted fixes | earlier sessions' context, priced in advance |
| **Project** | "that module feels problematic" | rewrite counts per node — many edits and zero tasks means blind patching |
| **Team** | "ask whoever wrote it" — and they left | context moves by copying a folder, merges without duplicates |
| **Company** | context cost smeared across every task | the same cost, measured, and knowledge that transfers with the project |

It does not replace documentation, decide anything for you, or return
anything where no agent sessions existed.

---

## Inside

```text
cairn_unified/  v2 package at the repo root (CLI `cairn2`, 12 MCP tools)
  store.py, ledger.py, db.py, projections.py   Event Ledger + SQLite view
  semantic.py, ontology.py, nodes.py           objects, trust, addresses
  context.py, work.py, workflow.py             bounded context, work graph
  zones.py, merge.py, federation.py            zones, git-linked merge, bundles
  verification.py, evidence.py, gates.py       deterministic checks
  adapters/     journal collectors: opencode, claude_code, codex, normalize
tests/          run_all.py + verify_* regressions

repo_id     fingerprint of the SET of root commits — identical in any
            clone, on any machine, for any account
paths       relative to the repository root, only
```

---

## Integration

```text
level 0   the CLI as it is          works today, nothing to install
level 1   a Skill                   the agent decides WHEN to ask
level 2   a plugin (hooks + skill)  it happens automatically, before an edit
```

The criterion is **who initiates the call**: a fixed point is a hook, a
model's judgement is a skill. Setup and plugin layout:
[`docs/INTEGRATION.md`](docs/INTEGRATION.md).

Drop [`AGENT-ONBOARD.md`](AGENT-ONBOARD.md) wherever your agent reads its
rules. Interface notes live in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
The regressions include explicit reversals — a positive case alone would
prove nothing:

```bash
python tests/run_all.py         # 15/15
python tests/verify_zones.py    # 29/29
python tests/verify_merge.py    # 16/16
```

---

## Limits, stated plainly

```text
* the store keeps ONLY compacted contexts, never raw conversation;
* the secret list is finite: what is not recognised is not protected;
* the PHP extractor is a declaration scanner, not a parser —
  recall 88.7–93.9 %, measured, not estimated;
* a repository with no sessions returns an empty answer, and that is
  correct: the tool does not invent what never happened;
* the link proves participation in time and path, not semantic cause:
  `node --caused_by--> session`, while a commit is a CrUD fact
  (`commit_fact --mutated--> node`), not the reason for an edit;
* journals outlive repositories: index BEFORE deleting a project;
* session journal formats are internal to each agent and may change —
  the adapter is a sample, not a contract;
* self-onboarding has been verified with one agent so far.
```

---

## License

**[Apache-2.0](LICENSE)** — free to use, including commercially. Keep the
licence and `NOTICE` with the code, and state in modified files that you
changed them.

---

## Support the project

> **For motivating the development of new ideas**

```text
network   TRON (TRC-20)
address   TZ5veYdk67o3dWNHHLRaBpdXw3fJHt3w5V
```

<img src="docs/donate-tron.png" alt="TRON wallet QR code for supporting Cairn" width="180">

---

Developed with the support of
**[CommerCentr](https://commercentr.com/en)** — building a marketplace
and CMS with its own AI layer.
