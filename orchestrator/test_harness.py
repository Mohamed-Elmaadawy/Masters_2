"""
Simulation harness for the orchestrator. Run after any change to orchestrator/pipeline.py:

    python -m orchestrator.test_harness

Plain script, no pytest -- same reasoning as design/test_schemas.py: one less
dependency, readable pass/fail report, wireable into CI later. design/ and
orchestrator/ import in one direction only (orchestrator -> design); this file never
imports from design/test_schemas.py, so design/ stays self-contained.

Each scenario is numbered to match docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md's
"Harness scenarios" section.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from design.schemas import (
    AttemptResult, ClarifyingQuestion, DocumentRunRecord, DocumentStageAttempt,
    PipelineStage, QualityReport, RefinedRequirement, RefinementRound, RefinerAnswer,
    RefinerTurn, Requirement, RequirementRunRecord, RequirementSet, RunOutcome,
    StageAttempt, StageConfig, StageError, RunMetadata, ALL_STAGES, FailureKind,
    prompt_fingerprint,
)
from orchestrator.pipeline import (
    resume_at, StageCallResult, StageCallFailed, StageFns, HumanFns,
)

PASSED = 0
FAILED: list[str] = []


def ok(label: str, condition: bool = True) -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(label)
        print(f"    FAIL  {label}")


def section(name: str) -> None:
    print(f"\n{name}")


# ---------------------------------------------------------------------------
# Shared fixtures -- deliberately independent of design/test_schemas.py's fixtures.
# ---------------------------------------------------------------------------

FAKE_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

T0 = "Temperatures that do not exceed these limits shall be output for subsequent processing."
T1 = "Temperatures within the valid range defined in DOC-REQ-B shall be output for subsequent processing."

REQ_A = Requirement(id="DOC-REQ-A", text=T0, source_doc_id="harness-doc")
REQ_B = Requirement(
    id="DOC-REQ-B", source_doc_id="harness-doc",
    text="If the current temperature value is outside the valid range, the system shall output an invalid status.",
)
DOC = RequirementSet(doc_id="harness-doc", requirements=[REQ_A, REQ_B])

STAGE_CONFIGS = {s: StageConfig(model="fake-model", prompt_hash=prompt_fingerprint(f"prompt for {s}"))
                 for s in ALL_STAGES}


def make_metadata(run_id: str = "run-harness-001") -> RunMetadata:
    return RunMetadata(run_id=run_id, started_at=FAKE_NOW, stages=STAGE_CONFIGS,
                       prompt_version="test-v1")


def rec(**kw) -> RequirementRunRecord:
    kw.setdefault("run_id", "run-harness-001")
    kw.setdefault("requirement", REQ_A)
    return RequirementRunRecord(**kw)


def failed_stage(stage, invocation_id, kind=FailureKind.TRANSPORT, message="429",
                 attempts=1) -> tuple:
    """A StageError plus the matching minimal StageAttempt log an exhausted
    `attempts`-try invocation would produce. Schema 1.1 requires every StageError to
    reference a real, matching invocation -- no exceptions for hand-built fixtures --
    so any test that constructs a bare StageError (rather than driving it through
    call_stage) needs this. See
    docs/superpowers/specs/2026-08-08-per-attempt-observability-design.md."""
    result = {FailureKind.TRANSPORT: AttemptResult.TRANSPORT_FAILURE,
             FailureKind.VALIDATION: AttemptResult.VALIDATION_FAILURE,
             FailureKind.OTHER: AttemptResult.OTHER_FAILURE}[kind]
    tokens = dict(prompt_tokens=10, completion_tokens=5) if result is AttemptResult.VALIDATION_FAILURE else {}
    log = [StageAttempt(stage=stage, invocation_id=invocation_id, attempt_number=i,
                        result=result, error_message=message, **tokens)
           for i in range(1, attempts + 1)]
    err = StageError(stage=stage, invocation_id=invocation_id, kind=kind, message=message,
                     retry_count=attempts - 1)
    return err, log


def mk_round(n, text, passed=True, rewrite_to=None, requirement_id=REQ_A.id) -> RefinementRound:
    quality_report = QualityReport(requirement_id=requirement_id, passed=passed,
                                    issues=[] if passed else [_dummy_issue(n)])
    turn = None
    answers = []
    rewrite = None
    if not passed:
        issue = quality_report.issues[0]
        turn = RefinerTurn(requirement_id=requirement_id, revision_number=n, questions=[
            ClarifyingQuestion(id=f"Q{n}", issue_id=issue.id, issue_category=issue.category,
                               question_text="?")])
        answers = [RefinerAnswer(question_id=f"Q{n}", answer_text="answer")]
        if rewrite_to:
            rewrite = RefinedRequirement(requirement_id=requirement_id, original_text=text,
                                         refined_text=rewrite_to, revision_number=n,
                                         answers_used=answers)
    return RefinementRound(revision_number=n, text_checked=text, quality_report=quality_report,
                           turn=turn, answers=answers, rewrite=rewrite)


def _dummy_issue(n):
    from design.schemas import Issue, IssueCategory
    return Issue(id=f"I{n}", category=IssueCategory.VAGUE_PRONOUN, span="these limits",
                explanation="Unresolved referent.")


class Scripted:
    """A stage fn returning one scripted behavior per call, in order.

    Each behavior is either a dict (wrapped into a successful StageCallResult) or an
    Exception instance (raised as-is, e.g. StageCallFailed("429") or KeyError("oops")).
    Records every call's positional args for scenarios that need to assert on them.
    """
    def __init__(self, behaviors: list, tokens: tuple[int, int] = (10, 5)):
        self._behaviors = list(behaviors)
        self._tokens = tokens
        self.calls: list[tuple] = []

    def __call__(self, *args, **kwargs) -> StageCallResult:
        self.calls.append(args)
        behavior = self._behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        return StageCallResult(raw=behavior, prompt_tokens=self._tokens[0],
                               completion_tokens=self._tokens[1])


# ---------------------------------------------------------------------------

def test_resume_positions() -> None:
    """Scenario 5: a failure at any stage must resume at that stage -- nothing earlier redone."""
    section("Scenario 5 -- resume_at correctness")
    # Each case needs its own invocation_id/attempts pair -- schema 1.1 requires every
    # StageError to reference a real, matching, failed invocation.
    def err(stage):
        e, attempts = failed_stage(stage, f"inv-{stage.value}", attempts=4)
        return [e], attempts
    mid_round = mk_round(1, T0, passed=False)                        # asked+answered, no rewrite yet
    rewritten = mk_round(1, T0, passed=False, rewrite_to=T1)
    from design.schemas import (
        Classification, QualityReport, SystemType, TestCase, TestPlan, TestStrategy,
        TestTechnique,
    )
    # turn=None, answers=[], rewrite=None -- the round hasn't been asked about at all yet
    # (the questioner itself failed, or the cap fired before it ever ran). Distinct from
    # `mid_round` above, which mk_round always gives a turn+answers to once passed=False.
    never_asked = RefinementRound(revision_number=1, text_checked=T0,
                                  quality_report=QualityReport(requirement_id=REQ_A.id,
                                                               passed=False, issues=[_dummy_issue(1)]))
    cls = Classification(requirement_id=REQ_A.id, system_type=SystemType.OTHER, rationale="r")
    strategy = TestStrategy(requirement_id=REQ_A.id, system_type=SystemType.OTHER,
                            techniques=[TestTechnique.BOUNDARY_VALUE_ANALYSIS], rationale="r")
    rounds_refined = [rewritten, mk_round(2, T1, passed=True)]
    plan = TestPlan(requirement_id=REQ_A.id, test_cases=[
        TestCase(id="TC1", requirement_ids=[REQ_A.id],
                 technique_used=TestTechnique.BOUNDARY_VALUE_ANALYSIS,
                 title="Temperature at limit", steps=["Set temperature to the limit value."],
                 expected_result="Value is output for subsequent processing.")])

    def kw_with_err(stage, **rest):
        errors, attempts = err(stage)
        return dict(errors=errors, attempts=attempts, **rest)

    cases = [
        ("classifier failed", kw_with_err(PipelineStage.CLASSIFIER), PipelineStage.CLASSIFIER),
        ("quality checker failed on round 1",
         kw_with_err(PipelineStage.QUALITY_CHECKER, classification=cls),
         PipelineStage.QUALITY_CHECKER),
        ("refiner questioner failed, nothing asked yet",
         kw_with_err(PipelineStage.REFINER_QUESTIONER, classification=cls,
                     rounds=[never_asked]),
         PipelineStage.REFINER_QUESTIONER),
        ("refiner rewriter failed, asked and answered but not rewritten yet",
         kw_with_err(PipelineStage.REFINER_REWRITER, classification=cls, rounds=[mid_round]),
         PipelineStage.REFINER_REWRITER),
        ("quality checker failed on round 2, round 1 already rewrote",
         kw_with_err(PipelineStage.QUALITY_CHECKER, classification=cls, rounds=[rewritten]),
         PipelineStage.QUALITY_CHECKER),
        ("strategy selector failed",
         kw_with_err(PipelineStage.STRATEGY_SELECTOR, classification=cls, rounds=rounds_refined),
         PipelineStage.STRATEGY_SELECTOR),
        ("test generator failed",
         kw_with_err(PipelineStage.TEST_GENERATOR, classification=cls, rounds=rounds_refined,
                     test_strategy=strategy), PipelineStage.TEST_GENERATOR),
    ]
    for label, kw, expected in cases:
        got = resume_at(rec(outcome=RunOutcome.ERROR, **kw))
        ok(f"{label} -> resume at {expected.value}", got is expected)

    ok("an interrupted record resumes at the classifier", resume_at(rec()) is PipelineStage.CLASSIFIER)

    ok("a finished record resumes nowhere",
       resume_at(rec(outcome=RunOutcome.COMPLETED, classification=cls, rounds=rounds_refined,
                    test_strategy=strategy, test_plan=plan)) is None)


def test_stage_fns_typo_is_a_typeerror() -> None:
    """A typo'd field name must be an immediate TypeError, not a silently-skipped key --
    the reason StageFns/HumanFns are dataclasses, not dicts."""
    section("StageFns / HumanFns construction")
    from orchestrator.pipeline import StageFns, HumanFns

    def not_provided(*a, **k):
        raise AssertionError("should never be called")

    try:
        StageFns(check_consistency=not_provided, map_dependencies=not_provided,
                 classify=not_provided, check_quality=not_provided,
                 refine_questioner=not_provided, refine_rewriter=not_provided,
                 select_strategy=not_provided, generate_tests=not_provided,
                 clasify=not_provided)  # typo
        ok("StageFns rejects an unknown field", False)
    except TypeError:
        ok("StageFns rejects an unknown field", True)

    try:
        HumanFns(answer_questions=not_provided, decide_at_cap=not_provided,
                 decide_at_capp=not_provided)  # typo
        ok("HumanFns rejects an unknown field", False)
    except TypeError:
        ok("HumanFns rejects an unknown field", True)

    real = StageFns(check_consistency=not_provided, map_dependencies=not_provided,
                    classify=not_provided, check_quality=not_provided,
                    refine_questioner=not_provided, refine_rewriter=not_provided,
                    select_strategy=not_provided, generate_tests=not_provided)
    ok("StageFns constructs with exactly the right fields", real.classify is not_provided)
    ok("StageFns keeps refine_questioner and refine_rewriter as independent fields",
       real.refine_questioner is not_provided and real.refine_rewriter is not_provided)


def test_call_stage() -> None:
    """The narrow-except wrapper: success, TRANSPORT, VALIDATION, and scenario 14 --
    OTHER, the one branch that must actually fire, not just be documented as possible.
    Also covers the per-attempt log itself (contract item 13's replacement): every
    attempt gets a StageAttempt row, not just the ones that returned."""
    section("call_stage")
    from orchestrator.pipeline import call_stage, StageFailed, Throttle
    from design.schemas import AttemptResult, Classification, FailureKind, StageAttempt

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)

    # -- success: first attempt, one StageAttempt row --
    attempts: list[StageAttempt] = []
    fn = Scripted([{"requirement_id": "R1", "system_type": "web", "rationale": "r"}])
    result = call_stage(fn, ("R1",), Classification, PipelineStage.CLASSIFIER, "inv-1",
                        "fake-model", throttle, attempts, "R1")
    ok("call_stage returns a validated model", isinstance(result, Classification))
    ok("a successful first attempt records exactly one attempt row", len(attempts) == 1)
    ok("the attempt carries the right stage and invocation_id",
       attempts[0].stage is PipelineStage.CLASSIFIER and attempts[0].invocation_id == "inv-1")
    ok("the attempt is attempt_number=1, result=SUCCESS, tokens present",
       attempts[0].attempt_number == 1 and attempts[0].result is AttemptResult.SUCCESS
       and attempts[0].prompt_tokens is not None)

    # -- TRANSPORT: exhausts retries, every attempt logged as a failure, no tokens --
    attempts2: list[StageAttempt] = []
    fn2 = Scripted([StageCallFailed("429"), StageCallFailed("429"), StageCallFailed("429")])
    try:
        call_stage(fn2, ("R1",), Classification, PipelineStage.CLASSIFIER, "inv-2",
                  "fake-model", throttle, attempts2, "R1", max_attempts=3,
                  backoff_seconds=lambda a: 0.0)
        ok("TRANSPORT exhaustion raises StageFailed", False)
    except StageFailed as f:
        ok("TRANSPORT exhaustion raises StageFailed", True)
        ok("StageFailed.kind is TRANSPORT", f.kind is FailureKind.TRANSPORT)
        ok("retry_count is attempts-1", f.retry_count == 2)
    ok("every failed attempt is logged, none carrying tokens",
       len(attempts2) == 3
       and all(a.result is AttemptResult.TRANSPORT_FAILURE for a in attempts2)
       and all(a.prompt_tokens is None for a in attempts2))
    ok("attempt numbers are 1, 2, 3 in order, all under the same invocation_id",
       [a.attempt_number for a in attempts2] == [1, 2, 3]
       and {a.invocation_id for a in attempts2} == {"inv-2"})

    # -- VALIDATION: the call succeeded, tokens were spent on rejected output --
    attempts3: list[StageAttempt] = []
    fn3 = Scripted([{"requirement_id": "R1"}])  # missing system_type, rationale
    try:
        call_stage(fn3, ("R1",), Classification, PipelineStage.CLASSIFIER, "inv-3",
                  "fake-model", throttle, attempts3, "R1", max_attempts=1,
                  backoff_seconds=lambda a: 0.0)
        ok("VALIDATION failure raises StageFailed", False)
    except StageFailed as f:
        ok("VALIDATION failure raises StageFailed", True)
        ok("StageFailed.kind is VALIDATION", f.kind is FailureKind.VALIDATION)
    ok("a validation failure still logs an attempt with tokens (tokens were spent)",
       len(attempts3) == 1 and attempts3[0].result is AttemptResult.VALIDATION_FAILURE
       and attempts3[0].prompt_tokens is not None)

    # -- Scenario 14: OTHER, and the negative it's paired with --
    attempts4: list[StageAttempt] = []
    fn4 = Scripted([KeyError("unexpected")])
    try:
        call_stage(fn4, ("R1",), Classification, PipelineStage.CLASSIFIER, "inv-4",
                  "fake-model", throttle, attempts4, "R1", max_attempts=1,
                  backoff_seconds=lambda a: 0.0)
        ok("an unexpected exception type raises StageFailed(OTHER)", False)
    except StageFailed as f:
        ok("an unexpected exception type raises StageFailed(OTHER)", True)
        ok("StageFailed.kind is OTHER", f.kind is FailureKind.OTHER)
        ok("message names the exception class", "KeyError" in f.message)
    ok("OTHER is logged with no tokens (never reached the model)",
       len(attempts4) == 1 and attempts4[0].result is AttemptResult.OTHER_FAILURE
       and attempts4[0].prompt_tokens is None)

    # Negative: a bug in code CALLING call_stage (outside the guarded line) still
    # crashes, rather than being caught and filed as OTHER. call_stage itself has no
    # surrounding try/except beyond the one line that calls stage_fn -- demonstrated by
    # confirming a bug in the *test's own* orchestration crashes as a real exception.
    # Uses a fresh Scripted (fn's one behavior was already consumed above) so the
    # call_stage call itself succeeds -- the AttributeError must come from the
    # caller's own code, not from stage_fn running dry.
    fn5 = Scripted([{"requirement_id": "R1", "system_type": "web", "rationale": "r"}])
    def broken_caller():
        result = call_stage(fn5, ("R1",), Classification, PipelineStage.CLASSIFIER, "inv-5",
                            "fake-model", throttle, attempts, "R1")
        return result.nonexistent_attribute  # AttributeError, not from inside call_stage
    try:
        broken_caller()
        ok("a caller bug (outside call_stage's guarded line) still crashes", False)
    except AttributeError:
        ok("a caller bug (outside call_stage's guarded line) still crashes", True)


def test_id_check_parameters_have_no_default() -> None:
    """Anchor test, same shape as design/test_schemas.py's test_rule_table_anchors: a
    design guarantee with no test is a claim that could silently stop holding. The
    commit message for the requirement_id/doc_id mismatch fix says req_id and doc_id
    have no default so a forgotten wire-up fails loud instead of silently skipping the
    check -- true in the code, but nothing pinned it. Verified by construction first:
    giving req_id a default (`req_id: str = ''`) left all other checks in this file
    passing, and giving doc_id a default did the same for call_document_stage's. This
    test is what makes a future reintroduction of either default visible."""
    section("Anchor -- call_stage/call_document_stage id-check parameters require no default")
    import inspect
    from orchestrator.pipeline import call_stage, call_document_stage

    ok("call_stage.req_id has no default -- a forgotten wire-up must fail loud",
       inspect.signature(call_stage).parameters["req_id"].default is inspect.Parameter.empty)
    ok("call_document_stage.doc_id has no default -- a forgotten wire-up must fail loud",
       inspect.signature(call_document_stage).parameters["doc_id"].default
       is inspect.Parameter.empty)


def test_requirement_id_mismatch_is_validation_at_every_stage() -> None:
    """ORCHESTRATOR_CONTRACT.md item 15 (option B): a stage answering about the wrong
    requirement is a FailureKind.VALIDATION failure, uniformly, at all six
    per-requirement stage models -- not three different behaviours depending on which
    stage produced it. Before this fix, verified by construction (not assumed):
    Classification/TestStrategy/an internally-consistent TestPlan crashed with an
    uncaught ValidationError only once the record was finally re-validated (after later
    stages had already run and been paid for); QualityReport was silently relabelled
    with the correct id and the run completed as if nothing were wrong; RefinerTurn/
    RefinedRequirement crashed immediately at RefinementRound construction. All six now
    go through the exact same call_stage check and come out the same way: retried per
    the normal policy, usage recorded (the call succeeded; it just answered about the
    wrong requirement), StageFailed(kind=VALIDATION) on exhaustion.

    Each payload below is otherwise completely valid and self-consistent -- the ONLY
    thing wrong is requirement_id -- so a fixture that returned kind=OTHER or crashed
    outright would mean call_stage's new check isn't what's catching it.
    """
    section("Requirement id mismatch is uniformly a validation failure (contract item 15)")
    from orchestrator.pipeline import call_stage, StageFailed, Throttle
    from design.schemas import (
        Classification, FailureKind, QualityReport, RefinedRequirement, RefinerTurn,
        TestPlan, TestStrategy,
    )

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    WRONG = "SOME-OTHER-REQ"

    cases = [
        ("classify", Classification,
         {"requirement_id": WRONG, "system_type": "web", "rationale": "r"}),
        ("check_quality", QualityReport,
         {"requirement_id": WRONG, "passed": True, "issues": []}),
        ("refine (turn)", RefinerTurn,
         {"requirement_id": WRONG, "revision_number": 1, "questions": [
             {"id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun", "question_text": "?"}]}),
        ("refine (rewrite)", RefinedRequirement,
         {"requirement_id": WRONG, "original_text": "a", "refined_text": "b",
          "revision_number": 1, "answers_used": [{"question_id": "Q1", "answer_text": "a"}]}),
        ("select_strategy", TestStrategy,
         {"requirement_id": WRONG, "system_type": "web", "techniques": ["exploratory"], "rationale": "r"}),
        # Internally self-consistent on purpose (the plan's cases also say WRONG, not
        # R1) -- isolates OUR check from TestPlan's own unrelated _cases_cover_this_
        # requirement validator, which would otherwise catch an INCONSISTENT payload
        # for an unrelated reason and give a false sense that this stage was covered.
        ("generate_tests", TestPlan,
         {"requirement_id": WRONG, "test_cases": [{
             "id": "TC-1", "requirement_ids": [WRONG], "technique_used": "exploratory",
             "title": "t", "steps": ["s"], "expected_result": "e"}]}),
    ]
    for label, model_cls, raw in cases:
        attempts: list = []
        fn = Scripted([raw])
        try:
            call_stage(fn, ("R1",), model_cls, PipelineStage.CLASSIFIER, "inv-mismatch",
                      "fake-model", throttle, attempts, "R1", max_attempts=1,
                      backoff_seconds=lambda a: 0.0)
            ok(f"{label}: requirement_id mismatch raises StageFailed", False)
        except StageFailed as f:
            ok(f"{label}: requirement_id mismatch raises StageFailed", True)
            ok(f"{label}: kind is VALIDATION, not OTHER or an uncaught crash",
               f.kind is FailureKind.VALIDATION)
        ok(f"{label}: attempt still logged (the call succeeded; the id was just wrong)",
           len(attempts) == 1)


def test_requirement_id_mismatch_end_to_end() -> None:
    """Same fix, proven through the full pipeline rather than call_stage in isolation --
    confirms run_requirement actually threads req.id into all six call sites, not just
    that call_stage's own check works when given the right id by hand.

    Picks the two ends of the old three-way split: classify (used to crash uncaught,
    late, after every later stage had already run) and check_quality (used to silently
    relabel and let the run complete looking clean). Both must now produce the same
    thing: outcome=ERROR, a single StageError naming the right stage with
    kind=VALIDATION, and -- for classify specifically -- no later stage ever called.
    """
    section("Requirement id mismatch, end to end through run_requirement")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import Classification, FailureKind, RunOutcome, SystemType

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    human_fns = HumanFns(answer_questions=lambda t: [],
                         decide_at_cap=lambda r: (RunOutcome.CAP_STOPPED, "n/a"))

    def never_called(*a, **k):
        raise AssertionError("must never be called -- classify already failed terminally")

    fns_classify = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_B.id, "system_type": "other", "rationale": "r"}]),
        check_quality=never_called, refine_questioner=never_called,
        refine_rewriter=never_called,
        select_strategy=never_called, generate_tests=never_called)
    result = run_requirement(rec(requirement=REQ_A), DOC, None, None, fns_classify, human_fns,
                             throttle, max_revisions=3, stage_configs=STAGE_CONFIGS, max_attempts=1)
    ok("classify wrong id -> outcome=ERROR (was: uncaught crash after later stages ran)",
       result.outcome is RunOutcome.ERROR)
    ok("classify wrong id -> exactly one StageError, kind=VALIDATION",
       len(result.errors) == 1 and result.errors[0].kind is FailureKind.VALIDATION
       and result.errors[0].stage is PipelineStage.CLASSIFIER)
    ok("classify wrong id -> no later stage was ever reached", result.classification is None)

    cls = Classification(requirement_id=REQ_A.id, system_type=SystemType.OTHER, rationale="r")
    fns_quality = StageFns(
        check_consistency=None, map_dependencies=None, classify=None,
        check_quality=Scripted([{"requirement_id": REQ_B.id, "passed": True, "issues": []}]),
        refine_questioner=never_called, refine_rewriter=never_called,
        select_strategy=never_called, generate_tests=never_called)
    result2 = run_requirement(rec(requirement=REQ_A, classification=cls), DOC, None, None,
                              fns_quality, human_fns, throttle, max_revisions=3,
                              stage_configs=STAGE_CONFIGS, max_attempts=1)
    ok("check_quality wrong id -> outcome=ERROR (was: silently relabelled, run completed)",
       result2.outcome is RunOutcome.ERROR)
    ok("check_quality wrong id -> exactly one StageError, kind=VALIDATION",
       len(result2.errors) == 1 and result2.errors[0].kind is FailureKind.VALIDATION
       and result2.errors[0].stage is PipelineStage.QUALITY_CHECKER)
    ok("check_quality wrong id -> no round was recorded (nothing to silently look clean)",
       result2.rounds == [])


def test_backoff_timing() -> None:
    """Backoff fires BETWEEN attempts, never after the last one -- and with the actual
    scheduled delay, not just "some" delay. Every scenario elsewhere stubs
    backoff_seconds to 0.0, so nothing else in the suite would notice if this line
    were deleted entirely (confirmed by mutation: replacing the guard with `if False`
    still passes all other checks). Mirrors test_throttle's discipline of asserting
    the recorded values, not merely that sleep_fn was called."""
    section("Backoff timing (call_stage / call_document_stage)")
    from orchestrator.pipeline import call_stage, call_document_stage, Throttle
    from design.schemas import Classification, DocumentStage, ConsistencyReport

    slept: list[float] = []
    throttle_recording = Throttle(sleep_fn=slept.append, now_fn=lambda: FAKE_NOW)
    fn = Scripted([StageCallFailed("429"), StageCallFailed("429"),
                  {"requirement_id": "R1", "system_type": "web", "rationale": "r"}])
    call_stage(fn, ("R1",), Classification, PipelineStage.CLASSIFIER, "inv-1",
              "fake-model", throttle_recording, [], "R1", max_attempts=3,
              backoff_seconds=lambda a: (a + 1) * 10.0)
    ok("call_stage: backoff fires between attempts, not after the last (or the first)",
       slept == [10.0, 20.0])

    slept2: list[float] = []
    throttle_recording2 = Throttle(sleep_fn=slept2.append, now_fn=lambda: FAKE_NOW)
    doc_fn = Scripted([StageCallFailed("429"), StageCallFailed("429"),
                       {"doc_id": DOC.doc_id, "conflicts": []}])
    call_document_stage(doc_fn, (DOC,), ConsistencyReport, DocumentStage.CONSISTENCY_CHECKER,
                        "inv-2", "fake-model", throttle_recording2, [], DOC.doc_id, max_attempts=3,
                        backoff_seconds=lambda a: (a + 1) * 10.0)
    ok("call_document_stage: backoff fires between attempts, not after the last",
       slept2 == [10.0, 20.0])


def test_document_stages_degraded() -> None:
    """Scenario 7: consistency checker and dependency mapper fail independently; the
    run continues without whichever one failed, per contract D1=b."""
    section("Scenario 7 -- DEGRADED document")
    from orchestrator.pipeline import run_document_stages, Throttle
    from design.schemas import FailureKind

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    stage_fns = StageFns(
        check_consistency=Scripted([StageCallFailed("429"), StageCallFailed("429"),
                                    StageCallFailed("429")]),
        map_dependencies=Scripted([{"doc_id": DOC.doc_id, "dependencies": []}]),
        classify=None, check_quality=None, refine_questioner=None, refine_rewriter=None,
        select_strategy=None,
        generate_tests=None,
    )
    cons, deps, errors, attempts = run_document_stages(
        DOC, STAGE_CONFIGS, stage_fns, throttle, max_attempts=3, backoff_seconds=lambda a: 0.0)
    ok("consistency checker failure leaves consistency_report None", cons is None)
    ok("dependency mapper still succeeds independently", deps is not None)
    ok("exactly one DocumentStageError recorded", len(errors) == 1)
    ok("the error names the failed stage", errors[0].stage.value == "consistency_checker")
    ok("the error's kind is TRANSPORT", errors[0].kind is FailureKind.TRANSPORT)
    ok("the error is linked to its invocation's attempts",
       errors[0].invocation_id in {a.invocation_id for a in attempts})
    ok("dependency mapper's success recorded one attempt", len(attempts) == 4)  # 3 failed + 1 success

    # No inline DocumentOutcome recomputation here: real DocumentOutcome derivation
    # coverage is in test_document_stage_retry_within_run (Task 12), which drives it
    # through the actual run_document code path.


def test_document_id_mismatch_is_validation() -> None:
    """ORCHESTRATOR_CONTRACT.md item 15's sibling at the document level: the
    per-requirement req_id fix was scoped to "all six per-requirement stages" and
    missed that call_document_stage has the identical hole. Verified by construction
    first: a consistency checker returning a doc_id for a completely different
    document was accepted by run_document_stages (errors=[]) and only raised an
    uncaught ValidationError later, at DocumentRunRecord construction -- same shape,
    same silent-until-too-late timing, as the pre-fix per-requirement bug.

    Both document stages (check_consistency, map_dependencies) must now treat a doc_id
    mismatch as FailureKind.VALIDATION, uniformly with the per-requirement fix.

    Also covers the difference from the per-requirement case that has to be decided
    deliberately, not copied blindly: doc_id is Optional on both
    RequirementSet.doc_id and the report models, so a None on EITHER side must NOT be
    treated as a mismatch -- silence is not disagreement, mirroring
    DocumentRunRecord._references_resolve's own doc_id check in design/schemas.py
    (which only fires when both sides are present).
    """
    section("Document id mismatch is uniformly a validation failure (contract item 15)")
    from orchestrator.pipeline import call_document_stage, StageFailed, Throttle
    from design.schemas import ConsistencyReport, DependencyReport, DocumentStage, FailureKind

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)

    cases = [
        ("check_consistency", ConsistencyReport, DocumentStage.CONSISTENCY_CHECKER,
         {"doc_id": "WRONG-DOC", "conflicts": []}),
        ("map_dependencies", DependencyReport, DocumentStage.DEPENDENCY_MAPPER,
         {"doc_id": "WRONG-DOC", "dependencies": []}),
    ]
    for label, model_cls, stage, raw in cases:
        attempts: list = []
        fn = Scripted([raw])
        try:
            call_document_stage(fn, (DOC,), model_cls, stage, "inv-mismatch", "fake-model",
                                throttle, attempts, "REAL-DOC", max_attempts=1,
                                backoff_seconds=lambda a: 0.0)
            ok(f"{label}: doc_id mismatch raises StageFailed", False)
        except StageFailed as f:
            ok(f"{label}: doc_id mismatch raises StageFailed", True)
            ok(f"{label}: kind is VALIDATION, not OTHER or an uncaught crash",
               f.kind is FailureKind.VALIDATION)
        ok(f"{label}: attempt still logged (the call succeeded; the doc_id was just wrong)",
           len(attempts) == 1)

    # Silence is not disagreement: a None on either side must not be flagged.
    attempts_a: list = []
    result_a = call_document_stage(
        Scripted([{"doc_id": "SOME-DOC", "conflicts": []}]), (DOC,), ConsistencyReport,
        DocumentStage.CONSISTENCY_CHECKER, "inv-a", "fake-model", throttle, attempts_a,
        None,  # requirement_set.doc_id is None -- no provenance recorded, not a claim
        max_attempts=1, backoff_seconds=lambda a: 0.0)
    ok("requirement_set.doc_id=None is not a mismatch against any reported doc_id",
       result_a.doc_id == "SOME-DOC")

    attempts_b: list = []
    result_b = call_document_stage(
        Scripted([{"doc_id": None, "conflicts": []}]), (DOC,), ConsistencyReport,
        DocumentStage.CONSISTENCY_CHECKER, "inv-b", "fake-model", throttle, attempts_b,
        "REAL-DOC",  # the model didn't echo a doc_id back -- also not a claim
        max_attempts=1, backoff_seconds=lambda a: 0.0)
    ok("a report with doc_id=None is not a mismatch against any requirement_set.doc_id",
       result_b.doc_id is None)


def test_document_wrong_doc_id_then_success() -> None:
    """Document-level sibling of test_wrong_id_then_success: a doc_id mismatch
    (contract item 15) followed by a correct retry is logged as its own
    VALIDATION_FAILURE attempt, distinct from a malformed-payload validation failure,
    and the eventual success shares its invocation_id -- symmetric with the
    per-requirement case, per CLAUDE.md's "check the twin" rule."""
    section("Document-level wrong doc_id, then a correct retry succeeds")
    from orchestrator.pipeline import call_document_stage, Throttle
    from design.schemas import AttemptResult, ConsistencyReport, DocumentStage

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    attempts: list = []
    fn = Scripted([
        {"doc_id": "WRONG-DOC", "conflicts": []},
        {"doc_id": DOC.doc_id, "conflicts": []},
    ])
    result = call_document_stage(fn, (DOC,), ConsistencyReport,
                                 DocumentStage.CONSISTENCY_CHECKER, "inv-1", "fake-model",
                                 throttle, attempts, DOC.doc_id, max_attempts=2,
                                 backoff_seconds=lambda a: 0.0)
    ok("the retry succeeds", isinstance(result, ConsistencyReport))
    ok("two attempts logged under the same invocation_id",
       len(attempts) == 2 and {a.invocation_id for a in attempts} == {"inv-1"})
    ok("the first attempt is a validation failure naming the wrong doc_id",
       attempts[0].result is AttemptResult.VALIDATION_FAILURE
       and "WRONG-DOC" in attempts[0].error_message)
    ok("the second attempt succeeded", attempts[1].result is AttemptResult.SUCCESS)


def test_throttle() -> None:
    """Scenario: the throttle enforces a minimum interval PER MODEL, and asserts the
    actual recorded delay -- not just that a delay happened -- since a no-op sleep_fn
    makes it easy to test that retries occur without testing that backoff is correct."""
    section("Throttle")
    from orchestrator.pipeline import Throttle

    slept: list[float] = []
    clock = [0.0]

    def fake_now():
        return datetime(2026, 1, 1, tzinfo=timezone.utc).fromtimestamp(
            clock[0], tz=timezone.utc)

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += seconds

    throttle = Throttle(sleep_fn=fake_sleep, now_fn=fake_now,
                        min_interval_seconds={"cheap-model": 10.0, "strong-model": 0.0})

    throttle.wait_for_slot("cheap-model")
    ok("first call on a model never waits", slept == [])

    clock[0] += 3.0  # only 3s elapsed, need 10s
    throttle.wait_for_slot("cheap-model")
    ok("second call within the interval sleeps exactly the remainder", slept == [7.0])

    throttle.wait_for_slot("strong-model")
    ok("a model with 0.0 min_interval never waits", slept == [7.0])

    slept.clear()
    clock[0] += 100.0  # plenty of time passed
    throttle.wait_for_slot("cheap-model")
    ok("a call after the interval has elapsed does not wait", slept == [])


def test_on_disk_round_trip() -> None:
    """Contract items 9 and 10: document.json is written with requirement_records=[],
    each requirement gets its own file, and everything re-validates before persisting."""
    section("On-disk layout round trip")
    from orchestrator.pipeline import write_document_run, write_requirement_run, read_document_run
    from design.schemas import DocumentOutcome, RunOutcome

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        metadata = make_metadata()
        record = DocumentRunRecord(requirement_set=DOC, metadata=metadata,
                                   outcome=DocumentOutcome.IN_PROGRESS)
        write_document_run(tmp_path, record)
        ok("document.json was written", (tmp_path / "document.json").exists())

        on_disk_raw = (tmp_path / "document.json").read_text()
        ok("requirement_records is empty on disk",
           '"requirement_records": []' in on_disk_raw)

        req_record = rec(requirement=REQ_A, outcome=RunOutcome.IN_PROGRESS)
        write_requirement_run(tmp_path, req_record)
        ok("the requirement file was written",
           (tmp_path / "requirements" / f"{REQ_A.id}.json").exists())

        reloaded = read_document_run(tmp_path)
        ok("reloaded document keeps its metadata", reloaded.metadata.run_id == metadata.run_id)
        ok("reloaded document reassembles requirement_records",
           [r.requirement.id for r in reloaded.requirement_records] == [REQ_A.id])

        # JSON persistence and reload of the attempt log itself, including the direct
        # invocation_id link between an error and its attempts (rev 2).
        cls_err, cls_attempts = failed_stage(PipelineStage.CLASSIFIER, "inv-json", attempts=2)
        req_record_with_attempts = rec(requirement=REQ_A, outcome=RunOutcome.ERROR,
                                       errors=[cls_err], attempts=cls_attempts)
        write_requirement_run(tmp_path, req_record_with_attempts)
        reloaded_after = read_document_run(tmp_path)
        reloaded_req = next(r for r in reloaded_after.requirement_records if r.requirement.id == REQ_A.id)
        ok("attempts survive a JSON round trip", len(reloaded_req.attempts) == 2)
        ok("the error's invocation_id still links to its attempts after reload",
           reloaded_req.errors[0].invocation_id
           == {a.invocation_id for a in reloaded_req.attempts}.pop())
        ok("attempt_number and result survive the round trip",
           [a.attempt_number for a in reloaded_req.attempts] == [1, 2]
           and reloaded_req.attempts[-1].result.value == "transport_failure")


def test_happy_path() -> None:
    """Scenario 1: one clean requirement, one refined-once requirement, straight
    through all 7 stages. Explicitly asserts contract item 2: the Requirement handed
    to stage 3/4 has .text equal to the original (clean) or refined_text (refined) --
    never read from record.final_text."""
    section("Scenario 1 -- happy path, both paths converge (contract item 2)")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import RunOutcome

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    strategy_calls = []
    generate_calls = []

    def select_strategy(req, *a):
        strategy_calls.append(req.text)
        return StageCallResult(raw={"requirement_id": req.id, "system_type": "other",
                                    "techniques": ["boundary_value_analysis"], "rationale": "r"},
                               prompt_tokens=10, completion_tokens=5)

    def generate_tests(req, *a):
        generate_calls.append(req.text)
        return StageCallResult(raw={"requirement_id": req.id, "test_cases": [{
            "id": f"TC-{req.id}-1", "requirement_ids": [req.id],
            "technique_used": "boundary_value_analysis", "title": "t", "steps": ["s"],
            "expected_result": "e"}]}, prompt_tokens=10, completion_tokens=5)

    # -- Clean path: passes on the first quality check --
    clean_fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([{"requirement_id": REQ_A.id, "passed": True, "issues": []}]),
        refine_questioner=None, refine_rewriter=None,
        select_strategy=select_strategy, generate_tests=generate_tests)
    human_fns = HumanFns(answer_questions=lambda turn: [], decide_at_cap=lambda rec: (None, None))
    clean_record = run_requirement(
        rec(requirement=REQ_A), DOC, None, None, clean_fns, human_fns, throttle,
        max_revisions=3, stage_configs=STAGE_CONFIGS)
    ok("clean path completes", clean_record.outcome is RunOutcome.COMPLETED)
    ok("clean path never refined", clean_record.final_requirement is None)
    ok("stage 3/4 saw the original text (contract item 2)",
       strategy_calls == [REQ_A.text] and generate_calls == [REQ_A.text])

    # -- Refined path: fails once, one round of Q&A, then passes --
    refined_text = "Temperatures within the valid range defined in DOC-REQ-B shall be output."
    strategy_calls.clear(); generate_calls.clear()
    refined_fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([
            {"requirement_id": REQ_A.id, "passed": False, "issues": [{
                "id": "I1", "category": "vague_pronoun", "span": "these limits",
                "explanation": "Unresolved referent."}]},
            {"requirement_id": REQ_A.id, "passed": True, "issues": []},
        ]),
        refine_questioner=Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
                "id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun",
                "question_text": "What limits?"}]},
        ]),
        refine_rewriter=Scripted([
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text,
             "refined_text": refined_text, "revision_number": 1,
             "answers_used": [{"question_id": "Q1", "answer_text": "DOC-REQ-B's range."}]},
        ]),
        select_strategy=select_strategy, generate_tests=generate_tests)
    human_fns2 = HumanFns(
        answer_questions=lambda turn: [RefinerAnswer(question_id="Q1", answer_text="DOC-REQ-B's range.")],
        decide_at_cap=lambda rec: (None, None))
    refined_record = run_requirement(
        rec(requirement=REQ_A), DOC, None, None, refined_fns, human_fns2, throttle,
        max_revisions=3, stage_configs=STAGE_CONFIGS)
    ok("refined path completes", refined_record.outcome is RunOutcome.COMPLETED)
    ok("refined path recorded exactly one rewrite",
       refined_record.final_requirement is not None
       and refined_record.final_requirement.refined_text == refined_text)
    ok("stage 3/4 saw the refined text, not the original (contract item 2)",
       strategy_calls == [refined_text] and generate_calls == [refined_text])
    ok("questioner and rewriter attempts are attributed to their own stage, not shared",
       sum(1 for a in refined_record.attempts if a.stage is PipelineStage.REFINER_QUESTIONER) == 1
       and sum(1 for a in refined_record.attempts if a.stage is PipelineStage.REFINER_REWRITER) == 1)
    ok("the questioner and rewriter calls got distinct invocation ids",
       len({a.invocation_id for a in refined_record.attempts
            if a.stage in (PipelineStage.REFINER_QUESTIONER, PipelineStage.REFINER_REWRITER)}) == 2)
    qc_invocation_ids = [a.invocation_id for a in refined_record.attempts
                         if a.stage is PipelineStage.QUALITY_CHECKER]
    ok("Quality Checker round 1 and round 2 are distinct invocations, each attributed",
       len(qc_invocation_ids) == 2 and qc_invocation_ids[0] != qc_invocation_ids[1])


