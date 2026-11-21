# Cairn 2.0

## Технічна архітектура та специфікація розробки (TAS)

**Статус:** Draft for implementation (архівний проєктний документ)\
**Чинна реалізація:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)\
**Версія:** 1.0\
**Розгортання:** повністю локальне, self-hosted\
**Стратегія:** greenfield redesign\
**Пріоритети:** спільний семантичний контекст, координація агентів без
конфліктів, оптимізація бюджету.

> **Призначення:** цей документ є базовою специфікацією для
> проєктування, реалізації, тестування та подальшої передачі контексту
> між різними AI coding agents.

# 0. Резюме

Cairn 2.0 --- локальна операційна система координації AI-розробки. Вона
надає Claude Code, Codex, Cursor, Gemini CLI, локальним моделям та іншим
агентам спільну, версійовану й перевірювану модель програмного проєкту.

Cairn має забезпечити чотири незалежні можливості:

1.  **Semantic Intelligence** --- розуміння структури коду, залежностей,
    контрактів, архітектурних рішень та їхнього походження.
2.  **Coordination** --- формальне керування завданнями, ownership,
    leases, паралельними змінами та конфліктами.
3.  **Execution** --- ізольований запуск агентів, Git Worktrees,
    checkpoints та відновлення.
4.  **Cost Intelligence** --- керування контекстом, вибором моделей,
    паралельністю та бюджетом.

**Ключова теза:** жоден агент не є єдиним носієм знань про проєкт, а
жодна критична гарантія не залежить лише від поведінки AI-моделі.

## 0.1. Зафіксовані рішення

-   Нова архітектура, а не косметичний рефакторинг Cairn 0.x.
-   Повністю локальне self-hosted ядро.
-   Git --- довготривале джерело переносимих знань.
-   SQLite --- оперативний стан, події, leases та облік.
-   Semantic graph + hybrid search --- модель знань.
-   MCP --- основний зовнішній інтерфейс до контексту й інструментів.
-   Provider adapters --- незалежність від моделей та coding agents.
-   Git Worktree --- ізоляція робочих просторів.
-   Deterministic policy --- права, бюджет, блокування та merge gate.
-   Модульний моноліт + окремі worker processes; без передчасних
    мікросервісів.

## 0.2. Межі та застереження

Це цільова архітектура і специфікація, а не твердження, що всі
компоненти вже реалізовані або протестовані. Попередній аудит Cairn 0.x
був попереднім оглядом окремих компонентів. Перед міграцією необхідно
зафіксувати конкретний commit старого репозиторію, виконати повний code
review, запустити тести та підтвердити всі висновки.

# 1. Продуктове бачення та принципи

**Позиціонування:** *The Shared Intelligence Layer for AI Software
Engineering.*

Cairn --- спільний шар архітектурної пам'яті, координації та контролю
для різних AI coding agents.

### P1. Model independence

Знання належать проєкту, а не Claude, OpenAI, Google, Cursor чи іншому
провайдеру. Заміна моделі не повинна вимагати міграції знань.

### P2. Git-native portability

Затверджені архітектурні рішення, контракти, інваріанти та важливі
знання мають переносиме представлення у Git. Індекси й кеші є похідними
та відновлюваними.

### P3. Deterministic governance

Права доступу, locks, бюджет, перевірки й інтеграція змін контролюються
звичайним кодом. Prompt не є механізмом безпеки.

### P4. Evidence before assertion

Важливі факти мають provenance: файл, символ, commit, тест, документ або
затверджене рішення.

### P5. Incremental computation

Після зміни файлу перераховуються лише залежні індекси та графові
фрагменти.

### P6. Local-first

Основні сценарії працюють без хмари, зовнішньої бази або обов'язкового
мережевого доступу.

### P7. Explicit trust boundaries

LLM output і вміст репозиторію --- недовірені дані. Агенти не отримують
автоматичних прав на shell, мережу, secrets або файлову систему.

### P8. Recoverability

Кожна задача має явний стан, checkpoint та визначену поведінку після
аварії.

### P9. Minimal infrastructure

Початкова версія використовує модульний моноліт, SQLite, Git і локальні
worker processes. Redis, Kafka, Kubernetes, Neo4j та окрема vector DB не
потрібні без виміряної потреби.

### P10. Observable economics

Оптимізувати потрібно вартість успішно виконаної задачі, а не лише
кількість токенів.

# 2. Межі системи

## 2.1. Cairn відповідає за

-   реєстрацію проєктів та агентів;
-   аналіз структури й залежностей коду;
-   побудову Semantic Knowledge Graph;
-   пошук релевантного контексту;
-   формування Task Contracts;
-   планування й розподіл задач;
-   ownership, locks, leases та fencing tokens;
-   Git Worktree lifecycle;
-   запуск, контроль і відновлення агентів;
-   збереження подій, checkpoints та результатів;
-   валідацію контрактів і змін;
-   облік model usage та бюджету;
-   інтеграцію через MCP, CLI та provider adapters.

## 2.2. Cairn не повинен

-   замінювати IDE або Git;
-   бути власною LLM;
-   автоматично вважати AI-висновки правдивими;
-   комітити всі runtime-логи;
-   вимагати cloud infrastructure;
-   запускати довільний код із правами користувача;
-   автоматично зливати будь-які зміни без перевірки;
-   обіцяти абсолютну відсутність конфліктів або помилок.

# 3. Аудит Cairn 0.x та міграційна позиція

