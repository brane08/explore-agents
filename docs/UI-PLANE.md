# UI-PLANE.md — Interaction Plane Specification (v1-beta)

> **Status: BETA (draft).** These specifications are under active iteration during the
> POC phases and are expected to change. Nothing here is a frozen contract yet: breaking
> revisions are permitted between phases, spec changes remain human-only (separate PRs),
> and consumers must pin the document version/hash they were built against.

Defines the UI layer for the agent platform: a **bespoke meta UI** for the orchestrator,
and **templated/generated UIs** for every agent the orchestrator creates. Companion to
`CATALOG.md` (v3) and the B2 playbook (v3).

Stack: FastAPI (co-hosted with orchestrator + LangGraph agents), Jinja2 fragments, htmx
(+ SSE extension), server-side sessions. No SPA, no client build step, no client state.

---

## 1. Two UI planes (core principle)

| Plane | Who builds it | Source of truth |
|---|---|---|
| **Meta UI** | Hand-built, versioned with `tooling/` | Orchestrator internals: routing, certify, trust, canary |
| **Agent UI** | Never hand-built per agent | Generated from catalog data: template `ui/` assets + slot values, or manifest-derived generic UI |

**Hard rule:** agents created by the meta-agent (B1, B1.5, B2) never ship bespoke UI.
Their interaction surface is *derived* — from the template's certified UI assets
(instances) or from the generic renderer over `AGENT_MANIFEST.yaml` (B1 registrations,
B2 full-agents pre-graduation). This keeps instances replicable, keeps UI inside the
certification boundary, and removes frontend work from the B2 generation surface
entirely. (Playbook §8 gains: *shipping UI assets is prohibited.*)

## 2. Agent UI generation

### 2.1 Template UI assets (`templates/<id>/ui/`)

Templates may ship three Jinja2 fragments — part of `fixed/`-equivalent certified
material, **inside the template hash scope**, reviewed at template certification:

```
templates/log-analysis/ui/
├── input.html.j2      # task-input form; may reference slots read-only
├── progress.html.j2   # per-SSE-event fragments (macro per event type, §4)
└── result.html.j2     # terminal-state rendering
```

Fragments are logic-light (display conditionals only), receive a fixed context
(`instance`, `slot_values`, `event` / `result`), and may not load external scripts —
htmx + platform CSS only. A template without `ui/` falls back to §2.3 generic rendering.

### 2.2 Instance UI = template UI + slot values

An instance's pages are the template fragments rendered with its `slot_values` bound.
No per-instance assets exist anywhere. UI changes = template version bump through
certification, propagating to all instances — same recall/staleness semantics as `impl/`
(CATALOG §7): demote the template, every instance UI is gone at routing time.

### 2.3 Generic manifest-derived UI (fallback + B2/B1 default)

For any agent without a template `ui/` (B1 registrations, promoted B2 full-agents,
templates that skipped `ui/`), the platform renders:

- **Input:** form generated from the entry's config surface / `slot_value_surface`
  (§3 rules) plus a free-text task field when the agent declares one.
- **Progress:** default renderers for the standard SSE event vocabulary (§4) — every
  agent streams the same event types, so the generic progress view is complete by
  construction.
- **Result:** typed rendering by result media type (markdown, json → collapsible tree,
  file refs → download links), with criteria-results table when present.

Consequence: a B2 candidate is fully operable through the generic UI on day one; earning
a *nicer* UI is part of template graduation, not agent authorship.

### 2.4 Slot-form generation (shared with B1.5 validation)

Slot schemas → dynamically built Pydantic models, one generic endpoint pair:

- `enum` slot → `Literal[…]` → `<select>` (the "defined loops" picker)
- `value` slot → field + template's deterministic validator attached as a Pydantic
  validator (expressiveness ceiling guarantees a plain callable)
- `binding` slot → `<select>` over lock entries filtered by `capability_tags` +
  `max_tier_required`, validated against the in-memory lock

`GET /templates/{id}/form` renders; `POST /templates/{id}/instantiate` validates and —
critically — **calls the same B1.5 instantiation path the router uses.** Form validation
and instantiation validation are one code path; `SLOT_INVALID` renders inline via the
error vocabulary (§5). Per-slot `hx-post` gives incremental server-side validation.

## 3. Meta UI (bespoke, v1 screens)

1. **Task console** — task submission; live routing visibility (cascade decision,
   post-match confirmation score, chosen entry + tier); invocation stream (§4);
   session/epoch banner (§6).
2. **Certify queue** — the human-promotion surface (CATALOG §8 honesty clause):
   `_proposed/` candidates with manifest summary, criteria-results tables, binding
   audit, judge findings vs. human-findings capture (feeds gate-graduation counters),
   promote/reject actions producing the atomic promotion commit.
3. **Canary review** — supervised-invocation evidence (CATALOG §7): shadow-diff viewer
   (quarantined vs. validated output, side-by-side), divergence stats per agent,
   promotion-threshold progress, instant-demote action (routing-time recall).
4. **Catalog browser** — lock-file view: entries by kind/tier/tags, stale flags with
   reasons, routing summaries, drill-down to `detail`. Read-only; catalog changes go
   through git.

