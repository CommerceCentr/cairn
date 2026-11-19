"""Докази: коміти, тести, перевірки. Жодного «зеленого прапорця» без запису.

Доказ завжди має immutable reference/hash, час, ревізію, верифікатора й
trust class. Агент може записати спостереження, але не може видати
його за системний факт: trust визначається адаптером і політикою.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .canon import canonical, prefixed, sha256_text
from .code import git
from .errors import CairnError
from .ontology import AUTHORITATIVE


def record(store, *, evidence_type: str, content_hash: str, actor: dict,
           subject_id: str | None = None, source_ref: str | None = None,
           metadata: dict | None = None, verifier: dict | None = None,
           trust: str = "verified_system", idempotency_key: str | None = None) -> dict:
    if trust not in AUTHORITATIVE and actor["kind"] == "agent":
        raise CairnError("authority_required", "агент не може видати доказ системним")
    eid = prefixed("evd")
    payload = {
        "id": eid,
        "evidence_type": evidence_type,
        "content_hash": content_hash,
        "source_ref": source_ref,
        "subject_id": subject_id,
        "collected_at": store.now(),
        "trust_class": trust,
        "metadata": metadata or {},
        "verifier": verifier or {"kind": actor["kind"], "id": actor["id"]},
    }

    def op(con):
        ev = store.append(con, "evidence.recorded", subject_id or eid, payload, actor, idempotency_key)
        return {"evidence": ev["payload"], "event_id": ev["event_id"], "replayed": ev["replayed"]}

    return store.mutate(op)


def record_commit(store, repo_id: str, commit: str, actor: dict, *, subject_id: str | None = None) -> dict:
    repo = store.read_one("SELECT * FROM repositories WHERE id = ?", (repo_id,))
    if repo is None:
        raise CairnError("not_found", f"репозиторій {repo_id} не зареєстровано", exit_code=1)
    if not git.exists(repo["path"], commit):
        raise CairnError("commit_missing", f"коміт {commit} не існує локально", exit_code=2)
    tree = git.tree_hash(repo["path"], commit)
    eid = prefixed("evd")
    payload = {
        "id": eid,
        "evidence_type": "commit",
        "content_hash": sha256_text(f"{commit}\n{tree}"),
        "source_ref": f"git://{repo_id}@{commit}",
        "subject_id": subject_id,
        "collected_at": store.now(),
        "trust_class": "verified_system",
        "metadata": {"repo_id": repo_id, "commit": commit, "tree": tree},
        "verifier": {"kind": "system", "id": "git-local", "revision": commit},
    }
    key = f"commit:{repo_id}:{commit}"

    def op(con):
        ev = store.append(con, "evidence.recorded", subject_id or eid, payload, actor, key)
        if ev["replayed"]:
            return {"evidence": ev["payload"], "event_id": ev["event_id"], "replayed": True}
        # Коміт виконавця стає новим відомим head. Індекс лишається на старій
        # ревізії й чесно позначається stale, доки його не переіндексовано.
        store.append(con, "repository.upserted", repo_id, {
            "id": repo_id,
            "path": repo["path"],
            "fingerprint": repo["fingerprint"],
            "head": commit,
            "label": repo["label"],
        }, actor, idempotency_key=f"repo-head:{repo_id}:{commit}")
        return {"evidence": ev["payload"], "event_id": ev["event_id"], "replayed": False}

    return store.mutate(op)


def record_test(store, *, command: list[str], cwd: str, actor: dict,
                subject_id: str | None = None, repo_id: str | None = None,
                commit: str | None = None, worktree: str | None = None,
                timeout: int = 300) -> dict:
    """Виконує дозволену команду без shell і зберігає точний digest виводу.

    Якщо вказано repo_id+commit, доказ прив'язується до ТОЧНОЇ ревізії:
    перевіряється, що коміт існує локально, що worktree, у якому запущено
    тест, належить тому самому репозиторію, має цей HEAD і не має некомітованих
    змін. Без цього агент міг би прикріпити зелений тест до чужого коміту.
    """
    if not command:
        raise CairnError("bad_command", "порожня команда", exit_code=2)
    allow = store.config_value("command_allowlist")["value"]
    binary = Path(command[0]).name
    if binary not in {str(item) for item in allow}:
        raise CairnError("command_denied", f"команда {binary} не в allowlist", exit_code=2)
    workdir = Path(cwd).resolve()
    root = Path(store.root).resolve()
    if workdir != root and root not in workdir.parents:
        raise CairnError("cwd_outside_scope", "робоча тека поза проєктом", exit_code=2)

    binding: dict = {"bound": False}
    if repo_id is not None or commit is not None or worktree is not None:
        if not repo_id or not commit:
            raise CairnError("bad_binding", "для прив'язки потрібні repo_id і commit", exit_code=2)
        repo = store.read_one("SELECT * FROM repositories WHERE id = ?", (repo_id,))
        if repo is None:
            raise CairnError("not_found", f"репозиторій {repo_id} не зареєстровано", exit_code=1)
        if not git.exists(repo["path"], commit):
            raise CairnError("commit_missing", f"коміт {commit} не існує локально", exit_code=2)
        run_path = str(workdir if worktree is None else Path(worktree).resolve())
        try:
            run_git_dir = git.common_dir(run_path)
            repo_git_dir = git.common_dir(repo["path"])
        except CairnError as exc:
            raise CairnError("bad_binding", "тест запущено поза Git-репозиторієм", details={"worktree": run_path}) from exc
        if run_git_dir != repo_git_dir:
            raise CairnError("bad_binding", "worktree тесту належить іншому репозиторію",
                             details={"expected": repo_git_dir, "actual": run_git_dir})
        run_head = git.head(run_path)
        if run_head != commit or not git.is_clean(run_path):
            raise CairnError(
                "test_revision_mismatch",
                "тест запущено не на заявленій ревізії або в брудному worktree",
                exit_code=4,
                details={"expected": commit, "actual_head": run_head, "clean": git.is_clean(run_path)},
            )
        binding = {
            "bound": True,
            "repo_id": repo_id,
            "commit": commit,
            "tree": git.tree_hash(repo["path"], commit),
            "worktree": run_path,
            "head_at_run": run_head,
        }
    try:
        proc = subprocess.run(
            [str(part) for part in command], cwd=str(workdir),
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise CairnError("command_unavailable", f"немає виконувача {binary}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CairnError("command_timeout", f"команда перевищила {timeout}с") from exc
    out = (proc.stdout or "") + "\n--- stderr ---\n" + (proc.stderr or "")
    digest = sha256_text(out)
    payload_hash = sha256_text(canonical({"command": command, "digest": digest, "exit_code": proc.returncode}))
    return record(
        store,
        evidence_type="test",
        content_hash=payload_hash,
        source_ref=f"local://command/{binary}",
        subject_id=subject_id,
        actor=actor,
        metadata={
            "command": [str(part) for part in command],
            "exit_code": proc.returncode,
            "output_digest": digest,
            "passed": proc.returncode == 0,
            "repo_id": repo_id,
            "commit": commit,
            "revision_binding": binding,
        },
        verifier={"kind": "system", "id": "test-runner"},
        idempotency_key=f"test:{subject_id}:{payload_hash}",
    )


def verify(store, evidence_id: str) -> dict:
    row = store.read_one("SELECT * FROM evidence WHERE id = ?", (evidence_id,))
    if row is None:
        raise CairnError("not_found", f"доказ {evidence_id} не знайдено", exit_code=1)
    meta = json.loads(row["metadata_json"])
    problems: list[str] = []
    if row["evidence_type"] == "commit":
        repo = store.read_one("SELECT * FROM repositories WHERE id = ?", (meta.get("repo_id"),))
        if repo is None:
            problems.append("репозиторій зник")
        elif not git.exists(repo["path"], meta.get("commit") or ""):
            problems.append("коміт зник")
        elif git.tree_hash(repo["path"], meta["commit"]) != meta.get("tree"):
            problems.append("tree hash не збігається")
    elif row["evidence_type"] == "test":
        if not meta.get("passed"):
            problems.append("тест завершився ненульовим кодом")
        if not meta.get("output_digest"):
            problems.append("немає digest виводу")
        binding = meta.get("revision_binding") or {}
        if binding.get("bound"):
            repo = store.read_one("SELECT * FROM repositories WHERE id = ?", (binding.get("repo_id"),))
            if repo is None:
                problems.append("репозиторій прив'язки зник")
            elif not git.exists(repo["path"], binding["commit"]):
                problems.append("коміт прив'язки зник")
            elif git.tree_hash(repo["path"], binding["commit"]) != binding.get("tree"):
                problems.append("tree hash прив'язки не збігається")
    return {"ok": not problems, "evidence_id": evidence_id, "problems": problems}


def show(store, subject_id: str | None = None) -> list[dict]:
    if subject_id:
        rows = store.read("SELECT * FROM evidence WHERE subject_id = ? ORDER BY collected_at", (subject_id,))
    else:
        rows = store.read("SELECT * FROM evidence ORDER BY collected_at")
    out = []
    for row in rows:
        body = dict(row)
        body["metadata"] = json.loads(row["metadata_json"])
        body["verifier"] = json.loads(row["verifier_json"])
        body.pop("metadata_json", None)
        body.pop("verifier_json", None)
        out.append(body)
    return out
