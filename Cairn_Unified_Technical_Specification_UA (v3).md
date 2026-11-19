# CAIRN UNIFIED 2.0
## Технічна специфікація: семантична операційна система для роботи людей та AI-агентів

**Версія документа:** 2.0  
**Дата дослідження:** 19 листопада 2026 року  
**Статус:** архітектурна специфікація для greenfield-розробки  
**Робоча назва:** Cairn Unified (далі — Cairn)  
**Розгортання:** local-first, self-hosted, offline-capable  
**Цільові агенти:** Claude Code, Codex, Cursor, Gemini CLI, Kimi Code, OpenCode та інші через адаптери  
**Основний інтерфейс:** локальний Workbench + CLI + MCP + HTTP/WebSocket API  

---

# 0. Резюме рішення

Cairn Unified 2.0 — не issue tracker, не чергова «пам'ять для LLM» і не ще один агентський IDE. Це **семантична операційна система проєкту**: спільний шар, який пов'язує наміри, задачі, процеси, сесії, рішення, код, докази, агентів та їхні повноваження.

Продукт має відповідати на шість питань:

1. **Що відбувається?** — актуальний стан задач, планів, процесів і блокувань.
2. **Чому це так?** — походження рішення, його докази, альтернативи, історія й умови чинності.
3. **До чого це належить?** — конкретний проєкт, репозиторій, модуль, символ, контракт, commit або зовнішня система.
4. **Що можна робити далі?** — обчислена множина допустимих і готових дій з урахуванням залежностей, gates, ownership, ресурсів і політик.
5. **Хто має право це робити?** — людина, агент, конкретна сесія, orchestration run або сервіс; із явними межами довіри й повноважень.
6. **Чим доведено результат?** — тести, CI, diff, commit, review, human approval та інші перевірювані артефакти.

## 0.1 Архітектурна теза

Cairn має об'єднати чотири незалежні, але пов'язані графи:

| Граф | Основне питання | Типові об'єкти |
|---|---|---|
| **Work Graph** | Що потрібно зробити і що готове до виконання? | Goal, WorkItem, Epic, Dependency, Assignment, Gate |
| **Semantic Graph** | Що означають об'єкти, як вони пов'язані та за яких умов твердження чинні? | Concept, Symbol, Contract, Decision, Invariant, Fact, Evidence |
| **Execution Graph** | Хто, де, яким інструментом і в межах яких повноважень виконує роботу? | Agent, Session, Run, Step, Lease, Worktree, ToolCall |
| **Evidence/Provenance Graph** | Які події та джерела підтверджують стан? | Event, Commit, Diff, TestResult, Review, Approval, SourceRecord |

Над ними працює **Context Compiler** — детермінований механізм складання мінімального достатнього контексту для конкретної задачі, агента, репозиторію та ревізії. Він повертає не просто текст, а пакет із джерелами, валідністю, рівнем довіри, обмеженнями й вартістю.

## 0.2 Що означає «врахувати Beads», не копіюючи Beads

Beads розглядається як повноцінний функціональний та архітектурний референс, а не як бібліотека команд, яку потрібно механічно перенести. Cairn повинен мати функціональне покриття відповідних класів проблем: типізовані робочі елементи, залежності, обчислення ready frontier, workflow templates, gates, ephemeral work, claims, handoffs, merge serialization, audit, configuration provenance, multi-repo routing та federation.

Водночас Cairn **не копіює** назви команд, внутрішню схему, Dolt як обов'язковий backend або специфічні implementation details. Він будує власну модель навколо головної відмінності: будь-який робочий об'єкт є також семантично описаним об'єктом із доказами, часовою валідністю, контекстом та зв'язками з кодом.

## 0.3 Роль Workflow Kit

Workflow Kit — не конкурент семантичному ядру Cairn. Це цінний референс **локального orchestration/control plane**: provider session management, керування повідомленнями, планами, cross-provider review, user decision capture, autopilot authorization, pause/stop, delivery acceptance, Git hygiene, platform-aware runtime та інтерактивна панель.

У цільовій системі:

- Cairn Core — канонічна модель стану, графи, події, семантика, контекст і політики.
- Workflow Runtime — виконання формалізованих процесів та їх відновлення.
- Agent Host Adapters — підключення до нативних сесій агентів.
- Workbench — інтерфейс людини для спостереження, керування та прийняття рішень.
- Provider-specific UI залишається джерелом нативних можливостей; Cairn не намагається відтворити весь Claude/Codex UI.

## 0.4 Рекомендована архітектура

- Greenfield modular monolith із чіткими bounded contexts; окремі worker processes для виконання коду.
- Python 3.12+ для Core/CLI/daemon; TypeScript для Web UI; Node/Rust helpers лише там, де є обґрунтована потреба інтеграції з ОС/PTY.
- SQLite WAL + FTS5 як перший локальний operational store; Git як переносимий журнал і довготривала історія; JSONL як interchange/event envelope; optional embeddings як rebuildable index.
- Не вимагати Dolt, PostgreSQL, Redis, Kafka, Neo4j чи окрему vector DB для single-user/self-hosted MVP.
- Federation — через адаптер синхронізації з явною моделлю replica, authority, conflict resolution і data sovereignty; не робити припущення, що Git merge автоматично вирішує семантичні конфлікти.
- MCP для agent tool access, CLI для низького overhead, REST/WebSocket для Workbench.
- Жодного прихованого cloud fallback. Хмарні провайдери opt-in; core має працювати без мережі.

---

# 1. Дослідження джерел і методологія

## 1.1 Об'єкти аналізу

