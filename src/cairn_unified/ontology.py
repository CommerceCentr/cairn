"""Онтологія зв'язків і життєвих циклів.

Ефект виконання записаний у реєстрі й зберігається на ребрі.
Його не виводять із текстової мітки під час читання.
"""

from __future__ import annotations

SCHEMA_VERSION = "2.0"
DB_SCHEMA_VERSION = 1

RELATION_REGISTRY: dict[str, dict] = {
    "depends_on": {"effect": "hard", "blocks": True},
    "blocks": {"effect": "hard", "blocks": True},
    "parent_of": {"effect": "none", "blocks": False},
    "conditional_blocks": {"effect": "conditional", "blocks": True},
    "waits_for": {"effect": "hard", "blocks": True},
    "implements": {"effect": "none", "blocks": False},
    "satisfies": {"effect": "none", "blocks": False},
    "validates": {"effect": "none", "blocks": False},
    "calls": {"effect": "none", "blocks": False},
    "depends_at_runtime_on": {"effect": "none", "blocks": False},
    "caused_by": {"effect": "none", "blocks": False},
    #: CrUD-участь коміту в вузлі — окреме твердження, не причинність.
    #: Ставиться тільки з rev-parse-резолвованого `git show --name-status`.
    "mutated": {"effect": "none", "blocks": False},
    "discovered_during": {"effect": "none", "blocks": False},
    "derived_from": {"effect": "none", "blocks": False},
    "supports": {"effect": "none", "blocks": False},
    "contradicts": {"effect": "none", "blocks": False},
    "related_to": {"effect": "none", "blocks": False},
    "duplicates": {"effect": "none", "blocks": False},
    "supersedes": {"effect": "none", "blocks": False},
    "replies_to": {"effect": "none", "blocks": False},
    "owns": {"effect": "none", "blocks": False},
    "grants": {"effect": "none", "blocks": False},
    "governed_by": {"effect": "none", "blocks": False},
    "targets_symbol": {"effect": "none", "blocks": False},
    "tests": {"effect": "none", "blocks": False},
    "renamed_to": {"effect": "none", "blocks": False},
    "moved_to": {"effect": "none", "blocks": False},
}

CLAIMABLE = {"open", "ready"}
TERMINAL = {"completed", "cancelled", "superseded", "rejected", "archived"}

TRANSITIONS: dict[str, set[str]] = {
    "draft": {"open", "cancelled"},
    "open": {"ready", "blocked", "deferred", "cancelled", "claimed"},
    "ready": {"claimed", "blocked", "deferred", "cancelled", "open"},
    "claimed": {"in_progress", "ready", "open", "cancelled"},
    "in_progress": {"review", "awaiting_gate", "failed", "blocked", "cancelled"},
    "review": {"in_progress", "rejected", "awaiting_gate"},
    "awaiting_gate": {"review", "in_progress", "cancelled", "failed"},
    "failed": {"open", "cancelled"},
    "blocked": {"open", "ready", "cancelled"},
    "deferred": {"open", "cancelled"},
    "completed": set(),
    "cancelled": set(),
    "rejected": set(),
    "superseded": set(),
    "archived": set(),
}

TRUST_CLASSES = (
    "human_authoritative",
    "verified_system",
    "source_observed",
    "agent_proposed",
    "agent_inferred",
    "imported_untrusted",
    "rejected_or_revoked",
)

AUTHORITATIVE = {"human_authoritative", "verified_system"}

FRESHNESS = ("current", "stale", "superseded", "invalidated", "unverified", "historical")

OBJECT_TYPES = (
    "organization", "project", "workspace", "repository", "decision", "contract",
    "invariant", "fact", "observation", "hypothesis", "work_item", "symbol",
    "evidence", "gate", "session", "note", "concept", "requirement",
)

GATE_TYPES = (
    "human.approval",
    "timer.elapsed",
    "git.commit.present",
    "git.branch.merged",
    "ci.run.success",
    "review.approved",
    "external.work_item.closed",
    "artifact.available",
)

DEFAULTS = {
    "budget_chars": 8000,
    "lease_ttl_sec": 3600,
    "command_allowlist": ["python", "php", "git"],
    "policy_version": "1",
}
