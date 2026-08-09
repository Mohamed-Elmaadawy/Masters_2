"""CLI run entrypoint: read a YAML RunConfig + a requirement-document JSON file,
assemble everything orchestrator/config.py, orchestrator/stages.py, and
orchestrator/human_cli.py already build, and start one run_document() execution.

    python -m orchestrator.cli CONFIG.yaml INPUT.json

No resume in v1 -- orchestrator.pipeline.resume_document exists but has no CLI wiring
here; that is the immediately following CLI task, not this one.

Exit codes:
    0   completed, no stage errors recorded
    1   completed, but the record contains a DocumentStageError, a StageError on some
        requirement, or a requirement whose outcome is RunOutcome.ERROR
    2   usage / configuration / input / run-directory-collision / missing-API-key error
        -- always before any provider adapter is constructed
    130 interrupted (KeyboardInterrupt or EOFError, e.g. from a terminal HumanFns call)

Adapter construction and HumanFns are the one seam this module exposes for tests
(`adapter_factories`/`human_fns_factory` params on `_run`) -- narrow, not a DI
framework: production code (`main`) always passes the real ones.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import yaml
from pydantic import ValidationError

from design.schemas import ALL_STAGES, RequirementSet, RunOutcome
from orchestrator.config import (
    ResolvedRunConfig, load_run_config, resolve_run_config, retry_args, run_dir_for,
    throttle_from, to_run_metadata, write_run_config,
)
from orchestrator.human_cli import answer_questions_cli, decide_at_cap_cli
from orchestrator.pipeline import DocumentRunRecord, HumanFns, StageFns, run_document
from orchestrator.providers.base import ProviderAdapter
from orchestrator.providers.gemini import GeminiAdapter
from orchestrator.providers.groq import GroqAdapter
from orchestrator.stages import (
    make_check_consistency_fn, make_check_quality_fn, make_classify_fn,
    make_generate_tests_fn, make_map_dependencies_fn, make_refine_questioner_fn,
    make_refine_rewriter_fn, make_select_strategy_fn,
)

EXIT_SUCCESS = 0
EXIT_STAGE_ERRORS = 1
EXIT_CONFIG_ERROR = 2
EXIT_INTERRUPTED = 130

# What resolve_run_config's ValueError, RunConfig/ResolvedRunConfig's ValidationError,
# a syntactically invalid YAML file (yaml.YAMLError), a bad --input file, and a
# from_env() missing-API-key RuntimeError all have in common: every one of them is a
# configuration/input problem discovered before any provider adapter is constructed,
# and every one of them maps to the same exit code.
_CONFIG_ERRORS = (ValidationError, ValueError, OSError, RuntimeError, yaml.YAMLError)

_DEFAULT_ADAPTER_FACTORIES: dict[str, Callable[[], ProviderAdapter]] = {
    "gemini": GeminiAdapter.from_env,
    "groq": GroqAdapter.from_env,
}


def _build_stage_fns(resolved: ResolvedRunConfig, adapters: dict[str, ProviderAdapter]) -> StageFns:
    stages = resolved.stages
    return StageFns(
        check_consistency=make_check_consistency_fn(
            adapters[stages["consistency_checker"].provider], stages["consistency_checker"],
            stages["consistency_checker"].prompt_path.read_text()),
        map_dependencies=make_map_dependencies_fn(
            adapters[stages["dependency_mapper"].provider], stages["dependency_mapper"],
            stages["dependency_mapper"].prompt_path.read_text()),
        classify=make_classify_fn(
            adapters[stages["classifier"].provider], stages["classifier"],
            stages["classifier"].prompt_path.read_text()),
        check_quality=make_check_quality_fn(
            adapters[stages["quality_checker"].provider], stages["quality_checker"],
            stages["quality_checker"].prompt_path.read_text()),
        refine_questioner=make_refine_questioner_fn(
            adapters[stages["refiner_questioner"].provider], stages["refiner_questioner"],
            stages["refiner_questioner"].prompt_path.read_text()),
        refine_rewriter=make_refine_rewriter_fn(
            adapters[stages["refiner_rewriter"].provider], stages["refiner_rewriter"],
            stages["refiner_rewriter"].prompt_path.read_text()),
        select_strategy=make_select_strategy_fn(
            adapters[stages["strategy_selector"].provider], stages["strategy_selector"],
            stages["strategy_selector"].prompt_path.read_text()),
        generate_tests=make_generate_tests_fn(
            adapters[stages["test_generator"].provider], stages["test_generator"],
            stages["test_generator"].prompt_path.read_text()),
    )


def _has_stage_errors(record: DocumentRunRecord) -> bool:
    if record.errors:
        return True
    for req_record in record.requirement_records:
        if req_record.errors or req_record.outcome is RunOutcome.ERROR:
            return True
    return False


def _print_summary(record: DocumentRunRecord, output_fn: Callable[[str], None]) -> None:
    total_tokens = record.document_stage_tokens + sum(
        r.total_tokens for r in record.requirement_records)
    output_fn(f"Document outcome: {record.outcome.value}")
    for req_record in record.requirement_records:
        output_fn(f"  {req_record.requirement.id}: {req_record.outcome.value}"
                  f"{f' ({len(req_record.errors)} error(s))' if req_record.errors else ''}")
    output_fn(f"Total tokens: {total_tokens}")


def _run(
    argv: list[str],
    adapter_factories: dict[str, Callable[[], ProviderAdapter]] = _DEFAULT_ADAPTER_FACTORIES,
    human_fns_factory: Callable[[], HumanFns] = lambda: HumanFns(
        answer_questions=answer_questions_cli, decide_at_cap=decide_at_cap_cli),
    output_fn: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m orchestrator.cli")
    parser.add_argument("config", type=Path, help="Path to the YAML RunConfig")
    parser.add_argument("input", type=Path, help="Path to a RequirementSet JSON file")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # argparse itself calls sys.exit(2) on a usage error -- normalize through the
        # same path as every other configuration error instead of letting argparse's
        # own process-exit bypass this function's return value.
        return e.code if isinstance(e.code, int) else EXIT_CONFIG_ERROR

    try:
        run_config = load_run_config(args.config)
        requirement_set = RequirementSet.model_validate_json(args.input.read_text())
        resolved = resolve_run_config(run_config, args.config)
        run_dir = run_dir_for(resolved)
        if run_dir.exists():
            output_fn(f"Run directory already exists: {run_dir} -- refusing to reuse or "
                      "mix files with an existing run. Choose a different run_id or "
                      "output_dir.")
            return EXIT_CONFIG_ERROR

        providers_in_use = {sc.provider for sc in resolved.stages.values()}
        adapters = {provider: adapter_factories[provider]() for provider in providers_in_use}
    except _CONFIG_ERRORS as e:
        output_fn(f"Configuration/input error: {e}")
        return EXIT_CONFIG_ERROR

    stage_fns = _build_stage_fns(resolved, adapters)
    human_fns = human_fns_factory()
    throttle = throttle_from(resolved)
    max_attempts, backoff_seconds = retry_args(resolved)
    metadata = to_run_metadata(resolved, datetime.now(timezone.utc))
    write_run_config(run_dir, resolved)

    try:
        record = run_document(
            requirement_set, metadata, stage_fns, human_fns, throttle,
            resolved.max_revisions, run_dir, max_attempts, backoff_seconds)
    except (KeyboardInterrupt, EOFError):
        output_fn(f"Run interrupted. Inspect {run_dir} for any saved state.")
        return EXIT_INTERRUPTED

    _print_summary(record, output_fn)
    return EXIT_STAGE_ERRORS if _has_stage_errors(record) else EXIT_SUCCESS


def main(argv: Optional[list[str]] = None) -> int:
    return _run(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
