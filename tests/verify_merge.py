"""Злиття контексту одного Git-репозиторію з іншого сховища/агента.

Автономний тест: два тимчасові `.cairn`, реальний git-адаптер поточного репо.
Жодних записів у виробниче сховище й жодного коміту.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cairn_unified import code, merge, nodes, semantic, verification, zones  # noqa: E402
from cairn_unified.store import Store  # noqa: E402

HUMAN = {"kind": "human", "id": "merge-tester"}
AGENT_A = {"kind": "agent", "id": "agent-alpha"}
AGENT_B = {"kind": "agent", "id": "agent-beta"}


def _proxy(store, key, session, commit, actor):
    return semantic.put_object(
        store, object_type="session", title=f"merge proxy {session}",
        summary=f"context of {session}", actor=actor, stable_key=f"merge-proxy-{key}",
        semantic={"kind": "session", "session_id": session, "commit": commit,
                  "summary_sha256": "ab" * 32},
    )["object"]


def main() -> int:
    checks: list[tuple[str, bool]] = []
    repo = str(ROOT)
    ident = code.git.identity(repo)
    with tempfile.TemporaryDirectory(prefix="cairn-merge-src-") as sa, \
            tempfile.TemporaryDirectory(prefix="cairn-merge-dst-") as sb:
        Store(Path(sa)).init()
        src = Store.open(Path(sa))
        Store(Path(sb)).init()
        dst = Store.open(Path(sb))

        proxy_a = _proxy(src, "a", "ses-merge-a", ident["head"], HUMAN)
        refs_a = zones.object_refs(src, [proxy_a["id"]])
        zones.descriptor(src, "shared.loc", repo=ident["id"], pathspec="*.py",
                         actor=HUMAN, context_refs=refs_a,
                         context_identity={"session_id": "ses-merge-a"})
        hold_a = zones.acquire(src, "shared.loc", AGENT_A)
        zones.append(src, "shared.loc", AGENT_A, hold_a["fence"],
                     {"path": "cairn_unified/merge.py", "op": "edit", "agent": "A"})
        bundle = zones.export_bundle(src, ["shared.loc"])
        checks.append(("bundle містить справжній хеш коміту",
                       ident["head"] in merge.collect_commits(bundle)))

        proxy_b = _proxy(dst, "b", "ses-merge-b", ident["head"], HUMAN)
        refs_b = zones.object_refs(dst, [proxy_b["id"]])
        zones.descriptor(dst, "shared.loc", repo=ident["id"], pathspec="*.py",
                         actor=HUMAN, context_refs=refs_b,
                         context_identity={"session_id": "ses-merge-b"})
        zones.descriptor(dst, "agent-b.loc", repo=ident["id"], pathspec="*.php",
                         actor=HUMAN, context_refs=[],
                         context_identity={"session_id": "ses-merge-b"})
        hold_b = zones.acquire(dst, "agent-b.loc", AGENT_B)
        zones.append(dst, "agent-b.loc", AGENT_B, hold_b["fence"],
                     {"path": "index.php", "op": "edit", "agent": "B"})

        dry = merge.merge_bundle(dst, bundle, repo_path=repo, actor=AGENT_B)
        checks.append(("dry-run нічого не пише",
                       not dry["applied"] and dry["merge_event_id"] is None))
        checks.append((" тотожність репозиторію збігається з git",
                       dry["ok"] and dry["repo_id"] == ident["id"]
                       and dry["declared_repos"] == [ident["id"]]))
        checks.append((" хеші комітів резолвляться локальним git",
                       dry["commit_links"] and not dry["unlinked_commits"]
                       and all(item["in_repo"] and item["full"] == ident["head"][:12]
                               for item in dry["commit_links"])))
        checks.append((" розбіжність context_identity — явний конфлікт",
                       any(c["kind"] == "zone_identity_divergence"
                           for c in dry["conflicts"])))

        result = merge.merge_bundle(dst, bundle, repo_path=repo,
                                    actor=HUMAN, apply=True)
        checks.append((" злиття застосовано людиною",
                       result["applied"] and result["merge_event_id"]
                       and result["applied_events"] >= 1))
        shared = zones.read_journal(dst, "shared.loc")
        own = zones.read_journal(dst, "agent-b.loc")
        checks.append((" журнали обох агентів збережено без перезапису",
                       len(shared) == 1 and len(own) == 1
                       and shared[0]["actor"] == AGENT_A["id"]
                       and own[0]["actor"] == AGENT_B["id"]))
        desc = json.loads(zones.descriptor_path(dst.root, "shared.loc").read_text(
            encoding="utf-8"))
        checks.append((" head зони не менший за обидва джерела",
                       desc["head"] == max(1, int(bundle["descriptors"][0]["head"]))))
        checks.append((" локальна ідентичність сесії не перезаписана",
                       desc["context_identity"] == {"session_id": "ses-merge-b"}))

        events = [json.loads(r["payload_json"]) for r in dst.read(
            "SELECT payload_json FROM events WHERE event_type = 'merge.recorded'")]
        checks.append((" подія злиття несе справжній HEAD і хеші",
                       len(events) == 1 and events[0]["git_head"] == ident["head"]
                       and events[0]["commit_links"] == result["commit_links"]
                       and events[0]["conflicts"] == result["conflicts"]))
        checks.append((" повторне злиття ідемпотентне",
                       merge.merge_bundle(dst, bundle, repo_path=repo,
                                          actor=HUMAN, apply=True)["applied_events"] == 0))
        semantic.put_object(src, object_type="decision", title="remote conflict",
                            summary="remote", actor=HUMAN,
                            stable_key="merge-protected-conflict")
        semantic.put_object(dst, object_type="decision", title="local conflict",
                            summary="local", actor=AGENT_B,
                            stable_key="merge-protected-conflict")
        conflicting = zones.export_bundle(src, ["shared.loc"])
        try:
            merge.merge_bundle(dst, conflicting, repo_path=repo,
                               actor=HUMAN, apply=True)
            conflict_blocked = False
        except Exception as exc:
            conflict_blocked = getattr(exc, "code", "") == "merge_conflict"
        checks.append((" захищена розбіжність блокує застосування",
                       conflict_blocked))

        body = semantic.put_object(
            dst, object_type="decision", title="merge context",
            summary="злиття контексту одного git-репозиторію", actor=AGENT_B,
            stable_key="merge-context-test",
            semantic={"kind": "merge_context", "repo_id": result["repo_id"],
                      "git_head": result["repo_head"],
                      "source_bundle_digest": result["bundle_digest"],
                      "commit_links": result["commit_links"],
                      "unlinked_commits": result["unlinked_commits"],
                      "conflicts": result["conflicts"]},
        )["object"]
        object_checks, reasons = verification.check_object(dst, body, repo_path=repo)
        names = {c["check"] for c in object_checks}
        checks_ok = {"merge_repo_identity_matches", "merge_git_head_resolves",
                     "merge_git_links_resolved", "merge_event_present",
                     "merge_conflicts_recorded"}
        checks.append((" верифікатор звіряє git, а не текст об'єкта",
                       checks_ok <= names
                       and not [r for r in reasons if r.startswith("unresolved:merge_")]))

        tampered = json.loads(json.dumps(bundle))
        tampered["descriptors"][0]["repo"] = "f" * 40
        tampered["digest"] = "00" * 32  # спершу пройти digest, потім відхилити repo
        audit = dict(tampered)
        audit.pop("digest", None)
        from cairn_unified.canon import sha256_text
        tampered["digest"] = sha256_text(json.dumps(
            audit, ensure_ascii=False, sort_keys=True))
        refused = merge.inspect_merge(dst, tampered, repo_path=repo)
        checks.append((" реверс: чужий repo-id відхиляється",
                       refused["status"] == "refused"
                       and "repo_identity_mismatch" in refused["problems"]))
        try:
            merge.merge_bundle(dst, tampered, repo_path=repo, actor=HUMAN,
                               apply=True)
            blocked = False
        except Exception as exc:  # CairnError: merge_refused
            blocked = getattr(exc, "code", "") == "merge_refused"
        checks.append((" реверс: застосування чужого bundle блокується", blocked))

        bad = json.loads(json.dumps(body))
        bad["semantic"]["commit_links"][0]["requested"] = "0" * 40
        _checks, bad_reasons = verification.check_object(dst, bad, repo_path=repo)
        checks.append((" реверс: нерезолвований хеш ескалує",
                       any(r.startswith("unresolved:merge_git_link_unresolved")
                           for r in bad_reasons)))
        # Реальний bundle виявив хибні причини: базовий шар вимагав commit і
        # вузол навіть для merge_context. Фіксуємо, що ескалація містить ЛИШЕ
        # чесну причину про git-хеш, а не артефакти перевіряльника.
        synthetic_wrong = [r for r in bad_reasons
                           if r in {"unresolved:commit_absent", "unresolved:node_absent",
                                    "unresolved:commit_not_found"}]
        checks.append((" реверс: немає хибних причин commit_absent/node_absent",
                       not synthetic_wrong))

    failed = [label for label, ok in checks if not ok]
    for label, ok in checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    print("ВЕРДИКТ: " + (f"FAIL ({len(checks) - len(failed)}/{len(checks)})"
                         if failed else f"злиття прив'язане до git ({len(checks)}/{len(checks)})"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
