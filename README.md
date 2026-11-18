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
   git history ---------------+--->  portable store   --->  why <file>
                                     JSONL, 7.2 MB          why --node <symbol>
                                     local, yours           bind --zone <zone>
```

```bash
python cairn.py index --store ./store                   # collect
python cairn.py why   --store ./store <path or node>    # ask
python cairn.py merge --store ./store --other <theirs>  # sync devices
```

**Python standard library only.** No database, no daemon, no cloud.

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
WHAT YOU GET  compacted contexts of past sessions that touched that node,
              newest first, bounded by --priority
WHAT YOU DO   NOT get: current code (read it yourself), documentation,
              or a causal claim — the link is session-level
```

| kind | unit | example address | what it is |
|---|---|---|---|
| `symbol` | byte | `method:Ns\Class::name` | class, method, function |
| `section` | byte | `section:Intro/Limits` | heading and its text |
| `segment` | second | `segment:00:04:12` | audio or video range |
| `whole` | none | `whole:` | the file as one node |

```bash
python cairn.py why --store ./store src/app.py            # whole file
python cairn.py why --store ./store --node 'method:App::run'
python cairn.py why --store ./store src/app.py --priority low   # ≈2 000 chars
python cairn.py stats --store ./store                     # what is indexed
```

```text
--priority  low 2 000 · normal 8 000 · high 24 000 · max 100 000 chars
--on-overflow  trim (default) · ask · allow

exit 0  an answer was printed (`stats` always exits 0)
exit 1  nothing found: no session ever touched that file or node —
        this is an ANSWER, not a failure. Also: a rejected adapter
exit 2  usage: no --zone, or no archivist root for `bind`
```

Exit 1 is the one to get right. It does not mean the tool broke; it
means the history is silent, and silence must not be filled with a
guess.

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

```text
input     2.5 GB of journals · 1 088 files
store     7.2 MB · 13 068 records · 68 sessions · 119 compacted contexts
          2.32 MB of text ≈ 608 000 tokens of compressed history
coverage  symbol 29.2 % · whole 70.2 % · section 0.6 %
privacy   418 redactions on live data (incl. a REAL connection string
          with a password) · 0 absolute paths · 0 user names
```

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
cairn.py    single entry point
core/       contract · store · paths · manifest · redact · budget
adapters/   journal collectors (+ deliberate fakes for the gate)
extract/    php · python · markdown · media sidecars
bind/       optional external symbol index
tests/      a bypass list for every layer

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
rules and it will connect itself. The gate checks the result — both lines
are mandatory, since rejection alone would prove nothing:

```bash
python cairn.py selftest --store ./store --adapter _probe_empty    # exit 1
python cairn.py selftest --store ./store --adapter claude_code     # exit 0
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
* the context→node link is SESSION-level, not causal;
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
