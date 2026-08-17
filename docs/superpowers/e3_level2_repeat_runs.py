"""Task 7 of docs/superpowers/plans/2026-08-15-CLAUDE-CODE-HANDOVER.md (E3 level 2):
five repetitions of a frozen, six-scenario, ten-requirement stratified matrix, all
replaying the same frozen answer transcript
(docs/superpowers/results/2026-08-14-live-answers/answers.json). Level 1 is Task 2
(docs/superpowers/quality_checker_stability.py), already built and unrelated to this
file.

**Operational definition (frozen 2026-08-17, agreed before implementation):**
one experimental repeat = one execution of the complete six-scenario matrix below;
the matrix is repeated 5 times; this is honestly 5 experimental repetitions and
**30 document executions**, not 5 pipeline invocations. Each of the 10 requirements
keeps its own document context (scn-08/09/10/04/11a/01-failure are 6 SEPARATE
documents, never flattened into one artificial RequirementSet) -- consistency/
dependency context per document stays exactly what it was when these fixtures were
built.

    scn-08-clean            THEMAS-REQ-G                          (normal)
    scn-09-vague            THEMAS-REQ-D, THEMAS-REQ-E             (normal)
    scn-10-atomicity        AUTOGEN-US2, LUITEL-R7                 (normal)
    scn-04-conflict-numeric PURE-THEMAS-R6, PURE-THEMAS-R6-P       (normal)
    scn-11a-cap-generate    AUTOGEN-US3                            (normal)
    scn-01-dep-pair         PURE-ERTMS-R7, PURE-ERTMS-R8           (FORCED FAILURE)

The failure scenario is a fixed experimental stratum, present in every repetition,
not between-repeat configuration drift: docs/superpowers/e3-configs/failure_template.yaml
is identical to normal_template.yaml except one pre-registered invalid
consistency_checker model, ported from
docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-13-degraded.yaml
(2026-08-11, the only DocumentOutcome.DEGRADED on record anywhere in this repo) onto
the current ten-stage schema. It guarantees DocumentOutcome.DEGRADED every repeat.

**Wiring correction (2026-08-17, required before implementation):** an in-memory
`ResolvedRunConfig.model_copy(update={"run_id": ...})` cannot be handed to
`orchestrator.cli._run()`, which takes a YAML path and reloads it. So preflight and
paid execution would silently diverge -- preflight validating an in-memory object,
execution reloading the unmodified template -- if preflight ran on anything other
than the exact file `_run()` will later read. This module never does that: every one
of the 30 concrete YAML configs is generated and PERSISTED to disk FIRST
(`_generate_config`, writing under docs/superpowers/e3-configs/generated/), then
`load_run_config`/`resolve_run_config` are run against THAT EXACT FILE for every
preflight check (fingerprint, stage-set, collision), and the SAME file path is what
`run_matrix` passes to `_run()`. Preflight and execution are provably reading
identical bytes because they are reading the same file, not because two
independently-built configs are asserted equal.

**Historical YAMLs are not used as execution configs.** The five normal scenarios'
existing `docs/superpowers/results/2026-08-14-live-answers/configs/scn-*-v2-live.yaml`
files were verified (2026-08-17) NOT to load through the current resolver --
`load_run_config` raises `ValidationError: prompts is missing an entry for:
['consistency_checker_refined', 'dependency_mapper_refined']` -- because they predate
S3's ten-stage schema. They remain valid as READ-ONLY evidence for which outcome type
each requirement produced (used only to pick this matrix, see the handover discussion),
never as something this module loads and resolves.

Reused, unmodified: `orchestrator.cli._run` (never bypassed or reimplemented),
`docs/superpowers/results/2026-08-14-live-answers/answering_policy_driver.py`'s
`TranscriptAnswerPolicy`/`_human_fns_factory`/`_paid_gemini_adapter` (loaded the same
way that file loads its own siblings -- see `_load_module` below),
`evaluation.arm_p_report.compute_arm_p_cost`, `evaluation.atomic_io.atomic_write_json`.
No orchestrator/, design/schemas.py, evaluation/, or prompt change accompanies this file.

    python -m docs.superpowers.e3_level2_repeat_runs --self-test
    python docs/superpowers/e3_level2_repeat_runs.py prepare
    python docs/superpowers/e3_level2_repeat_runs.py run       # PAID -- real API calls
    python docs/superpowers/e3_level2_repeat_runs.py report --output RESULTS-E3-LEVEL2.md

Exit codes: 0 success; 2 configuration/preflight error, always before any adapter is
constructed; 130 interrupted (propagated from `_run`, halts the whole matrix rather
than continuing to spend budget against a run that just told us to stop).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, NamedTuple, Optional

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from design.schemas import (  # noqa: E402
    ALL_STAGES, OutputMode, Requirement, RequirementSet,
)
from evaluation.arm_p_report import compute_arm_p_cost  # noqa: E402
from evaluation.atomic_io import atomic_write_json  # noqa: E402
from orchestrator.cli import EXIT_CONFIG_ERROR, EXIT_INTERRUPTED, _run  # noqa: E402
from orchestrator.config import (  # noqa: E402
    ResolvedRunConfig, load_run_config, resolve_run_config, run_dir_for,
)
from orchestrator.pipeline import read_document_run  # noqa: E402
from orchestrator.providers.base import CompletionResult  # noqa: E402
from orchestrator.providers.groq import GroqAdapter  # noqa: E402

EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR_LOCAL = 2

_E3_CONFIGS_DIR = _REPO_ROOT / "docs" / "superpowers" / "e3-configs"
_NORMAL_TEMPLATE = _E3_CONFIGS_DIR / "normal_template.yaml"
_FAILURE_TEMPLATE = _E3_CONFIGS_DIR / "failure_template.yaml"
_GENERATED_DIR = _E3_CONFIGS_DIR / "generated"
_SUMMARIES_DIR = _GENERATED_DIR / "summaries"
_RUNS_OUTPUT_DIR_NAME = "runs"  # written into every generated config's output_dir field

_LIVE_ANSWERS_DIR = _REPO_ROOT / "docs" / "superpowers" / "results" / "2026-08-14-live-answers"
_ANSWERS_JSON = _LIVE_ANSWERS_DIR / "answers.json"
_FIXTURES_DIR = _LIVE_ANSWERS_DIR / "fixtures"
_SCN01_FIXTURE = (_REPO_ROOT / "docs" / "superpowers" / "results" / "2026-08-11-behavior-scenarios"
                 / "fixtures" / "scn-01-dep-pair.json")

REPEATS = 5


def _load_module(name: str, path: Path):
    """Same helper docs/superpowers/results/2026-08-14-live-answers/
    answering_policy_driver.py uses to load ITS OWN siblings -- these results-directory
    scripts are not package-importable, so every driver that reuses one loads it this
    way rather than duplicating its logic."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_answering_policy_driver = _load_module(
    "answering_policy_driver", _LIVE_ANSWERS_DIR / "answering_policy_driver.py")
TranscriptAnswerPolicy = _answering_policy_driver.TranscriptAnswerPolicy
_human_fns_factory = _answering_policy_driver._human_fns_factory
_paid_gemini_adapter = _answering_policy_driver._paid_gemini_adapter

_DEFAULT_ADAPTER_FACTORIES = {"gemini": _paid_gemini_adapter, "groq": GroqAdapter.from_env}


class Scenario(NamedTuple):
    key: str
    template_path: Path
    input_path: Path
    cap_decision: str  # "stop" or "generate" -- see _human_fns_factory
    expected_requirement_ids: tuple[str, ...]  # the frozen matrix, mechanically enforced -- see
    # _assert_scenario_fixtures_match_frozen_ids


