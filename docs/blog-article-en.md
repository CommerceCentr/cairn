# Article package: "AI agent context between sessions"

> Generated with the `seo-article-writer` skill. **Human editing is
> mandatory**: fact-check the figures and resolve the final URLs. The
> finished package goes to `docs/seo/content/` in the
> `PUBLISH_MANIFEST.md` format.
>
> This is the EN localisation of `blog-article-ua.md` — a parallel
> article for the EN cluster, not a word-for-word translation. The
> measured figures are identical because they come from the same runs.

```text
target_keyword:   ai agent context between sessions
secondary:        ai coding assistant loses context; code history for llm;
                  reduce ai agent token cost; ai memory for codebase;
                  why is this code like this git history
pillar_url:       /en/blog/dev/                 (P6, developer hub)
commercial_url:   /en/extensions/ai/            (B6, AI extensions)
intent:           I → C
cluster:          B6 (AI-commerce) × P6 (dev)
languages:        EN
```

---

## 1. `<title>` (59 characters)

```
AI Agent Context Between Sessions: Fixing It | CommerCentr
```

## 2. meta_description (151 characters)

```
Your agent restarts from zero every session and burns tokens. Here is
how to keep AI agent context between sessions — measured, with open code.
```

## 3. URL slug

```
ai-agent-context-between-sessions
```

## 4. H1

```
AI agent context between sessions: why it disappears and what to do
```

---

## 5. Body

You close the terminal and the agent forgets everything. Next morning it
re-reads the same files, proposes the fix you rolled back yesterday, and
spends your money doing it. **AI agent context between sessions** is not
stored anywhere — not in the code, not in the commits, not in the agent.
Below: what exactly is lost, why git does not save you, and how we
closed that gap with an open-source tool.

### What disappears when a session ends

Git answers "what changed" honestly. A commit with a good body sometimes
answers "why". But decisions are made in conversation, and that is where
the valuable part stays:

- **rejected options** — "we tried it through the cache, it fails before
  bootstrap";
- **instructions from the product owner** — three strategic reversals
  inside a single session;
- **measurements that changed the approach** — the number that killed a
  hypothesis.

None of it reaches the repository. A month later the next developer, or
the next agent, repeats the same attempt.

This is not theory. On one core class we counted **26 edits over four
months**, and at least **four of them** were fixing the same defect: an
updater reporting success it had not achieved. Not one of the four
recorded the reason. The fifth edit would have been the fifth patch.

### Why "just read git log" does not save you

Because a cheap `git log` does not contain what you are looking for. We
measured this on three hot nodes of a real project, comparing **the same
content** reconstructed two ways: through a causality index, and by hand.

| Node | Via index | By hand | Cheaper |
|---|---:|---:|---:|
| `Redirector::handle` | 5 592 B | 70 727 B | **×12.6** |
| `MarketplaceClient` | 4 891 B | 120 815 B | **×24.7** |
| `PatchApplier` | 8 660 B | 96 417 B | **×11.1** |

The manual side is **understated**: it covers one file, the package
covers two to four.

There is a sharper case that shows git's limit best. When docblocks are
stripped out of the code — and everyone who keeps code clean does strip
them — the explanation leaves the file but stays in older revisions. In
a plain `git log` it has **zero** matches. The only route is
`git log -p`, which costs **×22** more. The answer exists, but you pay
for a whole book to read one paragraph.

### Three rules the solution rests on

We packaged this as an open tool called **Cairn** (a cairn is the stack
of stones travellers leave on a pass so the next one does not get lost).
Three decisions in it matter more than the code.

**First: address content, not position.** A line number shifts the
moment anything above it is inserted. So a node is addressed as
`method:Ns\Class::name`, a document section by its heading path, an
audio fragment by timecode. The same rule across three different data
types.

**Second: store the compacted context, not the raw transcript.** Modern
agents compact their own context when the window runs out. That
compacted context is already an agent-written summary of *why* — and it
is what belongs in the store, not the whole conversation. For us that is
2.32 MB of substance against 2.5 GB of raw journals.

**Third: store nothing redundant.** The first revision of the store
weighed 27 MB and was **96.6 %** empty records with no paths and no
text. After the cleanup: 7.2 MB, and for the first time with the
substance inside.

### Privacy: what never reaches the store

Conversation journals contain tokens, keys and machine paths. So before
anything is written, a scanner runs over 14 classes of secret — provider
keys, JWT, bearer tokens, private keys, connection strings, paths
carrying a username.

On live data it redacted **418 matches**, including a **real connection
string with a password**. The check is built from a bypass list: 14 of
14 planted secrets caught, while 5 ordinary sentences about code passed
untouched. The second list matters as much as the first — a redactor
that chews through normal prose would destroy exactly the *why* this is
all for.

The resulting store contains **zero absolute paths** and **zero
occurrences of the user's name**. That is not a promise; it is a check
that fails if violated.

### Portability across machines and accounts

The store is a folder of JSONL. Transfer means copying it. To keep it
alive on another machine, project identity is derived not from a path
but from the **fingerprint of the set of root commits** — identical in
any clone, for anyone.

Merging is verified on real data:

```text
device with an older snapshot:  2 621 records · 26 sessions
after merge:                   11 720 records · 59 sessions  (+9 099)
repeat merge:                  0 added
```

The zero in the third line is the point. Re-syncing duplicates nothing,
because a record's address does not depend on how the store was built.

### What this gives a team and a business

| Who | What exactly |
|---|---|
| Developer | "why is it like this" in seconds instead of archaeology |
| Agent | a ready context package instead of ten file reads |
| Project | visible hot spots: what was rewritten most, and without a task |
| Team | onboarding through *why*, not through "ask whoever wrote it" |
| Company | knowledge does not leave with the person; the context bill is measured |

One note on the last row. If reconstructing the context of a single node
costs 11–25× less, then on a project with dozens of lookups a day the
difference shows up in the invoice, not in the mood.

### Connecting your own agent

Writing a connector per agent is endless work: formats multiply and
change. So the project ships an instruction file you drop wherever your
agent reads its rules (`CLAUDE.md`, `AGENTS.md`, Cursor rules — it does
not matter), and the agent connects itself.

The instruction describes no formats. It defines a discovery algorithm,
and its second step is the important one: **prove the journals you found
are yours** — locate a line the user wrote in this very conversation.
The reason is stated outright: an agent that failed to find its journals
tends to write a plausible adapter returning emptiness. You cannot tell
the difference by eye, so a machine tells it instead.

### What the tool cannot do — said up front

- the secret list is finite: **what is not recognised is not
  protected**;
- the PHP symbol extractor is a declaration scanner, not a parser:
  recall is **0.887–0.939**, not 1.0;
- a repository with no sessions returns an empty answer — the tool does
  not invent what never happened;
- the summary→node link is **session-level**, not causal;
- journals outlive repositories: if the project directory is gone, its
  history is unreachable. Index **before** deleting, not after.

We publish these limits next to the numbers deliberately. A tool that
stays silent about its gaps is worse than no tool: you trust it exactly
where you should not.

### Conclusion

AI agent context between sessions disappears not because the agent is
bad, but because nobody stores it. Git holds changes, a tracker holds
tasks, and causality is held by no one.

Cairn closes that specific gap: it builds a portable store out of what
you already have, requires no dependencies, and **states its own numbers
together with its own limits**.

If you are building a store or a marketplace and want the AI layer
working for you rather than burning budget, see how we do it:
[CommerCentr AI extensions](/en/extensions/ai/). Same approach:
machine-readable data, ownership of code and data, zero hidden rent.

Cairn is developed **with the support of
[CommerCentr](https://commercentr.com/en)**.

---

## 6. Internal links

```text
pillar        /en/blog/dev/              anchor: "developer materials"
commercial    /en/extensions/ai/         anchor: "CommerCentr AI extensions"
sibling 1     [TODO: final URL]          anchor: about MCP and AI visibility
sibling 2     [TODO: final URL]          anchor: about machine-readable attributes
```

> `[TODO]` is deliberate: invented URLs would create redirects and
> cannibalisation. Fill from `docs/seo/10_PUBLICATION_SCHEDULE.md`.

## 7. JSON-LD

```json
{
  "@context": "https://schema.org",
  "@type": "Article",
  "headline": "AI agent context between sessions: why it disappears and what to do",
  "description": "Why an AI agent loses context between sessions, what it costs in measured numbers, and how to close the gap with the open-source tool Cairn.",
  "inLanguage": "en",
  "datePublished": "[TODO: publication date]",
  "author": { "@type": "Organization", "name": "CommerCentr", "url": "https://commercentr.com/en" },
  "publisher": { "@type": "Organization", "name": "CommerCentr", "url": "https://commercentr.com/en" },
  "mainEntityOfPage": "[TODO: final article URL]"
}
```

```json
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    { "@type": "ListItem", "position": 1, "name": "Blog", "item": "/en/blog/" },
    { "@type": "ListItem", "position": 2, "name": "For developers", "item": "/en/blog/dev/" },
    { "@type": "ListItem", "position": 3, "name": "AI agent context between sessions" }
  ]
}
```

> `FAQPage` is deliberately absent: rich snippets for it were retired on
> 2026-05-07.

## 8. External source (E-E-A-T)

```text
https://git-scm.com/docs/git-log
```

Appropriate where `git log -p` is discussed: the reader can verify we
did not invent the tool's behaviour.

## 9. Alt texts

```text
1. Diagram: agent session journals and git history feeding a context store
2. Measurement table: reconstructing a node's context via index vs by hand
3. Example output: a method's history with the reason for a change
```

---

## Quality checklist

- [x] keyword in title / H1 / url / first 100 words / meta
- [x] 4–7 H2 sections, tables, structured blocks
- [x] JSON-LD Article + BreadcrumbList, **no FAQPage**
- [x] every figure from a measured run; unknowns marked `[TODO]`
- [x] external source, alt texts, CTA to `/en/extensions/ai/`
- [x] CommerCentr mentioned in body and footer
- [ ] final sibling URLs — editor to fill
- [ ] publication date in JSON-LD
- [ ] human fact-check before publishing

> The article contains **no** legal claims about taxes or fiscalisation,
> so no disclaimer is required. If an editor adds such a block, the
> disclaimer becomes mandatory per the brand-voice rules.
