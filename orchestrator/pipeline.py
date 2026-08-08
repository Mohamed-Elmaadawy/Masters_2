"""Orchestrator control flow: stage sequencing, resume, retry, revision cap.

Every LLM call and human-interaction point is a parameter (StageFns, HumanFns), not a
hardcoded call -- see docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md.
orchestrator/test_harness.py wires in fixtures; orchestrator/stages.py (next phase)
wires in real ones. No control-flow logic is built twice.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from pydantic import BaseModel, ValidationError

from design.schemas import (
    Classification, ConsistencyReport, DependencyReport, DocumentOutcome,
    DocumentRunRecord, DocumentStage, DocumentStageError, DocumentTokenUsage,
    FailureKind, Issue, PipelineStage, QualityReport, RefinedRequirement,
    RefinementRound, RefinerAnswer, RefinerTurn, Requirement, RequirementRunRecord,
    RequirementSet, RunMetadata, RunOutcome, StageConfig, StageError, TestPlan,
    TestStrategy, TokenUsage,
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


def _all_issue_ids(rounds: list[RefinementRound]) -> set[str]:
    """Every issue id that has ever appeared in this record, across every round so far.
    Used to keep a freshly-minted id (see _reconcile_issue_ids) from colliding with
    anything already on the record, not just the immediately preceding round."""
    return {issue.id for rnd in rounds for issue in rnd.quality_report.issues}


def _reconcile_issue_ids(
    new_issues: list[Issue],
    previous_round: Optional[RefinementRound],
    used_ids: set[str],
    req_id: str,
) -> list[Issue]:
    """Matches this round's issues against the previous round's on (category, span) and
    reuses the id when it's the same defect -- the orchestrator's job per contract item
    4, not the LLM's: each round's QualityReport is a fresh call minting its own ids.

    Anything that does NOT match the previous round is a genuinely new defect (round 2
    onward only -- round 1 has no previous round to match against in the first place).
    It must NOT keep the LLM's raw id: the checker renumbers from 1 every round, so an
    unmatched round-2+ issue's raw id reliably collides with an id already used earlier
    in this record -- sometimes the very id another issue in the same round just got
    reconciled to. Contract item 4's corollary is explicit about who owns this: "the
    orchestrator, not the LLM, should assign Issue.id." So a genuinely new issue gets a
    fresh id here, guaranteed not to collide with `used_ids` (every id used anywhere
    earlier in the record) or with anything already produced in this same call.

    Round 1's ids are left as the LLM gave them: there is nothing yet to reconcile
    against, and a collision *within* one round's own output is already caught by
    QualityReport's own uniqueness check on construction -- a second check here could
    never fire on its own (CLAUDE.md: don't write a check that can't fire).
    """
    if previous_round is None:
        return new_issues
    available = {(i.category, i.span): i.id for i in previous_round.quality_report.issues}
    # Two SEPARATE sets, not one -- a reused id is already in `used_ids` (it came from
    # an earlier round), so checking a candidate reuse against `used_ids` would always
    # find it "taken" and never reuse anything. `claimed_this_round` only tracks a
    # previous-round id being claimed a second time in THIS round's batch (two new
    # issues both matching the same old defect -- only one of them can actually be it).
    # `minted_so_far` starts from the full history and grows as fresh ids are minted, so
    # a newly-minted id can never collide with anything used before or within this call.
    claimed_this_round: set[str] = set()
    minted_so_far: set[str] = set(used_ids)
    reconciled = []
    next_n = 1
    for issue in new_issues:
        reused_id = available.get((issue.category, issue.span))
        if reused_id is not None and reused_id not in claimed_this_round:
            claimed_this_round.add(reused_id)
            reconciled.append(issue.model_copy(update={"id": reused_id}))
            continue
        while f"{req_id}-ISSUE-{next_n}" in minted_so_far:
            next_n += 1
        minted_id = f"{req_id}-ISSUE-{next_n}"
        minted_so_far.add(minted_id)
        reconciled.append(issue.model_copy(update={"id": minted_id}))
    return reconciled


def _confirmed_issue_ids(rounds: list[RefinementRound]) -> set[str]:
    """Every issue id the human has ever confirmed resolved (user_confirms_resolved),
    recomputed from the rounds already on the record -- same shape as schemas.py's own
    _issue_identity_is_stable validator, so resuming mid-refinement doesn't need to
    track this separately from what's already persisted."""
    confirmed: set[str] = set()
    for rnd in rounds:
        if rnd.turn is None:
            continue
        issue_of = {q.id: q.issue_id for q in rnd.turn.questions}
        for ans in rnd.answers:
            if ans.user_confirms_resolved:
                confirmed.add(issue_of[ans.question_id])
    return confirmed


