"""Provenance models for Task 6's baseline arms (B1, B2).

Deliberately not design/schemas.py additions. `TestCase` is reused as-is (arm P, B1 and
B2 must be scored on the exact same case shape -- that IS the "same output shape" fairness
requirement in docs/EVALUATION_PROTOCOL.md section 3), but everything else here is new and
scoped to a baseline run:

- `TestPlan` (design/schemas.py) is not reused for baseline output. It is one plan PER
  REQUIREMENT (`TestPlan.requirement_id`, `_cases_cover_this_requirement` requires every
  case in the plan to name that one requirement) because arm P's Test Case Generator runs
  once per requirement. B1/B2 see the whole requirement set in a single call by design --
  forcing that output into N per-requirement TestPlan objects would either be dishonest
  (inventing a "requirement_id" for a plan that was never generated per-requirement) or
  would silently drop any case naming more than one requirement. `BaselineTestCaseBatch`
  is a flat pool of `TestCase` instead; A1-A5 and the blinding pool already operate on a
  flat pool of cases across all three arms anyway (EVALUATION_PROTOCOL.md 6.1
  "Pool every test case from arms P, B1 and B2"), so nothing downstream loses information.
- `ClarificationQuestion`/`ClarificationAnswer` are not `ClarifyingQuestion`/`RefinerAnswer`
  (design/schemas.py). Those pipeline types carry `issue_id`/`issue_category` -- fields
  that only make sense given the `IssueCategory` taxonomy B1/B2 must never see
  (EVALUATION_PROTOCOL.md section 3: "Neither baseline receives the IssueCategory
  taxonomy"). Reusing them would either leave those fields meaningless or tempt a caller
  into populating them, which would leak treatment information into a control arm.
- `BaselineAttempt`/`BaselineRunOutput` are not `StageAttempt`/`RequirementRunRecord`.
  Those are pipeline-run persistence, keyed to `PipelineStage`/`RunOutcome`/the revision
  cap and resume machinery none of which applies to a one-or-two-call baseline script.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from design.schemas import NonEmptyStr, OutputMode, TestCase


class BaselineCallConfig(BaseModel):
    """The scalars a baseline call needs from the adapter contract. Deliberately not
    `orchestrator.config.ResolvedStageConfig`: that type's `prompt_path`/`prompt_hash`
    fields describe ONE resolved prompt for ONE pipeline stage, and B2 alone spans two
    distinct prompt files in one run -- forcing this shape into that type would leave
    those two fields holding a placeholder value for every real caller.

    A `BaseModel`, not a `NamedTuple` (2026-08-17 fix): bounds must reject an invalid
    temperature/timeout at CONSTRUCTION time, before any adapter is built from it --
    same `ge=0.0, le=2.0` / `gt=0` bounds as `ResolvedStageConfig` (orchestrator/config.py),
    for the same reason given there (a value must be checked independently of whatever
    upstream validation the caller assumes already ran)."""
    provider: Literal["gemini", "groq"]
    model: NonEmptyStr
    temperature: float = Field(..., ge=0.0, le=2.0)
    timeout_seconds: float = Field(30.0, gt=0)
    output_mode: OutputMode = OutputMode.TEXT


class ClarificationQuestion(BaseModel):
    id: NonEmptyStr
    question_text: NonEmptyStr


class ClarificationAnswer(BaseModel):
    question_id: NonEmptyStr
    answer_text: NonEmptyStr


class BaselineQuestionBatch(BaseModel):
    """B2's first-call output: the model's one round of clarifying questions, asked
    about the whole requirement set at once (not per-requirement, unlike the pipeline's
    Refiner). May legitimately be empty -- a competent practitioner's prompt (see
    evaluation/prompts/b2_questions.txt) explicitly tells the model not to invent a
    question it does not need answered, so "found nothing worth asking" is a real,
    allowed outcome, not a parse failure."""
    questions: list[ClarificationQuestion] = Field(default_factory=list)

    @model_validator(mode="after")
    def _question_ids_are_unique(self) -> "BaselineQuestionBatch":
        ids = [q.id for q in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate question id(s): {ids}")
        return self


class BaselineTestCaseBatch(BaseModel):
    """The parsed shape of a B1 or B2 generation call: a flat pool of test cases
    covering the whole requirement set, not one plan per requirement. See this
    module's docstring for why `TestPlan` is not reused here."""
    test_cases: list[TestCase] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _case_ids_are_unique(self) -> "BaselineTestCaseBatch":
        ids = [c.id for c in self.test_cases]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate test case id(s): {ids}")
        return self


