# Cairn Unified 2.0: інструкція для агента

Цей файл — інтерфейс агента до чинної реалізації. Історичні документи
(`Cairn_2.0_Technical_Architecture_Specification_UA.md`,
`Cairn_Unified_Technical_Specification_UA (v3).md`) описують проєктування
й архівні рішення; вони не є довідкою по командах. Чинна довідка —
`python -m cairn_unified mcp tools` і [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Що таке Cairn

Cairn Unified — локальний Event Ledger і project graph. Жодного хмачного
API, жодного демона, жодної зовнішньої БД. Версія пакета `2.0.0`,
`SCHEMA_VERSION = "2.0"`, `DB_SCHEMA_VERSION = 1`.

```text
.cairn/
├── events/ledger.jsonl        джерело істини, append-only, JSONL
├── runtime/cairn.sqlite       відтворювана проєкція (FTS, objects, relations)
├── objects/                   семантичні об'єкти
├── runtime/zones/<zone>.json  дескриптори зон + .lock
└── zones/<zone>/journal.jsonl append-only журнал зони
```

Каталог `.cairn` лежить **всередині кореня проєкту** (його визначає `Store.__init__`, а не якийсь глобальний шлях) і під `.gitignore`. Він
переноситься разом із проєктом, але не комітиться без явного рішення людини.

---

## Перший запуск (людина або агент із дозволом на запис)

```bash
python -m cairn_unified --root <шлях> project init
python -m cairn_unified --root <шлях> project doctor
python -m cairn_unified --root <шлях> project rebuild
python -m cairn_unified --root <шлях> mcp tools
```

Усі команди повертають JSON на stdout. Помилка — теж JSON з полем
`error` і стабільним exit code. Ніякого прихованого стану між викликами:
стан відтворюється з Event Ledger (`rebuild`).

---

## 12 MCP-інструментів

Отримати актуальний перелік: `python -m cairn_unified mcp tools`.

| Інструмент | Що робить | Хто ініціює |
|---|---|---|
| `cairn_context` | складає обмежений контекст для задачі | агент перед роботою |
| `cairn_work_next` | готові задачі; `claim` — окремим прапорцем | планувальник |
| `cairn_work_show` | задача, блокувальники, eligibility | агент |
| `cairn_work_update` | дозволений перехід стану або коментар | агент |
| `cairn_semantic_query` | чинні об'єкти й твердження, `as_of` | агент |
| `cairn_code_explore` | символи за сталою адресою й ревізією | агент |
| `cairn_why` | коли і чому редагувався вузол: хронологія, походження, довіра, межі | агент перед правкою |
| `cairn_decision_propose` | пропонує рішення, **ніколи не затверджує** | агент |
| `cairn_evidence_attach` | приєднує commit/test доказ із перевіркою | агент |
| `cairn_checkpoint` | ідемпотентний checkpoint запуску | runtime |
| `cairn_handoff` | пакет передачі сесії | агент |
| `cairn_status` | компактний зріз стану проєкту | будь-хто |

### `cairn_why` — відповідь, а не сировина

```text
КОЛИ     штамп і коміт, що торкнувся шляху (git show --name-status)
СЕСІЯ    сесія й компактація-якір
ЧОМУ     наміри користувача; текст — лише за запитом і ЗВІРЕНИЙ за sha256
МЕЖА     участь у часі й шляху, НЕ семантична причина; коміт причиною не є
```

Шість полів у відповіді: `answer`, `chronology`, `provenance`, `trust`,
`omissions`, `cost`. `omissions` розрізняє **дві різні** причини:
`unresolved:*` — джерела немає, `verifier_error:*` — перевірка впала.
`--budget` у СИМВОЛАХ; якщо бюджет виконати неможливо, це сказано
(`budget_exceeded`), а не замовчано.

Виміряно 2026-11-21 на приватному сховищі (1 876 подій), той самий прилад
на обох шляхах, один прогін (`tests/compare_why.py`):

| шлях | байти | токени ≈ | мс |
|---|---:|---:|---:|
| нативний: скан журналу + `git log -1` + `show --name-status` + `log -p -12` + `blame` | 3 111 792 | 685 099 | 114 |
| `why --budget 8000` | 8 326 | 1 983 | 56 |
| `why --budget 2000` | 2 498 | 507 | 52 |

Межі дві, і вони різні: «сировина проти готової відповіді» (×345 за
токенами при бюджеті 8 000, ×1 351 при 2 000) і «сировина проти всього
шляху Cairn» (×220 і ×417). Жодна з них НЕ порівнює з рівносильною
відповіддю `grep`: нативний шлях причини не відтворював. Токени — `chars÷4`,
проксі, не токенізатор цільової моделі.

Виклик через MCP (`cairn_unified.mcp.handle_request`) або напряму:
`mcp.call_tool(store, "cairn_semantic_query", {"text": "..."}, actor)`.
Актор фіксується **хостом**, а не аргументами запиту.

---

## Правила довіри, які не обговорюються

```text
1  АГЕНТ НЕ ПІДВИЩУЄ ДОВІРУ ВЛАСНОМУ ТЕКСТУ
   trust_class агента — agent_proposed. human_authoritative і verified_system
   не можна виставити через CLI чи MCP: require_human / authority_required.

2  ІМПОРТ НЕ УСПАДКОВУЄ ДОВІРУ
   bundle з іншого сховища завжди стає imported_untrusted, verification
   викидається. Інакше чужий штамп verified_system замкнув би verify_object
   без чита河中 джерел на цій машині.

3  КОМІТ НЕ Є ПРИЧИНОЮ
   commit_fact --mutated--> node. Причина починається в user-повідомленні;
   reasoning агента лише розгортає намір. Зв'язок node --caused_by--> session.

4  «ТОРКНУВСЯ» = ЛИШЕ CrUD
   write/edit підтверджені mutation-event або git-diff. read/grep/glob/shell
   ребер не створюють.

5  ПЕРЕВІРКА НЕ ВІРИТЬ ТЕКСТУ ОБ'ЄКТА
   verify_object перечитує opencode.db у режимі mode=ro, git rev-parse
   і журнал. Якщо джерела немає — «не встановлено», не verified=True.

6  MERGE ТІЛЬКИ ДЛЯ ТОГО САМОГО GIT-РЕПОЗИТОРІЮ
   тотожність — набір кореневих комітів (code.git.identity), кожен хеш
   резолвиться локальним git. Нерезолвовані — unlinked_commits + ескалація,
   не silent last-write-wins. Конфлікти — явний перелік.
```

---

## Зони: два агенти, одна папка

Дескриптор зони — `.cairn/runtime/zones/<zone>.json`; лок — сусідній
`.lock`; журнал — `.cairn/zones/<zone>/journal.jsonl` (append-only).

```text
другий агент у зайнятій зоні  -> status="deferred", не блокування
прострочений власник          -> stale_fence
журнал                        -> тільки додавання, seq не перезаписується
дескриптор                    -> відносні context_refs на сесію/summary
```

Абсолютні шляхи всередині payload (`opencode_db`, `repo_roots`) —
історичні докази, а не посилання. Прапорець bundle називається
`relative_refs_only`, а не `relative_paths_only`.

---

## Адаптер джерела сесій

`adapters/opencode.py` — еталон: читає локальний `opencode.db` **тільки
на читання** (`mode=ro`). Якщо пишеш адаптер під іншого агента:

```python
NAME = "<коротке імʼя>"
def discover(root=None) -> list[str]: ...
def read(path, offset=0): ...   # словники {session_id, ts, actor, kind, abs_paths, cwd}
```

CrUD визначається **назвою інструмента** (`write`/`edit`), не наявністю
поля `path` — інакше самі виклики `read`/`grep`/`glob` стають хибними
«дотиками». Корінь «чому» — `user_intent`, а не лише `reasoning`.

---

## Перевірка перед подачею

```bash
python tests/run_all.py
python tests/verify_zones.py
python tests/verify_merge.py
python tests/verify_causal_history.py
python tests/verify_compaction_link.py
```

Останній вимір (2026-11-21): `run_all.py` **15/15**, `verify_zones.py`
**29/29**, `verify_merge.py` **16/16**, `verify_causal_history.py`
**19/19**, `verify_compaction_link.py` **31/31**. Усі вони тепер у
воротах `tests/run_all.py` (21/21) і не залежать від жодного шляху
поза репозиторієм: раніше два з них зверталися до приватного диска й
на іншій машині давали exit 1, а не пропуск.
Детальніше: [`docs/MEASUREMENTS.md`](docs/MEASUREMENTS.md).

---

## Межі

```text
* сирі транскрипти НЕ імпортуються як факти —
  тільки скомпактований контекст;
* зв'язок доводить участь у часі/шляху, не семантичну причину;
* 12/221 мутацій у ланцюжку — решта доступні окремим об'єктом;
* завершення задачі НЕ означає, що код змерджено чи CI зелений;
* інструкцію самопідключення перевірено на одному агенті (OpenCode).
```
