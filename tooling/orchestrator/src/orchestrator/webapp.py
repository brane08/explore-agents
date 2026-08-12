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
from orchestrator.trustcheck import load_records, transitive_tier

logger = logging.getLogger(__name__)

_TEMPLATES = Path(__file__).parent / "templates"
_STATIC = Path(__file__).parent / "static"

SESSION_COOKIE = "orch_session"


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
            ok, result_ref = run_invocation(
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

    @app.get("/invocations/{invocation_id}/stream")
    async def stream(invocation_id: str, request: Request):
        if store.get_invocation(invocation_id) is None:
            raise HTTPException(status_code=404)
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
    def invocation_detail(invocation_id: str):
        invocation = store.get_invocation(invocation_id)
        if invocation is None:
            raise HTTPException(status_code=404)
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
        if tier not in allowed_tiers(session.user_sub, settings,
                                     operator=is_operator(request)):
            store.queue_stale(entry["id"], f"tier:{tier}")
            err = StructuredError("STALE_ENTRY", f"{agent_id} refused at live tier {tier}")
            response = HTMLResponse(render_error(err), status_code=403)
            if created:
                attach_cookie(response, session)
            return response
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

    def _stub_summary_writer(spec_text: str, manifest) -> str:
        # Deterministic regeneration from SPEC (draft is advisory, never copied).
        # The certify-model writer rides this seam when creds exist.
        lines = [ln.strip() for ln in spec_text.splitlines()
                 if ln.strip() and not ln.startswith("#")]
        return lines[0] if lines else "Certified capability."

    def _run_lockbuild(root: Path) -> None:
        from lockbuild.build import LOCK_FILENAME, build_lock, render_lock
        (root / LOCK_FILENAME).write_text(render_lock(build_lock(root)),
                                          encoding="utf-8")

    def _git_commit(paths, message: str) -> None:
        subprocess.run(["git", "-C", str(settings.catalog_root), "add", "-A"],
                       check=True, capture_output=True)
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
            summary_writer=_stub_summary_writer,
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

    return app
