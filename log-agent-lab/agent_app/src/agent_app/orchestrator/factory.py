from agent_app.orchestrator.tool_spec import AgentSpec, ToolSpec

_AGENT_NAME = "orch-b1-agent"


def match_tools(required_tool_names: list[str], available: list[ToolSpec]) -> list[ToolSpec]:
    if not required_tool_names:
        raise ValueError("required_tool_names must not be empty")
    pool = {t.name: t for t in available}
    missing = [n for n in required_tool_names if n not in pool]
    if missing:
        raise ValueError(
            f"Tool(s) {missing} not found in available tool pool. "
            f"Available: {sorted(pool)}"
        )
    return [pool[n] for n in required_tool_names]


def assemble_agent(
    model: str,
    required_tool_names: list[str],
    available: list[ToolSpec],
    task_description: str,
) -> AgentSpec:
    matched = match_tools(required_tool_names, available)
    tool_list = "\n".join(f"- {t.name}: {t.description}" for t in matched)
    system_prompt = (
        f"You are a log-analysis assistant assembled for the following task:\n"
        f"{task_description}\n\n"
        f"You have access to these tools:\n{tool_list}\n\n"
        f"Use only the tools listed above. Be concise and precise."
    )
    return AgentSpec(
        name=_AGENT_NAME,
        model=model,
        tools=tuple(matched),
        system_prompt=system_prompt,
    )