def test_refiner_questioner_and_rewriter_have_independent_configs() -> None:
    """The REFINER_QUESTIONER/REFINER_REWRITER split exists so the two calls can be
    configured independently -- different model, different prompt (and so a different
    prompt_hash). Proven here by actually driving two different model names through
    Throttle (keyed per model, see Throttle's docstring) and confirming both were used,
    rather than trusting that two StageConfig entries merely exist unused."""
    section("REFINER_QUESTIONER / REFINER_REWRITER have independent model configs")
    from orchestrator.pipeline import run_requirement, Throttle

    configs = dict(STAGE_CONFIGS)
    configs[PipelineStage.REFINER_QUESTIONER.value] = StageConfig(
        model="question-model", prompt_hash=prompt_fingerprint("ask a clarifying question"))
    configs[PipelineStage.REFINER_REWRITER.value] = StageConfig(
        model="rewrite-model", prompt_hash=prompt_fingerprint("rewrite the requirement"))
    ok("the two stages were given different models and different prompt hashes",
       configs[PipelineStage.REFINER_QUESTIONER.value].model
       != configs[PipelineStage.REFINER_REWRITER.value].model
       and configs[PipelineStage.REFINER_QUESTIONER.value].prompt_hash
       != configs[PipelineStage.REFINER_REWRITER.value].prompt_hash)

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([
            {"requirement_id": REQ_A.id, "passed": False, "issues": [{
                "id": "I1", "category": "vague_pronoun", "span": "these limits",
                "explanation": "e"}]},
            {"requirement_id": REQ_A.id, "passed": True, "issues": []},
        ]),
        refine_questioner=Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
                "id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun",
                "question_text": "?"}]},
        ]),
        refine_rewriter=Scripted([
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text, "refined_text": T1,
             "revision_number": 1, "answers_used": [{"question_id": "Q1", "answer_text": "a"}]},
        ]),
        select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                   "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
        generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
            "id": "TC-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
            "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
    human_fns = HumanFns(
        answer_questions=lambda turn: [RefinerAnswer(question_id="Q1", answer_text="a")],
        decide_at_cap=lambda rec: (RunOutcome.CAP_STOPPED, "n/a"))
    result = run_requirement(rec(requirement=REQ_A), DOC, None, None, fns, human_fns, throttle,
                             max_revisions=3, stage_configs=configs)
    ok("the run completes using the two independently-configured stages",
       result.outcome is RunOutcome.COMPLETED)
    ok("both configured models actually reached the throttle (not just declared, unused)",
       {"question-model", "rewrite-model"} <= set(throttle.last_call_at))


