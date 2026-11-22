# Архітектура Cairn Unified 2.0 (чинна реалізація)

Це опис **того, що працює зараз**, а не цільова специфікація. Версія
пакета `2.0.0`, `SCHEMA_VERSION = "2.0"`, `DB_SCHEMA_VERSION = 3`
(ревізії обʼєктів і докази на ребрах; міграція — перебудова з журналу, див.
[`ADR-016-envelope.md`](ADR-016-envelope.md)).
Історичні плани лежать окремо й не є довідкою по API:

- `Cairn_2.0_Technical_Architecture_Specification_UA.md` — TAS, draft;
- `Cairn_Unified_Technical_Specification_UA (v3).md` — архітектурне
  дослідження; назва файлу «v3» **не** означає наявність v3 у коді;
- `Cairn_Unified_Implementation_Backlog_UA.md` — беклог.

Вступ для агента: [`AGENT-ONBOARD.md`](../AGENT-ONBOARD.md).
Виміри: [`MEASUREMENTS.md`](MEASUREMENTS.md).
Вигоди простою мовою: [`WHY-CAIRN.md`](WHY-CAIRN.md).
Схема обʼєкта: [`ADR-016-envelope.md`](ADR-016-envelope.md).

---

## 1. Принцип

```text
Джерело істини   append-only Event Ledger (.cairn/events/ledger.jsonl)
Проєкція         SQLite (.cairn/runtime/cairn.sqlite) — відновлювана rebuild
Об'єкти          семантичні envelope з trust_class, lifecycle, semantic
Ребра            typed relations: caused_by, derived_from, mutated, targets_symbol
Географія        local-only, offline; жодного хмачного API чи демона
```

Імпорт пакета не виконує жодних дій із диском чи мережею. Стан між
викликами CLI не зберігається — він відтворюється з Ledger за допомогою
`project rebuild`.

---

## 2. Bounded contexts і модулі

Реалізація — пакет `cairn_unified/` з одним підпакетом (`code/`), кожен
модуль відповідає одному контексту з v3 §5.1. Імена взяті з реального дерева файлів.

| Модуль | Відповідальність |
|---|---|
| `store.py` | `.cairn`, події, `mutate`, `rebuild`, `require_human` |
| `ledger.py` | append-only JSONL, `chain_hash` |
| `db.py`, `projections.py` | SQLite-проєкція подій у таблиці |
| `ontology.py` | `TRUST_CLASSES`, `AUTHORITATIVE`, `RELATION_REGISTRY`, `DB_SCHEMA_VERSION` |
| `semantic.py` | `put_object`, `relate`, `query`, bi-temporal `as_of` |
| `context.py` | `build`/`persist`/`show`/`explain`, бюджет, provenance-envelope |
| `nodes.py` | адреси `symbol:`/`section:`/`page:`/`segment:`/`whole:`, Python AST (`ast.parse`) |
| `code/` | `git` (identity, exists, head), PHP-парсер |
| `history.py`, `commits.py` | `commits_for_path`, `scan`, `import_compactions` |
| `compaction_link.py` | `mutation_readiness`, `normalize_path`, автокомактація |
| `causal_history.py` | `link()` — 5 об'єктів, `governed_by` |
| `verification.py` | детермінований `check_object`/`verify_object`, ескалація |
| `evidence.py`, `gates.py` | commit/test докази, явні gate |
| `work.py`, `workflow.py`, `handoff.py`, `dossiers.py` | Work Graph, Workflow Runtime, передача сесії |
| `federation.py` | `PROTECTED`, `inspect`, `import_bundle` |
| `zones.py` | дескриптори, lease/fence, журнали, `export_bundle`/`import_bundle` |
| `merge.py` | злиття того самого git-репозиторію, true git-хеші |
| `redact.py`, `errors.py`, `canon.py` | секрети, стабільні коди помилок, канонізація |
| `why.py` | `answer()` — коли і чому редагувався вузол: хронологія, походження, довіра, межі |
| `adapters/normalize.py` (у пакеті) | збирач: сирі записи журналів -> канонічна форма + матриця можливостей |
| `mcp.py`, `cli.py` | 13 MCP-інструментів і CLI `cairn2` (встановлюваний: `pyproject.toml`) |

Адаптери — ПІДПАКЕТ `cairn_unified/adapters/`: `opencode.py`,
`claude_code.py`, `codex.py`, `normalize.py`, три `_probe_*` (навмисні
підробки для воріт контракту), `php-parser/index.php`,
`fixtures/php84-demo/`.

Три джерела, і вони НЕ однакові. Матриця можливостей
(`normalize.CAPABILITIES`) записує, що саме кожне доводить: Codex не дає
структурованих мутацій (правки всередині `exec`), а прози агента не дає
ні Claude Code, ні Codex. Автоперевірка перечитує те джерело, яке назвав
обʼєкт (`semantic.source_kind` + `source_ref`), і межа джерела
(`node_link_unsupported_by_source`) відокремлена від браку даних
(`unresolved:*`). Раніше теки без
`__init__.py` діставали через `sys.path.insert` із середини пакета —
через це `cairn_unified` неможливо було встановити.

---

## 3. Події та стан

```text
Store.mutate(op)  -> op(con) -> store.append(...)  -> ledger -> projections
```