Попередній огляд вказував на Python/FastAPI, SQLite, subprocess workers,
JSON memory, агентні класи та GitHub-oriented інтеграцію. Зони, які
необхідно повторно перевірити на зафіксованому commit:

  -----------------------------------------------------------------------
  Компонент                           Питання для аудиту
  ----------------------------------- -----------------------------------
  `task_storage.py`                   Чи зберігається append-only
                                      історія, чи лише останній стан?

  `worker.py`                         Чи є heartbeat, cancellation,
                                      checkpoint, retry та recovery?

  `agent_classes.py`                  Чи відокремлені ролі, provider та
                                      tools?

  `toolbox.py`                        Чи можна працювати з локальним Git
                                      без GitHub-specific залежностей?

  `.cairn/memory/`                    Чи є schema, provenance, lifecycle
                                      та versioning?
  -----------------------------------------------------------------------

### 3.1. Обов'язковий аудит перед міграцією

1.  Зафіксувати commit hash Cairn 0.x.
2.  Побудувати inventory модулів і залежностей.
3.  Запустити наявні тести.
4.  Перевірити SQLite та JSON schemas.
5.  Перевірити GitHub coupling.
6.  Перевірити worker lifecycle і crash recovery.
7.  Визначити формат експорту старих даних.
8.  Створити fixtures та перевірити імпорт на копії репозиторію.

Greenfield redesign не означає втрату корисних артефактів. Переносити
лише перевірені задачі, ADR, контракти, конфігурації та посилання на
commits. Не переносити без перевірки runtime state, старі locks,
credentials та невалідувані AI summaries.

# 4. Цільова архітектура

## 4.1. Стиль

**Модульний моноліт + ізольовані worker processes.**

Основний процес містить API, domain logic, scheduler, knowledge services
та persistence adapters. Агентні процеси запускаються окремо, у власних
worktrees та з обмеженими правами.

Внутрішні модулі взаємодіють через ports/interfaces і події. Мережеві
мікросервіси не є вимогою першої версії.

## 4.2. Логічні компоненти

### Control Plane

Project Registry, Agent Registry, Task Scheduler, Policy Engine, Budget
Manager, Run Lifecycle Manager.

### Semantic Engine

Language Parsers, Symbol Index, Dependency Graph, Architecture
Knowledge, Hybrid Search, Impact Analysis, Context Assembly.

### Coordination Engine

Task Contract Validator, Resource Ownership, Read/Write Locks, Leases,
Fencing Tokens, Conflict Detector, Merge Gate.

### Execution Engine

Worker Supervisor, Git Worktree Manager, Checkpoint Manager, Process
Runner, Sandbox Adapter, Retry and Recovery.

### Intelligence Engine

Provider Registry, Model Router, Complexity/Risk Estimator, Context
Budgeter, Usage Accounting, Cache Manager, Evaluation.

### Agent Gateway

MCP Server, CLI, LLM API adapters, Coding Agent CLI adapters, optional
A2A adapter.

### Persistence

-   Git --- довготривалі артефакти;
-   SQLite --- оперативний стан;
-   FTS5 --- текстовий пошук;
-   локальні індекси --- AST, graph, optional embeddings;
-   filesystem --- worktrees, logs, checkpoints та кеші.

## 4.3. Межі компонентів

Жоден engine не повинен імпортувати внутрішні класи іншого engine.
Взаємодія --- через визначені ports та application services.

-   Semantic Engine надає `ContextProvider` та `ImpactAnalyzer`.
-   Coordination Engine надає `OwnershipService` та `ConflictService`.
-   Execution Engine використовує `GitWorkspacePort` та
    `ProcessRunnerPort`.
-   Intelligence Engine надає `ModelRouter` та `UsageRecorder`.
-   Control Plane оркеструє use cases, але не містить provider-specific
    чи parser-specific логіки.

## 4.4. Структура репозиторію

``` text
cairn/
├── apps/
│   ├── server/
│   └── cli/
├── src/cairn/
│   ├── domain/
│   │   ├── projects/
│   │   ├── agents/
│   │   ├── tasks/
│   │   ├── knowledge/
│   │   ├── contracts/
│   │   ├── ownership/
│   │   └── budgets/
│   ├── application/
│   │   ├── scheduler/
│   │   ├── context/
│   │   ├── coordination/
│   │   ├── execution/
│   │   ├── routing/
│   │   └── reconciliation/
│   ├── ports/
│   ├── engines/
│   │   ├── semantic/
│   │   ├── coordination/
│   │   ├── execution/
│   │   └── intelligence/
│   ├── adapters/
│   │   ├── git/
│   │   ├── storage/
│   │   ├── languages/
│   │   ├── mcp/
│   │   ├── providers/
│   │   ├── coding_agents/
│   │   └── sandbox/
│   ├── api/
│   ├── cli/
│   └── config/
├── schemas/
├── migrations/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── concurrency/
│   ├── recovery/
│   ├── security/
│   └── benchmarks/
├── docs/
│   ├── architecture/
│   ├── adr/
│   ├── protocols/
│   ├── security/
│   ├── operations/
│   └── migration/
├── examples/
├── scripts/
└── pyproject.toml
```

Це цільові межі, а не вимога створити всі каталоги в першому commit.

# 5. Доменна модель та knowledge provenance

## 5.1. Сутності

  Сутність          Призначення
  ----------------- -------------------------------
  Project           Git repository
  ProjectRevision   Commit/revision контексту
  Agent             Логічний виконавець
  AgentProfile      Capabilities та permissions
  Task              Одиниця роботи
  TaskContract      Межі й критерії задачі
  TaskRun           Конкретний запуск
  Checkpoint        Точка відновлення
  Module            Логічна частина коду
  File              Файл
  Symbol            Class, method, function, type
  Contract          API або внутрішній інтерфейс
  Decision          Архітектурне рішення
  Invariant         Обов'язкове правило
  Evidence          Джерело підтвердження
  ChangeSet         Набір змін агента
  ResourceLease     Тимчасове право на ресурс
  ModelInvocation   Виклик моделі
  UsageRecord       Токени, час і вартість
  Event             Незмінна подія

