import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from mcp_client import MCPClient
from orchestrator.factory import assemble_agent
from orchestrator.registry import AgentRegistry
from orchestrator.skills import load_skill_tools
from orchestrator.tool_spec import ToolSpec

_registry = AgentRegistry()
_mcp_client: MCPClient | None = None
_available_tools: list[ToolSpec] = []


def _skills_dir() -> Path:
    default = Path(__file__).parents[2] / "skills"  # log-agent-lab/skills
    return Path(os.environ.get("AGENT_SKILLS_DIR", str(default)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mcp_client, _available_tools
    mcp_url = os.environ.get("MCP_SERVER_URL", "http://localhost:8000")
    _mcp_client = MCPClient(base_url=mcp_url)
    # Discover tools: shared skills catalog first, live MCP registry as fallback.
    _available_tools = load_skill_tools(_skills_dir())
    if not _available_tools:
        try:
            _available_tools = await _mcp_client.list_tools()
        except Exception:
            _available_tools = []
    yield


app = FastAPI(title="agent-app", lifespan=lifespan)


class ChatRequest(BaseModel):
    task_description: str
    message: str
    required_tools: list[str]


class ChatResponse(BaseModel):
    agent_name: str
    model: str
    tools_used: list[str]
    response: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/agents")
def list_agents() -> dict:
    return {"agents": _registry.list_names()}


@app.get("/tools")
def list_tools() -> dict:
    """List the discovered tool catalog (from the skills manifest or MCP)."""
    return {"tools": [t.model_dump() for t in _available_tools]}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    if _mcp_client is None:
        raise HTTPException(status_code=503, detail="MCP client not initialised")

    if not _available_tools:
        raise HTTPException(
            status_code=503,
            detail="No tools available — populate the skills directory or start mcp_server.",
        )

    try:
        spec = assemble_agent(
            model=os.environ.get("AGENT_MODEL", "claude-opus-4-8"),
            required_tool_names=req.required_tools,
            available=_available_tools,
            task_description=req.task_description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _registry.register(spec)

    # Call each tool once as a demonstration; a real agent loop would iterate.
    tool_outputs = []
    for tool in spec.tools:
        try:
            result = await _mcp_client.call_tool(tool.name, {})
            tool_outputs.append(f"[{tool.name}]: {result}")
        except Exception as exc:
            tool_outputs.append(f"[{tool.name}]: ERROR — {exc}")

    response_text = (
        f"Agent '{spec.name}' assembled.\n"
        f"Task: {req.task_description}\n"
        f"User: {req.message}\n\n"
        + "\n".join(tool_outputs)
    )

    return ChatResponse(
        agent_name=spec.name,
        model=spec.model,
        tools_used=[t.name for t in spec.tools],
        response=response_text,
    )
