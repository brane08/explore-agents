from agent_app.orchestrator.tool_spec import AgentSpec


class AgentRegistry:
    def __init__(self) -> None:
        self._store: dict[str, AgentSpec] = {}

    def register(self, spec: AgentSpec) -> None:
        self._store[spec.name] = spec

    def get(self, name: str) -> AgentSpec | None:
        return self._store.get(name)

    def list_names(self) -> list[str]:
        return list(self._store)
