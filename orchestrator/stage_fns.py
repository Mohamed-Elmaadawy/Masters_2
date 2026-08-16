"""Every LLM call and human-interaction point in the pipeline, as typed parameters.

Moved out of orchestrator/pipeline.py (2026-08-09) so orchestrator/providers/ and the
future orchestrator/stages.py can import StageFns/HumanFns/the per-field Protocols
without pulling in pipeline.py's control-flow code. orchestrator/pipeline.py re-exports
everything here under its own name, so existing `from orchestrator.pipeline import
StageFns, HumanFns, StageCallResult, StageCallFailed` call sites keep working unchanged.

The ten Protocols below document each StageFns/HumanFns field's real call signature,
verified against every call site in orchestrator/pipeline.py (see
design/DESIGN_NOTES.md, "Run config, provider adapters, CLI HumanFns"). They are typing
aids only: StageFns/HumanFns stay frozen dataclasses, not Protocol-typed containers, so a
typo'd field name is still an immediate TypeError -- a Protocol field's structural typing
would not give you that. Per design/ORCHESTRATOR_CONTRACT.md item 16, this typing pass
also does NOT and cannot enforce the None-vs-[] document-context distinction on
check_quality/select_strategy/generate_tests -- a Callable-typed (or Protocol-typed)
parameter accepts None or [] equally happily; that distinction stays a runtime-tested
invariant (orchestrator/test_harness.py's test_document_context_none_vs_empty), not a
static one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple, Optional, Protocol

from design.schemas import (
    Classification, ConsistencyConflict, DependencyLink, QualityReport,
    RefinerAnswer, RefinerTurn, Requirement, RequirementRunRecord, RequirementSet,
    RunOutcome, TestStrategy,
)


class StageCallResult(NamedTuple):
    """One stage call's raw output, not yet validated against a schema model."""
    raw: dict
    prompt_tokens: int
    completion_tokens: int


class StageCallFailed(Exception):
    """Transport-level failure: network error, rate limit, timeout. Raised by a stage
    fn; never carries token counts, because the request was rejected before inference.
    Retrying usually helps -- call_stage/call_document_stage retry up to max_attempts
    times. Contrast StageCallFatal, below."""


class StageCallFatal(Exception):
    """Raised by a stage fn (in practice, a provider adapter) to signal a failure
    retrying cannot fix: bad credentials, a request the provider will never accept
    unchanged, an output mode the model doesn't support. Distinct from StageCallFailed:
    call_stage/call_document_stage record exactly one attempt and fail immediately
    (retry_count=0, kind=FailureKind.FATAL) instead of spending the remaining attempt
    budget on a request that cannot succeed. Never carries token counts, same reasoning
    as StageCallFailed -- rejected before inference.

    Deliberately narrow: NOT a general early-exit mechanism for a stage fn to skip
    retries for any reason it likes. Reserved for cases where retrying with the SAME
    inputs cannot possibly succeed -- provider configuration, capability, and
    authentication errors, per design/ORCHESTRATOR_CONTRACT.md's entry for this
    exception. See design/DESIGN_NOTES.md, "Run config, provider adapters, CLI
    HumanFns", for why this is the one authorized change to this project's otherwise
    unchanged retry behavior.
    """


class StageCallPartial(Exception):
    """Raised by a stage fn (in practice, a provider adapter) when a request reached
    inference and consumed tokens, but no usable output could be extracted -- Gemini
    safety-filtering removed every candidate, or a 200 response body was truncated or
    otherwise malformed after usage accounting. Distinct from both StageCallFailed and
    StageCallFatal, which by definition mean the request was rejected BEFORE inference
    and so never carry tokens: this is the one stage_fn-raised exception that DOES,
    because inference genuinely happened and the tokens were genuinely spent.

    Does not change retry policy -- call_stage/call_document_stage still retry it up to
    max_attempts, the same as StageCallFailed, since a malformed response on one attempt
    doesn't mean the next attempt will be malformed too. It exists purely so that spend
    is not silently lost from the record: without it, the only options are to force
    tokens into a StageAttempt shape that forbids them (TRANSPORT_FAILURE) or drop the
    token counts on the floor. Recorded as AttemptResult.OTHER_FAILURE, which already
    permits (but does not require) token counts -- no schema change was needed for this.
    """
    def __init__(self, message: str, prompt_tokens: int, completion_tokens: int):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        super().__init__(message)


class CheckConsistencyFn(Protocol):
    def __call__(self, requirement_set: RequirementSet) -> StageCallResult: ...


class MapDependenciesFn(Protocol):
    def __call__(self, requirement_set: RequirementSet) -> StageCallResult: ...


class ClassifyFn(Protocol):
    def __call__(
        self, requirement: Requirement, requirement_set: RequirementSet,
    ) -> StageCallResult: ...


class CheckQualityFn(Protocol):
    def __call__(
        self, requirement: Requirement, classification: Classification,
        relevant_conflicts: Optional[list[ConsistencyConflict]],
        relevant_dependencies: Optional[list[DependencyLink]],
        suppressed_issue_ids: list[str],
    ) -> StageCallResult: ...


