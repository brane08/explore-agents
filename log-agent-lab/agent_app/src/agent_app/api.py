import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from agent_app.mcp_client import MCPClient
from agent_app.orchestrator.factory import assemble_agent
from agent_app.orchestrator.registry import AgentRegistry

_registry = AgentRegistry()
_mcp_client: MCPClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mcp_client
    mcp_url = os.environ.get("MCP_SERVER_URL", "http://localhost:8000")
    _mcp_client = MCPClient(base_url=mcp_url)
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


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    if _mcp_client is None:
        raise HTTPException(status_code=503, detail="MCP client not initialised")

    try:
        available = await _mcp_client.list_tools()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MCP server unreachable: {exc}") from exc

    try:
        spec = assemble_agent(
            model=os.environ.get("AGENT_MODEL", "claude-sonnet-4-6"),
            required_tool_names=req.required_tools,
            available=available,
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
