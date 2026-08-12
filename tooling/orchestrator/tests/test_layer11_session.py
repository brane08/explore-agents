"""CHECKLISTS layer 11 [M] — session & auth."""
from __future__ import annotations

import pytest
from itsdangerous import Signer

from orch_fixtures import rebuild_and_commit
from orchestrator.config import Settings, settings_from_env
from orchestrator.webapp import SESSION_COOKIE


def test_cookie_is_signed_session_id_only_state_server_side(client, store):
    r = client.get("/")
    cookie = r.cookies[SESSION_COOKIE]
    sid = Signer("test-secret").unsign(cookie.encode()).decode()
    session = store.get_session(sid)
    assert session is not None
    assert session.user_sub == "dev@local"
    assert session.catalog_ref and session.thread_id       # server-side state
    assert list(r.cookies.keys()) == [SESSION_COOKIE]      # nothing else client-side


def test_tampered_cookie_gets_a_fresh_session(client):
    client.get("/")
    client.cookies.set(SESSION_COOKIE, "forged-value")
    r = client.get("/")
    assert r.status_code == 200
    assert r.cookies[SESSION_COOKIE] != "forged-value"     # re-issued, signed


def test_catalog_ref_frozen_at_session_creation(client, store, catalog_repo):
    r = client.get("/")
    sid = Signer("test-secret").unsign(r.cookies[SESSION_COOKIE].encode()).decode()
    pinned = store.get_session(sid).catalog_ref

    (catalog_repo / "tags.yaml").write_text(
        (catalog_repo / "tags.yaml").read_text() + "  - new-tag\n", encoding="utf-8")
    new_head = rebuild_and_commit(catalog_repo, "move the catalog")

    r = client.get("/")                                    # same cookie, same pin
    assert store.get_session(sid).catalog_ref == pinned != new_head
    assert "catalog has moved" in r.text                   # lag banner offers new session

    client.post("/session/new")
    r = client.get("/")
    sid2 = Signer("test-secret").unsign(client.cookies[SESSION_COOKIE].encode()).decode()
    assert store.get_session(sid2).catalog_ref == new_head


def test_exactly_one_auth_mode(settings, tmp_path):
    with pytest.raises(ValueError):
        Settings(catalog_root=settings.catalog_root, db_path=tmp_path / "x.db",
                 auth_mode="both")
    with pytest.raises(NotImplementedError):
        Settings(catalog_root=settings.catalog_root, db_path=tmp_path / "x.db",
                 auth_mode="oidc")


def test_blank_env_values_fall_back_to_defaults(monkeypatch, tmp_path):
    """`.env.example` ships the optional keys blank and documents them as
    "unset". Sourcing that file exports empty strings, so a blank value must
    mean the default — otherwise the shipped template cannot start the app."""
    monkeypatch.setenv("ORCH_CATALOG_ROOT", str(tmp_path))
    for blank in ("ORCH_DB_PATH", "ORCH_MODEL_PROFILE", "ORCH_HARNESS_ID",
                  "ORCH_SECRET_KEY", "ORCH_AUTH_MODE", "ORCH_MATCH_THRESHOLD"):
        monkeypatch.setenv(blank, "")

    settings = settings_from_env()

    defaults = Settings(catalog_root=tmp_path, db_path=tmp_path / "x.db")
    assert settings.db_path == tmp_path / "orchestrator.sqlite3"
    assert settings.model_profile == defaults.model_profile
    assert settings.harness_id == defaults.harness_id
    assert settings.auth_mode == defaults.auth_mode
    assert settings.match_threshold == defaults.match_threshold


def test_sse_timeout_budget_covers_max_runtime(settings, tmp_path):
    with pytest.raises(ValueError, match="budget"):
        Settings(catalog_root=settings.catalog_root, db_path=tmp_path / "x.db",
                 sse_timeout_s=10, max_agent_runtime_s=600)
    assert settings.sse_timeout_s >= settings.max_agent_runtime_s
