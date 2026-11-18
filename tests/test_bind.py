"""Шар звʼязки з архіваріусом: що він МУСИТЬ і чого НЕ мусить.

Перелік обходу, написаний до перевірки:
    відома адреса           -> той самий файл, що в heat.tsv
    вигадана адреса         -> None, а не «щось схоже»
    відомий файл            -> непорожній список вузлів
    вигаданий файл          -> порожньо, не виняток
    зони без heat.tsv       -> FileNotFoundError з іменем файла
    корінь не вказано       -> None, а не зашитий шлях

Тест працює лише там, де архіваріус є. Інакше пропускається — це
чесніше, ніж вдавати перевірку на порожнечі.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.console import setup as _console_setup  # noqa: E402
_console_setup()

from bind import php_archivist as arch  # noqa: E402


def main() -> int:
    root = arch.arch_root(sys.argv[1] if len(sys.argv) > 1 else None)
    if not root or not os.path.isdir(root):
        print(f"пропущено: архіваріуса немає (вкажіть шлях або {arch.ENV_ROOT})")
        return 0

    zs = arch.zones(root)
    print(f"зони з індексом: {zs}")
    if not zs:
        print("пропущено: жодної зони з heat.tsv")
        return 0

    zone = "cc.one" if "cc.one" in zs else zs[0]
    by_file, by_addr = arch.load(root, zone)
    print(f"зона {zone}: файлів {len(by_file)}, адрес {len(by_addr)}")

    bad = 0
    if not by_addr:
        print("ПРОВАЛ: індекс порожній — далі перевіряти нічого")
        return 1

    # Позитивний контроль: беремо адресу З САМОГО індексу.
    addr = next(iter(by_addr))
    want = by_addr[addr]
    got = arch.file_of(addr, root, zone)
    ok = got == want
    bad += 0 if ok else 1
    print(f"{'ok ' if ok else 'ПРОВАЛ'} відома адреса -> {got}")

    # Негативний: вигаданої адреси не існує.
    fake = arch.file_of("class:Totally\\Made\\Up\\Node", root, zone)
    ok = fake is None
    bad += 0 if ok else 1
    print(f"{'ok ' if ok else 'ПРОВАЛ'} вигадана адреса -> {fake}")

    # Файл -> вузли, в обидва боки.
    nodes = arch.nodes_of(want, root, zone)
    ok = bool(nodes) and addr in nodes
    bad += 0 if ok else 1
    print(f"{'ok ' if ok else 'ПРОВАЛ'} відомий файл -> {len(nodes)} вузлів, "
          f"шукана адреса {'є' if addr in nodes else 'ВІДСУТНЯ'}")

    ok = arch.nodes_of("totally/made/up.php", root, zone) == []
    bad += 0 if ok else 1
    print(f"{'ok ' if ok else 'ПРОВАЛ'} вигаданий файл -> порожньо")

    # Відсутня зона: виняток із іменем файла, а не тиха порожнеча.
    try:
        arch.load(root, "zone-that-does-not-exist")
        print("ПРОВАЛ відсутня зона -> мовчазний успіх")
        bad += 1
    except FileNotFoundError as exc:
        print(f"ok  відсутня зона -> {type(exc).__name__}")

    # Корінь не зашитий у код.
    saved = os.environ.pop(arch.ENV_ROOT, None)
    try:
        ok = arch.arch_root(None) is None
        bad += 0 if ok else 1
        print(f"{'ok ' if ok else 'ПРОВАЛ'} без аргументу й змінної -> None "
              f"(шлях не зашитий)")
    finally:
        if saved is not None:
            os.environ[arch.ENV_ROOT] = saved

    print()
    print("ВЕРДИКТ:", "звʼязка коректна" if bad == 0 else f"ПРОВАЛІВ {bad}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