## 5.2. Статуси знань

-   `observed` --- безпосередньо отримано з коду або джерела;
-   `declared` --- задано користувачем чи архітектором;
-   `inferred` --- виведено моделлю/алгоритмом;
-   `verified` --- підтверджено тестом або перевіркою;
-   `deprecated` --- застаріло;
-   `rejected` --- відхилено.

`confidence` не замінює `status` і не є математичною ймовірністю без
калібрування.

## 5.3. Provenance

Кожне важливе знання зберігає: - `source_type`; - repository-relative
path або source URI; - `commit_sha`; - `symbol_id`, якщо застосовно; -
line range або stable locator; - `created_by`; - `created_at`; -
`schema_version`; - `evidence_ids`; - `status`.

Знання без provenance не може бути авторитетним архітектурним фактом.

# 6. Semantic Engine

## 6.1. Призначення

Semantic Engine створює машиночитану модель проєкту: модулі, файли,
символи, залежності, контракти, рішення та інваріанти. Його мета ---
точна навігація, пошук доказів, impact analysis та формування
релевантного контексту, а не абстрактна обіцянка «повністю зрозуміти
код».

## 6.2. Типи вузлів і зв'язків

Вузли: `Project`, `Module`, `Directory`, `File`, `Symbol`, `Contract`,
`Decision`, `Invariant`, `Task`, `ChangeSet`, `Test`,
`ExternalDependency`, `Evidence`, `GitCommit`.

Зв'язки: `contains`, `defines`, `calls`, `imports`, `depends_on`,
`implements`, `extends`, `uses_contract`, `governed_by`,
`constrained_by`, `modifies`, `affects`, `verified_by`, `introduced_by`,
`supersedes`.

Кожне ребро містить source, target, relation, evidence та revision.

## 6.3. Language adapters

Початковий набір: - Python --- стандартний AST; - PHP ---
`nikic/php-parser`; - TypeScript/JavaScript --- TypeScript Compiler API
або Tree-sitter; - інші мови --- Tree-sitter як fallback.

Tree-sitter дає синтаксичну структуру, але не замінює повний type
resolution або language server. Для точного аналізу потрібні
language-specific adapters.

``` python
class LanguageAdapter(Protocol):
    def supports(self, path: str) -> bool: ...
    def parse(self, path: str, content: bytes) -> ParsedFile: ...
    def extract_symbols(self, parsed: ParsedFile) -> list[Symbol]: ...
    def extract_references(self, parsed: ParsedFile) -> list[Reference]: ...
```

## 6.4. Incremental indexing

1.  Отримати Git diff від останньої проіндексованої revision.
2.  Визначити added/modified/deleted/renamed files.
3.  Перепарсити лише змінені файли.
4.  Оновити symbols та references.
5.  Перерахувати залежні графові ребра.
6.  Позначити залежні кеші як stale.
7.  Зберегти index revision.
8.  Записати `index.updated`.

Повний rebuild має залишатися доступним для перевірки узгодженості.

## 6.5. Пошук

Рекомендований порядок: 1. exact symbol/path lookup; 2. graph traversal;
3. SQLite FTS5; 4. Git history/blame; 5. optional embedding similarity;
6. optional reranking.

Embeddings --- додатковий retrieval channel, не джерело істини.
Векторний індекс повинен бути відновлюваним.

## 6.6. Impact analysis

Вхід --- набір змінених symbols/files/contracts. Вихід: - прямі й
обмежені транзитивні залежності; - потенційно порушені контракти; -
пов'язані тести; - архітектурні інваріанти; - рівень ризику; - невідомі
або непроаналізовані залежності.

Неповний граф не може давати статус `safe`; використовувати `unknown`
або `requires_review`.

## 6.7. Context Assembly API

``` python
context = await context_engine.build(
    project_id="project-id",
    task_id="TASK-104",
    agent_id="agent-seo",
    revision="abc123",
    token_budget=12000,
    profile="implementation",
)
```

Приклад відповіді:

``` json
{
  "context_id": "ctx_...",
  "context_version": 1,
  "project_revision": "abc123",
  "task_id": "TASK-104",
  "required_constraints": [],
  "task_contract": {},
  "architecture_facts": [],
  "related_symbols": [],
  "contracts": [],
  "decisions": [],
  "related_changes": [],
  "tests": [],
  "evidence": [],
  "omissions": [],
  "estimated_tokens": 10450
}
```

Context snapshot повинен містити revision, schema version, sources,
required constraints, omissions, token estimate та freshness state.
Агент має знати, які матеріали були пропущені через budget.

Профілі: `planning`, `implementation`, `review`, `debugging`,
`architecture`, `testing`. Профіль змінює ранжування, але не може
прибирати обов'язкові security constraints або task contract.

# 7. Coordination Engine

## 7.1. Task Contract

Кожна задача має формальний контракт до запуску:

``` yaml
schema_version: 1
task_id: TASK-104
title: Implement SEO metadata resolver
base_revision: abc123

scope:
  read_paths:
    - src/Products/Contracts/**
  write_paths:
    - src/SEO/**
    - tests/SEO/**

contracts:
  required:
    - ProductInterface
  proposed_changes: []

constraints:
  - no_public_api_breaking_changes
  - preserve_backward_compatibility

acceptance:
  - resolver_returns_canonical_metadata
  - tests_pass

execution:
  max_agents: 1
  max_retries: 2
  timeout_seconds: 1800

budget:
  max_cost_usd: 0.50

permissions:
  network: deny
  shell_profile: restricted
  secrets: deny
```

