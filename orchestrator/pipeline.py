"""Orchestrator control flow: stage sequencing, resume, retry, revision cap.

Every LLM call and human-interaction point is a parameter (StageFns, HumanFns), not a
hardcoded call -- see docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md.
orchestrator/test_harness.py wires in fixtures; orchestrator/stages.py (next phase)
wires in real ones. No control-flow logic is built twice.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, ValidationError

from design.schemas import (
    AttemptResult, Classification, ConsistencyConflict, ConsistencyReport, DependencyLink,
    DependencyReport, DocumentOutcome, DocumentRunRecord, DocumentStage,
    DocumentStageAttempt, DocumentStageError, FailureKind, Issue, PipelineStage,
    QualityReport, RefinedRequirement, RefinementRound, RefinerAnswer, RefinerTurn,
    Requirement, RequirementRunRecord, RequirementSet, RunMetadata, RunOutcome,
    StageAttempt, StageConfig, StageError, TestPlan, TestStrategy,
)

# StageCallResult/StageCallFailed/StageCallFatal/StageFns/HumanFns moved to
# orchestrator/stage_fns.py (2026-08-09) so orchestrator/providers/ and the future
# orchestrator/stages.py can import them without pulling in this module's control-flow
# code. Re-exported here so existing `from orchestrator.pipeline import StageFns,
# HumanFns, StageCallResult, StageCallFailed` call sites (e.g.
# orchestrator/test_harness.py) keep working unchanged.
from orchestrator.stage_fns import (
    HumanFns, StageCallFailed, StageCallFatal, StageCallPartial, StageCallResult, StageFns,
)


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


ExtraCheck = Callable[[BaseModel], Optional[str]]


def _no_extra_check(parsed: BaseModel) -> Optional[str]:
    """Default extra_check: no cross-stage invariant to verify beyond requirement_id/
    doc_id, which call_stage/call_document_stage already check unconditionally."""
    return None


def call_stage(
    stage_fn: Callable[..., StageCallResult],
    args: tuple,
    model_cls: type[BaseModel],
    stage: PipelineStage,
    invocation_id: str,
    model_name: str,
    throttle: Throttle,
    attempt_sink: list[StageAttempt],
    req_id: str,
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
    extra_check: ExtraCheck = _no_extra_check,
) -> BaseModel:
    """Call one stage, validate its output, check it answers about the right
    requirement, retry on failure, record one StageAttempt per try -- success or
    failure -- so a retry that ultimately succeeds still leaves a full record of what
    came before it (see
    docs/superpowers/specs/2026-08-08-per-attempt-observability-design.md).

    invocation_id is required, no default -- same "a forgotten wire-up must fail loud"
    reasoning as req_id below: it groups every attempt made by THIS call (including its
    internal retries) into one invocation. The caller mints a fresh one per logical call
    site (e.g. once per Quality Checker round), so two rounds of the same stage produce
    two distinct invocation ids while one round's backoff retries share one.

    Only the stage_fn call itself is wrapped in `except Exception` -- not the
    validation that follows, and not any surrounding orchestrator code. A bug in the
    orchestrator's own loop must still crash instead of being filed as kind=OTHER,
    which would otherwise be an enum member with no real producer (CLAUDE.md: "don't
    write a check that can't fire"). See design/ORCHESTRATOR_CONTRACT.md item 7 and
    the FailureKind docstring in design/schemas.py.

    A stage_fn raising StageCallFatal (2026-08-09) short-circuits the retry loop
    immediately -- one StageAttempt recorded, no backoff sleep, kind=FailureKind.FATAL,
    retry_count=0 -- rather than spending the remaining attempt budget on a request that
    cannot succeed (bad credentials, an unsupported output mode). This is the one
    deliberate exception to "every failure gets max_attempts tries"; see
    orchestrator/stage_fns.py's StageCallFatal docstring and
    design/ORCHESTRATOR_CONTRACT.md for the narrow scope this is reserved for.

    req_id is required, not optional: every model_cls this is called with (all six
    per-requirement stage outputs) carries a requirement_id field, and a defaulted
    (e.g. None) parameter here would silently skip the check at exactly the call sites
    someone forgot to wire it up -- the same silent-gap shape this check exists to
    close. A model answering about a different requirement is treated as a validation
    failure, not a separate failure mode: before this, the six per-requirement stages
    disagreed on what to do with a requirement_id mismatch three different ways (see
    docs/superpowers/plans/2026-08-08-orchestrator-harness-fixes-and-changes.md
    section 5) -- check_quality silently relabelled it, classify/select_strategy/a
    consistently-wrong generate_tests payload crashed with an uncaught ValidationError
    only once the record was finally re-validated (after later stages had already run
    and been paid for), and refine's turn/rewrite crashed immediately at
    RefinementRound construction. Folding the check in here, before any of those
    paths are reached, makes it one outcome everywhere: FailureKind.VALIDATION,
    retried per the normal policy, usage recorded (the call succeeded; the answer was
    just about the wrong requirement) -- countable, the same way any other
    schema-invalid output is countable (contract item 14).

    extra_check (2026-08-09, stages.py real-prompt phase): an optional callable, run
    immediately after the requirement_id check passes, before returning. Generalizes
    the exact mechanism above -- "check immediately after model_validate succeeds,
    before returning; a mismatch is kind=VALIDATION, retried per the normal policy,
    usage recorded" -- to every OTHER cross-stage agreement a stage's output must
    satisfy that model_cls's own validators cannot check in isolation (they compare
    against an earlier stage's output, or against a full id-set no single object
    holds). Returns an error message string to fail this attempt as a normal
    VALIDATION_FAILURE, or None to let it succeed. Default is a no-op, so every
    existing call site/test that doesn't pass one keeps its exact current behavior.
    Never mutates parsed -- a stage output that disagrees with an earlier stage either
    retries or exhausts into a StageError; it is never silently corrected, which would
    destroy the exact "how often does this model produce internally-inconsistent
    output" signal this project treats as a thesis-relevant measurement (see
    design/ORCHESTRATOR_CONTRACT.md item 15's identical reasoning for requirement_id).
    See design/DESIGN_NOTES.md, "Real stage functions -- cross-stage validation", for
    the full audit of which fields needed this and why the others didn't.
    """
    if max_attempts < 1:
        # range(max_attempts) below would be empty, the for loop body would never run,
        # and `attempt` -- read after the loop in every exit path -- would be unbound,
        # an immediate NameError rather than a StageFailed a caller could catch. Fail
        # loud, at the boundary, with a message that says why, instead.
        raise ValueError(f"call_stage requires max_attempts >= 1 (got {max_attempts})")

    last_kind: FailureKind = FailureKind.OTHER
    last_message = ""

    for attempt in range(max_attempts):
        attempt_number = attempt + 1
        throttle.wait_for_slot(model_name)
        try:
            result = stage_fn(*args)
        except StageCallFatal as e:
            last_kind, last_message = FailureKind.FATAL, str(e)
            attempt_sink.append(StageAttempt(
                stage=stage, invocation_id=invocation_id, attempt_number=attempt_number,
                result=AttemptResult.FATAL_FAILURE, error_message=last_message))
            break
        except StageCallPartial as e:
            # Inference genuinely happened and tokens were genuinely spent -- unlike
            # every other exception branch here, this one has token counts to record.
            # Recorded as OTHER_FAILURE (not a new kind/result -- see StageCallPartial's
            # own docstring for why none was needed), which already permits, without
            # requiring, token counts. Retried normally: a malformed response on this
            # attempt doesn't mean the next one will be.
            last_kind, last_message = FailureKind.OTHER, str(e)
            attempt_sink.append(StageAttempt(
                stage=stage, invocation_id=invocation_id, attempt_number=attempt_number,
                result=AttemptResult.OTHER_FAILURE, error_message=last_message,
                prompt_tokens=e.prompt_tokens, completion_tokens=e.completion_tokens))
        except StageCallFailed as e:
            last_kind, last_message = FailureKind.TRANSPORT, str(e)
            attempt_sink.append(StageAttempt(
                stage=stage, invocation_id=invocation_id, attempt_number=attempt_number,
                result=AttemptResult.TRANSPORT_FAILURE, error_message=last_message))
        except Exception as e:
            last_kind, last_message = FailureKind.OTHER, f"{type(e).__name__}: {e}"
            attempt_sink.append(StageAttempt(
                stage=stage, invocation_id=invocation_id, attempt_number=attempt_number,
                result=AttemptResult.OTHER_FAILURE, error_message=last_message))
        else:
            try:
                parsed = model_cls.model_validate(result.raw)
            except ValidationError as e:
                last_kind, last_message = FailureKind.VALIDATION, str(e)
                attempt_sink.append(StageAttempt(
                    stage=stage, invocation_id=invocation_id, attempt_number=attempt_number,
                    result=AttemptResult.VALIDATION_FAILURE, error_message=last_message,
                    prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens))
            else:
                if parsed.requirement_id != req_id:
                    last_kind = FailureKind.VALIDATION
                    last_message = (
                        f"{model_cls.__name__}.requirement_id is {parsed.requirement_id!r}, "
                        f"expected {req_id!r} -- the model answered about a different "
                        "requirement"
                    )
                    attempt_sink.append(StageAttempt(
                        stage=stage, invocation_id=invocation_id, attempt_number=attempt_number,
                        result=AttemptResult.VALIDATION_FAILURE, error_message=last_message,
                        prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens))
                else:
                    extra_error = extra_check(parsed)
                    if extra_error is None:
                        attempt_sink.append(StageAttempt(
                            stage=stage, invocation_id=invocation_id, attempt_number=attempt_number,
                            result=AttemptResult.SUCCESS, prompt_tokens=result.prompt_tokens,
                            completion_tokens=result.completion_tokens))
                        return parsed
                    last_kind = FailureKind.VALIDATION
                    last_message = extra_error
                    attempt_sink.append(StageAttempt(
                        stage=stage, invocation_id=invocation_id, attempt_number=attempt_number,
                        result=AttemptResult.VALIDATION_FAILURE, error_message=last_message,
                        prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens))

        if attempt < max_attempts - 1:
            throttle.sleep_fn(backoff_seconds(attempt))

    # attempt (the loop variable, still in scope after the for loop exits, whether by
    # normal exhaustion or an early `break` on StageCallFatal) equals max_attempts - 1 on
    # a normal exhaustion -- identical to the old hardcoded value -- and equals however
    # many attempts actually ran before a fatal break (0 if fatal on the very first try),
    # matching StageFailed's own "0 means failed on the first try with no retry".
    raise StageFailed(last_kind, last_message, retry_count=attempt)


def call_document_stage(
    stage_fn: Callable[..., StageCallResult],
    args: tuple,
    model_cls: type[BaseModel],
    stage: DocumentStage,
    invocation_id: str,
    model_name: str,
    throttle: Throttle,
    attempt_sink: list[DocumentStageAttempt],
    doc_id: Optional[str],
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
    extra_check: ExtraCheck = _no_extra_check,
) -> BaseModel:
    """Structurally identical to call_stage apart from the stage/attempt types -- same
    reasoning as the StageError/DocumentStageError split: a shared implementation
    parameterised by a PipelineStage | DocumentStage union would let a call meant for
    one level accidentally target the other.

    doc_id is required (no default), same reasoning as call_stage's req_id: a defaulted
    parameter would silently skip the check at exactly the call site someone forgot to
    wire it up. Unlike req_id, though, doc_id is genuinely Optional -- RequirementSet.doc_id
    and ConsistencyReport/DependencyReport.doc_id can each legitimately be None (this
    document's provenance was never recorded, or the model didn't echo it back). The
    check below only fires when BOTH sides are present and disagree, mirroring
    DocumentRunRecord._references_resolve's own doc_id check in design/schemas.py:
    silence is not the same as disagreement, and a None on either side is not a claim
    the report was produced for a different document.

    invocation_id is required, no default -- same reasoning as call_stage's.

    extra_check: same mechanism as call_stage's own extra_check (see its docstring) --
    run after the doc_id check passes, before returning; a non-None return is a normal
    VALIDATION_FAILURE. Used for e.g. checking every id a ConsistencyConflict/
    DependencyLink names actually exists in the document's RequirementSet.
    """
    if max_attempts < 1:
        raise ValueError(
            f"call_document_stage requires max_attempts >= 1 (got {max_attempts})")

    last_kind: FailureKind = FailureKind.OTHER
    last_message = ""

    for attempt in range(max_attempts):
        attempt_number = attempt + 1
        throttle.wait_for_slot(model_name)
        try:
            result = stage_fn(*args)
        except StageCallFatal as e:
            last_kind, last_message = FailureKind.FATAL, str(e)
            attempt_sink.append(DocumentStageAttempt(
                stage=stage, invocation_id=invocation_id, attempt_number=attempt_number,
                result=AttemptResult.FATAL_FAILURE, error_message=last_message))
            break
        except StageCallPartial as e:
            last_kind, last_message = FailureKind.OTHER, str(e)
            attempt_sink.append(DocumentStageAttempt(
                stage=stage, invocation_id=invocation_id, attempt_number=attempt_number,
                result=AttemptResult.OTHER_FAILURE, error_message=last_message,
                prompt_tokens=e.prompt_tokens, completion_tokens=e.completion_tokens))
        except StageCallFailed as e:
            last_kind, last_message = FailureKind.TRANSPORT, str(e)
            attempt_sink.append(DocumentStageAttempt(
                stage=stage, invocation_id=invocation_id, attempt_number=attempt_number,
                result=AttemptResult.TRANSPORT_FAILURE, error_message=last_message))
        except Exception as e:
            last_kind, last_message = FailureKind.OTHER, f"{type(e).__name__}: {e}"
            attempt_sink.append(DocumentStageAttempt(
                stage=stage, invocation_id=invocation_id, attempt_number=attempt_number,
                result=AttemptResult.OTHER_FAILURE, error_message=last_message))
        else:
            try:
                parsed = model_cls.model_validate(result.raw)
            except ValidationError as e:
                last_kind, last_message = FailureKind.VALIDATION, str(e)
                attempt_sink.append(DocumentStageAttempt(
                    stage=stage, invocation_id=invocation_id, attempt_number=attempt_number,
                    result=AttemptResult.VALIDATION_FAILURE, error_message=last_message,
                    prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens))
            else:
                mismatch = (doc_id is not None and parsed.doc_id is not None
                            and parsed.doc_id != doc_id)
                if mismatch:
                    last_kind = FailureKind.VALIDATION
                    last_message = (
                        f"{model_cls.__name__}.doc_id is {parsed.doc_id!r}, expected "
                        f"{doc_id!r} -- the model answered about a different document"
                    )
                    attempt_sink.append(DocumentStageAttempt(
                        stage=stage, invocation_id=invocation_id, attempt_number=attempt_number,
                        result=AttemptResult.VALIDATION_FAILURE, error_message=last_message,
                        prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens))
                else:
                    extra_error = extra_check(parsed)
                    if extra_error is None:
                        attempt_sink.append(DocumentStageAttempt(
                            stage=stage, invocation_id=invocation_id, attempt_number=attempt_number,
                            result=AttemptResult.SUCCESS, prompt_tokens=result.prompt_tokens,
                            completion_tokens=result.completion_tokens))
                        return parsed
                    last_kind = FailureKind.VALIDATION
                    last_message = extra_error
                    attempt_sink.append(DocumentStageAttempt(
                        stage=stage, invocation_id=invocation_id, attempt_number=attempt_number,
                        result=AttemptResult.VALIDATION_FAILURE, error_message=last_message,
                        prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens))

        if attempt < max_attempts - 1:
            throttle.sleep_fn(backoff_seconds(attempt))

    # attempt (the loop variable, still in scope after the for loop exits, whether by
    # normal exhaustion or an early `break` on StageCallFatal) equals max_attempts - 1 on
    # a normal exhaustion -- identical to the old hardcoded value -- and equals however
    # many attempts actually ran before a fatal break (0 if fatal on the very first try),
    # matching StageFailed's own "0 means failed on the first try with no retry".
    raise StageFailed(last_kind, last_message, retry_count=attempt)


def _known_requirement_ids(requirement_set: RequirementSet) -> frozenset[str]:
    return frozenset(r.id for r in requirement_set.requirements)


def _consistency_extra_check(known_ids: frozenset[str]) -> ExtraCheck:
    def check(parsed: ConsistencyReport) -> Optional[str]:
        for conflict in parsed.conflicts:
            unknown = sorted(set(conflict.requirement_ids) - known_ids)
            if unknown:
                return (f"ConsistencyConflict references unknown requirement id(s) "
                        f"{unknown} -- not in this document's requirement set")
        return None
    return check


def _dependency_extra_check(known_ids: frozenset[str]) -> ExtraCheck:
    def check(parsed: DependencyReport) -> Optional[str]:
        for dep in parsed.dependencies:
            unknown = sorted({dep.from_requirement_id, dep.to_requirement_id} - known_ids)
            if unknown:
                return (f"DependencyLink references unknown requirement id(s) {unknown} "
                        f"-- not in this document's requirement set")
        return None
    return check


def _quality_checker_extra_check(req_id: str, other_known_ids: frozenset[str]) -> ExtraCheck:
    """other_known_ids must already exclude req_id -- an Issue's related_requirement_ids
    is a claim about OTHER requirements (contract item 4/RefinementRound's own
    self-reference check), so req_id itself is never a valid entry, known-id or not."""
    def check(parsed: QualityReport) -> Optional[str]:
        for issue in parsed.issues:
            if req_id in issue.related_requirement_ids:
                return (f"Issue {issue.id!r} lists {req_id!r} as a related requirement, "
                        "but that is the requirement it is about")
            unknown = sorted(set(issue.related_requirement_ids) - other_known_ids)
            if unknown:
                return (f"Issue {issue.id!r} references unknown requirement id(s) "
                        f"{unknown} -- not in this document's requirement set")
        return None
    return check


def _refiner_questioner_extra_check(n: int, quality_report: QualityReport) -> ExtraCheck:
    issues_by_id = {i.id: i for i in quality_report.issues}

    def check(parsed: RefinerTurn) -> Optional[str]:
        if parsed.revision_number != n:
            return (f"RefinerTurn.revision_number is {parsed.revision_number}, expected "
                    f"{n} -- the model answered for the wrong round")
        seen_question_ids: set[str] = set()
        for q in parsed.questions:
            if q.id in seen_question_ids:
                return f"question id {q.id!r} appears more than once in this turn"
            seen_question_ids.add(q.id)
            issue = issues_by_id.get(q.issue_id)
            if issue is None:
                return (f"question {q.id!r} addresses issue {q.issue_id!r}, which this "
                        "round's quality_report did not raise")
            if q.issue_category is not issue.category:
                return (f"question {q.id!r} says its issue is {q.issue_category.value!r}, "
                        f"but issue {issue.id!r} is {issue.category.value!r}")
        return None
    return check


def _refiner_rewriter_extra_check(
    n: int, text_checked: str, answers: list[RefinerAnswer],
) -> ExtraCheck:
    def check(parsed: RefinedRequirement) -> Optional[str]:
        if parsed.revision_number != n:
            return (f"RefinedRequirement.revision_number is {parsed.revision_number}, "
                    f"expected {n} -- the model answered for the wrong round")
        if parsed.original_text != text_checked:
            return "RefinedRequirement.original_text is not the text that was checked this round"
        for a in parsed.answers_used:
            if a not in answers:
                return (f"RefinedRequirement used an answer to {a.question_id!r} that is "
                        "not among this round's answers")
        return None
    return check


def _strategy_selector_extra_check(classification: Classification) -> ExtraCheck:
    def check(parsed: TestStrategy) -> Optional[str]:
        if parsed.system_type is not classification.system_type:
            return (f"TestStrategy.system_type is {parsed.system_type.value!r}, but the "
                    f"Classifier said {classification.system_type.value!r}")
        return None
    return check


def test_case_id_prefix(requirement_id: str) -> str:
    """Length-prefixed test case id namespace for one requirement: 'TC-<len>-<id>-'.

    Plain 'TC-<id>-' is ambiguous when one requirement's id is a prefix of another's:
    'REQ-1' and 'REQ-1-X' would both accept a case named 'TC-REQ-1-X-5' -- a case in
    REQ-1's namespace with suffix 'X-5' is byte-identical to a case in REQ-1-X's
    namespace with suffix '5'. Announcing the id's length up front removes the
    ambiguity the same way a netstring removes it from length-prefixed byte strings:
    for two different (len, id) pairs to produce the same 'TC-<len>-<id>-' text, either
    the length digits must match (forcing len1 == len2, hence id1/id2 have equal
    length) and then the id characters must match position-for-position (forcing
    id1 == id2), or the length digits must differ, which is visible at the first
    differing character -- either way, two different ids can never produce the same
    prefix. Combined with RequirementSet's own id-uniqueness and TestPlan's own
    within-plan case-id uniqueness, this makes cross-requirement test-case id
    collisions structurally impossible, not just unlikely. See
    design/DESIGN_NOTES.md, "Real stage functions -- cross-stage validation".
    """
    return f"TC-{len(requirement_id)}-{requirement_id}-"


def _test_generator_extra_check(
    req_id: str, strategy: TestStrategy, known_ids: frozenset[str],
) -> ExtraCheck:
    allowed_techniques = set(strategy.techniques)
    prefix = test_case_id_prefix(req_id)

    def check(parsed: TestPlan) -> Optional[str]:
        for case in parsed.test_cases:
            if not case.id.startswith(prefix) or case.id == prefix:
                return (f"test case id {case.id!r} does not follow the required "
                        f"convention {prefix}<suffix>")
            if case.technique_used not in allowed_techniques:
                return (f"test case {case.id!r} uses {case.technique_used.value!r}, "
                        "which the strategy did not select "
                        f"({sorted(t.value for t in allowed_techniques)})")
            unknown = sorted(set(case.requirement_ids) - known_ids)
            if unknown:
                return (f"test case {case.id!r} references unknown requirement id(s) "
                        f"{unknown} -- not in this document's requirement set")
        return None
    return check


def run_document_stages(
    requirement_set: RequirementSet,
    stage_configs: dict[str, StageConfig],
    stage_fns: StageFns,
    throttle: Throttle,
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
) -> tuple[Optional[ConsistencyReport], Optional[DependencyReport],
           list[DocumentStageError], list[DocumentStageAttempt]]:
    """Runs the two document-level stages independently -- one failing must not stop
    the other from running (contract item 8, D1=b)."""
    errors: list[DocumentStageError] = []
    attempts: list[DocumentStageAttempt] = []
    known_ids = _known_requirement_ids(requirement_set)

    consistency_report: Optional[ConsistencyReport] = None
    consistency_invocation_id = uuid.uuid4().hex
    try:
        consistency_report = call_document_stage(
            stage_fns.check_consistency, (requirement_set,), ConsistencyReport,
            DocumentStage.CONSISTENCY_CHECKER, consistency_invocation_id,
            stage_configs[DocumentStage.CONSISTENCY_CHECKER.value].model, throttle, attempts,
            requirement_set.doc_id, max_attempts, backoff_seconds,
            extra_check=_consistency_extra_check(known_ids))
    except StageFailed as f:
        errors.append(DocumentStageError(
            stage=DocumentStage.CONSISTENCY_CHECKER, invocation_id=consistency_invocation_id,
            kind=f.kind, message=f.message, retry_count=f.retry_count))

    dependency_report: Optional[DependencyReport] = None
    dependency_invocation_id = uuid.uuid4().hex
    try:
        dependency_report = call_document_stage(
            stage_fns.map_dependencies, (requirement_set,), DependencyReport,
            DocumentStage.DEPENDENCY_MAPPER, dependency_invocation_id,
            stage_configs[DocumentStage.DEPENDENCY_MAPPER.value].model, throttle, attempts,
            requirement_set.doc_id, max_attempts, backoff_seconds,
            extra_check=_dependency_extra_check(known_ids))
    except StageFailed as f:
        errors.append(DocumentStageError(
            stage=DocumentStage.DEPENDENCY_MAPPER, invocation_id=dependency_invocation_id,
            kind=f.kind, message=f.message, retry_count=f.retry_count))

    return consistency_report, dependency_report, errors, attempts


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
        if last.rewrite is not None:
            return PipelineStage.QUALITY_CHECKER
        # No rewrite yet -- which half of the Refiner is unfinished? No turn means
        # nothing was ever asked (the questioner itself failed, or this round never
        # got that far); a turn with no rewrite means the questioner has finished and
        # only the rewrite is outstanding -- NOT that the human has answered yet.
        # answers may still be empty here (interrupted between the questioner's turn
        # and the human's answer); _run_refine_loop asks the human iff answers is
        # empty, regardless of turn.
        return PipelineStage.REFINER_QUESTIONER if last.turn is None else PipelineStage.REFINER_REWRITER
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
    relevant_conflicts: Optional[list[ConsistencyConflict]],
    relevant_dependencies: Optional[list[DependencyLink]],
    stage_fns: StageFns,
    human_fns: HumanFns,
    throttle: Throttle,
    max_revisions: int,
    stage_configs: dict,
    max_attempts: int,
    backoff_seconds: Callable[[int], float],
    *,
    known_requirement_ids: frozenset[str],
    checkpoint: Optional[Callable[[RequirementRunRecord], None]] = None,
) -> tuple[RequirementRunRecord, Optional[StageError]]:
    """Runs quality-check/refine rounds until one passes, the cap is hit, or a stage
    call fails outright. Returns the updated record and, on failure, the StageError to
    append -- the caller sets outcome=ERROR, since only it knows the full error list.

    relevant_conflicts/relevant_dependencies are the Quality Checker's filtered document
    context (see docs/superpowers/specs/2026-08-08-document-context-wiring-design.md),
    computed once by the caller and passed unchanged into every round's check_quality
    call -- the document-level analysis doesn't change between rounds, so recomputing it
    per round would be redundant, not more correct.

    checkpoint (2026-08-09), if given, is called with a snapshot of the record --
    including the just-produced RefinerTurn, before any answer exists -- immediately
    before EITHER call to human_fns.answer_questions below. human_fns.answer_questions
    is the one point in this whole function that can block on a terminal and raise
    EOFError/KeyboardInterrupt (see orchestrator/human_cli.py); nothing here catches
    that exception -- it still propagates exactly as before -- but if the caller (see
    run_document's checkpoint=lambda rec: write_requirement_run(run_dir, rec)) has
    already persisted this snapshot, a later resume_document() picks up at
    REFINER_REWRITER with the turn already on record and answers still empty, which
    is the already-existing, already-tested resume path
    (test_resume_mid_round_asks_human_when_answers_missing) -- re-asking the human
    instead of silently repeating the classifier/quality-checker/questioner calls that
    already succeeded. Without this, nothing about an in-progress requirement is ever
    written to disk until run_requirement returns, so an interruption here loses that
    requirement's progress entirely, not just the pending answer.
    """
    req = record.requirement
    rounds = list(record.rounds)

    # Resuming mid-round: the last round already has a quality_report but no rewrite
    # yet (REFINER_QUESTIONER or REFINER_REWRITER position) -- pick up from there
    # instead of starting a new round.
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
            attempts = list(record.attempts)
            # A fresh invocation_id per round: round 1 and round 2's Quality Checker
            # calls are distinct logical calls, even though both use the same stage.
            qc_invocation_id = uuid.uuid4().hex
            try:
                raw_report = call_stage(
                    stage_fns.check_quality,
                    (current, record.classification, relevant_conflicts, relevant_dependencies,
                     suppressed_ids),
                    QualityReport, PipelineStage.QUALITY_CHECKER, qc_invocation_id,
                    stage_configs[PipelineStage.QUALITY_CHECKER.value].model, throttle, attempts,
                    req.id, max_attempts, backoff_seconds,
                    extra_check=_quality_checker_extra_check(
                        req.id, known_requirement_ids - {req.id}))
            except StageFailed as f:
                record = record.model_copy(update={"rounds": rounds, "attempts": attempts})
                return record, StageError(
                    stage=PipelineStage.QUALITY_CHECKER, invocation_id=qc_invocation_id,
                    kind=f.kind, message=f.message, retry_count=f.retry_count)
            record = record.model_copy(update={"attempts": attempts})
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
            suppressed_id_set = set(suppressed_ids)
            remaining = [i for i in reconciled if i.id not in suppressed_id_set]
            # requirement_id=req.id here is not "fixing" a possibly-wrong value the way
            # it looked before call_stage checked req_id itself: raw_report.requirement_id
            # is now guaranteed equal to req.id already (a mismatch would have made
            # call_stage retry and eventually raise StageFailed, caught above), so this is
            # just restating a value already confirmed correct. `passed` is still
            # recomputed here, deliberately -- see the suppression comment above.
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
        attempts = list(record.attempts)
        if turn is None:
            questioner_invocation_id = uuid.uuid4().hex
            try:
                turn = call_stage(
                    stage_fns.refine_questioner, (current, quality_report, n), RefinerTurn,
                    PipelineStage.REFINER_QUESTIONER, questioner_invocation_id,
                    stage_configs[PipelineStage.REFINER_QUESTIONER.value].model,
                    throttle, attempts, req.id, max_attempts, backoff_seconds,
                    extra_check=_refiner_questioner_extra_check(n, quality_report))
            except StageFailed as f:
                record = record.model_copy(update={"attempts": attempts})
                rounds.append(RefinementRound(revision_number=n, text_checked=text_checked,
                                              quality_report=quality_report,
                                              suppressed_issue_ids=suppressed_ids))
                return record.model_copy(update={"rounds": rounds}), StageError(
                    stage=PipelineStage.REFINER_QUESTIONER, invocation_id=questioner_invocation_id,
                    kind=f.kind, message=f.message, retry_count=f.retry_count)
            record = record.model_copy(update={"attempts": attempts})
            if checkpoint is not None:
                checkpoint(record.model_copy(update={"rounds": rounds + [RefinementRound(
                    revision_number=n, text_checked=text_checked, quality_report=quality_report,
                    turn=turn, suppressed_issue_ids=suppressed_ids)]}))
            answers = human_fns.answer_questions(turn)
        elif not answers:
            # turn already exists (questioner finished, possibly on a prior attempt)
            # but nothing has answered it yet -- interrupted between the questioner's
            # call and the human's answer. Schema-valid (RefinementRound only rejects
            # answers non-empty with turn=None, never the reverse), and distinct from
            # "turn and answers both already exist" just below, which must NOT re-ask.
            if checkpoint is not None:
                checkpoint(record.model_copy(update={"rounds": rounds + [RefinementRound(
                    revision_number=n, text_checked=text_checked, quality_report=quality_report,
                    turn=turn, suppressed_issue_ids=suppressed_ids)]}))
            answers = human_fns.answer_questions(turn)

        # Second checkpoint (2026-08-09, post-review): the human's answer is the
        # expensive-to-redo part of this round -- re-asking costs a person's time, not
        # just an API call -- so it must reach disk before the rewriter call, not only
        # before answer_questions. KeyboardInterrupt is not scoped to terminal-input
        # calls the way EOFError effectively is: it can land during the HTTP request
        # inside call_stage(refine_rewriter, ...) below just as easily as during
        # answer_questions above. This round now has turn AND non-empty answers, still
        # no rewrite -- resume_at still resolves this to REFINER_REWRITER, and
        # _run_refine_loop's resume branch (`elif not answers`) is False this time
        # (answers is non-empty), so a resume skips straight to the rewriter call with
        # the already-collected answers -- it does NOT re-ask the human.
        if checkpoint is not None:
            checkpoint(record.model_copy(update={"rounds": rounds + [RefinementRound(
                revision_number=n, text_checked=text_checked, quality_report=quality_report,
                turn=turn, answers=answers, suppressed_issue_ids=suppressed_ids)]}))

        attempts = list(record.attempts)
        rewriter_invocation_id = uuid.uuid4().hex
        try:
            rewrite = call_stage(
                stage_fns.refine_rewriter, (current, answers, n), RefinedRequirement,
                PipelineStage.REFINER_REWRITER, rewriter_invocation_id,
                stage_configs[PipelineStage.REFINER_REWRITER.value].model,
                throttle, attempts, req.id, max_attempts, backoff_seconds,
                extra_check=_refiner_rewriter_extra_check(n, text_checked, answers))
        except StageFailed as f:
            record = record.model_copy(update={"attempts": attempts})
            rounds.append(RefinementRound(revision_number=n, text_checked=text_checked,
                                          quality_report=quality_report, turn=turn,
                                          answers=answers, suppressed_issue_ids=suppressed_ids))
            return record.model_copy(update={"rounds": rounds}), StageError(
                stage=PipelineStage.REFINER_REWRITER, invocation_id=rewriter_invocation_id,
                kind=f.kind, message=f.message, retry_count=f.retry_count)
        record = record.model_copy(update={"attempts": attempts})

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
    checkpoint: Optional[Callable[[RequirementRunRecord], None]] = None,
) -> RequirementRunRecord:
    """The full per-requirement pipeline: classifier, quality-check/refine loop
    (delegated to _run_refine_loop), the revision cap, strategy selector, test
    generator. Resumable: resume_at(record) says where to pick up, and every stage
    already done is skipped.

    consistency_report/dependency_report (Task 11 threads them through from
    run_document_stages) are filtered to this requirement's own conflicts/dependencies
    once, below, and handed to the Quality Checker, Strategy Selector, and Test
    Generator -- never the whole report (see
    docs/superpowers/specs/2026-08-08-document-context-wiring-design.md). A report of
    None (the document-level stage failed) stays None after filtering -- it is not the
    same thing as a report that ran and found nothing for this requirement ([]).

    checkpoint (2026-08-09), if given, is threaded into _run_refine_loop (see its own
    docstring) and called again here, directly, right before human_fns.decide_at_cap --
    the second and last point in this whole call graph that can block on a terminal and
    raise EOFError/KeyboardInterrupt. At that point `record` is already the complete,
    just-returned-from-_run_refine_loop record (the capped round already appended), so
    no snapshot construction is needed here the way it was inside the loop.
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
    relevant_conflicts = (
        consistency_report.conflicts_for(req.id) if consistency_report is not None else None)
    relevant_dependencies = (
        dependency_report.dependencies_for(req.id) if dependency_report is not None else None)
    known_requirement_ids = _known_requirement_ids(requirement_set)

    if stage is PipelineStage.CLASSIFIER:
        attempts = list(record.attempts)
        classifier_invocation_id = uuid.uuid4().hex
        try:
            classification = call_stage(
                stage_fns.classify, (req, requirement_set), Classification,
                PipelineStage.CLASSIFIER, classifier_invocation_id,
                stage_configs[PipelineStage.CLASSIFIER.value].model,
                throttle, attempts, req.id, max_attempts, backoff_seconds)
        except StageFailed as f:
            errors = list(record.errors) + [StageError(
                stage=PipelineStage.CLASSIFIER, invocation_id=classifier_invocation_id,
                kind=f.kind, message=f.message, retry_count=f.retry_count)]
            return RequirementRunRecord.model_validate({
                **record.model_dump(mode="json"), "outcome": "error",
                "errors": [e.model_dump(mode="json") for e in errors],
                "attempts": [a.model_dump(mode="json") for a in attempts]})
        record = record.model_copy(update={"classification": classification, "attempts": attempts})

    # Only CLASSIFIER/QUALITY_CHECKER/REFINER_QUESTIONER/REFINER_REWRITER positions need
    # the refine loop at all -- STRATEGY_SELECTOR and TEST_GENERATOR mean the loop
    # already finished (the last round passed, or the cap already fired on an earlier
    # call to this function) and calling it again would try to start a phantom next
    # round: the last round has no rewrite in that case (a passed round never gets one
    # -- RefinementRound rejects it), so `rounds[-1].rewrite.refined_text` would raise
    # AttributeError. This was a real bug in the initial draft -- caught because it is
    # exactly the resume path Task 11 depends on, not because any of the four scenario
    # tests below exercise it.
    if stage in (PipelineStage.CLASSIFIER, PipelineStage.QUALITY_CHECKER,
                PipelineStage.REFINER_QUESTIONER, PipelineStage.REFINER_REWRITER):
        record, refine_error = _run_refine_loop(
            record, relevant_conflicts, relevant_dependencies, stage_fns, human_fns,
            throttle, max_revisions, stage_configs, max_attempts, backoff_seconds,
            known_requirement_ids=known_requirement_ids, checkpoint=checkpoint)
        if refine_error is not None:
            errors = list(record.errors) + [refine_error]
            return RequirementRunRecord.model_validate(
                {**record.model_dump(mode="json"), "outcome": "error",
                 "errors": [e.model_dump(mode="json") for e in errors]})

    last_round = record.rounds[-1]
    if not last_round.quality_report.passed:
        # The cap fired: ask the human whether to generate anyway or stop.
        if checkpoint is not None:
            checkpoint(record)
        outcome, cap_reason = human_fns.decide_at_cap(record)
        if outcome not in (RunOutcome.CAP_GENERATED, RunOutcome.CAP_STOPPED):
            raise ValueError(
                f"decide_at_cap returned {outcome!r}, must be CAP_GENERATED or CAP_STOPPED")
        if not cap_reason:
            raise ValueError("decide_at_cap returned an empty cap_reason")
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
        attempts = list(record.attempts)
        strategy_invocation_id = uuid.uuid4().hex
        try:
            strategy = call_stage(
                stage_fns.select_strategy,
                (current, record.classification, relevant_dependencies), TestStrategy,
                PipelineStage.STRATEGY_SELECTOR, strategy_invocation_id,
                stage_configs[PipelineStage.STRATEGY_SELECTOR.value].model, throttle, attempts,
                req.id, max_attempts, backoff_seconds,
                extra_check=_strategy_selector_extra_check(record.classification))
        except StageFailed as f:
            errors = list(record.errors) + [StageError(
                stage=PipelineStage.STRATEGY_SELECTOR, invocation_id=strategy_invocation_id,
                kind=f.kind, message=f.message, retry_count=f.retry_count)]
            return RequirementRunRecord.model_validate(
                {**record.model_dump(mode="json"), "outcome": "error",
                 "errors": [e.model_dump(mode="json") for e in errors],
                 "attempts": [a.model_dump(mode="json") for a in attempts]})
        record = record.model_copy(update={"test_strategy": strategy, "attempts": attempts})

    attempts = list(record.attempts)
    generator_invocation_id = uuid.uuid4().hex
    try:
        plan = call_stage(
            stage_fns.generate_tests, (current, strategy, relevant_dependencies), TestPlan,
            PipelineStage.TEST_GENERATOR, generator_invocation_id,
            stage_configs[PipelineStage.TEST_GENERATOR.value].model,
            throttle, attempts, req.id, max_attempts, backoff_seconds,
            extra_check=_test_generator_extra_check(req.id, strategy, known_requirement_ids))
    except StageFailed as f:
        errors = list(record.errors) + [StageError(
            stage=PipelineStage.TEST_GENERATOR, invocation_id=generator_invocation_id,
            kind=f.kind, message=f.message, retry_count=f.retry_count)]
        return RequirementRunRecord.model_validate(
            {**record.model_dump(mode="json"), "outcome": "error",
             "errors": [e.model_dump(mode="json") for e in errors],
             "attempts": [a.model_dump(mode="json") for a in attempts]})
    record = record.model_copy(update={"test_plan": plan, "attempts": attempts})

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
    requirement file at a time, so an interruption leaves a resumable partial run.

    checkpoint_fn (2026-08-09), when run_dir is given, is write_requirement_run itself,
    partially bound to run_dir -- passed into run_requirement so an in-progress
    requirement's state reaches disk right before either human-interaction point, not
    only after the requirement fully finishes. Without this, an EOFError/
    KeyboardInterrupt from a terminal HumanFns implementation (orchestrator/
    human_cli.py) mid-requirement loses that requirement's progress entirely -- nothing
    about it was ever written -- rather than leaving a resumable partial round. See
    _run_refine_loop's docstring for the resume path this makes possible.
    """
    consistency_report, dependency_report, doc_errors, doc_attempts = run_document_stages(
        requirement_set, metadata.stages, stage_fns, throttle, max_attempts, backoff_seconds)

    doc_outcome = (DocumentOutcome.COMPLETED
                  if consistency_report is not None and dependency_report is not None
                  else DocumentOutcome.DEGRADED)
    record = DocumentRunRecord(
        requirement_set=requirement_set, metadata=metadata, outcome=doc_outcome,
        errors=doc_errors, consistency_report=consistency_report,
        dependency_report=dependency_report, attempts=doc_attempts)
    if run_dir is not None:
        write_document_run(run_dir, record)

    checkpoint_fn = (lambda rec: write_requirement_run(run_dir, rec)) if run_dir is not None else None

    requirement_records = []
    for req in requirement_set.requirements:
        req_record = run_requirement(
            RequirementRunRecord(requirement=req, run_id=metadata.run_id), requirement_set,
            consistency_report, dependency_report, stage_fns, human_fns, throttle,
            max_revisions, metadata.stages, max_attempts, backoff_seconds, checkpoint_fn)
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
    would orphan every already-completed requirement record -- but ONLY while no
    requirement has been processed yet under this run's current document context.

    A manual retry is a new invocation, symmetric with requirement-level stage
    failures: it mints its own invocation_id and, if it fails, appends a new,
    independent DocumentStageError rather than merging into an existing one. `errors`
    stays a LOG of failed attempts, not current state -- the original failure(s) stay on
    record even after a later retry succeeds -- but each entry now stands for exactly
    one invocation, linked to it directly (invocation_id), instead of one entry
    aggregating retry_count across every manual retry of a stage. See
    docs/superpowers/specs/2026-08-08-per-attempt-observability-design.md.

    Once conflicts_for(id)/dependencies_for(id) are wired into per-requirement calls
    (see docs/superpowers/specs/2026-08-08-document-context-wiring-design.md), a retry
    that succeeds AFTER some requirements already ran would let those requirements and
    any still-pending ones see different document-level context within the same run_id
    -- an accident of timing, not a methodological choice. So this raises, before
    calling the stage fn at all (no API quota spent on a result that would be
    discarded), the moment any requirement record exists for this run. Recovering a
    failed document-level stage after that point requires starting a new run.
    """
    record = read_document_run(run_dir)
    if record.requirement_records:
        raise ValueError(
            f"cannot retry {stage.value}: {len(record.requirement_records)} requirement(s) "
            "already processed under this run's document context. Retrying now would let "
            "some requirements see the old context and others see the recovered one, in "
            "the same run. Start a new run to pick up corrected consistency/dependency "
            "analysis."
        )
    stage_fn = {DocumentStage.CONSISTENCY_CHECKER: stage_fns.check_consistency,
               DocumentStage.DEPENDENCY_MAPPER: stage_fns.map_dependencies}[stage]
    model_cls = {DocumentStage.CONSISTENCY_CHECKER: ConsistencyReport,
                DocumentStage.DEPENDENCY_MAPPER: DependencyReport}[stage]
    field_name = {DocumentStage.CONSISTENCY_CHECKER: "consistency_report",
                 DocumentStage.DEPENDENCY_MAPPER: "dependency_report"}[stage]
    known_ids = _known_requirement_ids(record.requirement_set)
    extra_check = {DocumentStage.CONSISTENCY_CHECKER: _consistency_extra_check(known_ids),
                  DocumentStage.DEPENDENCY_MAPPER: _dependency_extra_check(known_ids)}[stage]

    attempts: list[DocumentStageAttempt] = []
    invocation_id = uuid.uuid4().hex
    try:
        report = call_document_stage(
            stage_fn, (record.requirement_set,), model_cls, stage, invocation_id,
            record.metadata.stages[stage.value].model, throttle, attempts,
            record.requirement_set.doc_id, max_attempts, backoff_seconds,
            extra_check=extra_check)
    except StageFailed as f:
        errors = list(record.errors) + [DocumentStageError(
            stage=stage, invocation_id=invocation_id, kind=f.kind, message=f.message,
            retry_count=f.retry_count)]
        record = record.model_copy(update={"errors": errors, "attempts": record.attempts + attempts})
    else:
        record = record.model_copy(update={field_name: report, "attempts": record.attempts + attempts})

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
    IN_PROGRESS record; one that errored gets its existing record continued in place.

    Threads the same write_requirement_run-backed checkpoint into run_requirement that
    run_document does (see run_document's docstring) -- a resume that itself gets
    interrupted again mid-requirement must be just as resumable as the first attempt.
    """
    record = read_document_run(run_dir)
    pending = set(record.pending_requirement_ids)
    by_id = {r.requirement.id: r for r in record.requirement_records}
    checkpoint_fn = lambda rec: write_requirement_run(run_dir, rec)

    updated_records = []
    for req in record.requirement_set.requirements:
        if req.id not in pending:
            updated_records.append(by_id[req.id])
            continue
        base = by_id.get(req.id) or RequirementRunRecord(requirement=req, run_id=record.metadata.run_id)
        updated = run_requirement(
            base, record.requirement_set, record.consistency_report, record.dependency_report,
            stage_fns, human_fns, throttle, max_revisions, record.metadata.stages,
            max_attempts, backoff_seconds, checkpoint_fn)
        updated_records.append(updated)
        write_requirement_run(run_dir, updated)

    return record.model_copy(update={"requirement_records": updated_records})


def atomic_write_text(path: Path, text: str) -> None:
    """Writes `text` to `path` atomically: write to a temporary sibling file (same
    directory, so os.replace stays on one filesystem -- required for its atomicity
    guarantee), then os.replace() it onto the destination. An interruption mid-write
    leaves only the temporary file incomplete; the destination, if it already existed,
    is untouched until the replace, and the replace itself is all-or-nothing on both
    POSIX and Windows. Without this, a plain write_text() interrupted partway leaves a
    truncated JSON file that resume_document/read_document_run cannot parse -- turning
    one interruption into a run that can neither resume nor be told why."""
    tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp_path.write_text(text)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _requirement_filename(req_id: str) -> str:
    """Requirement.id is a free-form NonEmptyStr (design/schemas.py) -- no charset
    restriction -- and must never be interpolated into a filesystem path directly: an
    id like "../escape" writes outside requirements/, and "../document" can overwrite
    document.json. SHA-256 hash instead: deterministic (the same id always maps to the
    same filename, needed since write_requirement_run overwrites the same file across
    rounds/resumes), filesystem-safe by construction for any input, and the real id is
    never lost -- read_document_run reads it back from the JSON content
    (RequirementRunRecord.requirement.id), never from the filename."""
    return hashlib.sha256(req_id.encode("utf-8")).hexdigest() + ".json"


def write_document_run(run_dir: Path, record: DocumentRunRecord) -> None:
    """Writes document.json with an EMPTY requirement_records list (decision D2b) --
    each requirement is its own file, written separately by write_requirement_run.
    Re-validates before persisting (contract item 10): mutation after construction
    bypasses Pydantic's checks, so this re-runs them right before the bytes hit disk."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "requirements").mkdir(parents=True, exist_ok=True)
    on_disk = DocumentRunRecord.model_validate(
        {**record.model_dump(mode="json"), "requirement_records": []})
    atomic_write_text(run_dir / "document.json", on_disk.model_dump_json(indent=2))


def write_requirement_run(run_dir: Path, record: RequirementRunRecord) -> None:
    (run_dir / "requirements").mkdir(parents=True, exist_ok=True)
    validated = RequirementRunRecord.model_validate(record.model_dump(mode="json"))
    atomic_write_text(run_dir / "requirements" / _requirement_filename(record.requirement.id),
                      validated.model_dump_json(indent=2))


def read_document_run(run_dir: Path) -> DocumentRunRecord:
    """Reassembles the document from document.json (empty requirement_records) plus
    every requirements/*.json file -- the inverse of write_document_run/
    write_requirement_run under D2b.

    Filenames are content-addressed (see _requirement_filename) and carry no ordering
    information, unlike the old id-as-filename scheme where `sorted(glob(...))` happened
    to sort by id. Records are explicitly sorted by requirement.id after loading, not by
    glob/filename order, so read_document_run's observable behavior -- id-ordered
    requirement_records -- is unchanged by that scheme change.
    """
    doc_data = json.loads((run_dir / "document.json").read_text())
    req_dir = run_dir / "requirements"
    records = sorted(
        (RequirementRunRecord.model_validate_json(path.read_text()) for path in req_dir.glob("*.json")),
        key=lambda r: r.requirement.id)
    return DocumentRunRecord.model_validate(
        {**doc_data, "requirement_records": [r.model_dump(mode="json") for r in records]})
