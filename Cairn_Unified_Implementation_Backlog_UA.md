# CAIRN UNIFIED 2.0
## Implementation Backlog та зафіксований перший Vertical Slice

**Версія:** 1.0  
**Дата:** 19 листопада 2026  
**Статус:** Ready for technical planning; implementation not started  
**Базова специфікація:** `Cairn_Unified_Technical_Specification_UA.md`  
**Призначення:** перетворити архітектуру Cairn Unified на впорядкований, перевірюваний план розробки та зафіксувати перший наскрізний сценарій до написання production-коду.

---

# 0. Рішення, які фіксуємо до початку реалізації

Цей backlog є похідним від архітектурної специфікації Cairn Unified 2.0. Він не змінює її концепцію, але переводить її в конкретні інкременти, залежності, межі та критерії приймання.

## 0.1 Продуктове рішення

Cairn Unified — local-first semantic operating layer для людей і coding agents. Система пов'язує:

1. **Work** — намір, задачі, залежності, готовність до виконання.
2. **Semantics** — значення об'єктів, твердження, контекст, відносини та часову чинність.
3. **Execution** — агенти, сесії, workflow runs, claims, leases, worktrees.
4. **Evidence** — джерела, події, commits, diffs, тести, review та approvals.
5. **Context** — мінімальний перевірюваний пакет, потрібний виконавцю для конкретної роботи.

## 0.2 Архітектурні рішення, які не переглядаємо в межах першого slice

| Рішення | Зафіксоване значення |
|---|---|
| Архітектура | Greenfield modular monolith із чіткими bounded contexts |
| Core runtime | Python 3.12+ |
| Workbench | TypeScript UI; у першому slice — мінімальний локальний екран або CLI-first |
| Operational store | SQLite WAL |
| Search | SQLite FTS5; embeddings — пізніше й опційно |
| Versioned project state | Git |
| Event interchange | Versioned JSONL envelopes |
| Code indexing | Language adapters; перший reference adapter — PHP 8.4 |
| Agent interface | MCP + CLI; provider adapters через окремий host contract |
| Deployment | Local-first, self-hosted, offline-capable |
| Cloud providers | Opt-in; ніякого прихованого fallback |
| Federation | Не входить у перший slice |
| Security | Worktree ≠ sandbox; OS/container isolation потрібна окремо |

## 0.3 Source-of-truth boundaries

- Git є джерелом істини щодо коду, commit, branch та diff.
- Cairn Event Ledger є джерелом істини щодо прийнятих доменних подій.
- Semantic Assertion є твердженням із provenance та життєвим циклом, а не довільним текстом.
- Workflow Run є джерелом істини щодо стану виконання конкретного процесу.
- Provider host є джерелом істини щодо нативної сесії лише тоді, коли адаптер може це підтвердити.
- SQLite projections та індекси мають відновлюватися з durable records.
- LLM output не стає authoritative fact без policy-controlled promotion.

## 0.4 Принципи виконання

1. Спочатку contracts і invariants, потім реалізація.
2. Один bounded context — один власник доменних правил.
3. Події append-only; поточний стан — projection.
4. Кожна команда, що змінює стан, має idempotency semantics.
5. Усі claims/leases мають fencing token.
6. Усі контекстні твердження мають provenance, trust class та freshness.
7. Відносини, що блокують роботу, відокремлені від інформаційних зв'язків.
8. Жоден LLM не може сам собі видати human authority.
9. Усі витрати позначаються як `reported`, `estimated` або `unknown`.
10. Кожен етап має демонструвати працюючу наскрізну поведінку, а не лише таблиці та API.

---

# 1. Структура backlog

## 1.1 Пріоритети

- **P0 — Slice blocker:** без цього перший vertical slice не працює або не є безпечним.
- **P1 — MVP:** потрібне для першого придатного до щоденного використання локального релізу.
- **P2 — Post-MVP:** важлива можливість, але не повинна затримувати перевірку ядра.
- **P3 — Later / research:** потрібна лише після вимірювань або появи конкретного сценарію.

## 1.2 Розмір задач

- **XS:** до половини дня.
- **S:** 0.5–1.5 дня.
- **M:** 2–4 дні.
- **L:** 5–8 днів.
- **XL:** більше 8 днів; треба декомпозувати перед виконанням.

Оцінки — попередні engineering estimates для одного досвідченого розробника, не календарні обіцянки. Вони не включають невідомі витрати на інтеграцію конкретних coding-agent CLI.

## 1.3 Епіки

| Epic | Назва | Пріоритет | Результат |
|---|---|---:|---|
| E00 | Architecture & Contracts | P0 | Зафіксовані доменні контракти, ADR, межі модулів |
| E01 | Repository & Runtime Foundation | P0 | Запуск daemon/CLI, конфігурація, міграції, логування |
| E02 | Event Ledger & Object Identity | P0 | Append-only events, стабільні IDs, replay |
| E03 | Semantic Object Kernel | P0 | Semantic Envelope, Assertions, typed relations, lifecycle |
| E04 | Work Graph Kernel | P0 | WorkItem, dependencies, ready frontier, claim/lease |
| E05 | Code Semantic Adapter | P0 | PHP 8.4 symbols, source anchors, revision-aware indexing |
| E06 | Context Compiler | P0 | Відтворюваний контекстний пакет з доказами |
| E07 | Execution & Git Isolation | P0 | Worker run, worktree, patch, test, commit |
| E08 | Evidence & Decision Lifecycle | P0 | Commit/test evidence, accept/revoke/supersede |
| E09 | Agent Gateway & MCP | P1 | Підключення зовнішніх агентів через стабільний контракт |
| E10 | Workflow Runtime & Gates | P1 | Версійовані workflow, retries, gates, resume |
| E11 | Workbench | P1 | Людський контроль, пояснюваність, спостереження |
| E12 | Security & Policy | P0/P1 | Permission boundaries, secret redaction, trust policy |
| E13 | Observability & Budget | P1 | Metrics, tracing, cost attribution and enforcement |
| E14 | Federation & Multi-Project | P2 | Replica model, sync, remote dependencies |
| E15 | Advanced Intelligence | P2/P3 | Embeddings, ranking learning, automation, advanced planning |

---

# 2. Перший Vertical Slice — зафіксований сценарій

## 2.1 Назва

**VS-01: Decision-to-Code-to-Evidence-to-Next-Session**

Це найменший наскрізний сценарій, який перевіряє головну відмінність Cairn: система повинна перенести не лише текстову пам'ять, а перевірювану причину зміни конкретного символу коду з однієї сесії в іншу.