def _run_refine_loop(
    record: RequirementRunRecord,
    stage_fns: StageFns,
    human_fns: HumanFns,
    throttle: Throttle,
    max_revisions: int,
    stage_configs: dict,
    max_attempts: int,
    backoff_seconds: Callable[[int], float],
) -> tuple[RequirementRunRecord, Optional[StageError]]:
    """Runs quality-check/refine rounds until one passes, the cap is hit, or a stage
    call fails outright. Returns the updated record and, on failure, the StageError to
    append -- the caller sets outcome=ERROR, since only it knows the full error list.
    """
    req = record.requirement
    rounds = list(record.rounds)

    # Resuming mid-round: the last round already has a quality_report but no rewrite
    # yet (REFINER position) -- pick up from there instead of starting a new round.
    #
    # This is ALSO where resuming an already-capped record (n == max_revisions,
    # last call chose CAP_GENERATED, a later stage failed) lands, since a capped round
    # looks structurally identical (failed, no rewrite) -- resume_at cannot tell the two
    # apart, as it never sees max_revisions. No separate branch is needed for that case,
    # though: `n` below comes from `pending_round.revision_number`, which is already
    # >= max_revisions for an already-capped round, so the `n >= max_revisions` check a
    # few lines down fires immediately and re-appends the round unchanged -- it never
    # reaches the turn/rewrite calls. Confirmed by mutation test: an earlier version of
    # this fix added an explicit up-front short-circuit for that case; removing it
    # again left every test green, proving it was dead code (CLAUDE.md: don't write a
    # check that can't fire).
    pending_round = None
    if rounds and not rounds[-1].quality_report.passed and rounds[-1].rewrite is None:
        pending_round = rounds[-1]
        rounds = rounds[:-1]

    while True:
        if pending_round is not None:
            n = pending_round.revision_number
            text_checked = pending_round.text_checked
            quality_report = pending_round.quality_report
            suppressed_ids = pending_round.suppressed_issue_ids
            turn = pending_round.turn
            answers = pending_round.answers
        else:
            n = len(rounds) + 1
            text_checked = rounds[-1].rewrite.refined_text if rounds else req.text
            suppressed_ids = sorted(_confirmed_issue_ids(rounds))
            current = Requirement(id=req.id, text=text_checked, source_doc_id=req.source_doc_id)
            usage = list(record.usage)
            try:
                raw_report = call_stage(
                    stage_fns.check_quality, (current, record.classification, suppressed_ids),
                    QualityReport, PipelineStage.QUALITY_CHECKER,
                    stage_configs[PipelineStage.QUALITY_CHECKER.value].model, throttle, usage,
                    max_attempts, backoff_seconds)
            except StageFailed as f:
                record = record.model_copy(update={"rounds": rounds, "usage": usage})
                return record, StageError(stage=PipelineStage.QUALITY_CHECKER, kind=f.kind,
                                          message=f.message, retry_count=f.retry_count)
            record = record.model_copy(update={"usage": usage})
            reconciled = _reconcile_issue_ids(
                raw_report.issues, rounds[-1] if rounds else None, _all_issue_ids(rounds), req.id)
            # Enforce suppression regardless of whether the checker honored the
            # suppressed_ids it was just called with: a checker that re-flags a
            # suppressed defect under a fresh id gets reconciled back to that id above,
            # then dropped here. Without this, a re-flagged suppressed issue would fail
            # RefinementRound's "suppresses X but quality_report raises it anyway" check
            # with an uncaught ValidationError -- and VAGUE_PRONOUN in particular is
            # documented as expected to be noisy (Known Limitation 4), so this is a
            # normal-path risk on free-tier models, not a corner case. `passed` is
            # recomputed from what's left, not taken from the raw report, since
            # suppression can turn a "failed" raw report into a passing round.
            remaining = [i for i in reconciled if i.id not in set(suppressed_ids)]
            quality_report = QualityReport(requirement_id=req.id, passed=(len(remaining) == 0),
                                           issues=remaining)
            turn, answers = None, []

        if quality_report.passed:
            rounds.append(RefinementRound(revision_number=n, text_checked=text_checked,
                                          quality_report=quality_report,
                                          suppressed_issue_ids=suppressed_ids))
            return record.model_copy(update={"rounds": rounds}), None

        if n >= max_revisions:
            # Cap reached this round: record what we have (turn/answers if we got that
            # far while resuming, otherwise none yet) and stop -- the caller decides.
            rounds.append(RefinementRound(revision_number=n, text_checked=text_checked,
                                          quality_report=quality_report, turn=turn,
                                          answers=answers, suppressed_issue_ids=suppressed_ids))
            return record.model_copy(update={"rounds": rounds}), None

        current = Requirement(id=req.id, text=text_checked, source_doc_id=req.source_doc_id)
        usage = list(record.usage)
        if turn is None:
            try:
                turn = call_stage(
                    stage_fns.refine, (current, quality_report), RefinerTurn,
                    PipelineStage.REFINER, stage_configs[PipelineStage.REFINER.value].model,
                    throttle, usage, max_attempts, backoff_seconds)
            except StageFailed as f:
                record = record.model_copy(update={"usage": usage})
                rounds.append(RefinementRound(revision_number=n, text_checked=text_checked,
                                              quality_report=quality_report,
                                              suppressed_issue_ids=suppressed_ids))
                return record.model_copy(update={"rounds": rounds}), StageError(
                    stage=PipelineStage.REFINER, kind=f.kind, message=f.message,
                    retry_count=f.retry_count)
            record = record.model_copy(update={"usage": usage})
            answers = human_fns.answer_questions(turn)

        usage = list(record.usage)
        try:
            rewrite = call_stage(
                stage_fns.refine, (current, answers), RefinedRequirement,
                PipelineStage.REFINER, stage_configs[PipelineStage.REFINER.value].model,
                throttle, usage, max_attempts, backoff_seconds)
        except StageFailed as f:
            record = record.model_copy(update={"usage": usage})
            rounds.append(RefinementRound(revision_number=n, text_checked=text_checked,
                                          quality_report=quality_report, turn=turn,
                                          answers=answers, suppressed_issue_ids=suppressed_ids))
            return record.model_copy(update={"rounds": rounds}), StageError(
                stage=PipelineStage.REFINER, kind=f.kind, message=f.message,
                retry_count=f.retry_count)
        record = record.model_copy(update={"usage": usage})

        rounds.append(RefinementRound(revision_number=n, text_checked=text_checked,
                                      quality_report=quality_report, turn=turn,
                                      answers=answers, rewrite=rewrite,
                                      suppressed_issue_ids=suppressed_ids))
        pending_round = None


