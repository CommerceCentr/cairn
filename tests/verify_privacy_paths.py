r"""Регресія: домашня тека не їде в сховище, посилання лишаються робочими.

Виміряний дефект (2026-11-21): `redact.py` оголошував три шаблони машинних
шляхів і всі три пропускав через `SKIP`. У приватному сховищі лежало 220
рядків із домашньою текою користувача — у `summary`, `condition.rationale`,
`semantic.unresolved_rows` і `semantic.repo_roots` — і вони їхали в кожен
експортований bundle.

Тут перевіряються ОБА боки межі, бо вони лікуються по-різному:

* ТЕКСТ (`summary`, `rationale`, `intent`) — домашня тека згортається до `~`:
  імʼя зникає, шлях лишається читним;
* ПОСИЛАННЯ (`path`, `repo_roots`, `packs_dir`) — НЕ згортаються: їх значення
  використовується як адреса. Виміряно, що згортання `path` ламало
  `git rev-parse HEAD` у девʼяти тестах.

Односторонній тест тут нічого не вартий: він пройшов би і тоді, коли
згортається все, і тоді, коли не згортається нічого.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import semantic  # noqa: E402
from cairn_unified.redact import NO_FOLD_KEYS, redact_value  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

HUMAN = {"kind": "human", "id": "privacy-human"}
BS = chr(92)
WIN_HOME = "C:" + BS + "Users" + BS + "Testuser" + BS + ".local" + BS + "opencode.db"
NIX_HOME = "/" + "home/testuser/prj/cairn/tests/run_all.py"
MAC_HOME = "/" + "Users/testuser/Library/x"
OTHER_DRIVE = "Q:" + BS + "650" + BS + "home" + BS + "beta.loc"


def has_user_name(blob: str) -> bool:
    return "Testuser" in blob or "testuser" in blob


def name_leaks(node, path: str = "") -> list[str]:
    """Шляхи до рядків з імʼям користувача ПОЗА полями-посиланнями.

    Перевіряти текст JSON не можна: там backslash екранований, і наївна
    заміна не збігається з рядком у памʼяті. Тому йдемо структурою й
    питаємо КОЖНЕ поле, чи має воно право тримати адресу.
    """
    out: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key) in NO_FOLD_KEYS:
                continue
            out.extend(name_leaks(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(node, list):
        for value in node:
            out.extend(name_leaks(value, path + "[]"))
    elif isinstance(node, str) and has_user_name(node):
        out.append(path or "<root>")
    return out


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    # --- рівень функції: три форми домашньої теки й один не-домашній диск ---
    for label, value in (("win", WIN_HOME), ("nix", NIX_HOME), ("mac", MAC_HOME)):
        folded = redact_value(value)
        checks.append((f"{label}: тека згорнута до ~", folded.startswith("~"), folded))
        checks.append((f"{label}: імені користувача немає",
                       not has_user_name(folded), folded))
        tail = folded.split("~", 1)[1]
        checks.append((f"{label}: хвіст шляху збережено", len(tail) > 4, tail))

    kept = redact_value(OTHER_DRIVE)
    checks.append(("не-домашній диск не зачеплено", kept == OTHER_DRIVE, kept))

    # --- рівень полів: текст згортається, посилання ні ---
    text_fields = redact_value({"summary": WIN_HOME,
                                "condition": {"rationale": NIX_HOME}})
    checks.append(("summary згорнуто", text_fields["summary"].startswith("~"),
                   text_fields["summary"]))
    checks.append(("rationale згорнуто",
                   text_fields["condition"]["rationale"].startswith("~"),
                   text_fields["condition"]["rationale"]))

    refs = redact_value({"path": WIN_HOME, "repo_roots": [WIN_HOME],
                         "packs_dir": NIX_HOME})
    checks.append(("path лишився адресою", refs["path"] == WIN_HOME, refs["path"]))
    checks.append(("repo_roots лишились адресами",
                   refs["repo_roots"] == [WIN_HOME], str(refs["repo_roots"])))
    checks.append(("packs_dir лишився адресою", refs["packs_dir"] == NIX_HOME,
                   refs["packs_dir"]))

    # --- рівень сховища: що справді записано на диск ---
    temp = Path(tempfile.mkdtemp(prefix="privacy-"))
    try:
        Store(temp).init()
        store = Store.open(temp)
        out = semantic.put_object(
            store, object_type="observation", title="шлях у тексті",
            summary=f"мутація поза репозиторієм: {WIN_HOME}", actor=HUMAN,
            stable_key="privacy-probe-1",
            semantic={"kind": "observation",
                      "unresolved_rows": [[NIX_HOME, "path_outside_repo"]],
                      "repo_roots": [WIN_HOME]},
        )
        leaks = name_leaks(out["object"])
        checks.append(("у записаному обʼєкті імʼя лише в полях-посиланнях",
                       not leaks, "; ".join(leaks[:3]) or "жодного"))
        checks.append(("summary обʼєкта згорнуто",
                       "~" in out["object"]["summary"]
                       and not has_user_name(out["object"]["summary"]),
                       out["object"]["summary"][-40:]))
        rows = out["object"]["semantic"]["unresolved_rows"]
        checks.append(("рядки звіту згорнуто",
                       not has_user_name(json.dumps(rows)), json.dumps(rows)))
        checks.append(("repo_roots обʼєкта лишились адресами",
                       out["object"]["semantic"]["repo_roots"] == [WIN_HOME],
                       str(out["object"]["semantic"]["repo_roots"])))

        ledger_path = temp / ".cairn" / "events" / "ledger.jsonl"
        events = [json.loads(line) for line in
                  ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        leaks = [item for event in events for item in name_leaks(event)]
        checks.append(("у журналі імʼя лише в полях-посиланнях",
                       not leaks, "; ".join(leaks[:3]) or f"подій {len(events)}"))
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    good = 0
    for label, ok, detail in checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {label:46} | {detail[:46]}")
        good += int(ok)
    print(f"ВЕРДИКТ: приватність шляхів ({good}/{len(checks)})")
    return 0 if good == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