class BaselineAttempt(BaseModel):
    """One LLM call the runner made. B1 records exactly one (`b1_generate`); B2
    records exactly two, in order (`b2_questions`, then `b2_generate`) -- never a third,
    which is what makes the one-round limit mechanically enforceable rather than just
    documented (Task 6 requirement 5)."""
    call: Literal["b1_generate", "b2_questions", "b2_generate"]
    attempt_number: int = Field(..., ge=1)  # retries within ONE call, e.g. transport backoff
    result: Literal["success", "failed", "fatal", "partial"]
    error_message: Optional[str] = None
    prompt_tokens: Optional[int] = Field(None, ge=0)
    completion_tokens: Optional[int] = Field(None, ge=0)
    wall_clock_seconds: float = Field(..., ge=0.0)

    @model_validator(mode="after")
    def _tokens_both_present_or_both_absent(self) -> "BaselineAttempt":
        have = (self.prompt_tokens is not None, self.completion_tokens is not None)
        if have[0] != have[1]:
            raise ValueError(
                "prompt_tokens/completion_tokens must both be present or both be "
                f"absent (a call that never reached the provider has neither); got "
                f"prompt_tokens={self.prompt_tokens!r} completion_tokens={self.completion_tokens!r}")
        return self

    @model_validator(mode="after")
    def _success_has_tokens_failure_has_message(self) -> "BaselineAttempt":
        if self.result == "success" and self.prompt_tokens is None:
            raise ValueError("result='success' requires prompt_tokens/completion_tokens")
        if self.result != "success" and self.error_message is None:
            raise ValueError(f"result={self.result!r} requires error_message")
        return self