def test_refine_questioner_failure_and_retry() -> None:
    """Failure and retry attributed to REFINER_QUESTIONER specifically: a transient
    failure that succeeds on retry must not touch the rewriter or the human at all
    (nothing to rewrite or answer until a turn exists); exhaustion must record a
    StageError naming REFINER_QUESTIONER, not the old shared REFINER."""
    section("REFINER_QUESTIONER failure and retry")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import FailureKind

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)

    def never_called(*a, **k):
        raise AssertionError("must never be called -- questioner has not produced a turn")

    failing_quality = lambda: Scripted([{"requirement_id": REQ_A.id, "passed": False, "issues": [{
        "id": "I1", "category": "vague_pronoun", "span": "these limits", "explanation": "e"}]}])

    # -- transient failure, retry succeeds --
    fns_retry = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([
            {"requirement_id": REQ_A.id, "passed": False, "issues": [{
                "id": "I1", "category": "vague_pronoun", "span": "these limits", "explanation": "e"}]},
            {"requirement_id": REQ_A.id, "passed": True, "issues": []},
        ]),
        refine_questioner=Scripted([
            StageCallFailed("429"),
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
                "id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun",
                "question_text": "?"}]},
        ]),
        refine_rewriter=Scripted([
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text, "refined_text": T1,
             "revision_number": 1, "answers_used": [{"question_id": "Q1", "answer_text": "a"}]},
        ]),
        select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                   "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
        generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
            "id": "TC-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
            "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
    answer_calls: list = []
    human_fns_retry = HumanFns(
        answer_questions=lambda turn: answer_calls.append(turn) or [
            RefinerAnswer(question_id="Q1", answer_text="a")],
        decide_at_cap=lambda r: (RunOutcome.CAP_STOPPED, "n/a"))
    result = run_requirement(rec(requirement=REQ_A), DOC, None, None, fns_retry, human_fns_retry,
                             throttle, max_revisions=3, stage_configs=STAGE_CONFIGS,
                             backoff_seconds=lambda a: 0.0)
    ok("a transient questioner failure that retries successfully still completes",
       result.outcome is RunOutcome.COMPLETED)
    ok("the human was asked exactly once, only after the retry succeeded",
       len(answer_calls) == 1)
    ok("no error is recorded for a retry that ultimately succeeded", result.errors == [])

    # -- exhaustion --
    fns_exhaust = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=failing_quality(),
        refine_questioner=Scripted([StageCallFailed("429"), StageCallFailed("429")]),
        refine_rewriter=never_called, select_strategy=never_called, generate_tests=never_called)
    human_fns_exhaust = HumanFns(answer_questions=never_called,
                                 decide_at_cap=lambda r: (RunOutcome.CAP_STOPPED, "n/a"))
    result2 = run_requirement(rec(requirement=REQ_A), DOC, None, None, fns_exhaust,
                              human_fns_exhaust, throttle, max_revisions=3,
                              stage_configs=STAGE_CONFIGS, max_attempts=2,
                              backoff_seconds=lambda a: 0.0)
    ok("exhausting the questioner's retries produces outcome=ERROR",
       result2.outcome is RunOutcome.ERROR)
    ok("exactly one StageError naming REFINER_QUESTIONER, not the old shared REFINER",
       len(result2.errors) == 1 and result2.errors[0].stage is PipelineStage.REFINER_QUESTIONER
       and result2.errors[0].kind is FailureKind.TRANSPORT
       and result2.errors[0].retry_count == 1)
    ok("the rewriter and the human were never reached", result2.rounds[0].turn is None)


def test_refine_rewriter_failure_and_retry() -> None:
    """Mirror of test_refine_questioner_failure_and_retry for REFINER_REWRITER: the
    questioner and the human answer must already have happened (a rewrite always
    follows an answered turn) before the rewriter can fail at all; failure and retry
    must attribute to REFINER_REWRITER specifically, distinct from the questioner."""
    section("REFINER_REWRITER failure and retry")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import FailureKind

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)

    def never_called(*a, **k):
        raise AssertionError("must never be called")

    # -- transient failure, retry succeeds --
    fns_retry = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([
            {"requirement_id": REQ_A.id, "passed": False, "issues": [{
                "id": "I1", "category": "vague_pronoun", "span": "these limits", "explanation": "e"}]},
            {"requirement_id": REQ_A.id, "passed": True, "issues": []},
        ]),
        refine_questioner=Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
                "id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun",
                "question_text": "?"}]},
        ]),
        refine_rewriter=Scripted([
            StageCallFailed("429"),
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text, "refined_text": T1,
             "revision_number": 1, "answers_used": [{"question_id": "Q1", "answer_text": "a"}]},
        ]),
        select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                   "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
        generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
            "id": "TC-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
            "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
    human_fns_retry = HumanFns(
        answer_questions=lambda turn: [RefinerAnswer(question_id="Q1", answer_text="a")],
        decide_at_cap=lambda r: (RunOutcome.CAP_STOPPED, "n/a"))
    result = run_requirement(rec(requirement=REQ_A), DOC, None, None, fns_retry, human_fns_retry,
                             throttle, max_revisions=3, stage_configs=STAGE_CONFIGS,
                             backoff_seconds=lambda a: 0.0)
    ok("a transient rewriter failure that retries successfully still completes",
       result.outcome is RunOutcome.COMPLETED)
    ok("no error is recorded for a retry that ultimately succeeded", result.errors == [])

    # -- exhaustion --
    fns_exhaust = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([
            {"requirement_id": REQ_A.id, "passed": False, "issues": [{
                "id": "I1", "category": "vague_pronoun", "span": "these limits", "explanation": "e"}]},
        ]),
        refine_questioner=Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
                "id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun",
                "question_text": "?"}]},
        ]),
        refine_rewriter=Scripted([StageCallFailed("429"), StageCallFailed("429")]),
        select_strategy=never_called, generate_tests=never_called)
    human_fns_exhaust = HumanFns(
        answer_questions=lambda turn: [RefinerAnswer(question_id="Q1", answer_text="a")],
        decide_at_cap=lambda r: (RunOutcome.CAP_STOPPED, "n/a"))
    result2 = run_requirement(rec(requirement=REQ_A), DOC, None, None, fns_exhaust,
                              human_fns_exhaust, throttle, max_revisions=3,
                              stage_configs=STAGE_CONFIGS, max_attempts=2,
                              backoff_seconds=lambda a: 0.0)
    ok("exhausting the rewriter's retries produces outcome=ERROR",
       result2.outcome is RunOutcome.ERROR)
    ok("exactly one StageError naming REFINER_REWRITER, not the old shared REFINER",
       len(result2.errors) == 1 and result2.errors[0].stage is PipelineStage.REFINER_REWRITER
       and result2.errors[0].kind is FailureKind.TRANSPORT
       and result2.errors[0].retry_count == 1)
    ok("the turn and answers survived the failed rewrite (nothing lost, resumable)",
       result2.rounds[0].turn is not None and result2.rounds[0].answers != []
       and result2.rounds[0].rewrite is None)