## 2.2 Гіпотеза, яку перевіряємо

> Якщо робочий елемент пов'язаний із чинним архітектурним рішенням, конкретним символом коду та доказами, Cairn може автоматично зібрати для іншого виконавця компактний і revision-aware контекст, без ручного перечитування всього transcript або історії Git.

## 2.3 Фіксований тестовий репозиторій

Створити окремий fixture repository, не використовувати production repository на першому етапі.

```text
fixtures/
  php84-demo/
    composer.json
    src/
      Pricing/
        PriceCalculator.php
    tests/
      Pricing/
        PriceCalculatorTest.php
    README.md
```

Мова першого reference adapter — PHP 8.4. Для AST використовувати `nikic/php-parser` через окремий PHP helper або обґрунтований Tree-sitter adapter. Вибір фіксується ADR після короткого spike. Core Cairn залишається Python; parser не повинен переносити доменну логіку в helper.

## 2.4 Доменна ситуація

У fixture є клас `PriceCalculator` із методом `calculateTotal()`.

Створити durable decision:

- ID: `DEC-PRICE-001`
- Statement: «Проміжні грошові обчислення виконуються з decimal-safe представленням; binary floating-point не використовується для фінальної суми».
- Scope: `php84-demo/src/Pricing/PriceCalculator.php::PriceCalculator::calculateTotal`
- Status: `accepted`
- Authority: human approval у тестовому seed data
- Evidence: посилання на вихідний контракт/тестову вимогу
- Validity: current для поточного fixture baseline
- Trust: `human_authoritative`

Створити WorkItem:

- ID: генерується системою, стабільний після export/import.
- Title: «Забезпечити decimal-safe підрахунок підсумкової ціни».
- Type: `task`
- Status: `open`
- Priority: `P1`
- Relation: `implements` / `governed_by` до рішення та `targets_symbol` до методу.
- Acceptance criteria:
  1. Метод не використовує binary float для фінальної грошової суми.
  2. Вхідні/граничні випадки покриті тестами.
  3. Наявні test evidence і commit evidence.
  4. WorkItem не закривається лише на підставі повідомлення агента.

Це тестовий сценарій, а не рекомендація щодо конкретної фінансової реалізації production-системи.

## 2.5 Актори та сесії

- **Human operator** — створює/підтверджує decision та WorkItem.
- **Session A** — реєструє рішення/контекст або створює WorkItem.
- **Worker A** — забирає задачу, отримує Context Package, працює в Git worktree.
- **Session B / Worker B** — після завершення першої сесії відновлює контекст без transcript Session A.
- **Cairn Core** — фіксує події, семантичні зв'язки, leases, evidence та freshness.

На першому slice допускається deterministic test worker замість реального LLM. Це навмисно: ми перевіряємо контракти Cairn, а не мінливу якість моделі. Підключення Claude/Codex/Gemini — окремий наступний етап.

## 2.6 Послідовність сценарію

1. `cairn init` ініціалізує workspace і project manifest.
2. `cairn repo add` реєструє fixture Git repository.
3. Indexer знаходить файл, клас і метод; створює symbol identity та revision binding.
4. Людина або seed command створює accepted Decision із provenance.
5. Створюється WorkItem і typed relations до Decision та Symbol.
6. Work Eligibility Engine обчислює задачу як `eligible` і пояснює чому.
7. Worker A атомарно claim-ить WorkItem і отримує lease із fencing token.
8. Context Compiler формує пакет:
   - WorkItem і acceptance criteria;
   - актуальне Decision;
   - target Symbol та його source revision;
   - пов'язані tests/contracts;
   - relevant evidence;
   - trust/freshness labels;
   - explicit constraints;
   - budget/size estimate.
9. Worker A відкриває ізольований Git worktree та виконує зміну.
10. Cairn фіксує patch hash, changed symbols, test command, exit code та test output digest.
11. Merge/acceptance gate перевіряє:
    - lease/fencing token актуальний;
    - base revision не застарів;
    - acceptance criteria виконані;
    - tests успішні;
    - зміна не суперечить active Decision;
    - evidence пов'язане з поточним commit.
12. Worker створює commit у власному worktree.
13. Cairn записує Commit Evidence та переводить WorkItem у `review` або `done` лише за визначеною policy.
14. Session A завершується. Її transcript не передається Session B.
15. Session B запитує контекст для тієї самої задачі/символу.
16. Context Compiler повертає чинне Decision, останню реалізацію, test evidence, commit provenance та попередні обмеження.
17. Перевірити історичний запит `as_of` до зміни та поточний запит після зміни.
18. Створити тестову подію `DecisionSuperseded` або `DecisionRevoked`; наступний Context Package більше не подає старе рішення як чинну інструкцію.
19. Rebuild SQLite projections із durable events; перевірити, що об'єкти, зв'язки та контекст відновлюються.

## 2.7 Умови успіху Vertical Slice

VS-01 прийнятий, якщо всі критерії виконані:

- [ ] Задача, рішення, символ, сесія, commit і test evidence мають стабільні ID та provenance.
- [ ] Context Package можна відтворити з тієї самої ревізії та того самого набору фактів.
- [ ] Session B отримує необхідну інформацію без transcript Session A.
- [ ] Контекст містить джерела, revision, trust class та freshness для кожного важливого твердження.
- [ ] Скасоване або superseded рішення не потрапляє в active instruction set.
- [ ] Старе рішення доступне в історичному `as_of` query.
- [ ] WorkItem не може бути claim-нутий двома worker-ами одночасно в межах одного authority store.
- [ ] Stale worker із простроченим fencing token не може завершити commit/acceptance operation.
- [ ] Зміна коду виконується в окремому worktree.
- [ ] Test evidence прив'язане до точного commit/tree hash.
- [ ] Після видалення SQLite projection система відновлює потрібний стан.
- [ ] Повторне виконання idempotent command не створює дублікати.
- [ ] Core працює offline; жоден cloud API не викликається.
- [ ] Усі перевірки виконуються автоматично в CI.

## 2.8 Що навмисно не входить у VS-01

- Реальні LLM provider calls.
- Повна підтримка Claude Code/Codex/Cursor/Gemini.
- Multi-host federation.
- Distributed consensus.
- Embeddings/vector database.
- Автоматичне навчання ranking model.
- Повноцінний workflow DSL.
- Повний visual graph editor.
- Автоматичне production deployment.
- Загальний sandbox для довільного коду.

Ці функції не потрібні, щоб перевірити головну архітектурну гіпотезу.

---

# 3. Implementation Backlog — детальні задачі

