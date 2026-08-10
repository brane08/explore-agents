"""Certify pipeline steps (CATALOG §8, CHECKLISTS layer 7).

Model-independent steps only; model-backed steps (3 eval, 6 judge) enter behind
seams when built. Each step returns a StepResult — certify is a PR-generation
system (honesty clause): steps produce evidence for the human queue, they never
promote on their own.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from certify.candidate import Candidate, load_candidate
from certify.evalrun import (
    EVIDENCE_NAME,
    EvalRunner,
    load_eval_evidence,
    persist_eval_evidence,
    subprocess_runner,
)
from certify.contract import (
    check_bindings,
    check_criteria_refs,
    check_layout,
    check_no_agent_invocation,
    check_no_ui_no_model_ids_no_secrets,
    _read,
    _src_files,
)
from certify.conformance_list import is_listed
from certify.playbook import PLAYBOOK_VERSION, HarnessInputs


@dataclass
class StepResult:
    step: str
    passed: bool
    detail: str = ""
    evidence: dict = field(default_factory=dict)


def step_layout_manifest(candidate: Candidate, inputs: HarnessInputs,
                         catalog_root: Path) -> StepResult:
    """Step 1 — layout & manifest.

    §6 structure; `playbook_version` supported; `harness` on the conformance
    list (§10); `entry.draft.yaml` present; criteria refs match the frozen
    inputs. Reuses the layer-6 contract checks — layer 7 re-verifies them on
    the candidate as received, not as the harness reported it.
    """
    step = "layout_manifest"
    if not candidate.ok:
        return StepResult(step, False,
                          "; ".join(candidate.load_errors) or "unloadable candidate")

    layout = check_layout(candidate, inputs)
    if not layout.passed:
        return StepResult(step, False, layout.detail)

    m = candidate.manifest
    if m.playbook_version != PLAYBOOK_VERSION:
        return StepResult(step, False,
                          f"playbook_version {m.playbook_version!r} unsupported "
                          f"(certify implements {PLAYBOOK_VERSION!r})")

    if not is_listed(catalog_root, m.harness):
        return StepResult(step, False,
                          f"harness {m.harness!r} is not on the conformance list "
                          "(§10: unmeasured harnesses do not enter certify)")

    criteria = check_criteria_refs(candidate, inputs)
    if not criteria.passed:
        return StepResult(step, False, criteria.detail)

    return StepResult(step, True)


def step_rebase(candidate: Candidate, current_lock: dict) -> StepResult:
    """Step 2 — rebase check.

    Every bound entry's (version, hash) must still be current in the lock at
    the promotion tree; any drift since `catalog_ref` ⇒ NEEDS_REBASE (rebuild
    against current main + eval-rerun before proceeding).
    """
    step = "rebase"
    if not candidate.ok:
        return StepResult(step, False,
                          "; ".join(candidate.load_errors) or "unloadable candidate")

    current = {e["id"]: e for e in current_lock.get("entries", [])}
    for b in candidate.manifest.bindings:
        entry = current.get(b.id)
        if entry is None:
            return StepResult(step, False,
                              f"NEEDS_REBASE: bound entry '{b.id}' no longer in the "
                              "promotion-tree lock")
        if b.version != entry["version"] or b.schema_hash != entry["hash"]:
            return StepResult(step, False,
                              f"NEEDS_REBASE: bound entry '{b.id}' moved since "
                              f"catalog_ref ({b.version}/{b.schema_hash[:12]} vs "
                              f"{entry['version']}/{str(entry['hash'])[:12]})")
    return StepResult(step, True)


def step_eval(
    candidate: Candidate,
    *,
    runner: EvalRunner | None = None,
    security: StepResult | None = None,
    persist: bool = True,
) -> StepResult:
    """Step 3 — the candidate's own eval suite, on the manifest's profile.

    Green *and* evidence: a run whose per-criterion results were not captured is
    refused, because the value of `eval/` is that a profile migration reruns it
    and compares against a recorded baseline (layer 8: class migration =
    eval-rerun, never inherited evidence). The evidence is persisted into
    `trace/`, which promotion moves to content-addressed storage.

    Numbered 3, but it runs *last* among the mechanical steps: it executes
    candidate-authored code, so `security` carries the static scan's result and
    this refuses to run behind a failed one. The rule lives here rather than at
    each call site — a caller that got the ordering wrong would execute code
    certify had already flagged. See `evalrun` for the runner contract and for
    what this is not (a sandbox).
    """
    step = "eval"
    if security is not None and not security.passed:
        return StepResult(step, False,
                          "not run: the static security scan must pass first — "
                          f"step 3 executes candidate code ({security.detail})")
    if not candidate.ok:
        return StepResult(step, False,
                          "; ".join(candidate.load_errors) or "unloadable candidate")

    profile = candidate.manifest.model_profile
    if not profile:
        return StepResult(step, False,
                          "manifest declares no model_profile — eval evidence is "
                          "only valid for the profile it ran on")

    outcome = (runner or subprocess_runner)(candidate.path, profile)
    criteria = list((outcome.report or {}).get("criteria") or [])
    evidence = {
        "runner": "eval/run.py",
        "model_profile": profile,
        "exit_code": outcome.exit_code,
        "criteria": criteria,
        "passed": False,
        "detail": "",
    }

    def done(passed: bool, detail: str = "") -> StepResult:
        evidence["passed"] = passed
        evidence["detail"] = detail
        if persist:
            persist_eval_evidence(candidate.path, evidence)
        return StepResult(step, passed, detail, evidence)

    if outcome.exit_code is None:
        return done(False, outcome.detail or "the eval runner produced no exit code")
    if outcome.report is None:
        return done(False,
                    f"exited {outcome.exit_code} with no per-criterion report "
                    f"(playbook §5: JSON report via ${{EVAL_REPORT}} or stdout)"
                    + (f"; stderr: {outcome.detail}" if outcome.detail else ""))
    if not criteria:
        return done(False, "the eval report lists no criteria — no evidence to "
                           "rerun against after a profile migration")

    reported_profile = outcome.report.get("model_profile")
    if reported_profile and reported_profile != profile:
        return done(False,
                    f"eval ran on profile {reported_profile!r} but the manifest "
                    f"declares {profile!r} — evidence is not transferable")

    failed = [str(c.get("id")) for c in criteria if not c.get("passed")]
    if failed and outcome.exit_code == 0:
        return done(False,
                    f"runner reported exit 0 while its own report fails {failed} "
                    "— the exit code is a claim, the report is the evidence")
    if failed:
        return done(False, f"failing criteria: {failed}")
    if outcome.exit_code != 0:
        return done(False,
                    f"runner exited {outcome.exit_code} with every criterion "
                    "reported green"
                    + (f"; stderr: {outcome.detail}" if outcome.detail else ""))
    return done(True)


def select_evalmatrix_rows(
    candidate: Candidate,
    nearest_templates: list[dict],
    lock: dict,
) -> tuple[list[dict], list[dict]]:
    """Step 4 — mechanical applicability selection over NEAREST_TEMPLATES rows.

    A row is applicable iff its declared `signature` matches the candidate:
    every signature slot appears in `slot_value_surface` (name+type) and every
    signature capability tag is covered by the candidate's bound entries.
    Never a model judgment — prose fields on a row are ignored. Inapplicable
    rows are returned with `failed_check` (playbook §7 report requirement).

    Row signature shape is not pinned by the spec — invented minimally here
    ([H] review note): `signature: {slots: [{name, type}], capability_tags: []}`.
    Running the applicable rows executes the candidate and belongs to the
    step-3 eval seam, not here.
    """
    surface = {(s.get("name"), s.get("type"))
               for s in candidate.manifest.slot_value_surface}
    lock_entries = {e["id"]: e for e in lock.get("entries", [])}
    bound_tags: set[str] = set()
    for b in candidate.manifest.bindings:
        bound_tags.update(lock_entries.get(b.id, {}).get("capability_tags", []))

    applicable: list[dict] = []
    inapplicable: list[dict] = []
    for template in nearest_templates:
        for row in template.get("evalmatrix", []):
            sig = row.get("signature")
            if not sig:
                inapplicable.append({**row, "failed_check": "no signature declared"})
                continue
            missing_slot = next(
                ((s.get("name"), s.get("type")) for s in sig.get("slots", [])
                 if (s.get("name"), s.get("type")) not in surface), None)
            if missing_slot:
                inapplicable.append({
                    **row,
                    "failed_check": f"slot {missing_slot[0]!r} ({missing_slot[1]}) "
                                    "not in candidate slot_value_surface"})
                continue
            uncovered = sorted(set(sig.get("capability_tags", [])) - bound_tags)
            if uncovered:
                inapplicable.append({
                    **row,
                    "failed_check": f"capability_tags {uncovered} not covered by "
                                    "the candidate's bindings"})
                continue
            applicable.append(row)
    return applicable, inapplicable


# manifest-bypassing I/O: direct network/process escape hatches in src/ —
# all I/O goes through bound tools. Scan scope is spec-unpinned (§8.6 names
# the goal, not the mechanism) — minimal import/call scan, [H] review note.
_BYPASS_IO_RE = re.compile(
    r"^\s*(?:import|from)\s+(socket|http\.client|urllib|requests|httpx|aiohttp|"
    r"subprocess|ftplib|smtplib|telnetlib|paramiko)\b",
    re.MULTILINE,
)

_FAMILY_RE = re.compile(r"^([a-z]+)")


def step_structural(candidate: Candidate, inputs: HarnessInputs) -> StepResult:
    """Step 5 (mechanical half) — structural review.

    `slot_value_surface` declared; `delegation_requirements` consistent with
    code (no direct agent invocation paths — layer-6 check re-run on the frozen
    candidate). "Genuinely separated from skeleton" (entanglement) stays [H].
    """
    step = "structural"
    if not candidate.ok:
        return StepResult(step, False,
                          "; ".join(candidate.load_errors) or "unloadable candidate")
    if not candidate.manifest.slot_value_surface:
        return StepResult(step, False,
                          "slot_value_surface not declared — graduation signal "
                          "extraction (§9) has nothing to work from")
    invocation = check_no_agent_invocation(candidate, inputs)
    if not invocation.passed:
        return StepResult(step, False, invocation.detail)
    return StepResult(step, True)


def step_security_static(candidate: Candidate, inputs: HarnessInputs) -> StepResult:
    """Step 6 (mechanical half) — security static scan.

    Binding audit vs. ceiling; no UI/model-ids/secrets; no manifest-bypassing
    I/O in src/. The judge-side review is model-backed and separate.
    """
    step = "security_static"
    if not candidate.ok:
        return StepResult(step, False,
                          "; ".join(candidate.load_errors) or "unloadable candidate")
    bindings = check_bindings(candidate, inputs)
    if not bindings.passed:
        return StepResult(step, False, bindings.detail)
    scan = check_no_ui_no_model_ids_no_secrets(candidate, inputs)
    if not scan.passed:
        return StepResult(step, False, scan.detail)
    for p in _src_files(candidate):
        hit = _BYPASS_IO_RE.search(_read(p))
        if hit:
            return StepResult(step, False,
                              f"manifest-bypassing I/O: {p.relative_to(candidate.path)} "
                              f"imports {hit.group(1)!r} — all I/O goes through bound tools")
    return StepResult(step, True)


def model_family(model_id: str) -> str:
    """Leading token of the *model*, not the routing vendor.

    Gateway ids namespace by vendor (`anthropic/claude-3.5-sonnet`) and local
    runtimes suffix a tag (`llama3.1:8b`). Both are routing detail: family is
    read from the last path segment, so a gateway-routed judge cannot slip past
    the same-family check by wearing its provider's name.
    """
    tail = model_id.strip().lower().rsplit("/", 1)[-1]
    m = _FAMILY_RE.match(tail)
    return m.group(1) if m else ""


def step_model_diversity(*, judge_model: str, criteria_model: str,
                         impl_profile: dict) -> StepResult:
    """Step 6 model-diversity rule (mechanical).

    Judge and criteria-author models are pinned in certify config (hashed into
    the evidence) and must differ in family from the implementation profile
    where feasible. Families are leading-token mechanical: `gpt-4o-mini` → gpt,
    profile side from `profile_class` (`claude-class` → claude). A local/stub
    profile has no comparable family — passes under the feasibility clause,
    noted. The judge *run* is model-backed and lives elsewhere.
    """
    step = "model_diversity"
    if not judge_model or not criteria_model:
        return StepResult(step, False,
                          "judge and criteria-author models must be pinned in "
                          "certify config (CERTIFY_JUDGE_MODEL / CERTIFY_CRITERIA_MODEL)")

    config = (f"judge_model={judge_model}\ncriteria_model={criteria_model}\n"
              f"impl_profile={impl_profile.get('id', '')}\n")
    config_hash = hashlib.sha256(config.encode("utf-8")).hexdigest()[:16]

    impl_family = model_family(str(impl_profile.get("profile_class", "")))
    if impl_profile.get("provider") == "local" or impl_family in {"", "stub", "local"}:
        return StepResult(step, True,
                          f"impl family indeterminate ({impl_profile.get('provider')}/"
                          f"{impl_profile.get('profile_class')}) — diversity not "
                          f"comparable (feasibility clause); config_hash={config_hash}")

    for role, model in (("judge", judge_model), ("criteria-author", criteria_model)):
        if model_family(model) == impl_family:
            return StepResult(step, False,
                              f"{role} model {model!r} shares family {impl_family!r} "
                              "with the implementation profile — same-family "
                              f"self-agreement is a gate weakness; config_hash={config_hash}")
    return StepResult(step, True, f"families differ; config_hash={config_hash}")


def promote(
    *,
    catalog_root: Path,
    candidate_dir: Path,
    summary_writer,
    run_lockbuild,
    commit,
) -> StepResult:
    """Step 7 — atomic promotion (publish-only-on-success).

    Generate `entry.yaml` from `entry.draft.yaml` (routing_summary regenerated
    via the `summary_writer` seam — the draft's is advisory, never copied;
    `capability_tags` validated against tags.yaml, proposed tags refused for
    human approval; `model_requirements` carried over). Copy to `agents/<id>/`,
    append the (id, version, model_profile) trust record at `quarantined`, move
    `trace/` to content-addressed storage (`traces/<sha256>/` — location
    spec-unpinned, [H] note), run lockbuild, delete the `_proposed/` source,
    one commit. Any failure rolls everything back — no half-state.

    Seams (`summary_writer`, `run_lockbuild`, `commit`) keep the mechanics
    model-independent and testable; certify never promotes without a human
    behind `commit` (honesty clause).
    """
    import shutil
    from datetime import datetime, timezone

    import yaml

    step = "promote"
    candidate = load_candidate(candidate_dir)
    if not candidate.ok:
        return StepResult(step, False,
                          "; ".join(candidate.load_errors) or "unloadable candidate")
    m = candidate.manifest

    draft_file = candidate_dir / "entry.draft.yaml"
    if not draft_file.is_file():
        return StepResult(step, False, "entry.draft.yaml missing")
    draft = yaml.safe_load(draft_file.read_text(encoding="utf-8")) or {}

    tags_file = catalog_root / "tags.yaml"
    vocabulary = set((yaml.safe_load(tags_file.read_text(encoding="utf-8")) or {})
                     .get("tags", [])) if tags_file.is_file() else set()
    proposed = sorted(set(draft.get("capability_tags", [])) - vocabulary)
    if proposed:
        return StepResult(step, False,
                          f"proposed capability_tags {proposed} are not in tags.yaml — "
                          "new tags require human approval before promotion")

    # Layer 7 [M]: eval green on the manifest's profile, evidence captured.
    # Promotion is the last point where that can still be enforced — after it,
    # the candidate is a catalog entry and the question is closed.
    evidence = load_eval_evidence(candidate_dir)
    if evidence is None:
        return StepResult(step, False,
                          "no eval evidence in trace/ — step 3 has not run; a "
                          "candidate whose own eval suite was never executed is "
                          "not certifiable")
    if not evidence.get("passed"):
        return StepResult(step, False,
                          f"eval evidence is red: {evidence.get('detail') or 'failed'}")
    if evidence.get("model_profile") != m.model_profile:
        return StepResult(step, False,
                          f"eval evidence is for profile "
                          f"{evidence.get('model_profile')!r}, manifest declares "
                          f"{m.model_profile!r} — evidence is not inherited across "
                          "profiles (layer 8)")
    eval_digest = hashlib.sha256(
        (candidate_dir / "trace" / EVIDENCE_NAME).read_bytes()).hexdigest()

    dest = catalog_root / "agents" / m.agent_id
    if dest.exists():
        return StepResult(step, False, f"agents/{m.agent_id}/ already exists")

    trust_file = catalog_root / "trust.yaml"
    trust_before = trust_file.read_text(encoding="utf-8") if trust_file.is_file() else None
    lock_file = catalog_root / "catalog.lock.yaml"
    lock_before = lock_file.read_text(encoding="utf-8") if lock_file.is_file() else None
    stored_traces: list[Path] = []

    def rollback() -> None:
        if dest.exists():
            shutil.rmtree(dest)
        for p in stored_traces:
            if p.exists():
                p.unlink()
            if p.parent.exists() and not any(p.parent.iterdir()):
                p.parent.rmdir()
        if trust_before is not None:
            trust_file.write_text(trust_before, encoding="utf-8")
        if lock_before is not None:
            lock_file.write_text(lock_before, encoding="utf-8")
        elif lock_file.exists():
            lock_file.unlink()

    try:
        # stage a copy — the _proposed source stays intact until success
        shutil.copytree(candidate_dir, dest)
        (dest / "entry.draft.yaml").unlink()

        # trace → content-addressed storage
        trace_dir = dest / "trace"
        if trace_dir.is_dir():
            for f in sorted(p for p in trace_dir.rglob("*") if p.is_file()):
                digest = hashlib.sha256(f.read_bytes()).hexdigest()
                target = catalog_root / "traces" / digest / f.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
                stored_traces.append(target)
            shutil.rmtree(trace_dir)

        entry = dict(draft)
        entry["routing_summary"] = summary_writer(
            (candidate_dir / "SPEC.md").read_text(encoding="utf-8"), m)
        if m.model_profile:
            entry.setdefault("model_requirements", {})
            entry["model_requirements"].setdefault("profile", m.model_profile)
        (dest / "entry.yaml").write_text(
            yaml.safe_dump(entry, sort_keys=True), encoding="utf-8")

        run_id = f"certify-run-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')}"
        trust = (yaml.safe_load(trust_before) or {}) if trust_before else {"records": []}
        trust.setdefault("records", []).append({
            "id": m.agent_id,
            "version": str(draft.get("version", "1.0.0")),
            "model_profile": m.model_profile,
            "tier": "quarantined",
            "granted_by": run_id,
            # The eval digest is the traces/<sha256>/ directory the evidence
            # was just stored under, so the record points at the artifact
            # rather than asserting the gate passed.
            "evidence": f"certify steps green; harness={m.harness}; "
                        f"catalog_ref={m.catalog_ref}; "
                        f"eval={eval_digest} "
                        f"({len(evidence.get('criteria', []))} criteria)",
        })
        trust_file.write_text(yaml.safe_dump(trust, sort_keys=True), encoding="utf-8")

        run_lockbuild(catalog_root)
    except Exception as exc:
        rollback()
        return StepResult(step, False, f"promotion rolled back: {exc}")

    # Promotion is durable at this point (dest + trust.yaml + lock all written
    # and rebuilt) — the _proposed/ source is now disposable. Its removal is
    # cleanup, not part of the atomic operation: rolling back a fully
    # succeeded promotion because deleting the leftover source failed would
    # destroy the source without leaving the promoted copy either, i.e. the
    # exact half-state this step exists to prevent.
    cleanup_note = ""
    try:
        shutil.rmtree(candidate_dir)
    except Exception as exc:
        cleanup_note = (f" (promotion succeeded; failed to remove leftover "
                        f"_proposed/{candidate_dir.name}: {exc})")

    commit([dest, trust_file, lock_file, catalog_root / "traces"],
           f"certify: promote {m.agent_id} (quarantined)")
    return StepResult(step, True, f"promoted to agents/{m.agent_id}{cleanup_note}")