def test_revision_cap() -> None:
    """Scenario 2: quality check fails every round up to the cap; decide_at_cap is
    invoked; both CAP_GENERATED and CAP_STOPPED produce the matching outcome."""
    section("Scenario 2 -- revision cap")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import RunOutcome

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)

    def always_fails_quality():
        return Scripted([{"requirement_id": REQ_A.id, "passed": False, "issues": [{
            "id": "I1", "category": "vague_pronoun", "span": "these limits",
            "explanation": "Unresolved."}]}] * 5)

    def question_forever():
        return Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
                "id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun",
                "question_text": "?"}]},
        ] * 5)

    def rewrite_forever():
        return Scripted([
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text, "refined_text": T1,
             "revision_number": 1,
             "answers_used": [{"question_id": "Q1", "answer_text": "a"}]},
        ] * 5)

    for decision, expected in ((RunOutcome.CAP_GENERATED, RunOutcome.CAP_GENERATED),
                               (RunOutcome.CAP_STOPPED, RunOutcome.CAP_STOPPED)):
        fns = StageFns(
            check_consistency=None, map_dependencies=None,
            classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
            check_quality=always_fails_quality(), refine_questioner=question_forever(),
            refine_rewriter=rewrite_forever(),
            select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                       "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
            generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
                "id": "TC-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
                "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
        human_fns = HumanFns(
            answer_questions=lambda turn: [RefinerAnswer(question_id="Q1", answer_text="a")],
            decide_at_cap=lambda rec, d=decision: (d, f"cap reached, chose {d.value}"))
        result = run_requirement(rec(requirement=REQ_A), DOC, None, None, fns, human_fns,
                                 throttle, max_revisions=2, stage_configs=STAGE_CONFIGS)
        ok(f"cap decision {decision.value} produces outcome {expected.value}",
           result.outcome is expected)
        ok(f"{decision.value} record has a cap_reason", result.cap_reason is not None)

    ok("decide_at_cap returning a nonsense outcome raises ValueError",
       _cap_returns_nonsense_raises())


def _cap_returns_nonsense_raises() -> bool:
    """max_revisions=2, not 1: run_requirement now rejects max_revisions < 2 outright
    (Important finding #4a from task review -- a cap can only be reached after at
    least one refinement attempt, which max_revisions=1 could never produce), so this
    needs a real two-round cap to reach decide_at_cap at all. Round 2's issue matches
    round 1's identity, reconciling back to "I1" -- no fresh id involved, keeping this
    test focused on the decide_at_cap guard, not id reconciliation.
    """
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import RunOutcome
    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([
            {"requirement_id": REQ_A.id, "passed": False, "issues": [{
                "id": "I1", "category": "vague_pronoun", "span": "x", "explanation": "e"}]},
            {"requirement_id": REQ_A.id, "passed": False, "issues": [{
                "id": "I1", "category": "vague_pronoun", "span": "x", "explanation": "still there"}]},
        ]),
        refine_questioner=Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
                "id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun", "question_text": "?"}]},
        ]),
        refine_rewriter=Scripted([
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text, "refined_text": T1,
             "revision_number": 1, "answers_used": [{"question_id": "Q1", "answer_text": "a"}]},
        ]),
        select_strategy=None, generate_tests=None)
    human_fns = HumanFns(answer_questions=lambda turn: [RefinerAnswer(question_id="Q1", answer_text="a")],
                         decide_at_cap=lambda rec: (RunOutcome.COMPLETED, "nonsense"))
    try:
        run_requirement(rec(requirement=REQ_A), DOC, None, None, fns, human_fns, throttle,
                        max_revisions=2, stage_configs=STAGE_CONFIGS)
        return False
    except ValueError as e:
        # Distinguishes the intended guard from the (also ValueError) max_revisions
        # guard or an incidental schema ValidationError (also a ValueError subclass).
        return "decide_at_cap" in str(e)


def test_issue_identity_reuse() -> None:
    """Scenario 3: two rounds produce an issue at the same (category, span); the
    orchestrator reuses the same Issue.id, per contract item 4.

    Round 2's scripted RefinerTurn question deliberately references "FRESH-1", the
    id the orchestrator reconciled round 1's issue to -- not the fresh "FRESH-2" id the
    round 2 Quality Checker call minted. A real (LLM) refiner is shown the *already
    reconciled* QualityReport (see _run_refine_loop: the refine call's `quality_report`
    argument is the reconciled one, built before the refine call happens), so it would
    naturally echo "FRESH-1" back; a Scripted mock has to be told to do the same, or the
    resulting RefinementRound fails RefinementRound._round_is_coherent's check that
    every question's issue_id names an issue actually present in that round's
    quality_report -- which after reconciliation no longer contains "FRESH-2" at all.
    """
    section("Scenario 3 -- issue identity across rounds")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import RunOutcome

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([
            {"requirement_id": REQ_A.id, "passed": False, "issues": [
                {"id": "FRESH-1", "category": "vague_pronoun", "span": "these limits",
                 "explanation": "e1"}]},
            {"requirement_id": REQ_A.id, "passed": False, "issues": [
                {"id": "FRESH-2", "category": "vague_pronoun", "span": "these limits",
                 "explanation": "e1, still there"}]},
            {"requirement_id": REQ_A.id, "passed": True, "issues": []},
        ]),
        refine_questioner=Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
                "id": "Q1", "issue_id": "FRESH-1", "issue_category": "vague_pronoun",
                "question_text": "?"}]},
            {"requirement_id": REQ_A.id, "revision_number": 2, "questions": [{
                "id": "Q2", "issue_id": "FRESH-1", "issue_category": "vague_pronoun",
                "question_text": "?"}]},
        ]),
        refine_rewriter=Scripted([
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text, "refined_text": T1,
             "revision_number": 1, "answers_used": [{"question_id": "Q1", "answer_text": "a"}]},
            {"requirement_id": REQ_A.id, "original_text": T1, "refined_text": T1 + " ",
             "revision_number": 2, "answers_used": [{"question_id": "Q2", "answer_text": "a"}]},
        ]),
        select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                   "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
        generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
            "id": "TC-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
            "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
    human_fns = HumanFns(
        answer_questions=lambda turn: [RefinerAnswer(question_id=turn.questions[0].id, answer_text="a")],
        decide_at_cap=lambda rec: (RunOutcome.CAP_STOPPED, "n/a"))
    result = run_requirement(rec(requirement=REQ_A), DOC, None, None, fns, human_fns, throttle,
                             max_revisions=3, stage_configs=STAGE_CONFIGS)
    ids_seen = [r.quality_report.issues[0].id for r in result.rounds if r.quality_report.issues]
    ok("the LLM's fresh ids were replaced with the reused id",
       ids_seen == ["FRESH-1", "FRESH-1"])


def test_suppression_persists() -> None:
    """Scenario 4: user_confirms_resolved=True on one round's answer is present in
    suppressed_issue_ids on every LATER round, not just the next one."""
    section("Scenario 4 -- suppression persists across rounds")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import RunOutcome

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([
            {"requirement_id": REQ_A.id, "passed": False, "issues": [
                {"id": "I1", "category": "vague_pronoun", "span": "these limits", "explanation": "e"},
                {"id": "I2", "category": "non_verifiable", "span": "subsequent processing",
                 "explanation": "e2"}]},
            # I1 suppressed by the human after round 1; only I2 should still show up.
            {"requirement_id": REQ_A.id, "passed": False, "issues": [
                {"id": "I2b", "category": "non_verifiable", "span": "subsequent processing",
                 "explanation": "e2 still there"}]},
            {"requirement_id": REQ_A.id, "passed": True, "issues": []},
        ]),
        refine_questioner=Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [
                {"id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun", "question_text": "?"},
                {"id": "Q2", "issue_id": "I2", "issue_category": "non_verifiable", "question_text": "?"}]},
            {"requirement_id": REQ_A.id, "revision_number": 2, "questions": [
                {"id": "Q3", "issue_id": "I2", "issue_category": "non_verifiable", "question_text": "?"}]},
        ]),
        refine_rewriter=Scripted([
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text, "refined_text": T1,
             "revision_number": 1, "answers_used": [
                 {"question_id": "Q1", "answer_text": "confirmed fine", "user_confirms_resolved": True},
                 {"question_id": "Q2", "answer_text": "a"}]},
            {"requirement_id": REQ_A.id, "original_text": T1, "refined_text": T1 + " ",
             "revision_number": 2, "answers_used": [{"question_id": "Q3", "answer_text": "a"}]},
        ]),
        select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                   "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
        generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
            "id": "TC-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
            "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
    human_fns = HumanFns(
        answer_questions=lambda turn: [
            RefinerAnswer(question_id=q.id, answer_text="confirmed fine" if q.issue_id == "I1" else "a",
                         user_confirms_resolved=(q.issue_id == "I1"))
            for q in turn.questions],
        decide_at_cap=lambda rec: (RunOutcome.CAP_STOPPED, "n/a"))
    result = run_requirement(rec(requirement=REQ_A), DOC, None, None, fns, human_fns, throttle,
                             max_revisions=3, stage_configs=STAGE_CONFIGS)
    ok("round 2 suppresses I1", "I1" in result.rounds[1].suppressed_issue_ids)
    ok("round 3 (final passing round) still suppresses I1",
       "I1" in result.rounds[2].suppressed_issue_ids)


def test_resume_skips_finished_refine_loop() -> None:
    """Regression test for a bug found while implementing run_requirement (not one of
    the four scenarios in the harness design doc): resume_at can legitimately return
    STRATEGY_SELECTOR or TEST_GENERATOR for a record whose refine loop already finished
    (e.g. retrying after the Strategy Selector hit a rate limit). run_requirement must
    NOT re-enter _run_refine_loop in that case -- the last round already passed and so
    has no rewrite (RefinementRound forbids a rewrite on a passed round), and the loop's
    fresh-round branch unconditionally reads `rounds[-1].rewrite.refined_text`, which
    would raise AttributeError on a resumed, already-finished record.
    """
    section("Regression -- resuming at STRATEGY_SELECTOR must not re-run the refine loop")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import Classification, RunOutcome, SystemType

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    cls = Classification(requirement_id=REQ_A.id, system_type=SystemType.OTHER, rationale="r")
    passed_round = mk_round(1, REQ_A.text, passed=True)
    resuming_record = rec(requirement=REQ_A, classification=cls, rounds=[passed_round])
    ok("fixture actually resumes at the strategy selector (sanity check)",
       resume_at(resuming_record) is PipelineStage.STRATEGY_SELECTOR)

    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=None, check_quality=None,
        refine_questioner=None, refine_rewriter=None,  # must never be called
        select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                   "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
        generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
            "id": "TC-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
            "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
    human_fns = HumanFns(answer_questions=lambda turn: [],
                         decide_at_cap=lambda r: (RunOutcome.CAP_STOPPED, "n/a"))

    def _run():
        return run_requirement(resuming_record, DOC, None, None, fns, human_fns, throttle,
                               max_revisions=3, stage_configs=STAGE_CONFIGS)

    try:
        result = _run()
        ok("resuming at the strategy selector does not crash", True)
        ok("resuming at the strategy selector still completes", result.outcome is RunOutcome.COMPLETED)
    except AttributeError:
        ok("resuming at the strategy selector does not crash", False)
        ok("resuming at the strategy selector still completes", False)


def test_resume_mid_round_completes() -> None:
    """Regression, and one of two scenarios the REFINER_QUESTIONER/REFINER_REWRITER
    split's resume position (REFINER_REWRITER) covers: resuming INSIDE an unfinished
    round where the human has ALREADY answered, but the rewrite never happened (e.g.
    the process died between the human's answer and the Refiner Rewriter's call, or the
    rewriter call itself failed and this is a later retry). resume_at already covers the
    *position* (test_resume_positions: "questioner done, rewrite outstanding" ->
    REFINER_REWRITER -- note this position does NOT by itself mean the human has
    answered; see the sibling case below); this covers _run_refine_loop actually
    resuming from that state and finishing, which is structurally distinct from the
    other resume cases already covered:
      - test_resume_skips_finished_refine_loop -> resuming PAST the loop entirely
      - the already-capped resume case (see the comment above the pending_round branch
        in _run_refine_loop) -> resuming AT an already-capped round
      - test_resume_mid_round_asks_human_when_answers_missing -> resuming at
        REFINER_REWRITER where the turn exists but answers is still empty (the human
        has NOT answered yet) -- this test's sibling and the case this one must NOT
        cover, so the two together pin both halves of the REFINER_REWRITER position
      - this one -> resuming INSIDE a round with a turn AND answers already recorded,
        neither finished nor capped

    Confirmed by code trace before writing this test: _run_refine_loop's pending_round
    branch sets `turn = pending_round.turn` (non-None) and `answers =
    pending_round.answers` (non-empty, from mk_round), so neither the `if turn is
    None:` branch (fresh turn) nor the `elif not answers:` branch (ask the human) fires
    -- only the rewriter call runs, using the round's existing answers. refine_questioner
    is wired to a function that raises if called at all, and answer_questions records
    every call, making both "not re-run" guarantees assertions rather than inferences
    from "only one value was scripted."
    """
    section("Regression -- resuming inside an unfinished round (turn asked, rewrite outstanding)")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import Classification, RunOutcome, SystemType

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    cls = Classification(requirement_id=REQ_A.id, system_type=SystemType.OTHER, rationale="r")
    pending = mk_round(1, T0, passed=False)  # turn asked, answered; rewrite=None
    resuming_record = rec(requirement=REQ_A, classification=cls, rounds=[pending])
    ok("fixture actually resumes at the refiner rewriter (sanity check)",
       resume_at(resuming_record) is PipelineStage.REFINER_REWRITER)

    def never_called(*a, **k):
        raise AssertionError("refine_questioner must never be called -- turn already exists")

    answer_calls: list = []
    def answer_questions(turn):
        answer_calls.append(turn)
        return [RefinerAnswer(question_id=q.id, answer_text="new answer") for q in turn.questions]

    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=None,  # must never be called -- classification already exists
        check_quality=Scripted([{"requirement_id": REQ_A.id, "passed": True, "issues": []}]),
        refine_questioner=never_called,  # must never be called -- turn already exists
        refine_rewriter=Scripted([
            {"requirement_id": REQ_A.id, "original_text": T0, "refined_text": T1,
             "revision_number": 1, "answers_used": [
                 {"question_id": "Q1", "answer_text": "answer"}]},
        ]),
        select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                   "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
        generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
            "id": "TC-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
            "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
    human_fns = HumanFns(answer_questions=answer_questions,
                         decide_at_cap=lambda r: (RunOutcome.CAP_STOPPED, "n/a"))

    result = run_requirement(resuming_record, DOC, None, None, fns, human_fns, throttle,
                             max_revisions=3, stage_configs=STAGE_CONFIGS)

    ok("human is not re-asked for a question already answered", answer_calls == [])
    completed_round = result.rounds[0]
    ok("the outstanding rewrite was completed rather than crashing",
       completed_round.rewrite is not None)
    ok("rewrite.original_text matches the round's own text_checked",
       completed_round.rewrite is not None
       and completed_round.rewrite.original_text == completed_round.text_checked)
    ok("rewrite.answers_used matches the round's own pre-existing answers",
       completed_round.rewrite is not None
       and completed_round.rewrite.answers_used == completed_round.answers)
    ok("the run reaches a terminal outcome", result.outcome is RunOutcome.COMPLETED)