1. [CommerceCentr/cairn](https://github.com/CommerceCentr/cairn) — цільовий Cairn, що надає компактний історичний контекст між сесіями та адресує історію до файлів/символів.
2. [Beads](https://github.com/gastownhall/beads) і надані користувачем сторінки документації:
   - [Core concepts](https://beads.gascity.com/core-concepts/index)
   - [Issues & dependencies](https://beads.gascity.com/core-concepts/issues)
   - [Configuration](https://beads.gascity.com/reference/configuration)
   - [Agent coordination](https://beads.gascity.com/multi-agent/coordination)
   - [Federation](https://beads.gascity.com/multi-agent/federation#planned-features)
   - додатково: dependencies/gates, graph links, metadata, formulas, molecules, wisps.
3. [OleynikAleksandr/Workflow_Kit](https://github.com/OleynikAleksandr/Workflow_Kit) — локальний менеджер проєктів і агентських процесів, який взаємодіє з Claude, ChatGPT/Codex та Kimi Code.

## 1.2 Правила інтерпретації

- **Підтверджений факт** — прямо описаний у README, офіційній документації або структурі репозиторію.
- **Заява автора/результат вимірювання** — цифра чи висновок, наведені самим проєктом; це не незалежна валідація.
- **Архітектурна пропозиція Cairn** — нова вимога або механізм, який потрібно реалізувати та перевірити.
- **Planned / not exposed** — механізм, який має інфраструктурну підтримку, але ще не доступний як стабільна команда/API. Його не можна видавати за готову функцію.

Публічна документація Beads має широкий перелік можливостей і деталізує навіть operational edge cases. Для federation, наприклад, прямо описано replica-local leases, затримку видимості claims між репліками, TTL відносно sync interval та операцію `push/pull` як planned/unexposed. Ці нюанси впливають на архітектуру Cairn і не повинні бути спрощені до фрази «є peer-to-peer sync».

Workflow Kit README на дату дослідження вказує версію 0.20.2, тоді як окремий `docs/architecture/OVERVIEW.md` містить історичний стан M1–M35/0.14.3. Тому для поточних можливостей пріоритет віддано README та новішим release notes; архітектурний документ використано для розуміння модульних меж і реалізаційних принципів, а не як доказ того, що всі його згадані milestone-функції залишилися незмінними.

---

# 2. Функціональна декомпозиція Beads

Цей розділ — повна карта класів можливостей, які Cairn має врахувати. Це не перелік функцій для копіювання один-в-один.

## 2.1 Work item / Issue model

Beads моделює роботу через стійкі issue/bead об'єкти. Базова сутність має ID, title, description, type, status, priority, labels, timestamps та зв'язки. Документація описує стандартні типи `bug`, `feature`, `task`, `epic`, `chore` та можливість кастомних типів. Стани, пріоритети, типи та labels формують просту, зрозумілу операційну мову.

Cairn повинен підтримувати еквівалентний функціональний простір, але розділити:

- `WorkItem` — одиниця наміру/роботи;
- `WorkState` — поточний стан workflow;
- `WorkClassification` — тип/категорія;
- `PriorityPolicy` — порядок важливості;
- `AcceptanceContract` — перевірювані критерії завершення;
- `WorkMetadata` — extension data, що не повинна безконтрольно перетворюватися на довільний сховок істини.

Ключова вимога: завершення WorkItem не означає автоматично, що код змерджено, CI успішний або реліз опубліковано. Це окремі факти, які мають бути підтверджені Evidence та/або Gate.

## 2.2 Dependency graph і типи зв'язків

Beads відрізняє блокувальні залежності від інформаційних зв'язків.

### Блокувальні / workflow edges

- `blocks` — залежна робота не може стартувати до завершення blocker.
- `parent-child` — ієрархія; батьківський стан може впливати на дочірні задачі.
- `conditional-blocks` — альтернативний шлях, що активується при певному результаті/помилці.
- `waits-for` — fan-in: очікування набору динамічно створених дочірніх робіт.

### Неблокувальні / knowledge edges

- `related` / `relates-to` — семантична спорідненість.
- `discovered-from` — походження задачі/знахідки.
- `caused-by` — причинний зв'язок.
- `validates` — доказ/тест перевіряє об'єкт.
- `tracks` — відстежує прогрес іншого об'єкта.
- `replies-to` — ланцюг повідомлень.
- `duplicates` — дублікат канонічного об'єкта.
- `supersedes` — заміна попереднього об'єкта новим.

Cairn має зробити тип ребра формальним контрактом. Ребро містить `relation_type`, `directionality`, `execution_effect`, `validity`, `confidence`, `provenance`, `source_scope` і `policy`. `execution_effect` не можна виводити лише з текстового label.

## 2.3 Ready frontier / scheduler semantics

`bd ready` — не просто фільтр задач зі статусом open. Це обчислення frontier: відкрита робота без активних блокерів, без конфліктного claim, без deferred state та без незакритого gate.

Cairn має реалізувати окремий `WorkEligibility Engine`, який обчислює:

- чи дозволено почати роботу;
- чи виконані hard dependencies;
- чи відкриті conditional paths;
- чи є активні gates;
- чи є claim/lease іншого виконавця;
- чи відповідає агент потрібним capabilities;
- чи не порушує запуск бюджет, security policy або resource quota;
- чи сумісна задача з поточним semantic/code revision.

Результат — не булеве поле, а explainable decision: `eligible`, `blocked`, `deferred`, `requires_human`, `conflicted`, `not_capable`, `budget_denied`, із причинами та посиланнями на блокуючі об'єкти.

## 2.4 Stable identifiers

Beads застосовує hash-based IDs з адаптивною довжиною, щоб паралельне створення сутностей у різних гілках/агентах не призводило до колізій і перейменувань після merge.

Cairn повинен використовувати глобально унікальні, незалежні від послідовності IDs:

- UUIDv7 або ULID для event/object identity;
- content hash для immutable artifact identity;
- stable semantic identity для code symbols, що переживає rename/refactor через lineage mapping;
- human-readable slug — тільки display alias, не primary key.

Ідентифікатор не повинен залежати від title, оскільки назви змінюються. Collision resistance перевіряється на federation/merge tests.

## 2.5 Workflow templates: Formula → Template → Instance

Beads описує declarative workflow formulas у TOML/JSON із version, vars, steps, conditions, dependencies, gates та аспектами. Formula компілюється у reusable proto/template, а потім інстанціюється як реальний workflow graph (molecule). Для типових задач можна створити звичайний epic без формули; формули потрібні для повторюваних процесів.

Cairn повинен мати:

- `WorkflowDefinition` — версійований декларативний процес;
- `WorkflowTemplate` — валідований/компільований шаблон;
- `WorkflowRun` — конкретний запуск із зафіксованою версією шаблону та input snapshot;
- `WorkflowStep` — крок із контрактом, залежностями та executor policy;
- `WorkflowBranch` — умовна гілка;
- `WorkflowJoin` — fan-in / aggregate;
- `WorkflowGate` — асинхронне очікування зовнішньої умови;
- `WorkflowArtifact` — вихідний артефакт кожного кроку.

Workflow Definition має бути deterministic, versioned і валідованим до запуску. Зміна шаблону не повинна непомітно змінювати вже запущені workflow.

## 2.6 Molecules / structured multi-step work

Molecule у Beads — фактичний екземпляр workflow graph, зазвичай epic із дочірніми кроками та залежностями. Незалежні дочірні задачі можуть виконуватися паралельно; послідовність виникає лише через явні dependencies.

Cairn має підтримати такі семантики без копіювання термінології:

- DAG із паралельними кроками;
- dynamic fan-out;
- fan-in/waits-for;
- retries із backoff та idempotency keys;
- compensation steps / saga для частково виконаних процесів;
- resume після crash/session loss;
- explicit step ownership;
- state snapshots/checkpoints;
- workflow evolution через новий run, а не переписування старого.

## 2.7 Gates / external wait conditions

Beads представляє gate як work object, що блокує інші кроки до виконання зовнішньої умови. Серед прикладів: human approval, timer, GitHub PR merged, CI run successful, зовнішній bead/issue. Це важливо, оскільки закриття задачі у tracker не означає, що зовнішній світ уже змінився.

Cairn має підтримувати `Gate` як first-class entity з типом condition, source adapter, polling/webhook policy, timeout, escalation, manual resolution, evidence requirement та idempotent resolver.

Типи MVP:

- `human.approval`;
- `timer.elapsed`;
- `git.commit.present`;
- `git.branch.merged`;
- `ci.run.success`;
- `review.approved`;
- `external.work_item.closed`;
- `artifact.available`.

Резолвер gate повинен повертати evidence record. Не можна закривати gate лише на підставі повідомлення агента «все готово».

## 2.8 Ephemeral workflows

Beads має ephemeral molecules/wisps для операційних робіт, що не мають довготривалої audit value, із можливістю purge або promotion.

Cairn повинен мати lifecycle class:

- `durable` — зберігається в історії;
- `ephemeral` — короткоживучий run/diagnostic/task;
- `promotable` — може бути перетворений на durable object;
- `retention_locked` — збереження обов'язкове через compliance/evidence.

Purge не може видаляти події, на які посилаються durable objects, або записи з legal/security retention. Ephemeral content не синхронізується за замовчуванням; promotion створює durable summary із посиланням на оригінальний run та його provenance.

## 2.9 Agent coordination

Beads документує assignment, atomic claim, ready-and-claim, handoff, parallel work, fan-out/fan-in, merge slot та комунікацію через comments/labels. При цьому документація прямо зазначає, що agent registry як окрема сутність відсутній, а assignee — звичайний рядок.

Cairn має формалізувати agent registry і координацію:

- `AgentIdentity` — стабільна ідентичність агента/виконавця;
- `AgentRuntime` — конкретний процес/інстанс;
- `AgentCapability` — мови, інструменти, доступ, моделі, OS, контекстні ліміти;
- `Session` — розмова/контекст конкретного провайдера;
- `Assignment` — призначення роботи;
- `Claim` — атомарне отримання задачі;
- `Lease` — обмежене в часі право виконувати ресурс/об'єкт;
- `Heartbeat` — сигнал активності;
- `Handoff` — контрольована передача роботи та контексту;
- `ResourceLock` — exclusive/shared lock;
- `MergeSlot` — серіалізація конфліктної інтеграції;
- `AgentMessage` — повідомлення з thread/provenance.

Claim, lease і resource lock — різні поняття. Claim означає ownership задачі, lease — часову оренду, lock — доступ до конкретного ресурсу. Fencing token обов'язковий для захисту від stale worker після expiry/reclaim.

## 2.10 Multi-repository routing

Beads підтримує зовнішні залежності та routing між репозиторіями/rigs. Cross-repo capability може блокувати локальну задачу до появи/закриття відповідного зовнішнього work item.

Cairn має мати:

- `Workspace` — локальна папка;
- `Repository` — Git repository identity;
- `Project` — логічний продукт/система, що може охоплювати кілька repos;
- `RepositoryLink` — зв'язок між repos;
- `CapabilityContract` — контракт, який один repo надає іншому;
- `ExternalReference` — ID у зовнішній системі;
- `RemoteDependency` — залежність на capability/artifact із іншого repo;
- `RoutePolicy` — правила маршрутизації запитів та агентів.

Залежність на зовнішній ресурс за недоступності peer має бути `unverified/blocked`, а не мовчки вважатися виконаною.

## 2.11 Federation і replica semantics

Beads federation використовує Dolt remotes та підтримує peer-to-peer topology, hub-spoke і hierarchical топології, а також sovereignty tiers. Кожна workspace має власну базу. Синхронізація відбувається database-native commits; конфлікти без явної стратегії зупиняються для ручного вирішення. Документація описує важливий edge case: leases є replica-local, тоді як status/assignee можуть бути видимі на інших репліках із затримкою до sync interval. Reclaim має виконуватися на репліці, що видала lease; TTL/grace мають перевищувати sync interval. Окремі single-direction `federation push/pull` в документації позначені як planned/unexposed, а не готові команди.

Cairn має реалізувати федерацію в кілька етапів:

1. **Portable export/import** — переносимі event/object envelopes.
2. **Single authority remote** — один writer/authority для shared state, read replicas локальні.
3. **Peer federation** — кілька автономних вузлів із explicit ownership та reconciliation.
4. **Selective federation** — синхронізуються тільки дозволені scopes/object classes.

Ключові вимоги:

- Replica identity належить store/replica, а не обов'язково host machine.
- Local leases не реплікуються як глобальна істина.
- Distributed claims потребують consensus/authority або conservative conflict policy.
- Sync interval, lease TTL, heartbeat, grace window та clock skew мають бути узгоджені.
- Усі remote operations мають endpoint trust/pinning, credential isolation та data sovereignty policy.
- Conflict resolution має бути типізованим: field-level merge для безпечних атрибутів; CRDT/append-only для окремих колекцій; human resolution для semantic conflict.
- Ніякого автоматичного `ours/theirs` для зміни policy, security, decisions чи contracts.

## 2.12 Configuration system і provenance конфігурації

Beads розділяє tool-level YAML settings (поведінка CLI/runtime) та project-level settings, що зберігаються в Dolt і синхронізуються між командами. Конфігурація має precedence: flags → environment → config files → defaults; project settings мають власну область. Secrets не повинні потрапляти в shared database або committed config. Є endpoint trust/pinning, source-of-value display, validation та custom namespace.

Cairn має побудувати аналогічно строгий `Configuration Registry`:

- `user.local` — приватні налаштування користувача;
- `machine.local` — локальна машина/runtime;
- `workspace.shared` — спільні налаштування workspace;
- `project.shared` — правила проєкту;
- `organization.shared` — політики організації;
- `runtime.override` — одноразові параметри запуску.

Кожне effective value має provenance: `value`, `source`, `scope`, `precedence`, `secret_class`, `last_changed`, `changed_by`, `validation_state`.

Secrets зберігаються лише в OS keychain/secret store або encrypted local vault; ніколи не в event payload, Git, shared object store чи prompt logs. Конфігурація endpoint має бути pinned/trusted перед передачею credential. UI повинен показувати джерело кожного активного значення та конфлікти override.

## 2.13 Labels, metadata та extension points

Beads використовує labels для класифікації та arbitrary JSON metadata як extension point для integration/orchestration hints. Execution hints включають тип агента, suggested model, reasoning effort, mode та parallel group; документація підкреслює advisory nature цих полів.

Cairn має не перетворювати metadata на неконтрольований «JSON blob». Замість цього:

- типізовані extension namespaces (`ext.<vendor>.<domain>`);
- schema version + JSON Schema;
- declared owner/provider;
- allowed consumers;
- trust classification;
- retention policy;
- validation before execution;
- portable capability hints, що мапляться на локальні runtime capabilities.

Labels залишаються простими facet tags для пошуку/фільтрації. Семантичні факти й рішення зберігаються як окремі типізовані об'єкти з provenance.

## 2.14 Audit, comments, threads та history

Beads має issue history і опційний append-only interaction audit для model/tool calls. Coordination використовує comments та thread links для handoff/communication.

Cairn повинен мати два різні журнали:

- **Operational Event Ledger** — усі системні зміни стану, actor, timestamp, causation/correlation IDs, policy decisions.
- **Interaction Archive** — session/tool/model events, які можуть бути redacted і зберігатися за окремою retention policy.

Сирі транскрипти не повинні автоматично ставати довготривалою пам'яттю. Capture pipeline має зберігати reference/hash, sanitized excerpts, структуровані claims, decisions, alternatives та evidence. Повний raw transcript може залишатися в локальному джерелі користувача або в окремому encrypted archive.

---

# 3. Аналіз Workflow Kit і співіснування

## 3.1 Що робить Workflow Kit

За актуальним README Workflow Kit — локальне Node.js application для управління проєктами й агентською роботою у Claude, ChatGPT/Codex та Kimi Code. Служба запускає HTTP API і веб-панель, керує проектами/сесіями, передає інструкції через hooks, показує діалоги сесій, дозволяє писати агенту з панелі, керує планами, cross-provider review, послідовним або delegated execution, autopilot та публікацією після приймання.

Підтверджені сильні сторони:

1. **Provider session control.** Сесії відкриваються в нативних програмах провайдерів; Kit керує їх lifecycle і відображає діалог.
2. **Human-in-the-loop.** Рішення користувача (вибір режиму, підтвердження доставки, приймання, закриття, зняття блокування, встановлення інструментів) фіксує Kit, а не агент.
3. **Cross-provider plan review.** Один провайдер перевіряє план іншого, а служба пересилає заперечення та відповіді.
4. **Autopilot with explicit authority.** Служба видає/відкликає право на запуск; Pause/Stop обробляються окремо від звичайної черги повідомлень.
5. **Evidence-based acceptance.** Автоприймання не повинно проходити, якщо немає комітованих доказів або перевірки; система може чекати на людське рішення замість фальшивого успіху.
6. **Git hygiene.** Scope обмежує дозволені файли; commit/close/publish контролюються; dirty tree блокує закриття; зовнішні коміти потребують adoption.
7. **Recovery packet.** Сесія отримує інструкцію відновлення та наступний крок; при втраті контексту викликає recovery command.
8. **Project-scoped workflow.** План належить проєкту, а не тільки сесії; нова сесія іншого провайдера може продовжити незапущений план.
9. **Provider-specific controls.** Модель, effort, mode, permissions, context/usage indicators, message queue та connection recovery враховують відмінності провайдерів.
10. **Local UX.** Одна локальна панель керує кількома проектами та сесіями, підтримує віддалений доступ через Tailscale.

## 3.2 Архітектурні ідеї Workflow Kit, які варто врахувати

- Modular monolith, модулі з явними контрактами та facade exports.
- Module contract змінюється в одному commit із реалізацією.
- Event journal, atomic writes, mutexes, operation log, platform-specific paths.
- Provider registry + provider adapters + process supervisor + PTY host.
- Hook ingestion із validation і attempt logs.
- API access filter і локальний web server.
- Project registry та керовані project hooks/instructions.
- Build provenance для bundled runtime.
- Platform guard і CI на Windows/macOS/Linux.
- Recovery/repair для незавершених транзакцій.

## 3.3 Що Workflow Kit не повинен замінювати в Cairn

Workflow Kit не є повноцінною міжсесійною семантичною базою знань. Він керує процесом, сесіями й рішеннями, але не має бути єдиним джерелом причинної історії конкретного методу, семантичного графа коду, temporal facts та багатопроєктного knowledge graph.

Так само Cairn не повинен намагатися реалізувати повний нативний terminal/chat/provider UI. Він має делегувати provider-specific controls адаптерам і відображати узгоджений operational state.

## 3.4 Розподіл відповідальності

| Підсистема | Відповідальність | Канонічні дані |
|---|---|---|
| Cairn Core | Object model, events, semantics, provenance, policy, graph queries | Canonical project state |
| Work Graph | Tasks, dependencies, ready frontier, assignments, gates | Work state |
| Workflow Runtime | Plan compilation, workflow execution, checkpoints, retries | Run/step state |
| Agent Host | Provider sessions, message delivery, hook lifecycle, PTY/process | Runtime/session state |
| Context Engine | Retrieval, ranking, context packing, token budget | Derived context package |
| Semantic Indexer | AST, symbols, contracts, relations, code lineage | Rebuildable semantic projection |
| Workbench | UI, approvals, monitoring, operator controls | Read model + command gateway |
| Git Adapter | Commit/diff/worktree/branch/merge/publish evidence | Git remains source for code |
| Federation | Replication, sync, trust, conflict reconciliation | Replicated event/object subsets |

## 3.5 Рекомендований режим співіснування

**Режим A — Cairn standalone:** Cairn має власний Work Graph і Workflow Runtime; Workflow Kit concepts реалізуються поступово. Це цільовий продукт.

**Режим B — Cairn + Workflow Kit adapter:** якщо користувач уже використовує Workflow Kit, Cairn підключається як semantic/context backend, а Kit залишається operator UI/provider host. Синхронізація через versioned API/events, без спільного запису в чужі внутрішні таблиці.

**Режим C — Cairn headless:** Core/CLI/MCP без Workbench, для користувачів, які мають власний orchestration UI.

Не рекомендується форкати Workflow Kit і зливати його код у Cairn на старті. Це створить coupling до конкретних provider control flows і значно збільшить surface area. Краще визначити `Agent Host Contract` і `Workflow Control API` та реалізувати adapter.

---

# 4. Семантичне поле об'єктів — центральна модель Cairn 2.0

## 4.1 Проблема звичайного metadata

Поле `metadata: JSON` саме по собі не створює семантики. Воно дозволяє зберігати будь-які дані, але не відповідає на питання:

- що саме стверджує запис;
- хто має право це стверджувати;
- на якому джерелі це базується;
- чи це факт, гіпотеза, рішення, інструкція або спостереження;
- коли твердження було істинним і коли його вважали актуальним;
- до якої версії коду/проєкту воно належить;
- чи може воно впливати на виконання;
- що його спростовує або замінює;
- кому дозволено його бачити/використовувати.

Тому Cairn вводить **Semantic Envelope** для кожного об'єкта та **Semantic Assertion** для кожного окремого твердження.

## 4.2 Semantic Envelope

Кожен domain object (WorkItem, Decision, Symbol, Contract, Workflow, Agent, Artifact, Gate тощо) має стандартний envelope:

```json
{
  "id": "01J...",
  "object_type": "decision",
  "schema_version": "2.0",
  "namespace": "project:commerce-cms",
  "identity": {
    "stable_key": "decision:auth/session-rotation",
    "aliases": ["ADR-014", "auth-token-rotation"]
  },
  "semantic": {
    "kind": "architectural_decision",
    "label": "Use rotating refresh tokens",
    "summary": "Refresh tokens are rotated on every use",
    "concepts": ["authentication", "session-security"],
    "intent": "reduce-token-replay-risk",
    "scope": ["src/Auth/**", "api/session/**"],
    "constraints": ["single-use-refresh-token", "reuse-detection"]
  },
  "lifecycle": {
    "state": "accepted",
    "valid_from": "2026-11-01T00:00:00Z",
    "valid_until": null,
    "supersedes": [],
    "superseded_by": []
  },
  "provenance": {
    "created_by": "human:user-123",
    "source_events": ["evt-..."],
    "session_id": "ses-...",
    "task_id": "wrk-...",
    "commit_refs": ["git:repo@sha"],
    "evidence_refs": ["evd-..."]
  },
  "trust": {
    "class": "verified_human_decision",
    "confidence": 1.0,
    "review_state": "approved",
    "policy_tags": ["project-authoritative"]
  },
  "temporal": {
    "observed_at": "2026-11-01T10:00:00Z",
    "recorded_at": "2026-11-01T10:00:01Z",
    "valid_time_basis": "human-confirmed"
  },
  "access": {
    "classification": "internal",
    "read_scopes": ["project-members"],
    "write_policy": "decision-authority"
  },
  "extensions": {}
}
```

Це illustrative contract, а не остаточна wire schema. Реальна реалізація має використовувати JSON Schema / Pydantic models, canonical serialization і schema migrations.

## 4.3 Поля семантичного шару

| Поле | Значення | Обов'язковість |
|---|---|---|
| `object_type` | Тип доменного об'єкта | Обов'язкове |
| `stable_key` | Семантична адреса, що переживає rename | Для довгоживучих об'єктів |
| `kind` | Семантичний клас: fact, decision, rule, intent, observation тощо | Обов'язкове |
| `label` | Людинозрозуміла назва | Рекомендоване |
| `summary` | Стисле пояснення значення | Рекомендоване |
| `concepts` | Контрольована таксономія понять | Опційне |
| `intent` | Який намір/проблему представляє об'єкт | Для задач/рішень/контрактів |
| `scope` | Де об'єкт діє: project/repo/path/symbol/environment | Обов'язкове для scoped facts |
| `constraints` | Умови/інваріанти/обмеження | Для policy/contract/decision |
| `valid_from`, `valid_until` | Часова валідність у предметній області | Для temporal facts |
| `provenance` | Автор, джерело, події, сесії, commits | Для всіх assertions |
| `trust` | Клас довіри, confidence, review state | Обов'язкове |
| `access` | Класифікація та політика доступу | Обов'язкове |
| `extensions` | Типізовані сторонні поля | Опційне, schema-bound |

## 4.4 Semantic Assertion — атомарне твердження

Об'єкт може мати багато тверджень, і вони можуть мати різних авторів, різні джерела та різний рівень довіри. Не можна приписувати один confidence всьому об'єкту.

`SemanticAssertion`:

- `subject_id`;
- `predicate`;
- `object_ref` або typed literal;
- `polarity` (asserted/negated);
- `claim_type` (observed, inferred, decided, proposed, verified, rejected);
- `source_event_id`;
- `author_identity`;
- `observed_at`;
- `valid_time`;
- `confidence` (тільки для probabilistic inference, не для людського approval);
- `evidence_refs[]`;
- `review_state`;
- `supersedes_assertion_id`;
- `trust_class`;
- `scope`;
- `access_policy`.

Приклад: «Метод `AuthService::refresh` використовує одноразові refresh tokens» може бути підтверджений AST/code evidence на commit X, але рішення «це обов'язковий security invariant» має окремо походити з accepted Decision/Contract.

## 4.5 Semantic object types

Мінімальна ontology:

### Project & structure
- `Organization`, `Project`, `Workspace`, `Repository`, `Branch`, `Commit`, `File`, `Directory`, `Module`, `Package`, `Service`, `Environment`.

### Code semantics
- `Symbol`, `CallSite`, `Type`, `Interface`, `APIEndpoint`, `DatabaseEntity`, `Schema`, `ConfigKey`, `Dependency`, `CodeRegion`.

### Knowledge & architecture
- `Concept`, `Fact`, `Observation`, `Hypothesis`, `Decision`, `Alternative`, `Invariant`, `Constraint`, `Contract`, `Pattern`, `AntiPattern`, `Risk`, `Assumption`, `Requirement`, `ArchitectureNode`.

### Work & workflow
- `Goal`, `WorkItem`, `Epic`, `Assignment`, `Claim`, `Gate`, `WorkflowDefinition`, `WorkflowRun`, `WorkflowStep`, `Artifact`, `Review`, `Approval`.

### Execution & provenance
- `Agent`, `AgentRuntime`, `Session`, `ToolCall`, `ModelInvocation`, `Checkpoint`, `Lease`, `Lock`, `Event`, `Evidence`, `TestResult`, `BuildResult`, `Deployment`, `Incident`.

## 4.6 Typed semantic relations

Relations мають бути контрольованими й розділеними за функцією:

| Relation | Meaning | Виконання блокує? |
|---|---|---|
| `depends_on` | Work dependency | Так, якщо policy каже hard |
| `blocks` | Пряма блокувальна залежність | Так |
| `parent_of` | Ієрархія | Лише за явно заданою policy |
| `implements` | Код реалізує контракт/вимогу | Ні |
| `satisfies` | Об'єкт задовольняє умову | Ні; потрібне evidence |
| `validates` | Тест/перевірка підтверджує об'єкт | Ні; впливає на gate evaluation |
| `calls` | Code symbol викликає інший symbol | Ні |
| `depends_at_runtime_on` | Runtime dependency | Ні, але impact analysis |
| `caused_by` | Причинний зв'язок | Ні, потребує доказу |
| `discovered_during` | Походження знахідки | Ні |
| `derived_from` | Виведений з джерела | Ні |
| `supports` | Evidence підтримує claim | Ні |
| `contradicts` | Evidence/claim суперечить іншому | Ні, але trust review |
| `related_to` | Слабка спорідненість | Ні |
| `duplicates` | Дублікат canonical object | Ні |
| `supersedes` | Заміщує попередній об'єкт | Ні |
| `replies_to` | Thread relation | Ні |
| `owns` | Відповідальність/ownership | Ні |
| `grants` | Надання повноважень | Ні; security policy |

У relation edge є `validity`, `provenance`, `trust`, `scope`, `source_revision`, `execution_effect`, `created_by`, `created_at`, `superseded_by`.

## 4.7 Temporal semantics: valid time + transaction time

Cairn використовує bi-temporal model:

- **Valid time** — коли твердження було істинним у предметній області.
- **Transaction time** — коли Cairn записав/дізнався це твердження.

Це дозволяє відповісти на різні питання:

- «Що ми знали на 1 жовтня?»
- «Яке рішення реально діяло в production 1 жовтня?»
- «Коли система дізналася, що рішення було скасовано?»

Скасування не видаляє старий об'єкт. Воно створює нову подію/версію, яка закриває validity interval і вказує на successor/replacement. Query API має підтримувати `as_of` і `known_at`.

## 4.8 Trust & authority model

Trust не дорівнює cosine similarity і не повинен бути одним числом.

Рекомендовані класи:

1. `human_authoritative` — рішення/approval, підтверджене користувачем із дозволом.
2. `verified_system` — факт, підтверджений deterministic system event (Git SHA, CI result, test output hash).
3. `source_observed` — дані, прочитані з конкретного source system.
4. `agent_proposed` — пропозиція агента, ще не затверджена.
5. `agent_inferred` — висновок агента з evidence, але без незалежної перевірки.
6. `imported_untrusted` — імпортований зовнішній контент.
7. `rejected_or_revoked` — відхилене/відкликане твердження.

Trust policy визначає, які класи дозволено використовувати для:
- retrieval;
- рекомендації;
- генерації plan;
- зміни коду;
- обходу gate;
- зміни security/configuration;
- автоматичного виконання.

Агентська пропозиція не може стати authoritative лише через повторення або потрапляння в summary. Promotion вимагає evidence + policy-defined reviewer/approval.

## 4.9 Semantic freshness / decay

Не можна видаляти важливу пам'ять лише через вік. Freshness залежить від класу об'єкта:

- code fact — invalidation при зміні/rename символу або commit lineage;
- architectural decision — актуальний до superseded/revoked;
- runtime observation — TTL або нове спостереження;
- user preference — explicit revision/expiry;
- security policy — лише explicit policy change;
- inferred pattern — періодична перевірка та decay ranking, але не безшумне видалення.

Freshness engine позначає `current`, `stale`, `superseded`, `invalidated`, `unverified`, `historical`. Старі дані лишаються доступними в історичному query.

## 4.10 Семантична адресація коду

Cairn зберігає сильну ідею code-addressable memory:

- `repo://<repo-id>/file/<relative-path>`;
- `symbol://<repo-id>/<language>/<qualified-name>`;
- `api://<service>/<method>/<path>`;
- `schema://<repo-id>/<db>/<table>/<column>`;
- `contract://<project>/<contract-key>`.

Symbol identity має містити language, qualified name, structural fingerprint, enclosing scope та lineage edges (`renamed_to`, `moved_to`, `split_into`, `merged_into`). Line numbers — лише координата поточного source snapshot, не identity.

---

# 5. Цільова архітектура Cairn

## 5.1 Bounded contexts

1. Identity & Access
2. Project Registry
3. Work Graph
4. Workflow Runtime
5. Agent Registry & Host Adapters
6. Semantic Indexer
7. Evidence & Provenance
8. Context Compiler
9. Coordination & Resource Manager
10. Policy & Trust Engine
11. Federation & Sync
12. Observability & Cost Governance
13. Workbench API/UI

Модулі спілкуються через versioned contracts/commands/events. Не допускається прямий імпорт внутрішніх таблиць або runtime objects іншого модуля.

## 5.2 High-level architecture

```text
                ┌────────────────────────────────────┐
                │            Workbench UI             │
                │ Projects · Graph · Sessions · Gates │
                └──────────────────┬─────────────────┘
                                   │ HTTP / WS
┌─────────────────────┐   ┌───────▼────────────────────────────┐
│ Claude / Codex /     │   │              Cairn Core             │
│ Cursor / Gemini      │◄─►│ Commands · Policies · Event Ledger │
│ via MCP / CLI / Host │   │ Identity · Project · Object Model  │
└─────────────────────┘   └───────┬────────────────────────────┘
                                  │
       ┌──────────────────────────┼─────────────────────────┐
       │                          │                         │
┌──────▼──────┐          ┌────────▼────────┐       ┌────────▼────────┐
│ Work Graph  │          │ Semantic Engine │       │ Workflow Engine │
│ Ready/Deps  │          │ Code + Knowledge│       │ Runs/Gates      │
└──────┬──────┘          └────────┬────────┘       └────────┬────────┘
       │                          │                         │
       └──────────────────────────┼─────────────────────────┘
                                  │
                        ┌─────────▼──────────┐
                        │ Context Compiler   │
                        │ Evidence + Budget  │
                        └─────────┬──────────┘
                                  │
                  ┌───────────────┼─────────────────┐
                  │               │                 │
             ┌────▼────┐    ┌─────▼─────┐     ┌─────▼──────┐
             │ SQLite  │    │ Git Store │     │ Indexes    │
             │ WAL/FTS │    │ Events/MD │     │ AST/FTS/Vec│
             └─────────┘    └───────────┘     └────────────┘
```

## 5.3 Source of truth policy

Не існує однієї бази, яка має бути source of truth для всього:

- Git — істина щодо версійованого коду, commits, branches та diffs.
- Cairn Event Ledger — істина щодо подій і зміни доменного стану.
- Semantic Assertions — істина щодо того, що система вважає відомим/чинним, з provenance.
- Workflow Run — істина щодо стану конкретного виконання.
- Provider host — істина щодо нативного стану provider session, якщо adapter може його підтвердити.
- SQLite — operational projection/cache, відновлювана з event/object store.

Жоден summary не замінює оригінальне evidence. Жоден LLM output не є системним фактом без ingestion policy.

## 5.4 Storage architecture

MVP:

- SQLite WAL: transactional operational state, query projections, FTS5.
- Append-only event log: canonical event envelopes у `.cairn/events/` або embedded event table з export snapshots.
- Git: versioned project declarations, contracts, decisions, schemas, stable artifacts.
- Derived indexes: AST/symbol graph, BM25/FTS, optional embeddings; можна видалити й rebuild.
- Session archive: окреме сховище з redaction/encryption/retention.

Не рекомендується відразу зберігати весь operational event stream у Git commit-per-event. Це призведе до history bloat і конфліктів. Git зберігає durable, reviewable state та export snapshots; SQLite/event segments зберігають високочастотні runtime events. Snapshotting/compaction мають зберігати hash-chain/provenance та replay ability.

## 5.5 Directory layout

```text
.cairn/
  manifest.json
  config/
    project.yaml
    schemas/
    policies/
  objects/
    work/
    decisions/
    contracts/
    workflows/
    concepts/
  events/
    2026/11/*.jsonl
  evidence/
    commits/
    tests/
    reviews/
  exports/
  cache/                 # gitignored, rebuildable
  runtime/               # gitignored
    cairn.sqlite
    locks/
    sessions/
    workers/
    checkpoints/
  secrets/               # never committed; preferably OS keychain
```

Рекомендований `.gitignore`: `.cairn/cache/`, `.cairn/runtime/`, `.cairn/secrets/`, raw transcript archives, temporary worker outputs, local machine identifiers.

---

# 6. Work Graph і issue/work item model Cairn

## 6.1 WorkItem schema

```json
{
  "id": "wrk_01J...",
  "type": "implementation",
  "title": "Implement refresh token rotation",
  "description": "...",
  "status": "ready",
  "priority": 1,
  "parent_id": "wrk_epic_...",
  "labels": ["security", "backend"],
  "assignee": null,
  "capability_requirements": ["php", "security-review"],
  "execution_hints": {
    "agent_role": "worker",
    "model_tier": "high-reasoning",
    "parallel_group": "auth-backend"
  },
  "acceptance_contract_id": "ctr_...",
  "scope": {
    "repositories": ["repo_..."],
    "files": ["src/Auth/**"],
    "symbols": ["symbol://.../AuthService::refresh"]
  },
  "created_at": "...",
  "updated_at": "...",
  "semantic_ref": "sem_..."
}
```

## 6.2 Status model

Не прив'язуватися до одного фіксованого enum для всіх workflow. Core має canonical lifecycle:

`draft → open → ready → claimed → in_progress → review → awaiting_gate → completed`

Додаткові стани: `blocked`, `deferred`, `failed`, `cancelled`, `superseded`, `rejected`, `archived`.

Проєкт може мати custom status map, але кожен custom status повинен мапитися на canonical semantics: чи claimable, чи terminal, чи blocks descendants, чи counts as success.

## 6.3 Work relations

- Hard dependency graph — ациклічний, cycle check при write.
- Parent-child hierarchy — не тотожна hard dependency.
- Discovery/provenance graph — відстежує, звідки виникла задача.
- Semantic relation graph — зв'язує роботу з кодом/рішеннями/контрактами.
- External capability dependency — може бути resolved тільки через зовнішній evidence/gate.

## 6.4 Ready frontier algorithm

`eligible(work_item, agent, now)` повертає structured decision:

1. WorkItem у claimable lifecycle state?
2. Усі hard blockers завершені?
3. Parent scope дозволяє виконання?
4. Conditional dependency path активний?
5. `waits_for` aggregator завершився?
6. Немає відкритого gate?
7. Немає іншого valid claim/lease?
8. Agent capabilities відповідають requirements?
9. Policy допускає tool/model/data scope?
10. Budget доступний?
11. Code revision/semantic snapshot відповідає очікуваному base revision?
12. Немає unresolved high-risk semantic conflict?

Повертається пояснюваний eligibility record із причинами та dependency path.

## 6.5 Assignment / claim / lease

- Assignment — кому призначено роботу.
- Claim — хто фактично взяв її у виконання.
- Lease — право володіння на обмежений час.
- Heartbeat — підтвердження живого holder.
- Fencing token — монотонний номер, що відхиляє writes від старого holder після reclaim.

Atomic claim має виконуватися транзакційно. При expiry задача не повинна автоматично вважатися невиконаною, поки recovery policy не перевірить worker/run state та останній checkpoint.

## 6.6 Merge slot / resource locking

Cairn підтримує exclusive merge slot для операцій інтеграції, а також locks для ресурсів:

- `repo:<id>:branch:<name>`;
- `symbol:<stable-key>`;
- `schema:<db>:<table>`;
- `workflow:<run-id>`;
- `release:<environment>`.

Lock scope має бути мінімальним. Блокування всього репозиторію — останній fallback, а не default. Semantic conflict detection працює окремо від file-level Git conflicts.

---

# 7. Workflow Runtime і процеси

## 7.1 Declarative Workflow DSL

Рекомендований формат: YAML/TOML/JSON із canonical JSON internal representation. Приклад:

```yaml
workflow: feature-delivery
version: 1
inputs:
  feature:
    type: string
    required: true
steps:
  - id: discovery
    kind: agent_task
    role: explorer
  - id: spec
    kind: agent_task
    role: architect
    needs: [discovery]
  - id: plan_review
    kind: cross_provider_review
    needs: [spec]
    reviewer_policy: different_provider
  - id: implementation
    kind: fan_out
    needs: [plan_review]
    tasks_from: spec.outputs.work_packages
    parallelism: 3
  - id: integration
    kind: integration
    needs: [implementation]
    lock: repo.merge_slot
  - id: quality
    kind: verification
    needs: [integration]
  - id: acceptance
    kind: human_gate
    needs: [quality]
  - id: publish
    kind: git_publish
    needs: [acceptance]
```

## 7.2 Workflow compilation

До запуску:

- validate schema/version;
- resolve variables;
- validate dependency DAG and conditional branches;
- calculate expected capabilities;
- estimate resource/token budget;
- detect unresolvable gates;
- bind policy versions;
- freeze template revision and inputs;
- produce execution plan preview.

## 7.3 Execution semantics

- Кожен step має idempotency key.
- Side effects мають бути зареєстровані до/після виконання.
- Checkpoint пишеться після кожного safe boundary.
- Retry не повинен дублювати зовнішні side effects.
- Cancel/pause має бути cooperative і з hard-stop fallback.
- Compensation step може відкотити лише явно визначені side effects; Git revert не є універсальною компенсацією.
- Кожен output стає artifact/evidence з provenance.

## 7.4 Gates

Gate state: `pending`, `satisfied`, `failed`, `expired`, `escalated`, `cancelled`.

Gate resolver має бути adapter-based. Webhook preferred, polling fallback. Усі resolver runs мають зберігати check time, source response hash, auth identity, result і diagnostic. Human gate не може бути автоматично закритий агентом, якщо agent не має спеціального grant і це не дозволено policy.

## 7.5 Ephemeral runs

Ephemeral workflow має retention class і явний promotion operation. Promotion створює durable summary + provenance link. Purge має бути idempotent і перевіряти dependency references/retention locks.

---

# 8. Agent Runtime і контроль сесій

## 8.1 Agent Registry

Agent profile включає:

- provider/runtime;
- model capability tier;
- reasoning/effort mapping;
- context window and tokenization metadata;
- tools available;
- operating system/environment;
- supported hooks/events;
- session resume capability;
- message delivery semantics;
- permission model;
- data egress policy;
- cost model.

Не можна вважати «Claude» або «Codex» одним незмінним runtime. Adapter version та provider version мають бути зафіксовані в Session/Run.

## 8.2 Agent Host Contract

Мінімальні операції:

- `discover_capabilities()`;
- `create_session(project, worktree, instructions)`;
- `resume_session(session_ref)`;
- `send_message(session_ref, payload, delivery_mode)`;
- `queue_message(session_ref, payload)`;
- `stop(session_ref, mode)`;
- `get_session_state(session_ref)`;
- `read_transcript_page(session_ref, cursor, budget)`;
- `subscribe_events(session_ref)`;
- `apply_model_settings(session_ref, settings)`;
- `get_usage(session_ref)`;
- `close_session(session_ref)`.

Adapter повертає `supported`, `unsupported`, `unknown`, а не вигадує успішну операцію. Успішна доставка повідомлення має підтверджуватися host event/receipt.

## 8.3 Hook ingestion

Hooks можуть збирати session start/end, prompt submit, tool use, tool result, compaction, stop, permission request, user decision markers. Вони не повинні покладатися на один конкретний transcript format.

Capture levels:

- Level 0: explicit CLI/MCP write;
- Level 1: provider hooks;
- Level 2: out-of-band journal ingestion;
- Level 3: host adapter events.

Усі джерела нормалізуються в Event Envelope, але raw data залишається source-specific і не прирівнюється до verified semantic fact.

## 8.4 Recovery packet

На старті сесії агент отримує компактний packet:

1. current task + acceptance contract;
2. active workflow step + next action;
3. changed decisions/contracts relevant to scope;
4. code-symbol context for affected symbols;
5. open blockers/gates;
6. ownership/lease/fencing token;
7. policy and permission summary;
8. current base revision and expected changed files;
9. last checkpoint + unresolved errors;
10. budget remaining.

Packet містить checksum/revision і `next` command. Якщо packet частковий/обрізаний, агент має запросити recovery endpoint, а не домислювати.

## 8.5 Human authority

Усі важливі user decisions записує Cairn Host/UI, а не agent text. Agent може ініціювати запит на рішення, але не може сам оголосити, що користувач погодився.

Decision record має включати:

- actor identity;
- точне джерело user intent (panel action або message event);
- normalized decision kind;
- scope/run/task;
- timestamp;
- policy context;
- resulting state transition;
- idempotency key.

---

# 9. Context Compiler і семантичний retrieval

## 9.1 API

`context.compile(query)` приймає:

- task/work item;
- agent profile;
- project/repository;
- target revision/commit;
- target symbols/files;
- purpose (`understand`, `plan`, `edit`, `review`, `debug`, `release`);
- token/character budget;
- trust policy;
- freshness window;
- allowed sources.

Повертає `ContextPackage` із blocks, provenance, confidence/trust labels, omissions, cost estimate та overflow decision.

## 9.2 Retrieval pipeline

1. Parse intent and scope.
2. Resolve stable object/symbol IDs.
3. Query exact links and typed graph edges.
4. Retrieve code facts and current AST/symbol data.
5. Retrieve decisions/contracts/invariants valid for target revision.
6. Retrieve relevant work history and previous attempts.
7. Retrieve evidence and contradictory assertions.
8. Rank by task relevance, graph distance, freshness, trust, specificity and utility.
9. Deduplicate and resolve supersession.
10. Pack within budget.
11. Emit citations/provenance and explicit uncertainty.

## 9.3 Ranking model

Рекомендована scoring function із окремими факторами, а не одним непрозорим similarity score:

`score = w_r*relevance + w_p*provenance_quality + w_t*trust + w_f*freshness + w_s*scope_match + w_g*graph_proximity + w_u*utility - w_c*contradiction_penalty - w_x*token_cost`

Weights залежать від purpose. Для security review trust/evidence важливіші за semantic similarity. Для exploratory research можна підняти recall і показувати гіпотези окремо.

## 9.4 Progressive disclosure

- L0: concise task context and next action;
- L1: relevant decisions/contracts + key code symbols;
- L2: supporting evidence/diffs/tests;
- L3: full source excerpts/transcript archive on explicit request.

Видача має бути bounded і deterministic. Якщо релевантний матеріал не поміщається, система повідомляє про omissions і пропонує fetch конкретних blocks. Не обрізати provenance/critical constraints заради економії.

## 9.5 Context cache

Cache key включає:

- task revision;
- target commit/tree hash;
- semantic index revision;
- policy version;
- agent profile/context budget;
- query intent.

Зміна будь-якого ключового компонента invalidates cache. Не можна використовувати context package, зібраний для старої ревізії, без stale warning.

---

# 10. Semantic Code Engine

## 10.1 Indexing strategy

- Python: `ast`;
- PHP: `nikic/php-parser`;
- TypeScript/JavaScript: TypeScript compiler API;
- Rust: rust-analyzer/Tree-sitter fallback;
- Go: go/packages + AST;
- Java/Kotlin: language server / Tree-sitter;
- Markdown: heading hierarchy;
- SQL: parser + schema introspection;
- fallback: Tree-sitter with explicit precision labels.

Кожен extractor публікує capability matrix: exact declarations, references, call graph, type resolution, macros/dynamic behavior, confidence.

## 10.2 Code graph

Nodes: repo, file, module, package, class, function, method, property, endpoint, type, table, column, config key, test, build target.

Edges: contains, imports, calls, implements, extends, overrides, reads, writes, emits, subscribes, exposes, tests, configures, depends_on.

## 10.3 Incremental indexing

- Git diff identifies changed files.
- Parser re-indexes changed files only.
- Symbol matching uses stable key + structural fingerprint + rename detection.
- Downstream edges re-resolve incrementally.
- Semantic graph revision is tied to commit/tree hash.
- Failed parse leaves previous index visible as stale, never silently as current.

## 10.4 Impact analysis

`impact(symbol)` returns:

- direct callers/callees;
- public API consumers;
- data schema dependencies;
- tests validating the symbol;
- contracts/invariants attached;
- prior decisions and incidents;
- active tasks/locks touching this symbol;
- downstream repositories/services;
- uncertainty/unresolved edges.

Precise edges and fuzzy candidates must be clearly distinguished.

---

# 11. Evidence, provenance і decision lifecycle

## 11.1 Event envelope

```json
{
  "event_id": "evt_01J...",
  "event_type": "decision.accepted",
  "schema_version": "1.0",
  "aggregate_id": "dec_...",
  "actor": {"type": "human", "id": "user_..."},
  "occurred_at": "...",
  "recorded_at": "...",
  "correlation_id": "run_...",
  "causation_id": "evt_...",
  "source": {"adapter": "workbench", "version": "..."},
  "payload": {},
  "payload_hash": "sha256:..."
}
```

Event log має бути append-only; corrections — нові events. Event hash chaining або signed checkpoints потрібні для tamper evidence, але не слід називати це юридичним digital signature без key management.

## 11.2 Decision lifecycle

`proposed → under_review → accepted → active → superseded/revoked/expired`

Важливо розрізняти:
- accepted — рішення схвалене;
- active — рішення застосовується в поточному scope/revision;
- superseded — замінене новим;
- revoked — скасоване;
- expired — завершився термін чинності.

## 11.3 Evidence types

- Git commit/tree/diff;
- test run/result/log digest;
- CI workflow/run;
- code review;
- human approval;
- external issue/PR;
- runtime observation;
- benchmark result;
- source excerpt/transcript segment;
- artifact checksum.

Evidence record містить source, immutable reference/hash, collection method, timestamp, revision, verifier, trust class та retention.

## 11.4 Causality policy

`session_touched_symbol` не означає `session_caused_change`. Причинний зв'язок можна стверджувати лише за evidence chain: task assignment → session/tool activity → changed files/diff → commit → verification. Навіть цей ланцюг може доводити участь, але не обов'язково повну причинність. UI має показувати strength/level:

- direct: explicit causal event/decision;
- strong: change-set + session/tool evidence;
- inferred: temporal correlation;
- unknown: insufficient evidence.

---

# 12. Coordination, conflict detection і safe integration

## 12.1 Conflict classes

1. Textual conflict — Git merge conflict.
2. Stale-base conflict — branch основана на застарілому tree.
3. Symbol conflict — два агенти редагують той самий symbol.
4. Contract conflict — зміна порушує API/schema/architecture contract.
5. Semantic conflict — два зміни несумісні, навіть якщо файли різні.
6. Resource conflict — спільний stateful resource (migration, release, lockfile).
7. Decision conflict — зміна суперечить чинному accepted decision.
8. Workflow conflict — інший run уже володіє scope або ресурсом.

## 12.2 Ownership model

Task scope може містити allowed paths, symbols, modules, schemas, branches та resources. Агент не може змінювати поза scope без явного scope amendment. Scope expansion — подія, яку затверджує orchestrator або людина згідно policy.

## 12.3 Integration pipeline

1. Verify claim + lease + fencing token.
2. Verify base commit.
3. Check allowed files/symbol scope.
4. Re-index changed code.
5. Run textual merge/checks.
6. Run semantic impact analysis.
7. Check decisions/contracts/invariants.
8. Run required tests/quality gates.
9. Generate evidence package.
10. Acquire merge slot.
11. Integrate to target branch.
12. Release locks and publish state.

## 12.4 Worktree isolation and sandbox

Git worktree — isolation of working directory, not a security sandbox. Worker process requires OS-level least privilege, filesystem allowlists, network policy, process limits, secret isolation, resource limits and optional container/VM. No agent should receive broad host credentials by default.

---

# 13. Federation і multi-project architecture

## 13.1 Project topology

Cairn має підтримувати:
- single repository;
- multi-repo project;
- multiple projects in one local daemon;
- team shared store;
- federated organizations/locations;
- disconnected/offline replicas.

## 13.2 Replication classes

| Data class | Default sync | Merge policy |
|---|---|---|
| Durable decisions/contracts | Yes | Human-reviewed semantic merge |
| Work items/dependencies | Yes | Field-level merge + graph validation |
| Events | Yes, append-only | Union by event ID/hash |
| Claims/leases | No global replication | Replica-local; share visibility only |
| Session raw transcript | No | Explicit selective export |
| Redacted session summaries | Policy-controlled | Append-only + supersession |
| Embeddings/indexes | No | Rebuild locally |
| Secrets | Never | OS secret store only |
| Ephemeral workflows | No by default | Promotion required |

## 13.3 Sovereignty and trust tiers

Cairn should implement configurable data classes, not merely a single global tier:

- public;
- organization-internal;
- restricted/pseudonymized;
- confidential/local-only;
- secret (never sync).

A peer has a trust relationship and explicit object scopes. A remote may receive sanitized semantic assertions but not source transcript, local path, user identity or credential.

## 13.4 Conflict reconciliation

- Append-only event union for immutable events.
- CRDT only for fields where semantics support it.
- Versioned object revisions with three-way merge.
- Graph constraint validation after merge (cycles, missing refs, invalid gates).
- Semantic conflict queue for contradictory decisions/facts.
- No silent last-write-wins for authority, trust, policy, lifecycle or security fields.

## 13.5 Federation planned capabilities

Directional `push` and `pull` should be part of Cairn's API from the beginning, but be clearly versioned and not conflated with bidirectional `sync`. Implement only after replica identity, authority, conflict model and failure recovery are specified. This directly addresses the Beads documentation's distinction between existing bidirectional sync and planned/unexposed single-direction commands.

---

# 14. Configuration, security and privacy

## 14.1 Configuration precedence

Recommended precedence:

1. explicit command/request override;
2. process environment (only approved non-secret keys);
3. machine-local config;
4. user config;
5. workspace/project shared config;
6. organization policy;
7. defaults.

Security policy may prohibit lower-priority overrides. Effective config UI shows winning value and shadowed sources.

## 14.2 Secrets

- OS keychain/credential manager or encrypted local vault.
- Secret references only in shared config; actual values never in Git/events/metadata.
- Endpoint pinning before sending credentials.
- Rotation and revocation workflow.
- Redaction before persistence and before any model call.
- Redaction reports with detected pattern class, not raw secret.

## 14.3 Prompt injection / untrusted content

All retrieved session text, external issue content, web pages, code comments and imported records are untrusted data. They must be returned in a content envelope with provenance and instruction/data distinction. Retrieved content cannot modify system policy or tool permissions.

## 14.4 Permission model

RBAC/ABAC scopes:
- read project;
- read restricted semantic records;
- write observations;
- propose decisions;
- approve decisions;
- claim work;
- execute tools;
- change workflow policy;
- authorize autopilot;
- publish/merge;
- manage secrets/federation.

Agent identity is never equivalent to human identity. Human approval must be a first-class event, not inferred from an agent message.

## 14.5 Auditability

Every privileged action records actor, grant, policy, target, input hash, output/effect, timestamp and outcome. Audit export is tamper-evident and redacted.

---

# 15. Workbench UI/UX

## 15.1 Design principle

UI should make the system's state explainable at a glance, not expose every underlying graph edge. It should prioritize decisions and blockers over decorative dashboards.

## 15.2 Primary navigation

1. **Overview** — project health, active runs, blocked work, approvals, budget.
2. **Work** — board/list/graph, ready queue, dependency explorer, filters.
3. **Workflow** — visual DAG, current step, gates, run history, replay/checkpoints.
4. **Agents & Sessions** — provider sessions, status, messages, model, capabilities, usage.
5. **Semantic Explorer** — code graph, symbols, decisions, contracts, invariants, provenance.
6. **Evidence** — commits, diffs, tests, CI, reviews, approvals.
7. **Federation** — peers, sync state, conflicts, sovereignty, replica health.
8. **Settings & Policies** — providers, budgets, trust, secrets, project configuration.

## 15.3 Three-pane desktop layout

- Left: project/repository tree and navigation.
- Center: selected work item/workflow/semantic object.
- Right: context, evidence, activity, agent/session details.

A global command/search palette should resolve work IDs, symbols, decisions, sessions and commands. Avoid duplicating full provider chat when native host can open it; show normalized transcript excerpt and delivery state.

## 15.4 Work item view

Must show:
- title/status/priority/owner;
- computed eligibility and exact blocker reasons;
- parent/children and dependencies;
- affected files/symbols;
- linked decisions/contracts;
- active claim/lease;
- execution hints and budget estimate;
- acceptance criteria and evidence checklist;
- history/audit timeline.

## 15.5 Semantic object view

Must show:
- semantic meaning and type;
- current/historical validity;
- assertions with source and trust class;
- incoming/outgoing typed relations;
- code revision/symbol lineage;
- contradictory or superseding assertions;
- impact of changing this object;
- actions: propose correction, supersede, revoke, verify, link evidence.

## 15.6 Workflow view

Graph canvas should distinguish:
- ready, running, blocked, waiting gate, failed, completed;
- parallelizable tasks;
- human approvals;
- external waits;
- worker assignment;
- critical path and estimated cost.

Pause/Stop must be global and accessible while agent is busy. Stop should revoke authority synchronously at host layer, not wait for the agent to read a queued message.

## 15.7 Decision cards

A decision card contains the exact decision, scope, consequences, options, evidence and required permission. Accept/Reject/Defer actions are explicit. UI shows who made the decision and whether it is durable. Acceptance and closing a workflow are distinct decisions where policy requires it.

## 15.8 Explainability and uncertainty

No green checkmark without evidence. UI uses states such as:
- verified;
- inferred;
- proposed;
- stale;
- conflicting;
- awaiting human;
- unavailable/unverified.

Never display absent data as zero or infer provider status from silence.

---

# 16. CLI, MCP і API

## 16.1 CLI design

CLI should be low-token and scriptable, with JSON output and stable exit codes. Command groups:

- `cairn project ...`
- `cairn work ...`
- `cairn graph ...`
- `cairn workflow ...`
- `cairn agent ...`
- `cairn session ...`
- `cairn context ...`
- `cairn semantic ...`
- `cairn evidence ...`
- `cairn gate ...`
- `cairn sync ...`
- `cairn config ...`
- `cairn doctor ...`

Avoid cloning Beads command names/flags. CLI expresses Cairn's own domain model.

## 16.2 MCP tools

Keep tool count small and high-value:

- `cairn_context` — compile bounded context;
- `cairn_work_next` — list/claim eligible work;
- `cairn_work_show` — show task and blockers;
- `cairn_work_update` — propose/update permitted work state;
- `cairn_semantic_query` — retrieve assertions/relations;
- `cairn_code_explore` — symbol/code impact;
- `cairn_decision_propose` — propose a decision, never silently approve;
- `cairn_evidence_attach` — attach evidence;
- `cairn_checkpoint` — persist run checkpoint;
- `cairn_handoff` — create controlled handoff;
- `cairn_status` — compact state summary.

Every result carrying retrieved content includes provenance and trust envelope. Tool schemas must remain compact; large objects use references and progressive fetch.

## 16.3 API

- Local HTTP API bound to loopback by default.
- WebSocket for state updates and session stream.
- Auth token scoped per local UI/adapter.
- CSRF/origin checks for browser-facing endpoints.
- Explicit CORS allowlist.
- Idempotency keys for mutating operations.
- API schema versioning and compatibility tests.

---

# 17. Budget and model routing

## 17.1 Budget dimensions

- input tokens;
- output tokens;
- tool calls;
- latency;
- estimated monetary cost;
- local compute cost;
- context retrieval cost;
- total task/run budget.

Each measurement has status `reported`, `estimated`, or `unknown`. Never present estimated cost as billed actual.

## 17.2 Routing tiers

- L0 deterministic local code/index queries;
- L1 local lightweight model for classification/summarization;
- L2 capable local or low-cost remote model for planning/extraction;
- L3 high-reasoning model for architecture/security/high-risk decisions.

Routing must respect data sovereignty and model capability, not just cost.

## 17.3 Budget enforcement

Before invocation:
- estimate prompt/context tokens;
- reserve budget atomically;
- verify run and org limits;
- reject or downgrade if budget insufficient;
- record provider quote/estimated cost.

After invocation:
- ingest provider usage when available;
- reconcile reservation;
- record unknown usage explicitly;
- prevent further calls after hard limit.

## 17.4 Context budget allocation

Reserve minimum space for:
- task contract;
- security/policy instructions;
- active decisions/invariants;
- code context;
- evidence;
- recent handoff.

Do not allocate all tokens to semantic similarity. Critical constraints must be pinned, not ranked away.

---

# 18. Data model and schema outline

## 18.1 Core tables

- `objects(id, object_type, namespace, stable_key, schema_version, lifecycle_state, created_at, updated_at)`
- `object_revisions(id, object_id, revision, payload_json, payload_hash, valid_from, valid_until, recorded_at)`
- `semantic_assertions(id, subject_id, predicate, object_ref, literal_json, claim_type, polarity, trust_class, confidence, valid_from, valid_until, source_event_id)`
- `relations(id, from_id, to_id, relation_type, execution_effect, validity_json, provenance_json)`
- `events(event_id, event_type, aggregate_id, actor_json, occurred_at, recorded_at, causation_id, correlation_id, payload_json, payload_hash)`
- `work_items(id, type, status, priority, parent_id, assignee_id, acceptance_contract_id, scope_json)`
- `work_dependencies(from_id, to_id, dependency_type, condition_json)`
- `claims(id, work_item_id, agent_runtime_id, lease_id, fencing_token, claimed_at, expires_at, state)`
- `agents(id, provider, runtime, capability_json, trust_profile_id)`
- `sessions(id, agent_id, project_id, host_ref, state, started_at, ended_at, adapter_version)`
- `workflow_definitions(id, name, version, definition_json, content_hash)`
- `workflow_runs(id, definition_id, definition_hash, input_snapshot, state, checkpoint_id)`
- `workflow_steps(id, run_id, step_key, work_item_id, state, attempt, idempotency_key)`
- `gates(id, gate_type, condition_json, state, evidence_id, deadline_at)`
- `evidence(id, evidence_type, source_ref, content_hash, collected_at, verifier_json, trust_class, metadata_json)`
- `code_symbols(id, repo_id, stable_key, language, qualified_name, fingerprint, revision, span_json)`
- `code_edges(from_symbol_id, to_symbol_id, relation_type, confidence, revision)`
- `resource_locks(id, resource_key, holder_id, fencing_token, expires_at, mode)`
- `config_values(key, scope, value_json, source, secret_ref, revision)`
- `usage_records(id, run_id, session_id, provider, input_tokens, output_tokens, cost_value, cost_status, recorded_at)`
- `replicas(id, replica_key, topology, last_sync_at, authority_scope, trust_state)`
- `sync_records(id, peer_id, direction, cursor, status, conflict_count, started_at, finished_at)`

## 18.2 Indexes

- FTS5 for titles, descriptions, assertion text, decision summaries.
- B-tree for IDs, lifecycle, project, status, timestamps, validity intervals.
- Composite indexes for graph traversals and work frontier.
- Optional vector index is derived and replaceable.
- Code symbol indexes keyed by repository + stable key + revision.

## 18.3 Migration rules

- Every schema has explicit version.
- Migration is transactional or has recoverable journal.
- No silent lossy migration.
- Keep old schema reader for supported compatibility window.
- Migration preview + backup + post-migration integrity check.
- Object schema and database schema are versioned separately.

---

# 19. Observability and diagnostics

Metrics:
- ready frontier computation latency;
- claim contention and lease expiry;
- stale worker writes rejected;
- workflow recovery success;
- context retrieval latency and token cost;
- evidence coverage per completed task;
- semantic index freshness;
- parser precision/recall by language;
- conflict detection precision/recall;
- merge gate false positive/negative;
- sync lag and federation conflict rate;
- provider delivery failure/retry;
- budget estimation error.

Diagnostics:
- `cairn doctor` checks config precedence, database integrity, Git state, index staleness, adapters, permissions, secrets exposure, sync health and schema compatibility.
- `cairn explain eligibility <work-id>` explains why a task is/isn't ready.
- `cairn explain context <package-id>` shows ranking, included/excluded blocks and budget arithmetic.
- `cairn explain trust <assertion-id>` shows provenance and promotion path.
- `cairn replay <run-id>` reconstructs state transitions from events/checkpoints.

---

# 20. Testing and benchmark program

## 20.1 Functional tests

- Work graph dependencies, cycles, conditionals, fan-out/fan-in.
- Gate resolution by human/timer/CI/PR/external source.
- Atomic claims under concurrent agents.
- Lease expiry, heartbeat, reclaim, fencing token.
- Workflow pause/stop/retry/resume and idempotency.
- Semantic assertion provenance, temporal validity, supersession, contradiction.
- Code symbol rename/move/split lineage.
- Context compiler budget and stale cache invalidation.
- Human decision authority enforcement.
- Secret redaction and endpoint pinning.
- Federation conflict reconciliation and offline recovery.

## 20.2 Adversarial/concurrency tests

- Two agents claim same ready task simultaneously.
- Old worker writes after lease expiry.
- Replicas sync stale claim state.
- Decision superseded during active workflow.
- CI gate webhook delivered twice/out of order.
- Agent attempts to self-approve.
- Malicious transcript contains instruction injection.
- Context package generated against old commit.
- Two non-overlapping file edits violate shared semantic contract.
- User pauses autopilot while provider is busy and message queue is non-empty.

## 20.3 Benchmarks

Cairn must publish reproducible baseline datasets and scripts. Required comparisons:

1. Manual `git log`, `git log -p`, `git log -L` vs Cairn context retrieval.
2. Full transcript vs compacted context vs evidence graph retrieval.
3. Beads-like work graph operations vs Cairn Work Graph latency/consistency.
4. Workflow Kit-like session operations vs Cairn Host Adapter delivery/stop/recovery.
5. Multi-agent task throughput with/without semantic locks.
6. Repeated edits, reverted changes, regressions and reopen count.
7. Cost estimate vs provider-reported usage.
8. Federation convergence and conflict rate under network partition.

## 20.4 Quality targets (proposed, must be measured)

- No duplicate successful claim under concurrent claim test.
- No accepted stale fencing-token write.
- 100% human decision audit coverage for privileged transitions.
- No silent gate success without evidence.
- Context hard budget overrun: 0 for deterministic packing unit tests; provider tokenizer estimate error measured separately.
- Semantic index freshness: p95 under 2 seconds for changed files in a 10k-file repo on reference hardware (benchmark target, not current fact).
- Recovery of interrupted workflow: no lost durable event; at-least-once replay with idempotent side effects.
- Federation: explicit conflict rather than silent authority overwrite.

---

# 21. Implementation roadmap

## Phase 0 — Contract and repository foundation

- Define object envelope, event envelope, relation ontology, schema policy.
- Set module boundaries and API versioning.
- Establish CI, type checks, test harness, packaging and platform matrix.
- Implement local project registry and `cairn doctor`.

**Exit:** empty project can initialize, inspect config provenance, validate schemas and recover after interrupted write.

## Phase 1 — Work Graph kernel

- WorkItem, typed dependencies, ready frontier, atomic claim.
- Agent identity + assignments + leases + fencing tokens.
- Comments/threads, labels, metadata extensions.
- JSON CLI and MCP minimal tools.

**Exit:** two concurrent agents cannot both own same task; explain eligibility works.

## Phase 2 — Evidence and semantic objects

- Event ledger, semantic envelope/assertions, provenance, trust classes.
- Decision lifecycle, supersession/revocation, bi-temporal query.
- Evidence attachments and verification states.
- FTS5 and semantic query.

**Exit:** new session can recover a decision with evidence and current validity; rejected knowledge is never injected as authoritative.

## Phase 3 — Code semantic engine

- Python/PHP/TS extraction; stable symbol identity and lineage.
- Incremental indexing and code graph.
- `why(symbol)`, `explore(symbol)`, `impact(symbol)`.
- Context Compiler with budget, provenance and trust filtering.

**Exit:** context package for a method contains relevant prior decisions, changed history, current code facts and explicit uncertainty.

## Phase 4 — Workflow Runtime

- Declarative workflow DSL, compile/validate, run/step/checkpoints.
- Fan-out/fan-in, conditional paths, gates, ephemeral runs.
- Human approval and evidence-based acceptance.
- Retry, pause, stop, recovery and idempotency.

**Exit:** end-to-end workflow resumes after process kill without duplicate side effects.

## Phase 5 — Agent Host and Workbench

- Claude/Codex/Gemini/Kimi adapters by capability.
- Session lifecycle, message delivery receipts, transcript paging, hooks.
- Local Workbench: projects, work, workflow, sessions, decisions, evidence.
- User-authority controls and autopilot grants.

**Exit:** one UI coordinates at least two provider families and preserves human authority.

## Phase 6 — Coordination and safe integration

- Git worktrees, scoped file access, symbol locks, merge slot.
- Semantic conflict detection and contract gates.
- CI/PR integration and verified publish.
- Sandbox execution adapter.

**Exit:** parallel agents can work safely on separate scopes and integration blocks semantic contract violations.

## Phase 7 — Federation

- Portable export/import and sync snapshots.
- Replica identity and authority scopes.
- Peer sync, selective sync, data sovereignty, conflict UI.
- Offline replica recovery and sync observability.

**Exit:** disconnected replicas converge without silent loss of decisions, evidence or authority state.

## Phase 8 — Intelligence and optimization

- Model routing, local models, budget reservation/reconciliation.
- Learning from failed/repeated edits with controlled promotion.
- Adaptive retrieval and per-project optimization.
- Optional embeddings/reranking.

**Exit:** measured reduction in context cost and repeated regressions without reduced evidence coverage.

---

# 22. Vertical slice — перший наскрізний сценарій

Перший PoC має бути вузьким, але end-to-end:

1. Користувач створює задачу «Змінити refresh token rotation».
2. Cairn індексує PHP 8.4 репозиторій і знаходить `AuthService::refresh`, пов'язані symbols та tests.
3. Context Compiler повертає попереднє рішення, пов'язаний invariant, минулі невдалі спроби, relevant diff та token estimate.
4. Workflow Runtime формує plan: analysis → implementation → review → tests → human acceptance.
5. Агент отримує atomic claim і worktree з обмеженим scope.
6. Агент пропонує зміну; Cairn записує session/tool evidence та diff.
7. Semantic Engine перевіряє contract impact; test adapter запускає потрібні перевірки.
8. Якщо перевірка успішна, створюється evidence package; merge gate серіалізує інтеграцію.
9. Людина приймає результат; Cairn фіксує authoritative decision.
10. Інша сесія/інший провайдер запитує «чому refresh реалізовано так?» і отримує рішення з evidence та актуальною валідністю.

Цей сценарій доводить спільну цінність Work Graph + Semantic Graph + Execution Graph + Evidence Graph.

---

# 23. Прямі відповіді на вісім контрольних питань

## 1. Короткі сесії та сабагенти

Кожна сесія має власний Session ID, Run/Task reference, checkpoint та capture stream. Коротка сесія не залежить від compaction: hooks/host adapter пишуть нормалізовані events під час роботи, а session-end/out-of-band sweep добирає пропущене. Після завершення створюється handoff artifact: зроблено, не зроблено, зміни, рішення, evidence, невирішені питання, next action. Якщо агент завершився до summary, система відновлює стан з events + Git diff + tool receipts, позначаючи inferred facts окремо.

## 2. Рішення в одній сесії, код в іншій

Рішення не зберігається лише в transcript summary. Воно є durable Decision object із semantic assertions і links до Task/Session/Commit/Symbol/Contract. Коли інша сесія торкається symbol, Context Compiler знаходить Decision через semantic graph та перевіряє його validity/revision.

## 3. Сусідні проєкти та різні кореневі коміти

Project identity не має залежати лише від одного root commit. Використовується стабільний `project_id`, repository identity та repo fingerprint як атрибути. Cross-repo projects зв'язуються через explicit RepositoryLink/CapabilityContract. Якщо orchestrator працює поза коренем repo, його session event може бути пов'язаний із project через explicit workspace mapping, host context або user confirmation. Автоматичний fuzzy matching лише створює candidate link, не authoritative association.

## 4. Скасоване рішення

Decision lifecycle зберігає старий об'єкт, але закриває його validity interval та створює `revoked` або `superseded` relation. Retrieval за замовчуванням не повертає його як активну інструкцію, але може показати як історичний контекст із поміткою «скасовано».

## 5. Вимірювання результату

Потрібно вимірювати не тільки retrieval cost. Базові outcome metrics: повторні зміни того самого symbol, reopen rate, reverted commit rate, regression rate, time-to-correct, defect escape rate, failed acceptance attempts, context cost per accepted change. До/після порівнюється на matched tasks і однакових repo snapshots, з control group.

## 6. База порівняння

Benchmark має включати ручний `git log`, `git log -p`, `git log -L`, повне читання файлів, session transcript search, Beads issue/decision lookup, Workflow Kit recovery packet та Cairn Context Compiler. Порівнюються bytes/tokens, latency, completeness, provenance, task success і повторні правки. Необхідно публікувати точні query, corpus, model, tokenization method і правила inclusion/exclusion.

## 7. Втрати при compaction

Raw event archive та compacted semantic state — різні рівні. Compaction не має знищувати source event. Для кожного promoted assertion зберігається source span/hash, omitted alternatives, extraction method та reviewer state. Sampling audit оцінює recall: яка частина важливих фактів/альтернатив/замірів із raw session потрапила в durable graph. Якщо source transcript уже недоступний, система чесно знижує trust і позначає evidence gap.

## 8. Захист від отруєння пам'яті

Усі agent-generated claims починають як `agent_proposed` або `agent_inferred`; retrieved content — untrusted. Потрібні source provenance, secret redaction, trust labels, instruction/data separation, scoped visibility, contradiction detection, promotion gates, immutable audit і explicit human/system authority. Повторення claim не підвищує його trust. Жоден agent transcript не може змінити system policy або перетворити довільний текст на глобальну інструкцію.

---

# 24. Відмінності, спільне та синергія трьох проєктів

| Система | Найсильніша сторона | Природна межа | Роль у Cairn Unified |
|---|---|---|---|
| Beads | Durable work graph, dependency frontier, workflow formulas/molecules, gates, claims, federation | Не є повноцінним code-semantic/provenance engine; agent registry не є окремою сутністю; leases replica-local | Функціональний референс Work Graph, dependency semantics, workflow/federation edge cases |
| CommerceCentr Cairn | Compact historical context, symbol/file addressing, portable JSONL store, budgeted recall, session adapters | Session-level links не доводять causality; немає повної work/workflow model; summary-only limits | Основа semantic memory, code context, extraction, portable records |
| Workflow Kit | Local agent host/orchestration, provider session control, human decisions, autopilot authority, acceptance, Git hygiene, UI | Не є повним cross-session semantic code graph і temporal knowledge system | Execution control plane reference, host adapters, operator UX, authority/acceptance model |

## 24.1 Що є спільного

- Усі три системи прагнуть зберегти continuity між агентськими сесіями.
- Усі три використовують локальні/project-scoped artifacts та інструкції.
- Усі три пов'язують agent work із Git/project state.
- Усі три мають механізми recovery/context handoff.
- Усі три прагнуть зменшити повторну роботу та залежність від людського copy-paste.

## 24.2 Що принципово різне

- Beads зосереджений на **стані роботи та залежностях**.
- Cairn — на **семантичній пам'яті й причинно адресованому контексті**.
- Workflow Kit — на **контролі виконання, сесій та людських повноважень**.

Це не три версії однієї системи. Їхні моделі доповнюють одна одну, якщо не змішувати canonical responsibilities.

## 24.3 Який продукт може вийти

Cairn Unified може стати local-first control plane, де:

- Work Graph знає, що робити;
- Semantic Graph знає, що це означає і чому;
- Execution Graph знає, хто і як виконує;
- Evidence Graph доводить, що сталося;
- Context Compiler передає агенту лише релевантне;
- Policy Engine контролює, що агенту дозволено;
- Workbench дає людині зрозумілий контроль.

Перевага виникає не через максимальну кількість функцій, а через **єдину provenance-модель і узгоджені переходи станів** між плануванням, виконанням, кодом, доказами та пам'яттю.

---

# 25. Non-goals і ризики

Не є цілями першого релізу:

- повна заміна Jira/Linear/GitHub Issues;
- власна LLM-модель;
- повноцінний IDE/editor;
- універсальний distributed consensus engine;
- повна автоматизація будь-якого provider UI;
- обов'язкова vector DB;
- автоматичне перетворення всіх розмов на authoritative memory.

Основні ризики:

1. Надмірна модель — багато типів/зв'язків до того, як є реальні use cases.
2. Складна federation — distributed claims та authority важчі за звичайний file sync.
3. Provider API churn — adapters потребують versioned capabilities і контрактних тестів.
4. Semantic false positives — fuzzy code links можуть бути помилково сприйняті як точні.
5. Trust poisoning — погано контрольоване promotion перетворює agent hallucination на policy.
6. UI overload — показ усіх графів одночасно створює інформаційний шум.
7. Over-orchestration — система може витрачати більше часу на керування агентами, ніж на роботу.
8. Storage coupling — не слід прив'язувати canonical state до одного важкого backend до перевірки scale need.

---

# 26. Початкові ADR

- **ADR-001:** Modular monolith + isolated workers.
- **ADR-002:** Event ledger + typed object revisions; SQLite is rebuildable projection.
- **ADR-003:** Git is source of truth for code, not all runtime state.
- **ADR-004:** Semantic Envelope + Semantic Assertion are separate concepts.
- **ADR-005:** Typed relations explicitly declare execution effect.
- **ADR-006:** Bi-temporal validity for durable knowledge.
- **ADR-007:** Agent-generated knowledge is untrusted until promoted.
- **ADR-008:** Human authority is recorded by host/UI, not inferred from agent output.
- **ADR-009:** Work eligibility is computed and explainable.
- **ADR-010:** Claim, lease, lock and merge slot are distinct primitives.
- **ADR-011:** Federation is opt-in and scope-selective; local-only remains default.
- **ADR-012:** MCP + CLI are primary agent interfaces; Workbench uses local API.
- **ADR-013:** Provider adapters report capability/unsupported states honestly.
- **ADR-014:** Context compiler never hides omissions or trust state.
- **ADR-015:** No cloud fallback without explicit opt-in.

---

# 27. Definition of Done для Cairn 2.0

Cairn 2.0 не можна вважати готовим лише тому, що є dashboard, task list і MCP server. Мінімальна продуктова готовність означає:

- один project graph містить WorkItems, Decisions, Contracts, Symbols, Sessions та Evidence;
- інша сесія може відновити роботу без transcript copy-paste;
- ready frontier враховує dependencies, gates, claims, capabilities та policies;
- workflow можна pause/stop/resume без втрати durable events;
- людські рішення мають окрему authority chain;
- code context адресується до stable symbol identity і конкретної revision;
- semantic facts мають provenance, trust, validity і supersession;
- агент не може самостійно підвищити довіру до власного тексту;
- parallel workers ізольовані, а stale writes відхиляються;
- інтеграція проходить semantic/evidence gates;
- context budget жорстко контролюється та пояснюється;
- local-only режим працює без зовнішньої мережі;
- backup/restore/rebuild/recovery перевірені автоматично;
- benchmark демонструє вимірюване зниження повторної роботи, а не лише менший prompt.

---

# 28. Джерела

## Beads — офіційна документація

- Core concepts: https://beads.gascity.com/core-concepts/index
- Issues & dependencies: https://beads.gascity.com/core-concepts/issues
- Configuration: https://beads.gascity.com/reference/configuration
- Agent coordination: https://beads.gascity.com/multi-agent/coordination
- Federation: https://beads.gascity.com/multi-agent/federation#planned-features
- Dependencies and gates: https://beads.gascity.com/core-concepts/dependencies
- Graph links: https://beads.gascity.com/core-concepts/graph-links
- Issue metadata: https://beads.gascity.com/core-concepts/metadata
- Workflows: https://beads.gascity.com/workflows
- Molecules: https://beads.gascity.com/workflows/molecules
- Formulas: https://beads.gascity.com/workflows/formulas
- Gates: https://beads.gascity.com/workflows/gates
- Wisps: https://beads.gascity.com/workflows/wisps
- Documentation index: https://beads.gascity.com/llms.txt
- Repository: https://github.com/gastownhall/beads

## Cairn — CommerceCentr

- Repository: https://github.com/CommerceCentr/cairn
- README: https://github.com/CommerceCentr/cairn/blob/main/README.md
- Ukrainian README: https://github.com/CommerceCentr/cairn/blob/main/README.uk.md
- Agent onboarding: https://github.com/CommerceCentr/cairn/blob/main/AGENT-ONBOARD.md
- Measurements: https://github.com/CommerceCentr/cairn/blob/main/docs/MEASUREMENTS.md
- Integration: https://github.com/CommerceCentr/cairn/blob/main/docs/INTEGRATION.md
- Core store: https://github.com/CommerceCentr/cairn/tree/main/core
- Extractors: https://github.com/CommerceCentr/cairn/tree/main/extract

## Workflow Kit

- Repository: https://github.com/OleynikAleksandr/Workflow_Kit
- README: https://github.com/OleynikAleksandr/Workflow_Kit/blob/main/README.md
- Architecture overview: https://github.com/OleynikAleksandr/Workflow_Kit/blob/main/docs/architecture/OVERVIEW.md
- Usage: https://github.com/OleynikAleksandr/Workflow_Kit/blob/main/docs/usage.md
- Autopilot: https://github.com/OleynikAleksandr/Workflow_Kit/blob/main/docs/usage-autopiloting.md
- Agent instructions: https://github.com/OleynikAleksandr/Workflow_Kit/blob/main/AGENTS.md

---

# Підсумкова архітектурна позиція

Cairn Unified 2.0 повинен не «об'єднати три репозиторії», а створити спільну систему координат для трьох різних площин: **робота, знання та виконання**. Beads дає глибокий референс того, як моделювати роботу й залежності; Cairn — як стискати й адресувати історичний контекст від коду; Workflow Kit — як керувати реальними агентськими сесіями, людськими рішеннями та безпечним завершенням процесу.

Найважливіший диференціатор Cairn — Semantic Envelope + Semantic Assertion + Evidence Provenance. Саме вони з'єднають об'єкти, які зазвичай існують окремо: issue, workflow, session, decision, method, commit, test і approval. Коли система може відтворити не тільки «що відбулося», а й «чому це було зроблено», «на якій ревізії це було істинним», «хто це підтвердив» і «чи можна ще покладатися на це твердження», вона переходить від пам'яті агента до керованої семантичної інфраструктури розробки.