# The frozen six-scenario / ten-requirement matrix (2026-08-17). Order is the
# execution order within every repeat -- fixed, not re-derived, so the sequence of
# calls made against one TranscriptAnswerPolicy instance is identical across repeats.
# expected_requirement_ids is checked against each fixture's ACTUAL contents by
# _assert_scenario_fixtures_match_frozen_ids (called from prepare_and_preflight) --
# a fixture silently edited or swapped is caught before any config is generated, not
# discovered later as an unexplained result.
SCENARIOS: list[Scenario] = [
    Scenario("scn08", _NORMAL_TEMPLATE, _FIXTURES_DIR / "scn-08-clean.json", "stop",
            ("THEMAS-REQ-G",)),
    Scenario("scn09", _NORMAL_TEMPLATE, _FIXTURES_DIR / "scn-09-vague.json", "stop",
            ("THEMAS-REQ-D", "THEMAS-REQ-E")),
    Scenario("scn10", _NORMAL_TEMPLATE, _FIXTURES_DIR / "scn-10-atomicity.json", "stop",
            ("AUTOGEN-US2", "LUITEL-R7")),
    Scenario("scn04", _NORMAL_TEMPLATE, _FIXTURES_DIR / "scn-04-conflict-numeric.json", "stop",
            ("PURE-THEMAS-R6", "PURE-THEMAS-R6-P")),
    Scenario("scn11a", _NORMAL_TEMPLATE, _FIXTURES_DIR / "scn-11-cap.json", "generate",
            ("AUTOGEN-US3",)),
    Scenario("scn01-failure", _FAILURE_TEMPLATE, _SCN01_FIXTURE, "stop",
            ("PURE-ERTMS-R7", "PURE-ERTMS-R8")),
]


# ---------------------------------------------------------------------------------
# Generation -- template -> one persisted, concrete YAML file per (repeat, scenario).
# ---------------------------------------------------------------------------------