def test_resume_mid_round_asks_human_when_answers_missing() -> None:
    """Regression for a gap in the original REFINER_QUESTIONER/REFINER_REWRITER split:
    a schema-valid RefinementRound can have `turn` present, `answers` empty, and
    `rewrite` missing -- interruption after the questioner produced a turn but before
    the human answered it (RefinementRound only rejects `answers` non-empty with
    `turn is None`, never the reverse). resume_at correctly reports REFINER_REWRITER
    for this state (the questioner is done), but the first version of _run_refine_loop
    kept `if turn is None:` as the ONLY place that called human_fns.answer_questions --
    so a turn-but-no-answers round skipped asking the human entirely and handed the
    rewriter an empty answers list. Fixed by asking iff `not answers`, independent of
    `turn`.

    Sibling of test_resume_mid_round_completes, which covers the OTHER half of the
    REFINER_REWRITER position (turn AND answers already present -- must NOT re-ask).
    Together the two pin both meanings of "resume at REFINER_REWRITER."
    """
    section("Regression -- resuming at REFINER_REWRITER with turn asked but not yet answered")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import Classification, Issue, IssueCategory, RunOutcome, SystemType

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    cls = Classification(requirement_id=REQ_A.id, system_type=SystemType.OTHER, rationale="r")
    issue = Issue(id="I1", category=IssueCategory.VAGUE_PRONOUN, span="these limits",
                 explanation="Unresolved referent.")
    turn = RefinerTurn(requirement_id=REQ_A.id, revision_number=1, questions=[
        ClarifyingQuestion(id="Q1", issue_id="I1", issue_category=IssueCategory.VAGUE_PRONOUN,
                           question_text="Which limits?")])
    # turn present, answers empty, rewrite missing -- schema-valid (see docstring).
    pending = RefinementRound(revision_number=1, text_checked=T0,
                              quality_report=QualityReport(requirement_id=REQ_A.id, passed=False,
                                                           issues=[issue]),
                              turn=turn)
    resuming_record = rec(requirement=REQ_A, classification=cls, rounds=[pending])
    ok("fixture actually resumes at the refiner rewriter (sanity check)",
       resume_at(resuming_record) is PipelineStage.REFINER_REWRITER)
    ok("fixture sanity: turn is present but answers is empty",
       pending.turn is not None and pending.answers == [])

    def questioner_never_called(*a, **k):
        raise AssertionError("refine_questioner must never be called -- turn already exists")

    answer_calls: list = []
    def answer_questions(t):
        answer_calls.append(t)
        return [RefinerAnswer(question_id=q.id, answer_text="new answer") for q in t.questions]

    rewriter = Scripted([
        {"requirement_id": REQ_A.id, "original_text": T0, "refined_text": T1,
         "revision_number": 1, "answers_used": [{"question_id": "Q1", "answer_text": "new answer"}]},
    ])
    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=None,  # must never be called -- classification already exists
        check_quality=Scripted([{"requirement_id": REQ_A.id, "passed": True, "issues": []}]),
        refine_questioner=questioner_never_called,
        refine_rewriter=rewriter,
        select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                   "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
        generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
            "id": "TC-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
            "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
    human_fns = HumanFns(answer_questions=answer_questions,
                         decide_at_cap=lambda r: (RunOutcome.CAP_STOPPED, "n/a"))

    result = run_requirement(resuming_record, DOC, None, None, fns, human_fns, throttle,
                             max_revisions=3, stage_configs=STAGE_CONFIGS)

    ok("the human was asked exactly once", len(answer_calls) == 1)
    ok("the human was asked with the pre-existing turn, not a freshly-generated one",
       answer_calls == [turn])
    completed_round = result.rounds[0]
    ok("the rewriter received the answers the human just gave",
       len(rewriter.calls) == 1
       and rewriter.calls[0][1] == [RefinerAnswer(question_id="Q1", answer_text="new answer")])
    ok("the outstanding rewrite was completed", completed_round.rewrite is not None)
    ok("the round's recorded answers are the ones the human gave, not left empty",
       completed_round.answers == [RefinerAnswer(question_id="Q1", answer_text="new answer")])
    ok("the run reaches a terminal outcome", result.outcome is RunOutcome.COMPLETED)


def test_id_reconciliation_mints_fresh_ids_on_collision() -> None:
    """Regression for task review finding Important #1: _reconcile_issue_ids must mint
    a fresh, orchestrator-owned id for anything that does NOT match the previous round
    -- not keep the LLM's own raw id. The Quality Checker renumbers from 1 every round,
    so round 2's raw ids collide with round 1's real ones as a matter of course: here
    round 1 raises ISSUE-1 (vague_pronoun) and ISSUE-2 (non_verifiable); round 2's
    checker finds only the non_verifiable defect surviving (renumbered "ISSUE-1" by the
    LLM) plus one brand-new defect (renumbered "ISSUE-2" by the LLM). Reconciling the
    first back to its real identity (ISSUE-2) while keeping the second's raw id
    "ISSUE-2" too would give one QualityReport two issues sharing an id -- an uncaught
    ValidationError from QualityReport's own uniqueness check, before this fix.
    """
    section("Regression -- id reconciliation mints fresh ids instead of colliding")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import RunOutcome

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([
            {"requirement_id": REQ_A.id, "passed": False, "issues": [
                {"id": "ISSUE-1", "category": "vague_pronoun", "span": "these limits", "explanation": "e1"},
                {"id": "ISSUE-2", "category": "non_verifiable", "span": "subsequent processing",
                 "explanation": "e2"}]},
            # The checker renumbers from 1 every round: the surviving non_verifiable
            # defect comes back as "ISSUE-1", and a genuinely new defect also comes back
            # as "ISSUE-2" -- both raw ids collide with round 1's real ones.
            {"requirement_id": REQ_A.id, "passed": False, "issues": [
                {"id": "ISSUE-1", "category": "non_verifiable", "span": "subsequent processing",
                 "explanation": "e2, still there"},
                {"id": "ISSUE-2", "category": "ambiguous_term", "span": "the buffer",
                 "explanation": "brand new defect"}]},
        ]),
        refine_questioner=Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [
                {"id": "Q1", "issue_id": "ISSUE-1", "issue_category": "vague_pronoun", "question_text": "?"},
                {"id": "Q2", "issue_id": "ISSUE-2", "issue_category": "non_verifiable", "question_text": "?"}]},
        ]),
        refine_rewriter=Scripted([
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text, "refined_text": T1,
             "revision_number": 1, "answers_used": [
                 {"question_id": "Q1", "answer_text": "a"}, {"question_id": "Q2", "answer_text": "a"}]},
        ]),
        select_strategy=None, generate_tests=None)  # cap fires before stage 3 is ever reached
    human_fns = HumanFns(
        answer_questions=lambda turn: [RefinerAnswer(question_id=q.id, answer_text="a")
                                       for q in turn.questions],
        decide_at_cap=lambda rec: (RunOutcome.CAP_STOPPED, "n/a"))
    result = run_requirement(rec(requirement=REQ_A), DOC, None, None, fns, human_fns, throttle,
                             max_revisions=2, stage_configs=STAGE_CONFIGS)
    ok("the record was built without crashing", result.outcome is RunOutcome.CAP_STOPPED)
    round2_ids = [i.id for i in result.rounds[1].quality_report.issues]
    ok("round 2's two issues did not collide", len(set(round2_ids)) == 2)
    ok("the surviving defect reused round 1's real id (ISSUE-2, non_verifiable)",
       "ISSUE-2" in round2_ids)
    fresh = [i for i in round2_ids if i != "ISSUE-2"]
    ok("the genuinely new defect got a fresh, non-colliding id, not the LLM's raw 'ISSUE-2'",
       len(fresh) == 1 and fresh[0] not in ("ISSUE-1", "ISSUE-2"))


def test_suppressed_issue_reflagged_is_dropped() -> None:
    """Regression for task review finding Important #2: if the Quality Checker
    re-flags an issue the human already suppressed under a fresh id (VAGUE_PRONOUN is
    documented as expected to be noisy -- Known Limitation 4), the orchestrator must
    drop it after reconciliation, not let RefinementRound's "suppresses X but
    quality_report raises it anyway" check raise an uncaught ValidationError. `passed`
    must be recomputed from what survives the drop, not taken from the raw report.
    """
    section("Regression -- a re-flagged suppressed issue is dropped, not a crash")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import RunOutcome

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([
            {"requirement_id": REQ_A.id, "passed": False, "issues": [
                {"id": "I1", "category": "vague_pronoun", "span": "these limits", "explanation": "e"}]},
            # The checker ignores the suppression instruction and raises the same
            # defect again under a fresh id.
            {"requirement_id": REQ_A.id, "passed": False, "issues": [
                {"id": "FRESH-X", "category": "vague_pronoun", "span": "these limits",
                 "explanation": "still flagged despite suppression"}]},
        ]),
        refine_questioner=Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [
                {"id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun", "question_text": "?"}]},
        ]),
        refine_rewriter=Scripted([
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text, "refined_text": T1,
             "revision_number": 1, "answers_used": [
                 {"question_id": "Q1", "answer_text": "confirmed", "user_confirms_resolved": True}]},
        ]),
        select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                   "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
        generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
            "id": "TC-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
            "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
    human_fns = HumanFns(
        answer_questions=lambda turn: [RefinerAnswer(question_id="Q1", answer_text="confirmed",
                                                      user_confirms_resolved=True)],
        decide_at_cap=lambda rec: (RunOutcome.CAP_STOPPED, "n/a"))  # never called -- round 2 passes
    result = run_requirement(rec(requirement=REQ_A), DOC, None, None, fns, human_fns, throttle,
                             max_revisions=3, stage_configs=STAGE_CONFIGS)
    ok("the record was built without crashing and completed", result.outcome is RunOutcome.COMPLETED)
    ok("round 2 dropped the re-flagged issue entirely", result.rounds[1].quality_report.issues == [])
    ok("round 2 passed once the only remaining candidate was suppressed",
       result.rounds[1].quality_report.passed is True)
    ok("round 2 still records the suppression", "I1" in result.rounds[1].suppressed_issue_ids)


def test_resume_skips_finished_strategy_selector() -> None:
    """Regression for task review finding Important #3: resuming at TEST_GENERATOR
    (only the Test Generator failed last time; the Strategy Selector already succeeded
    and its result is already on the record) must not call select_strategy again --
    contract item 6: "nothing else is redone." Calling it again wastes an API call and
    could legitimately mint a DIFFERENT strategy for the same requirement.
    """
    section("Regression -- resuming at TEST_GENERATOR must not re-run the strategy selector")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import Classification, RunOutcome, SystemType, TestStrategy, TestTechnique

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    cls = Classification(requirement_id=REQ_A.id, system_type=SystemType.OTHER, rationale="r")
    strategy = TestStrategy(requirement_id=REQ_A.id, system_type=SystemType.OTHER,
                            techniques=[TestTechnique.BOUNDARY_VALUE_ANALYSIS], rationale="already chosen")
    passed_round = mk_round(1, REQ_A.text, passed=True)
    resuming_record = rec(requirement=REQ_A, classification=cls, rounds=[passed_round],
                          test_strategy=strategy)
    ok("fixture actually resumes at the test generator (sanity check)",
       resume_at(resuming_record) is PipelineStage.TEST_GENERATOR)

    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=None, check_quality=None, refine_questioner=None, refine_rewriter=None,
        select_strategy=None,  # must never be called
        generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
            "id": "TC-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
            "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
    human_fns = HumanFns(answer_questions=lambda turn: [],
                         decide_at_cap=lambda r: (RunOutcome.CAP_STOPPED, "n/a"))
    result = run_requirement(resuming_record, DOC, None, None, fns, human_fns, throttle,
                             max_revisions=3, stage_configs=STAGE_CONFIGS)
    ok("resuming at the test generator does not crash and completes",
       result.outcome is RunOutcome.COMPLETED)
    ok("the pre-existing strategy was kept, not replaced", result.test_strategy == strategy)


def test_max_revisions_must_be_at_least_two() -> None:
    """Regression for task review finding Important #4a: a cap can only be reached
    after at least one refinement attempt -- CAP_GENERATED/CAP_STOPPED's own schema
    rule requires a round with a rewrite to exist -- which max_revisions=1 (round 1 is
    already "at the cap" with nothing rewritten yet) or less could never produce.
    Rejected up front with a clear ValueError, rather than surfacing later as a
    confusing ValidationError deep inside the refine loop.
    """
    section("Regression -- max_revisions < 2 is rejected outright")
    from orchestrator.pipeline import run_requirement, Throttle

    def never_called(*a, **k):
        raise AssertionError("should never be called -- rejected before any stage runs")

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    fns = StageFns(check_consistency=None, map_dependencies=None, classify=never_called,
                   check_quality=never_called, refine_questioner=never_called,
                   refine_rewriter=never_called, select_strategy=never_called,
                   generate_tests=never_called)
    human_fns = HumanFns(answer_questions=never_called, decide_at_cap=never_called)

    for bad in (1, 0, -1):
        try:
            run_requirement(rec(requirement=REQ_A), DOC, None, None, fns, human_fns, throttle,
                            max_revisions=bad, stage_configs=STAGE_CONFIGS)
            ok(f"max_revisions={bad} is rejected", False)
        except ValueError:
            ok(f"max_revisions={bad} is rejected", True)


def test_resumed_cap_generated_then_stopped_strips_stage34() -> None:
    """Regression for task review finding Important #4b: a record capped once, told to
    generate anyway (CAP_GENERATED), whose Strategy Selector or Test Generator then
    failed and got resumed -- if the human now says CAP_STOPPED instead of retrying,
    CAP_STOPPED's own schema rule forbids test_strategy/test_plan and forbids errors
    naming those two stages ("the human stopped before stage 3"). The stop decision must
    retroactively discard that stage-3/4 work, not just relabel the outcome -- otherwise
    RequirementRunRecord.model_validate raises.

    Also confirms, in passing, that resuming into an already-capped round is safe even
    though resume_at sends it to REFINER_REWRITER (it cannot distinguish "mid-rewrite"
    from "already capped" -- both look like {passed: False, turn: <set>, rewrite: None}
    to it, an ambiguity the REFINER_QUESTIONER/REFINER_REWRITER split narrowed but did
    not remove): entering _run_refine_loop's pending-round branch here still lands on
    revision_number == max_revisions, so its existing `n >= max_revisions` check
    re-fires immediately and the round is re-appended unchanged, never reaching
    stage_fns.refine_questioner or stage_fns.refine_rewriter (both set to `None` below
    -- calling either would crash the mock, not just fail an assertion). An earlier
    draft of this fix added an explicit extra short-circuit for this ambiguity, on the
    assumption the existing check wouldn't fire on resume; a mutation test (removing
    the short-circuit) proved it unnecessary -- every check stayed green -- so it was
    deleted rather than kept as an untestable no-op (CLAUDE.md: don't write a check
    that can't fire).
    """
    section("Regression -- resuming CAP_GENERATED-then-failed and choosing to stop strips stage 3/4")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import (
        Classification, FailureKind, RunOutcome, StageError, SystemType, TestStrategy,
        TestTechnique,
    )

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    cls = Classification(requirement_id=REQ_A.id, system_type=SystemType.OTHER, rationale="r")
    strategy = TestStrategy(requirement_id=REQ_A.id, system_type=SystemType.OTHER,
                            techniques=[TestTechnique.BOUNDARY_VALUE_ANALYSIS], rationale="r")
    capped_round1 = mk_round(1, REQ_A.text, passed=False, rewrite_to=T1)
    capped_round2 = mk_round(2, T1, passed=False)  # cap fires here (max_revisions=2 below)
    prior_error, prior_attempts = failed_stage(PipelineStage.TEST_GENERATOR, "inv-tg-prior",
                                               attempts=4)
    resumed = rec(requirement=REQ_A, outcome=RunOutcome.ERROR, classification=cls,
                 rounds=[capped_round1, capped_round2], cap_reason="chose to generate anyway",
                 errors=[prior_error], attempts=prior_attempts, test_strategy=strategy)
    ok("fixture is a valid, already-capped-and-errored record (sanity check)",
       resumed.outcome is RunOutcome.ERROR)
    ok("resume_at sends an already-capped round to REFINER_REWRITER, same as a "
       "genuinely mid-rewrite one (sanity check -- see docstring)",
       resume_at(resumed) is PipelineStage.REFINER_REWRITER)

    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=None, check_quality=None, refine_questioner=None,
        refine_rewriter=None,  # must never be called
        select_strategy=None, generate_tests=None)        # human is stopping -- never reached
    human_fns = HumanFns(answer_questions=lambda turn: [],
                         decide_at_cap=lambda r: (RunOutcome.CAP_STOPPED, "changed my mind, stop here"))

    result = run_requirement(resumed, DOC, None, None, fns, human_fns, throttle,
                             max_revisions=2, stage_configs=STAGE_CONFIGS)
    ok("the record was built without crashing", result.outcome is RunOutcome.CAP_STOPPED)
    ok("test_strategy was stripped", result.test_strategy is None)
    ok("test_plan stays None", result.test_plan is None)
    ok("the stage-4 error was stripped along with it", result.errors == [])
    ok("the new cap_reason reflects the human's latest decision",
       result.cap_reason == "changed my mind, stop here")


