"""cairn — сховище причинності з історії агентських сесій.

    python cairn.py index   --store <тека> [--adapter claude_code] [--match <підрядок>]
    python cairn.py why     --store <тека> <шлях у репозиторії>
    python cairn.py stats   --store <тека>
    python cairn.py selftest --store <тека> --adapter <назва>

Сховище переноситься копіюванням теки. Абсолютних шляхів у ньому немає.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.console import setup as _console_setup
_console_setup()

from core import budget, contract, manifest, paths as P, redact  # noqa: E402
from core.store import Store  # noqa: E402


def load_adapter(name: str):
    return importlib.import_module(f"adapters.{name}")


def _roots_for(cwd: str, cache: dict[str, str | None], roots: dict[str, str],
               registry: dict[str, dict]):
    """Корінь і repo_id для cwd, із памʼяттю. None — якщо це не репозиторій.

    Заодно наповнює реєстр: repo_id непрозорий навмисно, тож десь мусить
    лежати його розшифровка, інакше сховище неможливо читати очима.
    Мітка (імʼя теки) — ЛИШЕ метадані: у ключі вона не бере участі, бо в
    іншого користувача тека зветься інакше.
    """
    if cwd in cache:
        return cache[cwd]
    root = P.repo_root(cwd) if cwd else None
    rid = P.repo_id(root) if root else None
    if root and rid:
        roots[P.norm(root)] = rid
        cache[cwd] = rid
        if rid not in registry:
            fp = P.fingerprint(root) or ()
            registry[rid] = {"root_commits": list(fp),
                             "label": os.path.basename(P.norm(root))}
    else:
        cache[cwd] = None
    return cache[cwd]


def _registry_labels(store_root: str) -> dict[str, str]:
    """{коротке id -> мітка}. Мітка лише для очей, у ключах не бере участі."""
    path = os.path.join(store_root, "repos.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            repos = json.load(fh).get("repos", {})
    except (OSError, json.JSONDecodeError):
        return {}
    return {rid[:12]: meta.get("label", rid[:12]) for rid, meta in repos.items()}


def _registry_path(store_root: str) -> str:
    return os.path.join(store_root, "repos.json")


def _registry_merge(store_root: str, found: dict[str, dict]) -> dict:
    path = _registry_path(store_root)
    have: dict = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                have = json.load(fh).get("repos", {})
        except (OSError, json.JSONDecodeError):
            have = {}
    have.update(found)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"schema": "cairn-repos/v1", "repos": have},
                  fh, ensure_ascii=False, indent=1, sort_keys=True)
    return have


def cmd_index(args) -> int:
    ad = load_adapter(args.adapter)
    store = Store(args.store)
    mf = manifest.load(store.manifest_path)

    files = ad.discover(args.root)
    if args.match:
        files = [f for f in files if args.match in P.norm(f)]
    print(f"журналів знайдено: {len(files)}")
    if not files:
        print("ПОРОЖНЬО: адаптер не знайшов журналів — індексувати нічого")
        return 2

    roots: dict[str, str] = {}
    cwd_cache: dict[str, str | None] = {}
    states = {manifest.NEW: 0, manifest.MATCH_APPENDED: 0,
              manifest.MATCH_SAME: 0, manifest.REWRITTEN: 0}
    records: list[dict] = []
    skipped_no_repo = 0
    ballast = 0
    redacted: dict[str, int] = {}
    registry: dict[str, dict] = {}

    for path in files:
        key = P.norm(path)
        state, offset = manifest.classify(path, mf.get(key))
        states[state] = states.get(state, 0) + 1
        if state == manifest.MATCH_SAME:
            continue

        turn = 0
        for ev in ad.read(path, offset):
            rid = _roots_for(ev.get("cwd", ""), cwd_cache, roots, registry)
            if not rid:
                skipped_no_repo += 1
                continue
            rel: list[str] = []
            for a in ev.get("abs_paths", []):
                hit = P.relativize(a, roots)
                if hit and hit[0] == rid and hit[1]:
                    rel.append(hit[1])

            turn += 1

            # БАЛАСТ. Виміряно 2026-11-18: із 102 905 записів лише 3 493
            # (3.4%) мали шляхи, а 95 170 були порожніми повідомленнями —
            # ні шляхів, ні тексту, 27 МБ нічого. Такі записи не несуть
            # причинності й не переносяться осмислено.
            if not rel and ev["kind"] != "compact_summary":
                ballast += 1
                continue

            rec = {
                "repo_id": rid,
                "session_id": ev["session_id"] or os.path.basename(path),
                "turn": turn,
                "ts": ev["ts"] or "1970-01-01T00:00:00Z",
                "actor": ev["actor"],
                "kind": ev["kind"],
                "paths": sorted(set(rel)),
                "agent": ad.NAME,
            }
            if ev.get("event_id"):
                rec["event_id"] = ev["event_id"]

            if ev["kind"] == "compact_summary":
                raw = ev.get("text") or ""
                clean, counts = redact.redact(raw)
                rec["text"] = clean
                for k, v in counts.items():
                    redacted[k] = redacted.get(k, 0) + v

            records.append(rec)

        mf[key] = manifest.snapshot(path)

    added, dup = store.append(records)
    manifest.save(store.manifest_path, mf)
    reg = _registry_merge(args.store, registry)

    print(f"стан журналів: {states}")
    print(f"подій зібрано: {len(records)}  (поза репозиторіями: {skipped_no_repo}, "
          f"баласту відкинуто: {ballast})")
    print(f"редаговано у скомпактований контекстх: {redacted or 'нічого не знайдено'}")
    print(f"у сховище додано: {added}, дублікатів пропущено: {dup}")
    print(f"сховище: {store.stats()}")
    print(f"репозиторіїв у реєстрі: {len(reg)}")
    for rid, meta in sorted(reg.items(), key=lambda kv: kv[1].get("label", "")):
        rc = meta.get("root_commits") or []
        print(f"   {rid[:12]}  {meta.get('label', '?'):<18} "
              f"коренів {len(rc)}: {' '.join(c[:8] for c in rc)}")
    return 0


def cmd_why(args) -> int:
    store = Store(args.store)
    target = P.norm(args.path)
    nodes_here: list[str] = []
    scope_rid: str | None = None

    # Якщо названо зону — питання про КОНКРЕТНИЙ продукт, і відповідати
    # сесіями з інших неправильно. Виміряно: `why --node … --zone beta.loc`
    # віддавав сесії community.loc і cc.one, бо відносний шлях у них
    # збігається (ядро розкладене копіями). Той самий дефект уже було
    # полагоджено в `bind`, а тут він лишався — латка в одному з двох
    # місць шляху.
    if args.zone and not args.all_repos:
        from bind import php_archivist as arch
        root = arch.arch_root(args.arch_root)
        zr = arch.zone_repo(root, args.zone) if root else None
        if zr:
            scope_rid = P.repo_id(P.repo_root(zr) or zr)

    # Адреса вузла — необовʼязковий шар. Якщо її передали, спершу
    # перекладаємо у шлях файла, бо сховище знає лише шляхи.
    if args.node:
        from bind import php_archivist as arch
        root = arch.arch_root(args.arch_root)
        if not root:
            print("не вказано корінь архіваріуса: --arch-root або "
                  f"змінна {arch.ENV_ROOT}", file=sys.stderr)
            return 2
        if not args.zone:
            print(f"потрібна --zone; доступні: {arch.zones(root)}", file=sys.stderr)
            return 2
        f = arch.file_of(args.node, root, args.zone)
        if not f:
            print(f"вузла {args.node} немає в індексі зони {args.zone}")
            print("(це відповідь, а не помилка: адреса могла протухнути "
                  "або зона не зіндексована)")
            return 1
        print(f"ВУЗОЛ {args.node}")
        print(f"  лежить у {f}  [зона {args.zone}]")
        print()
        target = P.norm(f)
    elif args.zone:
        # Зона без вузла: показати, які вузли має цей файл.
        from bind import php_archivist as arch
        root = arch.arch_root(args.arch_root)
        if root:
            try:
                nodes_here = arch.nodes_of(target, root, args.zone)
            except FileNotFoundError:
                nodes_here = []
    sessions: dict[str, dict] = {}
    for rec in store.iter_records():
        hit = any(p == target or p.endswith("/" + target) for p in rec.get("paths", []))
        if not hit:
            continue
        if args.repo and not rec["repo_id"].startswith(args.repo):
            continue
        if scope_rid and rec["repo_id"] != scope_rid:
            continue
        s = sessions.setdefault(rec["session_id"], {
            "first": rec["ts"], "last": rec["ts"], "touches": 0,
            "repo": rec["repo_id"][:12], "agent": rec.get("agent", "?"),
        })
        s["touches"] += 1
        s["first"] = min(s["first"], rec["ts"])
        s["last"] = max(s["last"], rec["ts"])

    if not sessions:
        print(f"НЕМАЄ слідів: {target}")
        if scope_rid:
            print(f"(звужено до репозиторію зони {args.zone}: {scope_rid[:12]}. "
                  f"Ширше — --all-repos)")
        print("(це відповідь, а не помилка: файла не торкались у жодній сесії)")
        return 1

    print(f"ФАЙЛ {target}")
    if nodes_here:
        print(f"вузлів архіваріуса у файлі: {len(nodes_here)}")
        for a in nodes_here[:8]:
            print(f"   {a}")
        if len(nodes_here) > 8:
            print(f"   … ще {len(nodes_here) - 8}")
    print(f"сесій, що торкались: {len(sessions)}")
    print()
    # Один і той самий ВІДНОСНИЙ шлях існує в кількох продуктах, коли ядро
    # розкладене копіями. Без колонки репозиторію це читалося б як історія
    # одного файла, а насправді це історія кількох різних.
    labels = _registry_labels(args.store)
    repos_seen = {s["repo"] for s in sessions.values()}
    if len(repos_seen) > 1:
        print("УВАГА: цей шлях існує в КІЛЬКОХ репозиторіях — дивись колонку "
              "«проєкт». Звузити: --repo <перші символи id>")
        print()

    print(f"{'дотиків':>8}  {'від':<20} {'до':<20} {'проєкт':<14} сесія")
    for sid, s in sorted(sessions.items(), key=lambda kv: -kv[1]["touches"]):
        lab = labels.get(s["repo"], s["repo"])
        print(f"{s['touches']:>8}  {s['first'][:19]:<20} {s['last'][:19]:<20} "
              f"{lab[:14]:<14} {sid[:8]} [{s['agent']}]")

    # Скомпактовані контексти тієї ж сесії — саме вони несуть «чому», і саме
    # вони роблять сховище самодостатнім на чужій машині.
    comp = [r for r in store.iter_records()
            if r.get("kind") == "compact_summary" and r["session_id"] in sessions]
    print()
    print(f"скомпактованих контекстів у цих сесіях: {len(comp)}")
    if not comp or args.summaries == 0:
        return 0

    # Блоки ГОТУЮТЬСЯ, але не друкуються: спершу оцінка, потім рішення.
    # Порядок обернений до звичайного саме тому, що інакше дорогу роботу
    # вже зроблено, а межа перетворюється на побажання.
    chosen = sorted(comp, key=lambda x: x["ts"])[:args.summaries]
    blocks: list[tuple[str, str]] = []
    for r in chosen:
        txt = (r.get("text") or "").strip()
        head = (f"--- {r['ts'][:19]}  сесія {r['session_id'][:8]}  "
                f"({len(txt)} символів)\n")
        if not txt:
            body = "    (тексту немає: сховище зібране без нього)\n"
        else:
            lines = txt.splitlines()[:args.lines]
            body = "".join("    " + ln[:110] + "\n" for ln in lines)
            rest = len(txt.splitlines()) - args.lines
            if rest > 0:
                body += f"    … ще {rest} рядків\n"
        blocks.append((r["session_id"][:8], head + body))

    bud = budget.budget_for(args.priority, args.budget)
    est = budget.estimate([(n, len(t)) for n, t in blocks])
    dec = budget.decide(est, bud, args.on_overflow)

    print()
    print(budget.report(dec))

    if dec.policy == "ask" and not dec.fits and not args.yes:
        print()
        print("  нічого не надруковано. Рішення за вами:")
        print(f"    --yes                показати все ({est.chars} символів)")
        print(f"    --on-overflow=trim   обрізати до {bud}")
        print(f"    --budget={est.chars}      підняти межу під цей запит")
        print("    --summaries 1        узяти менше скомпактованих контекстів")
        return 0

    allowed = est.chars if (args.yes or dec.policy == "allow") else dec.allowed_chars
    taken, skipped = budget.fit(blocks, allowed)

    print()
    for _, text in taken:
        print(text, end="")

    if skipped and not taken:
        # Жоден скомпактований контекст не вмістився ЦІЛКОМ. Частковий блок не
        # віддається навмисно: обрізаний посередині текст виглядає
        # повним і тому бреше. Тому тут потрібен важіль, що зменшує
        # САМІ блоки, а не їхню кількість.
        print(f"  ЖОДЕН із {len(skipped)} скомпактованих контекстів не вмістився цілком.")
        print(f"  найменша важить більше за {dec.allowed_chars} символів.")
        print("  зменшити блок: --lines 8    підняти межу: --priority high")
    elif skipped:
        print(f"  ПРОПУЩЕНО через бюджет: {len(skipped)} ({', '.join(skipped)})")
        print("  ширше: --priority high | --budget <число> | --on-overflow=allow")
    return 0


def cmd_merge(args) -> int:
    """Синхронізація між пристроями: додати все, чого тут немає."""
    store = Store(args.store)
    before = store.stats()

    res = store.merge_from(args.other)

    # Реєстр зливаємо окремо: без нього ключі з чужого сховища лишаться
    # непрозорими числами, і проєкт неможливо буде назвати.
    other_reg_path = os.path.join(args.other, "repos.json")
    found: dict[str, dict] = {}
    if os.path.exists(other_reg_path):
        try:
            with open(other_reg_path, "r", encoding="utf-8") as fh:
                found = json.load(fh).get("repos", {})
        except (OSError, json.JSONDecodeError):
            found = {}
    reg = _registry_merge(args.store, found)

    after = store.stats()
    print(f"злито з: {args.other}")
    print(f"  додано:       {res['added']}")
    print(f"  вже було:     {res['duplicates']}")
    print(f"  відхилено:    {res['rejected']}")
    print(f"записів: {before['records']} -> {after['records']}")
    print(f"сесій:   {before['sessions']} -> {after['sessions']}")
    print(f"репозиторіїв у реєстрі: {len(reg)}")
    return 0


def cmd_bind(args) -> int:
    """Скільки гарячих вузлів зони мають скомпактований контекст — і чи варта звʼязка роботи.

    Це вимір, а не показ: без нього «ми привʼязали скомпактовані контексти до вузлів»
    лишається обіцянкою. Відповідь «покриття нульове» теж є відповіддю.
    """
    from bind import php_archivist as arch

    root = arch.arch_root(args.arch_root)
    if not root:
        print(f"не вказано корінь архіваріуса: --arch-root або {arch.ENV_ROOT}",
              file=sys.stderr)
        return 2

    by_file, by_addr = arch.load(root, args.zone)

    # C вузла беремо з heat.tsv: це вимір архіваріуса, не наш.
    heat: dict[str, int] = {}
    with open(arch.heat_path(root, args.zone), "r",
              encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            col = ln.rstrip("\n").split("\t")
            if len(col) < 5:
                continue
            for chunk in col[4].split(";"):
                parts = chunk.split("|")
                if len(parts) >= 2 and parts[1].isdigit():
                    heat[parts[0]] = max(heat.get(parts[0], 0), int(parts[1]))

    # Зона належить КОНКРЕТНОМУ репозиторію. Без цієї фільтрації однакові
    # відносні шляхи з інших продуктів зараховувались би цій зоні.
    zone_root = arch.zone_repo(root, args.zone)
    zone_rid = P.repo_id(P.repo_root(zone_root) or zone_root) if zone_root else None
    if not zone_rid:
        print(f"не вдалося визначити репозиторій зони {args.zone} "
              f"(zone.json -> repo). Без нього числа були б чужі.",
              file=sys.stderr)
        return 2
    print(f"репозиторій зони: {zone_rid[:12]}  ({zone_root})")

    store = Store(args.store)
    touched, summaries = store.touched(zone_rid)

    if not touched and not summaries:
        print(f"у сховищі НЕМАЄ жодного запису цього репозиторію.")
        print("Покриття буде нульовим — це відповідь, а не збій: "
              "сесій у цьому проєкті не зафіксовано.")

    top = sorted(heat.items(), key=lambda kv: -kv[1])[:args.top]
    print(f"ЗОНА {args.zone}   вузлів у індексі {len(heat)}   "
          f"файлів {len(by_file)}")
    print()
    print(f"{'C':>4}  {'сесій':>6}  {'скомпактованих контекстів':>8}  вузол")
    with_sess = with_summ = 0
    for addr, c in top:
        f = by_addr.get(addr, "")
        sess = touched.get(f, set())
        nsum = sum(summaries.get(s, 0) for s in sess)
        if sess:
            with_sess += 1
        if nsum:
            with_summ += 1
        print(f"{c:>4}  {len(sess):>6}  {nsum:>8}  {addr[:72]}")

    print()
    print(f"ПОКРИТТЯ з {len(top)} найгарячіших:")
    print(f"  мають сесію:  {with_sess}")
    print(f"  мають скомпактований контекст: {with_summ}")
    if with_summ == 0:
        print("  -> звʼязка для цієї зони НІЧОГО не додає")
    print()
    print("МЕЖА: звʼязок СЕСІЙНИЙ. Колонка «скомпактованих контекстів» означає «стільки")
    print("      скомпактованих контекстів у сесіях, що торкались файла цього вузла», а НЕ")
    print("      «стільки скомпактованих контекстів про цей вузол». Сесія чіпає багато файлів.")
    return 0


def cmd_stats(args) -> int:
    print(json.dumps(Store(args.store).stats(), ensure_ascii=False, indent=1))
    return 0


def cmd_selftest(args) -> int:
    from core.selftest import run
    return run(args.store, args.adapter, args.root)


def main() -> int:
    ap = argparse.ArgumentParser(prog="cairn")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("index", "selftest"):
        p = sub.add_parser(name)
        p.add_argument("--store", required=True)
        p.add_argument("--adapter", default="claude_code")
        p.add_argument("--root", default=None)
        if name == "index":
            p.add_argument("--match", default=None,
                           help="індексувати лише журнали, чий шлях містить це")
        p.set_defaults(fn=cmd_index if name == "index" else cmd_selftest)

    p = sub.add_parser("why")
    p.add_argument("--store", required=True)
    p.add_argument("path", nargs="?", default="",
                   help="шлях у репозиторії; не потрібен із --node")
    p.add_argument("--node", default=None,
                   help="адреса вузла архіваріуса, напр. 'class:Ns\\Class'")
    p.add_argument("--zone", default=None, help="зона архіваріуса")
    p.add_argument("--arch-root", default=None,
                   help="корінь архіваріуса (або CAIRN_ARCH_ROOT)")
    p.add_argument("--repo", default=None,
                   help="звузити до репозиторію: перші символи repo_id")
    p.add_argument("--budget", type=int, default=None,
                   help="тверда межа виводу в СИМВОЛАХ")
    p.add_argument("--priority", default=None,
                   choices=["low", "normal", "high", "max"],
                   help="бюджет за пріоритетом задачі, якщо число не задано")
    p.add_argument("--on-overflow", default="trim",
                   choices=["trim", "ask", "allow"],
                   help="що робити, коли оцінка перевищує бюджет")
    p.add_argument("--yes", action="store_true",
                   help="підтвердити перевищення для --on-overflow=ask")
    p.add_argument("--all-repos", action="store_true",
                   help="не звужувати до репозиторію зони: показати всі "
                        "продукти, де є такий самий відносний шлях")
    p.add_argument("--summaries", type=int, default=2,
                   help="скільки скомпактованих контекстів показати (0 — лише лічильник)")
    p.add_argument("--lines", type=int, default=12,
                   help="рядків з кожної скомпактовані контексти")
    p.set_defaults(fn=cmd_why)

    p = sub.add_parser("stats")
    p.add_argument("--store", required=True)
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("bind")
    p.add_argument("--store", required=True)
    p.add_argument("--zone", required=True)
    p.add_argument("--arch-root", default=None)
    p.add_argument("--top", type=int, default=12)
    p.set_defaults(fn=cmd_bind)

    p = sub.add_parser("merge")
    p.add_argument("--store", required=True, help="куди зливаємо")
    p.add_argument("--other", required=True, help="сховище з іншого пристрою")
    p.set_defaults(fn=cmd_merge)

    args = ap.parse_args()
    try:
        return args.fn(args)
    except contract.ContractError as exc:
        print(f"КОНТРАКТ ПОРУШЕНО: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