Контракт проходить schema validation і policy validation до запуску.

## 7.2. Ownership

Ресурси: file, directory/path pattern, module, symbol, contract,
generated artifact.

Режими: - `read`; - `write`; - `exclusive`; - `contract_change`.

Для path patterns потрібна нормалізація та перевірка перетинів.

## 7.3. Leases та fencing tokens

Lease містить resource ID, owner agent, task/run ID, expiration,
monotonically increasing fencing token і heartbeat timestamp.

Fencing token захищає від процесу, який продовжує роботу після втрати
lease. Кожна контрольована write operation перевіряє чинність token.

**Обмеження:** процес із прямим filesystem access може обійти
application-level lock. Тому справжня ізоляція забезпечується окремим
worktree та OS permissions. Ownership --- координаційний механізм, не
заміна sandbox.

## 7.4. Типи конфліктів

-   **Textual conflict:** Git не може автоматично об'єднати зміни.
-   **Semantic conflict:** різні файли змінюють один контракт, порушують
    інваріант або несумісні залежності.
-   **Stale-base conflict:** агент працював на revision, яка вже
    застаріла.

Кожен ChangeSet повторно аналізується на актуальній цільовій revision
перед merge.

## 7.5. Merge gate

Merge дозволено лише якщо: 1. відома base revision; 2. diff доступний;
3. ownership перевірено; 4. task contract не порушено; 5. потрібні тести
пройшли; 6. contract validation пройшла; 7. security checks пройшли; 8.
немає unresolved critical semantic findings; 9. policy дозволяє
автоматичний merge.

Неповний аналіз означає `requires_review`, а не `safe`.

# 8. Execution Engine

## 8.1. Worker lifecycle

``` text
queued -> preparing -> running -> validating -> completed
running -> waiting -> running
running -> failed -> retry_wait -> preparing
running -> interrupted -> recovery_required -> preparing
running -> cancelled
```

Переходи станів мають бути явними й перевірятися state machine.

## 8.2. Worker Supervisor

Відповідає за process creation, heartbeat, timeout, cancellation,
stdout/stderr capture, exit code, resource limits, checkpoints, retry
policy, cleanup та recovery.

Retry не повинен повторювати side effects без перевірки ідемпотентності.

## 8.3. Git Worktree lifecycle

1.  Визначити base revision.
2.  Створити унікальну branch.
3.  Створити окремий worktree.
4.  Передати агенту дозволений workspace.
5.  Зберігати checkpoints.
6.  Отримати diff.
7.  Запустити перевірки.
8.  Інтегрувати або відхилити зміни.
9.  Очистити worktree за policy.

Branch naming:

``` text
cairn/task/TASK-104/run/RUN-001
```

Не використовувати спільний working directory для паралельних агентів.

## 8.4. Checkpoint

Checkpoint містить task/run ID, state, base commit, worktree reference,
branch, last diff hash, adapter, context ID, lease references, timestamp
і recovery metadata. Не зберігати API keys або секретний environment.

## 8.5. Recovery

Після перезапуску: 1. знайти активні TaskRun; 2. позначити
непідтверджені процеси як interrupted; 3. перевірити worktree та branch;
4. перевірити Git status; 5. перевірити checkpoint; 6. перевірити base
revision; 7. перевірити leases; 8. запропонувати resume, restart або
manual recovery.

Не робити автоматичний resume, якщо безпечність відновлення не доведена.

# 9. Git-native persistence

## 9.1. Принцип

Git --- довготривале джерело істини для затверджених знань та
артефактів. SQLite --- оперативний стан. Пошукові й embedding індекси
--- похідні та відновлювані.

## 9.2. Структура `.cairn/`

``` text
.cairn/
├── manifest.yaml
├── architecture/
│   ├── system.yaml
│   ├── modules/
│   ├── dependencies.yaml
│   └── invariants/
├── contracts/
│   ├── public-api/
│   └── module-interfaces/
├── decisions/
│   ├── ADR-0001.yaml
│   └── ADR-0002.yaml
├── tasks/
│   ├── specifications/
│   └── completed/
├── knowledge/
│   ├── facts/
│   └── references/
├── policies/
│   ├── agents.yaml
│   ├── security.yaml
│   └── budget.yaml
└── schemas/
```

## 9.3. Зберігати в Git

-   project manifest;
-   затверджену архітектуру;
-   ADR;
-   contracts;
-   invariants;
-   task specifications;
-   затверджені knowledge facts;
-   policies;
-   schema versions;
-   посилання на важливі ChangeSets.

Не комітити за замовчуванням: - embeddings; - тимчасові runtime logs; -
API keys; - credentials; - locks та leases; - worker process state; -
тимчасові індекси; - приватні промпти.

## 9.4. Дві історії

**Git history:** code, ADR, contracts, approved knowledge, task
specifications.

**Runtime event history:** agent started, context assembled, lock
acquired, model invoked, usage recorded, checkpoint created, agent
failed, task resumed.

Не потрібно комітити кожну heartbeat-подію. Важливі runtime events
можуть експортуватися як audit artifacts.

## 9.5. Clone and rebuild

``` bash
git clone <repository>
cd <repository>

cairn project init
cairn context rebuild
cairn verify
cairn agents status
```

Відновити затверджену архітектуру, контракти, ADR, інваріанти, task
specs, code graph та пошуковий індекс.

