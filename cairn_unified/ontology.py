"""Онтологія зв'язків і життєвих циклів.

Ефект виконання записаний у реєстрі й зберігається на ребрі.
Його не виводять із текстової мітки під час читання.
"""

from __future__ import annotations

SCHEMA_VERSION = "2.0"
#: 2 — додано `object_revisions` (v3 §18.1).
#: 3 — `relations.condition_json`: докази на ребрі (намір і мотив саме
#: цієї правки) писались у журнал і втрачались у проєкції.
#: Проєкція похідна від журналу, тому міграція = перебудова, а не ALTER.
DB_SCHEMA_VERSION = 3

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

#: Життєвий цикл обʼєкта (v3 §4.2 / SEM-004). Досі цієї множини не існувало
#: взагалі, тому поле було фактично вільним текстом, і в сховище потрапило
#: значення `current` — воно належить FRESHNESS, а не життєвому циклу.
LIFECYCLE_STATES = (
    "proposed",
    "accepted",
    "rejected",
    "superseded",
    "revoked",
    "stale",
)

#: Вид робочого елемента (v3 §6.1 `type`). Це НЕ `object_type`: обʼєкт має
#: тип `work_item`, а його вид — один із цих. Додати новий вид означає
#: додати його тут: у цьому й сенс контракту.
WORK_ITEM_TYPES = (
    "task",
    "bug",
    "epic",
    "spike",
)

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

#: Поверхні, які ІСНУЮТЬ у схемі, але не мають продюсера подій. Вони не
#: заповняться самі, і зі схеми це не видно. Список потрапляє в `doctor` і
#: перевіряється `tests/verify_surfaces.py` В ОБИДВА БОКИ: таблиця без
#: продюсера мусить бути тут, а та, що тут, — справді не мати продюсера.
#:
#: Це не «ще не зроблено»: це межа релізу, названа прямо.
RESERVED_SURFACES: dict[str, str] = {
    "usage_records": "облік токенів і вартості не веде ніхто; `budget_denied` недосяжний",
    "resource_locks": "блокування ресурсів зі специфікації §6.6 не реалізоване; зони — окремий механізм",
    "sessions": "сесія живе семантичним обʼєктом, ця таблиця не заповнюється",
    "sync_records": "мережевої синхронізації немає; обмін — файл-бандл вручну",
    "config_values": "конфіг читається з файлів через `config_value`, у таблицю не пишеться",
}

DEFAULTS = {
    #: Переноси шляхів у вигляді "старий/префікс/->новий/префікс/". Потрібні,
    #: коли коміти й події зберегли стару розкладку, а файли вже переїхали.
    #: Було прошито в коді як `src/cairn_unified/` -> корінь, тобто правило
    #: ОДНОГО репозиторію жило в загальному модулі.
    "path_rewrites": ["src/cairn_unified/->cairn_unified/"],
    #: Автор комітів, які створює воркер. Було прошито в `code/git.py`.
    "git_author": "Cairn Worker <worker@cairn.local>",
    "budget_chars": 8000,
    "lease_ttl_sec": 3600,
    "command_allowlist": ["python", "php", "git"],
    "policy_version": "1",
}