## E00 — Architecture & Contracts

| ID | Задача | Prio | Size | Depends on | Критерій приймання |
|---|---|---:|---:|---|---|
| ARC-001 | Зафіксувати ADR-001: modular monolith + bounded contexts | P0 | S | — | ADR містить rationale, alternatives, consequences |
| ARC-002 | Зафіксувати ADR-002: event ledger + projections + Git boundary | P0 | M | ARC-001 | Визначено canonical event, replay, snapshot, retention |
| ARC-003 | Зафіксувати ADR-003: SQLite schema/migration policy | P0 | S | ARC-001 | Міграції versioned, forward-only, tested |
| ARC-004 | Зафіксувати ADR-004: Semantic Envelope/Assertion model | P0 | M | ARC-002 | JSON Schema/Pydantic contracts і приклади |
| ARC-005 | Зафіксувати ADR-005: PHP parser adapter choice | P0 | S | — | Benchmark spike порівнює parser options на fixture |
| ARC-006 | Зафіксувати ADR-006: Git worktree execution boundary | P0 | S | ARC-001 | Описано lifecycle, cleanup, stale base handling |
| ARC-007 | Створити glossary і relation registry | P0 | S | ARC-004 | Типи об'єктів/зв'язків мають definition та execution effect |
| ARC-008 | Створити threat model для VS-01 | P0 | M | ARC-001 | Trust boundaries, secrets, untrusted agent output, abuse cases |

**Результат E00:** архітектурні суперечки, які впливають на схему та API, вирішені до імплементації.

## E01 — Repository & Runtime Foundation

| ID | Задача | Prio | Size | Depends on | Критерій приймання |
|---|---|---:|---:|---|---|
| FND-001 | Створити monorepo skeleton | P0 | S | ARC-001 | Core, CLI, adapters, tests, docs розділені |
| FND-002 | Налаштувати Python package та lockfile | P0 | S | FND-001 | Reproducible install у чистому середовищі |
| FND-003 | Реалізувати `cairn init` | P0 | M | FND-002 | Ідемпотентна ініціалізація `.cairn/` |
| FND-004 | Реалізувати config loader із precedence | P0 | M | FND-002 | CLI > env > local config > project config > defaults; джерело значення пояснюється |
| FND-005 | Реалізувати structured logging і correlation IDs | P0 | S | FND-002 | Усі command/run logs мають correlation ID |
| FND-006 | Реалізувати SQLite connection manager | P0 | S | FND-002 | WAL, foreign keys, busy timeout, transaction boundaries |
| FND-007 | Реалізувати migration runner | P0 | M | FND-006 | Upgrade з нуля та попередньої версії тестується |
| FND-008 | Реалізувати `cairn doctor` | P1 | M | FND-003 | Перевіряє Git, DB, config, parser, permissions |
| FND-009 | Створити fixture repo php84-demo | P0 | S | — | Git repo із source, tests і deterministic baseline |

## E02 — Event Ledger & Object Identity

| ID | Задача | Prio | Size | Depends on | Критерій приймання |
|---|---|---:|---:|---|---|
| EVT-001 | Визначити Event Envelope v1 | P0 | M | ARC-002 | event_id, type, schema_version, actor, scope, timestamp, causation/correlation, payload, integrity hash |
| EVT-002 | Реалізувати append-only event store | P0 | M | EVT-001, FND-006 | Події не редагуються; corrections — новими подіями |
| EVT-003 | Реалізувати idempotency keys | P0 | S | EVT-002 | Повторна команда не створює дубль side effect |
| EVT-004 | Реалізувати UUIDv7/ULID identity service | P0 | S | FND-002 | Стабільна генерація та перевірка collision |
| EVT-005 | Реалізувати projection registry | P0 | M | EVT-002 | Проєкції мають version і checkpoint |
| EVT-006 | Реалізувати event replay/rebuild | P0 | M | EVT-005 | DB projections видаляються й перебудовуються з ledger |
| EVT-007 | Реалізувати export/import JSONL | P1 | M | EVT-002 | Round-trip зберігає IDs, order, provenance та hashes |
| EVT-008 | Реалізувати event integrity verification | P1 | M | EVT-002 | Виявляє зміну/пропуск записів |

## E03 — Semantic Object Kernel

| ID | Задача | Prio | Size | Depends on | Критерій приймання |
|---|---|---:|---:|---|---|
| SEM-001 | Реалізувати Semantic Envelope schema | P0 | M | ARC-004, EVT-001 | Object ID/type/scope/revision/lifecycle/access policy |
| SEM-002 | Реалізувати Semantic Assertion schema | P0 | M | SEM-001 | Subject-predicate-object, polarity, modality, provenance, trust, validity |
| SEM-003 | Реалізувати typed relation registry | P0 | M | ARC-007 | Relation має direction, cardinality, execution effect та validation |
| SEM-004 | Реалізувати object lifecycle | P0 | M | SEM-001, EVT-002 | proposed/accepted/rejected/superseded/revoked/stale |
| SEM-005 | Реалізувати bi-temporal validity | P0 | M | SEM-002 | valid time + transaction time, as_of/known_at |
| SEM-006 | Реалізувати provenance references | P0 | M | EVT-001 | Assertion посилається на session/source/commit/test/human approval |
| SEM-007 | Реалізувати trust policy v1 | P0 | M | SEM-002, ARC-008 | agent_proposed не може автоматично стати authoritative |
| SEM-008 | Реалізувати semantic query API | P0 | M | SEM-001..007 | Фільтри scope, lifecycle, trust, validity, revision |
| SEM-009 | Реалізувати supersede/revoke commands | P0 | M | SEM-004, SEM-005 | Історія збережена; current view не показує revoked як active |
| SEM-010 | Реалізувати semantic object serialization | P1 | S | SEM-001 | Canonical JSON; round-trip без втрат |

## E04 — Work Graph Kernel