Meta UI is versioned and deployed with `tooling/`; it is *not* a catalog entry and not
subject to agent certification — it is the certifier's instrument.

## 4. SSE event schema (derived from trace format)

One event vocabulary for every agent — emitted by the orchestrator wrapping LangGraph
`astream_events()`, rendered by template `progress.html.j2` macros or generic renderers,
and persisted 1:1 as the trace (the stream *is* the trace, tee'd):

```
event: routing        data: {cascade_step, entry_id?, confirmation_score?}
event: invocation     data: {invocation_id, agent_id, version, model_profile, catalog_ref}
event: node           data: {node, status: enter|exit, elapsed_ms}
event: tool_call      data: {binding_id, tool, status: start|ok|error, summary}
event: delegation     data: {capability, resolved_entry?, tier}        # router-mediated A2A
event: criteria       data: {criterion_id, kind: bc|ic, pass, detail}
event: warn           data: {code, context}                            # playbook WARN codes
event: terminal       data: {status: COMPLETE|PARTIAL|ERROR, result_ref}
```

Transport: `sse-starlette` `EventSourceResponse`; client `hx-ext="sse"
sse-connect="/invocations/{id}/stream"`, fragment swap per event. Events carry
monotonically increasing ids; reconnect resumes via `Last-Event-ID` replayed from the
persisted trace — **the run never depends on the connection** (invocation continues
server-side; the stream is a view).

**OpenTelemetry mapping (GA-standard export):** the same events emit as OTel spans —
`invocation` = root span; `node`, `tool_call`, `delegation` = child spans (parent-child
nesting preserved across router-mediated delegations); `criteria`, `warn`, `terminal` =
span events; `agent_id`/`version`/`model_profile`/`catalog_ref` = resource attributes.
SSE remains the UI transport; OTel is the audit/observability export, letting standard
enterprise tooling (and compliance pipelines) ingest agent traces without bespoke
formats. One emitter, two sinks — the stream, the trace store, and the OTel exporter
must never diverge (single event source, asserted by test).

## 5. Endpoint contract (FastAPI, fragments unless noted)

```
GET  /                                  task console (full page)
POST /tasks                             submit → routing events begin; returns console fragment
GET  /invocations/{id}/stream           SSE (§4)
GET  /invocations/{id}                  invocation detail (replays trace when closed)

GET  /agents                            catalog browser
GET  /agents/{id}                       agent page: generated input UI (§2) + history
POST /agents/{id}/invoke                validated invoke (tier + user authz checked here)

GET  /templates/{id}/form               generated slot form (§2.4)
POST /templates/{id}/instantiate        shared B1.5 path; inline SLOT_* errors

GET  /certify/queue                     certify queue (meta)
POST /certify/{candidate}/promote|reject
GET  /canary/{agent_id}                 shadow-diff review (meta)
POST /canary/{agent_id}/demote          routing-time recall

Errors: every CATALOG §11 code has a fragment renderer; PATTERN_UNRECOGNIZED renders
its residual as the "not covered" explanation + (authz-gated) "propose B2 task" action.
```

## 6. Session model

```
session(session_id PK, user_sub, catalog_ref, thread_id, created_at)
```

- **Epoch pin:** `catalog_ref` frozen at session creation — lock rebuilds and drift never
  shift behavior mid-conversation. Sole deliberate exception: trust **recall** pierces
  the pin at routing time (CATALOG §7). Banner in the console shows the pinned ref and a
  "start new session on current catalog" action when it lags `main`.
- **History:** delegated to the LangGraph Postgres checkpointer via `thread_id` — the
  session row stays thin; no duplicated conversation state.
- Cookie = signed `session_id` only (`itsdangerous`); all state server-side (Postgres;
  Redis optional for hot session lookup multi-pod).

## 7. Auth (pick one per deployment target — never both)

- **Enterprise:** existing Spring Cloud Gateway MVC BFF fronts this service; FastAPI
  trusts relayed token / gateway-injected identity like any other downstream. Reuses the
  solved OAuth2 + route-affinity setup.
- **Standalone:** authlib OIDC directly in FastAPI.

Either way, authorization joins user → allowed tiers/agents against `trust.yaml` at
routing time, in-process. Meta screens (§3.2–3.4) require an operator role.

## 8. Deployment notes (OpenShift)

- SSE + multi-pod: route-level session affinity (v1) — the domain-route affinity caveats
  apply verbatim; or Redis pub/sub fanout so any pod serves any stream (v2).
- Long-lived streams: uvicorn/route timeouts sized for agent runtimes; invocations
  survive dropped connections by design (§4 reconnect).
- Co-hosting is packaging, not architecture: the UI calls the orchestrator through the
  same internal interface that becomes the network API if the execution plane (CATALOG
  §12) later moves agents out-of-process or behind A2A — the split is a deployment
  change, not a rewrite.

## 9. Certification & hashing implications (summary)

- `templates/<id>/ui/**` joins the template hash scope; UI defects are recalled with the
  template; UI review is a template-certification step (fragment lint: no external
  scripts, fixed context only).
- Agents/components have **no** UI files; certify rejects candidates containing any
  (playbook §8 amendment).
- Generic renderers + SSE vocabulary are meta-UI code — versioned with `tooling/`,
  outside catalog certification.
