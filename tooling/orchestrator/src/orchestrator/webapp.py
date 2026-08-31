"""Meta UI + endpoint contract (UI-PLANE §3–§6, Phase 1 subset).

FastAPI + Jinja2 fragments + htmx/SSE. All state server-side; the cookie
carries only a signed session id. Sessions pin `catalog_ref` at creation;
the router reads the lock at that pin via `git show` — only trust recall
pierces the pin (checked live at invocation).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, Signer
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sse_starlette.sse import EventSourceResponse

import shutil
import subprocess

import yaml as _yaml

from certify.candidate import load_candidate
from certify.playbook import HarnessInputs
from certify.steps import (
    promote as certify_promote,
    select_evalmatrix_rows,
    step_eval,
    step_layout_manifest,
    step_rebase,
    step_security_static,
    step_status,
    step_structural,
)

from orchestrator import assembly as assembly_mod
from orchestrator.config import Settings, settings_from_env
from orchestrator.criteria import CriteriaError
from orchestrator.dispatch import dispatch_b2b, select_harness
from orchestrator.errors import CODES, StructuredError
from orchestrator.executor import ToolInvoker, run_invocation
from orchestrator.lockload import current_ref, entry_by_id, load_lock_at
from orchestrator.router import allowed_tiers, route
from orchestrator.scoring import Scorer, ScorerError, select_scorer
from orchestrator.store import SQLiteStore, Store
from orchestrator.supervise import open_canary, open_shadow, run_shadow, select_mode
from orchestrator.trustcheck import load_records, transitive_tier

logger = logging.getLogger(__name__)

_TEMPLATES = Path(__file__).parent / "templates"
_STATIC = Path(__file__).parent / "static"

SESSION_COOKIE = "orch_session"


_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


def stub_summary_writer(spec_text: str, manifest=None) -> str:
    """Deterministic stand-in for the §8.7 certify-model summary writer.

    CATALOG §4 wants 1–2 *sentences*; SPEC.md is hard-wrapped prose, so reading
    the first physical line cuts mid-sentence and publishes a fragment as the
    text the router embeds. Unwrap the first paragraph of the capability
    restatement, then cut on sentence boundaries. The certify-model writer rides
    this same seam when credentials exist.
    """
    paragraph: list[str] = []
    for raw in spec_text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if not line:
            if paragraph:
                break
            continue
        paragraph.append(line)
    if not paragraph:
        return "Certified capability."
    sentences = _SENTENCE_END_RE.split(" ".join(paragraph))
    return " ".join(s for s in sentences[:2] if s).strip()


def _no_tool_backend(tool_id: str, args: dict) -> dict:
    raise RuntimeError(
        "no tool backend configured — install orchestrator[mcp] and run mcp_server, "
        "or inject an invoker"
    )


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    scorer: Scorer | None = None,
    invoke_tool: ToolInvoker | None = None,
) -> FastAPI:
    settings = settings or settings_from_env()
    store = store or SQLiteStore(settings.db_path)
    scorer = scorer or select_scorer()
    if invoke_tool is None:
        try:
            from orchestrator.mcpinvoke import mcp_invoker
            invoke_tool = mcp_invoker()
        except ImportError:
            invoke_tool = _no_tool_backend

    jinja = Environment(
        loader=FileSystemLoader(_TEMPLATES),
        autoescape=select_autoescape(["html", "j2"]),
    )
    signer = Signer(settings.secret_key)
    app = FastAPI(title="agent-platform orchestrator")
    app.state.settings, app.state.store = settings, store
    if _STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    def page(name: str, **ctx) -> HTMLResponse:
        return HTMLResponse(jinja.get_template(name).render(**ctx))

    def render_error(err: StructuredError) -> str:
        return jinja.get_template(f"errors/{err.code}.html.j2").render(error=err)

    def render_event(event) -> str:
        return jinja.get_template("event.html.j2").render(e=event)

    # -- identity & session ---------------------------------------------------

    def user_sub(request: Request) -> str:
        # bff mode: gateway-injected identity (UI-PLANE §7); dev fallback sub
        return request.headers.get("X-Forwarded-User", "dev@local")

    def is_operator(request: Request) -> bool:
        roles = request.headers.get("X-Forwarded-Roles", "")
        return ("operator" in {r.strip() for r in roles.split(",")}
                or user_sub(request) in settings.operator_users)

    def require_operator(request: Request) -> None:
        if not is_operator(request):
            raise HTTPException(status_code=403, detail="operator role required")

    def get_session(request: Request):
        raw = request.cookies.get(SESSION_COOKIE)
        if raw:
            try:
                sid = signer.unsign(raw.encode()).decode()
            except BadSignature:
                sid = None
            if sid:
                session = store.get_session(sid)
                # bff mode: the gateway identity is authoritative — a cookie
                # minted for another user never carries over
                if session and session.user_sub == user_sub(request):
                    return session, False
        session = store.create_session(
            user_sub(request), current_ref(settings.catalog_root))
        return session, True

    def attach_cookie(response, session) -> None:
        response.set_cookie(
            SESSION_COOKIE, signer.sign(session.session_id.encode()).decode(),
            httponly=True, samesite="lax")

    # -- console ---------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def console(request: Request):
        session, created = get_session(request)
        response = page(
            "console.html.j2",
            session=session,
            current=current_ref(settings.catalog_root),
            operator=is_operator(request),
        )
        if created:
            attach_cookie(response, session)
        return response

    @app.post("/session/new")
    def new_session(request: Request):
        session = store.create_session(
            user_sub(request), current_ref(settings.catalog_root))
        response = RedirectResponse("/", status_code=303)
        attach_cookie(response, session)
        return response

    @app.get("/health")
    def health() -> HTMLResponse:
        checks: dict[str, str] = {}
        ok = True
        try:
            store.error_records()
            checks["db"] = "ok"
        except Exception as exc:                      # noqa: BLE001 — report, don't crash
            ok = False
            checks["db"] = f"error: {exc}"
        try:
            load_lock_at(settings.catalog_root, current_ref(settings.catalog_root))
            checks["catalog_lock"] = "ok"
        except Exception as exc:                      # noqa: BLE001 — report, don't crash
            ok = False
            checks["catalog_lock"] = f"error: {exc}"
        body = json.dumps({"status": "ok" if ok else "error", "checks": checks})
        return HTMLResponse(body, status_code=200 if ok else 503,
                            media_type="application/json")

    # -- task submission (the cascade) -----------------------------------------

    def _persist_routing(invocation_id: str, events) -> None:
        for ev in events:
            store.append_event(invocation_id, "routing", ev.data())

    def _execute(invocation_id: str, session, task_text: str,
                 agent_entry: dict | None, tools: list[dict],
                 register_after: bool) -> None:
        """Background execution; registers a B1 assembly on first success.

        Must never raise: an escaped exception would leave the invocation
        'running' forever and the SSE stream spinning until its timeout —
        every exit path emits a terminal event."""
        agent_id = agent_entry["id"] if agent_entry else "b1-candidate"
        version = agent_entry["version"] if agent_entry else "1.0.0"
        try:
            ok, result_ref, _result = run_invocation(
                store, invocation_id,
                agent_id=agent_id, agent_version=version,
                model_profile=settings.model_profile,
                catalog_ref=session.catalog_ref,
                task_text=task_text, tools=tools,
                invoke_tool=invoke_tool, scorer=scorer,
            )
            if ok and register_after:
                # first successful use registers — before terminal, so the
                # registration event is part of the (streamed) trace
                reg = assembly_mod.assemble_and_register(task_text, tools, settings)
                store.append_event(invocation_id, "routing", {
                    "cascade_step": "b1-registered" if not reg.already_registered
                    else "b1-deduplicated",
                    "entry_id": reg.agent_id,
                })
                store.set_invocation(invocation_id, agent_id=reg.agent_id)
        except Exception as exc:
            ok, result_ref = False, f"{type(exc).__name__}: {exc}"
        store.append_event(invocation_id, "terminal", {
            "status": "COMPLETE" if ok else "ERROR", "result_ref": result_ref,
        })
        store.set_invocation(invocation_id, status="complete" if ok else "error")

    def _resolve_bindings(lock: dict, agent_entry: dict) -> list[dict] | None:
        """Every manifest binding must resolve at the pinned ref — running an
        agent with fewer tools than it declares is a silent capability lie."""
        tools = [entry_by_id(lock, b["id"]) for b in agent_entry.get("bindings", [])]
        return None if any(t is None for t in tools) else tools

    def _refuse(invocation, session, created: bool, context: str) -> HTMLResponse:
        err = StructuredError("STALE_ENTRY", context)
        store.append_event(invocation.invocation_id, "terminal",
                           {"status": "ERROR", "result_ref": f"STALE_ENTRY {context}"})
        store.set_invocation(invocation.invocation_id, status="error")
        response = HTMLResponse(render_error(err), status_code=409)
        if created:
            attach_cookie(response, session)
        return response

    def _pinned_lock(session) -> dict:
        try:
            return load_lock_at(settings.catalog_root, session.catalog_ref)
        except Exception:
            raise HTTPException(
                status_code=409,
                detail="pinned catalog ref is no longer resolvable — start a new session",
            )

    def _counterpart_for(task_text: str, lock: dict, entry: dict, session) -> dict | None:
        """The entry the cascade would have routed to if this agent did not
        exist — the comparison the caller would otherwise have received.

        This is a probe, not a real dispatch: a miss must not look like a
        routing refusal. `record_side_effects=False` stops it from writing
        `record_error`/`queue_stale` rows into the B2b backlog and stale
        queue on every supervised dispatch that doesn't find a counterpart."""
        records = load_records(settings.catalog_root)
        pruned = dict(lock, entries=[
            e for e in lock.get("entries", [])
            if e["id"] != entry["id"]
            and transitive_tier(records, e, lock, settings.model_profile) == "validated"
        ])
        result = route(task_text, pruned, settings.catalog_root, session.user_sub,
                       scorer, settings, store, operator=False,
                       record_side_effects=False)
        return result.agent_entry if result.outcome == "invoke-agent" else None

    def _run_agent(inv_id: str, session, task_text: str, agent_entry: dict,
                   lock: dict, *, supervised: bool) -> tuple[bool, str, dict]:
        """Foreground run of `agent_entry` on invocation `inv_id`; returns
        (ok, result_ref, result). Does not emit the terminal event — callers
        decide when (or whether) to close the invocation, since canary's
        fallback needs to run a second agent on the same `inv_id` before
        anything terminal is recorded. Must never raise."""
        tools = _resolve_bindings(lock, agent_entry)
        try:
            if tools is None:
                msg = f"{agent_entry['id']} has bindings missing at the pinned ref"
                return False, msg, {"error": msg}
            return run_invocation(
                store, inv_id,
                agent_id=agent_entry["id"], agent_version=agent_entry["version"],
                model_profile=settings.model_profile,
                catalog_ref=session.catalog_ref,
                task_text=task_text, tools=tools,
                invoke_tool=invoke_tool, scorer=scorer, supervised=supervised,
            )
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            return False, msg, {"error": msg}

    def _run_agent_now(inv_id: str, session, task_text: str, agent_entry: dict,
                       lock: dict, *, supervised: bool) -> tuple[bool, str, dict]:
        """`_run_agent` plus the terminal event, the way `_execute` would once
        it completes.

        Used both for a shadow mode's counterpart (supervised=False — a
        normal validated run) and for the quarantined agent's own run in
        either mode (supervised=True). Must never raise, same invariant as
        `_execute`: every exit path emits a terminal event."""
        ok, result_ref, result = _run_agent(inv_id, session, task_text, agent_entry, lock,
                                            supervised=supervised)
        store.append_event(inv_id, "terminal", {
            "status": "COMPLETE" if ok else "ERROR", "result_ref": result_ref,
        })
        store.set_invocation(inv_id, status="complete" if ok else "error")
        return ok, result_ref, result

    def _dispatch_supervised(plan, entry: dict, invocation, session, task_text: str,
                             lock: dict, background: BackgroundTasks, created: bool,
                             counterpart: dict | None) -> HTMLResponse:
        """`plan.mode` is "shadow" or "canary" — "refuse" is handled by the
        caller before this is ever reached. `counterpart` is the entry
        `_counterpart_for` found (may be None) — shadow ignores it and uses
        `plan.counterpart` (guaranteed set when mode is shadow); canary uses
        it for its error fallback.

        Shadow: the counterpart runs in the foreground and its result is the
        HTTP response; the quarantined agent's own run happens in a
        background task so it can never block or leak into that response,
        and its (real) output feeds `run_shadow` once it completes.

        Canary: the quarantined agent serves the request itself — same
        async dispatch pattern as an ordinary invoke. On success a `pending`
        row is opened for a human to adjudicate. On failure the error is
        agent-attributable evidence (design §4), so the row auto-closes
        `incident` and the caller is served the counterpart's result instead
        of the raw failure (design §2.3 / layer 8 "canary with fallback");
        with no counterpart to fall back to, the caller gets the standard
        refusal convention instead of a propagated failure."""
        def _fail_open_invocation(exc: Exception) -> None:
            # last-resort net (invoke-ui#5): if the invocation trace wasn't
            # already closed by the try block below, close it here so the
            # SSE stream never spins until sse_timeout_s with nothing to show.
            if store.get_invocation(invocation.invocation_id).status == "running":
                store.append_event(invocation.invocation_id, "terminal", {
                    "status": "ERROR", "result_ref": f"{type(exc).__name__}: {exc}",
                })
                store.set_invocation(invocation.invocation_id, status="error")

        if plan.mode == "shadow":
            counterpart = plan.counterpart
            cp_invocation = store.create_invocation(session.session_id, counterpart["id"])
            cp_ok, cp_ref, cp_result = _run_agent_now(
                cp_invocation.invocation_id, session, task_text, counterpart, lock,
                supervised=False)

            # opened before the quarantined agent runs (invoke-ui#4): a crash
            # between the run finishing and the row committing must not leave
            # zero evidence for a quarantined agent that served real work.
            shadow_run_id = open_shadow(
                store, entry=entry, model_profile=settings.model_profile,
                invocation_id=invocation.invocation_id, counterpart_id=counterpart["id"])

            def _shadow_task() -> None:
                # must never raise (invoke-ui#5): _run_agent_now already
                # guards its own errors, so this only catches a bug in
                # run_shadow/close_supervised_run leaving the ledger row (or
                # the invocation trace) stuck with no terminal state.
                try:
                    ok, result_ref, result = _run_agent_now(
                        invocation.invocation_id, session, task_text, entry, lock,
                        supervised=True)
                    run_shadow(
                        store, run_id=shadow_run_id,
                        counterpart_output=cp_result if cp_ok else None,
                        shadow_output=result if ok else None,
                        error=None if ok else result_ref,
                        predicates=settings.shadow_predicates.get(entry["id"], {}),
                    )
                except Exception as exc:
                    try:
                        store.close_supervised_run(
                            shadow_run_id, verdict="incident",
                            reason=f"{type(exc).__name__}: {exc}")
                    except ValueError:
                        pass  # already closed before the exception was raised
                    _fail_open_invocation(exc)

            background.add_task(_shadow_task)
            response = page(
                "invocation.html.j2",
                invocation=store.get_invocation(cp_invocation.invocation_id),
                events=store.events_after(cp_invocation.invocation_id, 0),
            )
        else:  # canary — select_mode guarantees a counterpart for this mode
            # opened before the agent runs (invoke-ui#4): see shadow above.
            canary_run_id = open_canary(store, entry=entry, model_profile=settings.model_profile,
                                        invocation_id=invocation.invocation_id)

            def _canary_task() -> None:
                # must never raise (invoke-ui#5): see _shadow_task.
                try:
                    ok, result_ref, _result = _run_agent(
                        invocation.invocation_id, session, task_text,
                        entry, lock, supervised=True)
                    if ok:
                        store.append_event(invocation.invocation_id, "terminal", {
                            "status": "COMPLETE", "result_ref": result_ref,
                        })
                        store.set_invocation(invocation.invocation_id, status="complete")
                        return
                    # agent-attributable execution error (design §4) — close
                    # the ledger row now rather than leaving it pending
                    store.close_supervised_run(canary_run_id, verdict="incident",
                                               reason=result_ref)
                    _run_agent_now(invocation.invocation_id, session, task_text,
                                   counterpart, lock, supervised=False)
                except Exception as exc:
                    try:
                        store.close_supervised_run(
                            canary_run_id, verdict="incident",
                            reason=f"{type(exc).__name__}: {exc}")
                    except ValueError:
                        pass
                    _fail_open_invocation(exc)

            background.add_task(_canary_task)
            response = page("task_started.html.j2", invocation=invocation)

        if created:
            attach_cookie(response, session)
        return response

    def _dispatch_quarantined(entry: dict, invocation, session, task_text: str,
                              lock: dict, background: BackgroundTasks,
                              created: bool) -> HTMLResponse:
        """Layer 8: quarantined agents run only in supervised mode. Shared by
        the direct invoke route (`/agents/{id}/invoke`) and the routing
        cascade (`/tasks`) — both reach a quarantined entry, and it is the
        same invariant either way. Caller must already know `entry`'s live
        tier is `quarantined` before calling this."""
        counterpart = _counterpart_for(task_text, lock, entry, session)
        plan = select_mode(entry, counterpart=counterpart,
                           read_only_bindings=set(settings.read_only_bindings),
                           canary_opt_in=set(settings.canary_opt_in))
        if plan.mode == "refuse":
            return _refuse(invocation, session, created,
                           f"{entry['id']} supervised invocation unavailable: "
                           f"{plan.reason}")
        return _dispatch_supervised(plan, entry, invocation, session, task_text,
                                    lock, background, created, counterpart)

    @app.post("/tasks", response_class=HTMLResponse)
    def submit_task(request: Request, background: BackgroundTasks,
                    task: str = Form(...)):
        session, created = get_session(request)
        lock = _pinned_lock(session)
        invocation = store.create_invocation(session.session_id, None)
        logger.info("task dispatch session_id=%s invocation_id=%s",
                    session.session_id, invocation.invocation_id)

        try:
            result = route(task, lock, settings.catalog_root, session.user_sub,
                           scorer, settings, store, operator=is_operator(request))
        except ScorerError as exc:
            # The scorer gates every cascade step. A scorer that cannot answer
            # must not be rounded down to "no match": that would record a false
            # PATTERN_UNRECOGNIZED (poisoning the B2 backlog signal) or send
            # reusable capability to generation. Fail the invocation loudly.
            logger.warning("scorer unavailable session_id=%s invocation_id=%s: %s",
                           session.session_id, invocation.invocation_id, exc)
            store.append_event(invocation.invocation_id, "terminal", {
                "status": "ERROR", "result_ref": f"scorer unavailable: {exc}",
            })
            store.set_invocation(invocation.invocation_id, status="error")
            response = HTMLResponse(
                "<p class='error'>Routing scorer unavailable — no routing decision "
                "was made. Retry, or check ORCH_SCORER configuration.</p>",
                status_code=503,
            )
            if created:
                attach_cookie(response, session)
            return response
        _persist_routing(invocation.invocation_id, result.events)

        if result.outcome == "error":
            logger.info("routing refused session_id=%s invocation_id=%s code=%s",
                        session.session_id, invocation.invocation_id, result.error.code)
            store.append_event(invocation.invocation_id, "terminal", {
                "status": "ERROR",
                "result_ref": f"{result.error.code} {result.error.context}".strip(),
            })
            store.set_invocation(invocation.invocation_id, status="error")
            response = HTMLResponse(
                render_error(result.error)
                + (_generate_form(task)
                   if result.error.code == "PATTERN_UNRECOGNIZED"
                   and is_operator(request) else ""))
        else:
            if result.outcome == "invoke-agent":
                agent_entry = result.agent_entry
                # same invariant as the direct invoke route: a quarantined
                # entry reached via the cascade still runs only supervised
                records = load_records(settings.catalog_root)
                tier = transitive_tier(records, agent_entry, lock, settings.model_profile)
                if tier == "quarantined":
                    return _dispatch_quarantined(agent_entry, invocation, session, task,
                                                 lock, background, created)
                tools = _resolve_bindings(lock, agent_entry)
                if tools is None:
                    return _refuse(invocation, session, created,
                                   f"{agent_entry['id']} has bindings missing at the pinned ref")
                store.set_invocation(invocation.invocation_id, agent_id=agent_entry["id"])
                register_after = False
            else:  # assemble-b1
                agent_entry, tools, register_after = None, result.tools, True
            background.add_task(
                _execute, invocation.invocation_id, session, task,
                agent_entry, tools, register_after)
            response = page("task_started.html.j2", invocation=invocation)
        if created:
            attach_cookie(response, session)
        return response

    # -- B2b generation (operator-triggered, ROADMAP §3) -------------------------

    def _generate_form(task: str) -> str:
        return jinja.get_template("generate_offer.html.j2").render(task=task)

    def _impl_profile() -> dict | None:
        """The resolved model profile as catalog data, for the independence check.

        The lock carries routing fields only, so `provider` / `profile_class`
        come from the profile entry itself. Missing profile ⇒ None: the check
        then records "implementation family unknown" instead of asserting a
        diversity it could not verify.
        """
        entry = (settings.catalog_root / "models" / settings.model_profile
                 / "entry.yaml")
        if not entry.is_file():
            return None
        return _yaml.safe_load(entry.read_text(encoding="utf-8")) or None

    @app.post("/generate", response_class=HTMLResponse)
    def request_generation(request: Request, task: str = Form(...)):
        """Dispatch B2b for a task the cascade could not cover.

        The cascade is re-run first and generation is refused unless it still
        reports PATTERN_UNRECOGNIZED. Without that, a hand-posted form would be
        a way around the one control that stops the catalog from growing a
        second copy of a capability it already has — and the residual recorded
        as B2 backlog would no longer match what was actually generated.
        """
        require_operator(request)
        session, created = get_session(request)
        lock = _pinned_lock(session)

        result = route(task, lock, settings.catalog_root, session.user_sub,
                       scorer, settings, store, operator=True)
        if not (result.outcome == "error"
                and result.error.code == "PATTERN_UNRECOGNIZED"):
            raise HTTPException(
                status_code=409,
                detail="the cascade covers this task — generation is for "
                       "PATTERN_UNRECOGNIZED residuals only",
            )

        try:
            dispatched = dispatch_b2b(
                task, settings.catalog_root, lock, settings,
                settings.harness_id, select_harness(),
                catalog_ref=session.catalog_ref,
                impl_profile=_impl_profile(),
            )
        except CriteriaError as exc:
            # Criteria that nobody authored produce evidence that means nothing;
            # no candidate is better than an uncertifiable one.
            raise HTTPException(status_code=409,
                                detail=f"criteria could not be authored: {exc}")

        if dispatched.outcome != "candidate":
            raise HTTPException(status_code=409, detail=dispatched.refusal)

        logger.info("b2b dispatched session_id=%s candidate=%s",
                    session.session_id, dispatched.candidate_dir.name)
        response = RedirectResponse(f"/certify/{dispatched.candidate_dir.name}",
                                    status_code=303)
        if created:
            attach_cookie(response, session)
        return response

    # -- invocation stream + detail ---------------------------------------------

    def _authorize_invocation(request: Request, invocation) -> None:
        # invoke-ui#9: an invocation id is low-enumerability but not access
        # control — without this, any caller could read another user's
        # trace, including a shadow invocation's real quarantined-agent
        # output. Session-ownership or operator, same gate either endpoint.
        if is_operator(request):
            return
        session, _ = get_session(request)
        if invocation.session_id != session.session_id:
            raise HTTPException(status_code=403, detail="not authorized for this invocation")

    @app.get("/invocations/{invocation_id}/stream")
    async def stream(invocation_id: str, request: Request):
        invocation = store.get_invocation(invocation_id)
        if invocation is None:
            raise HTTPException(status_code=404)
        _authorize_invocation(request, invocation)
        last = request.headers.get("Last-Event-ID") or request.query_params.get("last") or "0"
        try:
            seen = int(last)
        except ValueError:
            seen = 0

        async def gen():
            nonlocal seen
            budget = settings.sse_timeout_s
            waited = 0.0
            while True:
                events = store.events_after(invocation_id, seen)
                for e in events:
                    seen = e.seq
                    yield {"id": str(e.seq), "event": e.event_type,
                           "data": render_event(e)}
                    if e.event_type == "terminal":
                        return
                await asyncio.sleep(0.2)
                waited += 0.2
                if waited > budget:
                    return

        return EventSourceResponse(gen())

    @app.get("/invocations/{invocation_id}", response_class=HTMLResponse)
    def invocation_detail(invocation_id: str, request: Request):
        invocation = store.get_invocation(invocation_id)
        if invocation is None:
            raise HTTPException(status_code=404)
        _authorize_invocation(request, invocation)
        events = store.events_after(invocation_id, 0)
        return page("invocation.html.j2", invocation=invocation, events=events)

    # -- catalog browser (meta screen — operator role) -----------------------------

    @app.get("/agents", response_class=HTMLResponse)
    def agents(request: Request, _=Depends(require_operator)):
        session, created = get_session(request)
        response = page("agents.html.j2",
                        entries=_pinned_lock(session).get("entries", []), session=session)
        if created:
            attach_cookie(response, session)
        return response

    @app.get("/agents/{agent_id}", response_class=HTMLResponse)
    def agent_page(agent_id: str, request: Request):
        session, created = get_session(request)
        entry = entry_by_id(_pinned_lock(session), agent_id)
        if entry is None:
            raise HTTPException(status_code=404)
        response = page("agent.html.j2", entry=entry, session=session)
        if created:
            attach_cookie(response, session)
        return response

    @app.post("/agents/{agent_id}/invoke", response_class=HTMLResponse)
    def invoke_agent(agent_id: str, request: Request, background: BackgroundTasks,
                     task: str = Form(...)):
        session, created = get_session(request)
        lock = _pinned_lock(session)
        entry = entry_by_id(lock, agent_id)
        if entry is None:
            raise HTTPException(status_code=404)
        # same refusals as the routing cascade: direct invoke is not a bypass
        if entry.get("stale"):
            store.queue_stale(entry["id"], entry.get("stale_reason", "stale"))
            err = StructuredError(
                "STALE_ENTRY", f"{agent_id} {entry.get('stale_reason', 'stale')}")
            response = HTMLResponse(render_error(err), status_code=409)
            if created:
                attach_cookie(response, session)
            return response
        # tier + user authz checked here, live (recall pierces the pin)
        records = load_records(settings.catalog_root)
        tier = transitive_tier(records, entry, lock, settings.model_profile)
        user_tiers = allowed_tiers(session.user_sub, settings,
                                   operator=is_operator(request))
        if tier not in user_tiers:
            store.queue_stale(entry["id"], f"tier:{tier}")
            err = StructuredError("STALE_ENTRY", f"{agent_id} refused at live tier {tier}")
            response = HTMLResponse(render_error(err), status_code=403)
            if created:
                attach_cookie(response, session)
            return response
        if tier == "quarantined":
            # layer 8: quarantined agents run only in supervised mode. Only
            # operators reach here — allowed_tiers() grants "quarantined" to
            # operators alone, so a non-operator was already refused above.
            invocation = store.create_invocation(session.session_id, agent_id)
            return _dispatch_quarantined(entry, invocation, session, task, lock,
                                         background, created)
        tools = _resolve_bindings(lock, entry)
        invocation = store.create_invocation(session.session_id, agent_id)
        if tools is None:
            return _refuse(invocation, session, created,
                           f"{agent_id} has bindings missing at the pinned ref")
        background.add_task(
            _execute, invocation.invocation_id, session, task, entry, tools, False)
        response = page("task_started.html.j2", invocation=invocation)
        if created:
            attach_cookie(response, session)
        return response

    # -- certify queue (meta screen — operator role, UI-PLANE §3.2) -------------

    _proposed_root = settings.catalog_root / "agents" / "_proposed"

    def _load_frozen_inputs(cid: str) -> HarnessInputs | None:
        sidecar = _proposed_root / f"{cid}.inputs.yaml"
        if not sidecar.is_file():
            return None
        data = _yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {}
        ref = data.get("catalog_ref", "")
        try:
            manifest = load_lock_at(settings.catalog_root, ref)
        except Exception:
            manifest = {"entries": []}
        return HarnessInputs(
            task_spec=data.get("task_spec", ""),
            behavioral_criteria=data.get("behavioral_criteria", ""),
            catalog_ref=ref,
            capability_manifest=manifest,
            trust_tier_ceiling=data.get("trust_tier_ceiling", "validated"),
            mode=data.get("mode", "B2b"),
            scope=data.get("scope", "full-agent"),
            model_profile=data.get("model_profile", ""),
            harness=data.get("harness", ""),
            target_runtime=data.get("target_runtime", ""),
            residual=data.get("residual"),
            nearest_templates=data.get("nearest_templates") or [],
        )

    def _candidate_or_404(cid: str):
        cdir = _proposed_root / cid
        if "/" in cid or not cdir.is_dir():
            raise HTTPException(status_code=404)
        return cdir

    def _current_lock() -> dict:
        return load_lock_at(settings.catalog_root, current_ref(settings.catalog_root))

    def _mechanical_steps(candidate, inputs: HarnessInputs, lock: dict):
        """Steps 1-3, 5, 6 — the model-independent half of certify.

        The queue screen and the promote action run this one function, so what
        an operator approves is what was actually checked. Step 3 goes last and
        is handed the security result: it executes candidate code, and its own
        guard refuses to run behind a failed scan.
        """
        security = step_security_static(candidate, inputs)
        return [
            step_layout_manifest(candidate, inputs, settings.catalog_root),
            step_status(candidate),
            step_rebase(candidate, lock),
            step_structural(candidate, inputs),
            security,
            step_eval(candidate, security=security),
        ]

    def _run_lockbuild(root: Path) -> None:
        from lockbuild.build import LOCK_FILENAME, build_lock, render_lock
        (root / LOCK_FILENAME).write_text(render_lock(build_lock(root)),
                                          encoding="utf-8")

    def _git_commit(paths, message: str) -> None:
        # stage exactly `paths` — never `-A`, which would sweep in whatever
        # else is dirty in the checkout and make the promotion commit
        # unauditable (CATALOG §8 step 7: atomic to the promotion, not the
        # whole tree; certify/cli.py's _git_commit uses the same fix).
        existing = [str(p) for p in paths if p.exists()]
        subprocess.run(["git", "-C", str(settings.catalog_root), "add", "--",
                        *existing], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(settings.catalog_root), "commit", "-q",
                        "-m", message], check=True, capture_output=True)

    @app.get("/certify", response_class=HTMLResponse)
    def certify_queue(request: Request, _=Depends(require_operator)):
        session, created = get_session(request)
        candidates = []
        if _proposed_root.is_dir():
            for cdir in sorted(p for p in _proposed_root.iterdir() if p.is_dir()):
                c = load_candidate(cdir)
                candidates.append({
                    "cid": cdir.name,
                    "agent_id": c.manifest.agent_id if c.ok else "(unloadable)",
                    "status": c.manifest.status if c.ok else "; ".join(c.load_errors),
                    "has_inputs": (_proposed_root / f"{cdir.name}.inputs.yaml").is_file(),
                })
        response = page("certify_queue.html.j2", candidates=candidates, session=session)
        if created:
            attach_cookie(response, session)
        return response

    @app.get("/certify/{cid}", response_class=HTMLResponse)
    def certify_detail(cid: str, request: Request, _=Depends(require_operator)):
        cdir = _candidate_or_404(cid)
        session, created = get_session(request)
        candidate = load_candidate(cdir)
        inputs = _load_frozen_inputs(cid)
        steps, audit, matrix = [], [], ([], [])
        if candidate.ok and inputs is not None:
            lock = _current_lock()
            steps = _mechanical_steps(candidate, inputs, lock)
            matrix = select_evalmatrix_rows(candidate, inputs.nearest_templates, lock)
            by_id = {e["id"]: e for e in lock.get("entries", [])}
            audit = [{"id": b.id, "version": b.version,
                      "tier": by_id.get(b.id, {}).get("trust_tier", "(gone)")}
                     for b in candidate.manifest.bindings]
        response = page("certify_candidate.html.j2",
                        cid=cid, candidate=candidate, inputs=inputs, steps=steps,
                        audit=audit, applicable=matrix[0], inapplicable=matrix[1],
                        session=session)
        if created:
            attach_cookie(response, session)
        return response

    @app.post("/certify/{cid}/promote", response_class=HTMLResponse)
    def certify_promote_action(cid: str, request: Request,
                               _=Depends(require_operator)):
        cdir = _candidate_or_404(cid)
        sidecar = _proposed_root / f"{cid}.inputs.yaml"

        # Re-run the mechanical steps against the tree as it is now, not as the
        # detail screen found it: an operator's approval may be minutes old and
        # the lock may have moved under it. This also produces the eval evidence
        # promote() then verifies independently.
        candidate = load_candidate(cdir)
        inputs = _load_frozen_inputs(cid)
        if not candidate.ok or inputs is None:
            raise HTTPException(status_code=409,
                                detail="; ".join(candidate.load_errors)
                                or "frozen §0 inputs missing — cannot re-verify")
        failed = [s for s in _mechanical_steps(candidate, inputs, _current_lock())
                  if not s.passed]
        if failed:
            raise HTTPException(
                status_code=409,
                detail="; ".join(f"{s.step}: {s.detail}" for s in failed))

        def commit_promotion(paths, message: str) -> None:
            # the sidecar retires in the same atomic commit; only reached
            # after lockbuild succeeded, so a rollback never loses it
            if sidecar.is_file():
                sidecar.unlink()
            _git_commit(paths, message)

        result = certify_promote(
            catalog_root=settings.catalog_root,
            candidate_dir=cdir,
            summary_writer=stub_summary_writer,
            run_lockbuild=_run_lockbuild,
            commit=commit_promotion,
        )
        if not result.passed:
            logger.warning("certify promote failed candidate_id=%s detail=%s",
                           cid, result.detail)
            raise HTTPException(status_code=409, detail=result.detail)
        logger.info("certify promote succeeded candidate_id=%s", cid)
        return HTMLResponse(f"<p class='banner'>{result.detail}</p>")

    @app.post("/certify/{cid}/reject", response_class=HTMLResponse)
    def certify_reject_action(cid: str, request: Request,
                              _=Depends(require_operator),
                              reason: str = Form("")):
        cdir = _candidate_or_404(cid)
        shutil.rmtree(cdir)
        sidecar = _proposed_root / f"{cid}.inputs.yaml"
        if sidecar.is_file():
            sidecar.unlink()
        logger.info("certify reject candidate_id=%s reason=%s", cid, reason)
        return HTMLResponse(
            f"<p class='banner'>rejected {cid}: {reason or 'no reason given'}</p>")

    # -- canary review (meta screen — operator role, UI-PLANE §5) --------------

    def _canary_key(agent_id: str, entry: dict | None, runs: list) -> tuple[str, str]:
        # Prefer the live lock entry's version; fall back to the most recent
        # run's recorded version/profile so the screen still renders a key
        # once the entry has moved on (or isn't resolvable) but evidence exists.
        if entry is not None:
            return entry.get("version", ""), settings.model_profile
        if runs:
            # `runs` is ascending by run_id (supervised_runs' ordering) — the
            # most recent run is the last element, not the first.
            return runs[-1].entry_version, runs[-1].model_profile
        return "", settings.model_profile

    def _run_in_scope_or_404(agent_id: str, run_id: int):
        """The ledger has no per-agent partitioning at the SQL level, so a
        form-supplied `run_id` could name a row for any agent — guard the
        adjudicate/void routes against closing (or leaking the existence of)
        a run that doesn't belong to the `{agent_id}` named in the URL path."""
        run = next((r for r in store.supervised_runs(agent_id) if r.run_id == run_id),
                   None)
        if run is None:
            raise HTTPException(status_code=404, detail="no such run for this agent")
        return run

    @app.get("/canary/{agent_id}", response_class=HTMLResponse)
    def canary_review(agent_id: str, request: Request, demoted: bool = False,
                      _=Depends(require_operator)):
        runs = store.supervised_runs(agent_id)
        entry = entry_by_id(_current_lock(), agent_id)
        streak = store.supervised_streak(agent_id, entry["version"],
                                         settings.model_profile) if entry else 0
        key_version, key_model_profile = _canary_key(agent_id, entry, runs)
        return page("canary.html.j2", agent_id=agent_id, runs=runs, streak=streak,
                    threshold=settings.supervision_threshold,
                    eligible=streak >= settings.supervision_threshold,
                    key_version=key_version, key_model_profile=key_model_profile,
                    demoted=demoted)

    @app.post("/canary/{agent_id}/adjudicate")
    def canary_adjudicate(agent_id: str, request: Request, run_id: int = Form(...),
                          verdict: str = Form(...), _=Depends(require_operator)):
        if verdict not in {"clean", "incident"}:
            raise HTTPException(status_code=400, detail="verdict must be clean|incident")
        run = _run_in_scope_or_404(agent_id, run_id)
        if run.verdict != "pending":
            raise HTTPException(status_code=409, detail=f"run {run_id} is already {run.verdict}")
        store.close_supervised_run(run_id, verdict=verdict,
                                   adjudicated_by=user_sub(request))
        return RedirectResponse(f"/canary/{agent_id}", status_code=303)

    @app.post("/canary/{agent_id}/void")
    def canary_void(agent_id: str, request: Request, run_id: int = Form(...),
                    reason: str = Form(...), _=Depends(require_operator)):
        # an incident may also be voided (ledger-mode#3): infra-attributable
        # failures (tool-plane outage, timeout) auto-close as an incident
        # before an operator ever sees them, and design §4's void escape
        # hatch exists specifically to let an operator retract one of those.
        run = _run_in_scope_or_404(agent_id, run_id)
        if run.verdict not in {"pending", "incident"}:
            raise HTTPException(status_code=409, detail=f"run {run_id} is already {run.verdict}")
        store.close_supervised_run(run_id, verdict="void", reason=reason,
                                   adjudicated_by=user_sub(request))
        return RedirectResponse(f"/canary/{agent_id}", status_code=303)

    def _record_trust_override(agent_id: str, version: str, model_profile: str,
                               tier: str, granted_by: str, evidence: str) -> None:
        """CATALOG §7: 'written only by certify (or recorded human override)'
        — the demote action is that override. Append-only, same shape as a
        certify-written record, atomic to its own commit (no lockbuild: recall
        takes effect at routing time against the live trust.yaml, independent
        of the lock — CATALOG §7)."""
        trust_file = settings.catalog_root / "trust.yaml"
        before = trust_file.read_text(encoding="utf-8") if trust_file.is_file() else None
        trust = (_yaml.safe_load(before) if before else None) or {"records": []}
        trust.setdefault("records", []).append({
            "id": agent_id, "version": version, "model_profile": model_profile,
            "tier": tier, "granted_by": granted_by, "evidence": evidence,
        })
        trust_file.write_text(_yaml.safe_dump(trust, sort_keys=True), encoding="utf-8")
        try:
            _git_commit([trust_file], f"recall: {agent_id}@{version} -> {tier} ({granted_by})")
        except Exception:
            if before is not None:
                trust_file.write_text(before, encoding="utf-8")
            else:
                trust_file.unlink()
            raise

    @app.post("/canary/{agent_id}/demote")
    def canary_demote(agent_id: str, request: Request, _=Depends(require_operator)):
        # A real, operator-attributed routing-time recall (invoke-ui#3): the
        # mechanism CATALOG §7 sanctions ("recorded human override") but that
        # this route previously never used — it only wrote to stale_queue, a
        # table nothing reads.
        entry = entry_by_id(_current_lock(), agent_id)
        runs = store.supervised_runs(agent_id)
        version, model_profile = _canary_key(agent_id, entry, runs)
        if not version:
            raise HTTPException(
                status_code=409,
                detail=f"no resolvable version for {agent_id} — nothing to demote")
        _record_trust_override(
            agent_id, version, model_profile, "untrusted",
            granted_by=f"operator-recall:{user_sub(request)}",
            evidence="demoted via canary review screen")
        return RedirectResponse(f"/canary/{agent_id}?demoted=1", status_code=303)

    return app
