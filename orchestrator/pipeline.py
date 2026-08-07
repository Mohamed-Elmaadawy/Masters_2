"""Orchestrator control flow: stage sequencing, resume, retry, revision cap.

Every LLM call and human-interaction point is a parameter (StageFns, HumanFns), not a
hardcoded call -- see docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md.
orchestrator/test_harness.py wires in fixtures; orchestrator/stages.py (next phase)
wires in real ones. No control-flow logic is built twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, NamedTuple, Optional

from design.schemas import (
    PipelineStage, RefinerAnswer, RefinerTurn, RequirementRunRecord, RunOutcome,
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