def run_requirement(
    record: RequirementRunRecord,
    requirement_set: RequirementSet,
    consistency_report: Optional[ConsistencyReport],
    dependency_report: Optional[DependencyReport],
    stage_fns: StageFns,
    human_fns: HumanFns,
    throttle: Throttle,
    max_revisions: int,
    stage_configs: dict,
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
) -> RequirementRunRecord:
    """The full per-requirement pipeline: classifier, quality-check/refine loop
    (delegated to _run_refine_loop), the revision cap, strategy selector, test
    generator. Resumable: resume_at(record) says where to pick up, and every stage
    already done is skipped.

    consistency_report/dependency_report are accepted (Task 11 threads them through
    from run_document_stages) but not yet consumed by any per-requirement stage call --
    no stage_fns signature in this task's scenarios takes them. Wiring them into the
    quality checker / strategy selector is future work, not silently invented here.
    """
    if max_revisions < 2:
        # A cap can only be reached by exhausting revisions -- which means at least one
        # round already tried to fix the text and failed again. RunOutcome.CAP_GENERATED/
        # CAP_STOPPED's own schema rule requires evidence of that (at least one round
        # with a rewrite). max_revisions=1 caps on round 1 itself, before any rewrite
        # could exist, so it can never produce a record the schema will accept --
        # rejected here, at the only point that can explain why, rather than as a
        # ValidationError deep inside _run_refine_loop.
        raise ValueError(
            f"max_revisions must be >= 2 (got {max_revisions}): a revision cap can only "
            "be reached after at least one refinement attempt, and CAP_GENERATED/"
            "CAP_STOPPED both require a round with a rewrite to exist"
        )

    stage = resume_at(record)
    if stage is None:
        return record

    req = record.requirement

    if stage is PipelineStage.CLASSIFIER:
        usage = list(record.usage)
        try:
            classification = call_stage(
                stage_fns.classify, (req, requirement_set), Classification,
                PipelineStage.CLASSIFIER, stage_configs[PipelineStage.CLASSIFIER.value].model,
                throttle, usage, max_attempts, backoff_seconds)
        except StageFailed as f:
            errors = list(record.errors) + [StageError(
                stage=PipelineStage.CLASSIFIER, kind=f.kind, message=f.message,
                retry_count=f.retry_count)]
            return RequirementRunRecord.model_validate({
                **record.model_dump(mode="json"), "outcome": "error",
                "errors": [e.model_dump(mode="json") for e in errors],
                "usage": [u.model_dump(mode="json") for u in usage]})
        record = record.model_copy(update={"classification": classification, "usage": usage})

    # Only CLASSIFIER/QUALITY_CHECKER/REFINER positions need the refine loop at all --
    # STRATEGY_SELECTOR and TEST_GENERATOR mean the loop already finished (the last
    # round passed, or the cap already fired on an earlier call to this function) and
    # calling it again would try to start a phantom next round: the last round has no
    # rewrite in that case (a passed round never gets one -- RefinementRound rejects it),
    # so `rounds[-1].rewrite.refined_text` would raise AttributeError. This was a real
    # bug in the initial draft -- caught because it is exactly the resume path Task 11
    # depends on, not because any of the four scenario tests below exercise it.
    if stage in (PipelineStage.CLASSIFIER, PipelineStage.QUALITY_CHECKER, PipelineStage.REFINER):
        record, refine_error = _run_refine_loop(
            record, stage_fns, human_fns, throttle, max_revisions, stage_configs,
            max_attempts, backoff_seconds)
        if refine_error is not None:
            errors = list(record.errors) + [refine_error]
            return RequirementRunRecord.model_validate(
                {**record.model_dump(mode="json"), "outcome": "error",
                 "errors": [e.model_dump(mode="json") for e in errors]})

    last_round = record.rounds[-1]
    if not last_round.quality_report.passed:
        # The cap fired: ask the human whether to generate anyway or stop.
        outcome, cap_reason = human_fns.decide_at_cap(record)
        if outcome not in (RunOutcome.CAP_GENERATED, RunOutcome.CAP_STOPPED):
            raise ValueError(
                f"decide_at_cap returned {outcome!r}, must be CAP_GENERATED or CAP_STOPPED")
        if outcome is RunOutcome.CAP_STOPPED:
            # This decision can be re-asked on a resumed record (e.g. an earlier call
            # chose CAP_GENERATED, then the Strategy Selector or Test Generator failed,
            # and the human now says stop instead of retrying). CAP_STOPPED's own schema
            # rule forbids test_strategy/test_plan and forbids errors naming those two
            # stages -- "the human stopped before stage 3" -- so a stop decision made
            # AFTER stage 3/4 already ran (or failed) must retroactively discard that
            # work, not just relabel the outcome. Stripping is a safe no-op on a record
            # that never got that far in the first place.
            surviving_errors = [e for e in record.errors if e.stage not in (
                PipelineStage.STRATEGY_SELECTOR, PipelineStage.TEST_GENERATOR)]
            return RequirementRunRecord.model_validate(
                {**record.model_dump(mode="json"), "outcome": outcome.value,
                 "cap_reason": cap_reason, "test_strategy": None, "test_plan": None,
                 "errors": [e.model_dump(mode="json") for e in surviving_errors]})
        record = record.model_copy(update={"cap_reason": cap_reason})
        final_outcome = RunOutcome.CAP_GENERATED
    else:
        final_outcome = RunOutcome.COMPLETED

    # Contract item 2 / gap 1: stages 3/4 take a plain Requirement whose text is
    # whichever text stages 1-2c actually settled on. record.final_text is safe to read
    # here specifically -- not in general (see design/ORCHESTRATOR_CONTRACT.md item 2)
    # -- because the refine loop above has just finished (passed or capped), so `rounds`
    # is guaranteed non-empty and its last entry is exactly what was checked/rewritten.
    current = Requirement(id=req.id, text=record.final_text, source_doc_id=req.source_doc_id)

    # Contract item 6: "nothing else is redone." resume_at can send us here with
    # test_strategy already set (a resume where only the Test Generator failed last
    # time) -- calling the Strategy Selector again would waste an API call and could
    # legitimately return a DIFFERENT strategy for the same requirement, making the
    # stored result nondeterministic across resumes.
    if record.test_strategy is not None:
        strategy = record.test_strategy
    else:
        usage = list(record.usage)
        try:
            strategy = call_stage(
                stage_fns.select_strategy, (current, record.classification), TestStrategy,
                PipelineStage.STRATEGY_SELECTOR,
                stage_configs[PipelineStage.STRATEGY_SELECTOR.value].model, throttle, usage,
                max_attempts, backoff_seconds)
        except StageFailed as f:
            errors = list(record.errors) + [StageError(
                stage=PipelineStage.STRATEGY_SELECTOR, kind=f.kind, message=f.message,
                retry_count=f.retry_count)]
            return RequirementRunRecord.model_validate(
                {**record.model_dump(mode="json"), "outcome": "error",
                 "errors": [e.model_dump(mode="json") for e in errors],
                 "usage": [u.model_dump(mode="json") for u in usage]})
        record = record.model_copy(update={"test_strategy": strategy, "usage": usage})

    usage = list(record.usage)
    try:
        plan = call_stage(
            stage_fns.generate_tests, (current, strategy), TestPlan,
            PipelineStage.TEST_GENERATOR, stage_configs[PipelineStage.TEST_GENERATOR.value].model,
            throttle, usage, max_attempts, backoff_seconds)
    except StageFailed as f:
        errors = list(record.errors) + [StageError(
            stage=PipelineStage.TEST_GENERATOR, kind=f.kind, message=f.message,
            retry_count=f.retry_count)]
        return RequirementRunRecord.model_validate(
            {**record.model_dump(mode="json"), "outcome": "error",
             "errors": [e.model_dump(mode="json") for e in errors],
             "usage": [u.model_dump(mode="json") for u in usage]})
    record = record.model_copy(update={"test_plan": plan, "usage": usage})

    return RequirementRunRecord.model_validate(
        {**record.model_dump(mode="json"), "outcome": final_outcome.value})


