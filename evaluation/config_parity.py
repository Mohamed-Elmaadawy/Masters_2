"""Fair-configuration enforcement (Task 6 finding 4, 2026-08-17). B1/B2 must use the
exact provider, model, temperature and output mode arm P's frozen experiment used
(docs/EVALUATION_PROTOCOL.md section 3: "same model, same temperature ... same output
shape"). Checked here, and called from evaluation/runner.py's CLI BEFORE any adapter is
constructed -- a mismatch must never spend a network call to discover.
"""

from __future__ import annotations

from pathlib import Path

from evaluation.schemas import BaselineCallConfig
from orchestrator.config import load_run_config, resolve_run_config

# orchestrator/runs_gemini.yaml is the project's own frozen arm-P experiment config --
# every stage is EXPECTED to use the same (provider, model, temperature, output_mode).
# orchestrator/test_config.py::test_active_yaml_configs_resolve_with_all_ten_stages
# asserts this file resolves cleanly, but resolving cleanly says nothing about every
# stage sharing one tuple -- a legal YAML can carry a per-stage override
# (orchestrator/config.py's StageOverride exists precisely to allow one). This module
# does not trust "resolves cleanly" as a proxy for "is uniform"; frozen_arm_p_config
# checks uniformity itself, every call, below.
_DEFAULT_FROZEN_RUN_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "orchestrator" / "runs_gemini.yaml")


def frozen_arm_p_config(
    run_config_path: Path = _DEFAULT_FROZEN_RUN_CONFIG_PATH,
    reference_stage: str = "quality_checker",
) -> BaselineCallConfig:
    """Reads arm P's actual frozen run config file and returns the scalars every
    stage in it uses -- but only after verifying every resolved stage actually shares
    the SAME (provider, model, temperature, output_mode) tuple (2026-08-17,
    second-round finding 8). A per-stage override that gives even one stage a
    different value would make "arm P's config" ambiguous -- there would be no single
    tuple left to hold B1/B2 to -- so this raises rather than silently picking
    `reference_stage`'s value and ignoring the divergence. `reference_stage` only
    needs to be ANY stage name present in the file; which one is arbitrary once
    uniformity is confirmed, not load-bearing."""
    config = load_run_config(run_config_path)
    resolved = resolve_run_config(config, run_config_path)

    tuples = {
        stage: (sc.provider, sc.model, sc.temperature, sc.output_mode)
        for stage, sc in resolved.stages.items()
    }
    if reference_stage not in tuples:
        raise ValueError(
            f"reference_stage {reference_stage!r} is not a stage in {run_config_path}")
    reference_tuple = tuples[reference_stage]
    divergent = {stage: t for stage, t in tuples.items() if t != reference_tuple}
    if divergent:
        raise ValueError(
            f"{run_config_path} does not use one (provider, model, temperature, "
            f"output_mode) tuple across every stage -- {reference_stage} uses "
            f"{reference_tuple}, but {sorted(divergent)} diverge: {divergent}. "
            "There is no single 'arm P config' to hold a baseline to while this is true.")

    stage_config = resolved.stages[reference_stage]
    return BaselineCallConfig(
        provider=stage_config.provider, model=stage_config.model,
        temperature=stage_config.temperature, timeout_seconds=stage_config.timeout_seconds,
        output_mode=stage_config.output_mode)


def enforce_fair_config(candidate: BaselineCallConfig, frozen: BaselineCallConfig) -> None:
    """Raises ValueError, naming every mismatched field, if `candidate` diverges from
    `frozen` on provider/model/temperature/output_mode. `timeout_seconds` is
    deliberately NOT compared -- it is a client-side transport setting (how long THIS
    process waits for a response), not part of the experiment configuration
    docs/EVALUATION_PROTOCOL.md section 3 requires arms to share."""
    mismatches = []
    if candidate.provider != frozen.provider:
        mismatches.append(f"provider={candidate.provider!r} (frozen arm P used {frozen.provider!r})")
    if candidate.model != frozen.model:
        mismatches.append(f"model={candidate.model!r} (frozen arm P used {frozen.model!r})")
    if candidate.temperature != frozen.temperature:
        mismatches.append(
            f"temperature={candidate.temperature!r} (frozen arm P used {frozen.temperature!r})")
    if candidate.output_mode != frozen.output_mode:
        mismatches.append(
            f"output_mode={candidate.output_mode!r} (frozen arm P used {frozen.output_mode!r})")
    if mismatches:
        raise ValueError(
            "baseline configuration does not match arm P's frozen experiment config "
            "(docs/EVALUATION_PROTOCOL.md section 3, 'same model, same temperature ... "
            "same output shape'): " + "; ".join(mismatches))
