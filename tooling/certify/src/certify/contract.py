"""Playbook §7 contract checklist, mechanically enforced (CHECKLISTS layer 6).

Layer 6 does not fork the checklist — `playbook.CONTRACT_ITEMS` is the single
source of truth and these checks evaluate exactly those items. A harness echoes
the checklist in REPORT.md; certify **spot-verifies the echo against the
artifacts** (layer 12) rather than trusting it: a false COMPLETE is the
certification-integrity failure the whole gate exists to catch.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from certify.candidate import Candidate
from certify.playbook import (
    ALLOWED_TOP_LEVEL,
    CONTRACT_ITEMS,
    REQUIRED_DIRS_B2A,
    REQUIRED_DIRS_B2B,
    REQUIRED_FILES,
    UI_SUFFIXES,
    BINDABLE_KINDS,
    HarnessInputs,
    criteria_hash,
)

# hardcoded model identifiers (playbook §3.5 / CHECKLISTS layer 13)
_MODEL_ID_RE = re.compile(
    r"\b(claude-[a-z0-9.\-]+|gpt-[0-9][a-z0-9.\-]*|gemini-[a-z0-9.\-]+|"
    r"llama-?[0-9][a-z0-9.\-]*|mistral-[a-z0-9.\-]+)\b",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{12,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
# a criterion test that never runs (playbook §7.6)
_SKIP_RE = re.compile(r"@pytest\.mark\.skip|pytest\.skip\(|@unittest\.skip")
_CRITERION_TEST_RE = re.compile(r"def (test_(?:bc|ic)_\d+_\w+)")

_TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".toml", ".txt", ".cfg", ".ini"}


@dataclass
class ContractResult:
    item: str
    description: str
    passed: bool
    detail: str = ""


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def _text_files(candidate: Candidate) -> list[Path]:
    return [p for p in candidate.files() if p.suffix.lower() in _TEXT_SUFFIXES]


def _src_files(candidate: Candidate) -> list[Path]:
    roots = [candidate.path / "src", candidate.path / "scaffold"]
    return [p for p in candidate.files()
            if any(r in p.parents for r in roots) and p.suffix == ".py"]


def check_criteria_refs(candidate: Candidate, inputs: HarnessInputs) -> ContractResult:
    item, desc = CONTRACT_ITEMS[0]
    m = candidate.manifest
    expected = criteria_hash(inputs.behavioral_criteria)
    if m.behavioral_criteria_ref != expected:
        return ContractResult(
            item, desc, False,
            "behavioral_criteria_ref does not match the frozen input "
            f"(expected {expected[:12]}…, got {str(m.behavioral_criteria_ref)[:12]}…) "
            "— criteria were modified or the ref was fabricated",
        )
    # interface criteria required iff full-agent scope (both modes)
    if inputs.scope == "full-agent" and not m.interface_criteria_ref:
        return ContractResult(item, desc, False,
                              "full-agent scope requires interface_criteria_ref")
    if inputs.scope == "delta" and m.interface_criteria_ref:
        return ContractResult(item, desc, False,
                              "delta scope must not carry interface_criteria_ref "
                              "(the slot contract is the interface criteria)")
    return ContractResult(item, desc, True)


def check_bindings(candidate: Candidate, inputs: HarnessInputs) -> ContractResult:
    item, desc = CONTRACT_ITEMS[1]
    manifest_entries = {e["id"]: e for e in inputs.capability_manifest.get("entries", [])}
    allowed = {e["id"]: e for e in inputs.bindable()}
    for b in candidate.manifest.bindings:
        entry = manifest_entries.get(b.id)
        if entry is None:
            return ContractResult(item, desc, False, f"invented tool '{b.id}' — not in CAPABILITY_MANIFEST")
        if entry.get("kind") not in BINDABLE_KINDS:
            return ContractResult(item, desc, False,
                                  f"binding '{b.id}' is kind '{entry.get('kind')}' — "
                                  "only skill|mcp-tool may be bound")
        if b.id not in allowed:
            return ContractResult(item, desc, False,
                                  f"binding '{b.id}' is above TRUST_TIER_CEILING "
                                  f"({entry.get('trust_tier')} vs {inputs.trust_tier_ceiling})")
        if b.version != entry["version"] or b.schema_hash != entry["hash"]:
            return ContractResult(item, desc, False,
                                  f"binding '{b.id}' (id, version, schema_hash) not verbatim "
                                  "from CAPABILITY_MANIFEST")
    return ContractResult(item, desc, True)


def check_no_agent_invocation(candidate: Candidate, inputs: HarnessInputs) -> ContractResult:
    item, desc = CONTRACT_ITEMS[2]
    manifest_entries = {e["id"]: e for e in inputs.capability_manifest.get("entries", [])}
    forbidden = {
        eid for eid, e in manifest_entries.items()
        if e.get("kind") in {"agent", "component", "a2a-agent", "composite"}
    }
    for p in _src_files(candidate):
        text = _read(p)
        for eid in forbidden:
            if eid in text:
                return ContractResult(
                    item, desc, False,
                    f"{p.relative_to(candidate.path)} references '{eid}' "
                    "(kind agent/component/a2a-agent) — delegation is router-mediated",
                )
    return ContractResult(item, desc, True)


def check_no_ui_no_model_ids_no_secrets(candidate: Candidate,
                                        inputs: HarnessInputs) -> ContractResult:
    item, desc = CONTRACT_ITEMS[3]
    for p in candidate.files():
        if p.suffix.lower() in UI_SUFFIXES:
            return ContractResult(item, desc, False,
                                  f"UI asset {p.relative_to(candidate.path)} — agents ship no UI")
    for p in _text_files(candidate):
        if p.name == "REPORT.md":
            continue  # the report may quote the profile it ran against
        text = _read(p)
        hit = _MODEL_ID_RE.search(text)
        if hit:
            return ContractResult(item, desc, False,
                                  f"hardcoded model id '{hit.group(0)}' in "
                                  f"{p.relative_to(candidate.path)}")
        secret = _SECRET_RE.search(text)
        if secret:
            return ContractResult(item, desc, False,
                                  f"secret-shaped literal in {p.relative_to(candidate.path)}")
    return ContractResult(item, desc, True)


def check_layout(candidate: Candidate, inputs: HarnessInputs) -> ContractResult:
    item, desc = CONTRACT_ITEMS[4]
    required_dirs = REQUIRED_DIRS_B2A if inputs.mode == "B2a" else REQUIRED_DIRS_B2B
    for name in REQUIRED_FILES:
        if not (candidate.path / name).is_file():
            return ContractResult(item, desc, False, f"missing {name}")
    for name in required_dirs:
        if not (candidate.path / name).is_dir():
            return ContractResult(item, desc, False, f"missing {name}/ (mode {inputs.mode})")
    for child in candidate.path.iterdir():
        if child.name not in ALLOWED_TOP_LEVEL:
            return ContractResult(item, desc, False,
                                  f"unexpected top-level entry '{child.name}' — §6 layout is exact")
    return ContractResult(item, desc, True)


def check_no_criterion_weakened(candidate: Candidate, inputs: HarnessInputs) -> ContractResult:
    item, desc = CONTRACT_ITEMS[5]
    tests_dir = candidate.path / "tests"
    if not tests_dir.is_dir():
        return ContractResult(item, desc, False, "no tests/ to verify")
    found: set[str] = set()
    for p in sorted(tests_dir.rglob("*.py")):
        text = _read(p)
        if _SKIP_RE.search(text):
            return ContractResult(item, desc, False,
                                  f"{p.relative_to(candidate.path)} skips a test — "
                                  "criterion tests must run")
        found.update(_CRITERION_TEST_RE.findall(text))
    if not found:
        return ContractResult(item, desc, False,
                              "no criterion tests (test_bc_<n>_<slug> / test_ic_<n>_<slug>) found")
    return ContractResult(item, desc, True)


CHECKS = (
    check_criteria_refs,
    check_bindings,
    check_no_agent_invocation,
    check_no_ui_no_model_ids_no_secrets,
    check_layout,
    check_no_criterion_weakened,
)


def check_contract(candidate: Candidate, inputs: HarnessInputs) -> list[ContractResult]:
    """Evaluate every playbook §7 item against the artifacts."""
    if not candidate.ok:
        return [ContractResult(item, desc, False, "; ".join(candidate.load_errors) or "unloadable")
                for item, desc in CONTRACT_ITEMS]
    return [check(candidate, inputs) for check in CHECKS]


def contract_passed(results: list[ContractResult]) -> bool:
    return all(r.passed for r in results)


def echoed_checklist(report: str) -> dict[str, bool]:
    """Parse the §7 checklist echo out of REPORT.md ('<item>: true|false')."""
    echoed: dict[str, bool] = {}
    for item, _desc in CONTRACT_ITEMS:
        m = re.search(rf"{re.escape(item)}\s*[:=]\s*(true|false)", report, re.IGNORECASE)
        if m:
            echoed[item] = m.group(1).lower() == "true"
    return echoed