def test_run_document_happy_path() -> None:
    """run_document wires the document-level stages and both requirements together.
    Also scenario 11: every stage in RunMetadata.stages carries a prompt_hash from
    prompt_fingerprint -- a property of the fixture, verified here rather than assumed."""
    section("run_document -- full document, scenario 11 (prompt provenance)")
    from orchestrator.pipeline import run_document, Throttle
    from design.schemas import DocumentOutcome

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)

    def classification_for(req_id):
        return {"requirement_id": req_id, "system_type": "other", "rationale": "r"}

    def passing_quality(req_id):
        return {"requirement_id": req_id, "passed": True, "issues": []}

    def strategy_for(req_id):
        return {"requirement_id": req_id, "system_type": "other",
                "techniques": ["boundary_value_analysis"], "rationale": "r"}

    def plan_for(req_id):
        return {"requirement_id": req_id, "test_cases": [{
            "id": f"TC-{req_id}-1", "requirement_ids": [req_id],
            "technique_used": "boundary_value_analysis", "title": "t", "steps": ["s"],
            "expected_result": "e"}]}

    fns = StageFns(
        check_consistency=Scripted([{"doc_id": DOC.doc_id, "conflicts": []}]),
        map_dependencies=Scripted([{"doc_id": DOC.doc_id, "dependencies": []}]),
        classify=Scripted([classification_for(REQ_A.id), classification_for(REQ_B.id)]),
        check_quality=Scripted([passing_quality(REQ_A.id), passing_quality(REQ_B.id)]),
        refine_questioner=None, refine_rewriter=None,
        select_strategy=Scripted([strategy_for(REQ_A.id), strategy_for(REQ_B.id)]),
        generate_tests=Scripted([plan_for(REQ_A.id), plan_for(REQ_B.id)]))
    human_fns = HumanFns(answer_questions=lambda turn: [], decide_at_cap=lambda rec: (None, None))
    metadata = make_metadata()

    result = run_document(DOC, metadata, fns, human_fns, throttle, max_revisions=3)
    ok("document outcome is COMPLETED", result.outcome is DocumentOutcome.COMPLETED)
    ok("both requirements completed",
       all(r.outcome.value == "completed" for r in result.requirement_records))
    # Not a schema tautology (RunMetadata._covers_every_stage/StageConfig.prompt_hash
    # already guarantee every stage has a hash on any RunMetadata that constructs at
    # all) -- this instead tests real orchestrator behavior: that the metadata passed
    # INTO run_document is the SAME object threaded onto the RETURNED record, i.e.
    # provenance (contract item 12) actually flows through the call rather than being
    # rebuilt or dropped along the way.
    ok("run_document threads the same metadata object through to the record (scenario 11)",
       result.metadata is metadata)


def test_document_context_no_leakage_three_requirements() -> None:
    """Design test plan item 1 (docs/superpowers/specs/2026-08-08-document-context-
    wiring-design.md): filtered context, verified with THREE requirements, not two.
    ConsistencyConflict.requirement_ids requires min_length=2, so with only two
    requirements in the whole document any conflict necessarily names both of them --
    there is no way to construct an "unrelated bystander" with just two. B here is a
    genuine bystander: real conflicts/dependencies exist in the document, correctly
    absent from B's own call."""
    section("Document context: filtered, no leakage (three requirements)")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import (
        ConsistencyConflict, ConsistencyReport, DependencyLink, DependencyReport, RunOutcome,
    )

    req_c = Requirement(id="DOC-REQ-C", source_doc_id="harness-doc",
                        text="The system shall log every login attempt.")
    doc3 = RequirementSet(doc_id="harness-doc", requirements=[REQ_A, REQ_B, req_c])
    consistency = ConsistencyReport(doc_id="harness-doc", conflicts=[
        ConsistencyConflict(requirement_ids=[REQ_A.id, req_c.id], explanation="A and C disagree")])
    dependency = DependencyReport(doc_id="harness-doc", dependencies=[
        DependencyLink(from_requirement_id=REQ_A.id, to_requirement_id=req_c.id,
                       explanation="C must exist before A")])
    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    human_fns = HumanFns(answer_questions=lambda t: [], decide_at_cap=lambda r: (None, None))

    def run_one(req):
        quality_fn = Scripted([{"requirement_id": req.id, "passed": True, "issues": []}])
        strategy_fn = Scripted([{"requirement_id": req.id, "system_type": "other",
                                 "techniques": ["boundary_value_analysis"], "rationale": "r"}])
        fns = StageFns(
            check_consistency=None, map_dependencies=None,
            classify=Scripted([{"requirement_id": req.id, "system_type": "other", "rationale": "r"}]),
            check_quality=quality_fn, refine_questioner=None, refine_rewriter=None,
            select_strategy=strategy_fn,
            generate_tests=Scripted([{"requirement_id": req.id, "test_cases": [{
                "id": f"TC-{req.id}-1", "requirement_ids": [req.id],
                "technique_used": "boundary_value_analysis", "title": "t", "steps": ["s"],
                "expected_result": "e"}]}]))
        result = run_requirement(
            rec(requirement=req), doc3, consistency, dependency, fns, human_fns, throttle,
            max_revisions=3, stage_configs=STAGE_CONFIGS)
        ok(f"{req.id}: reaches COMPLETED", result.outcome is RunOutcome.COMPLETED)
        return quality_fn.calls[0], strategy_fn.calls[0]

    qc_a, ss_a = run_one(REQ_A)
    qc_b, ss_b = run_one(REQ_B)
    qc_c, ss_c = run_one(req_c)

    ok("A's check_quality call carries the conflict naming A and C",
       len(qc_a[2]) == 1 and qc_a[2][0].requirement_ids == [REQ_A.id, req_c.id])
    ok("C's check_quality call carries the same conflict",
       len(qc_c[2]) == 1 and qc_c[2][0].requirement_ids == [REQ_A.id, req_c.id])
    ok("B is a genuine bystander: relevant_conflicts=[] despite a real conflict "
       "existing in the document",
       qc_b[2] == [])
    ok("A's check_quality call carries the dependency",
       len(qc_a[3]) == 1 and qc_a[3][0].from_requirement_id == REQ_A.id)
    ok("C's check_quality call carries the same A->C dependency",
       len(qc_c[3]) == 1 and qc_c[3][0].from_requirement_id == REQ_A.id
       and qc_c[3][0].to_requirement_id == req_c.id)
    ok("B's check_quality call carries relevant_dependencies=[]", qc_b[3] == [])
    ok("A's select_strategy call carries the identical filtered dependency list "
       "as its check_quality call", ss_a[2] == qc_a[3])
    ok("C's select_strategy call carries the identical filtered dependency list "
       "as its check_quality call", ss_c[2] == qc_c[3])
    ok("B's select_strategy call carries relevant_dependencies=[]", ss_b[2] == [])


def test_document_context_none_vs_empty() -> None:
    """Design test plan item 2: None (the document-level stage failed) and [] (it ran
    and found nothing for this requirement) are never collapsed into each other."""
    section("Document context: None vs [] preserved")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import DependencyReport, RunOutcome

    dependency = DependencyReport(doc_id="harness-doc", dependencies=[])
    quality_fn = Scripted([{"requirement_id": REQ_A.id, "passed": True, "issues": []}])
    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=quality_fn, refine_questioner=None, refine_rewriter=None,
        select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                   "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
        generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
            "id": "TC-A-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
            "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
    human_fns = HumanFns(answer_questions=lambda t: [], decide_at_cap=lambda r: (None, None))
    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)

    result = run_requirement(rec(requirement=REQ_A), DOC, None, dependency, fns, human_fns,
                             throttle, max_revisions=3, stage_configs=STAGE_CONFIGS)
    ok("reaches COMPLETED", result.outcome is RunOutcome.COMPLETED)
    args = quality_fn.calls[0]
    ok("relevant_conflicts is None (consistency checker failed)", args[2] is None)
    ok("relevant_dependencies is [] (dependency mapper ran, found nothing)", args[3] == [])


def test_document_context_independent_failure_mirror() -> None:
    """Design test plan item 3: the mirror of item 2 -- dependency mapper failed,
    consistency checker succeeded. One side being None never forces the other to None."""
    section("Document context: independent failure, mirrored")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import ConsistencyConflict, ConsistencyReport, RunOutcome

    consistency = ConsistencyReport(doc_id="harness-doc", conflicts=[
        ConsistencyConflict(requirement_ids=[REQ_A.id, REQ_B.id], explanation="A and B disagree")])
    quality_fn = Scripted([{"requirement_id": REQ_A.id, "passed": True, "issues": []}])
    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=quality_fn, refine_questioner=None, refine_rewriter=None,
        select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                   "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
        generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
            "id": "TC-A-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
            "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
    human_fns = HumanFns(answer_questions=lambda t: [], decide_at_cap=lambda r: (None, None))
    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)

    result = run_requirement(rec(requirement=REQ_A), DOC, consistency, None, fns, human_fns,
                             throttle, max_revisions=3, stage_configs=STAGE_CONFIGS)
    ok("reaches COMPLETED", result.outcome is RunOutcome.COMPLETED)
    args = quality_fn.calls[0]
    ok("relevant_conflicts is populated (consistency checker ran)",
       len(args[2]) == 1 and args[2][0].requirement_ids == [REQ_A.id, REQ_B.id])
    ok("relevant_dependencies is None (dependency mapper failed)", args[3] is None)


def test_document_context_dependencies_reach_both_stages() -> None:
    """Design test plan item 4: Strategy Selector and Test Generator both receive the
    same filtered dependency list."""
    section("Document context: dependencies reach both Strategy Selector and Test Generator")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import DependencyLink, DependencyReport, RunOutcome

    dependency = DependencyReport(doc_id="harness-doc", dependencies=[
        DependencyLink(from_requirement_id=REQ_A.id, to_requirement_id=REQ_B.id,
                       explanation="A depends on B")])
    strategy_fn = Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                             "techniques": ["boundary_value_analysis"], "rationale": "r"}])
    generate_fn = Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
        "id": "TC-A-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
        "title": "t", "steps": ["s"], "expected_result": "e"}]}])
    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([{"requirement_id": REQ_A.id, "passed": True, "issues": []}]),
        refine_questioner=None, refine_rewriter=None,
        select_strategy=strategy_fn, generate_tests=generate_fn)
    human_fns = HumanFns(answer_questions=lambda t: [], decide_at_cap=lambda r: (None, None))
    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)

    result = run_requirement(rec(requirement=REQ_A), DOC, None, dependency, fns, human_fns,
                             throttle, max_revisions=3, stage_configs=STAGE_CONFIGS)
    ok("reaches COMPLETED", result.outcome is RunOutcome.COMPLETED)
    strategy_deps = strategy_fn.calls[0][2]
    generate_deps = generate_fn.calls[0][2]
    ok("select_strategy receives the dependency",
       len(strategy_deps) == 1 and strategy_deps[0].to_requirement_id == REQ_B.id)
    ok("generate_tests receives the identical filtered list", generate_deps == strategy_deps)


