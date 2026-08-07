"""Orchestrator control flow: stage sequencing, resume, retry, revision cap.

Every LLM call and human-interaction point is a parameter (StageFns, HumanFns), not a
hardcoded call -- see docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md.
orchestrator/test_harness.py wires in fixtures; orchestrator/stages.py (next phase)
wires in real ones. No control-flow logic is built twice.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, NamedTuple, Optional

from pydantic import BaseModel, ValidationError

from design.schemas import (
    ConsistencyReport, DependencyReport, DocumentStage, DocumentStageError,
    DocumentTokenUsage, FailureKind, PipelineStage, RefinerAnswer, RefinerTurn,
    RequirementRunRecord, RequirementSet, RunOutcome, StageConfig, TokenUsage,
)


class StageCallResult(NamedTuple):
    """One stage call's raw output, not yet validated against a schema model."""
    raw: dict
    prompt_tokens: int
    completion_tokens: int


class StageCallFailed(Exception):
    """Transport-level failure: network error, rate limit, timeout. Raised by a stage
    fn; never carries token counts, because the request was rejected before inference."""


@dataclass(frozen=True)
class StageFns:
    """Every LLM call in the pipeline, as a parameter. A frozen dataclass, not a dict --
    a typo'd dict key silently returns nothing and the stage gets skipped; a typo'd
    field name here is an immediate TypeError. Each callable returns a StageCallResult
    (or raises StageCallFailed). orchestrator/test_harness.py wires in scripted
    fixtures; orchestrator/stages.py (next phase) wires in real LLM calls."""
    check_consistency: Callable[..., StageCallResult]
    map_dependencies: Callable[..., StageCallResult]
    classify: Callable[..., StageCallResult]
    check_quality: Callable[..., StageCallResult]
    refine: Callable[..., StageCallResult]
    select_strategy: Callable[..., StageCallResult]
    generate_tests: Callable[..., StageCallResult]


@dataclass(frozen=True)
class HumanFns:
    """The pipeline's two human-interaction points, as parameters. Separate from
    StageFns because the source is categorically different (a person or a web request,
    not an LLM call) -- RefinerTurn/RefinerAnswer were split in the schema specifically
    so this contract works whether the caller is a CLI loop, a notebook cell, or a
    FastAPI backend (see DESIGN_NOTES.md); a blocking input() inside the orchestrator
    would discard that."""
    answer_questions: Callable[[RefinerTurn], list[RefinerAnswer]]
    decide_at_cap: Callable[[RequirementRunRecord], tuple[RunOutcome, str]]


@dataclass
class Throttle:
    """Paces stage calls, per model, so a tight per-minute quota mostly never gets hit
    in the first place -- backoff (see call_stage) then handles the rare exception
    rather than the normal case. Not frozen, unlike StageFns/HumanFns: it owns
    last_call_at as mutable state, since something has to hold it and threading a
    separate dict through every call site is worse. sleep_fn/now_fn are injected so
    production uses time.sleep/datetime.now(timezone.utc) and tests use a no-op
    recorder and a fake clock -- deterministic, and never actually wait.

    Keyed by model, not global: RunMetadata.stages[stage].model already allows
    different stages to use different models (e.g. a cheap classifier, a stronger
    generator), and those are separate quotas -- a single global interval is either
    too slow for one or too fast for the other. No default interval is hardcoded:
    neither Gemini's nor Groq's official docs expose a static free-tier RPM number,
    both defer to a live per-account dashboard. min_interval_seconds must be filled in
    from that dashboard for a real run.
    """
    sleep_fn: Callable[[float], None] = time.sleep
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    min_interval_seconds: dict[str, float] = field(default_factory=dict)
    last_call_at: dict[str, datetime] = field(default_factory=dict, init=False)

    def wait_for_slot(self, model: str) -> None:
        interval = self.min_interval_seconds.get(model, 0.0)
        last = self.last_call_at.get(model)
        now = self.now_fn()
        if last is not None:
            elapsed = (now - last).total_seconds()
            if elapsed < interval:
                self.sleep_fn(interval - elapsed)
        self.last_call_at[model] = self.now_fn()


class StageFailed(Exception):
    """Raised by call_stage once retries are exhausted. Carries what a StageError
    needs: kind, message, and retry_count (attempts before giving up, 0 meaning it
    failed on the first try with no retry -- see StageError's own docstring)."""
    def __init__(self, kind: FailureKind, message: str, retry_count: int):
        self.kind = kind
        self.message = message
        self.retry_count = retry_count
        super().__init__(message)


