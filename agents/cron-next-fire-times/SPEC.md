# SPEC — cron-next-fire-times (B2b, full-agent, langgraph-py311)

## 1. Capability restatement (§5.1)

Given a cron expression and a start timestamp, the agent returns the next five fire
times of that schedule, expressed in UTC. The start timestamp is normalised to UTC
(a naive timestamp is read as UTC, an offset-bearing one is converted); fire times
are strictly after it, ascending, at minute granularity. If the expression or the
timestamp cannot be parsed, an input is missing, or the schedule yields fewer than
five occurrences inside the bounded search horizon, the agent returns a typed
failure and no fire times at all. No contradiction with either behavioral criterion
was found, so no `WARN: SPEC_TENSION` is raised.

## 2. Bindings (§5.2, §3.1)

**None.** `CAPABILITY_MANIFEST` at `4a7408de3a6a` contains `es_search`,
`es_aggregate` (skills), `field_stats` (mcp-tool) — all mock-log-corpus tools with
`log-analysis` / `log-source` tags — plus `b1-8ef03559ba22` (`kind: agent`, never
bindable) and `stub-class-ref` (`kind: model`). None of them computes cron fire
times, and binding an irrelevant tool would widen the surface for nothing (§5.2
"minimize surface"). Cron evaluation is closed-form arithmetic over the two request
parameters, so the agent needs no external capability and consumes no trust tier.
No `kind: agent | component | a2a-agent` is bound or invoked, and no delegation
requirement arises.

## 3. Design (§5.3)

### Entrypoint

`src/agent.py:run(cron_expression, start_timestamp, config=None) -> dict`. It never
raises for bad input; every outcome is a result envelope.

### State / config shape

`AgentState` (`TypedDict`, §4.5 LangGraph conventions):

| key | meaning |
|---|---|
| `cron_expression`, `start_timestamp` | raw request parameters |
| `config` | `AgentConfig` (frozen dataclass — the slot-value surface) |
| `schedule`, `start_utc` | parsed intermediates |
| `fire_times` | formatted UTC strings (success path only) |
| `error` | `{code, message}` (failure path only) |
| `result` | terminal envelope |
| `iterations` | node-execution counter for the budget guard |

### Flow

```
validate_input --(error)--> format_result --> END
               \--(ok)----> compute_fire_times --> format_result --> END
```

Assembled in `src/graph.py` with `StateGraph` when LangGraph is importable, and
with an equivalent bounded sequential runner (`agent.run_state`) otherwise, so the
candidate is testable without the runtime installed. No import-time side effects;
the graph is built inside `build_graph()`.

The schedule search itself is the only loop: day-level stepping bounded by
`MAX_SEARCH_DAYS` (§4.3), with the exhausted-horizon case as an explicit terminal
failure rather than a silent short result.

### Output contract

Success: `{"status": "ok", "cron_expression", "start_timestamp", "timezone": "UTC",
"fire_times": [5 × "YYYY-MM-DDTHH:MM:SSZ"]}`.
Failure: `{"status": "error", "error": {"code", "message"}}` — the `fire_times` key
is absent, so a partial result cannot be mistaken for a complete one (BC-2).

### Failure modes

| code | trigger |
|---|---|
| `MISSING_INPUT` | `cron_expression` or `start_timestamp` absent/None |
| `INVALID_CRON` | wrong field count, out-of-range value, inverted range, non-positive step, unknown macro, unsupported Quartz token (`L`, `W`, `#`) |
| `INVALID_TIMESTAMP` | non-ISO-8601 string, wrong type, impossible date |
| `NO_FIRE_TIME` | fewer than `fire_time_count` occurrences within the horizon (e.g. `0 0 30 2 *`) |
| `INVALID_CONFIG` | slot values outside their supported domain |
| `BUDGET_EXHAUSTED` | node-iteration guard tripped |

### Cron dialect

Standard 5 fields `minute hour day-of-month month day-of-week`; `*`, `?`, lists,
ranges, `*/step` and `a-b/step`, names `JAN`–`DEC` / `SUN`–`SAT`, day-of-week `0`
and `7` both Sunday, macros `@yearly @annually @monthly @weekly @daily @midnight
@hourly`. When **both** day-of-month and day-of-week are restricted, a day matches
if **either** matches (Vixie cron semantics); otherwise the restricted field alone
decides. Quartz extensions are rejected explicitly rather than silently ignored.

### Skeleton vs. slot values (§4.1)

- **Skeleton (fixed):** node set and graph shape, error envelope, the
  validate → compute → format ordering, the iteration budget guard, the
  day-stepping search algorithm, the parse/format helpers.
- **Slot values (typed config, `src/config.py` + manifest `slot_value_surface`):**
  `fire_time_count` (the "five"), `output_timezone`, `timestamp_format`,
  `prompt_path`, `max_node_iterations`, `bindings`, and the search horizon
  `MAX_SEARCH_DAYS`. Nothing in `agent.py`/`graph.py` hardcodes them.

### Prompts (§3.5, §4.4)

`prompts/default.md` (parameter extraction, refuse-don't-guess) and the per-profile
variant `prompts/stub-class-ref.md`. Prompts never compute or format fire times;
the model layer is optional and referenced by path via `AgentConfig.prompt_path`.
No model identifier appears anywhere in code or config.

## 4. Interface-criteria checkpoint (§5.4)

This scope is `full-agent`, so the checkpoint applies. No `INTERFACE_CRITERIA` text
was supplied to this build, so no `test_ic_<n>_<slug>` functions exist; the harness
computes `interface_criteria_ref` itself. The design above is what the checkpoint
would receive. See REPORT.md "Known limitations".

## 5. Nearest templates

`NEAREST_TEMPLATES` is empty — no template conventions to rhyme with (§4.2) and no
template evalmatrix rows to replay (§7).