def _generate_config(template_path: Path, run_id: str, output_dir_rel: str, dest_path: Path) -> Path:
    """Reads a template, overrides ONLY run_id/output_dir, rewrites `prompts` to
    paths RELATIVE TO dest_path's OWN directory (2026-08-17, portability fix: an
    earlier version wrote absolute, machine-specific paths -- e.g.
    C:\\Users\\<name>\\...\\orchestrator\\example_prompts\\... -- which would commit a
    path that only resolves on the machine that generated it and would not even
    reproduce byte-identically on a second run on the SAME machine if the repo were
    ever checked out to a different location). The template's own relative paths are
    first resolved to an absolute path (anchored at the TEMPLATE's own directory,
    which is where they are authored relative to), then re-expressed relative to
    dest_path's directory via `os.path.relpath` -- `resolve_run_config` interprets a
    relative `prompts` entry relative to the CONFIG file's own directory (same
    mechanism the templates already rely on for their own relative paths), so this
    resolves correctly wherever the generated file and the repo it points into both
    live, as long as their relative offset is preserved (true for any clone of this
    repo, since both paths are inside it). Writes the result. Deterministic within one
    checkout: same template + same run_id + same output_dir_rel + same dest_path
    location always produces byte-identical output, since yaml.safe_load preserves the
    template's own key order and yaml.safe_dump(sort_keys=False) reproduces it -- this
    is what makes _config_fingerprint stable across independent `prepare` invocations,
    not just within one."""
    raw = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    base_dir = template_path.resolve().parent
    absolute_prompts = {stage: (base_dir / rel).resolve() for stage, rel in raw["prompts"].items()}
    dest_dir = dest_path.resolve().parent  # need not exist yet -- os.path.relpath is pure string math
    raw["prompts"] = {
        stage: Path(os.path.relpath(abs_path, dest_dir)).as_posix()
        for stage, abs_path in absolute_prompts.items()}
    raw["run_id"] = run_id
    raw["output_dir"] = output_dir_rel
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        template_label = template_path.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        template_label = str(template_path)  # outside the repo -- self-test tmp dirs only
    header = (
        f"# GENERATED by docs/superpowers/e3_level2_repeat_runs.py from "
        f"{template_label} -- do not hand-edit; "
        "regenerate via this driver's 'prepare' step. Persisted as an experiment "
        "artifact (E3 Level 2, 2026-08-17): preflight and paid execution both read "
        "THIS file, never an in-memory config, so the two cannot silently diverge.\n"
    )
    dest_path.write_text(header + yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return dest_path


def _config_fingerprint(resolved: ResolvedRunConfig) -> str:
    """Canonical sorted-JSON hash of everything that must stay identical across a
    scenario's 5 repetitions -- every stage's provider/model/temperature/output_mode/
    prompt_version/prompt_hash, retry, rate_limits, max_revisions. Excludes run_id/
    output_dir, the only two fields this module deliberately varies per (repeat,
    scenario). `sort_keys=True` makes this independent of dict insertion order (the
    `stages`/`rate_limits` dicts), per the 2026-08-17 requirement -- NOT
    model_dump_json(), which preserves declared field order but not dict key order."""
    payload = resolved.model_dump(mode="json", exclude={"run_id", "output_dir"})
    canonical = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PreparedExecution(NamedTuple):
    repeat: int
    scenario: Scenario
    run_id: str
    config_path: Path
    resolved: ResolvedRunConfig
    run_dir: Path
    fingerprint: str


def _run_id_for(repeat: int, scenario: Scenario) -> str:
    return f"e3-r{repeat}-{scenario.key}"


def _validate_prepared(prepared: list[PreparedExecution]) -> None:
    """Everything preflight must catch BEFORE any adapter is constructed: duplicate
    run_id, duplicate/colliding run_dir, a run_dir that already has output on disk,
    and a config fingerprint that is not identical across one scenario's 5
    repetitions. Raises ValueError naming the problem; never silently drops or
    dedupes anything."""
    run_ids = [p.run_id for p in prepared]
    if len(set(run_ids)) != len(run_ids):
        dupes = sorted({r for r in run_ids if run_ids.count(r) > 1})
        raise ValueError(f"duplicate run_id(s) generated: {dupes}")

    run_dirs = [p.run_dir for p in prepared]
    if len(set(run_dirs)) != len(run_dirs):
        dupes = sorted({str(d) for d in run_dirs if run_dirs.count(d) > 1})
        raise ValueError(f"duplicate run_dir(s) generated -- collision across scenarios/repeats: {dupes}")

    existing = [str(p.run_dir) for p in prepared if p.run_dir.exists()]
    if existing:
        raise ValueError(
            f"run directory already exists -- refusing to reuse or mix files with an "
            f"existing run (no batch-resume support: move the existing generated_dir/"
            f"runs aside or start a fresh generated_dir for a partial-matrix retry, "
            f"see prepare_and_preflight's docstring): {existing}")

    for p in prepared:
        if set(p.resolved.stages) != set(ALL_STAGES):
            raise ValueError(f"{p.config_path}: stage set is not the current ten-stage ALL_STAGES")

    by_scenario: dict[str, set[str]] = {}
    for p in prepared:
        by_scenario.setdefault(p.scenario.key, set()).add(p.fingerprint)
    drifted = sorted(k for k, v in by_scenario.items() if len(v) != 1)
    if drifted:
        raise ValueError(
            f"config fingerprint is not identical across the {REPEATS} repetitions "
            f"for scenario(s): {drifted}")


def _assert_scenario_fixtures_match_frozen_ids(scenarios: list[Scenario] = SCENARIOS) -> None:
    """Mechanically enforces the frozen matrix against what is actually on disk, so a
    fixture silently edited or swapped for a different document is caught here, not
    discovered later as an unexplained requirement id in a result. Loads each
    scenario's fixture exactly once (fixture content does not vary across repeats, so
    this does not need to run inside the 30-item generation loop) and requires an
    EXACT set match, both directions -- a fixture missing an expected id or carrying
    an extra one both fail. Also requires the ten ids named across the whole matrix to
    be unique -- a requirement id accidentally reused across two scenarios would
    silently corrupt any per-requirement aggregation across the matrix."""
    all_ids: list[str] = []
    for scenario in scenarios:
        requirement_set = RequirementSet.model_validate_json(
            scenario.input_path.read_text(encoding="utf-8"))
        actual_ids = {r.id for r in requirement_set.requirements}
        expected_ids = set(scenario.expected_requirement_ids)
        if actual_ids != expected_ids:
            raise ValueError(
                f"{scenario.key}: fixture {scenario.input_path} has requirement id(s) "
                f"{sorted(actual_ids)}, expected exactly {sorted(expected_ids)} (the frozen "
                "matrix -- design/DESIGN_NOTES.md, 'Task 7 (E3 Level 2 repeat runs)')")
        all_ids.extend(scenario.expected_requirement_ids)
    if len(all_ids) != 10 or len(set(all_ids)) != len(all_ids):
        raise ValueError(
            f"the frozen matrix must name exactly 10 unique requirement ids across all "
            f"scenarios -- got {len(all_ids)} total, {len(set(all_ids))} unique: {sorted(all_ids)}")


def _assert_templates_differ_only_by_forced_failure(
    normal_path: Path = _NORMAL_TEMPLATE, failure_path: Path = _FAILURE_TEMPLATE,
) -> None:
    """Proves failure_template.yaml diverges from normal_template.yaml by EXACTLY the
    two pre-registered differences and nothing else -- an unnoticed additional
    divergence (a stray temperature edit, a different retry setting) would be silent
    experimental drift between the normal and forced-failure strata, exactly the kind
    of thing a byte-level or line-level diff would catch by luck but a reviewer
    skimming two ~50-line YAML files could easily miss. Compares parsed YAML content
    (comments and formatting excluded on both sides, deliberately -- only the
    experimental configuration matters here), after normalizing exactly three fields:
    the two REGISTERED differences (consistency_checker's model override, the extra
    fake-model rate_limits entry), reset to the normal template's values before
    comparing; and run_id/output_dir, which are placeholder text in BOTH templates,
    never read for anything (docs/superpowers/e3_level2_repeat_runs.py always
    overwrites both via _generate_config) -- the same reason _config_fingerprint
    excludes them elsewhere in this module."""
    normal = yaml.safe_load(normal_path.read_text(encoding="utf-8"))
    failure = json.loads(json.dumps(yaml.safe_load(failure_path.read_text(encoding="utf-8"))))

    expected_override = {"model": "gemini-nonexistent-model-x9"}
    if failure["stages"].get("consistency_checker") != expected_override:
        raise ValueError(
            f"{failure_path}: consistency_checker override is "
            f"{failure['stages'].get('consistency_checker')!r}, expected exactly "
            f"{expected_override!r} (the pre-registered forced-failure model)")
    failure["stages"]["consistency_checker"] = normal["stages"].get("consistency_checker")

    fake_model_key = "gemini/gemini-nonexistent-model-x9"
    if fake_model_key not in failure["rate_limits"]:
        raise ValueError(
            f"{failure_path}: missing its registered rate_limits entry for {fake_model_key!r}")
    del failure["rate_limits"][fake_model_key]

    for placeholder_field in ("run_id", "output_dir"):
        failure[placeholder_field] = normal[placeholder_field]

    if failure != normal:
        diverging = sorted(k for k in normal if normal.get(k) != failure.get(k))
        raise ValueError(
            f"{failure_path} diverges from {normal_path} beyond the two registered "
            f"differences (consistency_checker's model override, the extra fake-model "
            f"rate_limits entry) -- unexpected divergence in top-level key(s): {diverging}")


def prepare_and_preflight(
    generated_dir: Path = _GENERATED_DIR, output_fn: Callable[[str], None] = print,
) -> list[PreparedExecution]:
    """Step 1-5 of the approved plan: generate all 30 concrete YAML configs from the
    two templates, persist them, give each its final run_id/output_dir, then
    load_run_config + resolve_run_config THOSE EXACT FILES and run every fingerprint/
    path/stage/collision check against the resolved result -- never against an
    in-memory config. Constructs no adapter. Raises ValueError on any failure (caught
    by main() and reported as EXIT_CONFIG_ERROR_LOCAL).

    `generated_dir` defaults to the real, tracked
    docs/superpowers/e3-configs/generated/ -- but is a parameter specifically so this
    module's own self-test can point it at a temporary directory instead (2026-08-17:
    an earlier version of this self-test called this function with no override,
    persisting 30 real files into the tracked source tree as a side effect of running
    `--self-test`, which is not something an offline test should ever do).

    No batch-resume support, deliberately, and this is the one place that matters:
    re-running `prepare`/`run` after a partially-executed matrix will FAIL here, in
    `_validate_prepared`, the moment it reaches a `run_id` whose `run_dir` already has
    output on disk -- by the same existing-run-directory check `orchestrator/cli.py`'s
    own `_do_run` already applies to one run at a time, just surfaced across all 30
    up front instead of discovered mid-matrix. This is intentional, not a gap to close
    trivially: recovering a partial matrix means either accepting the completed
    document executions as final and moving the frozen `generated_dir` aside before
    generating a fresh one for what remains, or discarding the partial attempt and
    starting over -- both are manual decisions, not something `prepare + run` can or
    should silently resume on your behalf.
    """
    _assert_templates_differ_only_by_forced_failure()
    _assert_scenario_fixtures_match_frozen_ids()

    prepared: list[PreparedExecution] = []
    for repeat in range(1, REPEATS + 1):
        for scenario in SCENARIOS:
            run_id = _run_id_for(repeat, scenario)
            config_path = generated_dir / f"{run_id}.yaml"
            _generate_config(scenario.template_path, run_id, _RUNS_OUTPUT_DIR_NAME, config_path)
            run_config = load_run_config(config_path)
            resolved = resolve_run_config(run_config, config_path)
            run_dir = run_dir_for(resolved)
            fingerprint = _config_fingerprint(resolved)
            prepared.append(PreparedExecution(
                repeat, scenario, run_id, config_path, resolved, run_dir, fingerprint))

    _validate_prepared(prepared)
    output_fn(
        f"Preflight OK: {len(prepared)} config(s) generated and resolved "
        f"({len(SCENARIOS)} scenario(s) x {REPEATS} repeat(s)), all run_id/run_dir "
        "distinct, config fingerprint identical across repeats for every scenario.")
    return prepared


# ---------------------------------------------------------------------------------
# Execution -- one _run() call per document execution, fresh policy per repeat.
# ---------------------------------------------------------------------------------

def _summary_path(p: PreparedExecution, summaries_dir: Path = _SUMMARIES_DIR) -> Path:
    return summaries_dir / f"{p.run_id}.summary.json"


def _build_summary(
    p: PreparedExecution, exit_code: int, record, cost, wall_clock_seconds: float,
    miss_count: int, drift_count: int,
) -> dict:
    return {
        "repeat": p.repeat,
        "scenario": p.scenario.key,
        "run_id": p.run_id,
        "run_dir": str(p.run_dir),
        "config_fingerprint": p.fingerprint,
        "cli_exit_code": exit_code,
        "document_outcome": record.outcome.value,
        "refined_analysis_outcome": record.refined_analysis_outcome.value,
        "requirement_outcomes": {
            r.requirement.id: r.outcome.value for r in record.requirement_records},
        "replay_miss_count": miss_count,
        "replay_drift_count": drift_count,
        "wall_clock_seconds": wall_clock_seconds,
        "cost": cost.model_dump(mode="json"),
    }


def _execute_one(
    p: PreparedExecution, policy, adapter_factories: dict, output_fn: Callable[[str], None],
    summaries_dir: Path = _SUMMARIES_DIR,
) -> tuple[int, dict]:
    """One document execution: exactly one `_run(["run", ...])` call against the
    persisted generated config (never an in-memory one), timed with
    `time.perf_counter()` (2026-08-17: no manual stopwatch, no pipeline-schema change
    -- the driver already controls every invocation, so it can just time it), then
    read back the DocumentRunRecord, cost it via evaluation.arm_p_report's existing
    function, and persist the summary atomically before returning. Returns (exit_code,
    summary) so the caller can decide whether to halt the whole matrix."""
    before_miss, before_drift = policy.miss_count, policy.drift_count

    def human_fns_factory():
        return _human_fns_factory(policy, p.scenario.cap_decision)

    start = time.perf_counter()
    exit_code = _run(
        ["run", str(p.config_path), str(p.scenario.input_path)],
        adapter_factories=adapter_factories, human_fns_factory=human_fns_factory,
        output_fn=output_fn)
    wall_clock_seconds = time.perf_counter() - start

    if exit_code in (EXIT_CONFIG_ERROR, EXIT_INTERRUPTED):
        # EXIT_CONFIG_ERROR: preflight already ruled out every structural cause
        # (collision, stage set, fingerprint drift) before any adapter existed -- a
        # config error surfacing only now means something preflight cannot see
        # without constructing an adapter (e.g. a missing API key). It will recur
        # identically on every remaining execution, so the caller halts rather than
        # looping 30 times into the same wall.
        #
        # EXIT_INTERRUPTED (2026-08-17, confirmed with a direct repro before fixing:
        # an adapter raising KeyboardInterrupt on its very first call -- before
        # run_document's phase-1 write_document_run has run -- made the un-guarded
        # read_document_run below raise an uncaught FileNotFoundError, since run_dir
        # existed but document.json did not yet): a run interrupted before
        # write_document_run's first write leaves no readable record at all, and one
        # interrupted mid-requirement can leave refined_analysis_outcome still
        # IN_PROGRESS -- neither is "this document execution's real outcome" in any
        # sense worth costing or summarizing. No summary is written for either case;
        # read_document_run is never called.
        return exit_code, {}

    record = read_document_run(p.run_dir)
    cost = compute_arm_p_cost(record)
    summary = _build_summary(
        p, exit_code, record, cost, wall_clock_seconds,
        policy.miss_count - before_miss, policy.drift_count - before_drift)
    summary_path = _summary_path(p, summaries_dir)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(summary_path, summary)
    output_fn(
        f"[repeat {p.repeat}] {p.scenario.key} ({p.run_id}): exit={exit_code} "
        f"doc_outcome={record.outcome.value} wall_clock={wall_clock_seconds:.1f}s "
        f"cost=${cost.total_cost_usd:.4f} "
        f"miss={policy.miss_count - before_miss} drift={policy.drift_count - before_drift}")
    return exit_code, summary


def run_matrix(
    prepared: list[PreparedExecution],
    adapter_factories: dict = _DEFAULT_ADAPTER_FACTORIES,
    policy_factory: Callable[[], object] = lambda: TranscriptAnswerPolicy(_ANSWERS_JSON),
    execute_one_fn: Callable = _execute_one,
    output_fn: Callable[[str], None] = print,
) -> tuple[int, list[dict]]:
    """Groups `prepared` by repeat (preserving SCENARIOS order within each), builds
    ONE fresh policy per repeat and reuses it across that repeat's six scenarios (the
    approved operational definition), and executes every document. Halts immediately
    -- returns whatever summaries were already collected, alongside the halting exit
    code -- on EXIT_INTERRUPTED or EXIT_CONFIG_ERROR, rather than continuing to spend
    budget against a run that just signalled it should stop.

    Returns `(status, summaries)`, NOT just `summaries` (2026-08-17: an earlier version
    returned only the list, which `main()`'s 'run' subcommand then discarded entirely
    -- always returning EXIT_SUCCESS even when the matrix halted early on an
    interrupt or a config error). `status` is EXIT_SUCCESS if every one of `prepared`
    ran (regardless of individual per-document CLI exit codes -- a document reaching
    EXIT_STAGE_ERRORS, e.g. the forced-failure scenario, is expected DATA, not a
    driver failure), or the halting exit code (EXIT_INTERRUPTED / EXIT_CONFIG_ERROR)
    otherwise.

    `policy_factory`/`execute_one_fn` are the seams docs/superpowers/
    e3_level2_repeat_runs.py's own self-test uses to verify the policy lifecycle and
    the summary/cost/wall-clock plumbing independently, without either one requiring
    a real adapter."""
    by_repeat: dict[int, list[PreparedExecution]] = {}
    for p in prepared:
        by_repeat.setdefault(p.repeat, []).append(p)

    summaries: list[dict] = []
    for repeat in sorted(by_repeat):
        policy = policy_factory()
        for p in by_repeat[repeat]:
            exit_code, summary = execute_one_fn(p, policy, adapter_factories, output_fn)
            if exit_code == EXIT_INTERRUPTED:
                output_fn(f"Interrupted at repeat {p.repeat}/{p.scenario.key} -- halting the matrix.")
                return EXIT_INTERRUPTED, summaries
            if exit_code == EXIT_CONFIG_ERROR:
                output_fn(
                    f"Configuration error at repeat {p.repeat}/{p.scenario.key} -- halting the "
                    "matrix rather than repeating the same failure 30 times. Fix and re-run "
                    "'prepare' + 'run' (re-generation is deterministic and safe to repeat).")
                return EXIT_CONFIG_ERROR, summaries
            summaries.append(summary)
    return EXIT_SUCCESS, summaries


# ---------------------------------------------------------------------------------
# Report -- Markdown table from the persisted per-document summaries.
# ---------------------------------------------------------------------------------

def _validate_summaries_complete(summaries: list[dict]) -> None:
    """Refuses to let a partial or corrupted set of summaries produce a report that
    LOOKS like the full frozen matrix (2026-08-17: an earlier version of build_report
    would happily describe "30 document executions" and claim per-scenario fingerprint
    stability as True from as little as one summary -- vacuously true with nothing to
    disagree with). Requires: exactly REPEATS * len(SCENARIOS) summaries; every
    (repeat, scenario) pair present EXACTLY once (catches missing, duplicate, AND
    extra/unexpected pairs in one check, since a dict keyed by the pair can only ever
    hold one entry per key); and, for every scenario, all REPEATS fingerprints
    identical -- the same guarantee _validate_prepared already enforces before
    execution, re-checked here against what was actually recorded, in case a summary
    file was hand-edited or came from a different generation of the templates."""
    expected_pairs = {(r, s.key) for r in range(1, REPEATS + 1) for s in SCENARIOS}
    seen_pairs: dict[tuple[int, str], list[dict]] = {}
    for s in summaries:
        pair = (s["repeat"], s["scenario"])
        seen_pairs.setdefault(pair, []).append(s)

    if len(summaries) != len(expected_pairs):
        raise ValueError(
            f"refusing to report: expected exactly {len(expected_pairs)} summaries "
            f"({REPEATS} repeats x {len(SCENARIOS)} scenarios), got {len(summaries)}")

    duplicates = sorted(pair for pair, entries in seen_pairs.items() if len(entries) > 1)
    if duplicates:
        raise ValueError(f"refusing to report: duplicate (repeat, scenario) pair(s): {duplicates}")

    missing = sorted(expected_pairs - set(seen_pairs))
    extra = sorted(set(seen_pairs) - expected_pairs)
    if missing or extra:
        raise ValueError(
            f"refusing to report: matrix is incomplete -- missing {missing or 'none'}, "
            f"unexpected {extra or 'none'}")

    by_scenario: dict[str, set[str]] = {}
    for s in summaries:
        by_scenario.setdefault(s["scenario"], set()).add(s["config_fingerprint"])
    drifted = sorted(k for k, v in by_scenario.items() if len(v) != 1)
    if drifted:
        raise ValueError(
            f"refusing to report: config fingerprint is not identical across all "
            f"{REPEATS} repeats for scenario(s): {drifted}")


def build_report(summaries: list[dict]) -> str:
    """Raises ValueError (never silently reports on partial data) unless `summaries`
    is exactly the complete, matrix -- see _validate_summaries_complete."""
    _validate_summaries_complete(summaries)
    lines = [
        "# E3 Level 2 -- repeat-run results",
        "",
        "5 repetitions x 6-scenario / 10-requirement matrix = 30 document executions.",
        "",
        "| Repeat | Scenario | run_id | Document outcome | Requirement outcomes | "
        "Miss | Drift | Cost (USD) | Wall-clock (s) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in sorted(summaries, key=lambda s: (s["repeat"], s["scenario"])):
        req_outcomes = "; ".join(f"{k}={v}" for k, v in s["requirement_outcomes"].items())
        lines.append(
            f"| {s['repeat']} | {s['scenario']} | {s['run_id']} | {s['document_outcome']} | "
            f"{req_outcomes} | {s['replay_miss_count']} | {s['replay_drift_count']} | "
            f"{s['cost']['total_cost_usd']:.4f} | {s['wall_clock_seconds']:.1f} |")

    fingerprints = {s["scenario"]: s["config_fingerprint"] for s in summaries}
    by_scenario: dict[str, set[str]] = {}
    for s in summaries:
        by_scenario.setdefault(s["scenario"], set()).add(s["config_fingerprint"])
    stable = all(len(v) == 1 for v in by_scenario.values())
    lines += ["", f"Config fingerprint identical across all repeats, per scenario: {stable}."]
    return "\n".join(lines) + "\n"


def _load_summaries() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(_SUMMARIES_DIR.glob("*.summary.json"))]


# ---------------------------------------------------------------------------------
# Self-test -- offline, no network, no adapter ever constructed.
# ---------------------------------------------------------------------------------

class _FakeAdapter:
    """Same shape as orchestrator/test_cli.py's FakeAdapter -- .complete() never
    touches the network. Each scripted response popped in order; an Exception
    instance is raised instead of returned."""

    def __init__(self, responses: list):
        self._responses = list(responses)

    def complete(self, prompt, *, model, temperature, timeout_seconds,
                output_mode=OutputMode.TEXT, response_schema=None, schema_name=None):
        return self._responses.pop(0)


def _completion(model) -> CompletionResult:
    return CompletionResult(text=model.model_dump_json(), prompt_tokens=5,
                            completion_tokens=5, output_mode=OutputMode.TEXT)


def _happy_path_responses(doc_id: str, req_id: str):
    """8-call scripted happy path -- same shape and same call order as
    orchestrator/test_cli.py's _happy_path_responses (S3: doc phase 1 (2), classifier +
    quality_checker-passed (2), doc phase 2 (2), strategy_selector + test_generator
    (2)). Duplicated here rather than imported: orchestrator/test_cli.py is a test
    module, not meant for cross-module import, and this is 8 lines.

    The test case id MUST use orchestrator/pipeline.py's `test_case_id_prefix` format
    (`TC-<len(req_id)>-<req_id>-`) -- `_test_generator_extra_check` rejects anything
    else, confirmed the hard way (2026-08-17): an earlier version of this fixture used
    a plain `TC-{req_id}-1` and the resulting VALIDATION_FAILURE, retried to
    exhaustion, silently emptied this function's own scripted response list for every
    later call in the document (IndexError: pop from empty list), which surfaced as
    an unrelated-looking TEST_GENERATOR OTHER_FAILURE two calls later, not as an
    obvious id-format error at the point it actually happened."""
    from design.schemas import (
        Classification, ConsistencyReport, DependencyReport, QualityReport, SystemType,
        TestCase, TestPlan, TestStrategy, TestTechnique,
    )
    from orchestrator.pipeline import test_case_id_prefix
    test_case_id = f"{test_case_id_prefix(req_id)}1"
    return [
        _completion(ConsistencyReport(doc_id=doc_id, conflicts=[])),
        _completion(DependencyReport(doc_id=doc_id, dependencies=[])),
        _completion(Classification(requirement_id=req_id, system_type=SystemType.OTHER,
                                   rationale="self-test")),
        _completion(QualityReport(requirement_id=req_id, passed=True, issues=[])),
        _completion(ConsistencyReport(doc_id=doc_id, conflicts=[])),
        _completion(DependencyReport(doc_id=doc_id, dependencies=[])),
        _completion(TestStrategy(requirement_id=req_id, system_type=SystemType.OTHER,
                                 techniques=[TestTechnique.EXPLORATORY], rationale="self-test")),
        _completion(TestPlan(requirement_id=req_id, test_cases=[TestCase(
            id=test_case_id, requirement_ids=[req_id], technique_used=TestTechnique.EXPLORATORY,
            title="exploratory pass", steps=["do the thing"], expected_result="it works")])),
    ]


def _self_test() -> int:
    import tempfile

    failures: list[str] = []

    def check(label: str, condition: bool) -> None:
        print(f"  {'ok' if condition else 'FAIL'}  {label}")
        if not condition:
            failures.append(label)

    # --- 1. Real templates resolve through the REAL resolver (the exact thing that
    # was verified by hand and found broken for the historical -v2-live YAMLs). ---
    print("1. Templates resolve cleanly against the current resolver")
    for template in (_NORMAL_TEMPLATE, _FAILURE_TEMPLATE):
        cfg = load_run_config(template)
        resolved = resolve_run_config(cfg, template)
        check(f"{template.name}: exactly ALL_STAGES", set(resolved.stages) == set(ALL_STAGES))
    check("failure_template overrides consistency_checker's model",
         resolve_run_config(load_run_config(_FAILURE_TEMPLATE), _FAILURE_TEMPLATE)
         .stages["consistency_checker"].model == "gemini-nonexistent-model-x9")
    check("normal_template does not",
         resolve_run_config(load_run_config(_NORMAL_TEMPLATE), _NORMAL_TEMPLATE)
         .stages["consistency_checker"].model != "gemini-nonexistent-model-x9")

    # --- 2. _generate_config is deterministic and produces a file the resolver
    # accepts -- this is the wiring-correction regression test -- AND is portable:
    # the raw YAML on disk carries relative prompt paths, not a machine-specific
    # absolute one (2026-08-17 portability fix). ---
    print("2. Generated configs are deterministic, portable, and resolve")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dest_a = _generate_config(_NORMAL_TEMPLATE, "self-test-run", "runs", tmp_path / "a.yaml")
        dest_b = _generate_config(_NORMAL_TEMPLATE, "self-test-run", "runs", tmp_path / "b.yaml")
        check("byte-identical for identical inputs", dest_a.read_text() == dest_b.read_text())
        raw_prompts_section = yaml.safe_load(dest_a.read_text())["prompts"]
        check("raw generated YAML uses RELATIVE prompt paths, not absolute",
             all(not Path(v).is_absolute() for v in raw_prompts_section.values()))
        resolved = resolve_run_config(load_run_config(dest_a), dest_a)
        check("run_id overridden correctly", resolved.run_id == "self-test-run")
        check("resolved prompts are absolute, existing paths (resolve_run_config's own "
             "contract, independent of how they were written in the YAML)",
             all(p.prompt_path.is_absolute() and p.prompt_path.is_file()
                 for p in resolved.stages.values()))

        # --- 3. Fingerprint: stable across different run_id/output_dir, sensitive to
        # a real config difference (canonical sorted JSON, not insertion order). ---
        print("3. Config fingerprint")
        dest_c = _generate_config(_NORMAL_TEMPLATE, "different-run-id", "different-output", tmp_path / "c.yaml")
        resolved_c = resolve_run_config(load_run_config(dest_c), dest_c)
        check("identical modulo run_id/output_dir -> identical fingerprint",
             _config_fingerprint(resolved) == _config_fingerprint(resolved_c))
        resolved_failure = resolve_run_config(load_run_config(_FAILURE_TEMPLATE), _FAILURE_TEMPLATE)
        check("a real config difference -> different fingerprint",
             _config_fingerprint(resolved) != _config_fingerprint(resolved_failure))
        # Insertion-order independence: rebuild the SAME resolved config's dict with
        # its stages/rate_limits keys in reverse order and confirm the fingerprint --
        # computed via json.dumps(..., sort_keys=True) -- is unaffected.
        payload = resolved.model_dump(mode="json", exclude={"run_id", "output_dir"})
        reordered = json.loads(json.dumps(payload))  # dict order preserved by json round-trip
        reordered["stages"] = dict(reversed(list(reordered["stages"].items())))
        fp_normal = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        fp_reordered = hashlib.sha256(json.dumps(reordered, sort_keys=True).encode()).hexdigest()
        check("insertion-order independent", fp_normal == fp_reordered)

    # --- 4. _validate_prepared catches every collision/drift case, on the REAL
    # resolved templates (not fakes), and passes the real 30-item matrix. ---
    print("4. Collision and fingerprint-drift detection")
    real_normal = resolve_run_config(load_run_config(_NORMAL_TEMPLATE), _NORMAL_TEMPLATE)
    scn = SCENARIOS[0]

    def _fake_prepared(run_id: str, run_dir: Path, fingerprint: str) -> PreparedExecution:
        r = real_normal.model_copy(update={"run_id": run_id})
        return PreparedExecution(1, scn, run_id, _NORMAL_TEMPLATE, r, run_dir, fingerprint)

    try:
        _validate_prepared([
            _fake_prepared("dup", Path("/tmp/a"), "fp1"),
            _fake_prepared("dup", Path("/tmp/b"), "fp2")])
        check("duplicate run_id raises", False)
    except ValueError as e:
        check("duplicate run_id raises", "duplicate run_id" in str(e))

    try:
        _validate_prepared([
            _fake_prepared("r1", Path("/tmp/same"), "fp1"),
            _fake_prepared("r2", Path("/tmp/same"), "fp2")])
        check("duplicate run_dir raises", False)
    except ValueError as e:
        check("duplicate run_dir raises", "duplicate run_dir" in str(e))

    try:
        drifted_a = _fake_prepared("r1", Path("/tmp/x"), "fp-A")
        drifted_b = drifted_a._replace(repeat=2, run_id="r2", run_dir=Path("/tmp/y"), fingerprint="fp-B")
        _validate_prepared([drifted_a, drifted_b])
        check("fingerprint drift within a scenario raises", False)
    except ValueError as e:
        check("fingerprint drift within a scenario raises", "fingerprint" in str(e))

    # The real thing: generate + preflight the actual frozen 6x5 matrix, against a
    # TEMPORARY generated_dir (2026-08-17: an earlier version called this with no
    # override, persisting 30 real files into the TRACKED source tree as a side
    # effect of running --self-test -- prepare_and_preflight's generated_dir
    # parameter exists specifically so this never happens again). Offline -- still
    # constructs no adapter.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_generated_dir = Path(tmp) / "generated"
        before = set(_GENERATED_DIR.glob("*.yaml")) if _GENERATED_DIR.is_dir() else set()
        prepared = prepare_and_preflight(generated_dir=tmp_generated_dir, output_fn=lambda _msg: None)
        after = set(_GENERATED_DIR.glob("*.yaml")) if _GENERATED_DIR.is_dir() else set()
        check("real matrix: 30 prepared executions", len(prepared) == len(SCENARIOS) * REPEATS)
        check("real matrix: preflight passes without raising", True)  # would have raised above
        check("self-test left the REAL tracked generated/ directory untouched", before == after)

    # --- 4b. Mechanical enforcement of the frozen matrix: fixture ids and the
    # template-diff-only-by-forced-failure guarantee. ---
    print("4b. Frozen-matrix mechanical enforcement (fixture ids, template diff scope)")
    _assert_scenario_fixtures_match_frozen_ids()  # the real matrix -- must pass cleanly
    check("real fixtures match the frozen requirement-id matrix", True)  # would have raised above
    _assert_templates_differ_only_by_forced_failure()  # the real templates -- must pass cleanly
    check("real templates differ only by the two registered forced-failure fields", True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # A scenario whose fixture doesn't match its declared expected ids must be caught.
        wrong_fixture_path = tmp_path / "wrong.json"
        wrong_fixture_path.write_text(RequirementSet(
            doc_id="scn-08-clean", requirements=[Requirement(id="NOT-THE-FROZEN-ID", text="x")]
        ).model_dump_json())
        wrong_scenario = SCENARIOS[0]._replace(input_path=wrong_fixture_path)
        try:
            _assert_scenario_fixtures_match_frozen_ids([wrong_scenario])
            check("mismatched fixture id raises", False)
        except ValueError as e:
            check("mismatched fixture id raises", "NOT-THE-FROZEN-ID" in str(e))

        # A requirement id reused across two scenarios must be caught (the "10 unique
        # ids across the whole matrix" requirement), even though each fixture matches
        # its OWN declared expected ids individually.
        dup_fixture_path = tmp_path / "dup.json"
        dup_fixture_path.write_text(RequirementSet(
            doc_id="dup", requirements=[Requirement(id="THEMAS-REQ-G", text="x")]
        ).model_dump_json())
        dup_scenario_a = Scenario("dup-a", _NORMAL_TEMPLATE, dup_fixture_path, "stop", ("THEMAS-REQ-G",))
        dup_scenario_b = Scenario("dup-b", _NORMAL_TEMPLATE, dup_fixture_path, "stop", ("THEMAS-REQ-G",))
        try:
            _assert_scenario_fixtures_match_frozen_ids([dup_scenario_a, dup_scenario_b])
            check("a requirement id reused across two scenarios raises", False)
        except ValueError as e:
            check("a requirement id reused across two scenarios raises", "unique" in str(e))

        # An unregistered template divergence (a stray temperature edit alongside the
        # two registered differences) must be caught, not normalized away.
        tampered_failure = json.loads(json.dumps(yaml.safe_load(_FAILURE_TEMPLATE.read_text())))
        tampered_failure["defaults"]["temperature"] = 0.1  # not a registered difference
        tampered_path = tmp_path / "tampered_failure.yaml"
        tampered_path.write_text(yaml.safe_dump(tampered_failure, sort_keys=False))
        try:
            _assert_templates_differ_only_by_forced_failure(failure_path=tampered_path)
            check("an unregistered template divergence raises", False)
        except ValueError as e:
            check("an unregistered template divergence raises", "defaults" in str(e))

        # A failure template that DROPPED its registered override entirely (looks
        # identical to normal_template) must ALSO be caught -- normalizing a field
        # that was never actually different would silently accept a config that no
        # longer forces the intended failure at all.
        no_override_failure = json.loads(json.dumps(yaml.safe_load(_NORMAL_TEMPLATE.read_text())))
        no_override_path = tmp_path / "no_override_failure.yaml"
        no_override_path.write_text(yaml.safe_dump(no_override_failure, sort_keys=False))
        try:
            _assert_templates_differ_only_by_forced_failure(failure_path=no_override_path)
            check("a failure template missing its forced-failure override raises", False)
        except ValueError as e:
            check("a failure template missing its forced-failure override raises",
                 "consistency_checker override" in str(e))

    # --- 5. Policy lifecycle: fresh per repeat, shared across a repeat's scenarios --
    # tested directly against run_matrix's grouping, with a fake execute_one_fn so no
    # adapter or real _run() call is needed. ---
    print("5. Policy lifecycle (fresh per repeat, shared within a repeat)")
    seen: list[tuple[int, str, int]] = []

    def _fake_execute_one(p, policy, adapter_factories, output_fn):
        seen.append((p.repeat, p.scenario.key, id(policy)))
        return EXIT_SUCCESS, {"repeat": p.repeat, "scenario": p.scenario.key,
                              "config_fingerprint": p.fingerprint, "replay_miss_count": 0,
                              "replay_drift_count": 0, "wall_clock_seconds": 0.1,
                              "cost": {"total_cost_usd": 0.0}, "document_outcome": "completed",
                              "requirement_outcomes": {}}

    toy = [_fake_prepared(f"toy-r{r}-{s.key}", Path(f"/tmp/{r}-{s.key}"), "fp") ._replace(repeat=r, scenario=s)
          for r in (1, 2) for s in SCENARIOS[:3]]
    status, summaries = run_matrix(toy, policy_factory=lambda: object(), execute_one_fn=_fake_execute_one,
                                   output_fn=lambda _msg: None)
    check("all 6 toy executions ran", len(seen) == 6)
    ids_by_repeat = {r: {pid for (rep, _k, pid) in seen if rep == r} for r in (1, 2)}
    check("one policy instance per repeat (same id across its 3 scenarios)",
         len(ids_by_repeat[1]) == 1 and len(ids_by_repeat[2]) == 1)
    check("different policy instance across repeats",
         ids_by_repeat[1] != ids_by_repeat[2])
    check("run_matrix returns one summary per execution", len(summaries) == 6)
    check("run_matrix reports EXIT_SUCCESS when nothing halted it", status == EXIT_SUCCESS)

    # --- 5b. Interruption/config-error propagation (2026-08-17 fix): run_matrix must
    # halt immediately, report the halting status (not EXIT_SUCCESS), preserve
    # summaries already collected, and never call execute_one_fn again afterward. ---
    print("5b. run_matrix halts on EXIT_INTERRUPTED, preserves prior summaries, reports status")
    seen_interrupt: list[tuple[int, str]] = []

    def _interrupting_execute_one(p, policy, adapter_factories, output_fn):
        seen_interrupt.append((p.repeat, p.scenario.key))
        if p.repeat == 1 and p.scenario.key == SCENARIOS[2].key:
            return EXIT_INTERRUPTED, {}
        return EXIT_SUCCESS, {"repeat": p.repeat, "scenario": p.scenario.key,
                              "config_fingerprint": p.fingerprint, "replay_miss_count": 0,
                              "replay_drift_count": 0, "wall_clock_seconds": 0.1,
                              "cost": {"total_cost_usd": 0.0}, "document_outcome": "completed",
                              "requirement_outcomes": {}}

    toy2 = [_fake_prepared(f"toy2-r{r}-{s.key}", Path(f"/tmp2/{r}-{s.key}"), "fp") ._replace(repeat=r, scenario=s)
           for r in (1, 2) for s in SCENARIOS[:3]]  # repeat 1: scn08, scn09, scn10 (interrupt here); repeat 2 never reached
    status2, summaries2 = run_matrix(toy2, policy_factory=lambda: object(),
                                     execute_one_fn=_interrupting_execute_one, output_fn=lambda _msg: None)
    check("halts exactly at the interrupting execution -- nothing after it runs",
         seen_interrupt == [(1, SCENARIOS[0].key), (1, SCENARIOS[1].key), (1, SCENARIOS[2].key)])
    check("earlier summaries preserved (the 2 successful executions before the interrupt)",
         len(summaries2) == 2)
    check("run_matrix reports EXIT_INTERRUPTED, not EXIT_SUCCESS", status2 == EXIT_INTERRUPTED)

    # Same shape, but the halting cause is a config error instead of an interrupt.
    def _config_erroring_execute_one(p, policy, adapter_factories, output_fn):
        if p.repeat == 1 and p.scenario.key == SCENARIOS[1].key:
            return EXIT_CONFIG_ERROR, {}
        return EXIT_SUCCESS, {"repeat": p.repeat, "scenario": p.scenario.key,
                              "config_fingerprint": p.fingerprint, "replay_miss_count": 0,
                              "replay_drift_count": 0, "wall_clock_seconds": 0.1,
                              "cost": {"total_cost_usd": 0.0}, "document_outcome": "completed",
                              "requirement_outcomes": {}}

    status3, summaries3 = run_matrix(toy2, policy_factory=lambda: object(),
                                     execute_one_fn=_config_erroring_execute_one, output_fn=lambda _msg: None)
    check("run_matrix reports EXIT_CONFIG_ERROR when that halts it", status3 == EXIT_CONFIG_ERROR)
    check("preserves the one summary collected before the config error", len(summaries3) == 1)

    # And the real _execute_one, not a fake: an adapter that raises KeyboardInterrupt
    # on its very first call must not crash (the direct repro that found this bug: an
    # earlier version called read_document_run unconditionally and raised an uncaught
    # FileNotFoundError, since run_dir existed but document.json did not yet).
    print("5c. _execute_one survives a real interrupt with no readable record yet")

    class _InterruptingAdapter:
        def complete(self, *a, **kw):
            raise KeyboardInterrupt()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        input_path = tmp_path / "input.json"
        input_path.write_text(RequirementSet(
            doc_id="E3-INTERRUPT-REPRO", requirements=[Requirement(id="REPRO-REQ-1", text="x")]
        ).model_dump_json())
        answers_path = tmp_path / "answers.json"
        answers_path.write_text(json.dumps(
            {"source_runs": [], "captured": "self-test", "answers": {},
             "fallback": {"answer_text": "x", "user_confirms_resolved": False}}))
        raw = yaml.safe_load(_NORMAL_TEMPLATE.read_text(encoding="utf-8"))
        real_base = _NORMAL_TEMPLATE.resolve().parent
        raw["prompts"] = {stage: str((real_base / rel).resolve()) for stage, rel in raw["prompts"].items()}
        for key in raw["rate_limits"]:
            raw["rate_limits"][key] = {"requests_per_minute": None, "tokens_per_minute": None}
        fast_template_path = tmp_path / "fast.yaml"
        fast_template_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        config_path = _generate_config(fast_template_path, "interrupt-repro", "runs", tmp_path / "cfg.yaml")
        resolved = resolve_run_config(load_run_config(config_path), config_path)
        fake_scenario = Scenario("interrupt-repro", _NORMAL_TEMPLATE, input_path, "stop", ("REPRO-REQ-1",))
        p = PreparedExecution(1, fake_scenario, "interrupt-repro", config_path, resolved,
                              run_dir_for(resolved), _config_fingerprint(resolved))
        interrupting = _InterruptingAdapter()
        exit_code, summary = _execute_one(
            p, TranscriptAnswerPolicy(answers_path),
            {"gemini": lambda: interrupting, "groq": lambda: interrupting},
            output_fn=lambda _msg: None, summaries_dir=tmp_path / "summaries")
        check("returns EXIT_INTERRUPTED, does not crash", exit_code == EXIT_INTERRUPTED)
        check("returns an empty summary (nothing readable to summarize)", summary == {})
        check("no summary file written for an interrupted execution",
             not (tmp_path / "summaries").exists() or not any((tmp_path / "summaries").iterdir()))

    # --- 6. Full integration: one real _run() call through a FakeAdapter, real
    # TranscriptAnswerPolicy against a scripted answers.json, real read-back/cost/
    # atomic-write. Proves _execute_one's actual plumbing, not a stand-in. ---
    print("6. Full integration: one real _run() call, FakeAdapter, real summary write")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        doc_id, req_id = "E3-SELFTEST", "SELFTEST-REQ-1"
        input_path = tmp_path / "input.json"
        input_path.write_text(RequirementSet(
            doc_id=doc_id, requirements=[Requirement(id=req_id, text="The system shall respond.")]
        ).model_dump_json())
        answers_path = tmp_path / "answers.json"
        answers_path.write_text(json.dumps(
            {"source_runs": [], "captured": "self-test", "answers": {},
             "fallback": {"answer_text": "no answer available", "user_confirms_resolved": False}}))

        # Unthrottled copy of the normal template, for THIS integration test only --
        # the real template's requests_per_minute: 15 makes orchestrator/pipeline.py's
        # Throttle actually sleep in real wall-clock time, which is correct for the
        # paid run but would make every offline self-test run slow for no reason.
        # Prompts are pre-resolved to absolute paths (against the REAL template's own
        # directory) before being written into fast_template.yaml so _generate_config's
        # own base-dir-relative resolution (relative to tmp_path here) is a no-op.
        raw = yaml.safe_load(_NORMAL_TEMPLATE.read_text(encoding="utf-8"))
        real_base = _NORMAL_TEMPLATE.resolve().parent
        raw["prompts"] = {stage: str((real_base / rel).resolve()) for stage, rel in raw["prompts"].items()}
        for key in raw["rate_limits"]:
            raw["rate_limits"][key] = {"requests_per_minute": None, "tokens_per_minute": None}
        fast_template_path = tmp_path / "fast_template.yaml"
        fast_template_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

        config_path = _generate_config(fast_template_path, "selftest-integration", "runs", tmp_path / "cfg.yaml")
        resolved = resolve_run_config(load_run_config(config_path), config_path)
        fake_scenario = Scenario("selftest", _NORMAL_TEMPLATE, input_path, "stop", (req_id,))
        p = PreparedExecution(1, fake_scenario, "selftest-integration", config_path, resolved,
                              run_dir_for(resolved), _config_fingerprint(resolved))

        summaries_tmp_dir = tmp_path / "summaries"
        fake_adapter = _FakeAdapter(_happy_path_responses(doc_id, req_id))
        exit_code, summary = _execute_one(
            p, TranscriptAnswerPolicy(answers_path),
            {"gemini": lambda: fake_adapter, "groq": lambda: fake_adapter},
            output_fn=lambda _msg: None, summaries_dir=summaries_tmp_dir)

        check("integration run succeeds (exit 0, happy path)", exit_code == EXIT_SUCCESS)
        check("summary has document_outcome completed", summary.get("document_outcome") == "completed")
        check("summary has 0 replay misses (no clarifying question needed)",
             summary.get("replay_miss_count") == 0)
        check("wall_clock_seconds is a positive float", summary.get("wall_clock_seconds", -1) > 0)
        check("cost computed from real recorded tokens", summary.get("cost", {}).get("total_cost_usd", 0) > 0)
        summary_path = _summary_path(p, summaries_tmp_dir)
        check("summary persisted atomically to disk", summary_path.is_file())
        on_disk = json.loads(summary_path.read_text())
        check("on-disk summary matches returned summary", on_disk == summary)

    # --- 7. Report builder, and the completeness gate that refuses a partial or
    # corrupted set (2026-08-17 fix). Mutation-tested: each mutation of a genuinely
    # complete 30-entry set must independently trip the gate, not rely on some OTHER
    # check catching it by accident. ---
    print("7. Markdown report builder + completeness gate (mutation-tested)")

    def _fake_full_summary_set() -> list[dict]:
        return [
            {"repeat": r, "scenario": s.key, "run_id": f"e3-r{r}-{s.key}",
             "document_outcome": "completed",
             "requirement_outcomes": {rid: "completed" for rid in s.expected_requirement_ids},
             "replay_miss_count": 0, "replay_drift_count": 0,
             "cost": {"total_cost_usd": 0.01}, "wall_clock_seconds": 10.0,
             "config_fingerprint": f"fp-{s.key}"}  # same fingerprint per scenario, every repeat
            for r in range(1, REPEATS + 1) for s in SCENARIOS
        ]

    def _deep_copy(obj):
        return json.loads(json.dumps(obj))

    complete = _fake_full_summary_set()
    check("a genuinely complete 30-entry set has the expected count",
         len(complete) == REPEATS * len(SCENARIOS) == 30)
    report = build_report(complete)
    check("report truthfully states 30 document executions given a complete set",
         "30 document executions" in report)
    check("report states fingerprint stability as True given a genuinely stable set",
         "identical across all repeats, per scenario: True" in report)
    check("report mentions a specific run_id from the set", "e3-r1-scn08" in report)

    # Mutation 1: too few (a plain missing entry, no compensating extra).
    too_few = _deep_copy(complete)[:-1]
    try:
        build_report(too_few)
        check("build_report refuses a set with one entry removed (wrong count)", False)
    except ValueError as e:
        check("build_report refuses a set with one entry removed (wrong count)",
             "expected exactly" in str(e))

    # Mutation 2: a genuine partial-input case -- only ONE repeat's worth of summaries
    # (6 entries), the shape an interrupted-after-repeat-1 matrix would actually
    # produce. This is the exact case the finding described: an earlier version would
    # have happily reported "30 document executions" from this.
    one_repeat_only = [s for s in _deep_copy(complete) if s["repeat"] == 1]
    check("one-repeat-only slice has 6 entries, as a real interrupted matrix would leave",
         len(one_repeat_only) == len(SCENARIOS) == 6)
    try:
        build_report(one_repeat_only)
        check("build_report refuses a genuinely partial (1-of-5-repeats) matrix", False)
    except ValueError as e:
        check("build_report refuses a genuinely partial (1-of-5-repeats) matrix",
             "expected exactly" in str(e))

    # Mutation 3: duplicate pair, with total count held at exactly 30 (one legitimate
    # pair dropped, replaced by a second copy of another) -- isolates the DUPLICATE
    # check from the count check, since count alone would not catch this.
    dup_set = _deep_copy(complete)
    dup_set = [s for s in dup_set if not (s["repeat"] == 5 and s["scenario"] == "scn01-failure")]
    duplicate_entry = _deep_copy(next(s for s in complete if s["repeat"] == 1 and s["scenario"] == "scn08"))
    dup_set.append(duplicate_entry)
    check("duplicate-pair mutation holds count at exactly 30", len(dup_set) == 30)
    try:
        build_report(dup_set)
        check("build_report refuses a duplicate (repeat, scenario) pair", False)
    except ValueError as e:
        check("build_report refuses a duplicate (repeat, scenario) pair", "duplicate" in str(e))

    # Mutation 4: extra/unexpected pair, with total count ALSO held at exactly 30 (one
    # legitimate pair dropped, replaced by a pair outside the frozen matrix) --
    # isolates the missing/extra check from both the count and duplicate checks.
    extra_set = _deep_copy(complete)
    extra_set = [s for s in extra_set if not (s["repeat"] == 5 and s["scenario"] == "scn01-failure")]
    extra_entry = _deep_copy(next(s for s in complete if s["repeat"] == 1 and s["scenario"] == "scn08"))
    extra_entry["repeat"] = 6  # outside 1..REPEATS -- not a real repeat
    extra_entry["run_id"] = "e3-r6-scn08"
    extra_set.append(extra_entry)
    check("extra-pair mutation holds count at exactly 30", len(extra_set) == 30)
    try:
        build_report(extra_set)
        check("build_report refuses an unexpected (repeat, scenario) pair", False)
    except ValueError as e:
        check("build_report refuses an unexpected (repeat, scenario) pair",
             "missing" in str(e) and "unexpected" in str(e))

    # Mutation 5: fingerprint drift within one scenario -- count correct, every pair
    # present exactly once, only the fingerprint disagrees. Isolates the drift check
    # from every structural check above it.
    drift_set = _deep_copy(complete)
    for s in drift_set:
        if s["repeat"] == 1 and s["scenario"] == "scn08":
            s["config_fingerprint"] = "a-different-fingerprint"
    try:
        build_report(drift_set)
        check("build_report refuses fingerprint drift within a scenario", False)
    except ValueError as e:
        check("build_report refuses fingerprint drift within a scenario", "scn08" in str(e))

    total = 46
    print(f"\nself-test: {total - len(failures)}/{total} checks passed")
    return EXIT_SUCCESS if not failures else 1


# ---------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("prepare", help="Generate and preflight all 30 configs. No adapter, no network.")
    subparsers.add_parser("run", help="Prepare, preflight, then execute all 30 document runs. PAID.")
    report_parser = subparsers.add_parser("report", help="Build the Markdown table from persisted summaries.")
    report_parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--self-test", action="store_true",
                        help="Offline check of this module's own logic. No network call, no API key.")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.command == "prepare":
        try:
            prepare_and_preflight()
        except (ValueError, OSError) as e:
            print(f"Configuration/preflight error: {e}")
            return EXIT_CONFIG_ERROR_LOCAL
        return EXIT_SUCCESS

    if args.command == "run":
        try:
            prepared = prepare_and_preflight()
        except (ValueError, OSError) as e:
            print(f"Configuration/preflight error: {e}")
            return EXIT_CONFIG_ERROR_LOCAL
        status, _summaries = run_matrix(prepared)
        return status

    if args.command == "report":
        summaries = _load_summaries()
        try:
            report = build_report(summaries)
        except ValueError as e:
            print(f"Refusing to generate report: {e}")
            return EXIT_CONFIG_ERROR_LOCAL
        if args.output:
            args.output.write_text(report, encoding="utf-8")
            print(f"Report written to {args.output}")
        else:
            print(report)
        return EXIT_SUCCESS

    parser.error("a command (prepare/run/report) is required unless --self-test is given")
    return EXIT_CONFIG_ERROR_LOCAL


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