def call_stage(
    stage_fn: Callable[..., StageCallResult],
    args: tuple,
    model_cls: type[BaseModel],
    stage: PipelineStage,
    model_name: str,
    throttle: Throttle,
    usage_sink: list[TokenUsage],
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
) -> BaseModel:
    """Call one stage, validate its output, retry on failure, record usage.

    Only the stage_fn call itself is wrapped in `except Exception` -- not the
    validation that follows, and not any surrounding orchestrator code. A bug in the
    orchestrator's own loop must still crash instead of being filed as kind=OTHER,
    which would otherwise be an enum member with no real producer (CLAUDE.md: "don't
    write a check that can't fire"). See design/ORCHESTRATOR_CONTRACT.md item 7 and
    the FailureKind docstring in design/schemas.py.
    """
    last_kind: FailureKind = FailureKind.OTHER
    last_message = "call_stage was invoked with max_attempts < 1"

    for attempt in range(max_attempts):
        throttle.wait_for_slot(model_name)
        try:
            result = stage_fn(*args)
        except StageCallFailed as e:
            last_kind, last_message = FailureKind.TRANSPORT, str(e)
        except Exception as e:
            last_kind, last_message = FailureKind.OTHER, f"{type(e).__name__}: {e}"
        else:
            usage_sink.append(TokenUsage(stage=stage, prompt_tokens=result.prompt_tokens,
                                         completion_tokens=result.completion_tokens))
            try:
                return model_cls.model_validate(result.raw)
            except ValidationError as e:
                last_kind, last_message = FailureKind.VALIDATION, str(e)

        if attempt < max_attempts - 1:
            throttle.sleep_fn(backoff_seconds(attempt))

    raise StageFailed(last_kind, last_message, retry_count=max_attempts - 1)


def call_document_stage(
    stage_fn: Callable[..., StageCallResult],
    args: tuple,
    model_cls: type[BaseModel],
    stage: DocumentStage,
    model_name: str,
    throttle: Throttle,
    usage_sink: list[DocumentTokenUsage],
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
) -> BaseModel:
    """Structurally identical to call_stage apart from the stage/usage types -- same
    reasoning as the StageError/DocumentStageError split: a shared implementation
    parameterised by a PipelineStage | DocumentStage union would let a call meant for
    one level accidentally target the other."""
    last_kind: FailureKind = FailureKind.OTHER
    last_message = "call_document_stage was invoked with max_attempts < 1"

    for attempt in range(max_attempts):
        throttle.wait_for_slot(model_name)
        try:
            result = stage_fn(*args)
        except StageCallFailed as e:
            last_kind, last_message = FailureKind.TRANSPORT, str(e)
        except Exception as e:
            last_kind, last_message = FailureKind.OTHER, f"{type(e).__name__}: {e}"
        else:
            usage_sink.append(DocumentTokenUsage(stage=stage, prompt_tokens=result.prompt_tokens,
                                                 completion_tokens=result.completion_tokens))
            try:
                return model_cls.model_validate(result.raw)
            except ValidationError as e:
                last_kind, last_message = FailureKind.VALIDATION, str(e)

        if attempt < max_attempts - 1:
            throttle.sleep_fn(backoff_seconds(attempt))

    raise StageFailed(last_kind, last_message, retry_count=max_attempts - 1)


def run_document_stages(
    requirement_set: RequirementSet,
    stage_configs: dict[str, StageConfig],
    stage_fns: StageFns,
    throttle: Throttle,
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
) -> tuple[Optional[ConsistencyReport], Optional[DependencyReport],
           list[DocumentStageError], list[DocumentTokenUsage]]:
    """Runs the two document-level stages independently -- one failing must not stop
    the other from running (contract item 8, D1=b)."""
    errors: list[DocumentStageError] = []
    usage: list[DocumentTokenUsage] = []

    consistency_report: Optional[ConsistencyReport] = None
    try:
        consistency_report = call_document_stage(
            stage_fns.check_consistency, (requirement_set,), ConsistencyReport,
            DocumentStage.CONSISTENCY_CHECKER,
            stage_configs[DocumentStage.CONSISTENCY_CHECKER.value].model, throttle, usage,
            max_attempts, backoff_seconds)
    except StageFailed as f:
        errors.append(DocumentStageError(stage=DocumentStage.CONSISTENCY_CHECKER, kind=f.kind,
                                         message=f.message, retry_count=f.retry_count))

    dependency_report: Optional[DependencyReport] = None
    try:
        dependency_report = call_document_stage(
            stage_fns.map_dependencies, (requirement_set,), DependencyReport,
            DocumentStage.DEPENDENCY_MAPPER,
            stage_configs[DocumentStage.DEPENDENCY_MAPPER.value].model, throttle, usage,
            max_attempts, backoff_seconds)
    except StageFailed as f:
        errors.append(DocumentStageError(stage=DocumentStage.DEPENDENCY_MAPPER, kind=f.kind,
                                         message=f.message, retry_count=f.retry_count))

    return consistency_report, dependency_report, errors, usage


def resume_at(rec: RequirementRunRecord) -> Optional[PipelineStage]:
    """Where an interrupted or errored requirement record should resume.

    Moved here from design/test_schemas.py, where it lived "so it stays honest"
    (design/ORCHESTRATOR_CONTRACT.md item 6) because there was nowhere else for it --
    the schema deliberately does not encode pipeline ordering. Now that the orchestrator
    exists, this is the real implementation the orchestrator calls, not a copy kept in
    sync with one. Its test moved with it, to orchestrator/test_harness.py.
    """
    if rec.classification is None:
        return PipelineStage.CLASSIFIER
    if not rec.rounds:
        return PipelineStage.QUALITY_CHECKER
    last = rec.rounds[-1]
    if not last.quality_report.passed:
        # A round whose check failed but which already rewrote has finished refining;
        # the next step is checking that rewrite, i.e. the next round.
        return PipelineStage.REFINER if last.rewrite is None else PipelineStage.QUALITY_CHECKER
    if rec.test_strategy is None:
        return PipelineStage.STRATEGY_SELECTOR
    if rec.test_plan is None:
        return PipelineStage.TEST_GENERATOR
    return None
