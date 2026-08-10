"""B1 assembly + registration (CATALOG §5, CHECKLISTS layer 4).

An assembly is config-only: validated tools + a generated prompt. Identity =
sha256 of the canonical config; identical configs deduplicate to the existing
registration. First successful use registers a lightweight `kind: agent`
entry (skipping `_proposed/` — zero novel code), with `assembly: b1` so
lockbuild derives tier = min(binding tiers).

Registration commits (config-flag): epoch-pinned routing reads locks via
`git show`, so a registration must reach a SHA to be routable by new sessions.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

import yaml

from lockbuild.build import LOCK_FILENAME, build_lock, render_lock
from lockbuild.hashing import entry_hash

from orchestrator.config import Settings
from orchestrator.errors import normalize_residual

PROMPT_VERSION = 1


@dataclass
class Assembly:
    agent_id: str
    config: dict
    prompt: str
    already_registered: bool


def assembly_config(task_text: str, tools: list[dict], settings: Settings) -> dict:
    return {
        "capability": normalize_residual(task_text),
        "tools": sorted(
            [{"id": t["id"], "version": t["version"]} for t in tools],
            key=lambda x: x["id"],
        ),
        "model_profile": settings.model_profile,
        "prompt_version": PROMPT_VERSION,
    }


def config_hash(config: dict) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def generate_prompt(task_text: str, tools: list[dict]) -> str:
    """Deterministic system prompt. Claims nothing beyond the bound tools
    (layer 4 [H]): capabilities are enumerated from the bindings verbatim."""
    lines = [
        "You are a task agent assembled from validated catalog tools.",
        "You can do exactly what the tools below allow — nothing else.",
        "If the task needs anything beyond them, say so instead of improvising.",
        "",
        "Tools:",
    ]
    for t in sorted(tools, key=lambda t: t["id"]):
        lines.append(f"- {t['id']} ({t['version']}): {t.get('routing_summary', '')}")
    lines += ["", f"Task class: {normalize_residual(task_text)}"]
    return "\n".join(lines) + "\n"


def _routing_summary(task_text: str, tools: list[dict]) -> str:
    tool_ids = ", ".join(sorted(t["id"] for t in tools))
    return (f"B1 assembly over [{tool_ids}] handling tasks of class: "
            f"{normalize_residual(task_text)}.")


def _union_tags(tools: list[dict], catalog_root: Path) -> list[str]:
    tags: set[str] = set()
    for t in tools:
        tags.update(t.get("capability_tags", []))
    # the lock flattens proposed tags to bare names; only vocabulary tags may
    # be copied unproposed or the registration's own lockbuild would fail
    known = set(
        (yaml.safe_load((catalog_root / "tags.yaml").read_text(encoding="utf-8"))
         or {}).get("tags") or []
    )
    return sorted(tags & known)


# registrations mutate the shared working tree + git index; serialize them
_REGISTER_LOCK = threading.Lock()


def assemble_and_register(
    task_text: str,
    tools: list[dict],
    settings: Settings,
) -> Assembly:
    config = assembly_config(task_text, tools, settings)
    digest = config_hash(config)
    agent_id = f"b1-{digest[:12]}"
    root = settings.catalog_root
    agent_dir = root / "agents" / agent_id
    prompt = generate_prompt(task_text, tools)

    with _REGISTER_LOCK:
        if agent_dir.is_dir():
            return Assembly(agent_id, config, prompt, already_registered=True)
        try:
            _write_registration(agent_dir, agent_id, digest, config, prompt,
                                task_text, tools, settings)
        except BaseException:
            # never leave a partial dir behind: it would permanently satisfy
            # the dedup check for an agent no lock contains
            shutil.rmtree(agent_dir, ignore_errors=True)
            raise
    return Assembly(agent_id, config, prompt, already_registered=False)


def _write_registration(agent_dir: Path, agent_id: str, digest: str, config: dict,
                        prompt: str, task_text: str, tools: list[dict],
                        settings: Settings) -> None:
    root = settings.catalog_root
    # entry.yaml — assembly: b1 → lockbuild derives tier = min(binding tiers)
    entry = {
        "id": agent_id,
        "kind": "agent",
        "version": "1.0.0",
        "assembly": "b1",
        "routing_summary": _routing_summary(task_text, tools),
        "capability_tags": _union_tags(tools, root),
        "detail": f"Config-only B1 assembly (config sha256 {digest}). "
                  f"Prompt: prompts/default.md. Tools: "
                  + ", ".join(sorted(t["id"] for t in tools)),
        "bindings": config["tools"],
        "model_requirements": {"tool_use": True},
    }
    manifest = {
        "model_profile": settings.model_profile,
        "config_hash": digest,
        "bindings": [
            {
                "id": t["id"],
                "version": t["version"],
                "schema_hash": entry_hash(_entry_dir(root, t), t["kind"]),
            }
            for t in sorted(tools, key=lambda t: t["id"])
        ],
    }

    agent_dir.mkdir(parents=True)
    (agent_dir / "entry.yaml").write_text(yaml.safe_dump(entry, sort_keys=True), encoding="utf-8")
    (agent_dir / "AGENT_MANIFEST.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8")
    (agent_dir / "prompts").mkdir()
    (agent_dir / "prompts" / "default.md").write_text(prompt, encoding="utf-8")
    (agent_dir / "src").mkdir()
    (agent_dir / "src" / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=True), encoding="utf-8")

    # registration is complete only with a lock that contains it
    (root / LOCK_FILENAME).write_text(render_lock(build_lock(root)), encoding="utf-8")

    if settings.register_commit:
        rel_dir = agent_dir.relative_to(root).as_posix()
        # pathspec'd add + commit: whatever else may be staged in the
        # operator's checkout must never be swept into a registration commit
        subprocess.run(
            ["git", "-C", str(root), "add", "--", rel_dir, LOCK_FILENAME],
            check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(root), "commit", "-q",
             "-m", f"b1: register {agent_id} (config {digest[:12]})",
             "--", rel_dir, LOCK_FILENAME],
            check=True, capture_output=True)


def _entry_dir(root: Path, lock_entry: dict) -> Path:
    kind_dirs = {"skill": "skills", "mcp-tool": "mcp"}
    return root / kind_dirs[lock_entry["kind"]] / lock_entry["id"]