Незавершені задачі отримують `interrupted` і проходять recovery
validation; вони не запускаються автоматично.

## 9.6. Schema evolution

Кожен artifact має `schema_version`. Міграції повинні бути
детермінованими, протестованими, мати backup, не видаляти невідомі поля
без policy та дозволяти перевірку сумісності.

# 10. Universal Agent Gateway

## 10.1. Рівні інтеграції

1.  **MCP:** контекст, пошук, task status та контрольовані tools.
2.  **CLI/Provider API:** пряме виконання моделей або coding-agent CLI.
3.  **A2A:** опційна взаємодія з незалежними агентними системами.

MCP і A2A не взаємозамінні. Внутрішня комунікація Cairn використовує
власні application interfaces та event model.

## 10.2. Канонічні tools

``` text
cairn.project.get_context
cairn.project.get_architecture
cairn.project.search_knowledge

cairn.code.search_symbols
cairn.code.get_dependencies
cairn.code.analyze_impact

cairn.task.get
cairn.task.claim
cairn.task.update
cairn.task.complete

cairn.ownership.acquire
cairn.ownership.release

cairn.contract.get
cairn.contract.propose_change

cairn.git.get_history
cairn.git.get_diff
cairn.git.create_checkpoint

cairn.budget.get_remaining
cairn.budget.report_usage
```

Кожен tool має schema, permission requirement, idempotency behavior,
timeout, audit event та error contract. Не надавати універсальний
`execute_shell` як заміну вузьким інструментам.

## 10.3. Provider adapters

Розділити LLM API adapters, coding-agent adapters і local inference
adapters.

``` text
LLMProvider
  ├── OpenAIAdapter
  ├── AnthropicAdapter
  ├── GeminiAdapter
  ├── OllamaAdapter
  └── OpenAICompatibleAdapter

CodingAgentAdapter
  ├── CodexCLIAdapter
  ├── ClaudeCodeAdapter
  ├── GeminiCLIAdapter
  └── CustomCLIAdapter
```

Не припускати, що API-модель і coding-agent CLI мають однакові
capabilities або lifecycle.

Adapter оголошує streaming, tool calling, structured output,
context/output limits, image support, cancellation, usage reporting,
local/remote execution та authentication mode.

Якщо provider не повертає точні usage/cost дані, Cairn позначає їх
`estimated`, а не `reported`.

# 11. Intelligence Engine та бюджет

## 11.1. Основна метрика

Оптимізувати **cost per successful task**, а не лише cost per token.

Вимірювати: - вартість успішної задачі; - model calls; - retries; -
невдалі зміни; - час до merge; - повторне використання контексту; -
regression rate; - частку локального виконання.

## 11.2. Рівні виконання

  Рівень   Виконавець                  Приклади
  -------- --------------------------- -----------------------------------
  L0       Детерміновані інструменти   AST, grep, Git, formatter, tests
  L1       Мала локальна модель        класифікація, прості summaries
  L2       Економна модель             звичайні зміни
  L3       Потужна модель              архітектура, складний refactoring

У повністю offline режимі L2/L3 можуть бути локальними або недоступними.
Віддалений API --- явна конфігурація, а не прихований fallback.

## 11.3. Complexity та risk

Complexity factors: кількість файлів/symbols, залежних модулів,
невизначеність вимог, обсяг змін і складність тестів.

Risk factors: public API, security-sensitive code, migrations,
auth/payment, contract changes, відсутність тестів та невідомі
залежності.

Не зводити complexity і risk до одного score. Router використовує обидві
оцінки для вибору моделі, паралельності та review policy.

## 11.4. Context budget

Пріоритети: - P0 --- task contract та обов'язкові constraints; - P1 ---
contracts та invariants; - P2 --- змінювані symbols; - P3 --- прямі
залежності; - P4 --- ADR та релевантна Git history; - P5 --- додаткові
пояснення.

Обов'язкові constraints не можна відкидати заради економії токенів. Якщо
вони не поміщаються, задача зупиняється або маршрутизується на модель із
більшим контекстом.

## 11.5. Cache

**Analysis cache:** content hash + parser version + analyzer version.

**Context cache:** task contract hash + project revision + graph
revision + profile + schema version.

**LLM cache:** provider/model/version + prompt hash + context hash +
tool schema hash + parameters + output format.

Не використовувати кеш, якщо залежний контракт або policy змінилися.

## 11.6. Budget policy

``` yaml
schema_version: 1
currency: USD
limits:
  per_task: 0.50
  per_agent: 0.30
  daily_project: 5.00
controls:
  max_retries: 2
  max_parallel_agents: 3
  require_approval_for_escalation: true
  stop_on_budget_exceeded: true
local_models:
  preferred: true
  fallback: null
```

Це демонстраційні значення, не універсальні рекомендації.

Cost accounting має розрізняти `reported`, `estimated` та `unknown`. Не
показувати estimated cost як точну фактичну вартість.

## 11.7. Економіка паралельності

Planner запускає кілька агентів лише якщо задачі незалежні, ownership не
створює надмірного блокування, очікувана користь виправдовує витрати, а
acceptance criteria можна перевірити окремо.