| ID | Задача | Prio | Size | Depends on | Критерій приймання |
|---|---|---:|---:|---|---|
| WRK-001 | Реалізувати WorkItem schema | P0 | M | SEM-001, EVT-002 | type/status/priority/title/description/labels/acceptance/scope |
| WRK-002 | Реалізувати status transition policy | P0 | M | WRK-001 | Невалідні переходи відхиляються з поясненням |
| WRK-003 | Реалізувати typed work dependencies | P0 | M | SEM-003, WRK-001 | blocks, parent_of, relates_to, discovered_from, duplicates, supersedes |
| WRK-004 | Реалізувати cycle detection | P0 | S | WRK-003 | Блокувальний цикл відхиляється; knowledge cycle дозволений за policy |
| WRK-005 | Реалізувати ready frontier engine | P0 | L | WRK-002, WRK-003 | Повертає eligible/blocked/deferred/conflicted + причини |
| WRK-006 | Реалізувати assignment/claim | P0 | M | WRK-005 | Atomic claim під транзакцією |
| WRK-007 | Реалізувати leases + heartbeat | P0 | M | WRK-006 | Expiry/reclaim, owner, TTL, heartbeat |
| WRK-008 | Реалізувати fencing tokens | P0 | M | WRK-007 | Stale worker не може змінювати protected state |
| WRK-009 | Реалізувати handoff packet | P1 | M | WRK-006, SEM-008 | Передача work + context + outstanding risks |
| WRK-010 | Реалізувати merge slots | P1 | M | WRK-007 | Серіалізація інтеграції для конфліктного ресурсу |
| WRK-011 | Реалізувати WorkItem CLI | P0 | M | WRK-001..008 | create/show/list/ready/claim/release/close |

## E05 — Code Semantic Adapter

| ID | Задача | Prio | Size | Depends on | Критерій приймання |
|---|---|---:|---:|---|---|
| CODE-001 | Визначити repository identity і revision model | P0 | M | FND-009, ARC-005 | repo identity не залежить лише від path |
| CODE-002 | Реалізувати Git adapter | P0 | M | CODE-001 | HEAD, branches, commit, diff, worktree operations |
| CODE-003 | Реалізувати PHP 8.4 parser spike | P0 | M | ARC-005, FND-009 | AST для класів, методів, calls, namespaces |
| CODE-004 | Реалізувати Symbol identity v1 | P0 | M | CODE-003 | Stable qualified identity + structural fingerprint |
| CODE-005 | Реалізувати symbol lineage | P1 | L | CODE-004 | rename/move mapping; ambiguous mapping потребує review |
| CODE-006 | Реалізувати code index projection | P0 | M | EVT-005, CODE-004 | File/symbol graph keyed by repo revision |
| CODE-007 | Реалізувати incremental invalidation | P1 | M | CODE-006 | Changed files re-indexed; unaffected symbols reused |
| CODE-008 | Реалізувати symbol locator CLI/API | P0 | M | CODE-006 | `symbol://` resolves to source range and revision |
| CODE-009 | Реалізувати test-to-symbol mapping | P1 | M | CODE-006 | Test evidence links to symbol(s) under test |

## E06 — Context Compiler

| ID | Задача | Prio | Size | Depends on | Критерій приймання |
|---|---|---:|---:|---|---|
| CTX-001 | Визначити Context Package schema | P0 | M | SEM-008, WRK-001 | Package має revision, items, provenance, trust, freshness, budget |
| CTX-002 | Реалізувати deterministic retrieval v1 | P0 | M | SEM-008, CODE-006 | Target work/symbol → explicit graph traversal |
| CTX-003 | Реалізувати freshness evaluation | P0 | M | SEM-005, CODE-006 | stale/superseded/invalidated не видається за current |
| CTX-004 | Реалізувати evidence-aware ranking v1 | P0 | M | CTX-002, SEM-007 | Trust/freshness/scope/revision впливають на ranking |
| CTX-005 | Реалізувати context budget estimator | P1 | M | CTX-001 | Token/byte estimate із uncertainty label |
| CTX-006 | Реалізувати context package explain | P0 | S | CTX-001..004 | Для кожного item показано чому він включений |
| CTX-007 | Реалізувати context snapshot/cache | P1 | M | CTX-001 | Cache key включає project revision + policy version |
| CTX-008 | Реалізувати `cairn context build/show` | P0 | M | CTX-001..006 | Пакет можна зібрати CLI та зберегти fixture snapshot |

## E07 — Execution & Git Isolation

| ID | Задача | Prio | Size | Depends on | Критерій приймання |
|---|---|---:|---:|---|---|
| EXE-001 | Визначити Worker Contract | P0 | M | ARC-006 | input/output, cancellation, timeout, exit states |
| EXE-002 | Реалізувати deterministic fixture worker | P0 | M | EXE-001, CTX-008 | Виконує заздалегідь визначений patch plan без LLM |
| EXE-003 | Реалізувати worktree manager | P0 | M | CODE-002 | Create/list/cleanup, branch naming, recovery |
| EXE-004 | Реалізувати run/checkpoint lifecycle | P0 | M | EVT-002, EXE-001 | queued/running/paused/failed/completed/recovered |
| EXE-005 | Реалізувати patch capture | P0 | S | EXE-003 | Diff hash, changed paths, base/head revision |
| EXE-006 | Реалізувати test runner adapter | P0 | M | EXE-003 | Command allowlist, timeout, output digest, exit code |
| EXE-007 | Реалізувати stale-base guard | P0 | M | WRK-008, EXE-003 | Забороняє acceptance на застарілій базі без rebase/revalidation |
| EXE-008 | Реалізувати commit creation flow | P0 | M | EXE-005, EXE-006, EXE-007 | Commit прив'язаний до run, work item і evidence |
| EXE-009 | Реалізувати worker cancellation/recovery | P1 | M | EXE-004 | Після restart можна визначити стан і безпечно відновити/зупинити |
| EXE-010 | Реалізувати cleanup/reaper | P1 | S | EXE-003, WRK-007 | Не видаляє активний worktree; прибирає orphaned resources |

## E08 — Evidence & Decision Lifecycle

| ID | Задача | Prio | Size | Depends on | Критерій приймання |
|---|---|---:|---:|---|---|
| EVD-001 | Реалізувати Evidence schema | P0 | M | SEM-001, EVT-001 | kind/source/hash/revision/actor/timestamp |
| EVD-002 | Реалізувати Git commit evidence | P0 | S | CODE-002, EVD-001 | Commit SHA/tree hash verified locally |
| EVD-003 | Реалізувати test evidence | P0 | M | EXE-006, EVD-001 | Exact command, environment fingerprint, exit code, output digest |
| EVD-004 | Реалізувати review/approval evidence | P1 | M | SEM-007, EVD-001 | Reviewer identity, scope, decision, timestamp |
| EVD-005 | Реалізувати acceptance evaluator | P0 | M | WRK-001, EVD-002, EVD-003 | WorkItem close policy перевіряє всі required evidence |
| EVD-006 | Реалізувати Decision seed/import command | P0 | S | SEM-009 | VS-01 decision можна створити з provenance |
| EVD-007 | Реалізувати decision audit timeline | P1 | M | SEM-004, EVD-001 | accepted/superseded/revoked історія видима |
| EVD-008 | Реалізувати evidence integrity verification | P1 | M | EVD-001 | Hash/revision mismatch виявляється |