def test_document_context_survives_resume() -> None:
    """Design test plan item 5: a document interrupted after the document-level stages
    but before any requirement finishes, resumed with NO retry_document_stage call in
    between -- the resumed requirement's check_quality call receives the same filtered
    context an uninterrupted run would, not None or [] by default."""
    section("Document context: survives resume (no retry in between)")
    from orchestrator.pipeline import (
        run_document_stages, write_document_run, read_document_run, resume_document, Throttle,
    )
    from design.schemas import (
        ConsistencyConflict, ConsistencyReport, DocumentOutcome, RunOutcome,
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
        metadata = make_metadata(run_id="run-context-resume")

        cons, deps, errors, attempts = run_document_stages(
            DOC, metadata.stages,
            StageFns(check_consistency=Scripted([{"doc_id": DOC.doc_id, "conflicts": [
                        {"requirement_ids": [REQ_A.id, REQ_B.id], "explanation": "A and B disagree"}]}]),
                    map_dependencies=Scripted([{"doc_id": DOC.doc_id, "dependencies": []}]),
                    classify=None, check_quality=None, refine_questioner=None,
                    refine_rewriter=None, select_strategy=None, generate_tests=None),
            throttle)
        partial = DocumentRunRecord(requirement_set=DOC, metadata=metadata,
                                    outcome=DocumentOutcome.COMPLETED, consistency_report=cons,
                                    dependency_report=deps, attempts=attempts)
        write_document_run(tmp_path, partial)
        # No requirement files written -- both requirements are pending, as if the
        # process crashed right after the document-level stages.
        ok("both requirements are pending on the interrupted record",
           set(read_document_run(tmp_path).pending_requirement_ids) == {REQ_A.id, REQ_B.id})

        quality_fn = Scripted([
            {"requirement_id": REQ_A.id, "passed": True, "issues": []},
            {"requirement_id": REQ_B.id, "passed": True, "issues": []}])

        def classification_for(req_id):
            return {"requirement_id": req_id, "system_type": "other", "rationale": "r"}

        finishing_fns = StageFns(
            check_consistency=None, map_dependencies=None,
            classify=Scripted([classification_for(REQ_A.id), classification_for(REQ_B.id)]),
            check_quality=quality_fn, refine_questioner=None, refine_rewriter=None,
            select_strategy=Scripted([
                {"requirement_id": REQ_A.id, "system_type": "other",
                 "techniques": ["boundary_value_analysis"], "rationale": "r"},
                {"requirement_id": REQ_B.id, "system_type": "other",
                 "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
            generate_tests=Scripted([
                {"requirement_id": REQ_A.id, "test_cases": [{
                    "id": "TC-A-1", "requirement_ids": [REQ_A.id],
                    "technique_used": "boundary_value_analysis", "title": "t", "steps": ["s"],
                    "expected_result": "e"}]},
                {"requirement_id": REQ_B.id, "test_cases": [{
                    "id": "TC-B-1", "requirement_ids": [REQ_B.id],
                    "technique_used": "boundary_value_analysis", "title": "t", "steps": ["s"],
                    "expected_result": "e"}]}]))
        human_fns = HumanFns(answer_questions=lambda t: [], decide_at_cap=lambda r: (None, None))
        resume_document(tmp_path, finishing_fns, human_fns, throttle, max_revisions=3)

        ok("both resumed check_quality calls happened", len(quality_fn.calls) == 2)
        for call in quality_fn.calls:
            ok(f"{call[0].id}: relevant_conflicts survives the resume, naming A and B",
               len(call[2]) == 1 and call[2][0].requirement_ids == [REQ_A.id, REQ_B.id])
            ok(f"{call[0].id}: relevant_dependencies survives the resume as []", call[3] == [])


def test_document_context_persists_across_refinement_rounds() -> None:
    """Design test plan item 6: a requirement that fails round 1 and passes round 2 --
    both rounds' check_quality calls carry the identical filtered context (conflicts
    AND dependencies), proving _run_refine_loop threads both through every iteration,
    not just the first."""
    section("Document context: persists across refinement rounds")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import (
        ConsistencyConflict, ConsistencyReport, DependencyLink, DependencyReport, RunOutcome,
    )

    consistency = ConsistencyReport(doc_id="harness-doc", conflicts=[
        ConsistencyConflict(requirement_ids=[REQ_A.id, REQ_B.id], explanation="A and B disagree")])
    dependency = DependencyReport(doc_id="harness-doc", dependencies=[
        DependencyLink(from_requirement_id=REQ_A.id, to_requirement_id=REQ_B.id,
                       explanation="A depends on B")])
    quality_fn = Scripted([
        {"requirement_id": REQ_A.id, "passed": False, "issues": [{
            "id": "I1", "category": "vague_pronoun", "span": "these limits",
            "explanation": "Unresolved."}]},
        {"requirement_id": REQ_A.id, "passed": True, "issues": []},
    ])
    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=quality_fn,
        refine_questioner=Scripted([{"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
            "id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun", "question_text": "?"}]}]),
        refine_rewriter=Scripted([{"requirement_id": REQ_A.id, "original_text": REQ_A.text,
                                   "refined_text": T1, "revision_number": 1,
                                   "answers_used": [{"question_id": "Q1", "answer_text": "a"}]}]),
        select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                   "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
        generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
            "id": "TC-A-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
            "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
    human_fns = HumanFns(answer_questions=lambda turn: [RefinerAnswer(question_id="Q1", answer_text="a")],
                         decide_at_cap=lambda r: (None, None))
    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)

    result = run_requirement(rec(requirement=REQ_A), DOC, consistency, dependency, fns, human_fns,
                             throttle, max_revisions=3, stage_configs=STAGE_CONFIGS)
    ok("reaches COMPLETED after round 2 passes", result.outcome is RunOutcome.COMPLETED)
    ok("two check_quality calls happened (round 1, round 2)", len(quality_fn.calls) == 2)
    round1_conflicts, round2_conflicts = quality_fn.calls[0][2], quality_fn.calls[1][2]
    round1_deps, round2_deps = quality_fn.calls[0][3], quality_fn.calls[1][3]
    ok("round 1 and round 2 carry the identical relevant_dependencies list",
       round1_deps == round2_deps
       and len(round1_deps) == 1
       and round1_deps[0].from_requirement_id == REQ_A.id
       and round1_deps[0].to_requirement_id == REQ_B.id)
    ok("round 1 and round 2 carry the identical relevant_conflicts list",
       round1_conflicts == round2_conflicts
       and len(round1_conflicts) == 1
       and round1_conflicts[0].requirement_ids == [REQ_A.id, REQ_B.id])


def test_document_stage_retry_allowed_before_any_requirement() -> None:
    """Design test plan item 7: retry_document_stage's ALLOWED branch, happy path --
    zero requirement records on disk (the narrow crash-window case: the process died
    right after the document-level stages, before touching any requirement), so the
    retry is still legal, succeeds, and outcome climbs DEGRADED -> COMPLETED."""
    section("Retry allowed (zero requirement records), and it succeeds")
    from orchestrator.pipeline import (
        run_document_stages, write_document_run, retry_document_stage, Throttle,
    )
    from design.schemas import DocumentOutcome, DocumentStage

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
        metadata = make_metadata(run_id="run-retry-allowed-succeeds")

        cons, deps, errors, attempts = run_document_stages(
            DOC, metadata.stages,
            StageFns(check_consistency=Scripted([StageCallFailed("429")] * 2),
                    map_dependencies=Scripted([{"doc_id": DOC.doc_id, "dependencies": []}]),
                    classify=None, check_quality=None, refine_questioner=None,
                    refine_rewriter=None, select_strategy=None, generate_tests=None),
            throttle, max_attempts=2, backoff_seconds=lambda a: 0.0)
        record = DocumentRunRecord(requirement_set=DOC, metadata=metadata,
                                   outcome=DocumentOutcome.DEGRADED, errors=errors,
                                   consistency_report=cons, dependency_report=deps,
                                   attempts=attempts)
        write_document_run(tmp_path, record)
        ok("fixture has zero requirement records", record.requirement_records == [])

        retry_fns = StageFns(
            check_consistency=Scripted([{"doc_id": DOC.doc_id, "conflicts": []}]),
            map_dependencies=None, classify=None, check_quality=None,
            refine_questioner=None, refine_rewriter=None,
            select_strategy=None, generate_tests=None)
        retried = retry_document_stage(tmp_path, DocumentStage.CONSISTENCY_CHECKER, retry_fns,
                                       throttle, max_attempts=1, backoff_seconds=lambda a: 0.0)
        ok("retry succeeds: outcome climbs to COMPLETED", retried.outcome is DocumentOutcome.COMPLETED)
        ok("the recovered report is written", retried.consistency_report is not None)
        ok("the original failure is still on record", len(retried.errors) == 1)
        ok("no second entry was appended for a SUCCESSFUL retry",
           sum(1 for e in retried.errors if e.stage is DocumentStage.CONSISTENCY_CHECKER) == 1)


def test_document_stage_retry_allowed_but_fails_before_any_requirement() -> None:
    """Design test plan item 8: retry_document_stage's ALLOWED branch, failure path.
    Rewriting the old single retry test into "allowed" (item 7) and "blocked" (item 9)
    would silently drop its old coverage of a SECOND failed retry appending its own,
    distinct-invocation_id DocumentStageError rather than merging -- that coverage
    happened to sit in a fixture this design now makes illegal to retry in place.
    Re-homed here in a fixture where it's still legal: zero requirement records, and
    the retry itself fails too."""
    section("Retry allowed (zero requirement records), but it fails too")
    from orchestrator.pipeline import (
        run_document_stages, write_document_run, retry_document_stage, Throttle,
    )
    from design.schemas import AttemptResult, DocumentOutcome, DocumentStage

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
        metadata = make_metadata(run_id="run-retry-allowed-fails")

        cons, deps, errors, attempts = run_document_stages(
            DOC, metadata.stages,
            StageFns(check_consistency=Scripted([StageCallFailed("429")] * 2),
                    map_dependencies=Scripted([{"doc_id": DOC.doc_id, "dependencies": []}]),
                    classify=None, check_quality=None, refine_questioner=None,
                    refine_rewriter=None, select_strategy=None, generate_tests=None),
            throttle, max_attempts=2, backoff_seconds=lambda a: 0.0)
        record = DocumentRunRecord(requirement_set=DOC, metadata=metadata,
                                   outcome=DocumentOutcome.DEGRADED, errors=errors,
                                   consistency_report=cons, dependency_report=deps,
                                   attempts=attempts)
        write_document_run(tmp_path, record)
        first_retry_count = errors[0].retry_count
        first_invocation_id = errors[0].invocation_id

        fail_again_fns = StageFns(
            check_consistency=Scripted([StageCallFailed("429")]), map_dependencies=None,
            classify=None, check_quality=None, refine_questioner=None,
            refine_rewriter=None, select_strategy=None, generate_tests=None)
        retried = retry_document_stage(tmp_path, DocumentStage.CONSISTENCY_CHECKER,
                                       fail_again_fns, throttle, max_attempts=1,
                                       backoff_seconds=lambda a: 0.0)

        ok("the guard did not fire (zero requirement records, so the call proceeded)",
           len(fail_again_fns.check_consistency.calls) == 1)
        cc_errors = [e for e in retried.errors if e.stage is DocumentStage.CONSISTENCY_CHECKER]
        ok("a second, independent error is appended -- not a merge", len(cc_errors) == 2)
        ok("the first error's invocation_id is untouched",
           cc_errors[0].invocation_id == first_invocation_id)
        ok("the second error's invocation_id is distinct from the first",
           cc_errors[1].invocation_id != first_invocation_id)
        ok("the first error's retry_count is untouched (no merge/bump)",
           cc_errors[0].retry_count == first_retry_count)
        new_attempts = [a for a in retried.attempts if a.invocation_id == cc_errors[1].invocation_id]
        ok("the new failure's attempt is recorded and linked to the new error via invocation_id",
           len(new_attempts) == 1 and new_attempts[0].result is AttemptResult.TRANSPORT_FAILURE)
        ok("outcome stays DEGRADED -- the retry didn't help",
           retried.outcome is DocumentOutcome.DEGRADED)


def test_document_stage_retry_blocked_after_requirement_processed() -> None:
    """Design test plan item 9: retry_document_stage's BLOCKED branch. The pre-rev-2
    test called retry_document_stage on a run where a requirement had already been
    processed -- under this design that must now raise instead of succeeding.
    Verified with full document-record equality before/after, not a field subset, plus
    proof the stage fn was never even called."""
    section("Retry blocked once a requirement has been processed")
    from orchestrator.pipeline import run_document, retry_document_stage, read_document_run, Throttle
    from design.schemas import DocumentStage

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
        # doc_id must match REQ_A.source_doc_id -- see test_error_resume_finish for the
        # same one-requirement workaround (RequirementSet._requirements_belong_to_this_document).
        one_req_doc = RequirementSet(doc_id="harness-doc", requirements=[REQ_A])
        first_fns = StageFns(
            check_consistency=Scripted([StageCallFailed("429")] * 2),
            map_dependencies=Scripted([{"doc_id": "harness-doc", "dependencies": []}]),
            classify=Scripted([StageCallFailed("429")] * 2),
            check_quality=None, refine_questioner=None, refine_rewriter=None,
            select_strategy=None, generate_tests=None)
        metadata = make_metadata(run_id="run-retry-blocked")
        run_document(one_req_doc, metadata, first_fns, HumanFns(
            answer_questions=lambda t: [], decide_at_cap=lambda r: (None, None)),
            throttle, max_revisions=3, run_dir=tmp_path, max_attempts=2,
            backoff_seconds=lambda a: 0.0)
        before = read_document_run(tmp_path)
        ok("fixture has at least one requirement record", len(before.requirement_records) >= 1)

        would_succeed_fn = Scripted([{"doc_id": "harness-doc", "conflicts": []}])
        blocked_fns = StageFns(
            check_consistency=would_succeed_fn, map_dependencies=None, classify=None,
            check_quality=None, refine_questioner=None, refine_rewriter=None,
            select_strategy=None, generate_tests=None)
        try:
            retry_document_stage(tmp_path, DocumentStage.CONSISTENCY_CHECKER, blocked_fns,
                                 throttle, max_attempts=1, backoff_seconds=lambda a: 0.0)
            ok("retrying after a requirement has run raises ValueError", False)
        except ValueError:
            ok("retrying after a requirement has run raises ValueError", True)
        ok("the stage fn was never called -- the guard fires before call_document_stage",
           would_succeed_fn.calls == [])
        after = read_document_run(tmp_path)
        ok("the complete document record is unchanged (full equality, not a field subset)",
           after.model_dump(mode="json") == before.model_dump(mode="json"))


def test_document_context_consistent_across_resume_and_retry() -> None:
    """Design test plan item 10: the scenario Decision 7 exists to prevent, turned into
    an assertion. Two requirements; the first is processed while the document is still
    DEGRADED; a document-level retry is attempted (and blocked, item 9's guard) before
    the second requirement is resumed -- the second must see the SAME None the first
    saw, never a value that would have differed if the retry had been allowed through."""
    section("Document context stays consistent across resume + a blocked retry")
    from orchestrator.pipeline import (
        run_document_stages, write_document_run, write_requirement_run, read_document_run,
        run_requirement, resume_document, retry_document_stage, Throttle,
    )
    from design.schemas import DocumentOutcome, DocumentStage, RunOutcome

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
        metadata = make_metadata(run_id="run-context-consistency")
        human_fns = HumanFns(answer_questions=lambda t: [], decide_at_cap=lambda r: (None, None))

        cons, deps, errors, attempts = run_document_stages(
            DOC, metadata.stages,
            StageFns(check_consistency=Scripted([StageCallFailed("429")] * 2),
                    map_dependencies=Scripted([{"doc_id": DOC.doc_id, "dependencies": []}]),
                    classify=None, check_quality=None, refine_questioner=None,
                    refine_rewriter=None, select_strategy=None, generate_tests=None),
            throttle, max_attempts=2, backoff_seconds=lambda a: 0.0)
        doc_record = DocumentRunRecord(requirement_set=DOC, metadata=metadata,
                                       outcome=DocumentOutcome.DEGRADED, errors=errors,
                                       consistency_report=cons, dependency_report=deps,
                                       attempts=attempts)
        write_document_run(tmp_path, doc_record)

        quality_fn_a = Scripted([{"requirement_id": REQ_A.id, "passed": True, "issues": []}])
        fns_a = StageFns(
            check_consistency=None, map_dependencies=None,
            classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
            check_quality=quality_fn_a, refine_questioner=None, refine_rewriter=None,
            select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                       "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
            generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
                "id": "TC-A-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
                "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
        req_a_record = run_requirement(
            rec(requirement=REQ_A, run_id=metadata.run_id), DOC, doc_record.consistency_report,
            doc_record.dependency_report, fns_a, human_fns, throttle, max_revisions=3,
            stage_configs=metadata.stages)
        write_requirement_run(tmp_path, req_a_record)
        ok("A processed under relevant_conflicts=None", quality_fn_a.calls[0][2] is None)

        would_recover_fn = Scripted([{"doc_id": DOC.doc_id, "conflicts": []}])
        try:
            retry_document_stage(tmp_path, DocumentStage.CONSISTENCY_CHECKER,
                                 StageFns(check_consistency=would_recover_fn, map_dependencies=None,
                                         classify=None, check_quality=None, refine_questioner=None,
                                         refine_rewriter=None, select_strategy=None, generate_tests=None),
                                 throttle, max_attempts=1, backoff_seconds=lambda a: 0.0)
            ok("retry is blocked (A already ran)", False)
        except ValueError:
            ok("retry is blocked (A already ran)", True)
        ok("the recovering stage fn was never called", would_recover_fn.calls == [])

        quality_fn_b = Scripted([{"requirement_id": REQ_B.id, "passed": True, "issues": []}])
        resumed = resume_document(tmp_path, StageFns(
            check_consistency=None, map_dependencies=None,
            classify=Scripted([{"requirement_id": REQ_B.id, "system_type": "other", "rationale": "r"}]),
            check_quality=quality_fn_b, refine_questioner=None, refine_rewriter=None,
            select_strategy=Scripted([{"requirement_id": REQ_B.id, "system_type": "other",
                                       "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
            generate_tests=Scripted([{"requirement_id": REQ_B.id, "test_cases": [{
                "id": "TC-B-1", "requirement_ids": [REQ_B.id], "technique_used": "boundary_value_analysis",
                "title": "t", "steps": ["s"], "expected_result": "e"}]}])),
            human_fns, throttle, max_revisions=3)
        ok("B reaches COMPLETED", any(r.requirement.id == REQ_B.id and r.outcome is RunOutcome.COMPLETED
                                      for r in resumed.requirement_records))
        ok("B was ALSO processed under relevant_conflicts=None -- never diverges from A",
           quality_fn_b.calls[0][2] is None)


def test_error_resume_finish() -> None:
    """Scenario 8: not just 'retries exhaust and outcome=ERROR is recorded' -- continue
    past that. A fresh resume pass must find the requirement in pending_requirement_ids,
    restart it at the failed stage, and reach a terminal outcome."""
    section("Scenario 8 -- transport failure -> error -> resume -> finish")
    from orchestrator.pipeline import run_document, resume_document, Throttle
    from design.schemas import RunOutcome

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
        # doc_id must match REQ_A.source_doc_id ("harness-doc") -- RequirementSet
        # rejects a set whose declared doc_id disagrees with a requirement's recorded
        # source document (design/schemas.py::_requirements_belong_to_this_document).
        # The brief's literal "one-req" collides with that check; fixed here the same
        # way Task 10's implementer fixed self-contradictory hand-built fixtures.
        one_req_doc = RequirementSet(doc_id="harness-doc", requirements=[REQ_A])
        failing_fns = StageFns(
            check_consistency=Scripted([{"doc_id": "harness-doc", "conflicts": []}]),
            map_dependencies=Scripted([{"doc_id": "harness-doc", "dependencies": []}]),
            classify=Scripted([StageCallFailed("429")] * 2),  # exhausts at max_attempts=2
            check_quality=None, refine_questioner=None, refine_rewriter=None,
            select_strategy=None, generate_tests=None)
        human_fns = HumanFns(answer_questions=lambda t: [], decide_at_cap=lambda r: (None, None))
        metadata = make_metadata(run_id="run-scenario-8")
        result = run_document(one_req_doc, metadata, failing_fns, human_fns, throttle,
                              max_revisions=3, run_dir=tmp_path, max_attempts=2,
                              backoff_seconds=lambda a: 0.0)
        ok("the requirement errored", result.requirement_records[0].outcome is RunOutcome.ERROR)
        ok("it shows up as pending", REQ_A.id in result.pending_requirement_ids)

        recovering_fns = StageFns(
            check_consistency=None, map_dependencies=None,
            classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
            check_quality=Scripted([{"requirement_id": REQ_A.id, "passed": True, "issues": []}]),
            refine_questioner=None, refine_rewriter=None,
            select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                       "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
            generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
                "id": "TC-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
                "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
        resumed = resume_document(tmp_path, recovering_fns, human_fns, throttle, max_revisions=3)
        ok("resume finds and finishes the errored requirement",
           resumed.requirement_records[0].outcome is RunOutcome.COMPLETED)
        ok("nothing is pending after resume", resumed.pending_requirement_ids == [])


def test_interruption_mid_document_round_trip() -> None:
    """Scenario 9: write partial files, abandon the in-memory orchestrator entirely,
    construct a NEW one from disk, continue to completion. Distinct from resume_at as a
    pure function and from serialization round-trip checks -- this is the only scenario
    proving resumability works end to end."""
    section("Scenario 9 -- interruption mid-document, full round trip")
    from orchestrator.pipeline import (
        run_document_stages, write_document_run, write_requirement_run, resume_document,
        read_document_run, Throttle)
    from design.schemas import DocumentOutcome, RunOutcome

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
        metadata = make_metadata(run_id="run-scenario-9")

        # Simulate an interruption: document-level stages ran and were written, REQ_A
        # is untouched (no file at all -- "no record file" also counts as pending),
        # and then the process is abandoned. No run_document call spans this at all.
        cons, deps, errors, attempts = run_document_stages(
            DOC, metadata.stages,
            StageFns(check_consistency=Scripted([{"doc_id": DOC.doc_id, "conflicts": []}]),
                    map_dependencies=Scripted([{"doc_id": DOC.doc_id, "dependencies": []}]),
                    classify=None, check_quality=None, refine_questioner=None,
                    refine_rewriter=None, select_strategy=None,
                    generate_tests=None),
            throttle)
        partial = DocumentRunRecord(requirement_set=DOC, metadata=metadata,
                                    outcome=DocumentOutcome.COMPLETED, consistency_report=cons,
                                    dependency_report=deps, attempts=attempts)
        write_document_run(tmp_path, partial)
        # (No requirement files written at all -- both requirements are pending.)

        reloaded_before = read_document_run(tmp_path)
        ok("both requirements are pending on the interrupted, freshly-read record",
           set(reloaded_before.pending_requirement_ids) == {REQ_A.id, REQ_B.id})

        # A brand new orchestrator "process" picks up from disk -- nothing here reuses
        # any in-memory state from the run_document_stages call above.
        def classification_for(req_id):
            return {"requirement_id": req_id, "system_type": "other", "rationale": "r"}
        finishing_fns = StageFns(
            check_consistency=None, map_dependencies=None,
            classify=Scripted([classification_for(REQ_A.id), classification_for(REQ_B.id)]),
            check_quality=Scripted([
                {"requirement_id": REQ_A.id, "passed": True, "issues": []},
                {"requirement_id": REQ_B.id, "passed": True, "issues": []}]),
            refine_questioner=None, refine_rewriter=None,
            select_strategy=Scripted([
                {"requirement_id": REQ_A.id, "system_type": "other",
                 "techniques": ["boundary_value_analysis"], "rationale": "r"},
                {"requirement_id": REQ_B.id, "system_type": "other",
                 "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
            generate_tests=Scripted([
                {"requirement_id": REQ_A.id, "test_cases": [{
                    "id": "TC-A-1", "requirement_ids": [REQ_A.id],
                    "technique_used": "boundary_value_analysis", "title": "t", "steps": ["s"],
                    "expected_result": "e"}]},
                {"requirement_id": REQ_B.id, "test_cases": [{
                    "id": "TC-B-1", "requirement_ids": [REQ_B.id],
                    "technique_used": "boundary_value_analysis", "title": "t", "steps": ["s"],
                    "expected_result": "e"}]}]))
        human_fns = HumanFns(answer_questions=lambda t: [], decide_at_cap=lambda r: (None, None))
        finished = resume_document(tmp_path, finishing_fns, human_fns, throttle, max_revisions=3)
        ok("both requirements finished after the full round trip",
           all(r.outcome is RunOutcome.COMPLETED for r in finished.requirement_records))
        ok("re-reading from disk after resume shows nothing pending",
           read_document_run(tmp_path).pending_requirement_ids == [])


def test_validation_failure() -> None:
    """Scenario 10: a stage returns output that fails model_validate. New ground
    relative to design/ORCHESTRATOR_CONTRACT.md as written -- added there in this same
    task, not treated as a special case."""
    section("Scenario 10 -- validation failure")
    from orchestrator.pipeline import call_stage, StageFailed, Throttle
    from design.schemas import AttemptResult, Classification, FailureKind

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    attempts = []
    fn = Scripted([{"requirement_id": "R1", "system_type": "not-a-real-type", "rationale": "r"}])
    try:
        call_stage(fn, ("R1",), Classification, PipelineStage.CLASSIFIER, "inv-1",
                  "fake-model", throttle, attempts, "R1", max_attempts=1,
                  backoff_seconds=lambda a: 0.0)
        ok("an invalid enum value fails validation", False)
    except StageFailed as f:
        ok("an invalid enum value fails validation", f.kind is FailureKind.VALIDATION)
    ok("the call still returned, so an attempt with tokens was logged",
       len(attempts) == 1 and attempts[0].result is AttemptResult.VALIDATION_FAILURE
       and attempts[0].prompt_tokens is not None)


def test_first_attempt_success() -> None:
    """A call that succeeds on the very first try: exactly one attempt row, SUCCESS,
    tokens present, no error_message."""
    section("First-attempt success")
    from orchestrator.pipeline import call_stage, Throttle
    from design.schemas import AttemptResult, Classification

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    attempts = []
    fn = Scripted([{"requirement_id": "R1", "system_type": "web", "rationale": "r"}])
    result = call_stage(fn, ("R1",), Classification, PipelineStage.CLASSIFIER, "inv-1",
                        "fake-model", throttle, attempts, "R1")
    ok("the call succeeds", isinstance(result, Classification))
    ok("exactly one attempt row, SUCCESS, no error_message", len(attempts) == 1
       and attempts[0].attempt_number == 1 and attempts[0].result is AttemptResult.SUCCESS
       and attempts[0].error_message is None)


def test_validation_then_success() -> None:
    """A single schema-rejected output followed by a correct retry: two attempts under
    one invocation, the first VALIDATION_FAILURE (with tokens -- inference happened),
    the second SUCCESS."""
    section("Validation failure, then a retry succeeds")
    from orchestrator.pipeline import call_stage, Throttle
    from design.schemas import AttemptResult, Classification

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    attempts = []
    fn = Scripted([
        {"requirement_id": "R1"},  # missing system_type, rationale -> VALIDATION
        {"requirement_id": "R1", "system_type": "web", "rationale": "r"},
    ])
    result = call_stage(fn, ("R1",), Classification, PipelineStage.CLASSIFIER, "inv-1",
                        "fake-model", throttle, attempts, "R1", max_attempts=2,
                        backoff_seconds=lambda a: 0.0)
    ok("the retry succeeds", isinstance(result, Classification))
    ok("two attempts, one invocation", len(attempts) == 2
       and attempts[0].invocation_id == attempts[1].invocation_id == "inv-1")
    ok("first attempt: VALIDATION_FAILURE with tokens (inference happened)",
       attempts[0].result is AttemptResult.VALIDATION_FAILURE
       and attempts[0].prompt_tokens is not None)
    ok("second attempt: SUCCESS", attempts[1].result is AttemptResult.SUCCESS)


def test_wrong_id_then_success() -> None:
    """A requirement_id mismatch (contract item 15) followed by a correct retry: the
    mismatch is logged as its own VALIDATION_FAILURE attempt, distinct from a
    malformed-payload validation failure, and the eventual success shares its
    invocation_id."""
    section("Wrong requirement_id, then a correct retry succeeds")
    from orchestrator.pipeline import call_stage, Throttle
    from design.schemas import AttemptResult, Classification

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    attempts = []
    fn = Scripted([
        {"requirement_id": "WRONG-ID", "system_type": "web", "rationale": "r"},
        {"requirement_id": "R1", "system_type": "web", "rationale": "r"},
    ])
    result = call_stage(fn, ("R1",), Classification, PipelineStage.CLASSIFIER, "inv-1",
                        "fake-model", throttle, attempts, "R1", max_attempts=2,
                        backoff_seconds=lambda a: 0.0)
    ok("the retry succeeds", isinstance(result, Classification))
    ok("two attempts logged under the same invocation_id",
       len(attempts) == 2 and {a.invocation_id for a in attempts} == {"inv-1"})
    ok("the first attempt is a validation failure naming the wrong id",
       attempts[0].result is AttemptResult.VALIDATION_FAILURE
       and "WRONG-ID" in attempts[0].error_message)
    ok("the second attempt succeeded", attempts[1].result is AttemptResult.SUCCESS)


def test_mixed_failures_exhausting_retries() -> None:
    """A transport failure, then a validation failure, then exhaustion: mixed failure
    kinds within one invocation are each logged with their own result and shape, and
    the final StageFailed reflects the LAST attempt (validation), not the first."""
    section("Mixed failure kinds exhausting retries")
    from orchestrator.pipeline import call_stage, StageFailed, Throttle
    from design.schemas import AttemptResult, Classification, FailureKind

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    attempts = []
    fn = Scripted([
        StageCallFailed("429"),
        {"requirement_id": "R1"},  # missing fields -> VALIDATION
    ])
    try:
        call_stage(fn, ("R1",), Classification, PipelineStage.CLASSIFIER, "inv-1",
                  "fake-model", throttle, attempts, "R1", max_attempts=2,
                  backoff_seconds=lambda a: 0.0)
        ok("exhaustion raises StageFailed", False)
    except StageFailed as f:
        ok("exhaustion raises StageFailed", True)
        ok("StageFailed reflects the LAST attempt's kind (VALIDATION), not the first",
           f.kind is FailureKind.VALIDATION)
    ok("both attempts logged with their own distinct result", len(attempts) == 2
       and attempts[0].result is AttemptResult.TRANSPORT_FAILURE
       and attempts[0].prompt_tokens is None
       and attempts[1].result is AttemptResult.VALIDATION_FAILURE
       and attempts[1].prompt_tokens is not None)


def test_error_summary_agrees_with_final_attempt() -> None:
    """End-to-end (not hand-built): run_requirement exhausting the Classifier must
    produce a StageError whose invocation_id, kind, message, and retry_count all agree
    with the final attempt of the invocation it names -- schema 1.1's agreement
    validator, exercised through the real pipeline path rather than trusted by
    construction alone."""
    section("StageError agrees with its final recorded attempt, end to end")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import AttemptResult, RunOutcome

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    human_fns = HumanFns(answer_questions=lambda t: [],
                         decide_at_cap=lambda r: (RunOutcome.CAP_STOPPED, "n/a"))
    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([StageCallFailed("429"), StageCallFailed("429")]),
        check_quality=None, refine_questioner=None, refine_rewriter=None,
        select_strategy=None, generate_tests=None)
    result = run_requirement(rec(requirement=REQ_A), DOC, None, None, fns, human_fns,
                             throttle, max_revisions=3, stage_configs=STAGE_CONFIGS,
                             max_attempts=2, backoff_seconds=lambda a: 0.0)
    ok("the requirement errors out", result.outcome is RunOutcome.ERROR)
    ok("exactly one error, exactly two attempts", len(result.errors) == 1
       and len(result.attempts) == 2)
    err = result.errors[0]
    invocation = [a for a in result.attempts if a.invocation_id == err.invocation_id]
    ok("the error's invocation_id resolves to attempts actually on the record",
       len(invocation) == 2)
    final = invocation[-1]
    ok("kind, message, and retry_count all agree with the final attempt",
       final.result is AttemptResult.TRANSPORT_FAILURE
       and err.message == final.error_message
       and err.retry_count == len(invocation) - 1 == 1)


def test_token_usage_validation_failures() -> None:
    """Scenario 12: two validation-failing calls then a success -> 3 attempt rows,
    every call returned so every call is metered, including the two rejected ones."""
    section("Scenario 12 -- token totals, validation failures")
    from orchestrator.pipeline import call_stage, Throttle
    from design.schemas import Classification

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    attempts = []
    fn = Scripted([
        {"requirement_id": "R1"},                          # missing fields -> VALIDATION
        {"requirement_id": "R1", "system_type": "bogus"},  # bad enum -> VALIDATION
        {"requirement_id": "R1", "system_type": "web", "rationale": "r"},  # succeeds
    ])
    result = call_stage(fn, ("R1",), Classification, PipelineStage.CLASSIFIER, "inv-1",
                        "fake-model", throttle, attempts, "R1", max_attempts=3,
                        backoff_seconds=lambda a: 0.0)
    ok("the call eventually succeeds", isinstance(result, Classification))
    ok("all three calls logged an attempt (every call returned)", len(attempts) == 3)
    ok("total tokens sum all three calls, including the two rejected outputs",
       sum(a.prompt_tokens + a.completion_tokens for a in attempts) == 45)
    ok("the record round-trips into a RequirementRunRecord.total_tokens matching that sum",
       rec(attempts=attempts).total_tokens == 45)


def test_token_usage_transport_failures() -> None:
    """Scenario 13: two transport failures then a success -> 3 attempt rows, but only
    the one that returned carries tokens -- the two 429s never reached the model.
    Kept separate from scenario 12 deliberately -- a shared scenario would hide a wrong
    assumption about WHERE tokens get attributed."""
    section("Scenario 13 -- token totals, transport failures")
    from orchestrator.pipeline import call_stage, Throttle
    from design.schemas import AttemptResult, Classification

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    attempts = []
    fn = Scripted([
        StageCallFailed("429"), StageCallFailed("429"),
        {"requirement_id": "R1", "system_type": "web", "rationale": "r"},
    ])
    result = call_stage(fn, ("R1",), Classification, PipelineStage.CLASSIFIER, "inv-1",
                        "fake-model", throttle, attempts, "R1", max_attempts=3,
                        backoff_seconds=lambda a: 0.0)
    ok("the call eventually succeeds", isinstance(result, Classification))
    ok("all three attempts are logged, not just the one that returned", len(attempts) == 3)
    ok("the two transport failures contribute zero tokens",
       all(a.prompt_tokens is None for a in attempts if a.result is AttemptResult.TRANSPORT_FAILURE))
    ok("only the successful call's tokens count toward the total",
       rec(attempts=attempts).total_tokens == attempts[2].prompt_tokens + attempts[2].completion_tokens)


def main() -> int:
    print("=" * 72)
    print("orchestrator simulation harness")
    print("=" * 72)
    for fn in (test_resume_positions, test_stage_fns_typo_is_a_typeerror, test_throttle,
              test_call_stage, test_id_check_parameters_have_no_default,
              test_requirement_id_mismatch_is_validation_at_every_stage,
              test_requirement_id_mismatch_end_to_end,
              test_backoff_timing, test_document_stages_degraded,
              test_document_id_mismatch_is_validation,
              test_document_wrong_doc_id_then_success,
              test_on_disk_round_trip,
              test_happy_path,
              test_refiner_questioner_and_rewriter_have_independent_configs,
              test_refine_questioner_failure_and_retry, test_refine_rewriter_failure_and_retry,
              test_revision_cap, test_issue_identity_reuse,
              test_suppression_persists, test_resume_skips_finished_refine_loop,
              test_resume_mid_round_completes,
              test_resume_mid_round_asks_human_when_answers_missing,
              test_id_reconciliation_mints_fresh_ids_on_collision,
              test_suppressed_issue_reflagged_is_dropped,
              test_resume_skips_finished_strategy_selector,
              test_max_revisions_must_be_at_least_two,
              test_resumed_cap_generated_then_stopped_strips_stage34,
              test_run_document_happy_path,
              test_document_context_no_leakage_three_requirements,
              test_document_context_none_vs_empty,
              test_document_context_independent_failure_mirror,
              test_document_context_dependencies_reach_both_stages,
              test_document_context_survives_resume,
              test_document_context_persists_across_refinement_rounds,
              test_document_stage_retry_allowed_before_any_requirement,
              test_document_stage_retry_allowed_but_fails_before_any_requirement,
              test_document_stage_retry_blocked_after_requirement_processed,
              test_document_context_consistent_across_resume_and_retry,
              test_error_resume_finish,
              test_interruption_mid_document_round_trip, test_validation_failure,
              test_first_attempt_success, test_validation_then_success,
              test_wrong_id_then_success, test_mixed_failures_exhausting_retries,
              test_error_summary_agrees_with_final_attempt,
              test_token_usage_validation_failures, test_token_usage_transport_failures):
        fn()
    print("\n" + "=" * 72)
    if FAILED:
        print(f"{PASSED} passed, {len(FAILED)} FAILED:")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print(f"{PASSED} checks passed, 0 failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
