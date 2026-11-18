# Cairn

> 🇺🇦 **Українською: [README.uk.md](README.uk.md)**
> &nbsp;&nbsp;|&nbsp;&nbsp; License: [Apache-2.0](LICENSE)
> &nbsp;&nbsp;|&nbsp;&nbsp; Zero dependencies
> &nbsp;&nbsp;|&nbsp;&nbsp; [Support](#support-the-project)

### Your AI agent forgets everything between sessions. Cairn remembers.

Close the terminal, and tomorrow your agent starts from zero. It
re-reads the same files, suggests the thing you rejected yesterday, and
spends tokens rebuilding what it already knew. Meanwhile your context
window fills with archaeology instead of the task.

**Cairn keeps those conversations and gives them back when they matter.**
Ask *"why is this file like this?"* and get the answer in about
**300–1 700 tokens** instead of the **12 000–240 000** it takes to dig
through git by hand.

Developed with the support of
**[CommerCentr](https://commercentr.com/en)**.

---

## The problem, in one table

A coding agent has a fixed context window. History is the first thing
cut from it — not because it is useless, but because it is enormous.
Measured on one real file:

| What the agent would have to read | Bytes | ≈ tokens |
|---|---:|---:|
| the file itself | 646 713 | **162 000** |
| its 166 commit messages (`git log`) | 169 698 | **42 000** |
| full diffs (`git log -p`) — where removed prose lives | 959 030 | **240 000** |

The third row is the whole problem: the place where the explanation
actually lives **does not fit into any mainstream context window**. So
the agent skims, guesses, or re-derives a decision that was already made
and reverted.

On one class we counted **26 edits in four months**, at least **four**
of them fixing the same defect. Not one recorded why. The fifth edit
would have been the fifth patch.

*(Token figures throughout are estimated as characters ÷ 4 and labelled
as estimates; byte counts are measured.)*

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

**Python standard library only.** No database, no daemon, no cloud, no
format nobody will be able to open in a year.

Three ideas carry the design:

```text
1  ADDRESS CONTENT, NOT POSITION
   history attaches to `method:Ns\Class::name`, to a heading path, to a
   timecode — things that survive edits, unlike line numbers

2  STORE THE COMPACTED CONTEXT, NOT THE TRANSCRIPT
   agents already compact their own context when the window fills.
   That compacted context IS the "why", pre-written and pre-shrunk

3  BOUND THE ANSWER BEFORE PRODUCING IT
   every query estimates its own cost first, then applies a policy:
   trim, ask, or allow-and-say-so
```

---

## What a question costs

The same question asked two ways. Both files are real, both measured.

| | typical file (10.7 KB) | large file (632 KB) |
|---|---:|---:|
| commits | 48 | 166 |
| `git log` | ≈ **11 700 tok** | ≈ **42 000 tok** |
| `git log -p` | ≈ **41 000 tok** | ≈ **240 000 tok** |
| **`cairn why --priority low`** | ≈ **384 tok** | ≈ **290 tok** |
| **`cairn why --priority normal`** | ≈ **1 660 tok** | ≈ **1 660 tok** |
| cheaper than `git log` | **×30** | **×146** |

Cairn's cost **does not grow with the file's history** — the budget
bounds it. A file with ten times the history costs the same to ask about.

### Over a session, a week, a quarter

Both multipliers below are measured from 59 real sessions, not assumed:

```text
distinct files touched per session   median 21   (half of sessions 9–67)
sessions per week                    median 2, mean 3.3
```

How often an agent genuinely needs the *why* is a judgement, so the
table gives you the multiplier. Saving per lookup is the typical file
(11 700 → 384 tokens):

| Lookups per session | Per session | Per week | Per quarter |
|---:|---:|---:|---:|
| 1 | 11 K | 23 K | 271 K |
| 3 | 34 K | 68 K | 814 K |
| 5 | 57 K | 113 K | 1.36 M |
| 10 | 113 K | 226 K | 2.71 M |

**What this is not:** a bill. Tokens saved are not money saved
one-to-one, and an agent that never asked about history was not spending
them. What it shows is the window you get back — context that can go to
the task instead of to archaeology.

---

## Who gains what

| | Without Cairn | With Cairn | Measured |
|---|---|---|---|
| **Developer** | read the code, then `git log`, then `git log -p` — 70–120 KB per node | one command, the reason for that exact method | ×11–25; removed prose: 0 hits in `git log`, ×22 via `-p` |
| **Agent** | re-reads files, re-proposes reverted fixes | earlier sessions' context, with the price known in advance | 0 % budget overruns vs 87.5 % in a comparable tool |
| **Project** | "that module feels problematic" | rewrite counts per node; "many edits, zero tasks" = patched blind | 26 edits, 4 on one defect, 0 reasons recorded |
| **Team** | "ask whoever wrote it" — and they left | context moves by copying a folder, merges without duplicates | merge 2 621 + 9 099 = 11 720; repeat adds 0 |
| **Company** | context cost smeared across every task | the same cost, measured, and knowledge that transfers with the project | ×11–25 per lookup |

**What Cairn does not do.** It does not replace documentation, does not
decide anything for you, and returns nothing where no agent sessions
existed — it recovers conversations that happened.

---

## Measured results

Full tables, references and caveats:
[`docs/MEASUREMENTS.md`](docs/MEASUREMENTS.md).

### Node extraction

| Language | Reference | Files | Result |
|---|---|---:|---|
| Python | `ast`, the language's own parser | 977 | **100.0 %** exact (15 669 = 15 669 nodes) |
| Markdown | independent heading counter | 28 | **100.0 %** exact (299 sections) |
| PHP | an index built by a different tool, in a different language | 4 × 120 | recall **88.7–93.9 %** |

Python scores 100 % because the extractor uses the language's own
parser. That is the rule, not a boast: a hand-written scanner appears
only where the language gives you nothing.

### Heterogeneous assets

5 000 files across a PHP monolith and a Python application:

```text
symbol   29.2 %   classes, methods, functions
whole    70.2 %   data and binaries — the file as one node
section   0.6 %   document headings
```

An unknown type is not an error: it degrades to `whole:`. Of 797 PHP
files that fell back, **0 %** actually contained declarations — nothing
is silently lost.

### Store, privacy, portability

```text
input       2.5 GB of journals · 1 088 files
store       7.2 MB · 13 068 records · 68 sessions · 119 compacted contexts
            2.32 MB of text ≈ 608 000 tokens of compressed history
ballast     96.6 % of the first revision was empty rows — dropped

redaction   418 matches on live data, including a REAL connection
            string with a password
gate        100 % — 14 of 14 planted secrets caught,
            5 of 5 ordinary sentences left untouched
contains    0 absolute paths · 0 occurrences of the user's name

portability copying the folder gives BYTE-IDENTICAL query output
```

---

## Inside

```text
cairn.py           single entry point
core/              contract · store · paths · manifest · redact · budget
adapters/          journal collectors (+ deliberate fakes for the gate)
extract/           php · python · markdown · media sidecars
bind/              optional external symbol index
tests/             a bypass list for every layer
```

### The node model

| kind | unit | example address | what it is |
|---|---|---|---|
| `symbol` | byte | `method:Ns\Class::name` | class, method, function |
| `section` | byte | `section:Intro/Limits` | heading and its text |
| `segment` | second | `segment:00:04:12` | audio or video range |
| `whole` | none | `whole:` | the file as one node |

Everywhere the address is **content, not position** — a symbol rather
than a line number, a heading path rather than a section number, a
timecode rather than a caption index.

### Portability

```text
repo_id   fingerprint of the SET of root commits — identical in any
          clone, on any machine, for any account
paths     relative to the repository root, only
store     a folder of JSONL; transfer = copy; merge = add what is missing
```

---

## Integration

```text
level 0   the CLI as it is          works today, nothing to install
level 1   a Skill                   the agent decides WHEN to ask
level 2   a plugin (hooks + skill)  it happens automatically, before an edit
```

The criterion is **who initiates the call**: a fixed point is a hook, a
model's judgement is a skill. Step-by-step setup, including the plugin
layout and its caveats: [`docs/INTEGRATION.md`](docs/INTEGRATION.md).

### Connecting your own agent

Drop [`AGENT-ONBOARD.md`](AGENT-ONBOARD.md) wherever your agent reads
its rules — it will connect itself. The gate checks the result:

```bash
python cairn.py selftest --store ./store --adapter _probe_empty    # exit 1
python cairn.py selftest --store ./store --adapter claude_code     # exit 0
```

The second line is mandatory: without it, rejection would prove nothing.

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

**[Apache-2.0](LICENSE)** — free to use, including commercially. Keep
the licence and `NOTICE` with the code, and state in modified files that
you changed them.

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
