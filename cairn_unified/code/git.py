"""Локальний Git-адаптер: тотожність, ревізії, worktree, коміти.

 Git є джерелом істини для коду. Адаптер не приймає доменних рішень і не
 виконує довільних команд: список git-підкоманд обмежений. Тотожність
 репозиторію — відбиток НАБОРУ кореневих комітів (не одного «першого»):
 форки зі спільним зародком мають розрізнятися, а клони — збігатися.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..canon import sha256_text
from ..errors import CairnError
from ..redact import redact_text

READ_ONLY = frozenset({
    "rev-parse", "rev-list", "show", "diff", "status", "log", "cat-file",
    "worktree", "branch", "ls-files", "name-rev", "merge-base",
})
WRITE = frozenset({"add", "commit", "checkout", "apply", "clean", "rm"})
ALLOWED = READ_ONLY | WRITE


def git(repo, *args: str, timeout: int = 60) -> str:
    """Виконати дозволену git-команду й повернути stdout.

    `work.py` викликає саме цю функцію як `git(path, "show", "sha:path")`.
    """
    cmd = [str(part) for part in args]
    cursor = 0
    while cursor + 1 < len(cmd) and cmd[cursor] == "-c":
        cursor += 2  # тимчасові -c key=value перед дієсловом
    verb = cmd[cursor] if cursor < len(cmd) else None
    if verb not in ALLOWED:
        raise CairnError("git_command_denied", f"git-команда {[verb]} не дозволена", exit_code=2)
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise CairnError("git_unavailable", "git не встановлено або недоступний у PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise CairnError("git_timeout", f"git {cmd[0]} перевищив {timeout}с") from exc
    if proc.returncode != 0:
        detail, _ = redact_text((proc.stderr or "").strip()[:600])
        raise CairnError("git_failed", f"git {verb} завершився з кодом {proc.returncode}", details={"stderr": detail})
    return proc.stdout


def repo_root(path) -> str:
    root = git(path, "rev-parse", "--show-toplevel").strip()
    if not root:
        raise CairnError("not_a_repository", f"{path} не є Git-репозиторієм", exit_code=2)
    return root


def common_dir(path) -> str:
    """Спільний .git для main checkout і будь-якого worktree одного репозиторію."""
    out = git(path, "rev-parse", "--git-common-dir").strip()
    candidate = Path(out)
    if not candidate.is_absolute():
        candidate = Path(path) / candidate
    return str(candidate.resolve())


def root_commits(repo) -> list[str]:
    out = git(repo, "rev-list", "--max-parents=0", "--all").strip()
    return sorted(line.strip() for line in out.splitlines() if line.strip())


def identity(path) -> dict:
    """Стабільна тотожність репозиторію, незалежна від шляху й гілки."""
    root = repo_root(path)
    roots = root_commits(root)
    if not roots:
        raise CairnError("no_root_commit", "репозиторій не має комітів", exit_code=2)
    return {
        "id": sha256_text("\n".join(roots))[:40],
        "path": root,
        "fingerprint": "\n".join(roots),
        "head": head(root),
        "label": Path(root).name,
    }


def head(repo) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def tree_hash(repo, revision: str = "HEAD") -> str:
    return git(repo, "rev-parse", f"{revision}^{{tree}}").strip()


def show(repo, revision: str, path: str) -> str:
    return git(repo, "show", f"{revision}:{path}")


def changed_paths(repo, base: str, head_revision: str) -> list[str]:
    out = git(repo, "diff", "--name-only", base, head_revision)
    return [line.strip() for line in out.splitlines() if line.strip()]


def is_clean(repo) -> bool:
    return not bool(git(repo, "status", "--porcelain").strip())


def worktree_add(repo, dest, branch: str, base: str) -> str:
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-b", branch, str(target), base)
    return str(target)


def worktree_remove(repo, dest, branch: str | None = None) -> None:
    if Path(dest).exists():
        git(repo, "worktree", "remove", "--force", str(dest))
    if branch:
        proc = subprocess.run(
            ["git", "-C", str(repo), "branch", "-D", branch],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 and "not found" not in (proc.stderr or "").lower():
            detail, _ = redact_text((proc.stderr or "").strip()[:300])
            raise CairnError("git_branch_delete_failed", "не вдалося видалити гілку", details={"stderr": detail})


def commit(repo, message: str, paths: list[str]) -> dict:
    if not paths:
        raise CairnError("nothing_to_commit", "немає файлів для коміту", exit_code=2)
    git(repo, "add", "--", *paths)
    git(
        repo,
        "-c", "user.name=Cairn Worker",
        "-c", "user.email=worker@cairn.local",
        "commit", "-m", message,
    )
    sha = head(repo)
    return {"commit": sha, "tree": tree_hash(repo, sha)}


def exists(repo, revision: str) -> bool:
    try:
        git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}")
        return True
    except CairnError:
        return False
