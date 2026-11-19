# CAIRN UNIFIED
## Технічна специфікація архітектури та розробки

**Версія документа:** 1.0  
**Дата дослідження:** 19 листопада 2026 року  
**Статус:** архітектурна специфікація для greenfield-розробки  
**Робоча назва продукту:** Cairn Unified (далі — Cairn)  
**Ціль розгортання:** local-first, повністю self-hosted, offline-capable  
**Основні клієнти:** Claude Code, Codex, Cursor, Gemini CLI та інші coding agents через адаптери, CLI і MCP

---

## 0. Резюме рішення

Cairn має стати не «пам'яттю агента» і не ще одним issue tracker. Це **локальна операційна система контексту та виконання роботи для команд людей і coding agents**.

Система об'єднує чотири площини, які не можна змішувати в одну модель:

1. **Work Graph** — що потрібно зробити, у якій послідовності, хто відповідає, що блокує роботу та які критерії завершення.
2. **Evidence & History** — хто, коли, у якій сесії та на підставі яких подій ухвалив рішення або змінив код.
3. **Semantic Code Graph** — які файли, символи, модулі, контракти та архітектурні інваріанти пов'язані між собою.
4. **Execution & Coordination** — які агенти виконують роботу, які ресурси вони захопили, як ізолюються зміни, перевіряються та інтегруються.

Над ними працює **Context Engine**: він збирає для конкретної задачі, агента, символу та ревізії коду мінімальний достатній контекст із доказами, часовою актуальністю, рівнем довіри й жорстким бюджетом.

### Основна архітектурна теза

> Beads показує, як зробити роботу видимою, структурованою та керованою через граф задач і workflow. Cairn показує, як дешево відновити історичне «чому» безпосередньо від вузла коду. Новий Cairn повинен з'єднати Work Graph із Semantic/Evidence Graph так, щоб задача, сесія, рішення, коміт, diff, символ і тест утворювали перевірюваний ланцюг походження.

Це не буквальне об'єднання кодових баз і не копіювання внутрішньої реалізації Beads. Це незалежна архітектура, яка використовує перевірені концепції обох продуктів і закриває їхні різні прогалини.

### Ключові рішення

- Greenfield-архітектура; Cairn 0.x — джерело перевірених ідей та сумісності, а не обмеження моделі.
- Модульний моноліт + локальний daemon + ізольовані worker-процеси. Не починати з мікросервісів.
- Python 3.12+ для ядра та CLI; SQLite WAL + FTS5 для локальних проєкцій; Git для переносимої історії; JSONL/JSON event envelopes як стабільний interchange format.
- MCP — основний універсальний протокол для агентів; CLI залишається найекономнішим і найпростішим інтерфейсом; REST/WebSocket — для локального UI та інтеграцій.
- Усі критичні функції працюють локально без зовнішнього API. Хмарні моделі — тільки явно ввімкнений адаптер, без прихованого fallback.
- Ніякого обов'язкового vector DB, Neo4j, Redis, Kafka або Dolt Server у першому релізі.
- SQLite — відновлювана проєкція, а не єдине джерело довготривалої істини. Події та декларативні артефакти зберігаються у переносимому форматі.
- Жодна LLM-згенерована пам'ять не стає авторитетною інструкцією автоматично.
- Ніяких тверджень про причинність лише на підставі того, що сесія торкнулася файла.
- Git Worktree ізолює робочі дерева, але не є sandbox. Виконання коду потребує окремих OS/container обмежень.

---

## 1. Межі дослідження та коректність вихідних даних

Дослідження виконане станом на **19.11.2026** за публічними репозиторіями та офіційною документацією. Важливо розділяти три категорії:

- **Підтверджений факт** — функція або обмеження прямо описані у вихідному коді/офіційній документації.
- **Вимір автора проєкту** — результат тесту або benchmark, наведений самим проєктом. Він не є незалежним аудитом.
- **Архітектурна пропозиція Cairn** — нове рішення, яке потрібно реалізувати й перевірити; не слід сприймати його як уже наявну функцію.

### 1.1 Який саме Cairn аналізувався