# 12. Рекомендований технологічний стек

  Компонент         Рекомендація                                 Призначення
  ----------------- -------------------------------------------- ---------------------
  Runtime           Python 3.12+                                 Core
  API               FastAPI                                      Local API
  Validation        Pydantic                                     Schemas/contracts
  Operational DB    SQLite + WAL                                 Tasks/events/leases
  Search            SQLite FTS5                                  Full-text
  Versioning        Git                                          Portable history
  Parsing           Native AST + Tree-sitter                     Code analysis
  PHP               nikic/php-parser                             PHP AST
  TypeScript        TypeScript Compiler API                      TS analysis
  Agent interface   MCP SDK                                      Tools/context
  Local inference   Ollama / llama.cpp / vLLM adapters           Local models
  Tests             pytest                                       Automated testing
  Migrations        Alembic або контрольований migration layer   DB schema

Не додавати Redis, Kafka, Kubernetes, Neo4j або окрему vector database
до появи виміряної потреби.

# 13. SQLite operational schema --- початковий варіант

SQLite використовує foreign keys, WAL mode, транзакції та міграції. Не
тримати write transaction відкритою під час LLM invocation.

``` sql
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    root_path TEXT NOT NULL,
    git_remote TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    adapter_type TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    status TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    base_revision TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE task_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    status TEXT NOT NULL,
    worktree_path TEXT,
    branch_name TEXT,
    base_revision TEXT,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(id),
    FOREIGN KEY(agent_id) REFERENCES agents(id)
);

CREATE TABLE events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_events_aggregate
ON events(aggregate_type, aggregate_id, sequence);

CREATE TABLE checkpoints (
    id TEXT PRIMARY KEY,
    task_run_id TEXT NOT NULL,
    revision TEXT,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_run_id) REFERENCES task_runs(id)
);

CREATE TABLE resource_leases (
    resource_id TEXT PRIMARY KEY,
    owner_agent_id TEXT NOT NULL,
    task_run_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL
);

CREATE TABLE model_invocations (
    id TEXT PRIMARY KEY,
    task_run_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    usage_status TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_cost REAL,
    reported_cost REAL,
    duration_ms INTEGER,
    created_at TEXT NOT NULL
);
```

Це початкова схема, не повна фінальна модель. Перед production потрібно
визначити індекси, retention, transaction boundaries, concurrency
behavior та migration tests.

Події append-only. Не використовувати `INSERT OR REPLACE` для event log.
Event sourcing застосовувати для audit/recovery-critical потоків, але не
переводити кожну таблицю в event-sourced модель без потреби.

# 14. Security model

## 14.1. Trust boundaries

1.  Cairn core --- довірена локальна програма.
2.  User-approved configuration --- довірена після перевірки.
3.  Agent process --- недовірений виконавець.
4.  LLM output --- недовірені дані.
5.  Repository content --- недовірений input, включно з prompt injection
    у comments/docs.
6.  External tools --- окремий trust boundary.

## 14.2. Вимоги

-   Не передавати secrets в agent context.
-   Не зберігати credentials у Git.
-   Використовувати OS keychain або локальний secret store.
-   Обмежувати filesystem scope і shell commands.
-   За замовчуванням вимикати network access у sandbox.
-   Відокремлювати agent process від control plane.
-   Вимагати approval для небезпечних операцій.
-   Захищати integration branches.
-   Логувати дозволи й відмови.
-   Перевіряти tool arguments за schema.
-   Обмежувати file size, output та runtime.

## 14.3. Prompt injection

Вміст README, source comments, issues та документації передається як
data/evidence, а не як system policy. Policy Engine є джерелом дозволів.
LLM не може змінити власні permissions через текст репозиторію.

## 14.4. Sandbox

Git Worktree --- ізоляція робочого дерева, але не security sandbox.

Рівні: - базовий: окремий worktree + обмежений OS process/user; -
посилений: filesystem sandbox і network restrictions; - високий:
container або VM з обмеженим workspace та ресурсами.

Реалізувати `SandboxPort` і конкретний adapter для цільової ОС. Не
заявляти гарантії безпеки, якщо існують лише prompt restrictions.

# 15. CLI та API

## 15.1. CLI

``` bash
cairn init
cairn project add <path>
cairn project status

cairn index build
cairn index update
cairn index verify

cairn context show <task-id>
cairn search "<query>"
cairn impact analyze <path-or-symbol>

cairn task create
cairn task plan <task-id>
cairn task run <task-id>
cairn task status <task-id>
cairn task resume <task-id>
cairn task cancel <task-id>

cairn agents list
cairn agents status

cairn git worktrees
cairn git diff <task-id>
cairn verify

cairn budget status
cairn doctor
```

CLI підтримує `--json` та стабільні exit codes.

## 15.2. Local API

Початкові endpoints: - `GET /health`; - `GET /projects`; -
`GET /projects/{id}/context`; - `POST /projects/{id}/index`; -
`POST /tasks`; - `GET /tasks/{id}`; - `POST /tasks/{id}/run`; -
`POST /tasks/{id}/cancel`; - `GET /tasks/{id}/events`; -
`GET /agents`; - `GET /budget`; - `GET /ownership`; -
`POST /contracts/{id}/proposals`.

API за замовчуванням прив'язаний до loopback. Для віддаленого доступу
потрібні authentication, authorization та transport security.

# 16. Observability

Кожен TaskRun має correlation IDs: project ID, task ID, run ID, agent
ID, context ID, provider invocation ID.

Метрики: - task duration; - queue wait; - context assembly duration; -
index update duration; - model latency; - token usage; -
reported/estimated cost; - retry count; - lease contention; - merge
rejection rate; - recovery success rate; - semantic conflict detection
rate; - cache hit rate.

Не записувати secrets, приватні промпти або source code у telemetry без
явної opt-in policy.

# 17. Тестова стратегія

## Unit tests

Domain state transitions, schema validation, budget calculations,
ownership decisions, context ranking, cache keys.

## Integration tests