def run_document(
    requirement_set: RequirementSet,
    metadata: RunMetadata,
    stage_fns: StageFns,
    human_fns: HumanFns,
    throttle: Throttle,
    max_revisions: int,
    run_dir: Optional[Path] = None,
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
) -> DocumentRunRecord:
    """Runs the whole pipeline for one document: consistency/dependency checks (D1=b:
    continue DEGRADED if either fails independently), then every requirement in order.
    Writes to run_dir incrementally if given (D2b) -- document.json first, then one
    requirement file at a time, so an interruption leaves a resumable partial run."""
    consistency_report, dependency_report, doc_errors, doc_usage = run_document_stages(
        requirement_set, metadata.stages, stage_fns, throttle, max_attempts, backoff_seconds)

    doc_outcome = (DocumentOutcome.COMPLETED
                  if consistency_report is not None and dependency_report is not None
                  else DocumentOutcome.DEGRADED)
    record = DocumentRunRecord(
        requirement_set=requirement_set, metadata=metadata, outcome=doc_outcome,
        errors=doc_errors, consistency_report=consistency_report,
        dependency_report=dependency_report, usage=doc_usage)
    if run_dir is not None:
        write_document_run(run_dir, record)

    requirement_records = []
    for req in requirement_set.requirements:
        req_record = run_requirement(
            RequirementRunRecord(requirement=req, run_id=metadata.run_id), requirement_set,
            consistency_report, dependency_report, stage_fns, human_fns, throttle,
            max_revisions, metadata.stages, max_attempts, backoff_seconds)
        requirement_records.append(req_record)
        if run_dir is not None:
            write_requirement_run(run_dir, req_record)

    return record.model_copy(update={"requirement_records": requirement_records})


