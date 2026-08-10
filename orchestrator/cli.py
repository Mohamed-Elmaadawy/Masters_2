"""CLI run entrypoint: read a YAML RunConfig + a requirement-document JSON file,
assemble everything orchestrator/config.py, orchestrator/stages.py, and
orchestrator/human_cli.py already build, and start or resume one run_document()
execution.

    python -m orchestrator.cli run CONFIG.yaml INPUT.json
    python -m orchestrator.cli resume RUN_DIR

`resume` takes only a run directory, never a config or input path: everything it needs
-- the exact ResolvedRunConfig the run started with, and the document/requirement
records already on disk -- is read back from RUN_DIR itself
(orchestrator/config.py's read_resolved_run_config, orchestrator/pipeline.py's
read_document_run). This is deliberate, not a missing feature: a resume that accepted a
fresh config or input path could point at something that disagrees with what is already
on disk, and it is the on-disk run_id/prompt_hash that catch that disagreement (see
_prompt_provenance_mismatches below and design/ORCHESTRATOR_CONTRACT.md item 18) --
accepting fresh inputs here would reopen exactly the gap those checks close.

Exit codes (shared by both subcommands):
    0   completed, no stage errors recorded
    1   completed, but the record contains a DocumentStageError, a StageError on some
        requirement, or a requirement whose outcome is RunOutcome.ERROR
    2   usage / configuration / input / run-directory-collision / missing-API-key /
        not-a-resumable-run-directory / prompt-provenance-mismatch error -- always
        before any provider adapter is constructed
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

from design.schemas import RequirementSet, RunOutcome, prompt_fingerprint
from orchestrator.config import (
    ResolvedRunConfig, load_run_config, read_resolved_run_config, resolve_run_config,
    retry_args, run_dir_for, throttle_from, to_run_metadata, write_run_config,
)
from orchestrator.human_cli import answer_questions_cli, decide_at_cap_cli
from orchestrator.pipeline import (
    DocumentRunRecord, HumanFns, StageFns, Throttle, read_document_run, resume_document,
    run_document,
)
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
# and every one of them maps to the same exit code. OSError also covers a missing
# run_dir/run_config.json/document.json on the resume path (FileNotFoundError is an
# OSError subclass) -- no separate branch needed for that.
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


def _prompt_provenance_mismatches(resolved: ResolvedRunConfig) -> list[str]:
    """One message per stage whose prompt_path no longer hashes to the prompt_hash this
    run started with (or no longer exists at all) -- see design/ORCHESTRATOR_CONTRACT.md
    item 18. Empty means resuming is safe: every stage will call the same prompt text the
    original run recorded provenance for.

    Only the prompt file is checked, deliberately: everything else a resumed call needs
    (provider/model/temperature/output_mode) is already frozen, verbatim, inside the
    ResolvedStageConfig this function is given -- read straight back from run_config.json
    by read_resolved_run_config, never re-resolved from a YAML file resume does not even
    accept a path for. The prompt file is the one piece of a ResolvedStageConfig that is
    a reference (prompt_path) rather than an inlined value, so it is the one piece able
    to drift out from under an unchanged run_config.json.
    """
    mismatches = []
    for stage_name in sorted(resolved.stages):
        sc = resolved.stages[stage_name]
        if not sc.prompt_path.is_file():
            mismatches.append(f"{stage_name}: prompt file no longer exists: {sc.prompt_path}")
            continue
        current_hash = prompt_fingerprint(sc.prompt_path.read_text())
        if current_hash != sc.prompt_hash:
            mismatches.append(
                f"{stage_name}: {sc.prompt_path} has changed since this run started "
                f"(recorded prompt_hash={sc.prompt_hash!r}, file now hashes to "
                f"{current_hash!r})")
    return mismatches


def _finish(
    resolved: ResolvedRunConfig,
    run_dir: Path,
    adapters: dict[str, ProviderAdapter],
    human_fns_factory: Callable[[], HumanFns],
    output_fn: Callable[[str], None],
    execute: Callable[[StageFns, HumanFns, Throttle, int, Callable[[int], float]], DocumentRunRecord],
) -> int:
    """Shared tail of both subcommands, once each has resolved its own config and passed
    its own pre-adapter checks: build StageFns/Throttle/retry args from `resolved` and
    the already-constructed `adapters`, call `execute` (run_document or resume_document,
    already bound to everything specific to that path), and translate the outcome into
    one of the four exit codes documented at module level. Factored out so 'run' and
    'resume' cannot silently drift apart on exit-code handling -- the exact "two things
    that must agree" shape CLAUDE.md names as this project's biggest bug source, applied
    to control flow instead of a data field.
    """
    # ORDERING INVARIANT, tested only by inspection, not by an automated test (see
    # orchestrator/test_cli.py's module docstring note on this): this call MUST happen
    # before `execute` runs, because _do_run's `execute` closure writes run_dir/
    # run_config.json (write_run_config) only once it is actually invoked, below. A
    # prompt file re-read here (independent of resolve_run_config's own earlier
    # read/hash, e.g. deleted in the narrow window between the two) must fail HERE,
    # before any half-written run_dir exists -- not after. This was previously
    # regressed once by moving write_run_config too early; a black-box CLI test cannot
    # distinguish "resolve_run_config's own prompt check failed" from "this re-read
    # failed" without either duplicating the former or monkeypatching this function
    # directly (both were judged not worth the brittleness -- see the fix-wave report
    # for 2026-08-10). Keep write_run_config/to_run_metadata inside `execute`, never
    # move them before this line.
    stage_fns = _build_stage_fns(resolved, adapters)
    human_fns = human_fns_factory()
    throttle = throttle_from(resolved)
    max_attempts, backoff_seconds = retry_args(resolved)

    try:
        record = execute(stage_fns, human_fns, throttle, max_attempts, backoff_seconds)
    except (KeyboardInterrupt, EOFError):
        output_fn(f"Run interrupted. Inspect {run_dir} for any saved state.")
        return EXIT_INTERRUPTED

    _print_summary(record, output_fn)
    return EXIT_STAGE_ERRORS if _has_stage_errors(record) else EXIT_SUCCESS


def _do_run(
    config_path: Path,
    input_path: Path,
    adapter_factories: dict[str, Callable[[], ProviderAdapter]],
    human_fns_factory: Callable[[], HumanFns],
    output_fn: Callable[[str], None],
) -> int:
    try:
        run_config = load_run_config(config_path)
        requirement_set = RequirementSet.model_validate_json(input_path.read_text())
        resolved = resolve_run_config(run_config, config_path)
        run_dir = run_dir_for(resolved)
        if run_dir.exists():
            output_fn(f"Run directory already exists: {run_dir} -- refusing to reuse or "
                      "mix files with an existing run. Choose a different run_id or "
                      "output_dir, or use 'resume' to continue it.")
            return EXIT_CONFIG_ERROR

        providers_in_use = {sc.provider for sc in resolved.stages.values()}
        adapters = {provider: adapter_factories[provider]() for provider in providers_in_use}
    except _CONFIG_ERRORS as e:
        output_fn(f"Configuration/input error: {e}")
        return EXIT_CONFIG_ERROR

    def execute(stage_fns, human_fns, throttle, max_attempts, backoff_seconds):
        # metadata/write_run_config deliberately run here, inside execute, rather than
        # directly in _do_run: _finish builds stage_fns/human_fns/throttle/retry args
        # (including _build_stage_fns's independent prompt_path.read_text() re-reads)
        # BEFORE calling execute, so a crash there -- e.g. a prompt file deleted in a
        # narrow window -- still happens before run_dir/run_config.json exist on disk,
        # matching the pre-refactor order and leaving a clean retry instead of a
        # half-written run_dir that neither 'run' nor 'resume' can recover.
        metadata = to_run_metadata(resolved, datetime.now(timezone.utc))
        write_run_config(run_dir, resolved)
        return run_document(
            requirement_set, metadata, stage_fns, human_fns, throttle,
            resolved.max_revisions, run_dir, max_attempts, backoff_seconds)

    return _finish(resolved, run_dir, adapters, human_fns_factory, output_fn, execute)


def _do_resume(
    run_dir: Path,
    adapter_factories: dict[str, Callable[[], ProviderAdapter]],
    human_fns_factory: Callable[[], HumanFns],
    output_fn: Callable[[str], None],
) -> int:
    try:
        resolved = read_resolved_run_config(run_dir)
        # Loads document.json + every requirements/*.json and re-runs
        # DocumentRunRecord's full validator suite, including the run_id agreement check
        # (design/schemas.py's _outcome_matches_contents) -- the existing mechanism
        # design/ORCHESTRATOR_CONTRACT.md item 18 relies on instead of a new marker: a
        # run directory holding files from more than one run fails to load, here,
        # before any adapter is constructed.
        record = read_document_run(run_dir)

        # DocumentRunRecord's own validators only check that requirement_records agree
        # with metadata.run_id -- they never compare against the run_id inside a
        # SEPARATELY loaded run_config.json, because that file lives outside the record
        # entirely. Without this check, dropping a foreign run's run_config.json into
        # this run_dir would resume using the foreign config's models/prompts while
        # every record on disk still carries THIS run's run_id: nothing above would
        # notice, because each file is internally self-consistent on its own -- see
        # design/ORCHESTRATOR_CONTRACT.md item 18.
        if resolved.run_id != record.metadata.run_id:
            output_fn(f"Refusing to resume {run_dir}: run_config.json is for run "
                      f"{resolved.run_id!r} but document.json is run "
                      f"{record.metadata.run_id!r}")
            return EXIT_CONFIG_ERROR

        mismatches = _prompt_provenance_mismatches(resolved)
        if mismatches:
            output_fn(f"Refusing to resume {run_dir}: prompt provenance no longer "
                      "matches this run's run_config.json --")
            for message in mismatches:
                output_fn(f"  {message}")
            return EXIT_CONFIG_ERROR

        providers_in_use = {sc.provider for sc in resolved.stages.values()}
        adapters = {provider: adapter_factories[provider]() for provider in providers_in_use}
    except _CONFIG_ERRORS as e:
        output_fn(f"Configuration/input error: {e}")
        return EXIT_CONFIG_ERROR

    def execute(stage_fns, human_fns, throttle, max_attempts, backoff_seconds):
        return resume_document(
            run_dir, stage_fns, human_fns, throttle, resolved.max_revisions,
            max_attempts, backoff_seconds)

    return _finish(resolved, run_dir, adapters, human_fns_factory, output_fn, execute)


def _run(
    argv: list[str],
    adapter_factories: dict[str, Callable[[], ProviderAdapter]] = _DEFAULT_ADAPTER_FACTORIES,
    human_fns_factory: Callable[[], HumanFns] = lambda: HumanFns(
        answer_questions=answer_questions_cli, decide_at_cap=decide_at_cap_cli),
    output_fn: Callable[[str], None] = print,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m orchestrator.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Start a new run")
    run_parser.add_argument("config", type=Path, help="Path to the YAML RunConfig")
    run_parser.add_argument("input", type=Path, help="Path to a RequirementSet JSON file")

    resume_parser = subparsers.add_parser("resume", help="Resume an interrupted run")
    resume_parser.add_argument("run_dir", type=Path, help="Path to an existing run directory")

    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # argparse itself calls sys.exit(2) on a usage error -- normalize through the
        # same path as every other configuration error instead of letting argparse's
        # own process-exit bypass this function's return value.
        return e.code if isinstance(e.code, int) else EXIT_CONFIG_ERROR

    if args.command == "run":
        return _do_run(args.config, args.input, adapter_factories, human_fns_factory, output_fn)
    return _do_resume(args.run_dir, adapter_factories, human_fns_factory, output_fn)


def main(argv: Optional[list[str]] = None) -> int:
    return _run(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    sys.exit(main())
