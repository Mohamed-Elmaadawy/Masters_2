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

from datetime import datetime, timezone

from design.schemas import (
    ClarifyingQuestion, PipelineStage, QualityReport, RefinedRequirement,
    RefinementRound, RefinerAnswer, RefinerTurn, Requirement, RequirementRunRecord,
    RequirementSet, RunOutcome, StageConfig, StageError, RunMetadata, ALL_STAGES,
    FailureKind, prompt_fingerprint,
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
    err = lambda stage: [StageError(stage=stage, kind=FailureKind.TRANSPORT, message="429",
                                    retry_count=3)]
    mid_round = mk_round(1, T0, passed=False)                        # no rewrite yet
    rewritten = mk_round(1, T0, passed=False, rewrite_to=T1)
    from design.schemas import (
        Classification, SystemType, TestCase, TestPlan, TestStrategy, TestTechnique,
    )
    cls = Classification(requirement_id=REQ_A.id, system_type=SystemType.OTHER, rationale="r")
    strategy = TestStrategy(requirement_id=REQ_A.id, system_type=SystemType.OTHER,
                            techniques=[TestTechnique.BOUNDARY_VALUE_ANALYSIS], rationale="r")
    rounds_refined = [rewritten, mk_round(2, T1, passed=True)]
    plan = TestPlan(requirement_id=REQ_A.id, test_cases=[
        TestCase(id="TC1", requirement_ids=[REQ_A.id],
                 technique_used=TestTechnique.BOUNDARY_VALUE_ANALYSIS,
                 title="Temperature at limit", steps=["Set temperature to the limit value."],
                 expected_result="Value is output for subsequent processing.")])

    cases = [
        ("classifier failed", dict(errors=err(PipelineStage.CLASSIFIER)), PipelineStage.CLASSIFIER),
        ("quality checker failed on round 1",
         dict(errors=err(PipelineStage.QUALITY_CHECKER), classification=cls),
         PipelineStage.QUALITY_CHECKER),
        ("refiner failed mid-round, nothing rewritten yet",
         dict(errors=err(PipelineStage.REFINER), classification=cls, rounds=[mid_round]),
         PipelineStage.REFINER),
        ("quality checker failed on round 2, round 1 already rewrote",
         dict(errors=err(PipelineStage.QUALITY_CHECKER), classification=cls, rounds=[rewritten]),
         PipelineStage.QUALITY_CHECKER),
        ("strategy selector failed",
         dict(errors=err(PipelineStage.STRATEGY_SELECTOR), classification=cls, rounds=rounds_refined),
         PipelineStage.STRATEGY_SELECTOR),
        ("test generator failed",
         dict(errors=err(PipelineStage.TEST_GENERATOR), classification=cls, rounds=rounds_refined,
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
                 classify=not_provided, check_quality=not_provided, refine=not_provided,
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
                    classify=not_provided, check_quality=not_provided, refine=not_provided,
                    select_strategy=not_provided, generate_tests=not_provided)
    ok("StageFns constructs with exactly the right fields", real.classify is not_provided)


def test_call_stage() -> None:
    """The narrow-except wrapper: success, TRANSPORT, VALIDATION, and scenario 14 --
    OTHER, the one branch that must actually fire, not just be documented as possible."""
    section("call_stage")
    from orchestrator.pipeline import call_stage, StageFailed, Throttle
    from design.schemas import Classification, SystemType, FailureKind, TokenUsage

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)

    # -- success --
    usage: list[TokenUsage] = []
    fn = Scripted([{"requirement_id": "R1", "system_type": "web", "rationale": "r"}])
    result = call_stage(fn, ("R1",), Classification, PipelineStage.CLASSIFIER,
                        "fake-model", throttle, usage)
    ok("call_stage returns a validated model", isinstance(result, Classification))
    ok("a successful call records one usage entry", len(usage) == 1)
    ok("usage entry carries the right stage", usage[0].stage is PipelineStage.CLASSIFIER)

    # -- TRANSPORT: exhausts retries, no usage recorded --
    usage2: list[TokenUsage] = []
    fn2 = Scripted([StageCallFailed("429"), StageCallFailed("429"), StageCallFailed("429")])
    try:
        call_stage(fn2, ("R1",), Classification, PipelineStage.CLASSIFIER, "fake-model",
                  throttle, usage2, max_attempts=3, backoff_seconds=lambda a: 0.0)
        ok("TRANSPORT exhaustion raises StageFailed", False)
    except StageFailed as f:
        ok("TRANSPORT exhaustion raises StageFailed", True)
        ok("StageFailed.kind is TRANSPORT", f.kind is FailureKind.TRANSPORT)
        ok("retry_count is attempts-1", f.retry_count == 2)
    ok("no usage recorded for pure transport failures", usage2 == [])

    # -- VALIDATION: the call succeeded, tokens were spent on rejected output --
    usage3: list[TokenUsage] = []
    fn3 = Scripted([{"requirement_id": "R1"}])  # missing system_type, rationale
    try:
        call_stage(fn3, ("R1",), Classification, PipelineStage.CLASSIFIER, "fake-model",
                  throttle, usage3, max_attempts=1, backoff_seconds=lambda a: 0.0)
        ok("VALIDATION failure raises StageFailed", False)
    except StageFailed as f:
        ok("VALIDATION failure raises StageFailed", True)
        ok("StageFailed.kind is VALIDATION", f.kind is FailureKind.VALIDATION)
    ok("a validation failure still records usage (tokens were spent)", len(usage3) == 1)

    # -- Scenario 14: OTHER, and the negative it's paired with --
    usage4: list[TokenUsage] = []
    fn4 = Scripted([KeyError("unexpected")])
    try:
        call_stage(fn4, ("R1",), Classification, PipelineStage.CLASSIFIER, "fake-model",
                  throttle, usage4, max_attempts=1, backoff_seconds=lambda a: 0.0)
        ok("an unexpected exception type raises StageFailed(OTHER)", False)
    except StageFailed as f:
        ok("an unexpected exception type raises StageFailed(OTHER)", True)
        ok("StageFailed.kind is OTHER", f.kind is FailureKind.OTHER)
        ok("message names the exception class", "KeyError" in f.message)
    ok("no usage recorded for OTHER (never reached the model)", usage4 == [])

    # Negative: a bug in code CALLING call_stage (outside the guarded line) still
    # crashes, rather than being caught and filed as OTHER. call_stage itself has no
    # surrounding try/except beyond the one line that calls stage_fn -- demonstrated by
    # confirming a bug in the *test's own* orchestration crashes as a real exception.
    # Uses a fresh Scripted (fn's one behavior was already consumed above) so the
    # call_stage call itself succeeds -- the AttributeError must come from the
    # caller's own code, not from stage_fn running dry.
    fn5 = Scripted([{"requirement_id": "R1", "system_type": "web", "rationale": "r"}])
    def broken_caller():
        result = call_stage(fn5, ("R1",), Classification, PipelineStage.CLASSIFIER,
                            "fake-model", throttle, usage)
        return result.nonexistent_attribute  # AttributeError, not from inside call_stage
    try:
        broken_caller()
        ok("a caller bug (outside call_stage's guarded line) still crashes", False)
    except AttributeError:
        ok("a caller bug (outside call_stage's guarded line) still crashes", True)


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


def main() -> int:
    print("=" * 72)
    print("orchestrator simulation harness")
    print("=" * 72)
    for fn in (test_resume_positions, test_stage_fns_typo_is_a_typeerror, test_throttle,
              test_call_stage):
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