Цільовий репозиторій — [CommerceCentr/cairn](https://github.com/CommerceCentr/cairn), описаний як AI agent context between sessions. У ньому є `core/`, `adapters/`, `extract/`, `bind/`, `tests/`, `docs/`, `cairn.py`, `AGENT-ONBOARD.md` та український README. На момент перевірки GitHub показував 13 commits.

**Не плутати з `cairn-framework/cairn`.** Це інший, однойменний проєкт із blueprint, contracts, code reconciliation, architecture map та commit gates. Його можливості не приписуються CommerceCentr/Cairn і не вважаються частиною поточної бази. Його можна вивчати як окремий зовнішній приклад архітектурних декларацій, але не як наявний функціонал вашого репозиторію.

### 1.2 Поточний Cairn: підтверджені властивості

За README, `README.uk.md`, `AGENT-ONBOARD.md`, `core/contract.py`, `core/store.py`, `docs/INTEGRATION.md` та `docs/MEASUREMENTS.md`:

- Збирає метадані сесій агентів і Git history; адресує історію файлами та вузлами `symbol`, `section`, `segment`, `whole`.
- Для коду використовує адресацію до символу на кшталт `method:Ns\\Class::name`, а не тільки до номера рядка.
- Зберігає **скомпактований контекст**, а не повні транскрипти; сире листування не є стандартним вмістом сховища.
- Має переносиме JSONL-сховище без бази даних і демона; `merge` додає відсутні незмінні записи та відкидає дублікати.
- Використовує `repo_id`, `session_id`, `event_id` (коли джерело його надає), час, actor, kind і відносні шляхи.
- Валідує записи перед збереженням, забороняє абсолютні шляхи та відхиляє текст, у якому спрацював секрет-сканер.
- Оцінює розмір відповіді до її побудови; підтримує бюджетні режими trim/ask/allow та не видає частковий блок за повний.
- Має адаптери джерел журналів, екстрактори Python/PHP/Markdown та зовнішній bind для індексу символів.
- Описує три рівні інтеграції: CLI, skill, plugin/hooks. Документація прямо застерігає, що формат журналів Claude Code внутрішній і змінюється, а plugin hook у репозиторії ще не пройшов повний runtime self-test.

#### Виміри Cairn, які можна використовувати як вихідну точку

`docs/MEASUREMENTS.md` оновлено 18.11.2026. Там наведено:

- 13 068 записів, 68 сесій, 3 029 файлів, 119 compacted contexts, 7.2 MB сховища на одному з описаних зрізів.
- 2.5 GB журналів на вході у відповідному прогоні; 42 проєкти в окремому описаному наборі.
- Python extractor: 977 файлів, 15 669 вузлів, 100% збігу з Python AST — це очікувано, оскільки extractor використовує `ast`.
- PHP: заявлена повнота 88.7–93.9% проти зовнішнього індексу на чотирьох вибірках по 120 файлів; автор прямо зазначає, що це не повна перевірка точності всіх меж.
- Markdown: 28 файлів і 299 заголовків із 100% збігом проти власного лічильника — слабший еталон.
- Переносність: копія теки дає побайтово ідентичний результат; повторний merge додає 0 дублікатів.
- Секрет-редакція: 14/14 тестових секретів знайдено, 5/5 звичайних фраз збережено; автор прямо вказує, що перелік форм секретів скінченний.
- Бюджет виводу: 0 перевищень у 12 описаних комбінаціях. Одиниця бюджету — символи, а не токени; tokenizer немає.
- Економія контексту порівнювалася на трьох вузлах; ручний шлях був обмежений одним файлом, а пакет — 2–4 файлами. Це не універсальний незалежний benchmark.

Ці показники корисні як baseline, але специфікація вимагає повторити їх на відтворюваному корпусі й додати незалежні еталони.

### 1.3 Поточний Beads: підтверджені властивості

Цільовий репозиторій — [gastownhall/beads](https://github.com/gastownhall/beads). У поточній документації Beads описує себе як persistent structured work graph для coding agents.

Підтверджені механізми:

- **Bead/issue** із типом, пріоритетом, статусом, описом, acceptance criteria, notes, assignee, metadata, labels, comments, timestamps, close reason.
- Типізовані залежності: `blocks`, `parent-child`, `discovered-from`, `related`, а також workflow edges `conditional-blocks`, `waits-for` і graph links на кшталт `relates-to`, `duplicates`, `supersedes`, `replies-to`.
- `bd ready` обчислює claimable frontier — задачі без відкритих блокерів, які не перебувають у роботі, не deferred і не затримані gate.
- Hash-based IDs з адаптивною довжиною зменшують ризик колізій між паралельними агентами/гілками.
- Атомарний `--claim`, assignment, handoff, fan-out/fan-in, merge slots.
- Формули (TOML/JSON) → proto → molecules; wisps для тимчасових workflow; gates для human/timer/GitHub подій.
- Dolt як єдиний storage backend: embedded mode для одного writer; server mode для багатьох concurrent writers; versioned SQL, branches, diff, merge, push/pull, cell-level merge.
- Sync Dolt через окремий Git ref `refs/dolt/data`; `issues.jsonl` — export/interchange, а не source of truth і не backup.
- `bd prime` для компактного контексту; hooks та integrations для різних coding agents; CLI рекомендований там, де агент має shell, через менший token overhead порівняно з MCP schema.
- Опційний `bd audit record` для append-only JSONL interaction audit (llm_call/tool_call, prompt/response за явного передавання); issue history пишеться в базу незалежно від цього.
- Agent registry як окрема сутність відсутній у описаному coordination layer: assignee — рядок.
- Існують правила compact/decay/cleanup та інструменти керування життєвим циклом задач.

### 1.4 Суміжні системи, які впливають на архітектуру

| Система / підхід | Що варто врахувати | Чого не слід механічно копіювати |
|---|---|---|
| LangGraph persistence | Checkpoints для відновлення run/thread; окреме довготривале сховище для cross-thread data; time-travel/HITL | Прив'язку всієї пам'яті до конкретного orchestration framework |
| Letta | Stateful agents, memory blocks, archival retrieval, контрольований background memory consolidation | Постійну підкладку великих memory blocks у кожен prompt |
| Graphiti / Zep | Temporal facts, valid-time/invalid-time, provenance до source episodes, комбінований graph/full-text/vector retrieval | Автоматичне перетворення будь-якого тексту на істинний факт без approval/evidence |
| MCP | Стандартизований tool interface для різних агентів | Надмірну кількість tools/schema, що збільшує prompt overhead |
| Git worktrees / merge queues | Ізоляція робочих дерев і контрольована інтеграція | Припущення, що worktree сам по собі є sandbox або захистом від конфлікту |

Джерела наведені в розділі 18.

---

## 2. Порівняння Beads і Cairn

| Вимір | Beads | CommerceCentr/Cairn | Цільовий Cairn |
|---|---|---|---|
| Головна одиниця | Задача/issue | Подія/контекст сесії, прив'язаний до файла/вузла | Work item + evidence + semantic node |
| Робочий процес | Сильний: статуси, залежності, ready queue, formulas, gates | Не є task scheduler | DAG/workflow engine із claim/lease/checkpoint/gates |
| Історія | Versioned issue history та optional interaction audit | Компактовані контексти й метадані сесій | Append-only evidence ledger + event-sourced work state |
| Кодова адресація | Через зовнішні refs/metadata/коментарі; не є основним symbol index | Файл/символ/section/timecode | Stable symbol identity + AST + commit/diff + semantic edges |
| Причинність | Task relation може бути явною, але не автоматично доведеною кодовим diff | Зв'язок session touched node; сам Cairn називає його session-level, не causal | Рівні provenance: explicit, verified, inferred; ніяких прихованих причинних висновків |
| Координація | Atomic claim, assignee, merge slot | Немає повноцінного ownership/lease engine | Agent registry, leases, fencing tokens, semantic locks, conflict gate |
| Короткі сесії | Стан задачі переживає сесію | Результат залежить від capture/compact та джерела журналу | Capture кожної значущої події + incremental checkpoint; compact необов'язковий |
| Рішення та скасування | Історія/graph links; рішення можуть бути issue types | Немає повної життєвої моделі рішення | Decision records із supersede/revoke, temporal validity і evidence |
| Multi-agent | Сильний issue-level coordination; Dolt server для concurrent writers | Append-only merge, переносність | Task + semantic resource coordination, event merge, worktrees |
| Multi-repo | Routing/federation та repo hydration | repo_id, relative paths, archive merge | Workspace registry, project identity, aliases, cross-repo graph |
| Контекстна економія | Компактний `prime`, готові задачі | Budgeted `why` package; сильна економія на history lookup | Budgeted context compiler із tokenizer-aware hard cap та cache |
| UI | CLI-first; graph output та інтеграції | CLI-centric; невеликий UI не є основою | Unified Workbench + agent API, але UI не є обов'язковим для agent flow |
| Зберігання | Dolt | JSONL portable store | Event files + Git + SQLite projections; optional Dolt backend later |
| Локальність | Embedded/offline possible; server/remote sync optional | Local, zero-dependency, portable | Offline-first, self-hosted; no outbound calls by default |

### 2.1 Сильні сторони Beads, які потрібно засвоїти

1. **Граф задач замість плоского backlog.** `ready` — це обчислювана множина задач, які справді можна виконувати.
2. **Атомарний claim.** Два агенти не повинні одночасно вважати одну задачу своєю.
3. **Typed dependencies.** Блокування, ієрархія, походження та м'які зв'язки мають різну семантику.
4. **Workflow-as-data.** Повторювані процеси визначаються декларативними формулами та інстанціюються як конкретна робота.
5. **Gates.** Агент може чекати на людину, таймер, CI або іншу зовнішню умову, не займаючи процесорний/модельний цикл.
6. **History and audit.** Стани задач і взаємодії можна простежити; label не переписує початкову подію, а додає окрему.
7. **Versioned storage and sync.** Dolt вирішує нетривіальні задачі конкурентного оновлення структурованих записів.
8. **Низький token overhead.** CLI + hooks + `prime` часто економніші за оголошення великого MCP набору інструментів.

### 2.2 Сильні сторони Cairn, які потрібно зберегти й розширити

1. **Запит від коду.** Початкова точка — конкретний метод/клас/файл, а не пошук по всій історії задач.
2. **Content-addressed context.** Символи й структурні вузли переживають звичайні зміни line numbers.
3. **Компактна видача з передбачуваною ціною.** Бюджет перевіряється до формування повного пакета.
4. **Portable, inspectable data.** Простий формат можна перенести, прочитати та відновити без vendor lock-in.
5. **Адаптери до різних журналів.** Агент-специфічна нестабільність ізольована від ядра.
6. **Чесне «немає даних».** Відсутність історії має бути явним результатом, а не приводом для вигадки.
7. **Secret redaction і мінімізація.** За замовчуванням зберігати мінімум необхідних даних, а не сирі transcript-и.
8. **Вимірювання.** Кожна заявлена перевага має бути прив'язана до еталону, корпусу та відтворюваного тесту.

### 2.3 Що не треба переносити буквально

- Не використовувати Dolt як обов'язковий runtime backend лише тому, що він є в Beads. Він потужний, але збільшує runtime/maintenance surface; для локального першого релізу достатньо SQLite + immutable events + Git.
- Не зводити всі знання до issue metadata/comments. Кодова семантика, доказ, рішення й execution state мають окремі типи.
- Не зберігати весь transcript за замовчуванням. Зберігати provenance і redacted evidence; transcript — лише за явною політикою.
- Не вважати session-to-file relation причинним зв'язком.
- Не робити один глобальний merge lock єдиним механізмом координації. Потрібні granular resource locks та автоматичний conflict analysis.
- Не давати агентам безконтрольну можливість записувати глобальні інструкції в shared memory.

---

## 3. Продуктова модель: чотири площини та контекстний компілятор

### 3.1 Work Plane — керування роботою

Відповідає на питання: що робити, чому, у якому порядку, хто має право працювати та як визначається готовність.

Основні сутності:
- `WorkItem` — атомарна задача, bug, feature, spike, review, decision, incident, chore.
- `WorkGraphEdge` — `blocks`, `parent_of`, `discovered_from`, `related_to`, `implements`, `validates`, `supersedes`.
- `WorkflowDefinition` — повторно використовуваний DAG із параметрами, політиками та gates.
- `WorkflowRun` — конкретна інстанція workflow.
- `Gate` — human approval, timer, command/CI, external signal, dependency completion.
- `AcceptanceCriterion` — машинно/людино-перевірюваний критерій завершення.

`ready` має повертати не просто відкриті задачі, а задачі, для яких одночасно:

- усі hard blockers завершені;
- агент має capability/permission;
- немає активного lease іншого агента;
- потрібні resources доступні;
- branch/base revision актуальна або допускається rebase;
- немає unresolved blocking findings;
- budget policy дозволяє наступний крок.

### 3.2 Evidence Plane — події та походження

Відповідає на питання: хто саме зробив/сказав/запустив це, коли, у якій сесії, на якій ревізії та що можна перевірити.

Збирає:
- agent session lifecycle;
- user/agent/tool event metadata;
- tool calls і exit codes;
- task claim/handoff/state transitions;
- Git commits, branches, diffs, trailers;
- test/lint/build results;
- decisions та approvals;
- symbol/file touch observations;
- model invocation metadata та usage;
- capture gaps / adapter failures.

**Event ≠ Fact ≠ Decision.** Подія — те, що сталося. Fact — твердження, отримане з подій і підтверджене визначеним способом. Decision — нормативний вибір, який має автора, статус, причину та життєвий цикл.

### 3.3 Semantic Plane — знання про код

Відповідає на питання: які структурні одиниці існують, що від чого залежить, які контракти діють і які зміни можуть мати побічний вплив.

Типи вузлів:
- project, repository, package, module, directory;
- file, class, method, function, type, constant, route, schema, migration;
- interface, contract, invariant, decision;
- test, test suite, build target;
- external dependency, service, API endpoint;
- work item, session, commit, changeset, evidence.

Типи ребер:
- `contains`, `defines`, `imports`, `calls`, `implements`, `extends`;
- `reads`, `writes`, `routes_to`, `serializes_as`, `migrates`;
- `tested_by`, `validates`, `violates`, `depends_on`;
- `touched_in`, `introduced_by`, `removed_by`, `reviewed_by`;
- `explains`, `motivated`, `supersedes`, `contradicts`.

Не всі ребра однаково надійні. Кожне ребро має provenance, confidence class, observed revision, valid-time та status.

### 3.4 Execution Plane — агенти та середовище виконання

Відповідає на питання: хто виконує роботу, в якому workspace, з якими правами, ресурсами та checkpoints.

- Agent Registry та capability profiles.
- Session/Run Manager.
- Git Worktree Manager.
- Process supervisor.
- Sandbox adapter (container/VM/OS policy).
- Checkpoint/recovery manager.
- Merge queue і verification gate.

### 3.5 Context Plane — Context Compiler

Єдиний сервіс, який формує prompt/context package. Не кожен агент має сам вирішувати, які десятки файлів/історій читати.

Вхід:
`project_id + work_item_id? + agent_id + session_id? + target_node? + git_revision + intent + budget + trust_policy`.

Вихід:
- `context_package_id`;
- summary із чітким статусом `complete | partial | no_evidence | stale | blocked`;
- список джерел із refs та ревізіями;
- рішення/контракти/інваріанти;
- пов'язані задачі й незавершені blockers;
- історія релевантних змін і спроб;
- impact set;
- budget estimate і фактичний обсяг;
- warnings: stale, ambiguous, unverified, redacted, conflict.

---

## 4. Джерела істини, persistence та синхронізація

### 4.1 Не одна база для всього

Розділити три категорії зберігання:

| Рівень | Призначення | Джерело істини | Можна відновити? |
|---|---|---|---|
| Versioned project state | Tasks, decisions, contracts, policies, workflow definitions, event envelopes | Git-tracked Cairn data | Так, з Git |
| Runtime state | Locks, heartbeats, active process state, query cache, local indexes | SQLite локального daemon | Так, із ledger + Git |
| Sensitive local evidence | Локальні transcript fragments, encrypted payloads, secrets references | Local-only encrypted store | Лише з локального backup; не синхронізується автоматично |

### 4.2 Рекомендована структура `.cairn/`

```text
.cairn/
  project.yaml                 # stable project UUID, schema, policy refs
  config.yaml                  # shared non-secret configuration
  agents/                      # capability profiles, optional project-level roles
  workflows/                   # reusable workflow definitions
  decisions/                   # accepted/proposed/superseded decision records
  contracts/                   # interfaces, invariants, acceptance contracts
  ledger/
    streams/<agent>/<session>.jsonl  # append-only per-session event streams
    snapshots/                  # deterministic snapshots/checkpoints
    manifests/                 # stream hashes, schema version, project lineage
  exports/                     # optional human-readable JSONL/Markdown exports
  .gitignore                   # ignore runtime/cache/private artifacts
```

Локальні runtime-дані поза versioned state:

```text
<Cairn user data dir>/<project-id>/
  runtime.sqlite
  indexes/
  cache/
  worktrees/
  logs/
  secrets/                     # OS keychain or encrypted local vault references
  private-evidence/             # opt-in, encrypted, never git-tracked
```

Не комітити: SQLite WAL/SHM, pid/port files, process logs, raw transcripts, credentials, model API keys, embeddings cache, temporary worktrees, test artifacts великих розмірів.

### 4.3 Event envelope

Кожна значуща зміна записується як immutable event. Краще окремий stream на agent/session, щоб паралельні агенти не дописували в один JSONL-файл. Merge — об'єднання streams за event ID, а не редагування історичних записів.

```json
{
  "schema": "cairn.event.v1",
  "event_id": "sha256:...",
  "project_id": "prj_...",
  "stream_id": "agent_.../session_...",
  "sequence": 42,
  "occurred_at": "2026-11-19T12:30:00Z",
  "recorded_at": "2026-11-19T12:30:01Z",
  "actor": {"type": "agent", "id": "codex-local-01"},
  "session_id": "ses_...",
  "task_id": "wrk_...",
  "git": {"repo_id": "repo_...", "base": "abc123", "head": "def456"},
  "type": "DecisionAccepted",
  "payload": {},
  "causation_id": "sha256:...",
  "correlation_id": "run_...",
  "parents": ["sha256:..."],
  "integrity": {"payload_hash": "sha256:...", "signature": null}
}
```

Правила:
- `event_id` має бути стабільним та ідемпотентним; використовувати UUIDv7/ULID для первинного ID або hash від stream identity + sequence + canonical payload. Не використовувати timestamp як єдиний ключ.
- `occurred_at` і `recorded_at` розділяють час фактичної події та час її індексації.
- `parents`/causation links формують причинний partial order; wall-clock timestamp не є достатнім для причинності.
- Не переписувати старі події. Виправлення — нова compensating/superseding event.
- Схема версіонується; невідома major schema не може мовчки ігноруватися.
- Усі payloads канонізуються перед hashing; порядок ключів JSON не має впливати на hash.

### 4.4 SQLite projections

SQLite використовується як локальна query engine та транзакційна проєкція ledger:

- WAL mode;
- foreign keys ON;
- FTS5 для повнотекстового пошуку;
- таблиці матеріалізованого стану задач, графа, символів, рішень, evidence, usage;
- окремі таблиці для `projection_offsets` та `schema_migrations`;
- один writer actor/queue у daemon; багато read connections;
- короткі транзакції; ніяких LLM викликів усередині DB transaction;
- rebuild command видаляє projections і відновлює їх із versioned streams + Git refs.

### 4.5 Чому не Dolt у першій версії

Dolt має реальну перевагу Beads: versioned SQL, cell-level merge, branches, remote sync і multi-writer server mode. Але Cairn Unified має інший базовий контракт: immutable evidence streams, локальна робота та Git-native portability. На старті SQLite + immutable events дають меншу операційну складність.

Dolt варто залишити як **опційний backend/adapter для команди**, якщо виміри покажуть, що event stream sync + local projection не забезпечують потрібної швидкості або multi-writer UX. Не будувати власний «Dolt-клон» у першій версії.

### 4.6 Sync та конфлікти

- Локальна робота не вимагає мережі.
- Між машинами — Git push/pull у self-hosted Git remote або перенос теки.
- Sync об'єднує immutable event streams за `event_id`; повторний sync ідемпотентний.
- Порядок подій визначається causality links/sequence per stream, не глобальним часом.
- Дві події з несумісними змінами одного WorkItem не «зливаються» за принципом last-write-wins. Створюється `ConflictDetected` із двома гілками стану та потребою resolution.
- Для lease/lock synchronization між машинами потрібен coordinator endpoint або чіткий single-writer policy. Git merge не є механізмом distributed locking.
- Git merge conflict у project state не можна обходити мовчки; CLI запускає `cairn doctor` і показує точний conflict set.

---

## 5. Ідентичність проєктів, репозиторіїв та символів

### 5.1 Project identity

Поточний Cairn використовує fingerprint набору кореневих комітів, що вирішує частину clone portability проблем, але project identity має бути стабільною незалежно від поточного HEAD.

Нова модель:
- `project_id` — UUID, створений під час `cairn init`, зберігається в `.cairn/project.yaml`.
- `repo_id` — стабільний ID конкретного repository identity, з aliases для URL, local roots, mirrors, forks.
- `lineage_id`/`forked_from` — явний зв'язок між upstream і fork.
- `worktree_id` — окремий ID робочого дерева, який посилається на `repo_id` і base commit.
- `root_commit_fingerprint` — лише доказ/діагностична ознака, не primary identity.

Це дозволяє коректно зв'язувати сесії, які стартували з іншої директорії, у worktree, у fork або в orchestrator repo.

### 5.2 Symbol identity

Не використовувати line numbers як основний ID. Symbol record має:

- language, fully qualified name, symbol kind;
- normalized signature;
- enclosing symbol path;
- file path at revision;
- AST fingerprint / signature hash;
- repository ID та revision range;
- optional rename lineage (`renamed_from`, `renamed_to`);
- extractor name/version і parse status.

Після rename/move символ не обов'язково має той самий textual name. Identity resolution використовує послідовно:
1. AST/signature fingerprint;
2. enclosing scope + normalized signature;
3. Git rename/copy detection;
4. structural similarity;
5. LLM suggestion лише як candidate, не як автоматичний факт.

### 5.3 Мовні адаптери

- Python: `ast` (повний parser стандартної бібліотеки).
- PHP: `nikic/php-parser` або Tree-sitter PHP; не залишати regex scanner як фінальний parser.
- TypeScript/JavaScript: TypeScript compiler API або Tree-sitter.
- Go: `go/parser`/`go/types`.
- Rust: `syn`/rust-analyzer protocol або Tree-sitter.
- Інші мови: Tree-sitter fallback з `parse_confidence`.

Кожен extractor повертає typed AST nodes, source spans, diagnostics, stable fingerprints, parent-child relations. Якщо parse не вдався — створюється file-level node з `parse_status=degraded`, а не фальшивий symbol graph.

---

## 6. Work Graph та workflow engine

### 6.1 WorkItem schema

```yaml
id: wrk_<stable-id>
project_id: prj_<id>
title: "Refactor payment adapter"
type: task # bug|feature|task|epic|spike|review|decision|incident|chore
status: ready # draft|open|ready|claimed|in_progress|blocked|in_review|done|cancelled|deferred
priority: 0 # 0 critical .. 4 backlog
created_by: human:...
owner: agent:codex-local-01
acceptance:
  - id: ac_1
    description: "All payment adapter tests pass"
    verifier: command
    command: "pytest tests/payments"
    required: true
scope:
  files: ["src/payments/**"]
  symbols: ["PaymentGateway::authorize"]
base_revision: "abc123"
workflow_id: wf_implementation
budget:
  max_model_cost_usd: 0
  max_context_tokens: 12000
metadata: {}
```

Статуси не повинні бути вільним текстом без семантики. Custom statuses мають категорію `ready|active|blocked|terminal|frozen`, щоб `ready` та SLA працювали однозначно.

### 6.2 Типи ребер

| Edge | Значення | Впливає на ready? |
|---|---|---|
| `blocks` | hard dependency | Так |
| `parent-child` | decomposition | Через parent policy |
| `discovered-from` | знайдено під час роботи над іншою задачею | Ні |
| `related-to` | тематичний зв'язок | Ні |
| `implements` | задача реалізує contract/decision | За policy |
| `validates` | тест/задача перевіряє вимогу | За policy |
| `touches` | зміна зачіпає symbol/file | Ні |
| `supersedes` | новий decision замінює старий | Змінює актуальність |
| `contradicts` | два твердження несумісні | Може блокувати |
| `caused-by` | причинний зв'язок із доказом | Ні, лише якщо provenance підтверджений |

Не трактувати всі зв'язки як dependency. DAG scheduling і semantic knowledge graph мають спільні IDs, але різну семантику.

### 6.3 Claim, lease та fencing

Кожна задача може бути захоплена одним активним owner (або групою з explicit shared lease policy).

Lease fields:
- `lease_id`, `resource_id`, `owner_agent_id`, `run_id`;
- `issued_at`, `expires_at`, `heartbeat_at`;
- `fencing_token` — монотонно зростаючий номер для ресурсу;
- `scope` — task/file/symbol/module/merge queue;
- `reason` і `policy`.

Правила:
- Claim атомарний у daemon transaction.
- Heartbeat поновлює lease лише чинному owner.
- Після expiry новий owner отримує більший fencing token.
- Будь-який write/commit від старого fencing token відхиляється merge gate.
- Lease expiry не означає автоматичне завершення задачі; задача переходить у `needs_recovery`/`orphaned_run` до reconciliation.
- Agent identity — окрема сутність із provider, model, capabilities, host, workspace, trust tier, resource limits.

### 6.4 Semantic resource locks

На відміну від одного merge slot, Cairn захоплює ресурси на різних рівнях:

- Task lock — конкретна задача.
- File lock — файл, якщо агент змінює весь файл або formatter торкається його цілком.
- Symbol lock — метод/клас/функція для локальної зміни.
- Contract lock — інтерфейс/схема/публічний API.
- Module lock — широкий refactor або зміна shared config.
- Global integration lock — merge queue / release.

Lock conflict визначається не тільки однаковим path. Наприклад, зміна signature методу може блокувати інший агентський patch, який змінює його callers. Impact graph має підвищувати scope lock до спільного контракту.

### 6.5 Workflow formulas та gates

Workflow definitions зберігаються в TOML/YAML/JSON, але мають typed schema і version.

Приклад:

```yaml
id: wf_feature_delivery
version: 1
inputs: [feature_spec, target_module]
steps:
  - id: inspect
    kind: agent_task
    output: impact_report
  - id: design
    kind: agent_task
    depends_on: [inspect]
    output: design_record
  - id: implement
    kind: agent_task
    depends_on: [design]
    output: changeset
  - id: verify
    kind: command_gate
    depends_on: [implement]
    command: "pytest && ruff check ."
  - id: review
    kind: human_gate
    depends_on: [verify]
  - id: merge
    kind: merge_gate
    depends_on: [review]
```

Workflow engine повинен підтримувати:
- static DAG validation та cycle detection;
- parameter substitution із schema validation;
- conditional branches;
- human/timer/command/CI/webhook gates;
- resumable checkpoints;
- ephemeral runs для дрібних housekeeping задач;
- durable runs для release/migration/long-running workflows;
- retry policy з backoff та idempotency keys;
- explicit compensation steps для незворотних операцій.

---

## 7. Evidence, рішення та причинний зв'язок

Це найважливіший шар для усунення головної слабкості обох систем.

### 7.1 Рівні доказовості

Кожен факт/зв'язок отримує `evidence_class`:

| Клас | Назва | Приклад | Чи можна подавати як факт? |
|---|---|---|---|
| E0 | Unobserved | Немає запису | Ні |
| E1 | Session association | Сесія торкнулася файла | Лише як association |
| E2 | Explicit reference | Агент/людина пов'язали task/decision із symbol/commit | Так, як заявлений зв'язок |
| E3 | Artifact corroborated | Git diff/commit/AST підтверджує фактичну зміну | Так, у межах доказу |
| E4 | Verified outcome | Тести/CI/review підтвердили очікуваний результат | Так, із зазначенням scope тесту |
| E5 | Human accepted | Людина затвердила рішення/результат | Так, як затверджений намір, але не як доказ runtime поведінки |

Не перетворювати `E1` на `E3` або `E4` через LLM summary.

### 7.2 Decision lifecycle

Decision — окрема сутність, не коментар і не звичайна пам'ять.

Поля:
- `decision_id`, `title`, `statement`, `rationale`;
- `status`: `proposed | accepted | rejected | superseded | revoked | expired`;
- `author`, `approver`, `created_at`, `accepted_at`;
- `valid_from`, `valid_until` (valid-time);
- `recorded_at`, `supersedes_id` (transaction-time/history);
- `scope`: project/module/symbol/contract;
- `evidence_refs`, `related_work_items`, `affected_revisions`;
- `reversal_reason` і `reversal_event_id`.

Важливо: decision не стає неактуальним просто через вік. Він стає stale, коли код/контракт/залежність змінились так, що його premise більше не підтверджується; або коли є explicit supersede/revoke.

### 7.3 Causality chain

Найкращий ланцюг:

`WorkItem → Session/Run → Decision/Plan → Changeset → Commit → Symbol/File diff → Verification → Outcome`

Зв'язок формується з кількох сигналів:
- Task ID у run context і commit trailer (`Cairn-Task: wrk_...`).
- Session/run ID у event envelope.
- Git commit ancestry та diff.
- AST-level symbol diff.
- Tool events, що створювали/редагували файли.
- Tests/CI, прив'язані до changeset.
- Explicit decision refs у task/commit/PR description.

Якщо бракує ланки, система показує саме те, чого бракує. Наприклад: «сесія змінила метод; коміт знайдено; task link відсутній; причинність не доведена».

### 7.4 Outcome tracking

Для кожного WorkItem/Changeset зберігати:
- rework count;
- revert/cherry-pick/reopen count;
- test failure before/after;
- defect recurrence window;
- time-to-merge;
- review iteration count;
- regression incidents;
- budget consumed;
- context retrieval cost.

Це дозволить вимірювати не тільки дешевизну пошуку, а й якість рішень та скорочення повторної роботи.

---

## 8. Session capture та міжагентна пам'ять

### 8.1 Не залежати від compaction

Коротка сесія або subagent може завершитися до compaction. Тому capture має бути event-driven:

1. `SessionStarted` — агент, project, workspace, git base, task.
2. `ToolCallStarted/Completed` — назва інструмента, тип операції, path/symbol candidates, exit code, duration; payload мінімізований.
3. `FileMutationObserved` — до/після hash, diff reference, agent/run.
4. `DecisionProposed/Accepted` — якщо агент/людина явно створює decision.
5. `CheckpointCreated` — поточний стан задачі, completed steps, pending questions, uncommitted diff hash.
6. `SessionEnded` — reason, final status, capture watermark.
7. `SessionRecovered` — якщо завершення не було зафіксоване, daemon відновлює останній checkpoint і позначає неповні дані.

Compaction summary — додатковий artifact, а не єдиний спосіб зберегти знання.

### 8.2 Raw transcripts

За замовчуванням:
- не копіювати повний transcript у `.cairn`;
- зберігати event metadata, tool names, path/symbol refs, timestamps, exit codes, task/run IDs;
- зберігати compacted summary лише після schema validation і secret scanning;
- raw transcript pointer зберігати локально, якщо він доступний і користувач дозволив;
- не переносити raw transcript між машинами без окремої політики та encryption.

Режими capture:
- `metadata_only` — default;
- `redacted_summary` — compact context після redaction;
- `encrypted_local_archive` — opt-in;
- `full_audit` — тільки explicit opt-in для контрольованих середовищ.

### 8.3 Перехід рішення між сесіями

Рішення прив'язується не до сесії, а до `decision_id` та `scope`. Наступна сесія знаходить його через:
- WorkItem link;
- target symbol/module;
- commit/change set;
- contract/invariant graph;
- workspace-level context retrieval.

Таким чином сесія A може сформувати decision, сесія B — реалізувати код, а сесія C — перевірити результат без копіювання summary вручну.

### 8.4 Capture adapters

Adapter contract має бути стабільним і мінімальним:

```python
class AgentAdapter(Protocol):
    name: str
    def discover(self, environment: AdapterEnvironment) -> list[SessionSource]: ...
    def read_events(self, source: SessionSource, cursor: Cursor | None) -> Iterator[NormalizedEvent]: ...
    def healthcheck(self) -> AdapterHealth: ...
```

Adapter повинен:
- виявити справжнє джерело журналу, а не вигадати порожній success;
- підтримувати incremental cursor;
- мати stable event ID, якщо джерело його надає;
- явно повідомляти unsupported event types;
- мати fixture tests для кожної підтримуваної версії формату;
- бути fail-closed для capture integrity, але fail-open для основної роботи агента: помилка Cairn не повинна ламати редагування коду.

---

## 9. Semantic Engine та retrieval

### 9.1 Index pipeline

`Repository scan → file inventory → language detection → AST parse → symbol extraction → import/call graph → Git history mapping → test mapping → index projection`.

Індекс інкрементальний:
- hash кожного файла;
- commit/tree identity;
- parser version;
- content fingerprint;
- parse status;
- dirty invalidation queue;
- rebuild only changed files + dependents where required.

### 9.2 Search strategy

Не покладатися лише на embeddings. Рекомендований retrieval stack:

1. Exact ID/path/symbol lookup.
2. FTS5/BM25 для тексту, decisions, task descriptions, comments, summaries.
3. Symbol graph traversal для caller/callee/import/interface/test neighbors.
4. Git history lookup для відповідного revision range.
5. Optional local embeddings для semantic recall.
6. Optional reranker, якщо local model доступна і budget дозволяє.

Embeddings не можуть визначати істинність, актуальність чи довіру. Вони лише пропонують кандидатів.

### 9.3 Retrieval scoring

Початковий прозорий score (не «магічний» LLM score):

`score = relevance × evidence_weight × freshness_weight × scope_weight × trust_policy_weight`

Де:
- relevance — exact symbol/path/task match, FTS score, graph distance;
- evidence_weight — E0..E5 class;
- freshness_weight — current revision compatibility;
- scope_weight — project/module/symbol proximity;
- trust_policy_weight — дозволений рівень джерела для поточної операції.

У UI та agent output треба показувати, чому елемент потрапив у результат: exact symbol match, explicit task link, same commit, test evidence, semantic neighbor тощо.

### 9.4 Freshness та memory decay

Не видаляти історичні рішення через TTL. Розділити:
- `validity`: active/superseded/revoked;
- `freshness`: current/stale/unknown;
- `confidence`: evidence class;
- `retrieval_priority`: наскільки корисно це зараз.

Freshness перевіряється проти поточного commit та contract hash. Якщо метод змінив signature, рішення, прив'язане до старої signature, отримує `stale_scope` до повторної перевірки.

---

## 10. Context Compiler та бюджет

### 10.1 Pipeline

1. Parse intent: explain/implement/debug/review/refactor/plan.
2. Resolve project, repo, worktree, git revision.
3. Resolve task and target symbols.
4. Retrieve direct evidence.
5. Expand bounded graph neighborhood.
6. Validate validity/freshness/trust.
7. Rank candidates.
8. Build context package from atomic evidence blocks.
9. Estimate token count for target model/tokenizer.
10. Apply hard budget policy.
11. Return package + citations/provenance + omissions.

### 10.2 Budget policy

Budget units:
- tokens when tokenizer adapter is available;
- conservative upper-bound estimate when unavailable;
- bytes/chars only as secondary diagnostics.

Hard rules:
- budget check before full output construction;
- never exceed configured hard token cap;
- do not truncate evidence mid-block;
- omit low-priority blocks whole and report omission;
- `ask` policy returns zero additional context until approved;
- `allow` requires explicit policy and labels output over-budget;
- reserve fixed overhead for system instructions, MCP schema, metadata and response wrappers;
- report `estimated`, `measured`, `unknown` separately.

### 10.3 Budget allocation

Default profile for an implementation task (configurable):
- 10% task/acceptance criteria;
- 20% architecture/contracts/decisions;
- 25% target symbol and direct code neighbors;
- 20% relevant history and failed attempts;
- 15% tests/verification/impact;
- 10% safety/unknowns/omissions.

Allocation is adaptive: if no prior evidence exists, do not fabricate history; reallocate to contracts/current code references and clearly state `no_evidence`.

### 10.4 Cache

Cache key includes:
`project_id + target_revision + task_id + target_node + intent + trust_policy_version + budget_profile + index_version`.

Invalidate on:
- relevant symbol hash change;
- decision supersede/revoke;
- contract change;
- task state change;
- new evidence with higher trust/freshness;
- permission policy change.

---

## 11. Coordination, execution та conflict prevention

### 11.1 Agent Registry

Agent identity is not just a string assignee.

```yaml
agent_id: agt_codex_local_01
provider: openai
runtime: codex_cli
model: local-or-configured-model
host_id: host_dev_01
capabilities: [python, php, git, tests, refactor]
trust_tier: project_worker
max_parallel_runs: 1
workspace_policy: isolated_worktree
network_policy: disabled
```

Sensitive runtime config is local-only. Shared project config contains capabilities and roles, not credentials.

### 11.2 Worktree isolation

Each run receives:
- dedicated branch `cairn/<task-id>/<agent-id>/<run-id>`;
- dedicated worktree;
- immutable base revision;
- assigned task and lock scopes;
- sandbox policy;
- output artifacts directory.

### 11.3 Conflict classes

1. Textual conflict — Git cannot merge overlapping lines.
2. Semantic conflict — two changes modify same symbol/interface/invariant even if textual merge succeeds.
3. Stale-base conflict — agent changed code against old base revision.
4. Contract conflict — change violates declared API/schema/invariant.
5. Ownership conflict — write outside assigned scope or expired lease.
6. Behavioral conflict — tests/regression/verification fail.
7. Data migration conflict — incompatible schema/data migration assumptions.

### 11.4 Merge gate

Before integration:
- validate lease and fencing token;
- verify base revision / rebase policy;
- inspect Git diff and AST symbol diff;
- compute impact set (callers, dependents, contracts, tests);
- run required test gates;
- check secrets and forbidden files;
- verify task acceptance criteria;
- require human approval for configured risk classes;
- create merge report with evidence refs.

No system can guarantee zero conflicts. Cairn must prevent avoidable overlap, detect hidden semantic conflicts, and refuse unsafe merges with actionable diagnostics.

### 11.5 Recovery

For every run, checkpoint:
- task/workflow step;
- branch/worktree;
- base/head commit;
- changed file hashes;
- completed verification;
- pending tool invocation (if safe to retry);
- lease state;
- budget spent;
- last accepted context package.

On crash:
1. detect missing heartbeat;
2. mark run `orphaned`;
3. validate worktree and uncommitted diff;
4. preserve diff snapshot;
5. invalidate old lease/fencing token;
6. offer resume/reassign/abort;
7. never silently delete an uncommitted worktree.

---

## 12. Security, trust та privacy

### 12.1 Threat model

- Prompt injection embedded in source files, issue descriptions, comments, transcripts, docs, test fixtures.
- Malicious or compromised agent writing false memory/decision.
- Secret leakage through transcript, logs, MCP arguments, context package, Git commits.
- Agent executing arbitrary commands with developer privileges.
- Stale or malicious event stream imported from another clone.
- Cross-project data leakage due path/repo identity collision.
- Untrusted MCP client attempting unauthorized write/read.

### 12.2 Trust boundaries

- Project repository data is untrusted input, not policy.
- Agent-generated summaries are claims, not verified facts.
- Only human-approved policy/decision or verified evidence can become high-trust context.
- Tool outputs are evidence with provenance, but not executable instructions.
- Local daemon authorizes every mutation; MCP tool call is not authority by itself.
- Each project is a separate security scope.

### 12.3 Memory promotion policy

`candidate → validated → accepted → superseded/revoked`.

- Agent may propose candidate knowledge.
- Deterministic validators check schema, links, source refs, revision, secret scan, contradiction scan.
- Human approval required for global policy, architecture invariant, security constraint, data migration policy, irreversible decisions.
- Low-risk facts may be promoted automatically only when corroborated by code/test evidence and configured policy.
- A single poisoned session cannot create global instruction.

### 12.4 Secret handling

- Secret scanner runs before persistence and before context export.
- Redaction patterns are versioned and tested; custom enterprise patterns allowed.
- Unknown suspicious payload → quarantine/reject, not silently store.
- Credentials never enter Git-tracked files.
- Optional encryption at rest via OS keychain/TPM-backed key or user-managed key.
- Logs avoid full prompts/responses by default.
- Audit records store hashes/refs when content is not required.

### 12.5 Sandbox

Git Worktree is workspace isolation only. Actual execution must use one of:
- OS-level user/process isolation;
- container runtime with read-only mounts and resource limits;
- VM for untrusted execution;
- network deny-by-default with explicit allowlist;
- filesystem scope allowlist;
- command allow/deny policy.

Do not advertise a sandbox if only a worktree is configured.

### 12.6 Fully self-hosted guarantee

Core mode:
- no telemetry;
- no cloud account;
- no outbound network required;
- no automatic cloud model fallback;
- local model adapter optional;
- explicit endpoint allowlist for any networked provider;
- audit log for outbound connections and model invocation metadata.

Note for Beads evaluation: its docs describe local-first behavior, but its security guidance also notes Dolt usage metrics may contact Dolt's remote endpoint by default unless disabled. Cairn should not inherit that default; offline/no-egress must be a tested invariant.

---

## 13. Universal Agent Gateway

### 13.1 Interfaces

- CLI — lowest overhead, scriptable, ideal for hooks.
- MCP server — standard agent-facing interface for tools/resources/prompts.
- Local HTTP API — UI and external automation.
- Event adapter — capture from supported agent journals/hooks.

### 13.2 MCP tool design

Keep tools few, composable, narrow and budget-aware:

Read tools:
- `cairn.project_status`
- `cairn.work_ready`
- `cairn.work_get`
- `cairn.context_for_task`
- `cairn.why_node`
- `cairn.impact_analyze`
- `cairn.decision_search`
- `cairn.workflow_get`

Mutation tools:
- `cairn.work_create`
- `cairn.work_claim`
- `cairn.work_update`
- `cairn.work_handoff`
- `cairn.decision_propose`
- `cairn.evidence_attach`
- `cairn.checkpoint`
- `cairn.changeset_submit`

High-risk tools require explicit confirmation / policy gate:
- `cairn.decision_accept`
- `cairn.merge_approve`
- `cairn.policy_update`
- `cairn.project_sync` (if it can transmit data externally)

Do not expose a giant tool surface by default. MCP resources may expose read-only compact project context; prompts should be optional.

### 13.3 Agent compatibility

Support adapters by capability, not by marketing name. For each host, test:
- session start hook;
- pre-edit hook;
- tool-call capture;
- session end/compaction event;
- workspace root discovery;
- permission prompts;
- MCP transport compatibility;
- Windows/macOS/Linux behavior.

If an agent does not expose a stable journal API, use documented hooks or metadata capture. Never rely on private journal format as a stable contract.

---

## 14. UI/UX — Cairn Workbench

UI має зменшувати когнітивне навантаження, а не дублювати Jira/Linear. Основна одиниця інтерфейсу — **поточний стан роботи + докази + наступна безпечна дія**.

### 14.1 Інформаційна архітектура

1. **Overview** — що відбувається зараз: активні runs, ready work, blocked work, unresolved conflicts, budget, recent decisions.
2. **Work** — список/таблиця задач, dependency graph, filters, priority, owner, status, ready frontier.
3. **Task detail** — опис, acceptance criteria, dependency graph, assigned agent, session timeline, changed symbols, test results, decision refs.
4. **Code Map** — модулі/файли/символи, callers/dependencies/contracts, heatmap змін, orphan/unknown nodes.
5. **Why / Evidence Explorer** — для будь-якого вузла показати причини, рішення, історичні спроби, commits, source events, рівень доказовості й актуальність.
6. **Agents & Runs** — registry, capabilities, active/idle/failed agents, leases, checkpoints, worktrees, logs summary.
7. **Merge & Conflicts** — textual/semantic/contract conflicts, impact set, verification status, approve/reject actions.
8. **Decisions & Contracts** — accepted/proposed/superseded decisions, invariants, API contracts, contradiction queue.
9. **Budgets & Usage** — token/cost budgets, actual vs estimated, model routing, per-task/per-agent breakdown.
10. **Settings & Security** — local paths, adapters, permissions, retention, secret scanning, offline mode, backups.

### 14.2 Основний екран

Рекомендований desktop layout:

- Left sidebar: workspace/project navigation.
- Center: task frontier / current work / graph or timeline, залежно від вибраного розділу.
- Right inspector: selected task/node/agent/evidence details.
- Global top bar: project, revision, sync status, active agents, search/command palette.

Не показувати все одночасно. Overview повинен мати 5–7 головних сигналів, а не десятки метрик.

### 14.3 Task detail — ключовий UX

Панелі:
- Goal & acceptance criteria.
- Why this task exists (source issue/decision/incident).
- Dependencies and ready blockers.
- Agent assignment and lease countdown.
- Impact scope: symbols/files/contracts.
- Execution timeline.
- Evidence rail: commit, diff, test, review, source event.
- Budget consumed / remaining.
- Next safe actions: resume, handoff, request approval, re-run verification, merge.

### 14.4 Evidence Explorer — ключова диференціація

Для вибраного методу/символу показувати:
- поточну signature та revision;
- прямі callers/callees;
- пов'язані задачі;
- decisions із status і valid range;
- sessions, які його торкалися (лише як association);
- commits/diffs, що реально змінили symbol;
- tests, що перевіряють symbol;
- повторні правки/reverts/regressions;
- джерело кожного твердження та evidence class.

### 14.5 Взаємодія та accessibility

- Command palette (`Ctrl/Cmd+K`).
- Keyboard-first navigation та shortcuts.
- Search із розумінням task ID, path, symbol, commit, decision ID.
- Table view для power users; graph view для залежностей; timeline view для історії.
- Всі графи мають text/table fallback.
- Підтримка dark/light themes, screen-reader labels, reduced motion.
- UI не є required runtime dependency: агенти мають повноцінно працювати через CLI/MCP.

### 14.6 Що не робити в UI

- Не будувати ще один складний kanban як єдиний екран.
- Не ховати evidence за LLM-generated summary без посилання на джерело.
- Не фарбувати невизначені/неперевірені зв'язки як підтверджені.
- Не показувати token estimates як точну вартість, якщо tokenizer/provider usage невідомі.
- Не створювати граф, у якому неможливо зрозуміти, чому вузол пов'язаний з іншим.

---

## 15. API, CLI та приклади workflow

### 15.1 CLI surface

```bash
# Setup / diagnostics
cairn init
cairn doctor
cairn status
cairn config show

# Work graph
cairn work create "Implement X" --type task --priority 1
cairn work ready --json
cairn work claim wrk_...
cairn work show wrk_...
cairn work handoff wrk_... --to agt_...
cairn work graph wrk_...

# Semantic context
cairn why src/Payments/Gateway.php
cairn why --node 'method:Payments\\Gateway::authorize'
cairn context --task wrk_... --budget 12000
cairn impact --node 'method:Payments\\Gateway::authorize'

# Decisions and evidence
cairn decision propose --file decision.yaml
cairn decision supersede dec_... --with dec_...
cairn evidence show ev_...

# Agent/session lifecycle
cairn agent list
cairn run list
cairn run resume run_...
cairn checkpoint --task wrk_...

# Git / integration
cairn changeset inspect cs_...
cairn merge check cs_...
cairn sync
cairn export --format jsonl
cairn rebuild-index
```

CLI commands should support `--json`, stable exit codes, machine-readable diagnostics, `--dry-run`, idempotency keys for mutations, and explicit `--project` when cwd discovery is ambiguous.

### 15.2 End-to-end example

1. Human asks: «Додай новий спосіб оплати».
2. Planner creates WorkItem and decomposes it into design/implementation/tests/review.
3. `ready` returns design task; coordinator atomically claims it for Agent A.
4. Context Compiler resolves module, relevant symbols, accepted decisions, contract, prior failed attempts, tests and budget.
5. Agent A proposes a decision; human accepts it; decision becomes `accepted` with evidence and scope.
6. Implementation task becomes ready. Agent B claims it and gets a worktree + fencing token.
7. Pre-edit hook injects `why_node` package before editing an existing symbol.
8. Agent B creates changeset; Cairn computes AST-level symbol diff and impact set.
9. Verification runs required tests and contract checks.
10. Merge gate checks lease, base revision, diff, tests, secrets and policy.
11. On merge, events connect task → session → decision → changeset → commit → symbol → tests.
12. Next agent asks why the method has this structure and gets concise, revision-aware evidence instead of a transcript dump.

---

## 16. Technology stack та deployment

### 16.1 Recommended stack

| Layer | Choice | Reason |
|---|---|---|
| Core/runtime | Python 3.12+ | Cairn continuity, adapter ecosystem, fast iteration |
| CLI | Typer or argparse | Scriptable, stable CLI |
| Local API | FastAPI | Typed local API; not required for CLI-only mode |
| Validation | Pydantic v2 / JSON Schema | Strict event and tool contracts |
| Local state | SQLite WAL + FTS5 | Small footprint, transactional, rebuildable |
| Event format | JSONL/JSON canonical envelopes | Portable, inspectable, append-only |
| VCS | Git CLI/libgit2 adapter | Source history, worktrees, merge operations |
| Parsing | Native parsers + Tree-sitter fallback | Structural code graph |
| Agent protocol | MCP | Cross-agent tool interface |
| UI | TypeScript + React + lightweight graph renderer | Local workbench |
| Tests | pytest + property-based tests | Determinism, recovery, concurrency |
| Packaging | uv/pyproject + platform binaries later | Reproducible installs |

### 16.2 Deployment modes

- **Solo local:** one daemon, SQLite, one or more agents, local worktrees.
- **Local multi-agent:** one local coordinator daemon; multiple worker processes; one SQLite writer queue.
- **LAN team (later):** self-hosted coordinator service with authenticated clients; central lease authority; Git remote for history sync.
- **Offline:** all core flows run without network; sync queue pauses; no cloud fallback.

### 16.3 Process model

```text
Cairn CLI / MCP / UI
        |
Local API + AuthZ
        |
Cairn Coordinator Daemon
  |       |        |         |
Work   Context  Evidence   Execution
Engine  Engine   Engine    Manager
  |       |        |         |
SQLite projections + FTS5 + Git + Event streams
                            |
                  Worker processes / sandboxes
```

### 16.4 Resource limits

- Configurable max workers and per-agent concurrency.
- Max event size, summary size, context package size.
- Max index CPU/memory and incremental scan batch size.
- Max retained runtime logs; rotation.
- Bounded retry queues and timeouts.
- Backpressure: if indexer lags, queries report index revision rather than silently claim freshness.

---

## 17. Data model та SQLite starter schema

Це стартова логічна схема, а не фінальна migration. Event ledger лишається authoritative; таблиці нижче — проєкції для швидкого читання.

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE projects (
  project_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  root_revision TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE agents (
  agent_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  runtime TEXT NOT NULL,
  model TEXT,
  trust_tier TEXT NOT NULL,
  capabilities_json TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE work_items (
  work_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  title TEXT NOT NULL,
  type TEXT NOT NULL,
  status TEXT NOT NULL,
  priority INTEGER NOT NULL,
  owner_agent_id TEXT REFERENCES agents(agent_id),
  base_revision TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE TABLE work_edges (
  edge_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  target_id TEXT NOT NULL,
  edge_type TEXT NOT NULL,
  created_event_id TEXT NOT NULL,
  evidence_class TEXT NOT NULL
);

CREATE TABLE decisions (
  decision_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  status TEXT NOT NULL,
  scope_json TEXT NOT NULL,
  statement TEXT NOT NULL,
  rationale TEXT,
  valid_from TEXT,
  valid_until TEXT,
  supersedes_id TEXT,
  author_id TEXT NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE TABLE symbols (
  symbol_id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL,
  path TEXT NOT NULL,
  language TEXT NOT NULL,
  qualified_name TEXT NOT NULL,
  kind TEXT NOT NULL,
  signature_hash TEXT,
  ast_hash TEXT,
  revision TEXT NOT NULL,
  parse_status TEXT NOT NULL
);

CREATE TABLE evidence (
  evidence_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  evidence_class TEXT NOT NULL,
  source_event_id TEXT NOT NULL,
  target_ref TEXT,
  revision TEXT,
  observed_at TEXT NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE TABLE leases (
  resource_id TEXT PRIMARY KEY,
  lease_id TEXT NOT NULL,
  owner_agent_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  fencing_token INTEGER NOT NULL,
  expires_at TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  scope_json TEXT NOT NULL
);

CREATE TABLE runs (
  run_id TEXT PRIMARY KEY,
  work_id TEXT,
  agent_id TEXT NOT NULL,
  session_id TEXT,
  status TEXT NOT NULL,
  worktree_path TEXT,
  base_revision TEXT,
  head_revision TEXT,
  checkpoint_ref TEXT,
  started_at TEXT NOT NULL,
  ended_at TEXT
);

CREATE TABLE usage_records (
  usage_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  provider TEXT,
  model TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  estimated_cost REAL,
  reported_cost REAL,
  cost_status TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE projection_offsets (
  stream_id TEXT PRIMARY KEY,
  last_event_id TEXT,
  last_sequence INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE evidence_fts USING fts5(
  evidence_id UNINDEXED,
  text,
  tokenize='unicode61'
);
```

Додатково потрібні migrations, indexes, graph integrity checks, unique constraints для idempotency, transaction-level event append/projection update, та `doctor` для відновлення.

---

## 18. Вісім контрольних питань — прямі відповіді

### 1. Короткі сесії: що зберігається, якщо subagent завершується до compaction?

**Відповідь:** не покладатися на compaction. Tool/session hooks та daemon записують події інкрементально: task/run/session identity, touched paths/symbols, tool result metadata, exit codes, checkpoints, diff hashes. SessionEnd лише закриває run і запускає reconciliation. Якщо процес зник без SessionEnd, heartbeat watchdog створює `orphaned_run`, зберігає останній checkpoint і виявлений diff. Compact summary — необов'язковий додатковий artifact.

### 2. Різні сесії: як рішення першої сесії доходить до вузла коду, який змінює друга?

**Відповідь:** через окремий `Decision` із `decision_id`, scope та explicit links до task/module/symbol/contract. Перша сесія створює й пропонує decision; після acceptance він стає доступним у графі. Друга сесія отримує його через `context_for_task`/`why_node`. Зв'язок підтверджується через task/run/commit/diff, а не через копіювання текстового summary.

### 3. Сусідні проєкти: як зв'язати журнали orchestrator repo з кодовим repo?

**Відповідь:** `project_id` і `repo_id` відокремлені від робочої директорії. Workspace registry має aliases для local roots, git remotes, worktrees, mirrors, forks та submodules. Orchestrator run може посилатися на один або кілька `repo_id`; кожна file/symbol подія має конкретний repository identity та revision. Немає автоматичного зв'язування лише за однаковим relative path.

### 4. Скасування рішення: як агент відрізняє старий пересказ від чинного?

**Відповідь:** Decision lifecycle із `accepted`, `superseded`, `revoked`, valid-time та `supersedes_id`. Старе рішення не переписується й не видаляється: воно залишається в історії, але виключається з active context. Якщо кодова передумова змінилася, рішення маркується `stale` до повторної перевірки. Контекстний компілятор за замовчуванням повертає тільки active/current decisions, а історичні показує окремо.

### 5. Результат: чи вимірюється outcome, а не тільки ціна пошуку?

**Відповідь:** так, це обов'язковий acceptance area. Метрики: repeated edits per symbol, reopen/revert rate, regression recurrence, failed verification count, review iterations, time-to-merge, task completion quality, cost per accepted change. Потрібні baseline і контрольна група; не можна приписувати покращення Cairn без експериментального дизайну.

### 6. База порівняння: що входить у ручний шлях і як порівнювати з `git log -L`?

**Відповідь:** benchmark має мати щонайменше чотири шляхи з однаковим запитанням і однаковим target scope:

- A: `git log --follow -- <file>` + commit messages;
- B: `git log -p --follow -- <file>`;
- C: `git log -L :<funcname>:<file>` або еквівалент для історії конкретного методу;
- D: Cairn `why_node`/`context_for_task`.

Окремо вимірювати: input tokens, latency, relevant evidence recall, precision, completeness, hallucination/unsupported claim rate, answer quality by blinded reviewers. `git log -L` — важливий baseline для функцій, але він не завжди відновлює intent, рішення, відкинуті альтернативи або міжсесійний handoff.

### 7. Втрати стиснення: яка частка відкинутих варіантів і вимірів переживає summary?

**Відповідь:** новий Cairn не повинен вважати summary повним архівом. Зберігається provenance index і структуровані evidence references до source event IDs; summary є похідним view. Для кожної сесії треба вимірювати retention: важливі decisions, alternatives, failed attempts, test measurements, constraints, unresolved questions. Gold annotations на вибірці сесій покажуть recall. Якщо raw transcript недоступний/не збережений, система чесно вказує `capture_gap` і не заявляє повноту.

### 8. Довіра: що заважає отруєній сесії стати інструкцією для всіх майбутніх агентів?

**Відповідь:** розділення event/fact/decision, trust tiers, evidence classes, quarantine, schema validation, secret scan, contradiction detection, explicit approval для policy/invariant, revision-aware validity. Агент може створити candidate, але не може самостійно надати йому статус глобальної accepted instruction. Retrieval policy виключає untrusted content із normative instructions; за потреби його можна подати як цитований, неперевірений historical claim.

---

## 19. Потолок можливостей об'єднаного Cairn

### 19.1 Що система може забезпечити конструктивно

- Робочий стан переживає сесії, агенти, машини та перезапуски.
- Від задачі можна перейти до рішень, комітів, символів, тестів і результату.
- Агент отримує контекст, релевантний конкретній задачі та revision, із provenance.
- Система автоматично знаходить ready work і безпечно розподіляє його між агентами.
- Короткі сесії не гублять усі значущі події, якщо adapter має доступ до їхніх hooks/events.
- Рішення можуть бути замінені/скасовані без знищення історії.
- Контекст може бути обмежений точним або консервативним token budget.
- Можна відновити runtime projections із portable event ledger.
- Можна виміряти повторні правки, відкот, regression та реальну якість workflow.

### 19.2 Чого система не може гарантувати

- Повну історію, якщо агент не експортує журнал і не має hooks/API.
- Абсолютну причинність із самих лише timestamps/path touches.
- Відсутність усіх semantic conflicts: AST та tests мають неповне покриття.
- Відсутність hallucinations від LLM; можна обмежити їх доказами та policy.
- Повну безпеку довільного коду без реального sandbox.
- Ідеальну переносність приватних журналів, якщо формат агента змінюється або дані видаляються.
- Нульову вартість: індексація, embeddings, reranking, локальні моделі й storage мають вимірювану ціну.

### 19.3 Найвища цінність

Головна перевага — не «пам'ять на всі випадки», а **зв'язаний, перевірюваний стан розробки**. Beads відповідає на «що робимо і що готове?». Cairn відповідає на «чому цей код такий і що вже пробували?». Unified Cairn відповідає на «що безпечно робити далі, які докази й обмеження потрібні агенту, що зміниться, хто має право це змінити та як перевірити результат?».

---

## 20. Benchmark та критерії успіху

Не заявляти «кращий за все існуюче» до незалежного порівняння. Специфікація задає протокол, за яким можна довести або спростувати перевагу.

### 20.1 Benchmark corpus

- PHP 8.4 CMS/monolith із великим Git history — обов'язковий PoC.
- Python service із tests і CI.
- TypeScript frontend/API.
- Multi-repo workspace з orchestrator repo, library repo та application repo.
- Synthetic adversarial corpus: renamed symbols, deleted files, duplicate paths, stale decisions, poisoned session, secret-bearing transcript, concurrent edits.

### 20.2 Baselines

- Manual investigation.
- `git log` / `git log -p` / `git log -L`.
- Beads `bd prime` + `bd ready` + `bd show`/history.
- Cairn current `why`/symbol context.
- Unified Cairn context compiler.
- Optional LangGraph checkpoint store / Graphiti temporal retrieval / Letta-style memory as separate comparison categories, not necessarily drop-in coding-agent trackers.

### 20.3 Quality metrics

| Metric | Що вимірює | Ціль MVP |
|---|---|---:|
| Context precision@k | Частка релевантних evidence items | ≥ 0.85 |
| Context recall@k | Частка gold evidence, знайдених системою | ≥ 0.80 |
| Unsupported claim rate | Твердження без source evidence | ≤ 2% у контрольованому eval |
| Stale decision leakage | Неактуальні decisions у normative context | 0 у тестах |
| Cross-project contamination | Чужі сесії/paths у context | 0 у тестах |
| Budget overrun | Перевищення hard cap | 0 |
| Duplicate event ingestion | Дублікати після повторного sync | 0 |
| Recovery | Відновлення після kill/crash | Усі acknowledged events |
| Concurrent claim | Подвійний owner | 0 |
| Merge gate bypass | Commit без обов'язкових checks | 0 |
| Secret leakage | Secrets у tracked store/context | 0 у тестовому корпусі |

Цільові числа — acceptance thresholds для MVP, а не вже виміряні результати.

### 20.4 Outcome experiment

Провести A/B або crossover experiment на схожих задачах:
- Control: агент працює з репозиторієм, Git history та стандартними task notes.
- Treatment: агент отримує Unified Cairn context і work graph.

Вимірювати:
- кількість повторних правок того самого symbol;
- скільки разів відтворено раніше відкинуте рішення;
- число reopen/revert;
- дефекти після merge;
- час від task start до accepted change;
- токени/вартість пошуку та виконання;
- blinded reviewer score за correctness і completeness.

Розмір вибірки визначити після pilot та power analysis. Не робити висновок за трьома файлами або одним репозиторієм.

---

## 21. Roadmap та implementation backlog

### Phase 0 — Repository and contract audit

- Зафіксувати commit Cairn 0.x як baseline.
- Пройти всі наявні tests і повторити `docs/MEASUREMENTS.md`.
- Створити corpus із реальними, але очищеними сесіями.
- Перевірити кожен adapter, schema, redaction rule, merge path.
- Підготувати ADR та migration compatibility policy.

**Exit:** reproducible baseline, відомі дефекти, frozen fixtures, threat model.

### Phase 1 — Event ledger + project identity

- Stable project/repo/worktree IDs.
- Event envelope schema v1.
- Per-session append-only streams.
- Event hashing, idempotent merge, schema migrations.
- SQLite projection/rebuild.
- `cairn doctor`, `cairn export`, `cairn sync`.

**Exit:** copy/merge/rebuild produce deterministic state; crash tests pass.

### Phase 2 — Work Graph

- WorkItem CRUD and typed edges.
- Ready frontier and cycle detection.
- Atomic claim, lease, heartbeat, fencing token.
- Agent registry.
- Comments/notes only as non-authoritative discussion.
- JSON CLI and MCP read/write endpoints.

**Exit:** N concurrent agents cannot double-claim one task; expired lease cannot write.

### Phase 3 — Semantic Code Graph

- Python/PHP/TS adapters.
- Stable symbol IDs and rename lineage.
- Git commit/diff mapping.
- Call/import/test graph.
- Incremental indexing.
- FTS5 retrieval and `why_node`.

**Exit:** known precision/recall on reference corpus; parse degradation surfaced.

### Phase 4 — Decisions, evidence and context compiler

- Decision lifecycle and approval.
- Evidence classes and provenance links.
- Revision-aware freshness.
- Context assembly, hard token budget, omissions report.
- Session capture adapters for first two target agents.
- Prompt-injection and secret-handling tests.

**Exit:** all 8 control questions have passing end-to-end tests.

### Phase 5 — Execution and merge safety

- Git worktree lifecycle.
- Sandbox adapter.
- Checkpoint/recovery.
- Semantic locks and impact analysis.
- Verification/merge gates.
- Outcome metrics.

**Exit:** crash recovery and semantic conflict tests pass; no unauthorized merge.

### Phase 6 — Workbench UI

- Overview, Work, Task Detail, Code Map, Evidence Explorer.
- Agents/Runs, Merge Queue, Decisions/Contracts, Budgets.
- Search and command palette.
- Accessibility and text fallback.

**Exit:** primary workflows can be operated without reading raw JSON/CLI output.

### Phase 7 — Multi-machine and scale

- Self-hosted coordinator mode.
- Sync conflict UX and stream integrity verification.
- Optional Dolt backend evaluation.
- Optional local embeddings/reranker.
- Large monorepo benchmarks.

**Exit:** load/performance SLOs met; offline core and egress policy verified.

### 21.1 Перший vertical slice

Не починати з UI чи повного оркестратора. Перший slice має довести наскрізну модель:

1. `cairn init` створює stable project identity.
2. `cairn work create` створює task.
3. Agent session отримує task і claim із lease.
4. `cairn context --task` повертає task + symbol + decision/evidence package з hard budget.
5. Agent змінює один PHP method у worktree.
6. Cairn фіксує diff, symbol mapping, commit/task link і test evidence.
7. Merge gate приймає або блокує changeset.
8. Друга сесія запитує `why <method>` і бачить причину, decision, commit та outcome.
9. `cairn rebuild-index` відновлює той самий стан із Git-tracked ledger.

Цей slice демонструє цілісну відмінність продукту краще, ніж десятки ізольованих команд.

---

## 22. ADR — ключові архітектурні рішення

| ADR | Рішення | Статус |
|---|---|---|
| ADR-001 | Greenfield modular monolith | Proposed |
| ADR-002 | Immutable event streams + rebuildable SQLite projections | Proposed |
| ADR-003 | Git-tracked portable state; private/runtime state excluded | Proposed |
| ADR-004 | Stable project UUID distinct from repo/root commit fingerprint | Proposed |
| ADR-005 | Work Graph and Semantic Graph share IDs but keep distinct edge semantics | Proposed |
| ADR-006 | Evidence classes; no causal inference from session touch alone | Proposed |
| ADR-007 | Decision lifecycle with supersede/revoke and temporal validity | Proposed |
| ADR-008 | Local daemon as lease authority; fencing tokens for stale workers | Proposed |
| ADR-009 | MCP + CLI, CLI-first for token economy | Proposed |
| ADR-010 | No mandatory cloud, vector DB, Dolt, or telemetry | Proposed |
| ADR-011 | Raw transcripts opt-in; metadata-only default | Proposed |
| ADR-012 | Semantic merge gate beyond textual Git merge | Proposed |

---

## 23. Основні ризики та способи зниження

| Ризик | Наслідок | Контроль |
|---|---|---|
| Нестабільні приватні agent journals | Capture зламається після оновлення агента | Versioned adapters, fixtures, health status, documented hooks |
| Summary loses critical nuance | Неповний/помилковий контекст | Evidence refs, retention evaluation, capture gap indicators |
| Memory poisoning | Хибні інструкції всім агентам | Trust tiers, candidate/accepted lifecycle, human approval |
| Symbol identity drift | Історія прив'язана не до того методу | AST fingerprint, rename lineage, revision-aware IDs |
| Multi-agent semantic overlap | Тихі логічні конфлікти | Scope locks, impact graph, AST diff, tests, merge gate |
| Event ledger bloat | Git repo росте | Stream rotation, compression, retention classes, artifacts outside Git |
| SQLite contention | Latency / locked DB | Single writer queue, short transactions, read snapshots |
| Tokenizer mismatch | Budget overrun | Provider tokenizer adapters, conservative estimate, hard output cap |
| Overengineering | Довгий time-to-value | Modular monolith, vertical slice, optional modules |
| Local model quality | Poor extraction/reranking | Deterministic core; local LLM optional, benchmarked |
| Secret leakage | Credential exposure | Redaction, quarantine, no raw transcripts by default, secret tests |
| False causal claims | Agents follow wrong rationale | Evidence classes, explicit causality chain, source citations |

---

## 24. Definition of Done для MVP

MVP вважається готовим лише коли:

- [ ] Повністю працює без зовнішньої мережі.
- [ ] Є стабільний project/repo/worktree identity.
- [ ] Event ledger append-only, portable, idempotent та versioned.
- [ ] SQLite projection можна повністю відновити.
- [ ] Є Work Graph, ready frontier, atomic claim, leases і fencing tokens.
- [ ] Є agent registry з capability/trust profile.
- [ ] Є PHP/Python symbol indexing та Git diff mapping.
- [ ] Є Decision lifecycle із supersede/revoke.
- [ ] Є Evidence class/provenance і чітке розрізнення association vs causality.
- [ ] Є context compiler із hard token budget і omission report.
- [ ] Коротка сесія відновлюється без compaction.
- [ ] Рішення переходить між різними сесіями через ID/scope/evidence.
- [ ] Multi-repo workspace не змішує однакові relative paths.
- [ ] Poisoned session не може створити accepted global policy.
- [ ] Semantic conflict gate виявляє хоча б базові same-symbol/interface conflicts.
- [ ] Crash recovery зберігає acknowledged events і uncommitted diff.
- [ ] Secret scan, offline test та egress audit проходять.
- [ ] Benchmark порівнює Cairn з `git log`, `git log -p`, `git log -L`, Beads workflow і manual baseline.
- [ ] Outcome metrics вимірюють rework/revert/regression, а не лише token savings.

---

## 25. Висновок

Beads і Cairn не є конкурентними реалізаціями одного й того самого шару. Їхня сила в різних частинах життєвого циклу розробки:

- Beads структурує роботу, залежності, ownership, workflow та координацію.
- Cairn стискає й адресує історичний контекст від конкретного вузла коду, зберігаючи переносність і бюджет.
- Суміжні системи додають корисні ідеї: durable checkpoints, temporal facts, long-term memory, context graph та standardized tool interfaces.

**Cairn Unified має з'єднати їх не через спільний список записів, а через спільну модель provenance.** Задача, агентська сесія, рішення, commit, diff, symbol, contract і test result повинні бути частинами одного перевірюваного графа. Це дає системі можливість не тільки пам'ятати минуле, а й планувати наступну дію, обмежувати доступ, запобігати конфліктам, перевіряти зміни та показувати, чому агент отримав саме цей контекст.

Реальна перевага над наявними підходами може бути доведена лише тоді, коли система одночасно покаже:

1. нижчу вартість релевантного контексту;
2. вищу повноту відновлення причин і невдалих спроб;
3. меншу кількість повторних правок та регресій;
4. безпечнішу паралельну роботу кількох агентів;
5. відтворюваність і переносність без vendor lock-in;
6. контрольовану довіру до пам'яті;
7. повністю локальну роботу без прихованих зовнішніх залежностей.

Це і є критерій «потужнішого продукту»: не кількість функцій, а повний, вимірюваний цикл **plan → retrieve evidence → execute → verify → learn → recover**.

---

## 26. Джерела та посилання

### Вихідний Cairn — CommerceCentr

- Repository: https://github.com/CommerceCentr/cairn
- README (UK): https://github.com/CommerceCentr/cairn/blob/main/README.uk.md
- Agent onboarding: https://github.com/CommerceCentr/cairn/blob/main/AGENT-ONBOARD.md
- Measurements: https://github.com/CommerceCentr/cairn/blob/main/docs/MEASUREMENTS.md
- Integration: https://github.com/CommerceCentr/cairn/blob/main/docs/INTEGRATION.md
- Record contract: https://github.com/CommerceCentr/cairn/blob/main/core/contract.py
- Store: https://github.com/CommerceCentr/cairn/blob/main/core/store.py

### Beads

- Repository: https://github.com/gastownhall/beads
- Architecture overview: https://github.com/gastownhall/beads/blob/main/docs/architecture/index.md
- Core concepts / ready work / workflows: https://github.com/gastownhall/beads/blob/main/docs/core-concepts/index.md
- Agent coordination: https://github.com/gastownhall/beads/blob/main/docs/multi-agent/coordination.md
- Configuration: https://github.com/gastownhall/beads/blob/main/docs/reference/configuration.md
- Audit CLI: https://github.com/gastownhall/beads/blob/main/docs/cli-reference/audit.md
- Graph CLI: https://github.com/gastownhall/beads/blob/main/docs/cli-reference/graph.md
- Sync concepts: https://github.com/gastownhall/beads/blob/main/docs/core-concepts/sync-concepts.md
- Sync setup: https://github.com/gastownhall/beads/blob/main/docs/getting-started/sync-setup.md
- MCP integration: https://github.com/gastownhall/beads/tree/main/integrations/beads-mcp
- Security overview: https://github.com/gastownhall/beads/security

### Суміжні архітектури

- LangGraph persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- Letta stateful agents: https://docs.letta.com/concepts/stateful-agents
- Letta memory/dreaming: https://docs.letta.com/configuration/memory
- Graphiti: https://help.getzep.com/v4/graphiti/getting-started/overview
- Zep context graph: https://help.getzep.com/graph-overview
- MCP tools specification: https://modelcontextprotocol.io/specification/2025-11-25/server/tools

### Примітка щодо ліцензій та запозичень

Документ описує архітектурні концепції на високому рівні та нову незалежну модель. Він не є копією реалізації Beads і не пропонує переносити їхній код. Перед повторним використанням конкретних фрагментів коду, схем, назв команд, документації чи assets потрібно окремо перевірити чинні ліцензії та notices відповідних репозиторіїв. Поточний Cairn README вказує Apache-2.0; Beads має власний LICENSE у своєму репозиторії. Цей документ не є юридичним висновком.
