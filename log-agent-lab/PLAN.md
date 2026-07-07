# log-agent-lab — phase plan

A staged experiment in compositional / self-owned agents over a mock
Elasticsearch-style log corpus exposed through MCP. Each phase is a self-contained
milestone with its own tests; later phases depend on the MCP server from Phase 0.

## Architecture

```
mcp_server (:8000)  --MCP/SSE-->  agent_app (:8002)   [B1: compose agent from model + tool pool]
     ^                            b2a_app  (:8003)     [B2a: generate→sandbox→evaluate loop]
     |                                 |
     +-------- generated agent subprocess (sandbox) ---+
```

| Module     | Role                                                      | Entry            | Port |
|------------|-----------------------------------------------------------|------------------|------|
| mcp_server | 3 log tools (`es_search`, `es_aggregate`, `field_stats`)  | `server:app`     | 8000 |
| agent_app  | Assemble an agent from a model + validated MCP tool pool   | `api:app`        | 8002 |
| b2a_app    | Self-owned code-gen loop; writes + evaluates LangGraph agents | `api:app`     | 8003 |

## Phases

### Phase 0 — Workspace scaffold ✅
- uv workspace (`mcp_server`, `agent_app`, `b2a_app`), `uv.lock`, `.env.example`.
- Commit `7c431f3`.

### Phase A — MCP log server ✅
- FastMCP SSE server, mock log corpus + generator.
- Tools: `es_search`, `es_aggregate`, `field_stats`.
- Tests: `tests/mcp_server/` (generator, mock API, tools).

### Phase B1 — Compositional agent factory ✅
- `assemble_agent(model, required_tool_names, available, task_description)` validates
  requested tools against the live MCP tool pool and registers the spec.
- FastAPI: `/chat`, `/agents`, `/tools`, `/health`; `MCPClient` over `/mcp`.
- Shares the skills catalog (see B2a) via `skills_kit`; discovery is
  skills-first with live MCP fallback (`AGENT_SKILLS_DIR`).
- Tests: `tests/agent_app/` (factory, registry, mcp_client, api).
- Commit `5887dec`.

### Phase B2a — Self-owned code-gen loop ✅
- Flat bounded loop: **generate → sandbox → evaluate → decide**.
- **Tool discovery** is registry-independent: a local skills directory
  (`skills/<tool>/SKILL.md`, YAML frontmatter → `ToolSchema`, `loop/skills.py`)
  is used first, so the loop works with no live MCP registry; live MCP
  `list_tools()` is the fallback (`B2A_SKILLS_DIR`). An aggregated
  `skills/manifest.json` (regen: `python -m loop.skills`) is the fast path for
  list calls and is served by `GET /tools`; folders are the authoring source.
- Generator is **provider-agnostic** (`loop/llm.py`): a `Completer` seam
  (`prompt → text`) with `stub` (template, no key), `anthropic`, and
  `openai`-compatible (OpenAI / Ollama / vLLM / local via base URL) backends,
  selected by `B2A_LLM_PROVIDER` / `B2A_MODEL`. Any callable can be injected.
- Generated agents are **resilient**: each tool call is wrapped so a failure
  records an `{"error": ...}` result rather than crashing — a bad run becomes a
  retryable eval failure (feedback → regenerate) instead of a hard `crash` stop.
- Sandbox: subprocess with hard timeout, isolated env; never runs in-process.
- Evaluator: judges agent stdout JSON against the fixed eval suite.
- 4 eval cases (`search_error_level`, `aggregate_by_service`, `field_stats_numeric`,
  `field_stats_categorical`); stop reasons: `passed`, `timeout`, `crash`, `bad_json`,
  `syntax_error`, `exception`, `ceiling`.
- FastAPI: `/run-loop`, `/health`.
- Tests: `tests/b2a_app/` (spec, generator, sandbox, evaluator, orchestrator, api, cases).
- Commits `aa74dc9`, `899baee`.

### Phase B2b — Reasoning agents + held-out evaluation 🚧
- Closes B2a's memorization gap: B2a hands the generator the exact tool+args to
  replay; B2b gives it only a **natural-language task** + the tool catalog and
  makes it **infer** the call. Reasoning is build-time (generator LLM); the
  generated agent stays static and model-free at runtime.
- `ReasoningTask` carries the canonical tool+args as **hidden ground truth**
  (`eval_suite/reasoning_tasks.py`): `TRAIN_DEMOS` are few-shot examples,
  `HELDOUT_TASKS` are graded and disjoint (no answer leakage).
- `loop/reasoning.py`: prompt builder (never reveals the task's own call),
  reference/oracle agent renderer, live-ground-truth grader (runs the canonical
  call and compares the agent's `{"answer": ...}`), and `run_reasoning_task`.
- **Feedback→retry** (`run_reasoning_loop`): failed attempts feed error detail
  (never the canonical call) back into the next generation; `run_reasoning_suite`
  reports a held-out pass rate. Served by `POST /run-reasoning` (requires a
  configured LLM provider; 503 otherwise).
- 9 held-out tasks — 6 answerable + 3 **gap tasks** (delete, time-range filter,
  live tail) that no available tool can satisfy. The correct behaviour on a gap
  is a grounded refusal: print `{"missing": "<needed capability>"}` instead of
  fabricating a call. Grading is **symmetric** (fabricating on a gap and
  claiming a gap on an answerable task fail with the same generic detail, so
  feedback never reveals the task kind). The suite report splits answerable
  accuracy from gap detection and aggregates `missing_capabilities` — the
  actionable "what to add to the tool catalog" highlight, surfaced by
  `/run-reasoning`.
- A **drift guard** e2e asserts the skills catalog matches the live MCP server
  (tool names + parameter names).
- Tests: unit **leak guard** (canonical call absent from prompt and feedback) +
  train/held-out disjointness; e2e oracle passes every task, grader rejects a
  wrong plan, feedback loop recovers from a wrong first plan in 2 attempts,
  endpoint drives the suite end to end.
- ⏭️ Only remaining: the real-model pass-rate number — run
  `uv run pytest -m e2e tests/e2e/test_reasoning.py -k real_provider -s`
  with `B2A_LLM_PROVIDER` + credentials set (no creds in this environment).

### Phase 1 (current, `ft/phase1`) — Cross-service verification 🚧
- ✅ E2E integration test driving mcp_server + agent_app + b2a_app over HTTP
  (`tests/e2e/`, opt-in `e2e` marker). Boots the three services as subprocesses and
  asserts `/chat` and a full stub `/run-loop` converge to `passed` against the live MCP.
- ⏭️ Docker packaging for `b2a_app` (Dockerfile + docker-compose entry) — **deferred**.

## Running

```bash
# unit suite (fast, mocked; no MCP server needed)
uv run pytest -q -m "not e2e"

# e2e suite (boots all 3 services; slower)
uv run pytest -q -m e2e
```