## E09 — Agent Gateway & MCP

| ID | Задача | Prio | Size | Depends on | Критерій приймання |
|---|---|---:|---:|---|---|
| AGT-001 | Визначити Agent Host Contract | P1 | M | E00 | Capabilities, session IDs, hooks, cancellation, usage |
| AGT-002 | Реалізувати generic CLI adapter | P1 | M | AGT-001 | Запускає сумісний локальний CLI за allowlist |
| AGT-003 | Реалізувати session ingestion API | P1 | M | EVT-002, SEM-006 | Події сесії зберігаються з source/provenance |
| AGT-004 | Реалізувати hook event normalizer | P1 | M | AGT-003 | Provider hooks приводяться до canonical envelope |
| AGT-005 | Реалізувати MCP server v1 | P1 | L | WRK-011, CTX-008, AGT-001 | list_ready, claim, context, report_evidence, handoff |
| AGT-006 | Реалізувати provider adapter #1 | P1 | L | AGT-002..005 | Один реальний agent host проходить contract tests |
| AGT-007 | Реалізувати session recovery packet | P1 | M | AGT-003, CTX-008 | Новий session отримує verified task context без повного transcript |
| AGT-008 | Реалізувати raw transcript retention policy | P1 | M | AGT-003, ARC-008 | Redaction, retention, opt-in, no secret leakage |

## E10 — Workflow Runtime & Gates

| ID | Задача | Prio | Size | Depends on | Критерій приймання |
|---|---|---:|---:|---|---|
| WFL-001 | Визначити Workflow Definition schema | P1 | M | WRK-003 | Versioned DSL contract |
| WFL-002 | Реалізувати workflow validator/compiler | P1 | L | WFL-001 | Виявляє cycle, invalid refs, invalid conditions |
| WFL-003 | Реалізувати workflow run engine | P1 | L | WFL-002, WRK-005 | DAG execution, step states, checkpoint |
| WFL-004 | Реалізувати retries/idempotency | P1 | M | WFL-003, EVT-003 | Retry не дублює external side effects |
| WFL-005 | Реалізувати fan-out/fan-in | P1 | M | WFL-003 | Dynamic children і wait-for aggregation |
| WFL-006 | Реалізувати gates | P1 | L | WFL-003, EVD-001 | Human/timer/CI/Git gates із evidence |
| WFL-007 | Реалізувати ephemeral run lifecycle | P2 | M | WFL-003 | Retention/promotion/purge policy |
| WFL-008 | Реалізувати compensation actions | P2 | L | WFL-003 | Saga-like recovery із явними compensation steps |

## E11 — Workbench

| ID | Задача | Prio | Size | Depends on | Критерій приймання |
|---|---|---:|---:|---|---|
| UI-001 | Визначити information architecture | P1 | S | E00 | Projects, Work, Runs, Knowledge, Evidence, Settings |
| UI-002 | Реалізувати local app shell | P1 | M | UI-001 | Local-only auth/session boundary |
| UI-003 | Реалізувати Work queue view | P1 | M | WRK-011 | Ready/blocked/deferred з причинами |
| UI-004 | Реалізувати WorkItem detail | P1 | M | WRK-001, EVD-001 | Relations, acceptance, claim, evidence timeline |
| UI-005 | Реалізувати Semantic object detail | P1 | M | SEM-008 | Assertions, provenance, validity, trust, supersession |
| UI-006 | Реалізувати Session/Run monitor | P1 | M | EXE-004, AGT-003 | State, logs, artifacts, pause/cancel |
| UI-007 | Реалізувати approval inbox | P1 | M | WFL-006, EVD-004 | Human approval із явним scope |
| UI-008 | Реалізувати graph explorer | P2 | L | SEM-008, WRK-003 | Фільтровані графи, без перевантаження вузлами |
| UI-009 | Реалізувати explainability panel | P1 | M | CTX-006 | Чому контекст/задача має цей стан |

## E12 — Security & Policy

| ID | Задача | Prio | Size | Depends on | Критерій приймання |
|---|---|---:|---:|---|---|
| SEC-001 | Реалізувати actor identity і permission checks | P0 | M | EVT-001 | Commands перевіряють actor/scope/permission |
| SEC-002 | Реалізувати secret redaction | P0 | M | EVT-002, ARC-008 | Secrets не потрапляють у events/logs/context |
| SEC-003 | Реалізувати endpoint trust registry | P1 | M | FND-004 | Credentials не передаються неперевіреним endpoints |
| SEC-004 | Реалізувати untrusted content marking | P0 | M | SEM-007 | Agent/imported content не може змінювати policy |
| SEC-005 | Реалізувати tool/command allowlist | P0 | M | EXE-001 | Worker не виконує довільні команди без policy |
| SEC-006 | Реалізувати local data encryption strategy | P1 | M | ARC-008 | Secrets та приватні transcripts мають окремий encrypted store |
| SEC-007 | Реалізувати audit export | P1 | M | EVT-007 | Перевірюваний експорт без secret leakage |
| SEC-008 | Реалізувати security regression suite | P0 | M | SEC-001..005 | Prompt injection, stale lease, path traversal, secret exfiltration tests |

## E13 — Observability & Budget

| ID | Задача | Prio | Size | Depends on | Критерій приймання |
|---|---|---:|---:|---|---|
| OBS-001 | Визначити event/run telemetry schema | P1 | S | EVT-001 | Correlation між task/session/run/tool |
| OBS-002 | Реалізувати structured run logs | P1 | M | EXE-004 | Logs мають actor, task, run, timestamps |
| OBS-003 | Реалізувати usage ledger | P1 | M | AGT-003 | input/output tokens, provider, model, status |
| OBS-004 | Розділити reported/estimated/unknown costs | P1 | S | OBS-003 | UI/API не змішує точні та оцінені витрати |
| OBS-005 | Реалізувати budget policy | P1 | M | OBS-003 | Новий виклик блокується при hard limit |
| OBS-006 | Реалізувати context size telemetry | P1 | M | CTX-008 | Package size, included/excluded reasons |
| OBS-007 | Реалізувати baseline benchmark runner | P1 | M | CTX-008 | Порівняння з manual retrieval і Git-only baseline |

## E14 — Federation & Multi-Project