SQLite transactions, Git operations, worktree lifecycle, MCP calls,
provider contracts, parser adapters, incremental indexing.

## Concurrency tests

-   два агенти претендують на один write resource;
-   різні файли змінюють один контракт;
-   lease expires під час роботи;
-   stale worker намагається записати;
-   base branch змінюється під час TaskRun.

## Recovery tests

-   worker crash;
-   Cairn process crash;
-   machine restart;
-   incomplete checkpoint;
-   corrupted local index;
-   interrupted Git operation.

## Security tests

-   path traversal;
-   symlink escape;
-   command injection;
-   secret exposure;
-   unauthorized tool call;
-   prompt injection у repository content;
-   network access із restricted worker.

## Acceptance matrix

  -----------------------------------------------------------------------
  Сценарій                            Критерій
  ----------------------------------- -----------------------------------
  Clone + rebuild                     Затверджені знання та індекс
                                      відновлюються

  Provider switch                     Не потрібна міграція knowledge

  Parallel editing                    Окремі worktrees та write scopes

  Semantic conflict                   Порушення контракту виявляється або
                                      позначається unknown

  Contract breaking                   Merge gate блокує непогоджену
                                      breaking change

  Worker crash                        Run переходить у recoverable
                                      interrupted state

  Lease expiration                    Застарілий owner не може виконати
                                      контрольований запис

  Budget exceeded                     Нові model calls блокуються policy

  Offline mode                        Core workflows працюють без
                                      зовнішньої мережі

  Secret isolation                    Restricted agent не читає secrets

  Index corruption                    Індекс перебудовується з первинних
                                      джерел

  Schema upgrade                      Міграція зберігає підтримувані
                                      артефакти
  -----------------------------------------------------------------------

## KPI

Цілі приймання: - 0 втрат затверджених знань після clone/rebuild; - 0
несанкціонованих операцій через Cairn tools; - 0 model calls без budget
authorization; - 100% критичних checkpoint recovery tests проходять; -
100% accepted knowledge має provenance; - 100% merge operations
проходять merge gate; - неповні semantic dependencies ніколи не
позначаються як safe.

Це цілі, а не заявлені результати вже виконаних тестів.

# 18. Roadmap

## Phase 0 --- Specification and baseline

Зафіксувати commit Cairn 0.x, завершити аудит, ADR, domain model,
schemas, threat model та test strategy.

**Exit:** узгоджені component boundaries, відомі джерела міграції,
визначені acceptance tests.

## Phase 1 --- Foundation

Package structure, config, CLI, project registry, local Git adapter,
SQLite migrations, append-only events, basic task state machine, schema
validation.

**Exit:** Cairn працює з локальним Git без GitHub/cloud dependency.

## Phase 2 --- Semantic MVP

Language adapter, перший parser, symbol index, dependency graph, FTS5,
incremental indexing, ADR/contract loader, context assembler.

**Exit:** два різні agent clients отримують узгоджені revision-aware
facts; кожен факт має provenance; rebuild дає еквівалентний індекс.

## Phase 3 --- Coordination MVP

Task Contract, worktree manager, ownership, leases, fencing tokens,
ChangeSet, textual conflict check, базовий semantic impact check, merge
gate.

**Exit:** два агенти працюють у різних worktrees; stale base
виявляється; інтеграція відбувається після перевірок.

## Phase 4 --- Agent Gateway

MCP server, read-only context tools, task tools, permission enforcement,
перший coding-agent adapter, provider capability registry.

**Exit:** зовнішній агент отримує контекст без прямого доступу до
внутрішньої БД; права перевіряються на кожному tool call.

## Phase 5 --- Budget Intelligence

Usage recorder, reported/estimated cost, context budget, deterministic
routing, cache, budget guard, local model adapter.

**Exit:** кожен model invocation має budget decision; перевищення ліміту
блокує виклики; offline mode не робить прихованих network calls.

## Phase 6 --- Recovery and security hardening

Worker supervisor, checkpoint/recovery, sandbox adapter, command policy,
secret isolation, crash/concurrency/security tests.

**Exit:** критичні recovery та security tests проходять.

## Phase 7 --- Knowledge feedback loop

ChangeSet-to-knowledge reconciliation, proposed knowledge, evidence
validation, human approval, accepted knowledge commit, stale knowledge
detection.

**Exit:** агентні зміни можуть пропонувати оновлення знань, але
неперевірені висновки не стають авторитетними автоматично.

# 19. Перший Proof of Concept

Рекомендований PoC --- реальний PHP 8.4 CMS або інший структурований
репозиторій.

1.  Cairn індексує модулі та symbols.
2.  Архітектор декларує контракт.
3.  Agent A отримує задачу змінити SEO resolver.
4.  Agent B отримує незалежну задачу в іншому модулі.
5.  Обидва агенти отримують спільний architecture snapshot.
6.  Кожен працює у власному Git Worktree.
7.  Один агент пропонує зміну контракту.
8.  Coordination Engine виявляє перетин із залежним модулем.
9.  Merge gate блокує несумісну зміну.
10. Після виправлення проходять тести.
11. ChangeSet інтегрується.
12. Knowledge Reconciler пропонує оновлення архітектурної пам'яті.
13. Після погодження оновлення потрапляє в Git.

PoC перевіряє не кількість агентів, а спільний контекст, ізоляцію та
коректність інтеграції.

