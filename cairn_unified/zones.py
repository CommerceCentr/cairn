"""Дескриптори зон і почерговий, неблокуючий запис кількох агентів.

Одна зона = один дескрипторний файл + один лок-файл + один журнал.
Різні зони не ділять жодного блокування, тому агент у `beta.loc` не
чекає на агента в `cc.one`. Два агенти в одній зоні не блокуються
назавжди: другий отримує `status="deferred"` із поточним `head`/`fence`
і повторює спробу, коли перший звільнить лок або спливе TTL.

Запис захищено fence: прострочений агент не перезапише чужий рядок,
а отримає `status="stale_fence"`. Журнал append-only, `head` монотонний.

Нових таблиць БД немає: дескриптори й журнали — файли під `.cairn`.
Семантичні об'єкти в сховищі лише публікують уже зроблений факт.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .canon import audit_digest, canonical, iso, sha256_text
from . import federation, ledger, semantic

DEFAULT_TTL_S = 30.0
PROTOCOL = "cairn_unified.zones/1.0"


def descriptors_dir(root: str | Path) -> Path:
    return Path(root) / ".cairn" / "runtime" / "zones"


def journals_dir(root: str | Path) -> Path:
    return Path(root) / ".cairn" / "zones"


def descriptor_path(root: str | Path, zone: str) -> Path:
    return descriptors_dir(root) / f"{zone}.json"


def lock_path(root: str | Path, zone: str) -> Path:
    return descriptors_dir(root) / f"{zone}.lock"


def journal_path(root: str | Path, zone: str) -> Path:
    return journals_dir(root) / zone / "journal.jsonl"


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


class _NonBlockingLock:
    """Per-zone lock; never waits for another holder."""

    def __init__(self, path: Path):
        self.path = path
        self.fh = None

    def __enter__(self) -> "_NonBlockingLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+b")
        # msvcrt.locking блокує байти починаючи з поточної позиції. Без
        # явного seek(0) два процеси можуть заблокувати РІЗНі байти й обидва
        # вважати, що лок у них. Виміряно: недетерміновані deferred/stale.
        self.fh.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - POSIX path, exercised on CI
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            self.fh = None
        return self

    def __exit__(self, *exc) -> None:
        if self.fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        finally:
            self.fh.close()
            self.fh = None

    @property
    def held(self) -> bool:
        return self.fh is not None


def iso_of_ms(ms: int | float | None) -> str | None:
    """epoch-ms -> ISO-8601 ms Z. Один хелпер, щоб конверсія була одна.

    Арифметика оренди лишається в мілісекундах; ISO потрібен для порівняння
    з журналом подій, де час лише в ISO. Без цього кожен читач конвертував
    би сам, і рано чи пізно по-різному.
    """
    if ms is None:
        return None
    return iso(datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc))


def object_refs(store, ids: list[str]) -> list[dict]:
    """Відносні посилання на семантичні об'єкти всередині cairn.

    Абсолютних шляхів немає: на іншій машині той самий об'єкт знаходиться
    за тим самим відносним шляхом у `.cairn/objects/...`.
    """
    refs: list[dict] = []
    for oid in ids:
        row = store.read_one("SELECT object_type FROM objects WHERE id = ?", (oid,))
        if not row:
            continue
        rel = f".cairn/objects/{row['object_type']}/{oid}.json"
        if (Path(store.root) / rel).is_file():
            refs.append({"object_id": str(oid), "path": rel})
    return refs


def descriptor(store, zone: str, *, repo: str, pathspec: str,
               actor: dict, context_refs: list[dict] | None = None,
               context_identity: dict | None = None) -> dict:
    """Створити (або прочитати) дескриптор однієї зони."""
    root = store.root
    path = descriptor_path(root, zone)
    with _NonBlockingLock(lock_path(root, zone)) as lock:
        if not lock.held:
            current = _read(path)
            if current:
                return {"ok": False, "status": "deferred", "descriptor": current}
        current = _read(path)
        if current is None:
            current = {
                "protocol": PROTOCOL,
                "zone": zone,
                "repo": repo,
                "pathspec": pathspec,
                "head": 0,
                "fence": 0,
                "owner": None,
                "lease_expires_ms": None,
                "created_by": str(actor.get("id") or ""),
                "created_at_ms": int(time.time() * 1000),
                "created_at": iso_of_ms(time.time() * 1000),
                "context_refs": [],
                "context_identity": {},
            }
        if context_refs is not None:
            current["context_refs"] = context_refs
        if context_identity is not None:
            current["context_identity"] = context_identity
        _write(path, current)
        return {"ok": True, "status": "ready", "descriptor": current}


def acquire(store, zone: str, actor: dict, *, ttl_s: float = DEFAULT_TTL_S,
            now_ms: int | None = None) -> dict:
    """Неблокуючий захват зони. Другий агент отримує `deferred`, не виняток."""
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    root = store.root
    lock = _NonBlockingLock(lock_path(root, zone))
    lock.__enter__()
    if not lock.held:
        current = _read(descriptor_path(root, zone)) or {}
        holder = str(current.get("owner") or "")
        expires = int(current.get("lease_expires_ms") or 0)
        return {"ok": False, "status": "deferred", "holder": holder,
                "retry_after_ms": max(0, expires - now) or 1,
                "head": int(current.get("head") or 0),
                "fence": int(current.get("fence") or 0)}
    try:
        current = _read(descriptor_path(root, zone))
        if current is None:
            lock.__exit__()
            return {"ok": False, "status": "unknown_zone", "zone": zone}
        expires = int(current.get("lease_expires_ms") or 0)
        owner = str(current.get("owner") or "")
        if owner and owner != str(actor.get("id") or "") and expires > now:
            return {"ok": False, "status": "deferred", "holder": owner,
                    "retry_after_ms": expires - now,
                    "head": int(current.get("head") or 0),
                    "fence": int(current.get("fence") or 0)}
        current["fence"] = int(current.get("fence") or 0) + 1
        current["owner"] = str(actor.get("id") or "")
        current["lease_expires_ms"] = now + int(ttl_s * 1000)
        current["lease_expires_at"] = iso_of_ms(current["lease_expires_ms"])
        _write(descriptor_path(root, zone), current)
        return {"ok": True, "status": "acquired", "fence": current["fence"],
                "head": int(current["head"]), "descriptor": current}
    finally:
        lock.__exit__()


def append(store, zone: str, actor: dict, fence: int, payload: dict) -> dict:
    """Один запис у журнал зони з CAS за fence."""
    root = store.root
    lock = _NonBlockingLock(lock_path(root, zone))
    lock.__enter__()
    if not lock.held:
        current = _read(descriptor_path(root, zone)) or {}
        return {"ok": False, "status": "deferred",
                "head": int(current.get("head") or 0),
                "fence": int(current.get("fence") or 0)}
    try:
        path = descriptor_path(root, zone)
        current = _read(path)
        if current is None:
            return {"ok": False, "status": "unknown_zone", "zone": zone}
        owner = str(current.get("owner") or "")
        expires = int(current.get("lease_expires_ms") or 0)
        actor_id = str(actor.get("id") or "")
        if owner and owner != actor_id and expires > int(time.time() * 1000):
            # Зона в оренді іншим агентом: не помилка й не блокування,
            # а почергова відмова з явним часом повтору.
            return {"ok": False, "status": "deferred", "holder": owner,
                    "retry_after_ms": expires - int(time.time() * 1000),
                    "head": int(current.get("head") or 0),
                    "fence": int(current.get("fence") or 0)}
        if int(current.get("fence") or 0) != int(fence) or owner != actor_id:
            return {"ok": False, "status": "stale_fence",
                    "head": int(current.get("head") or 0),
                    "fence": int(current.get("fence") or 0)}
        seq = int(current.get("head") or 0) + 1
        jpath = journal_path(root, zone)
        jpath.parent.mkdir(parents=True, exist_ok=True)
        row = {"seq": seq, "zone": zone, "fence": int(fence),
               "actor": str(actor.get("id") or ""),
               "ts_ms": int(time.time() * 1000),
               "ts": iso_of_ms(time.time() * 1000), "payload": payload}
        with open(jpath, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        current["head"] = seq
        _write(path, current)
        return {"ok": True, "status": "appended", "seq": seq,
                "journal": str(jpath.relative_to(root)).replace("\\", "/")}
    finally:
        lock.__exit__()


def release(store, zone: str, actor: dict, fence: int) -> dict:
    """Явне звільнення: збільшує fence, тому старий власник більше не пише."""
    root = store.root
    lock = _NonBlockingLock(lock_path(root, zone))
    lock.__enter__()
    if not lock.held:
        return {"ok": False, "status": "deferred", "zone": zone}
    try:
        path = descriptor_path(root, zone)
        current = _read(path)
        if current is None:
            return {"ok": False, "status": "unknown_zone", "zone": zone}
        if int(current.get("fence") or 0) != int(fence) or \
                str(current.get("owner") or "") != str(actor.get("id") or ""):
            return {"ok": False, "status": "stale_fence",
                    "fence": int(current.get("fence") or 0)}
        current["fence"] = int(fence) + 1
        current["owner"] = None
        current["lease_expires_ms"] = None
        current["lease_expires_at"] = None
        _write(path, current)
        return {"ok": True, "status": "released", "fence": current["fence"],
                "head": int(current["head"])}
    finally:
        lock.__exit__()


def read_journal(store, zone: str) -> list[dict]:
    path = journal_path(store.root, zone)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def snapshot(store, zones: list[str]) -> list[dict]:
    """Зріз дескрипторів для контексту; journal_lines — фактичний обсяг записів."""
    out = []
    for zone in zones:
        d = _read(descriptor_path(store.root, zone))
        if d is None:
            continue
        rows = read_journal(store, zone)
        out.append({
            "zone": d["zone"], "repo": d.get("repo"), "pathspec": d.get("pathspec"),
            "head": int(d.get("head") or 0), "fence": int(d.get("fence") or 0),
            "owner": d.get("owner"), "journal_lines": len(rows),
            "lock_file": str(lock_path(store.root, zone).relative_to(store.root)).replace("\\", "/"),
            "descriptor_file": str(descriptor_path(store.root, zone).relative_to(store.root)).replace("\\", "/"),
        })
    return out


def export_bundle(store, zones: list[str]) -> dict:
    """Самоописний bundle для перенесення стану зон між сховищами.

    Усередині — лише відносні шляхи `.cairn/...`, ідентичність репозиторію
    (не абсолютний шлях), дескриптори й повні журнали. Радіус перенесення
    видно з digest: той самий набір файлів дає той самий bundle.
    """
    descriptors = []
    journals: dict[str, list[dict]] = {}
    context_objects: dict[str, dict] = {}
    for zone in zones:
        d = _read(descriptor_path(store.root, zone))
        if d is None:
            continue
        descriptors.append(d)
        journals[zone] = read_journal(store, zone)
        for ref in d.get("context_refs") or []:
            rel = str(ref.get("path") or "")
            target = Path(store.root) / rel
            if rel.startswith(".cairn/objects/") and target.is_file():
                context_objects[rel] = json.loads(target.read_text(encoding="utf-8"))
    # Повний причинно-наслідковий контекст — це не лише об'єкти, а й події
    # з ребрами caused_by/mutated/derived_from. Їх несе federation-бандл із
    # перевіреним hash-ланцюжком; без нього на іншому пристрої були б факти
    # без історії. Шляхи всередині — відносні, `.cairn/...`.
    fed = federation.export_bundle(store)
    bundle = {
        "protocol": PROTOCOL,
        "portable": True,
        # Лише ПОСИЛАННЯ відносні. Рядки-докази (opencode_db, repo_roots,
        # шляхи відкинутих мутацій) лишаються як історичні свідчення;
        # називати їх «без абсолютних шляхів» було б перебільшенням.
        "relative_refs_only": True,
        "descriptors": descriptors,
        "journals": journals,
        "context_objects": context_objects,
        "cairn_bundle": {
            "format": fed["format"], "manifest": fed["manifest"],
            "events": fed["events"], "event_count": fed["event_count"],
            "chain_ok": fed["chain_ok"], "digest": fed["digest"],
        },
        "causal_event_count": len([
            e for e in fed["events"] if e.get("event_type") == "relation.recorded"]),
    }
    bundle["digest"] = sha256_text(json.dumps(
        bundle, ensure_ascii=False, sort_keys=True))
    return bundle


def state_digest(bundle: dict) -> str:
    """Відтисок стану зон без federation-подій.

    Federation-бандл змінюється вже після того, як об'єкт дескрипторів
    записаний (запис самого об'єкта — нова подія). Звіряти повний digest
    було б звіркою об'єкта зі зміненим ним самим світом. Тому тотожність
    зони — це descriptors + journals + context_objects; події перевіряються
    окремо за chain_ok і монотонністю кількості.
    """
    # Вміст об'єктів НЕ входить у відтисок: верифікатор підвищує довіру
    # сесії й «чому відкинуто» до verified_system, тіло файлу міняється,
    # а тотожність зони має лишатися тією самою. Зміст перевіряється
    # окремо за object_id у zone_context_refs_resolve.
    return sha256_text(json.dumps({
        "descriptors": bundle.get("descriptors") or [],
        "journals": bundle.get("journals") or {},
        "context_paths": sorted((bundle.get("context_objects") or {}).keys()),
    }, ensure_ascii=False, sort_keys=True))


def import_bundle(store, bundle: dict, *, actor: dict) -> dict:
    """Відтворити bundles у свіжому сховищі; хибний digest відхиляється."""
    if bundle.get("protocol") != PROTOCOL:
        return {"ok": False, "status": "bad_protocol"}
    audit = dict(bundle)
    audit.pop("digest", None)
    if audit_digest(audit) \
            != bundle.get("digest"):
        return {"ok": False, "status": "bad_digest"}
    causal_events = 0
    fed = bundle.get("cairn_bundle")
    if fed is not None:
        checked = federation.verify_bundle(fed)
        if not checked["ok"]:
            return {"ok": False, "status": "bad_causal_chain",
                    "problems": checked.get("problems")}
        lp = ledger.ledger_path(Path(store.root))
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text("".join(canonical(e) + "\n" for e in (fed.get("events") or [])),
                      encoding="utf-8", newline="\n")
        store.rebuild()
        causal_events = len(fed.get("events") or [])

    written = 0
    reset_stamps = 0
    for rel, payload in (bundle.get("context_objects") or {}).items():
        if not str(rel).startswith(".cairn/objects/"):
            return {"ok": False, "status": "bad_context_ref", "path": rel}
        # Імпорт не успадковує довіру джерела. Штамп `verified_system` з іншої
        # машини зробив би `verify_object` коротким замиканням: він повернув би
        # verified=True, навіть якщо opencode.db тут недоступний. Знижуємо до
        # imported_untrusted і викидаємо verification, щоб наступна перевірка
        # справді перечитала джерела (verification.check_object).
        incoming = dict(payload)
        old_verification = incoming.pop("verification", None)
        incoming["trust_class"] = "imported_untrusted"
        incoming["review_state"] = "unreviewed"
        incoming["updated_at"] = store.now()
        sem = dict(incoming.get("semantic") or {})
        sem["import_provenance"] = {
            "source_bundle_digest": bundle["digest"],
            "source_trust_class": str(payload.get("trust_class") or ""),
            "had_verification": bool(old_verification),
            "imported_at": store.now(),
            "policy": "import-does-not-inherit-trust/1.0",
        }
        incoming["semantic"] = sem
        reset_stamps += 1
        target = Path(store.root) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(incoming, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    for d in bundle.get("descriptors") or []:
        zone = str(d.get("zone") or "")
        if not zone or "/" in zone or "\\" in zone:
            return {"ok": False, "status": "bad_zone_name", "zone": zone}
        for ref in d.get("context_refs") or []:
            rel = str(ref.get("path") or "")
            if not rel.startswith(".cairn/objects/") or \
                    rel not in (bundle.get("context_objects") or {}):
                return {"ok": False, "status": "dangling_context_ref", "path": rel}
        _write(descriptor_path(store.root, zone), d)
        rows = (bundle.get("journals") or {}).get(zone) or []
        jpath = journal_path(store.root, zone)
        jpath.parent.mkdir(parents=True, exist_ok=True)
        jpath.write_text("".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows
        ), encoding="utf-8")
        written += len(rows)
    return {"ok": True, "status": "imported",
            "zones": len(bundle.get("descriptors") or []), "journal_lines": written,
            "context_objects": len(bundle.get("context_objects") or {}),
            "reset_verification_stamps": reset_stamps,
            "causal_events": causal_events, "digest": bundle["digest"]}


def context_object(store, *, zones: list[str], actor: dict,
                   commit: str = "", scope: list[str] | None = None
                   ) -> tuple[dict, list[dict]]:
    """Опублікувати дескриптори як семантичний об'єкт Cairn."""
    rows = snapshot(store, zones)
    bundle = export_bundle(store, zones)
    state = state_digest(bundle)
    digest = audit_digest(rows)
    text = "\n".join(
        ["Дескриптори зон Cairn: одна зона = один файл + один лок + один журнал.",
         f"Протокол: {PROTOCOL}. Коміт: {commit[:12] or '—'}.",
         "Різні зони не блокують одна одну; у спільній зоні другий агент",
         "отримує deferred, а не блокування, і повторює спробу за head/fence.",
         "Прострочений fence відхиляється (stale_fence), журнал append-only.",
         "Portability: дескриптори й журнали переносяться bundle-ом усередині",
         f"цього репозиторію (.cairn, під gitignore), відносні ПОСИЛАННЯ; стан {state[:12]}.",
         "Абсолютні шляхи в payload — історичні докази, а не посилання.",
          "Дескриптор несе відносні посилання на сесію/summary; разом із",
         f"bundle переносяться й самі об'єкти ({len(bundle['context_objects'])}).",
         f"Причинні ребра йдуть у federation-бандлі: {bundle['causal_event_count']} подій,",
         f"ланцюжок {bundle['cairn_bundle']['digest'][:12]}; імпорт знижує чужу довіру",
         "до imported_untrusted: без opencode.db перевірка ескалує, а не видає перенесене за підтверджене.",
         f"Зон: {len(rows)}. Всього рядків журналів: {sum(r['journal_lines'] for r in rows)}.",
         "",]
        + [f"[{r['zone']}] head={r['head']} fence={r['fence']} "
           f"owner={r['owner'] or '—'} lines={r['journal_lines']} "
           f"descriptor={r['descriptor_file']}" for r in rows]
    ) + "\n"
    obj = semantic.put_object(
        store, object_type="observation",
        title="zone descriptors and non-blocking multi-agent protocol",
        summary=text, actor=actor, lifecycle="proposed", trust="agent_proposed",
        stable_key="zone-descriptors-v1-" + sha256_text(canonical(
        {"snapshot": digest, "bundle": state})),
        idempotency_key=f"zone-descriptors:{digest}",
        semantic={"kind": "zone_descriptors", "protocol": PROTOCOL,
                  "commit": commit, "zones": rows, "digest": digest,
                  "portable": True, "relative_refs_only": True,
                  "bundle_digest": state,
                  "context_objects": len(bundle["context_objects"]),
                  "causal_event_count": bundle["causal_event_count"],
                  "causal_chain_digest": bundle["cairn_bundle"]["digest"],
                  "no_new_db_tables": True, "db_schema_version": 1},
        scope=sorted(set([f"zone:{z}" for z in zones] + list(scope or []))),
    )["object"]
    return obj, rows