Кожна подія має `event_id`, `event_type`, `aggregate_id`, `actor`,
`chain_hash` і детермінований `idempotency_key`. Повторна запис того самого
ключа не створює дубліката. Ledger — єдиний носій істини: SQLite можна
видалити й відбудувати.

`require_human(actor)` блокує дії, які агент не має права виконувати
(authority_required). Авторитетні класи — `human_authoritative` і
`verified_system`.

---

## 4. Причинна модель

Напрямок ребра за конвенцією `derived_from`:

```text
user_intent (opencode.db, mode=ro)
    ├─ reasoning тієї самої події       — розгортає намір, не корінь
    └─ mutation (write/edit)            — CrUD, підтверджений подією
           └─ mutated --> node          — коміт НЕ причина

node --caused_by--> session             — вузол належить до сесії
auto_compaction --derived_from--> session — детермінований зріз «чому»
commit_fact --mutated--> node           — факт участі в часі/шляху
node --targets_symbol--> work           — адреса вузла
work --governed_by--> session/obj       — керування, не HISTORY-timeline
```

П'ять об'єктів, які створює `causal_history.link()`:
`session`, `unresolved_mutations`, `zone_descriptors`, `commit_fact`,
`auto_compaction`. Ланцюжок намірів обрізається до `MAX_CHAIN = 12`
у `compaction_link.py`; решта мутацій лишаються в повному об'єкті.

Якір сесії — остання компактація з `ts <= commit_ms`. Немає передування →
`anchor_before_commit=False`. Коміт і зжата компактація **причинами не є**.

---

## 5. Довіра

```text
human_authoritative   підтверджено людиною з дозволом
verified_system       детермінована перевірка (git SHA, тест, журнал)
source_observed       прочитано з джерела
agent_proposed        пропозиція агента, не затверджена
agent_inferred        висновок без незалежної перевірки
imported_untrusted    імпортований bundle — verification завжди викидається
rejected_or_revoked   відхилено/відкликано
```

`verify_object` має коротке замикання лише для `AUTHORITATIVE` з чинним
`POLICY_VERSION`. Для всього іншого він перечитує джерела: `opencode.db`,
`git rev-parse`, журнал. Немає джерела → `verification.escalated`
з причиною `unresolved:*`, а не `verified=True`.

`import_bundle` призначає кожному об'єкту `imported_untrusted`, прибирає
`verification` і додає `import_provenance` з digest джерела. Це виправляє
реальний дефект: перенесений штамп `auto-verify/1.0` видавав би імпортоване
за перевірене на машині без `opencode.db`.

---

## 6. Зони та переносність

```text
.cairn/runtime/zones/<zone>.json      дескриптор: head, fence, owner, context_refs
.cairn/runtime/zones/<zone>.lock      лок
.cairn/zones/<zone>/journal.jsonl     append-only, seq не перезаписується
```

Другий агент у зайнятій зоні → `status="deferred"`. Прострочений власник →
`stale_fence`. Bundle несе дескриптори, журнали й context-об'єкти з
відносними посиланнями (`relative_refs_only: true`). Абсолютні шляхи
всередині payload — історичні докази, а не посилання.

---

## 7. Merge того самого репозиторію

`merge.py` не довіряє рядкам bundle:

1. тотожність репозиторію — `code.git.identity()` (набір кореневих комітів);
2. кожен хеш коміту резолвиться локальним `git rev-parse`;
3. нерезолвовані → `unlinked_commits` + ескалація, не ребра;
4. захищені поля federation, розбіжні дескриптори й journal seq → явні
   конфлікти, локальний стан не перезаписується мовчки;
5. застосування (`apply=True`) вимагає `require_human`;
6. подія `merge.recorded` несе HEAD, root commits, commit_links, conflicts.

Вимір на реальному bundle (2026-11-21): 7 зв'язаних хешів, 5 unlinked,
1 871 подія, 4 об'єкти, 5 зон, конфлікти `zone_identity_divergence` і
`zone_lease_active`. Детальніше — §11 `MEASUREMENTS.md`.

---

## 8. Інтерфейс

```bash
python -m cairn_unified mcp tools              # 13 інструментів
python -m cairn_unified --root <path> project init
python -m cairn_unified --root <path> project doctor
python -m cairn_unified --root <path> project rebuild
python -m cairn_unified --root <path> repo add <path>
python -m cairn_unified --root <path> code index <repo_id>
python -m cairn_unified --root <path> context compile <work_id> --budget 8000
python -m cairn_unified --root <path> why node <path> --commit <sha> --budget 8000
```

Усі команди — JSON на stdout. Вихідні коди: 0 — успіх; 1 — не знайдено
(це **відповідь**, не збій); 2 — помилка використання/контракту;
3 — доменна помилка (типова для `CairnError`); 4 — відмова
(`stale_fence`, `bundle_*_corrupt`, `merge_refused`).

---

## 9. Межі, названі прямо

```text
* сирі транскрипти не імпортуються як факти — лише скомпактований контекст;
* зв'язок доводить участь у часі/шляху, не семантичну причину;
* 12/221 мутацій у ланцюжку, решта — окремий об'єкт;
* repo/шляхи-докази можуть містити абсолютні рядки — вони не є refs;
* PHP-екстрактор — сканер оголошень, повнота 0.887–0.939 (виміряно);
* завершення задачі ≠ змерджений код ≠ зелений CI;
* merge не спрацьовує, якщо історія іншого девайсу недоступна локально.
```
