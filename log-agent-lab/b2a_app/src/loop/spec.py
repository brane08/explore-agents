from __future__ import annotations

import os
from pydantic import BaseModel, ConfigDict, Field


class ToolSchema(BaseModel):
    """MCP tool descriptor as fetched from the server."""
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    input_schema: dict


class EvalCaseSpec(BaseModel):
    """Serialisable description of one eval case (no callable — just id + invocation)."""
    model_config = ConfigDict(frozen=True)

    id: str
    tool_name: str
    arguments: dict


class TaskSpec(BaseModel):
    """
    Describes what the generated agent must accomplish.

    Passed unchanged into every generation call so the prompt is
    deterministic for a given spec + feedback pair.
    """
    model_config = ConfigDict(frozen=True)

    task_description: str
    mcp_server_url: str
    available_tools: list[ToolSchema]
    eval_case_ids: list[str]
    eval_cases: list[EvalCaseSpec] = Field(default_factory=list)
    max_iterations: int = Field(
        default_factory=lambda: int(os.environ.get("B2A_MAX_ITERATIONS", "5")),
    )
    agent_name: str = "orch-b2a-agent"
