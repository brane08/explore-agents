"""Entry schemas per docs/CATALOG.md §4. Unknown fields are rejected
(extra="forbid") — which also enforces "no trust tier declared in the entry".
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$")

TIER_ORDER = {"untrusted": 0, "quarantined": 1, "validated": 2}
BINDABLE_KINDS = {"skill", "mcp-tool"}

# catalog dir → kinds allowed inside it
DIR_KINDS = {
    "skills": {"skill"},
    "mcp": {"mcp-tool"},
    "a2a": {"a2a-agent"},
    "models": {"model"},
    "templates": {"template"},
    "agents": {"agent", "component", "composite"},
}

# kind → files/globs in the hash scope (CATALOG §4 table)
HASH_SCOPES = {
    "skill": ("SKILL.md", "impl/**"),
    "mcp-tool": ("schema.snapshot.json",),
    "a2a-agent": ("agentcard.snapshot.json",),
    "model": ("entry.yaml",),
    "agent": ("AGENT_MANIFEST.yaml", "src/**", "prompts/**", "tests/**", "eval/**"),
    "component": ("AGENT_MANIFEST.yaml", "src/**", "prompts/**", "tests/**", "eval/**"),
    "template": ("entry.yaml", "impl/**", "prompts/**", "ui/**", "evalmatrix/**"),
    "composite": ("entry.yaml",),
}

# kind → files that MUST exist for the entry to be complete
REQUIRED_FILES = {
    "skill": ("SKILL.md",),
    "mcp-tool": ("schema.snapshot.json", "routing_summary.md"),
    "a2a-agent": ("agentcard.snapshot.json", "routing_summary.md"),
    "model": (),
    "agent": ("AGENT_MANIFEST.yaml",),
    "component": ("AGENT_MANIFEST.yaml",),
    "template": (),
    "composite": (),
}

EXTERNAL_KINDS = {"mcp-tool", "a2a-agent"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ProposedTag(_Strict):
    name: str
    proposed: Literal[True]


class Ref(_Strict):
    id: str
    version: str


class InstantiatedFrom(_Strict):
    id: str
    version: str
    hash: str


class SlotSpec(_Strict):
    name: str
    type: Literal["binding", "value", "enum"]
    required: bool = True
    accepts: dict | list | None = None
    slot_schema: dict | None = Field(default=None, alias="schema")


class BaseEntry(_Strict):
    id: str
    version: str
    routing_summary: str
    capability_tags: list[str | ProposedTag] = Field(default_factory=list)
    detail: str = ""

    @field_validator("version")
    @classmethod
    def _semver(cls, v: str) -> str:
        if not SEMVER_RE.match(v):
            raise ValueError(f"'{v}' is not valid semver")
        return v

    def tag_names(self) -> list[str]:
        return [t if isinstance(t, str) else t.name for t in self.capability_tags]

    def unproposed_tags(self) -> list[str]:
        return [t for t in self.capability_tags if isinstance(t, str)]


class SkillEntry(BaseEntry):
    kind: Literal["skill"]


class _ExternalEntry(BaseEntry):
    # routing_summary lives in routing_summary.md (regenerated, never vendor prose)
    routing_summary: str | None = None
    routing_summary_provenance: Literal["regenerated"]


class McpToolEntry(_ExternalEntry):
    kind: Literal["mcp-tool"]
    registry_url: str


class A2AAgentEntry(_ExternalEntry):
    kind: Literal["a2a-agent"]
    endpoint_url: str


class ModelEntry(BaseEntry):
    kind: Literal["model"]
    provider: str
    endpoint_class: Literal["anthropic", "openai-compat", "bedrock", "local"]
    capabilities: dict = Field(default_factory=dict)
    cost_class: str
    profile_class: str
    status: Literal["active", "deprecated"]


class AgentEntry(BaseEntry):
    kind: Literal["agent"]
    bindings: list[Ref] = Field(default_factory=list)
    model_requirements: dict = Field(default_factory=dict)
    delegation_requirements: list[str] = Field(default_factory=list)
    instantiated_from: InstantiatedFrom | None = None


class ComponentEntry(BaseEntry):
    kind: Literal["component"]
    bindings: list[Ref] = Field(default_factory=list)
    model_requirements: dict = Field(default_factory=dict)
    delegation_requirements: list[str] = Field(default_factory=list)
    conforms_to: dict = Field(default_factory=dict)  # {template_id, slot}
    instantiated_from: InstantiatedFrom | None = None


class TemplateEntry(BaseEntry):
    kind: Literal["template"]
    slots: list[SlotSpec] = Field(default_factory=list)
    model_requirements: dict = Field(default_factory=dict)


class CompositeEntry(BaseEntry):
    kind: Literal["composite"]
    members: list[Ref] = Field(default_factory=list)


KIND_MODELS: dict[str, type[BaseEntry]] = {
    "skill": SkillEntry,
    "mcp-tool": McpToolEntry,
    "a2a-agent": A2AAgentEntry,
    "model": ModelEntry,
    "agent": AgentEntry,
    "component": ComponentEntry,
    "template": TemplateEntry,
    "composite": CompositeEntry,
}


class ManifestBinding(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    version: str
    hash: str


class AgentManifest(BaseModel):
    """AGENT_MANIFEST.yaml — playbook-owned; only the fields lockbuild
    consumes are typed, the rest pass through."""
    model_config = ConfigDict(extra="allow", protected_namespaces=())
    model_profile: str | None = None
    bindings: list[ManifestBinding] = Field(default_factory=list)
