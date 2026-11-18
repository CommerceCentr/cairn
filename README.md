# Cairn

> 🇺🇦 **Українською: [README.uk.md](README.uk.md)**
> &nbsp;&nbsp;|&nbsp;&nbsp; License: [Apache-2.0](LICENSE)
> &nbsp;&nbsp;|&nbsp;&nbsp; [Support the project](#support-the-project)

### Your AI agent forgets everything between sessions. Cairn remembers.

Close the terminal, and tomorrow your agent starts from zero. It
re-reads the same files, suggests the thing you rejected yesterday, and
spends tokens rebuilding what it already knew. Meanwhile your context
window fills with archaeology instead of the task.

**Cairn keeps those conversations and gives them back when they matter.**
You ask *"why is this file like this?"* — and get the answer in about
**300–1 700 tokens**, instead of the **12 000–240 000** it would take to
dig through git history by hand.

A **cairn** is a stack of stones left on a mountain pass by the people
who walked it before you, so the next traveller does not get lost. It
stays where it is, and it costs exactly as much as a stone weighs.

Developed with the support of
**[CommerCentr](https://commercentr.com/en)**.

---

## The pain: the context window is a budget, and history does not fit

A coding agent has a fixed context window. Everything it reads competes
for the same space: your prompt, the code, the tool output, its own
reasoning. History is the first thing cut — not because it is useless,
but because it is **enormous and undifferentiated**.

Measured on one real file we work with daily:

| What the agent would have to read | Bytes | ≈ tokens |
|---|---:|---:|
| `gui/server.py` itself | 646 713 | **162 000** |
| its 166 commit messages (`git log`) | 169 698 | **42 000** |
| full diffs (`git log -p`) — where removed prose lives | 959 030 | **240 000** |

*(Tokens throughout this document are estimated as characters ÷ 4 and
marked as estimates; the byte counts are measured.)*

The third row is the problem in one number: the place where the
explanation actually lives **does not fit into any mainstream context
window at all**. So the agent does what you have seen it do — skims,
guesses, or re-derives a decision that was already made and reverted.

This is not hypothetical. On one core class we counted:

```text
26 edits over four months
at least 4 of them fixing the SAME defect — an updater reporting
success it had not achieved
not one of the four recorded why
```

The fifth edit would have been the fifth patch.

---

## How Cairn solves it

Three ideas, each doing one job:

```text
1  ADDRESS CONTENT, NOT POSITION
   history is attached to `method:Ns\Class::name`, to a heading path,
   to a timecode — things that survive edits, unlike line numbers

2  STORE THE SUMMARY, NOT THE TRANSCRIPT
   agents already compact their own context when the window fills.
   That compaction summary IS the "why", pre-written and pre-shrunk.
   Cairn keeps those, not the raw conversation

3  BOUND THE ANSWER BEFORE PRODUCING IT
   every query estimates its own cost first, then applies a policy:
   trim, ask, or allow-and-say-so. The agent learns the price before
   it pays it
```

### The cost of one question

The same question — *why is this file the way it is* — asked two ways.
Both files are real, both measured today.

| | typical file (10.7 KB) | large file (632 KB) |
|---|---:|---:|
| commits | 48 | 166 |
| `git log` | 46 920 B ≈ **11 700 tok** | 169 698 B ≈ **42 000 tok** |
| `git log -p` | 165 628 B ≈ **41 000 tok** | 959 030 B ≈ **240 000 tok** |
| **`cairn why --priority low`** | 1 535 chars ≈ **384 tok** | 1 158 chars ≈ **290 tok** |
| **`cairn why --priority normal`** | 6 637 chars ≈ **1 660 tok** | 6 637 chars ≈ **1 660 tok** |
| cheaper than `git log` | **×30** | **×146** |

Note the shape of the large-file column: Cairn's cost **does not grow
with the file's history**, because the budget bounds it. A file with ten
times the history costs the same to ask about.

### What the store holds, and what a query costs

```text
held       13 068 records · 68 sessions · 3 029 files · 119 summaries
           2.32 MB of summary text ≈ 608 000 tokens of compressed history
           total on disk 7.2 MB, built from 2.5 GB of raw journals
spent      290–1 660 tokens per question, bounded in advance
```

The ratio is the point: roughly **600 000 tokens of history held, a few
hundred spent to reach the relevant part of it.**

### Projected over a session, a week, a quarter

The two parameters below are **measured from our own 59 sessions**, not
assumed:

```text
distinct files touched per session   median 21   (half of sessions 9–67)
sessions per week                    median 2, mean 3.3, max 15
```

How often an agent genuinely needs the *why* for a file is a judgement,
not a measurement — so the table gives you the multiplier instead of
picking one. Saving per lookup is taken from the **typical** file
(11 700 → 384 tokens ≈ 11 300 saved):

| Lookups per session | Per session | Per week (2 sessions) | Per quarter (12 weeks) |
|---:|---:|---:|---:|
| 1 | 11 K | 23 K | 271 K |
| 3 | 34 K | 68 K | 814 K |
| 5 | 57 K | 113 K | 1.36 M |
| 10 | 113 K | 226 K | 2.71 M |

**What this table is not.** It is not a bill: tokens saved are not money
saved one-to-one, and an agent that never asked about history was not
spending those tokens in the first place. What it does show is the size
of the window you get back — context that can now go to the task instead
of to archaeology.

---

## How it works

```text
   SOURCES                     STORE                 ANSWERS

   agent session journals --+
                            |
                            +--->  portable store  --->  why <file>
                            |      JSONL, 7.2 MB         why --node <symbol>
   git history -------------+      local, yours          bind --zone <zone>
```

Three commands cover the whole cycle:

```bash
python cairn.py index --store ./store                   # collect
python cairn.py why   --store ./store <path or node>    # ask
python cairn.py merge --store ./store --other <theirs>  # sync devices
```

**Zero dependencies** — Python standard library only. No database, no
daemon, no cloud, no format that nobody will be able to open in a year.

### A budget that actually holds

Output is bounded **before** it is produced: estimate first, then a named
policy, then printing.

```bash
python cairn.py why --store ./store <path> --priority normal
python cairn.py why --store ./store <path> --budget 4000 --on-overflow ask
```

```text
--priority low|normal|high|max   the agent picks a priority, not a number
--budget N                       hard limit, in characters
--on-overflow trim|ask|allow     trim · ask the human · pass and say so
```

Measured end to end over 12 budget/size combinations: **0 % overruns.**
For comparison, an external index doing the same job exceeded its budget
in **87.5 %** of runs (14 of 16), the worst by **×15.6**.

---

## Status: what is actually on Cairn

Measured by session ID, not estimated from folder names — the two do not
agree, and the folder-name heuristic flattered us by 25 points.

```text
journals on disk        107 sessions across 15 project folders
in the store             68 sessions  →  64 %
                         13 068 records · 5 repositories · 119 summaries
```

### Where the other 39 sessions went

| Reason | Sessions | Indexable? |
|---|---:|---|
| repository is **not a git repo** | 7 | ❌ never |
| project directory **no longer exists** | 2 | ❌ never |
| session touched no file in the repo and produced no summary | 30 | ⬜ nothing to record |

Only the first two rows are losses, and both are structural: Cairn
derives a project's identity from its git root commits, so a deleted or
non-git directory cannot be addressed at all. The third row is the
design working as intended — a conversation that edited nothing and was
never compacted leaves nothing worth keeping.

**So: every project that can be indexed, is.** The number to watch is
not "64 %" but the first two rows, and they will not improve by running
more commands.

### The limit this exposed

Journals outlive repositories. Two projects here have sessions on disk
and no directory left to attribute them to — their history is
unreachable, permanently. If you intend to rely on this, index **before**
you delete a project, not after.

---

## Measured results

Every number below comes from a run on real data. Where a measurement is
weak, the document says so. Full tables, references and caveats:
[`docs/MEASUREMENTS.md`](docs/MEASUREMENTS.md).

### Node extraction accuracy

| Language | Reference | Files | Exact match | Note |
|---|---|---:|---:|---|
| Python | `ast`, the language's own parser | 977 | **100.0 %** | 15 669 nodes = 15 669 reference |
| Markdown | independent heading counter | 28 | **100.0 %** | 299 sections |
| PHP | index built by a different tool, in a different language | 4 × 120 | recall **88.7 / 93.9 / 93.8 / 91.9 %** | declaration scanner, not a parser |

Python scores 100 % because the extractor uses the language's own
parser — that is the point, not a boast: a hand-written scanner is
written **only** where the language gives you nothing.

For PHP, *precision against that reference is deliberately not counted*:
the reference file is a **heat index** listing only nodes that were ever
changed, not all of them.

### Heterogeneous assets

5 000 files across a PHP monolith and a Python application, 28 683 nodes:

| Node kind | Files | Share |
|---|---:|---:|
| `whole` (file as one node) | 3 511 | **70.2 %** |
| `symbol` (class, method, function) | 1 461 | **29.2 %** |
| `section` (document headings) | 28 | **0.6 %** |

Most of `whole` is data and binaries: `.dat`, extensionless, `.exe`,
`.lock`. For those, "address the file as a whole" is the correct answer,
not a failure — an unknown type never breaks the tool, it only gets a
coarser address.

Checked separately: of 797 PHP files that fell back to `whole`,
**0 %** actually contained declarations. Nothing is silently lost.

### Store, privacy, portability

```text
input            2.5 GB of journals · 1 088 files · 42 projects
collected        13 068 records · 68 sessions · 3 029 files · 119 summaries
store size       6.0 MB, of which 2.4 MB is summary text
ballast removed  96.6 % of the first revision was empty rows — dropped

redaction        418 matches on live data, including a REAL database
                 connection string with a password
in the store     0 absolute paths · 0 occurrences of the user's name
redactor gate    100 % (14 of 14 planted secrets caught,
                 5 of 5 ordinary sentences left untouched)

portability      copying the folder gives BYTE-IDENTICAL query output
merge            2 621 + 9 099 = 11 720 · repeat merge adds 0
```

### Context economy

The same content, reconstructed two ways — through the index, and by
hand:

| Node | Index | By hand | Cheaper |
|---|---:|---:|---:|
| A | 5 592 B | 70 727 B | **×12.6** |
| B | 4 891 B | 120 815 B | **×24.7** |
| C | 8 660 B | 96 417 B | **×11.1** |

The manual side is **understated**: it covers one file, the index covers
two to four.

Separately, and this matters more than the ratio: documentation prose
that was stripped out of the code during cleanup appears **0 times** in
`git log`. The only other route to it, `git log -p`, costs **×22**.

---

## What this is for, and who gains

A percentage explains nothing on its own. Each block below follows the
same shape: **situation → without the tool → with it → what proves it.**
The number comes last, as evidence rather than as a slogan.

### For the developer

**Situation.** You open a file you did not write — or wrote six months
ago. The code does something odd, and you cannot tell whether that is a
bug or a decision.

**Without.** You read the code. Then `git log`, to find when it
appeared. Then `git log -p`, because commit subjects do not carry
reasons. That is **70–120 KB** of text to scroll through for a single
node. And if the prose was ever stripped out of the docblocks, plain
`git log` does not contain it **at all** — you do not even know there is
something to look for.

**With.** One command returns the history of that exact method: what was
done, what was reverted and why, plus the stripped prose verbatim.
**5–9 KB** instead of a hundred.

**Evidence.** ×11.1–24.7 on three nodes of a real project, comparing
identical content. Stripped prose: 0 hits in `git log`, ×22 via
`git log -p`. *Limit: three nodes of one project.*

### For the agent

**Situation.** A new session remembers nothing. Yesterday's decisions,
rejected options, your instructions — all gone with the context window.

**Without.** The agent re-reads the same files, proposes what you
reverted yesterday, and spends its window rebuilding what was already
known. You pay twice for one piece of knowledge.

**With.** Compaction summaries of earlier sessions sit next to the node.
More importantly, the **budget bounds the output in advance**: the agent
knows the price of context before receiving it, and can plan the rest of
the work around it.

**Evidence.** **0 %** budget overruns (0 of 12 combinations) against
**87.5 %** (14 of 16) in an external index doing the same job.

### For the project

**Situation.** There is a feeling that some module is "problematic", but
where the code actually rots is unknown.

**Without.** Decisions are made on impression: somebody remembers that
things keep breaking around here.

**With.** A rewrite counter per node. A line reading "many edits, zero
tasks" means the place was patched **with no reason recorded anywhere**,
and the next edit will be blind.

**Evidence.** On a real node — **26 edits in four months**, at least
**four** of them fixing one and the same defect. Not one of the four
recorded why.

### For the team

**Situation.** A person leaves, and the knowledge leaves with them.

**Without.** "Ask whoever wrote it" — except they are gone. Onboarding
turns into archaeology.

**With.** Context lives in the store, moves by copying a folder, and
merges across devices without duplicates. A new teammate asks *why*
rather than *who*.

**Evidence.** merge 2 621 + 9 099 = **11 720**, a repeat merge adds
**0**. A copied folder produces **byte-identical** output.

### For the company

Three things visible in the report, none of them about convenience.

**1. Budget.** Context reconstruction is a daily cost nobody measures,
because it is smeared across every task. The measured difference is
×11–25 **per lookup**. At dozens of lookups a day it shows up in the
invoice, not in the mood. *Limit: measured on three nodes of one
project; your codebase will give its own number.*

**2. Knowledge as an asset.** Causality stops being oral tradition and
becomes **data** — transferable, mergeable, verifiable, and handed over
together with the project. Roughly what version control once did for
code, but for *why* instead of *what*.

**3. Repetition risk.** Re-fixing the same defect costs more than time.
In the measured example one class of defect survived **four** patches,
because each time the case was fixed instead of the mechanism. Making a
node's history visible is the cheapest known way to stop that.

**What Cairn does NOT give a company.** It does not replace
documentation, does not make decisions for people, and does nothing at
all where no agent sessions existed: in our own measurement, two
projects out of four had **zero** journals, and for them the channel is
empty.

---

## Architecture

```text
cairn.py           single entry point
core/
  contract.py      record schema + gates
  store.py         storage, merge, deduplication
  paths.py         repository identity, relative paths
  manifest.py      incrementality: sha256 of a byte prefix
  redact.py        secret scanning
  budget.py        estimate-first output budget
  selftest.py      adapter gate
adapters/
  claude_code.py   reference journal collector
  _probe_*.py      deliberate fakes for the bypass list
extract/
  code_php.py      PHP symbols (own scanner)
  code_python.py   Python symbols (via ast)
  doc_markdown.py  document sections
  media_sidecar.py audio/video segments from subtitles
bind/
  php_archivist.py optional: external symbol index
tests/             a bypass list for every layer
```

### The node model

A node is an **addressable part of an asset**, measured in the unit
natural to its type:

| kind | unit | example address | what it is |
|---|---|---|---|
| `symbol` | byte | `method:Ns\Class::name` | class, method, function |
| `section` | byte | `section:Intro/Limits` | heading and its text |
| `segment` | second | `segment:00:04:12` | audio or video range |
| `whole` | none | `whole:` | the file as a single node |

An unknown type is **not an error**: it degrades to `whole:`. The tool
never breaks, it only gets coarser.

Everywhere, the address is **content, not position**: a symbol rather
than a line number, a heading path rather than a section number, a
timecode rather than a caption index. A number shifts the moment
anything above it is inserted.

### Portability

```text
repo_id   fingerprint of the SET of root commits — identical in any
          clone, on any machine, for any account
paths     relative to the repository root, only
machine    recorded as metadata, never part of a key
store     a folder of JSONL; transfer = copy
```

---

## Integration

```text
level 0   the CLI as it is          works today, nothing to install
level 1   a Skill                   the agent decides WHEN to ask
level 2   a plugin (hooks + skill)  the tool becomes part of the setup
```

The criterion is not preference but **who initiates the call**. An
action that must happen at a fixed point — after a session, before an
edit — is a hook. An action whose necessity the model judges is a skill.

By that criterion an MCP server is not needed here: MCP earns its keep
when you need structured answers from a long-lived process, and this is
a one-shot script.

See [`docs/INTEGRATION.md`](docs/INTEGRATION.md) for the step-by-step
setup, including the plugin layout and its caveats.

### Connecting your own agent

Do not wait for a connector. Drop
[`AGENT-ONBOARD.md`](AGENT-ONBOARD.md) wherever your agent reads its
rules — it will connect itself. The `selftest` gate checks the result
and rejects a fake adapter:

```bash
python cairn.py selftest --store ./store --adapter _probe_empty    # exit 1
python cairn.py selftest --store ./store --adapter claude_code     # exit 0
```

The second line is mandatory: without it, rejection would prove nothing
— a gate that rejects everything also rejects fakes.

---

## Limits, stated plainly

```text
* the store keeps ONLY compaction summaries, never raw conversation;
* the secret list is finite: what is not recognised is not protected.
  That is precisely why only summaries are stored;
* the PHP extractor is a declaration scanner, not a parser. Exotic
  syntax is missed: recall is 0.887–0.939, not 1.0 — measured, not
  estimated;
* a repository with no sessions returns an empty answer, and that is
  correct behaviour: the tool does not invent what never happened;
* the summary→node link is SESSION-level, not causal: it means "a file
  of this node was touched in a session that produced this summary";
* session journal format is internal to the agent and may change
  between versions; the adapter is a sample, not a contract;
* the self-onboarding instruction has so far been verified with one
  agent only.
```

---

## License

**[Apache-2.0](LICENSE)** — free to use, including commercially.

What the license asks of you if you take the code:

```text
§4(a)  ship a copy of the license alongside it
§4(b)  in modified files, STATE that you changed them
§4(d)  keep the NOTICE file in derivative works
§3     an explicit patent grant, in both directions
```

In short: use it freely, but **do not lose the source**, and say where
you departed from the original.

---

## Support the project

Cairn is open and free. If it saved you time:

> **For motivating the development of new ideas**

```text
network   TRON (TRC-20)
address   TZ5veYdk67o3dWNHHLRaBpdXw3fJHt3w5V
```

<img src="docs/donate-tron.png" alt="TRON wallet QR code for supporting Cairn" width="200">

> The address is given as text on purpose, so it can be verified
> character by character. The QR was not generated automatically — it is
> a file provided by the wallet owner.

---

Developed with the support of
**[CommerCentr](https://commercentr.com/en)** — the company building a
marketplace and CMS with its own AI layer.