| ID | Задача | Prio | Size | Depends on | Критерій приймання |
|---|---|---:|---:|---|---|
| FED-001 | Визначити Project/Repository topology | P2 | M | CODE-001 | Один Project може містити багато repositories |
| FED-002 | Реалізувати RemoteDependency model | P2 | M | WRK-003, FED-001 | External refs мають authority/sync state |
| FED-003 | Реалізувати portable sync envelope | P2 | L | EVT-007 | Export/import із replica identity |
| FED-004 | Реалізувати single-authority replication | P2 | L | FED-003 | Один writer, локальні read replicas |
| FED-005 | Реалізувати peer reconciliation | P3 | XL | FED-004 | Conflict classes та explicit resolution |
| FED-006 | Реалізувати sovereignty policies | P2 | M | FED-003 | Object classes мають sync allow/deny |
| FED-007 | Реалізувати remote lease semantics | P3 | L | FED-005 | Replica-local leases не видаються за global lock |

## E15 — Advanced Intelligence

| ID | Задача | Prio | Size | Depends on | Критерій приймання |
|---|---|---:|---:|---|---|
| INT-001 | Додати optional embeddings index | P2 | M | CTX-004 | Embeddings — rebuildable candidate generator, не source of truth |
| INT-002 | Додати hybrid retrieval | P2 | L | INT-001 | FTS + graph + vector ranking |
| INT-003 | Додати semantic contradiction detector | P2 | L | SEM-008 | Виявляє потенційні суперечності, не вирішує їх сам |
| INT-004 | Додати decision impact prediction | P3 | L | CODE-006, SEM-008 | Показує candidate impact із confidence/evidence |
| INT-005 | Додати adaptive model routing | P2 | M | OBS-003, OBS-005 | Routing policy тестується на benchmark corpus |
| INT-006 | Додати automatic memory promotion | P3 | L | SEC-004, SEM-007 | Лише policy-governed promotion; human approval для high impact |

---

# 4. Залежності та критичний шлях

## 4.1 Критичний шлях до VS-01

```text
ARC-001..008
    |
FND-001..009
    |
EVT-001..006
    |
SEM-001..009 -------- WRK-001..008
    |                       |
CODE-001..008 --------------+
    |
CTX-001..008
    |
EXE-001..008
    |
EVD-001..006
    |
VS-01 end-to-end acceptance
```

Це не означає, що всі задачі треба виконувати суворо послідовно. Наприклад, fixture repository, parser spike, event schema та threat model можна вести паралельно після фіксації їхніх контрактів.

## 4.2 Перші паралельні потоки

| Потік | Можна почати після | Задачі |
|---|---|---|
| Архітектура | Одразу | ARC-001..008 |
| Fixture + parser | ARC-005 draft | FND-009, CODE-003 |
| Runtime foundation | ARC-001 | FND-001..008 |
| Event model | ARC-002 | EVT-001..004 |
| Semantic schema | ARC-004 | SEM-001..003 |
| Security | ARC-008 | SEC-001, SEC-002, SEC-004 |

## 4.3 Не починати паралельно

Не запускати незалежних агентів одночасно на:
- зміну одного schema contract без ownership;
- міграції SQLite без єдиного migration owner;
- зміну relation registry та Work Eligibility semantics без узгодження;
- зміну Event Envelope після початку реалізації producer-ів;
- зміну Git worktree lifecycle та worker recovery без спільних tests.

Для таких файлів/модулів потрібні exclusive resource leases або один owner.

---

# 5. Рекомендована структура репозиторію

```text
cairn-unified/
├── pyproject.toml
├── uv.lock
├── README.md
├── LICENSE
├── .gitignore
├── docs/
│   ├── architecture/
│   │   ├── ADR/
│   │   ├── contracts/
│   │   └── threat-model/
│   ├── backlog/
│   ├── vertical-slices/
│   └── decisions/
├── schemas/
│   ├── events/
│   ├── semantic/
│   ├── work/
│   ├── workflow/
│   └── context/
├── src/
│   └── cairn/
│       ├── bootstrap/
│       ├── cli/
│       ├── core/
│       │   ├── identity/
│       │   ├── events/
│       │   ├── policy/
│       │   └── transactions/
│       ├── projects/
│       ├── semantic/
│       ├── work/
│       ├── code/
│       │   ├── git/
│       │   └── adapters/
│       ├── context/
│       ├── execution/
│       ├── evidence/
│       ├── agents/
│       ├── workflows/
│       ├── federation/
│       ├── observability/
│       └── storage/
├── adapters/
│   ├── php-parser/
│   ├── mcp/
│   └── providers/
├── apps/
│   └── workbench/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── concurrency/
│   ├── recovery/
│   ├── security/
│   └── fixtures/
│       └── php84-demo/
└── scripts/
```

Правила:
- `core` не імпортує UI/provider-specific packages.
- Domain modules не читають таблиці один одного напряму.
- Зв'язок між bounded contexts — через application services та versioned contracts.
- Parser adapter повертає normalized symbol records, а не доменні рішення.
- Provider adapter не має права самостійно змінювати authoritative semantic state.
- Усі external side effects мають idempotency key або explicit non-idempotent marker.

---

# 6. Мінімальний domain contract для першого slice

## 6.1 Event Envelope

```json
{
  "event_id": "01J...ULID",
  "event_type": "semantic.assertion.accepted",
  "schema_version": 1,
  "occurred_at": "2026-11-19T12:00:00Z",
  "recorded_at": "2026-11-19T12:00:01Z",
  "actor": {
    "type": "human",
    "id": "local-user"
  },
  "scope": {
    "project_id": "prj_...",
    "repository_id": "repo_..."
  },
  "correlation_id": "run_...",
  "causation_id": null,
  "idempotency_key": "accept:DEC-PRICE-001:v1",
  "payload": {},
  "integrity": {
    "algorithm": "sha256",
    "digest": "..."
  }
}
```

## 6.2 Semantic Envelope

```json
{
  "object_id": "dec_...",
  "object_type": "Decision",
  "schema_version": 1,
  "scope": {
    "project_id": "prj_...",
    "repository_id": "repo_..."
  },
  "lifecycle": "accepted",
  "revision": 1,
  "created_at": "2026-11-19T12:00:00Z",
  "updated_at": "2026-11-19T12:00:00Z",
  "validity": {
    "valid_from": "2026-11-19T12:00:00Z",
    "valid_to": null
  },
  "authority": {
    "class": "human_authoritative",
    "actor_id": "local-user"
  },
  "provenance": {
    "event_ids": ["01J..."],
    "source_refs": ["requirement://..."]
  },
  "content": {
    "statement": "..."
  }
}
```

## 6.3 Semantic Assertion

