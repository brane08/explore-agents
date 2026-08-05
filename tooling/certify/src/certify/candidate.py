"""Loading a Role A candidate from `agents/_proposed/<candidate>/`."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from certify.playbook import MANIFEST_NAME


class ManifestBinding(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    version: str
    schema_hash: str


class CandidateManifest(BaseModel):
    """§6 AGENT_MANIFEST.yaml. Unknown fields pass through — the playbook owns
    this shape and may extend it; certify reads only what it checks."""
    model_config = ConfigDict(extra="allow", protected_namespaces=())

    agent_id: str
    kind: str
    mode: str
    scope: str
    playbook_version: str
    harness: str
    model_profile: str
    target_runtime: str
    catalog_ref: str
    status: str
    behavioral_criteria_ref: str | None = None
    interface_criteria_ref: str | None = None
    residual_ref: str | None = None
    target_slot: dict | None = None
    bindings: list[ManifestBinding] = Field(default_factory=list)
    delegation_requirements: list[str] = Field(default_factory=list)
    trust_tier_consumed: str | None = None
    slot_value_surface: list[dict] = Field(default_factory=list)


@dataclass
class Candidate:
    path: Path
    manifest: CandidateManifest | None
    report: str = ""
    load_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.manifest is not None and not self.load_errors

    def files(self) -> list[Path]:
        return sorted(p for p in self.path.rglob("*") if p.is_file())


def load_candidate(path: Path) -> Candidate:
    path = Path(path)
    errors: list[str] = []
    manifest = None
    manifest_file = path / MANIFEST_NAME

    if not manifest_file.is_file():
        errors.append(f"missing {MANIFEST_NAME}")
    else:
        try:
            manifest = CandidateManifest.model_validate(
                yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {})
        except Exception as exc:
            errors.append(f"{MANIFEST_NAME}: {exc}")

    report_file = path / "REPORT.md"
    report = report_file.read_text(encoding="utf-8") if report_file.is_file() else ""
    return Candidate(path=path, manifest=manifest, report=report, load_errors=errors)
