"""Orchestrator settings — env-driven, validated at startup.

CHECKLISTS layer 11: exactly one auth mode active; SSE timeout budget must
cover the longest agent runtime class.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.supervise import FieldPredicate

logger = logging.getLogger(__name__)

INSECURE_DEFAULT_SECRET_KEY = "dev-only-not-a-secret"


@dataclass
class Settings:
    catalog_root: Path
    db_path: Path
    auth_mode: str = "bff"                    # bff (gateway-trusted) XOR oidc
    secret_key: str = INSECURE_DEFAULT_SECRET_KEY
    match_threshold: float = 0.35             # cascade agent/template match
    confirm_threshold: float = 0.5            # post-match confirmation (policy, calibrate)
    coverage_threshold: float = 0.15          # B1 tool coverage
    model_profile: str = "stub-class-ref"     # resolved profile (resolver = Phase 4)
    max_agent_runtime_s: int = 600
    sse_timeout_s: int = 3600
    register_commit: bool = True              # commit B1 registrations (epoch-pinned routing needs a SHA)
    harness_id: str = "stub-harness/0"        # conformance-list id dispatched for B2b (§10)
    operator_users: tuple[str, ...] = ()
    read_only_bindings: frozenset[str] = field(default_factory=frozenset)
    canary_opt_in: frozenset[str] = field(default_factory=frozenset)
    supervision_threshold: int = 20
    # entry_id -> {field_name: FieldPredicate} — operator-declared shadow
    # comparison tolerances (design §5.1: deliberately not in
    # AGENT_MANIFEST.yaml, so this is the only reachable place to set them).
    shadow_predicates: dict[str, dict[str, FieldPredicate]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.auth_mode not in {"bff", "oidc"}:
            raise ValueError(f"auth_mode must be bff or oidc, got {self.auth_mode!r}")
        if self.auth_mode == "oidc":
            raise NotImplementedError(
                "oidc auth mode is specced (UI-PLANE §7) but not built in Phase 1 — use bff"
            )
        if self.sse_timeout_s < self.max_agent_runtime_s:
            raise ValueError(
                "sse_timeout_s must cover max_agent_runtime_s (layer 11 budget rule)"
            )


_ENV_FIELDS = {
    "ORCH_AUTH_MODE": ("auth_mode", str),
    "ORCH_SECRET_KEY": ("secret_key", str),
    "ORCH_MATCH_THRESHOLD": ("match_threshold", float),
    "ORCH_CONFIRM_THRESHOLD": ("confirm_threshold", float),
    "ORCH_COVERAGE_THRESHOLD": ("coverage_threshold", float),
    "ORCH_MODEL_PROFILE": ("model_profile", str),
    "ORCH_HARNESS_ID": ("harness_id", str),
    "ORCH_SUPERVISION_THRESHOLD": ("supervision_threshold", int),
}


def _env(name: str) -> str | None:
    """Blank means unset: `.env.example` ships the optional keys empty and
    documents them as defaults, and `source .env` exports those as empty
    strings. Treating "" as a value makes the shipped template unstartable."""
    value = os.environ.get(name, "").strip()
    return value or None


def settings_from_env() -> Settings:
    """Env overrides on top of the dataclass defaults — the dataclass is the
    single source of truth for default values."""
    root = Path(_env("ORCH_CATALOG_ROOT") or ".").resolve()
    kwargs: dict = {
        "catalog_root": root,
        "db_path": Path(_env("ORCH_DB_PATH") or str(root / "orchestrator.sqlite3")),
    }
    for env_name, (field_name, cast) in _ENV_FIELDS.items():
        raw = _env(env_name)
        if raw is not None:
            kwargs[field_name] = cast(raw)
    operators = _env("ORCH_OPERATOR_USERS")
    if operators is not None:
        kwargs["operator_users"] = tuple(
            u.strip() for u in operators.split(",") if u.strip()
        )
    read_only = _env("ORCH_READ_ONLY_BINDINGS")
    if read_only is not None:
        kwargs["read_only_bindings"] = frozenset(
            b.strip() for b in read_only.split(",") if b.strip()
        )
    canary = _env("ORCH_CANARY_OPT_IN")
    if canary is not None:
        kwargs["canary_opt_in"] = frozenset(
            a.strip() for a in canary.split(",") if a.strip()
        )
    predicates = _env("ORCH_SHADOW_PREDICATES")
    if predicates is not None:
        # {"entry_id": {"field": {"kind": "numeric", "tolerance": 0.5}}}
        try:
            raw_predicates = json.loads(predicates)
        except json.JSONDecodeError as exc:
            raise ValueError(f"ORCH_SHADOW_PREDICATES is not valid JSON: {exc}") from exc
        kwargs["shadow_predicates"] = {
            entry_id: {name: FieldPredicate(**spec) for name, spec in fields.items()}
            for entry_id, fields in raw_predicates.items()
        }
    settings = Settings(**kwargs)
    if settings.secret_key == INSECURE_DEFAULT_SECRET_KEY:
        logger.warning(
            "ORCH_SECRET_KEY not set — sessions are signed with the insecure "
            "built-in default (%r). Set ORCH_SECRET_KEY before any non-local use.",
            INSECURE_DEFAULT_SECRET_KEY,
        )
    return settings