class RefineQuestionerFn(Protocol):
    def __call__(
        self, requirement: Requirement, quality_report: QualityReport, revision_number: int,
    ) -> StageCallResult: ...


class RefineRewriterFn(Protocol):
    def __call__(
        self, requirement: Requirement, answers: list[RefinerAnswer], revision_number: int,
    ) -> StageCallResult: ...


class SelectStrategyFn(Protocol):
    def __call__(
        self, requirement: Requirement, classification: Classification,
        relevant_dependencies: Optional[list[DependencyLink]],
    ) -> StageCallResult: ...


class GenerateTestsFn(Protocol):
    def __call__(
        self, requirement: Requirement, strategy: TestStrategy,
        relevant_dependencies: Optional[list[DependencyLink]],
    ) -> StageCallResult: ...


class AnswerQuestionsFn(Protocol):
    def __call__(self, turn: RefinerTurn) -> list[RefinerAnswer]: ...


class DecideAtCapFn(Protocol):
    def __call__(self, record: RequirementRunRecord) -> tuple[RunOutcome, str]: ...


@dataclass(frozen=True)
class StageFns:
    """Every LLM call in the pipeline, as a parameter. A frozen dataclass, not a dict --
    a typo'd dict key silently returns nothing and the stage gets skipped; a typo'd
    field name here is an immediate TypeError. Each callable returns a StageCallResult
    (or raises StageCallFailed / StageCallFatal). orchestrator/test_harness.py wires in
    scripted fixtures; orchestrator/stages.py (next phase) wires in real LLM calls.

    check_consistency_refined/map_dependencies_refined (S3, "phase the pipeline" --
    design/DESIGN_NOTES.md) are the SECOND document-analysis phase's own callables --
    genuinely independent closures (own ResolvedStageConfig, own prompt), not a re-run
    of check_consistency/map_dependencies under a different label. Optional, defaulting
    to None: orchestrator/pipeline.py's run_document/resume_document fall back to
    reusing check_consistency/map_dependencies when a caller leaves these unset, so the
    ~60 existing StageFns(...) fixtures across this test suite that predate S3 and never
    exercise the two-phase document flow do not need updating. orchestrator/cli.py's
    _build_stage_fns (the real, production wiring) always sets both explicitly, built
    from the refined stages' own resolved config -- so a real run's refined-phase model/
    prompt is never silently substituted by its phase-1 sibling's, only test fixtures
    that don't care are allowed that shortcut.

    refine_questioner/refine_rewriter were one field (refine) until 2026-08-08: two
    calls with different inputs/outputs (Requirement, QualityReport -> RefinerTurn;
    requirement + RefinerAnswer[] -> RefinedRequirement) shared one callable, one
    PipelineStage identity, and one model config -- neither could be configured,
    measured, or retried independently. See design/DESIGN_NOTES.md, "Refiner split
    into REFINER_QUESTIONER / REFINER_REWRITER".

    Both gained a `revision_number: int` parameter (2026-08-09, stages.py real-prompt
    phase): neither stage's *given* args (Requirement/QualityReport for the questioner,
    Requirement/RefinerAnswer[] for the rewriter) contain the round number their own
    output schema requires (RefinerTurn.revision_number, RefinedRequirement.
    revision_number, both checked against RefinementRound's round counter). Without it,
    no real implementation of either Protocol could know what round it's answering for
    -- a demonstrated blocker, not a style choice. See design/DESIGN_NOTES.md, "Real
    stage functions -- cross-stage validation".

    check_quality/select_strategy/generate_tests gained filtered document context
    (2026-08-08, see
    docs/superpowers/specs/2026-08-08-document-context-wiring-design.md):
    check_quality's args are (Requirement, Classification,
    Optional[list[ConsistencyConflict]], Optional[list[DependencyLink]],
    suppressed_issue_ids); select_strategy's and generate_tests' each gain one trailing
    Optional[list[DependencyLink]] argument. None means the document-level stage that
    would have produced it failed (no context available); [] means it ran and found
    nothing naming this requirement -- collapsing the two would make a DEGRADED run's
    output indistinguishable from a clean one. See this module's own docstring for why
    the Protocol types below cannot enforce that distinction by themselves.
    """
    check_consistency: CheckConsistencyFn
    map_dependencies: MapDependenciesFn
    classify: ClassifyFn
    check_quality: CheckQualityFn
    refine_questioner: RefineQuestionerFn
    refine_rewriter: RefineRewriterFn
    select_strategy: SelectStrategyFn
    generate_tests: GenerateTestsFn
    check_consistency_refined: Optional[CheckConsistencyFn] = None
    map_dependencies_refined: Optional[MapDependenciesFn] = None


@dataclass(frozen=True)
class HumanFns:
    """The pipeline's two human-interaction points, as parameters. Separate from
    StageFns because the source is categorically different (a person or a web request,
    not an LLM call) -- RefinerTurn/RefinerAnswer were split in the schema specifically
    so this contract works whether the caller is a CLI loop, a notebook cell, or a
    FastAPI backend (see DESIGN_NOTES.md); a blocking input() inside the orchestrator
    would discard that."""
    answer_questions: AnswerQuestionsFn
    decide_at_cap: DecideAtCapFn
