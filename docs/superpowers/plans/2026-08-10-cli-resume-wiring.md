# CLI Resume Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `resume` subcommand to `orchestrator/cli.py` that continues an interrupted run via `orchestrator.pipeline.resume_document`, without letting a resume silently start a different run or use different prompt text than the original run recorded.

**Architecture:** `orchestrator/cli.py`'s `_run` dispatches to two subcommands via `argparse` subparsers: `run` (existing behavior, extracted into `_do_run`, unchanged) and `resume` (new, `_do_resume`). `resume` takes only a run directory — never a fresh config or input path — and reads everything it needs back from that directory: `orchestrator/config.py`'s `read_resolved_run_config` for the exact `ResolvedRunConfig` the run started with, and `orchestrator/pipeline.py`'s `read_document_run` for the document + every requirement record (which also re-runs `DocumentRunRecord`'s existing `run_id`-agreement validator, catching a directory mixed from more than one run). Before constructing any adapter, `resume` additionally recomputes each stage's `prompt_hash` from its `prompt_path` and refuses if it no longer matches what `run_config.json` recorded — a prompt file is the one piece of a resolved stage config that is a reference, not an inlined value, so it is the one piece able to drift without `run_config.json` itself changing. `_do_run` and `_do_resume` share a new `_finish` helper for everything after adapter construction, so the two subcommands cannot drift apart on exit-code translation.

**Tech Stack:** Python, Pydantic v2, `argparse`, plain-script tests (no pytest — same convention as `orchestrator/test_cli.py`/`design/test_schemas.py`).

## Global Constraints

