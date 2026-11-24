"""Збій `git archive` мусить бути ДІАГНОСТОВНИМ, а не мовчазним.

Виміряний дефект. 2026-11-24 хук `post-commit` спрацював сам і впав:

    cairn_unified.errors.CairnError: git archive failed
    details: {"stderr": ""}

Код виходу не записувався, тож причина падіння була невідновна. Саме через
це збій виявився невідтворюваним: я двічі заявляв механізм (успадковані
`GIT_*`, потім необірваний хвіст потоку) і двічі відкидав його виміром.
Другий «до/після» був сплутаний зміною стану репозиторію між прогонами.

Тому ці ворота перевіряють НЕ гіпотезу про причину, а те, що наступний
такий збій можна буде діагностувати: код виходу, номер сигналу й пряме
`not_established`, коли причина невідома.

Реверс обовʼязковий і тут: без `nodes.archive_failure` перевірки нижче
падають, бо старі деталі несли лише `stderr`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fixture_repo import PY_FILE, make_repo  # noqa: E402

from cairn_unified import nodes  # noqa: E402
from cairn_unified.errors import CairnError  # noqa: E402


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    # --- 1. SIGPIPE: саме та комбінація, що приховала причину ---
    detail = nodes.archive_failure(141, b"")
    checks.append(("rc 141 + порожній stderr -> названо обрив каналу",
                   detail.get("signal") == 13
                   and detail.get("likely_cause") == "broken_pipe_reader_closed_early",
                   str(detail.get("likely_cause"))[:38]))
    checks.append(("код виходу збережено, не загублено",
                   detail.get("returncode") == 141, str(detail.get("returncode"))))
    checks.append(("порожній stderr названо прямо",
                   detail.get("stderr_empty") is True, str(detail.get("stderr_empty"))))

    # --- 2. інший сигнал не підмінюється обривом каналу ---
    killed = nodes.archive_failure(137, b"")
    checks.append(("rc 137 -> інший сигнал, не обрив каналу",
                   killed.get("signal") == 9
                   and killed.get("likely_cause") == "killed_by_signal",
                   f"signal={killed.get('signal')}"))

    # --- 3. невідома причина лишається НЕ встановленою ---
    plain = nodes.archive_failure(1, b"")
    checks.append(("rc 1 без stderr -> not_established, не вгадування",
                   plain.get("likely_cause") == "not_established",
                   str(plain.get("likely_cause"))[:38]))

    # --- 4. справжній stderr проходить наскрізь і причину не вигадують ---
    real = nodes.archive_failure(128, b"fatal: not a valid object name\n")
    checks.append(("справжній stderr збережено",
                   "not a valid object name" in real.get("stderr", "")
                   and "likely_cause" not in real,
                   real.get("stderr", "")[:38]))

    # --- 5. цілісно: справжній збій git несе код виходу в деталях ---
    temp = Path(tempfile.mkdtemp(prefix="cairn-archfail-"))
    try:
        made = make_repo(temp / "repo", files={PY_FILE: "def a():\n    return 1\n"})
        repo = Path(made["path"])
        probe = subprocess.run(["git", "-C", str(repo), "archive",
                                "--format=tar", "no-such-revision"],
                               capture_output=True)
        checks.append(("git справді відмовляє на невідомій ревізії",
                       probe.returncode != 0, f"rc={probe.returncode}"))
        try:
            nodes.discover_commit(str(repo), "rid", revision="no-such-revision")
            checks.append(("discover_commit -> CairnError", False, "не відмовив"))
        except CairnError as exc:
            got = exc.details or {}
            checks.append(("discover_commit -> CairnError", exc.code == "git_failed",
                           exc.code))
            checks.append(("деталі справжнього збою несуть код виходу",
                           isinstance(got.get("returncode"), int)
                           and got["returncode"] != 0,
                           f"rc={got.get('returncode')}"))
            checks.append(("деталі справжнього збою несуть stderr",
                           bool(str(got.get("stderr") or "").strip()),
                           str(got.get("stderr"))[:38]))
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    good = 0
    for label, ok, info in checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {label:52} | {info[:36]}")
        good += int(ok)
    print(f"ВЕРДИКТ: діагностовність збою git archive ({good}/{len(checks)})")
    return 0 if good == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