class BaselineRunOutput(BaseModel):
    """Everything Task 6 requirement 4 asks to be recorded for one arm's run over one
    document: model/config/prompt provenance, token usage, wall-clock, failures, and
    (B2 only) the single clarification round.

    Carries the COMPLETE effective `BaselineCallConfig`, not just provider/model/
    temperature (2026-08-17, second-round finding 7): `timeout_seconds` and
    `output_mode` are as much a part of "what configuration actually produced this
    run" as the other three, and a freeze record with only three of five config
    scalars is a freeze record with a silent gap in it."""
    arm: Literal["B1", "B2"]
    doc_id: Optional[str] = None
    # Deterministic content hash of the canonical RequirementSet this run actually used
    # (2026-08-17, third-round finding 2) -- `doc_id` alone does not prove the
    # requirement TEXT is unchanged (a document can be edited without renaming it), and
    # `doc_id` can be `None` for two genuinely different inputs, making it useless as a
    # match key in that case. Required, not optional: every run has SOME RequirementSet,
    # so there is no legitimate state where this is unknown.
    requirement_set_hash: NonEmptyStr
    provider: NonEmptyStr
    model: NonEmptyStr
    temperature: float = Field(..., ge=0.0, le=2.0)
    timeout_seconds: float = Field(..., gt=0)
    output_mode: OutputMode
    # {"b1_generate": "<sha256 of that prompt file's rendered template>", ...} -- same
    # provenance idea as ResolvedStageConfig.prompt_hash (orchestrator/config.py), one
    # entry per distinct prompt file this run used.
    prompt_hash: dict[str, NonEmptyStr]
    attempts: list[BaselineAttempt] = Field(default_factory=list)
    questions: list[ClarificationQuestion] = Field(default_factory=list)  # B2 only
    answers: list[ClarificationAnswer] = Field(default_factory=list)      # B2 only
    test_cases: list[TestCase] = Field(default_factory=list)
    total_wall_clock_seconds: float = Field(..., ge=0.0)
    failed: bool = False
    # Set only when the run failed for a reason that is NOT an LLM call attempt --
    # e.g. the operator's answer source raised (EOF, interrupted, malformed answers)
    # after a successful questions call. An LLM-call failure is already fully
    # explained by `attempts`; this field exists for the one failure mode that isn't
    # an attempt at all (2026-08-17, Task 6 finding 6).
    failure_reason: Optional[str] = None
    # Explicit structural marker for "this is the awaiting-answers checkpoint state"
    # (2026-08-17, third-round finding 1) -- set ONLY by run_b2's checkpoint_fn call
    # site (evaluation/runner.py), never by any other construction site in this
    # codebase. Preferred over inferring the state from `failure_reason` text (a
    # free-form string, matched by substring, that a completed or differently-failed
    # run could coincidentally share) -- see `_awaiting_answers_checkpoint_is_valid`
    # below, which makes an invalid "awaiting_answers"-tagged object impossible to
    # construct or load at all, not just discouraged.
    checkpoint_phase: Optional[Literal["awaiting_answers"]] = None

    @model_validator(mode="after")
    def _failure_reason_implies_failed(self) -> "BaselineRunOutput":
        if self.failure_reason is not None and not self.failed:
            raise ValueError("failure_reason is set but failed=False")
        return self

    @model_validator(mode="after")
    def _awaiting_answers_checkpoint_is_valid(self) -> "BaselineRunOutput":
        """Task 6 third-round finding 1: a checkpoint claiming `checkpoint_phase=
        "awaiting_answers"` must actually BE the state `run_b2` produces right before
        asking the operator to answer -- not a completed or differently-failed B2
        output that happens to carry the marker (which nothing but this validator
        stops from being wrong, if the marker were trusted blindly). Every property
        Task 6's third-round finding 1 lists is checked here, so a malformed or
        hand-edited "awaiting_answers" file fails to even LOAD, before any resume
        logic ever sees it."""
        if self.checkpoint_phase != "awaiting_answers":
            return self
        if self.arm != "B2":
            raise ValueError("checkpoint_phase='awaiting_answers' requires arm='B2'")
        if not self.failed:
            raise ValueError(
                "checkpoint_phase='awaiting_answers' requires failed=True -- it is an "
                "incomplete, in-progress state, never a final outcome")
        if self.answers:
            raise ValueError("checkpoint_phase='awaiting_answers' must carry no answers yet")
        if self.test_cases:
            raise ValueError("checkpoint_phase='awaiting_answers' must carry no test_cases yet")
        if any(a.call != "b2_questions" for a in self.attempts):
            raise ValueError(
                "checkpoint_phase='awaiting_answers' must contain ONLY b2_questions "
                "attempts -- a b2_generate attempt means generation already started")
        if set(self.prompt_hash) != {"b2_questions"}:
            raise ValueError(
                "checkpoint_phase='awaiting_answers' must record ONLY the b2_questions "
                "prompt hash -- a b2_generate entry means generation already started")
        if not self.attempts:
            raise ValueError(
                "checkpoint_phase='awaiting_answers' requires at least one attempt -- "
                "the successful questions call that produced this checkpoint")
        numbers = [a.attempt_number for a in self.attempts]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError(
                "checkpoint_phase='awaiting_answers' attempts must be numbered 1..N "
                f"with no gaps or repeats, got {numbers}")
        # Every attempt before the final one must be "failed" or "partial" -- the ONLY
        # results _call_once ever retries after (2026-08-17, fourth-round finding 2:
        # tightened from "no success before the last" alone, which let a "fatal"
        # attempt through in second position even though _call_once's own contract
        # makes that combination impossible -- a StageCallFatal short-circuits
        # immediately with exactly one attempt total, so "fatal" can only ever be the
        # LAST recorded attempt, never followed by a retry that then succeeds).
        if any(a.result not in ("failed", "partial") for a in self.attempts[:-1]):
            raise ValueError(
                "checkpoint_phase='awaiting_answers' must have every attempt before "
                "the final one be 'failed' or 'partial' -- those are the only results "
                "_call_once (evaluation/runner.py) ever retries after; a 'success' or "
                "'fatal' attempt anywhere but last describes a call sequence _call_once "
                "cannot actually produce")
        if self.attempts[-1].result != "success":
            raise ValueError(
                "checkpoint_phase='awaiting_answers' must end in a successful "
                "b2_questions attempt -- the questions call has to have actually "
                "succeeded to reach this state")
        # questions may legitimately be empty (BaselineQuestionBatch's own contract) --
        # not checked here, deliberately: "zero questions" is a valid awaiting-answers
        # checkpoint, not an invalid one.
        return self

    @model_validator(mode="after")
    def _failed_and_test_cases_agree(self) -> "BaselineRunOutput":
        # Same "two fields that must agree" pattern CLAUDE.md flags project-wide
        # (passed/issues, outcome/contents, ...) -- forced here rather than left to
        # caller discipline.
        if self.failed and self.test_cases:
            raise ValueError("failed=True but test_cases is non-empty")
        if not self.failed and not self.test_cases:
            raise ValueError("failed=False requires at least one test case")
        return self

    @model_validator(mode="after")
    def _b1_has_no_clarification_round(self) -> "BaselineRunOutput":
        if self.arm == "B1" and (self.questions or self.answers):
            raise ValueError(
                "B1 is one-prompt, no-human-interaction -- must not carry questions/answers")
        return self

    @model_validator(mode="after")
    def _b2_clarification_round_is_exactly_one_round(self) -> "BaselineRunOutput":
        """Task 6 requirement 5: the single round must be enforceable, not just
        documented. A successful B2 run must answer id-for-id every question actually
        asked, with no answer referencing a question that was never asked --
        vacuously satisfied when the model asked nothing (a legitimate outcome, see
        `BaselineQuestionBatch`). Combined with `BaselineAttempt.call`'s closed
        three-value set (no `b2_questions_2`), there is no field anywhere in this
        model a second round could be recorded in."""
        if self.arm != "B2" or self.failed:
            return self
        asked = {q.id for q in self.questions}
        answered = {a.question_id for a in self.answers}
        unanswered = asked - answered
        if unanswered:
            raise ValueError(f"question id(s) {sorted(unanswered)} were asked but never answered")
        unknown = answered - asked
        if unknown:
            raise ValueError(f"answer(s) reference unknown question id(s) {sorted(unknown)}")
        return self
