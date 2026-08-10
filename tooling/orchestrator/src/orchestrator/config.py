"""Orchestrator settings — env-driven, validated at startup.

CHECKLISTS layer 11: exactly one auth mode active; SSE timeout budget must
cover the longest agent runtime class.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

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
}


def settings_from_env() -> Settings:
    """Env overrides on top of the dataclass defaults — the dataclass is the
    single source of truth for default values."""
    root = Path(os.environ.get("ORCH_CATALOG_ROOT", ".")).resolve()
    kwargs: dict = {
        "catalog_root": root,
        "db_path": Path(os.environ.get("ORCH_DB_PATH", str(root / "orchestrator.sqlite3"))),
    }
    for env_name, (field_name, cast) in _ENV_FIELDS.items():
        if env_name in os.environ:
            kwargs[field_name] = cast(os.environ[env_name])
    if "ORCH_OPERATOR_USERS" in os.environ:
        kwargs["operator_users"] = tuple(
            u for u in os.environ["ORCH_OPERATOR_USERS"].split(",") if u
        )
    settings = Settings(**kwargs)
    if settings.secret_key == INSECURE_DEFAULT_SECRET_KEY:
        logger.warning(
            "ORCH_SECRET_KEY not set — sessions are signed with the insecure "
            "built-in default (%r). Set ORCH_SECRET_KEY before any non-local use.",
            INSECURE_DEFAULT_SECRET_KEY,
        )
    return settings