def retry_document_stage(
    run_dir: Path,
    stage: DocumentStage,
    stage_fns: StageFns,
    throttle: Throttle,
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
) -> DocumentRunRecord:
    """Retries ONE failed document-level stage within the same run (contract item 6,
    'Retrying a failed document-level stage') rather than starting a new run, which
    would orphan every already-completed requirement record. errors is a LOG of failed
    attempts, not current state, so the original failure stays even after a successful
    retry; a second failure of the same stage bumps retry_count on the existing entry
    instead of appending a duplicate (document-level stages run once per run)."""
    record = read_document_run(run_dir)
    stage_fn = {DocumentStage.CONSISTENCY_CHECKER: stage_fns.check_consistency,
               DocumentStage.DEPENDENCY_MAPPER: stage_fns.map_dependencies}[stage]
    model_cls = {DocumentStage.CONSISTENCY_CHECKER: ConsistencyReport,
                DocumentStage.DEPENDENCY_MAPPER: DependencyReport}[stage]
    field_name = {DocumentStage.CONSISTENCY_CHECKER: "consistency_report",
                 DocumentStage.DEPENDENCY_MAPPER: "dependency_report"}[stage]

    usage: list[DocumentTokenUsage] = []
    try:
        report = call_document_stage(
            stage_fn, (record.requirement_set,), model_cls, stage,
            record.metadata.stages[stage.value].model, throttle, usage,
            max_attempts, backoff_seconds)
    except StageFailed as f:
        existing = next((e for e in record.errors if e.stage is stage), None)
        errors = [e for e in record.errors if e.stage is not stage]
        if existing is not None:
            errors.append(existing.model_copy(
                update={"retry_count": existing.retry_count + f.retry_count + 1}))
        else:
            errors.append(DocumentStageError(stage=stage, kind=f.kind, message=f.message,
                                             retry_count=f.retry_count))
        record = record.model_copy(update={"errors": errors, "usage": record.usage + usage})
    else:
        record = record.model_copy(update={field_name: report, "usage": record.usage + usage})

    both_present = record.consistency_report is not None and record.dependency_report is not None
    record = record.model_copy(update={
        "outcome": DocumentOutcome.COMPLETED if both_present else DocumentOutcome.DEGRADED})
    record = DocumentRunRecord.model_validate(record.model_dump(mode="json"))  # re-validate before persisting
    write_document_run(run_dir, record)
    return record