- Exit codes are exactly the four `orchestrator/cli.py` already documents (`EXIT_SUCCESS=0`, `EXIT_STAGE_ERRORS=1`, `EXIT_CONFIG_ERROR=2`, `EXIT_INTERRUPTED=130`) — no new code, for either subcommand.
- No new marker file, sentinel, or `--force` flag for "is this directory resumable" — reuse `RunMetadata.run_id`/`RequirementRunRecord.run_id` and the existing `DocumentRunRecord` validator that checks them agree.
- `resume` accepts only a run directory as input — no config path, no input-JSON path.
- On prompt-provenance drift (a stage's `prompt_path` no longer hashes to the `prompt_hash` recorded in `run_config.json`, or the file is gone): **refuse**, `EXIT_CONFIG_ERROR`, before any adapter is constructed. Do not warn-and-continue.
- Tests are a plain script (`if __name__ == "__main__":` + an `ALL_TESTS` list + `ok()`/`section()` helpers), matching `orchestrator/test_cli.py`'s existing style exactly. Run via `python -m orchestrator.test_cli`.
- Never mutate the shared `orchestrator/example_prompts/*.txt` fixture files in a test — any test that needs to edit a prompt's content must do so on its own local copy.
- `design/schemas.py` is not touched by this plan — no need to run `python -m design.test_schemas` / `generate_diagrams` (that rule applies only after a `schemas.py` change).

---

## Task 1: Split `cli.py`'s run path into `_do_run` + a shared `_finish` tail, no behavior change

**Files:**
- Modify: `orchestrator/cli.py`
- Modify: `orchestrator/test_cli.py:149,162,174,184,205,228,260,300,354` (every `_run([str(config_path), ...` call site gains a leading `"run"` argument)
- Test: `orchestrator/test_cli.py` (run existing suite, must stay green)

**Interfaces:**
- Consumes: everything already in `orchestrator/cli.py` (`_build_stage_fns`, `_has_stage_errors`, `_print_summary`, the existing `_CONFIG_ERRORS` tuple, `_DEFAULT_ADAPTER_FACTORIES`) — unchanged.
- Produces: `_run(argv, adapter_factories=..., human_fns_factory=..., output_fn=...) -> int` now parses `argv[0]` as a subcommand (`"run"` or `"resume"`) before anything else. `_do_run(config_path, input_path, adapter_factories, human_fns_factory, output_fn) -> int` and `_finish(resolved, run_dir, adapters, human_fns_factory, output_fn, execute) -> int` are new, both used again in Task 2.

- [ ] **Step 1: Update every existing test call site to the `"run"` subcommand, confirm they fail against the current (pre-subcommand) `cli.py`**

In `orchestrator/test_cli.py`, change every occurrence of `_run([str(config_path), str(input_path)]` (there are 7: lines 149, 162, 174, 184, 205, 228, 260 — and one more at line 300 and 354 with a differently-named config path variable) to `_run(["run", str(config_path), str(input_path)]`. The two at lines 300/354 use `config_dict_path`; change those to `_run(["run", str(config_dict_path), str(input_path)]` — same edit, just matching the local variable name at each call site. Do this with a project-wide find/replace of `_run([str(config_path)` → `_run(["run", str(config_path)` and `_run([str(config_dict_path)` → `_run(["run", str(config_dict_path)` across the file (both patterns occur only inside call sites, not inside comments).

Run: `python -m orchestrator.test_cli`
Expected: every test that used to pass now fails — argparse rejects `"run"` as a bad value for the `config` positional (a `Path`), since `cli.py` has no subparsers yet. This confirms the test file was actually updated and the current `cli.py` doesn't yet understand subcommands.

- [ ] **Step 2: Rewrite `orchestrator/cli.py`'s argument parsing and split `_run` into `_do_run` + `_finish`**

Replace the module docstring, imports, and everything from `def _run(` to the end of the file with:

```python
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

from design.schemas import ALL_STAGES, RequirementSet, RunOutcome, prompt_fingerprint
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

    metadata = to_run_metadata(resolved, datetime.now(timezone.utc))
    write_run_config(run_dir, resolved)

    def execute(stage_fns, human_fns, throttle, max_attempts, backoff_seconds):
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
        read_document_run(run_dir)

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
```

- [ ] **Step 3: Run the existing suite, confirm it's green again**

Run: `python -m orchestrator.test_cli`
Expected: ends `9 checks passed, 0 failed` (same 9 tests as before Task 1, now invoked with `"run"`).

- [ ] **Step 4: Commit**

```bash
git add orchestrator/cli.py orchestrator/test_cli.py
git commit -m "refactor: split orchestrator/cli.py into run/resume subcommands, no behavior change yet"
```

---

## Task 2: Wire `resume` end-to-end and add its tests

**Files:**
- Modify: `orchestrator/test_cli.py` (new imports, one new local-prompts helper, five new tests)
- Test: `orchestrator/test_cli.py`

**Interfaces:**
- Consumes: `_do_resume`/`_prompt_provenance_mismatches`/`EXIT_CONFIG_ERROR`/`EXIT_SUCCESS` from `orchestrator/cli.py` (Task 1). `read_document_run`, `write_document_run`, `write_requirement_run` from `orchestrator.pipeline`. `load_run_config`, `resolve_run_config`, `run_dir_for`, `write_run_config`, `to_run_metadata` from `orchestrator.config`.
- Produces: nothing new consumed by later tasks — this task is test-only. `cli.py` itself needs no further changes; Task 1 already implemented `_do_resume` in full.

- [ ] **Step 1: Add the new imports and a local-prompts config helper**

At the top of `orchestrator/test_cli.py`, extend the existing import blocks:

```python
from datetime import datetime, timezone
```

(new, alongside the existing `import json` / `import tempfile` / `from pathlib import Path`)

```python
from design.schemas import (
    ALL_STAGES, ClarifyingQuestion, Classification, ConsistencyReport, DependencyReport,
    DocumentOutcome, DocumentRunRecord, Issue, IssueCategory, OutputMode, QualityReport,
    RefinedRequirement, Requirement, RequirementRunRecord, RequirementSet, RefinerAnswer,
    RefinerTurn, RunOutcome, SystemType, TestCase, TestPlan, TestStrategy, TestTechnique,
)
```

(replaces the existing `from design.schemas import (...)` block — adds `DocumentOutcome`,
`DocumentRunRecord`, `RequirementRunRecord`)

```python
from orchestrator.cli import EXIT_CONFIG_ERROR, EXIT_INTERRUPTED, EXIT_STAGE_ERRORS, EXIT_SUCCESS, _run
from orchestrator.config import (
    load_run_config, resolve_run_config, run_dir_for, to_run_metadata, write_run_config,
)
from orchestrator.human_cli import decide_at_cap_cli
from orchestrator.pipeline import HumanFns, read_document_run, write_document_run, write_requirement_run
from orchestrator.providers.base import CompletionResult
```

(the `orchestrator.cli` import line is unchanged; `orchestrator.config` is new; `orchestrator.pipeline`
gains `read_document_run`, `write_document_run`, `write_requirement_run`)

Then, right after the existing `write_config_yaml` function, add a second helper that
copies prompt files into a location private to one test, so a test that edits prompt
content never touches the shared `example_prompts/` fixture directory other tests read:

```python
def write_config_yaml_with_local_prompts(
    tmp_path: Path, run_id: str, output_dir: str = "runs",
) -> tuple[Path, dict[str, str]]:
    """Same shape as write_config_yaml, but copies each stage's prompt text into
    tmp_path/prompts/<stage>.txt instead of pointing at the shared PROMPTS_DIR fixture --
    for tests that need to edit a prompt file's content afterward without mutating a
    fixture every other test in this file also reads."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    prompts: dict[str, str] = {}
    for stage in ALL_STAGES:
        text = (PROMPTS_DIR / f"{stage}.txt").read_text()
        stage_path = prompts_dir / f"{stage}.txt"
        stage_path.write_text(text)
        prompts[stage] = str(stage_path)
    config_dict = {
        "run_id": run_id,
        "output_dir": output_dir,
        "max_revisions": 3,
        "rate_limits": {"gemini/fake-model": {"requests_per_minute": None}},
        "defaults": {"provider": "gemini", "model": "fake-model", "prompt_version": "v1"},
        "prompts": prompts,
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config_dict))
    return config_path, prompts
```

Run: `python -m orchestrator.test_cli`
Expected: still `9 checks passed, 0 failed` — this step only adds unused imports/a helper, no behavior changed yet. (An unused-import warning from a linter, if one runs, is expected and resolved once Step 2 adds the tests that use them.)

- [ ] **Step 2: Write the five new tests, add them to `ALL_TESTS`**

Append, before the `ALL_TESTS = [...]` list:

```python
# ---------------------------------------------------------------------------------
# resume -- continuing an interrupted run.
# ---------------------------------------------------------------------------------

def test_resume_completes_an_eof_interrupted_run() -> None:
    section("resume re-asks the human and finishes a run that hit EOFError mid-round")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        req_id = "REQ-1"
        config_path = write_config_yaml(tmp_path, run_id="run-resume-happy")
        input_path = write_input_json(tmp_path, req_id=req_id)
        issue = Issue(id=f"{req_id}-ISSUE-1", category=IssueCategory.AMBIGUOUS_TERM,
                     span="quickly", explanation="no measurable threshold")
        failing_report = QualityReport(requirement_id=req_id, passed=False, issues=[issue])
        question = ClarifyingQuestion(id="Q1", issue_id=issue.id, issue_category=issue.category,
                                      question_text="how fast, precisely?")
        first_responses = [
            _completion(ConsistencyReport(doc_id="DOC-1", conflicts=[])),
            _completion(DependencyReport(doc_id="DOC-1", dependencies=[])),
            _completion(Classification(requirement_id=req_id, system_type=SystemType.OTHER,
                                       rationale="test")),
            _completion(failing_report),
            _completion(RefinerTurn(requirement_id=req_id, revision_number=1,
                                    questions=[question])),
        ]
        first_fake = FakeAdapter(first_responses)

        def eof_human_fns_factory() -> HumanFns:
            def answer_questions(turn):
                raise EOFError("stdin closed")
            return HumanFns(answer_questions=answer_questions, decide_at_cap=decide_at_cap_cli)

        code = _run(["run", str(config_path), str(input_path)],
                    adapter_factories={"gemini": lambda: first_fake, "groq": _spy_factory()},
                    human_fns_factory=eof_human_fns_factory)
        ok("initial run is interrupted (exit 130)", code == EXIT_INTERRUPTED)

        run_dir = tmp_path.resolve() / "runs" / "run-resume-happy"
        answer = RefinerAnswer(question_id=question.id, answer_text="under 200ms",
                               user_confirms_resolved=False)
        rewrite = RefinedRequirement(
            requirement_id=req_id, original_text=REQUIREMENT_TEXT,
            refined_text="The system shall respond within 200ms.", revision_number=1,
            answers_used=[answer])
        resume_responses = [
            _completion(rewrite),                                                  # round 1 rewriter
            _completion(QualityReport(requirement_id=req_id, passed=True, issues=[])),  # round 2 check
            _completion(TestStrategy(requirement_id=req_id, system_type=SystemType.OTHER,
                                     techniques=[TestTechnique.EXPLORATORY],
                                     rationale="nothing else fits")),
            _completion(TestPlan(requirement_id=req_id, test_cases=[TestCase(
                id=f"TC-{len(req_id)}-{req_id}-1", requirement_ids=[req_id],
                technique_used=TestTechnique.EXPLORATORY, title="exploratory pass",
                steps=["do the thing"], expected_result="it works")])),
        ]
        resume_fake = FakeAdapter(resume_responses)

        def resume_human_fns_factory() -> HumanFns:
            def decide_at_cap_unused(record):
                raise AssertionError("decide_at_cap should not be called -- round 2 passes")
            return HumanFns(answer_questions=lambda turn: [answer],
                            decide_at_cap=decide_at_cap_unused)

        code = _run(["resume", str(run_dir)],
                    adapter_factories={"gemini": lambda: resume_fake, "groq": _spy_factory()},
                    human_fns_factory=resume_human_fns_factory)

        ok("resume completes (exit 0)", code == EXIT_SUCCESS)
        ok("all 4 resume-side stage calls were consumed", resume_fake.calls == 4)
        record = read_document_run(run_dir)
        ok("requirement outcome is completed",
           record.requirement_records[0].outcome == RunOutcome.COMPLETED)
        ok("the round the questioner asked before the interrupt is still on record",
           len(record.requirement_records[0].rounds) == 2)


def test_resume_missing_run_dir_rejected() -> None:
    section("resuming a run directory that doesn't exist exits 2, no adapter built")
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "no-such-run"
        code = _run(["resume", str(run_dir)],
                    adapter_factories={"gemini": _spy_factory(), "groq": _spy_factory()})
        ok("exit code is EXIT_CONFIG_ERROR", code == EXIT_CONFIG_ERROR)


def test_resume_prompt_drift_rejected() -> None:
    section("editing a prompt file after the run started makes resume refuse")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        req_id = "REQ-1"
        config_path, prompts = write_config_yaml_with_local_prompts(tmp_path, run_id="run-drift")
        input_path = write_input_json(tmp_path, req_id=req_id)
        fake = FakeAdapter(_happy_path_responses(req_id))

        code = _run(["run", str(config_path), str(input_path)],
                    adapter_factories={"gemini": lambda: fake, "groq": _spy_factory()},
                    human_fns_factory=_human_fns_unused)
        ok("initial run completes", code == EXIT_SUCCESS)

        classifier_prompt_path = Path(prompts["classifier"])
        classifier_prompt_path.write_text(
            classifier_prompt_path.read_text() + "\nEDITED AFTER RUN STARTED\n")

        messages: list[str] = []
        run_dir = tmp_path / "runs" / "run-drift"
        code = _run(["resume", str(run_dir)],
                    adapter_factories={"gemini": _spy_factory(), "groq": _spy_factory()},
                    output_fn=messages.append)

        ok("exit code is EXIT_CONFIG_ERROR", code == EXIT_CONFIG_ERROR)
        ok("message names the drifted stage", any("classifier" in m for m in messages))
        ok("the shared example_prompts fixture was never touched",
           "EDITED AFTER RUN STARTED" not in (PROMPTS_DIR / "classifier.txt").read_text())


def test_resume_mismatched_requirement_file_rejected() -> None:
    section("a requirements/*.json from a different run_id fails to load, refusing resume")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        req_id = "REQ-1"
        config_path, _ = write_config_yaml_with_local_prompts(tmp_path, run_id="run-mixed")
        run_config = load_run_config(config_path)
        resolved = resolve_run_config(run_config, config_path)
        run_dir = run_dir_for(resolved)
        write_run_config(run_dir, resolved)

        requirement_set = RequirementSet(
            doc_id="DOC-1", requirements=[Requirement(id=req_id, text=REQUIREMENT_TEXT)])
        metadata = to_run_metadata(resolved, datetime.now(timezone.utc))
        doc_record = DocumentRunRecord(
            requirement_set=requirement_set, metadata=metadata,
            outcome=DocumentOutcome.COMPLETED,
            consistency_report=ConsistencyReport(doc_id="DOC-1", conflicts=[]),
            dependency_report=DependencyReport(doc_id="DOC-1", dependencies=[]))
        write_document_run(run_dir, doc_record)
        foreign_record = RequirementRunRecord(
            requirement=Requirement(id=req_id, text=REQUIREMENT_TEXT),
            run_id="a-different-run-id")
        write_requirement_run(run_dir, foreign_record)

        code = _run(["resume", str(run_dir)],
                    adapter_factories={"gemini": _spy_factory(), "groq": _spy_factory()})
        ok("exit code is EXIT_CONFIG_ERROR", code == EXIT_CONFIG_ERROR)


def test_resume_after_completion_makes_no_stage_calls() -> None:
    section("resuming a fully COMPLETED run makes no stage calls and exits 0")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        req_id = "REQ-1"
        config_path = write_config_yaml(tmp_path, run_id="run-done")
        input_path = write_input_json(tmp_path, req_id=req_id)
        fake = FakeAdapter(_happy_path_responses(req_id))
        code = _run(["run", str(config_path), str(input_path)],
                    adapter_factories={"gemini": lambda: fake, "groq": _spy_factory()},
                    human_fns_factory=_human_fns_unused)
        ok("initial run completes", code == EXIT_SUCCESS)

        run_dir = tmp_path / "runs" / "run-done"
        resume_fake = FakeAdapter([])
        code = _run(["resume", str(run_dir)],
                    adapter_factories={"gemini": lambda: resume_fake, "groq": _spy_factory()},
                    human_fns_factory=_human_fns_unused)
        ok("exit code is EXIT_SUCCESS", code == EXIT_SUCCESS)
        ok("no stage calls made on resume", resume_fake.calls == 0)
```

Add all five to `ALL_TESTS`:

```python
ALL_TESTS = [
    test_bad_config_shape_rejected,
    test_malformed_yaml_rejected,
    test_bad_input_json_rejected_before_any_adapter,
    test_missing_input_file_rejected,
    test_run_dir_collision_rejected,
    test_happy_path_completes_with_exit_0,
    test_stage_error_exits_1,
    test_interrupted_by_eof_exits_130,
    test_cap_stopped_alone_does_not_exit_1,
    test_resume_completes_an_eof_interrupted_run,
    test_resume_missing_run_dir_rejected,
    test_resume_prompt_drift_rejected,
    test_resume_mismatched_requirement_file_rejected,
    test_resume_after_completion_makes_no_stage_calls,
]
```

- [ ] **Step 3: Run the suite, confirm all 14 pass**

Run: `python -m orchestrator.test_cli`
Expected: `14 checks passed, 0 failed`.

- [ ] **Step 4: Mutation-test the prompt-drift guard**

In `orchestrator/cli.py`'s `_prompt_provenance_mismatches`, temporarily change
`if current_hash != sc.prompt_hash:` to `if current_hash == sc.prompt_hash:`.

Run: `python -m orchestrator.test_cli`
Expected: `test_resume_prompt_drift_rejected` fails (it now refuses on the *unchanged*
prompts and accepts the *edited* one — inverted). Confirms the test actually exercises
the comparison, not just the code path around it.

Revert the change (`!=` back).

Run: `python -m orchestrator.test_cli`
Expected: `14 checks passed, 0 failed` again.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/test_cli.py
git commit -m "feat: wire resume subcommand into orchestrator/cli.py, add resume test coverage"
```

---

## Task 3: Update docs — contract, design notes, CLAUDE.md

**Files:**
- Modify: `design/ORCHESTRATOR_CONTRACT.md` (add item 18)
- Modify: `design/DESIGN_NOTES.md` (add "CLI resume wiring (2026-08-10)" section at the end)
- Modify: `CLAUDE.md` (bump "17 things" to "18 things"; replace the "What's still open: CLI resume..." sentence)

**Interfaces:**
- Consumes: nothing from Tasks 1-2 except their existence — this task is documentation only, no code.
- Produces: nothing consumed elsewhere.

- [ ] **Step 1: Add item 18 to `design/ORCHESTRATOR_CONTRACT.md`**

Insert immediately before the `---` / `## Things the schema does NOT check, by design` section at the end of the file:

```markdown
## 18. CLI resume and prompt-provenance drift

`orchestrator/cli.py`'s `resume` subcommand takes only a run directory — never a fresh
config or input path. Everything it needs is read back from `RUN_DIR` itself:
`orchestrator/config.py`'s `read_resolved_run_config` for the exact `ResolvedRunConfig`
the run started with, and `orchestrator/pipeline.py`'s `read_document_run` for the
document and every requirement record. This is deliberate, not a missing feature: it is
what makes "a resume must not be able to silently start a different run against an
existing run directory" true by construction rather than by an added check — there is no
second, independently-supplied config or input for the on-disk state to disagree with.

**Reused, not invented.** `read_document_run` re-runs `DocumentRunRecord`'s full
validator suite on load, including the `run_id` agreement check between
`metadata.run_id` and every `RequirementRunRecord.run_id` (`_outcome_matches_contents`,
item 9's "every RequirementRunRecord must carry run_id matching the document's
metadata.run_id"). A run directory holding requirement files from more than one run —
whether from manual tampering or from two different runs colliding on the same
`run_id`/`output_dir` — fails to load at all, in `resume`, before any provider adapter is
constructed. No new marker file or `--force` flag was added for this; the existing
schema-level check already does the job.

**What the schema's run_id check does NOT catch: a prompt file edited after the run
started.** `ResolvedStageConfig` (`orchestrator/config.py`) freezes every stage's
provider/model/temperature/output_mode as inlined values inside `run_config.json` — none
of those can drift once written, since `resume` never re-resolves from a YAML file it
does not even accept a path for. The one field that is a *reference* rather than an
inlined value is `prompt_path`: the prompt text itself is not persisted, only its
`prompt_hash` (item 12). If the file at `prompt_path` is edited between the original run
and a resume, `resume`'s calls would silently use different prompt text than the
`prompt_hash` already recorded in `run_config.json`/`RunMetadata.stages` claims — the
run's own record would misdescribe how the resumed portion of its results were produced.

**Decision: refuse, don't warn.** Before constructing any adapter, `resume` recomputes
`prompt_fingerprint` for every stage's `prompt_path` and compares it against the
`prompt_hash` `run_config.json` recorded at run start (`cli._prompt_provenance_mismatches`).
Any mismatch — including the file no longer existing — refuses the entire resume with
`EXIT_CONFIG_ERROR`, naming every affected stage, before any adapter is built. Rejected:
*warn and continue* — resume exists specifically for interruptions nobody may be watching
when they happen (a free-tier rate limit at 3am, a closed terminal, someone stepping
away mid-question per D1=c), so a warning printed to a terminal nobody is reading has no
more effect than silence, in exactly the scenario resume is for. *Silently reusing the
new prompt* was never considered: it is the same "silently overwrite" shape item 15
already rejected for a different field, for the same reason — it destroys, rather than
counts, the exact drift this check exists to catch.

*(DESIGN_NOTES: "CLI resume wiring".)*
```

- [ ] **Step 2: Add the "CLI resume wiring" section to `design/DESIGN_NOTES.md`**

Append at the end of the file:

```markdown
## CLI resume wiring (2026-08-10)

`orchestrator/cli.py` gained a `resume` subcommand alongside the existing `run` one --
`orchestrator.pipeline.resume_document` existed and was harness-tested
(`test_resume_positions` etc.) since the control-flow phase, but had no CLI path calling
it. See `design/ORCHESTRATOR_CONTRACT.md` item 18 for the two decisions this required
(why `resume` takes only a run directory, and why a drifted prompt file makes it refuse
rather than warn) -- this note only adds what the contract item doesn't cover: the CLI
shape itself, and what was rejected.

**Why a subcommand, not a flag on the existing invocation.** An earlier sketch kept one
command (`python -m orchestrator.cli CONFIG.yaml INPUT.json [--resume]`) and had the
existing `run_dir.exists()` guard branch on the flag: refuse unless `--resume` was
passed, then resume if it was. Rejected: this still takes a config/input pair for the
resume case, and doing anything with them -- even just to compute `run_dir_for` and find
the existing directory -- reopens exactly the "did the config change since the run
started" question item 18 has to answer for the *prompt* file specifically. It would also
have to re-derive `run_dir` from a possibly-different config and then discover it happens
to collide with an old one, rather than the operator naming the directory to resume
directly. Two subcommands, `resume` taking a bare `RUN_DIR` and nothing else, sidesteps
the question instead of answering it -- there is no config path fresh enough to disagree
with anything.

**Why `resume` re-checks `read_document_run` even though `resume_document` (pipeline.py)
does that anyway.** `resume_document` only reads the run directory *after*
`orchestrator/cli.py` has already constructed every provider adapter for it (`main`'s
existing `EXIT_CONFIG_ERROR`-before-any-adapter rule, unchanged from the `run` path).
Reading it once, deliberately, in `cli._do_resume`, before adapter construction, is what
keeps that rule true for `resume` too -- a foreign requirement file (item 18) is
discovered before any adapter exists, not after, even though `resume_document` would
have discovered it a few lines later anyway on its own read.

**Exit codes: no new one.** `resume` reuses all four existing codes unchanged -- a
prompt-provenance refusal and a missing/foreign run directory are both
`EXIT_CONFIG_ERROR`, matching every other "discovered before any adapter is built"
failure on the `run` path. `_do_run`/`_do_resume` share a `_finish` helper for
everything after adapter construction (build `StageFns`, `Throttle`, retry args, call
the pipeline function, translate the outcome to an exit code) specifically so the two
subcommands cannot drift apart on that translation -- see `_finish`'s own docstring in
`orchestrator/cli.py`.

Test coverage: `orchestrator/test_cli.py` -- a resume that completes an EOFError-
interrupted run (re-asks the human, does not repeat already-succeeded calls), a missing
run directory, a prompt-drift refusal (edits a local copy of one stage's prompt file
between `run` and `resume`, confirmed not to touch the shared `example_prompts/`
fixtures other tests depend on), a hand-constructed foreign-`run_id` requirement file
(proves the existing schema check, not new code, catches it), and a resume of an
already-`COMPLETED` run (zero stage calls, exit 0). The prompt-drift guard
(`_prompt_provenance_mismatches`) was mutation-tested by hand: inverting its `!=` to `==`
turns `test_resume_prompt_drift_rejected` red.
```

- [ ] **Step 3: Update `CLAUDE.md`**

Change:
```
| `design/ORCHESTRATOR_CONTRACT.md` | **Start here.** The 17 things the orchestrator must do that the schema deliberately does not enforce. |
```
to:
```
| `design/ORCHESTRATOR_CONTRACT.md` | **Start here.** The 18 things the orchestrator must do that the schema deliberately does not enforce. |
```

Change:
```
`orchestrator/cli.py` reads a `RunConfig` + a requirement-document JSON file, builds a
`StageFns` from `orchestrator/stages.py`'s eight factories and a `HumanFns` from
`orchestrator/human_cli.py`, and calls `orchestrator.pipeline.run_document`. What's
still open: CLI resume (`orchestrator.pipeline.resume_document` exists but has no CLI
wiring — see `orchestrator/cli.py`'s own docstring, "No resume in v1"). See
`design/DESIGN_NOTES.md`, "Real stage functions -- cross-stage validation" and "--
prompt provenance", for what changed in `orchestrator/stage_fns.py`/`pipeline.py` while
building `stages.py`.
```
to:
```
`orchestrator/cli.py` reads a `RunConfig` + a requirement-document JSON file, builds a
`StageFns` from `orchestrator/stages.py`'s eight factories and a `HumanFns` from
`orchestrator/human_cli.py`, and calls `orchestrator.pipeline.run_document` (`run`
subcommand) or `orchestrator.pipeline.resume_document` (`resume` subcommand, reading
everything it needs back from the run directory itself — see
`design/ORCHESTRATOR_CONTRACT.md` item 18 and `design/DESIGN_NOTES.md`, "CLI resume
wiring"). See `design/DESIGN_NOTES.md`, "Real stage functions -- cross-stage
validation" and "-- prompt provenance", for what changed in
`orchestrator/stage_fns.py`/`pipeline.py` while building `stages.py`.
```

- [ ] **Step 4: Commit**

```bash
git add design/ORCHESTRATOR_CONTRACT.md design/DESIGN_NOTES.md CLAUDE.md
git commit -m "docs: record CLI resume wiring decisions (contract item 18, design notes, CLAUDE.md)"
```