```json
{
  "assertion_id": "ast_...",
  "subject": "symbol://repo/.../PriceCalculator::calculateTotal",
  "predicate": "governed_by",
  "object": "dec_...",
  "polarity": "positive",
  "modality": "normative",
  "scope": "repository",
  "trust_class": "human_authoritative",
  "validity": {
    "valid_from": "2026-11-19T12:00:00Z",
    "valid_to": null
  },
  "provenance": {
    "actor_id": "local-user",
    "event_id": "01J..."
  }
}
```

## 6.4 Context Package

```json
{
  "context_id": "ctx_...",
  "work_item_id": "wrk_...",
  "target_revision": {
    "repository_id": "repo_...",
    "commit": "abc123..."
  },
  "policy_version": "context-policy-v1",
  "items": [
    {
      "object_id": "dec_...",
      "reason_included": "active decision governing target symbol",
      "trust_class": "human_authoritative",
      "freshness": "current",
      "source_refs": ["event://...", "git://..."]
    }
  ],
  "excluded": [
    {
      "object_id": "dec_old_...",
      "reason": "superseded"
    }
  ],
  "budget": {
    "unit": "bytes",
    "estimated": 12000,
    "confidence": "measured"
  },
  "integrity_digest": "sha256:..."
}
```

Ці схеми — стартові контракти для VS-01, а не остаточна повна ontology. Перед реалізацією вони мають бути оформлені як JSON Schema/Pydantic models і contract tests.

---

# 7. CLI contract для першого slice

Початкові команди:

```bash
cairn init
cairn repo add ./fixtures/php84-demo
cairn repo index <repository-id>

cairn decision create \
  --id DEC-PRICE-001 \
  --status accepted \
  --scope "symbol://.../PriceCalculator::calculateTotal"

cairn work create \
  --title "Ensure decimal-safe total calculation" \
  --type task \
  --priority P1

cairn work relate <work-id> --type governed_by --target DEC-PRICE-001
cairn work relate <work-id> --type targets_symbol --target "symbol://..."

cairn work ready --explain
cairn work claim <work-id> --agent fixture-worker
cairn context build --work <work-id> --explain
cairn run start --work <work-id> --worker fixture-worker
cairn run show <run-id>
cairn evidence show --work <work-id>
cairn work verify <work-id>
cairn work close <work-id>

cairn decision supersede DEC-PRICE-001 --by <new-decision-id>
cairn context build --work <work-id> --as-of <timestamp>

cairn db rebuild
cairn doctor
```

CLI назви можуть уточнюватися, але семантика команд та їхні side effects мають бути стабільними. `work close` повинен викликати Acceptance Evaluator, а не напряму змінювати статус без evidence.

---

# 8. Definition of Done

## 8.1 Definition of Done для кожної задачі

- [ ] Acceptance criteria записані до початку реалізації.
- [ ] Зміна має unit tests.
- [ ] Зміна має contract tests, якщо зачіпає public/domain contract.
- [ ] Міграція має upgrade test, якщо змінюється schema.
- [ ] Логи не містять secrets або raw private content без дозволу.
- [ ] Error states typed і пояснювані.
- [ ] Idempotency та retry behavior визначені.
- [ ] Документація/API приклади оновлені.
- [ ] Не порушено межі bounded contexts.
- [ ] Усі lint/type/test checks пройшли.

## 8.2 Definition of Done для VS-01

- [ ] Повний сценарій проходить з чистого clone.
- [ ] Сценарій проходить offline.
- [ ] SQLite projection можна видалити й відновити.
- [ ] Session B відновлює необхідний контекст без Session A transcript.
- [ ] Superseded/revoked decision не подається як current.
- [ ] Context Package має reproducible digest.
- [ ] Work claim/lease concurrency tests проходять.
- [ ] Stale fencing token tests проходять.
- [ ] Commit/test evidence відповідає точній revision.
- [ ] Security tests для secret redaction та untrusted content проходять.
- [ ] Демонстрація не залежить від реального LLM.

---

# 9. Test strategy та вимірювання

## 9.1 Test layers

1. **Unit:** доменні правила, lifecycle, relation semantics, freshness, policy.
2. **Contract:** JSON schemas, CLI/API payloads, parser normalized output.
3. **Integration:** SQLite + event ledger + Git + parser + context compiler.
4. **Concurrency:** atomic claim, lease expiry, heartbeat races, fencing tokens.
5. **Recovery:** process kill, restart, projection rebuild, interrupted run.
6. **Security:** prompt injection, secret leakage, path traversal, command injection, malicious metadata.
7. **Golden tests:** стабільний Context Package для fixture repository.
8. **Performance:** indexing latency, context build latency, event replay, SQLite contention.

## 9.2 Baselines для Context Compiler

Не заявляти про економію токенів чи зменшення повторних правок до вимірювання. Порівняти три режими на однаковому наборі задач:

- **B0 Manual:** людина/агент вручну читає потрібні файли, ADR та Git history.
- **B1 Git-only:** `git log`, `git blame`, `git log -L`, diff та тести.
- **B2 Cairn:** Context Compiler з Work Graph + Semantic Graph + Evidence.

Вимірювати:
- контекстні байти/токени;
- час до коректного розуміння задачі;
- частку знайдених чинних рішень;
- частку застарілих/скасованих тверджень, що помилково потрапили в контекст;
- повторні правки;
- regression rate;
- кількість конфліктів;
- кількість ручних уточнень;
- час від task claim до verified result;
- частку context items із валідним provenance.

## 9.3 Мінімальні quality gates

Початкові цілі для VS-01 (підлягають перегляду після першого вимірювання):

| Метрика | Ціль |
|---|---:|
| Відтворюваність Context Package | 100% на незмінному fixture/revision/policy |
| Provenance coverage для normative assertions | 100% |
| Revoked/superseded active leakage | 0 |
| Stale worker write acceptance | 0 |
| Duplicate effects при idempotent retry | 0 |
| Offline VS-01 completion | 100% |
| Projection rebuild consistency | 100% |
| Parser resolution для fixture target symbols | 100% |

Не встановлювати жорсткий token-saving target до отримання B0/B1 baseline.

---

# 10. Ризики та запобіжники