def resume_document(
    run_dir: Path,
    stage_fns: StageFns,
    human_fns: HumanFns,
    throttle: Throttle,
    max_revisions: int,
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
) -> DocumentRunRecord:
    """A resume pass: read the document from disk, process everything in
    pending_requirement_ids (no record at all, or IN_PROGRESS/ERROR), starting each at
    its derived resume_at position. A requirement that never had a file gets a fresh
    IN_PROGRESS record; one that errored gets its existing record continued in place."""
    record = read_document_run(run_dir)
    pending = set(record.pending_requirement_ids)
    by_id = {r.requirement.id: r for r in record.requirement_records}

    updated_records = []
    for req in record.requirement_set.requirements:
        if req.id not in pending:
            updated_records.append(by_id[req.id])
            continue
        base = by_id.get(req.id) or RequirementRunRecord(requirement=req, run_id=record.metadata.run_id)
        updated = run_requirement(
            base, record.requirement_set, record.consistency_report, record.dependency_report,
            stage_fns, human_fns, throttle, max_revisions, record.metadata.stages,
            max_attempts, backoff_seconds)
        updated_records.append(updated)
        write_requirement_run(run_dir, updated)

    return record.model_copy(update={"requirement_records": updated_records})


def write_document_run(run_dir: Path, record: DocumentRunRecord) -> None:
    """Writes document.json with an EMPTY requirement_records list (decision D2b) --
    each requirement is its own file, written separately by write_requirement_run.
    Re-validates before persisting (contract item 10): mutation after construction
    bypasses Pydantic's checks, so this re-runs them right before the bytes hit disk."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "requirements").mkdir(exist_ok=True)
    on_disk = DocumentRunRecord.model_validate(
        {**record.model_dump(mode="json"), "requirement_records": []})
    (run_dir / "document.json").write_text(on_disk.model_dump_json(indent=2))


def write_requirement_run(run_dir: Path, record: RequirementRunRecord) -> None:
    (run_dir / "requirements").mkdir(parents=True, exist_ok=True)
    validated = RequirementRunRecord.model_validate(record.model_dump(mode="json"))
    (run_dir / "requirements" / f"{record.requirement.id}.json").write_text(
        validated.model_dump_json(indent=2))


def read_document_run(run_dir: Path) -> DocumentRunRecord:
    """Reassembles the document from document.json (empty requirement_records) plus
    every requirements/*.json file -- the inverse of write_document_run/
    write_requirement_run under D2b."""
    doc_data = json.loads((run_dir / "document.json").read_text())
    req_dir = run_dir / "requirements"
    records = [RequirementRunRecord.model_validate_json(path.read_text())
               for path in sorted(req_dir.glob("*.json"))]
    return DocumentRunRecord.model_validate(
        {**doc_data, "requirement_records": [r.model_dump(mode="json") for r in records]})
