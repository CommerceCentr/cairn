> **АРХІВНИЙ ДОДАТОК. Не керується цим документом.**
>
> Чинний мандат — `MANDATE-AGENT-CAIRN-CAUSAL-HISTORY.md` у корені
> репозиторію: він має 257 рядків проти 84 тут і містить розділ §7
> («Мандат на повну вузлову причинність»), якого в цій копії немає.
> Ця копія лишена як запис ранішої редакції; за розбіжності діє корінь.

## 7. Якщо потрібно з'ясувати, хто саме правив файл, а писати в канон не можна

### 7.1. Розмежуй диски ДО першої команди

<REPO> — канонічний Git-репозиторій: ТІЛЬКИ читання. <ROOT> — копія
сховища .cairn на узгодженому диску даних; оригінальний пілот не мутувати.
Файл opencode.db читати через mode=ro; не копіювати журнали на диск,
визначений для тестових даних як заборонений.

Зафіксуй ДО і ПІСЛЯ: git rev-parse HEAD, git status --porcelain | count,
розмір і mtime вихідного ledger.jsonl. Різниця = припинити роботу, а не
пояснювати її «ефектом індексації».

### 7.2. Baseline: позитивний контроль перед будь-яким «немає»

python tools/measure_vec_mvp.py --root <ROOT> --repo <REPO> --path <KNOWN_PATH>

Потрібно: doctor_ok=true, missing_commits=[], path_probe з
native_commits == cairn_commits > 0. Порожній результат без цього контролю
є INSTRUMENT_STATUS_UNKNOWN, а не «атрибуція не встановлена».

### 7.3. Два рівні атрибуції — не плутай їх

caused_by          → участь сесії у часі/шляху (participation)
mutation_in_commit → унікальний текстовий перехід у коміті (admissible)

why.py:61-89 приймає caused_by ЛИШЕ якщо існує mutation_in_commit з тим
 самим node_key. Тому при mutation_in_commit = 0 команда
 `why node <PATH>` чесно друкує «із сесіями 0; підтверджених мутацій 0»
 НАВІТЬ за наявності тисяч caused_by-ребер. Це не доказ, що файл ніхто
 не шукав/не правив; це доказ відсутності допустимого містка до коміту.

Перевірка рівня (read-only SQL до <ROOT>/.cairn/runtime/cairn.sqlite):
SELECT relation_type, count(*) FROM relations GROUP BY relation_type;

Перед тим як стверджувати «сесію встановлено», переконайся, що
mutation_in_commit > 0.

### 7.4. Порядок команд: сухий прогон → запис → верифікація

1) index_agent_history.py --root <ROOT> --repo <REPO> --opencode-db <DB>
   Фільтр проєкту — session_v2.directory всередині <REPO>; змішані сесії
   пропускаються за дизайном. automatic_commit_links: 0 на цьому кроці
   очікуваний і НЄ є проходом.

2) та сама команда з --apply. Перевір doctor_ok=true; запиши
   sessions_indexed / mutations_resolved / mutations_unresolved окремо.

3) link_mutations_to_git.py --root <ROOT> --repo <REPO>        (dry-run)
   Читай counts: matched, no_unique_transition,
   no_indexed_session_or_path, shared_witness_rejected.
   Заборонено з'єднувати за часом, ім'ям, Git-email або згадкою шляху.

4) та сама команда з --apply, потім обов'язково --verify.
   Критерій проходу: failed == 0.

5) повтори measure_vec_mvp.py і why node для контрольного шляху.
   Подай числа ДО/ПІСЛЯ: session_objects, direct_mutation_edges,
   event_count, mutation_in_commit.

### 7.5. Що можна і чого не можна стверджувати

 actor у записах = source family (claude_code / codex / opencode) + session_id.
 Це НЕ ім'я моделі, НЕ роль VEC (Alice/Bob), НЕ особа. Рольову атрибуцію
 без окремого джерела не вигадуй.

 Дозволено: «сесія <id> виконала правку шляху <path>, зіставлену з комітом
            <sha> через унікальний перехід» — за наявності рядка з п. 7.4.4.
 Не дозволено: «агент X є автором усього коміту», «усі правки належать Y»,
               «жодних правок не було» — без механічного переліку.

 У звіті окремо назви: sessions_with_mutations але неіндексовані,
 mutations_unresolved, mixed_sessions_skipped, поточну сесію (вона ще
 триває й у сховищі відсутня). Це omissions з причинами, не прихований шум.

 Незалежний аудитор: звіт SQL-проєкції та результат `why node` мають
 збігтися. Один метод не присвоює власному висновку VERIFIED.

### 7.6. Критерії виходу

doctor_ok == true; witness verification failed == 0;
missing_commits == []; позитивний контроль з 7.2 пройдено;
канонічний <REPO> і вихідний пілот не змінені; жодних тестових даних
на забороненому диску. Якщо хоч один пункт не виконано — статус
INCONCLUSIVE, а не PASS.