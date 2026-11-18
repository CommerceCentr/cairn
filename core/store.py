"""Сховище: тека з JSONL, яку можна просто скопіювати на іншу машину.

Формат навмисно нудний — рядок JSON на запис. Жодної бази, жодного
демона, жодного формату, який через рік ніхто не відкриє. Перенесення =
копіювання теки; злиття = конкатенація з відкиданням дублікатів.

Ключ дедуплікації — (repo_id, session_id, turn, kind, пакет шляхів).
Тому повторний прогін збирача нічого не дублює, а два сховища з різних
машин зливаються без звірки годинників.
"""

from __future__ import annotations

import hashlib
import json
import os

from . import contract

REPOS = "repos.json"

RECORDS = "records.jsonl"
MANIFEST = "manifest.json"
CONFIG = "config.json"


def key_of(rec: dict) -> str:
    """Адреса запису. Від неї залежить злиття сховищ із різних пристроїв.

    ЧОМУ НЕ ПОРЯДКОВИЙ НОМЕР. Виміряно 2026-11-18: `turn` обнуляється на
    кожному файлі й ЩЕ РАЗ на хвості при інкрементному читанні. Одна й та
    сама подія діставала різний номер залежно від того, звідки читали
    журнал — отже й різний ключ, отже дублікат при злитті.

    Тому ключ будується з ВЛАСНОГО ідентифікатора події агента, якщо той
    його дає. Запасний варіант — час і зміст; він слабший (дві події з
    тим самим часом і тими самими шляхами склеяться), і це названа межа,
    а не непомічений випадок.
    """
    eid = rec.get("event_id")
    if eid:
        raw = "\x1f".join([rec["repo_id"], rec["session_id"], str(eid)])
    else:
        raw = "\x1f".join([
            rec["repo_id"], rec["session_id"], rec["ts"], rec["kind"],
            "\x1e".join(sorted(rec.get("paths", []))),
        ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class Store:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.records_path = os.path.join(root, RECORDS)
        self.manifest_path = os.path.join(root, MANIFEST)

    def existing_keys(self) -> set[str]:
        keys: set[str] = set()
        if not os.path.exists(self.records_path):
            return keys
        with open(self.records_path, "r", encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    keys.add(json.loads(ln)["_k"])
                except Exception:
                    continue
        return keys

    def append(self, records: list[dict]) -> tuple[int, int]:
        """(додано, пропущено як дублікат). Кожен запис звіряється з контрактом
        ПЕРЕД записом: сховище не приймає того, чого не прийняли б ворота."""
        seen = self.existing_keys()
        added = dup = 0
        with open(self.records_path, "a", encoding="utf-8", newline="\n") as fh:
            for i, rec in enumerate(records):
                contract.check_record(rec, i)
                k = key_of(rec)
                if k in seen:
                    dup += 1
                    continue
                seen.add(k)
                out = dict(rec)
                out["_k"] = k
                fh.write(json.dumps(out, ensure_ascii=False, sort_keys=True) + "\n")
                added += 1
        return added, dup

    def iter_records(self):
        if not os.path.exists(self.records_path):
            return
        with open(self.records_path, "r", encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln:
                    try:
                        yield json.loads(ln)
                    except json.JSONDecodeError:
                        continue

    def merge_from(self, other_root: str) -> dict:
        """Злити сховище з іншого пристрою. Нічого не перезаписує.

        Злиття тут — не «синхронізація» у сенсі двостороннього обміну, а
        ДОДАВАННЯ відсутнього. Записи незмінні за побудовою (подія вже
        сталася), тож конфліктів редакцій не буває: запис або є, або ні.

        Звідси головна властивість: повторне злиття нічого не змінює.
        Її і перевіряє тест.
        """
        src = os.path.join(other_root, RECORDS)
        if not os.path.exists(src):
            raise FileNotFoundError(f"у {other_root} немає {RECORDS}")

        seen = self.existing_keys()
        added = dup = bad = 0
        with open(self.records_path, "a", encoding="utf-8", newline="\n") as out:
            with open(src, "r", encoding="utf-8") as fh:
                for i, ln in enumerate(fh):
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        rec = json.loads(ln)
                    except json.JSONDecodeError:
                        bad += 1
                        continue
                    try:
                        contract.check_record(rec, i)
                    except contract.ContractError:
                        # Чуже сховище могло бути зібране іншим адаптером
                        # або старішою редакцією. Приймати непридатне
                        # означало б зробити власне сховище непридатним.
                        bad += 1
                        continue
                    k = key_of(rec)
                    if k in seen:
                        dup += 1
                        continue
                    seen.add(k)
                    rec["_k"] = k
                    out.write(json.dumps(rec, ensure_ascii=False,
                                         sort_keys=True) + "\n")
                    added += 1
        return {"added": added, "duplicates": dup, "rejected": bad}

    def touched(self, repo_id: str) -> tuple[dict[str, set[str]], dict[str, int]]:
        """(шлях -> сесії, сесія -> скільки згорток) ОДНОГО репозиторію.

        Фільтр за repo_id тут не осторога, а умова правильності. Виміряно
        2026-11-18: той самий відносний шлях існує в кількох продуктах,
        бо ядро розкладене копіями. Без фільтра зона beta.loc, яка не має
        жодної сесії, показувала 5 із 8 покритих вузлів — а ті дотики
        належали cc.one і community.loc.
        """
        touched: dict[str, set[str]] = {}
        summaries: dict[str, int] = {}
        for rec in self.iter_records():
            if rec.get("repo_id") != repo_id:
                continue
            sid = rec["session_id"]
            for p in rec.get("paths", []):
                touched.setdefault(p, set()).add(sid)
            if rec.get("kind") == "compact_summary":
                summaries[sid] = summaries.get(sid, 0) + 1
        return touched, summaries

    def stats(self) -> dict:
        n = 0
        repos: set[str] = set()
        sessions: set[str] = set()
        paths: set[str] = set()
        compacts = 0
        for rec in self.iter_records():
            n += 1
            repos.add(rec.get("repo_id", ""))
            sessions.add(rec.get("session_id", ""))
            paths.update(rec.get("paths", []))
            if rec.get("kind") == "compact_summary":
                compacts += 1
        return {"records": n, "repos": len(repos), "sessions": len(sessions),
                "paths": len(paths), "compact_summaries": compacts}
