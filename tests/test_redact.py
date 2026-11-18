"""Перелік обходу редактора: що МУСИТЬ бути спіймано і що МУСИТЬ пройти.

Прилад будується від спроби обходу. Перелік нижче написано ДО того, як
довіряти редакторові, і кожен вхід прогнано. «0 секретів у сховищі» щось
означає лише тому, що на цих входах редактор червоніє.

Другий список не менш важливий: якщо редактор ріже звичайний текст, він
знищить саме те «чому», заради якого все робиться.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.console import setup as _console_setup  # noqa: E402
_console_setup()

from core import redact  # noqa: E402

#: Префікси провайдерів відокремлені від решти НАВМИСНО. Усі ці рядки
#: вигадані, але сканер секретів GitHub читає ФАЙЛ, а не наміри: цілий
#: літерал `xoxb-…` заблокував push у цей репозиторій. Склейка лишає
#: редакторові рівно ту саму строку, тож перелік обходу не слабшає —
#: слабшає лише збіг сканера з текстом файла.
_ANTHROPIC = "sk-" + "ant-api03-AbCdEfGh1234567890XyZw"
_OPENAI = "sk-" + "proj1234567890abcdefghij"
_GITHUB = "ghp" + "_1234567890abcdefghijklmnopqrstuv"
_GOOGLE = "AIza" + "SyD-1234567890abcdefghijklmnopqrstu"
_SLACK = "xoxb-" + "123456789012-abcdefghijklmno"

MUST_CATCH = [
    ("ANTHROPIC_KEY", f"ключ {_ANTHROPIC} у тексті"),
    ("OPENAI_KEY", f"export KEY={_OPENAI}"),
    ("GITHUB_TOKEN", _GITHUB),
    ("AWS_KEY", "AKIAIOSFODNN7EXAMPLE"),
    ("GOOGLE_KEY", _GOOGLE),
    ("SLACK_TOKEN", _SLACK),
    ("JWT", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27u"),
    ("PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----"),
    ("BEARER", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123"),
    ("PASSWORD", "password = hunter2secret"),
    ("CONNSTR", "postgres://user:pa55word@db.local:5432/app"),
    # Імена вигадані навмисно: публічний репозиторій не має рознести
    # акаунт автора як зразок. Для редактора форма та сама.
    ("WIN_HOME", r"C:\Users\jdoe\AppData\Local\Temp"),
    ("NIX_HOME", "/home/jdoe/projects/app"),
    ("WIN_DRIVE", "Q:/work/home/example.loc/core/X.php"),
]

MUST_PASS = [
    "Правили core/Services/Routing/Redirector.php, бо редирект губив мову.",
    "Відкинули варіант з кешем: UrlPolicyReader не має DIR_CACHE до bootstrap.",
    "Канон comment-lint#1 забороняє прозу в докблоках.",
    "Метод Elitty\\Services\\Update\\UpdateManager::preloadSelf довантажує класи.",
    "Тест 171 passed / 0 failed, регресій немає.",
]


def main() -> int:
    bad = 0

    print("МУСЯТЬ БУТИ СПІЙМАНІ:")
    for want, sample in MUST_CATCH:
        hits = {n for n, _ in redact.find_secrets(sample)}
        ok = want in hits
        if not ok:
            bad += 1
        print(f"  {'ok ' if ok else 'ПРОВАЛ'} {want:<14} {sorted(hits) or 'нічого'}")

    print()
    print("МУСЯТЬ ПРОЙТИ НЕЗАЙМАНИМИ:")
    for sample in MUST_PASS:
        hits = redact.find_secrets(sample)
        ok = not hits
        if not ok:
            bad += 1
        print(f"  {'ok ' if ok else 'ПРОВАЛ'} {sample[:52]!r} -> "
              f"{[n for n, _ in hits] or 'чисто'}")

    print()
    # Після редакції сканер МУСИТЬ мовчати — інакше редакція нічого не дала.
    joined = "\n".join(s for _, s in MUST_CATCH)
    cleaned, counts = redact.redact(joined)
    left = redact.find_secrets(cleaned)
    print(f"замін зроблено: {sum(counts.values())} ({len(counts)} типів)")
    print(f"після редакції лишилось: {len(left)}")
    if left:
        bad += 1
        print(f"  ЩО ЛИШИЛОСЬ: {[n for n, _ in left][:5]}")

    print()
    print("ВЕРДИКТ:", "редактор придатний" if bad == 0 else f"ПРОВАЛІВ {bad}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