| Ризик | Наслідок | Запобіжник |
|---|---|---|
| Надмірна ontology до першого value | Повільна розробка, складна схема | Почати з типів, потрібних VS-01; schema evolution |
| Занадто ранній multi-agent режим | Race conditions, нестабільні міграції | Один worker спочатку; concurrency tests до fan-out |
| LLM-generated memory стає authoritative | Отруєння контексту | Trust classes, approval, evidence, lifecycle |
| Parser не знаходить stable symbol | Хибна прив'язка історії | Structural fingerprint + lineage + ambiguity review |
| SQLite стає distributed DB | Некоректна федерація | Local authority в MVP; federation окремим етапом |
| Worktree сприймається як sandbox | Виконання небезпечного коду | OS/container sandbox і allowlists |
| Context ranking приховує важливе | Неправильне рішення агента | Explainability + included/excluded manifest |
| Неповний transcript capture | Втрата подій сесії | Capture capability status; не заявляти повноту без підтвердження |
| Зміна schema під час паралельної роботи | Несумісні producers/consumers | Contract owner, versioning, migration gates |
| Невиміряна економія | Маркетингові, а не технічні твердження | B0/B1/B2 benchmark |

---

# 11. План першого інкременту

Рекомендована послідовність без календарних обіцянок:

### Increment A — Contracts
- ARC-001..008
- FND-001, FND-002, FND-009
- EVT-001 draft
- SEM-001 draft

**Exit:** команда може незалежно реалізовувати модулі, не сперечаючись про ідентичність об'єктів, події та provenance.

### Increment B — Durable state
- FND-003..007
- EVT-001..006
- SEM-001..006
- SEC-001, SEC-002, SEC-004

**Exit:** створення об'єкта породжує подію; проєкції відновлюються; semantic assertion має lifecycle та provenance.

### Increment C — Work + code
- WRK-001..008
- CODE-001..008
- EVD-001, EVD-006

**Exit:** система знаходить метод, створює задачу, перевіряє її готовність і безпечно видає claim.

### Increment D — Context
- CTX-001..008
- SEM-007..009

**Exit:** Cairn формує пояснюваний Context Package з актуальними рішеннями, символами та evidence.

### Increment E — Execution
- EXE-001..008
- EVD-002..005

**Exit:** worker виконує контрольовану зміну в worktree, запускає тести, створює commit та прикріплює evidence.

### Increment F — Cross-session proof
- EVT-006
- decision supersede/revoke tests
- full VS-01 acceptance
- baseline benchmark harness

**Exit:** Session B відновлює правильний контекст без Session A transcript; зміна доведена commit/test evidence.

---

# 12. Після VS-01: що реалізовувати наступним

Після успішного VS-01 не варто одразу переходити до federation або vector search.

Рекомендований порядок:

1. **VS-02 — Real Agent Host:** під'єднати один реальний coding agent через Host Contract/MCP; перевірити короткі сесії, hook capture і recovery.
2. **VS-03 — Workflow + Gates:** реалізувати повторюваний workflow із human approval, CI gate, retry та recovery.
3. **VS-04 — Multi-agent Coordination:** два агенти, незалежні worktrees, resource lease, handoff, merge slot, stale worker rejection.
4. **VS-05 — Workbench:** UI для Work queue, semantic object, session/run monitor, approvals та evidence.
5. **VS-06 — Multi-repo:** capability contracts і remote dependencies.
6. **VS-07 — Federation:** спочатку single-authority sync, потім peer reconciliation.
7. **VS-08 — Intelligence & Budget:** hybrid retrieval, optional embeddings, adaptive model routing, budget optimization на виміряних baseline.

---

# 13. Release gates

## Alpha 0 — Core proof
- VS-01 завершений.
- Offline/local-only.
- Deterministic worker.
- No federation.
- CLI-first.

## Alpha 1 — Agent-integrated
- Один реальний provider adapter.
- Session ingestion/recovery.
- MCP tools.
- Human approval boundaries.

## Beta — Team workflow
- Workflow runtime, gates, Workbench.
- Multi-agent claims/leases.
- Worktree integration.
- Evidence-based merge gate.

## 1.0 — Local semantic operating layer
- Stable schema/API.
- Backup/restore.
- Migration guarantees.
- Multi-project.
- Budget governance.
- Security review.
- Federation limited to tested topologies.

---

# 14. Перший набір ADR, який треба затвердити

| ADR | Питання | Рекомендована позиція |
|---|---|---|
| ADR-001 | Modular monolith чи microservices? | Modular monolith |
| ADR-002 | Event log vs mutable rows? | Append-only events + projections |
| ADR-003 | Operational database? | SQLite WAL |
| ADR-004 | Semantic model? | Envelope + Assertion + typed relations |
| ADR-005 | PHP parsing? | Spike: nikic/php-parser vs Tree-sitter |
| ADR-006 | Code isolation? | Git worktrees + separate sandbox boundary |
| ADR-007 | Agent integration? | Host Contract + MCP, provider adapters later |
| ADR-008 | Federation? | Deferred; no distributed writes in MVP |
| ADR-009 | Embeddings? | Optional, rebuildable, not source of truth |
| ADR-010 | Human authority? | Explicit approval event with scope and provenance |

---

# 15. Критерій готовності до старту розробки

Реалізацію VS-01 можна починати, коли виконано всі умови:

- [ ] Архітектурні ADR-001..010 затверджені або мають явно позначені відкриті питання.
- [ ] Event Envelope v1 і Semantic Envelope v1 зафіксовані.
- [ ] WorkItem/status/dependency contract зафіксований.
- [ ] Relation registry визначає `execution_effect` кожного relation type.
- [ ] Fixture PHP 8.4 repository створений і має deterministic tests.
- [ ] Parser spike завершений; вибір адаптера задокументований.
- [ ] Git/worktree lifecycle описаний і протестований окремо.
- [ ] Threat model VS-01 прийнятий.
- [ ] Контракти мають schema validation та golden fixtures.
- [ ] Scope exclusions VS-01 погоджені.

Якщо хоча б один із перших шести пунктів не виконаний, починати паралельну реалізацію модулів передчасно.

---

# 16. Підсумкове рішення

Першим продуктово значущим результатом Cairn має бути не task tracker, не agent dashboard і не універсальний workflow builder.

Це має бути перевірений ланцюг:

**WorkItem → Claim → Semantic Context → Code Symbol → Isolated Change → Test Evidence → Commit → Decision Lifecycle → Context Recovery in a New Session.**

Якщо цей ланцюг працює, Cairn доводить свою головну цінність: він переносить між агентами не просто пам'ять про розмову, а актуальну, перевірювану й адресовану до коду модель того, що потрібно зробити, чому це потрібно зробити та якими доказами підтверджується результат.

Усі інші можливості — повний Workflow Kit-style orchestration, Beads-class work management, federation, visual graph, embeddings і adaptive model routing — розвиваються навколо цього ядра, а не замінюють його.
