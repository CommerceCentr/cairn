"""Один вхід для всіх перевірок Unified v2 (без pytest).

Запуск:  python tests/run_all.py

Кожен standalone-скрипт має власний exit code. Цей runner запускає їх усі
в одним процесом-оркестратором і повертає глобальний exit code, придатний
для CI. Додатково виконує import smoke і CLI smoke, щоб «чистий клон»
перевіряв не лише юніт-сценарії, а й реальний інтерфейс.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
SCRIPTS = (
    "test_unified_session_context.py",
    "test_unified_commit_facts.py",
    "test_unified_v2_root.py",
    "verify_zones.py",
    "verify_merge.py",
    "test_unified_vs01.py",
    "test_unified_federation.py",
    "test_unified_mcp.py",
    "test_unified_hardening.py",
    "verify_was_rationale.py",
    "verify_auto_gate.py",
    "verify_causal_history.py",
    # Були поза воротами, хоч і проходили: 31+5 тверджень не перевірялися
    # ніколи. Виміряно наслідок — `verify_compaction_link` не спіймав зміну
    # контракту причин, бо його не запускали.
    "verify_compaction_link.py",
    "verify_adapter_crud.py",
    "verify_codex_adapter.py",
    "test_git_content_witness.py",
    "verify_commit_node_scope.py",
    # Нові ворота: підроблений bundle і приватність шляхів.
    "verify_bundle_tamper.py",
    "verify_why.py",
    "verify_zone_causality.py",
    "verify_change_notes.py",
    "verify_causal_release_scope.py",
    "verify_normalize.py",
    "verify_sources.py",
    "verify_surfaces.py",
    # Переносність, зони без колізій, спільна картина — у воротах,
    # бо саме на це питання користувач мусить мати відповідь прогоном.
    "../tools/measure_teamwork.py",
    "verify_privacy_paths.py",
    "verify_public_paths.py",
    # Непереносний: пропускається з названою причиною, не падає беззвучно.
    "verify_auto_dossier.py",
    "benchmark_unified.py",
)


def env() -> dict[str, str]:
    result = dict(os.environ)
    result["PYTHONDONTWRITEBYTECODE"] = "1"
    result["PYTHONIOENCODING"] = "utf-8"
    result["PYTHONPATH"] = str(ROOT)
    return result


def run(script: str) -> bool:
    proc = subprocess.run([sys.executable, str(TESTS / script)], cwd=str(ROOT),
                          env=env(), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    lines = [line for line in proc.stdout.splitlines()
             if "ВЕРДИКТ" in line or "FAIL" in line or line.startswith("SKIP")]
    verdict = lines[-1] if lines else (proc.stderr.strip().splitlines() or ["<без виводу>"])[-1]
    skipped = verdict.startswith("SKIP")
    flag = "SKIP" if skipped and proc.returncode == 0 else (
        "ok  " if proc.returncode == 0 else "FAIL")
    print(f"  {flag} {script:32} exit={proc.returncode} | {verdict[:110]}")
    if proc.returncode != 0:
        for line in proc.stdout.splitlines()[-6:]:
            print("       " + line)
        for line in proc.stderr.splitlines()[-6:]:
            print("       " + line)
    return proc.returncode == 0


def import_smoke() -> bool:
    code = (
        "import cairn_unified, cairn_unified.cli, cairn_unified.mcp, cairn_unified.commits, "
        "cairn_unified.zones, "
        "cairn_unified.handoff, cairn_unified.gates, cairn_unified.workflow, "
        "cairn_unified.federation; print(cairn_unified.__version__)"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env(),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    good = proc.returncode == 0 and proc.stdout.strip().endswith("2.0.0")
    print(f"  {'ok  ' if good else 'FAIL'} {'import smoke':32} | {proc.stdout.strip() or proc.stderr.strip()[:80]}")
    return good


def cli_smoke() -> bool:
    with tempfile.TemporaryDirectory(prefix="cairn-runall-") as tmp:
        base = [sys.executable, "-m", "cairn_unified", "--root", tmp, "--compact"]
        init = subprocess.run(base + ["project", "init"], cwd=str(ROOT), env=env(),
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        tools = subprocess.run(base + ["mcp", "tools"], cwd=str(ROOT), env=env(),
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
        doctor = subprocess.run(base + ["project", "doctor"], cwd=str(ROOT), env=env(),
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
        try:
            count = len(json.loads(tools.stdout)["tools"])
            healthy = json.loads(doctor.stdout)["ok"]
        except (ValueError, KeyError):
            count, healthy = -1, False
        # 12, не 11: додано `cairn_why`. Число тут навмисно жорстке — інакше
        # випадкова втрата інструмента пройшла б непоміченою.
        good = init.returncode == 0 and count == 13 and doctor.returncode == 0 and healthy is True
        detail = f"init={init.returncode} tools={count} doctor_ok={healthy}"
        print(f"  {'ok  ' if good else 'FAIL'} {'cli smoke':32} | {detail}")
        return good


def main() -> int:
    print("ПОВНИЙ ПРОГІН UNIFIED v2 (локально, без pytest)")
    results = [run(script) for script in SCRIPTS]
    results.append(import_smoke())
    results.append(cli_smoke())
    passed = sum(1 for item in results if item)
    total = len(results)
    print(f"\n{passed}/{total} перевірок пройдено")
    if all(results):
        print("ВЕРДИКТ: усі перевірки Unified v2 зелені")
        return 0
    print("ВЕРДИКТ: є падіння — див. FAIL вище")
    return 1


if __name__ == "__main__":
    sys.exit(main())