# 20. Початкові ADR

  ADR       Рішення                                  Статус
  --------- ---------------------------------------- ----------
  ADR-001   Modular monolith + isolated workers      Proposed
  ADR-002   Git as durable knowledge source          Proposed
  ADR-003   SQLite as local operational store        Proposed
  ADR-004   Semantic graph + hybrid search           Proposed
  ADR-005   MCP as agent context interface           Proposed
  ADR-006   Git Worktree per TaskRun                 Proposed
  ADR-007   Deterministic ownership and merge gate   Proposed
  ADR-008   Provider-independent interfaces          Proposed
  ADR-009   Local-first and offline-capable          Proposed
  ADR-010   Cost-aware orchestration                 Proposed
  ADR-011   Evidence-backed knowledge                Proposed
  ADR-012   Sandbox separate from worktree           Proposed

ADR повинні бути окремими файлами з контекстом, рішенням,
альтернативами, наслідками та статусом. Таблиця вище --- індекс рішень.

# 21. Нефункціональні вимоги

### Portability

-   У committed artifacts немає залежності від абсолютних шляхів.
-   Індекси відновлюються з Git.
-   Schema versioning.
-   Provider-neutral knowledge format.

### Reliability

-   Транзакційне збереження критичних подій.
-   Recovery після process crash.
-   Ідемпотентність там, де це можливо.
-   Явні interrupted states.

### Performance

-   Incremental indexing.
-   Context caching.
-   Обмежений graph traversal.
-   Відкладене embedding generation.
-   Повний re-index лише за потреби.

### Security

-   Least privilege.
-   Explicit permissions.
-   Sandbox.
-   Secret isolation.
-   Local API auth поза loopback.
-   Audit trail.

### Maintainability

-   Типізовані interfaces.
-   Schema validation.
-   Contract tests.
-   ADR.
-   Мінімальні міжмодульні залежності.

# 22. Відкриті питання

Потрібно визначити до production implementation: 1. Підтримувані ОС:
Windows, Linux, macOS? 2. Перший language adapter? 3. Чи потрібен UI у
першому релізі, чи достатньо CLI + MCP? 4. Які coding-agent CLI доступні
в цільовому середовищі? 5. Який sandbox підтримуватиме кожна ОС? 6. Який
обсяг репозиторіїв --- ціль першого benchmark? 7. Чи потрібна multi-user
робота на одному локальному сервері? 8. Який retention policy для
runtime events? 9. Чи дозволяється opt-in віддалений LLM API? 10. Які
acceptance tasks порівнюватимуть Cairn 2.0 з Cairn 0.x?

Не блокувати Foundation через необов'язкові UI або distributed
deployment рішення.

# 23. Перший вертикальний зріз

Перший реалізаційний шлях має пройти повний цикл:

``` text
Local Git Repository
        |
        v
Project Manifest
        |
        v
Semantic Index
        |
        v
Context Assembly
        |
        v
Task Contract
        |
        v
One Agent in Worktree
        |
        v
Diff + Tests + Impact Analysis
        |
        v
Merge Gate
        |
        v
Approved Knowledge Update
        |
        v
Git Commit
```

Спочатку стабілізувати цей цикл для одного агента, потім додати другого
та перевірити координацію. Це зменшує кількість одночасних невідомих і
вартість налагодження.

# 24. Definition of Done для MVP

-   [ ] Cairn запускається локально без cloud dependency.
-   [ ] Проєкт реєструється через CLI.
-   [ ] Git repository аналізується інкрементально.
-   [ ] Semantic facts мають provenance.
-   [ ] ADR, contracts та invariants зберігаються в `.cairn/`.
-   [ ] Context API повертає revision-aware snapshot.
-   [ ] MCP client може читати контекст.
-   [ ] Task Contract перевіряється до запуску.
-   [ ] TaskRun працює в окремому Git Worktree.
-   [ ] Ownership та lease працюють.
-   [ ] Зміни проходять validation gate.
-   [ ] Runtime events зберігаються append-only.
-   [ ] Model usage та budget контролюються.
-   [ ] Index можна повністю перебудувати.
-   [ ] Clone/rebuild зберігає затверджені знання.
-   [ ] Worker crash переводить задачу в recovery state.
-   [ ] Concurrency, recovery та security tests проходять.

# 25. Технічні джерела

-   Git Worktree: https://git-scm.com/docs/git-worktree
-   Git hooks: https://git-scm.com/docs/githooks
-   Model Context Protocol: https://modelcontextprotocol.io/
-   Agent2Agent Protocol: https://a2a-protocol.org/
-   Tree-sitter: https://tree-sitter.github.io/tree-sitter/
-   SQLite: https://www.sqlite.org/docs.html
-   SQLite FTS5: https://www.sqlite.org/fts5.html
-   FastAPI: https://fastapi.tiangolo.com/
-   Pydantic: https://docs.pydantic.dev/
-   Ollama: https://ollama.com/
-   llama.cpp: https://github.com/ggml-org/llama.cpp
-   vLLM: https://docs.vllm.ai/
-   PHP Parser: https://github.com/nikic/PHP-Parser
-   TypeScript Compiler API:
    https://github.com/microsoft/TypeScript/wiki/Using-the-Compiler-API

Перед реалізацією звіряти актуальні версії специфікацій та SDK.
Внутрішні domain contracts не повинні залежати від нестабільних API
конкретного SDK.

------------------------------------------------------------------------

# Фінальне архітектурне правило

Cairn 2.0 будується навколо Semantic Intelligence, Coordination,
Execution та Cost Intelligence. Git зберігає довготривалу історію;
SQLite керує оперативним станом; MCP відкриває контрольований доступ
агентам; provider adapters забезпечують взаємозамінність моделей.

**Жоден агент не повинен бути єдиним носієм знань про проєкт, а жодна
критична гарантія системи не повинна залежати лише від поведінки
AI-моделі.**
