"""
generator.py — Step 3 of the B2a loop.

Produces a candidate LangGraph agent as a Python source file, validates it
with ast.parse, and writes it to disk.

Two generator backends:
  stub_generate      — template-based, no API key needed, used by default
  anthropic_generate — real Claude call; swap in when ANTHROPIC_API_KEY is set

generate_candidate() is the public entry point used by orchestrator.py.
It calls whichever backend is passed, then validates + writes the file.
"""

from __future__ import annotations

import ast
import json
import os
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from loop.spec import TaskSpec, ToolSchema

# Type alias for any backend: (spec, feedback) -> source code string
GeneratorFunc = Callable[[TaskSpec, str], str]


@dataclass
class GenerationResult:
    iteration: int
    path: Path
    prompt_sent: str
    raw_response: str


# ---------------------------------------------------------------------------
# Stub backend — deterministic template, no API
# ---------------------------------------------------------------------------

def _render_case_node(case_id: str, tool_name: str, arguments: dict) -> str:
    """Render one async LangGraph node that executes one eval case."""
    args_repr = json.dumps(arguments)
    node_name = f"node_{case_id}"
    lines = [
        f"async def {node_name}(state: AgentState) -> AgentState:",
        f"    async with Client(MCP_URL) as client:",
        f"        raw = await client.call_tool({tool_name!r}, {args_repr})",
        f"    state['results'][{case_id!r}] = _parse_mcp_result(raw)",
        f"    return state",
    ]
    return "\n".join(lines)


def _render_agent(spec: TaskSpec, feedback: str) -> str:
    from loop.spec import EvalCaseSpec

    # Drive nodes from eval_cases if present, else fall back to available_tools
    if spec.eval_cases:
        cases = spec.eval_cases
        node_names = [f"node_{c.id}" for c in cases]
        tool_nodes = "\n\n".join(
            _render_case_node(c.id, c.tool_name, c.arguments) for c in cases
        )
    else:
        # fallback: one node per tool, keyed by tool name
        node_names = [f"node_{t.name}" for t in spec.available_tools]
        tool_nodes = "\n\n".join(
            _render_case_node(t.name, t.name, {}) for t in spec.available_tools
        )

    add_nodes = "\n".join(f'    graph.add_node("{n}", {n})' for n in node_names)

    # linear chain
    edges: list[str] = []
    for i, name in enumerate(node_names):
        nxt = node_names[i + 1] if i + 1 < len(node_names) else "__end__"
        edges.append(f'    graph.add_edge("{name}", "{nxt}")')
    add_edges = "\n".join(edges)
    entry = node_names[0] if node_names else "__end__"

    feedback_comment = f"# feedback: {feedback}" if feedback.strip() else "# iteration 0"

    parts = [
        f'"""',
        f"Generated LangGraph agent: {spec.agent_name}",
        f"Task: {spec.task_description}",
        feedback_comment,
        f'"""',
        "from __future__ import annotations",
        "",
        "import asyncio",
        "import json",
        "import os",
        "from typing import TypedDict",
        "",
        "from eval_suite.log_analysis_cases import _parse_mcp_result",
        "from fastmcp import Client",
        "from langgraph.graph import StateGraph, END",
        "",
        f'MCP_URL = os.environ.get("MCP_SERVER_URL", {spec.mcp_server_url!r}).rstrip("/") + "/mcp"',
        "",
        "",
        "class AgentState(TypedDict):",
        "    results: dict",
        "",
        "",
        tool_nodes,
        "",
        "def build_graph() -> object:",
        "    graph = StateGraph(AgentState)",
        add_nodes,
        add_edges,
        f'    graph.set_entry_point("{entry}")',
        "    return graph.compile()",
        "",
        "",
        "async def main() -> None:",
        "    compiled = build_graph()",
        '    state = await compiled.ainvoke({"results": {}})',
        "    print(json.dumps(state[\"results\"]))",
        "",
        "",
        'if __name__ == "__main__":',
        "    asyncio.run(main())",
    ]
    return "\n".join(parts)


def stub_generate(spec: TaskSpec, feedback: str) -> str:
    """Return candidate source code without calling any external API."""
    prompt = (
        f"[stub] task={spec.task_description!r} "
        f"tools={[t.name for t in spec.available_tools]} "
        f"feedback={feedback!r}"
    )
    return prompt, _render_agent(spec, feedback)


# ---------------------------------------------------------------------------
# Anthropic backend — real Claude call (needs ANTHROPIC_API_KEY)
# ---------------------------------------------------------------------------

def anthropic_generate(spec: TaskSpec, feedback: str) -> tuple[str, str]:
    """Call Claude to generate a LangGraph agent. Requires ANTHROPIC_API_KEY."""
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("anthropic package not installed") from exc

    tool_schemas = json.dumps(
        [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
         for t in spec.available_tools],
        indent=2,
    )
    feedback_section = f"\n\nPrevious attempt failed. Feedback:\n{feedback}" if feedback.strip() else ""

    prompt = textwrap.dedent(f"""\
        Generate a complete, runnable Python file implementing a LangGraph agent named
        "{spec.agent_name}" that accomplishes the following task:

        {spec.task_description}

        Requirements:
        - Use LangGraph (StateGraph with TypedDict state).
        - Call MCP tools via fastmcp.Client at the SSE endpoint:
          MCP_URL = os.environ.get("MCP_SERVER_URL", "{spec.mcp_server_url}").rstrip("/") + "/mcp"
        - Available tools (call them with these exact names and argument shapes):
        {tool_schemas}
        - Store each tool's result in state["results"][<tool_name>] as a parsed dict.
        - At the end of main(), print json.dumps(state["results"]) to stdout.
        - The file must be self-contained and runnable as: python agent.py
        - No placeholders, no TODO comments — complete working code only.
        {feedback_section}

        Return ONLY the Python source code, no markdown fences, no explanation.
    """)

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=os.environ.get("B2A_MODEL", "claude-sonnet-4-6"),
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text
    # strip accidental markdown fences
    if raw.strip().startswith("```"):
        lines = raw.strip().splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return prompt, raw


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _select_backend() -> GeneratorFunc:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic_generate
    return stub_generate


def generate_candidate(
    spec: TaskSpec,
    iteration: int,
    feedback: str = "",
    generator_fn: GeneratorFunc | None = None,
    generated_base_dir: Path | None = None,
) -> GenerationResult:
    """
    Call generator_fn (default: auto-selected backend), validate the result
    with ast.parse, write to disk, and return a GenerationResult.

    Raises SyntaxError if generated code is not valid Python.
    """
    if generator_fn is None:
        generator_fn = _select_backend()

    prompt_sent, source = generator_fn(spec, feedback)

    try:
        ast.parse(source)
    except SyntaxError as exc:
        raise SyntaxError(
            f"Generated code failed ast.parse at iteration {iteration}: {exc}"
        ) from exc

    base = generated_base_dir or (Path(__file__).parents[3] / "generated")
    out_dir = base / f"iteration_{iteration}" / spec.agent_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "agent.py"
    out_path.write_text(source, encoding="utf-8")

    return GenerationResult(
        iteration=iteration,
        path=out_path,
        prompt_sent=prompt_sent,
        raw_response=source,
    )
