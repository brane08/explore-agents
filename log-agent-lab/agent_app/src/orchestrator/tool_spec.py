from pydantic import BaseModel, ConfigDict


class ToolSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    input_schema: dict


class AgentSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    model: str
    tools: tuple[ToolSpec, ...]
    system_prompt: str
