"""Typed configuration surface (slot values) for the cron-next-fire-times agent.

Everything a future template would vary lives here; the skeleton in `agent.py` /
`graph.py` never hardcodes these values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

DEFAULT_PROMPT_PATH = "prompts/default.md"


@dataclass(frozen=True)
class AgentConfig:
    fire_time_count: int = 5
    output_timezone: str = "UTC"
    timestamp_format: str = "iso8601-z"
    prompt_path: str = DEFAULT_PROMPT_PATH
    max_node_iterations: int = 8
    bindings: List[str] = field(default_factory=list)

    def validate(self) -> None:
        if self.fire_time_count < 1:
            raise ValueError("fire_time_count must be >= 1")
        if self.output_timezone != "UTC":
            raise ValueError("only UTC output is supported by this candidate")
        if self.max_node_iterations < 1:
            raise ValueError("max_node_iterations must be >= 1")
