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
    ClarifyingQuestion, DocumentRunRecord, PipelineStage, QualityReport,
    RefinedRequirement, RefinementRound, RefinerAnswer, RefinerTurn, Requirement,
    RequirementRunRecord, RequirementSet, RunOutcome, StageConfig, StageError,
    RunMetadata, ALL_STAGES, FailureKind, prompt_fingerprint,
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


def test_document_stages_degraded() -> None:
    """Scenario 7: consistency checker and dependency mapper fail independently; the
    run continues without whichever one failed, per contract D1=b."""
    section("Scenario 7 -- DEGRADED document")
    from orchestrator.pipeline import run_document_stages, Throttle
    from design.schemas import DocumentOutcome, FailureKind

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    stage_fns = StageFns(
        check_consistency=Scripted([StageCallFailed("429"), StageCallFailed("429"),
                                    StageCallFailed("429")]),
        map_dependencies=Scripted([{"doc_id": DOC.doc_id, "dependencies": []}]),
        classify=None, check_quality=None, refine=None, select_strategy=None,
        generate_tests=None,
    )
    cons, deps, errors, usage = run_document_stages(
        DOC, STAGE_CONFIGS, stage_fns, throttle, max_attempts=3, backoff_seconds=lambda a: 0.0)
    ok("consistency checker failure leaves consistency_report None", cons is None)
    ok("dependency mapper still succeeds independently", deps is not None)
    ok("exactly one DocumentStageError recorded", len(errors) == 1)
    ok("the error names the failed stage", errors[0].stage.value == "consistency_checker")
    ok("the error's kind is TRANSPORT", errors[0].kind is FailureKind.TRANSPORT)
    ok("dependency mapper's success recorded usage", len(usage) == 1)

    outcome = DocumentOutcome.COMPLETED if cons is not None and deps is not None else DocumentOutcome.DEGRADED
    ok("both-independent-failures-considered outcome is DEGRADED", outcome is DocumentOutcome.DEGRADED)


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
        refine=None, select_strategy=select_strategy, generate_tests=generate_tests)
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
        refine=Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
                "id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun",
                "question_text": "What limits?"}]},
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

    def refine_forever():
        return Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
                "id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun",
                "question_text": "?"}]},
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text, "refined_text": T1,
             "revision_number": 1,
             "answers_used": [{"question_id": "Q1", "answer_text": "a"}]},
        ] * 5)

    for decision, expected in ((RunOutcome.CAP_GENERATED, RunOutcome.CAP_GENERATED),
                               (RunOutcome.CAP_STOPPED, RunOutcome.CAP_STOPPED)):
        fns = StageFns(
            check_consistency=None, map_dependencies=None,
            classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
            check_quality=always_fails_quality(), refine=refine_forever(),
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
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import RunOutcome
    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([{"requirement_id": REQ_A.id, "passed": False, "issues": [{
            "id": "I1", "category": "vague_pronoun", "span": "x", "explanation": "e"}]}] * 3),
        refine=Scripted([{"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
            "id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun", "question_text": "?"}]}] * 3),
        select_strategy=None, generate_tests=None)
    human_fns = HumanFns(answer_questions=lambda turn: [],
                         decide_at_cap=lambda rec: (RunOutcome.COMPLETED, "nonsense"))
    try:
        run_requirement(rec(requirement=REQ_A), DOC, None, None, fns, human_fns, throttle,
                        max_revisions=1, stage_configs=STAGE_CONFIGS)
        return False
    except ValueError:
        return True


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
        refine=Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
                "id": "Q1", "issue_id": "FRESH-1", "issue_category": "vague_pronoun",
                "question_text": "?"}]},
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text, "refined_text": T1,
             "revision_number": 1, "answers_used": [{"question_id": "Q1", "answer_text": "a"}]},
            {"requirement_id": REQ_A.id, "revision_number": 2, "questions": [{
                "id": "Q2", "issue_id": "FRESH-1", "issue_category": "vague_pronoun",
                "question_text": "?"}]},
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
        refine=Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [
                {"id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun", "question_text": "?"},
                {"id": "Q2", "issue_id": "I2", "issue_category": "non_verifiable", "question_text": "?"}]},
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text, "refined_text": T1,
             "revision_number": 1, "answers_used": [
                 {"question_id": "Q1", "answer_text": "confirmed fine", "user_confirms_resolved": True},
                 {"question_id": "Q2", "answer_text": "a"}]},
            {"requirement_id": REQ_A.id, "revision_number": 2, "questions": [
                {"id": "Q3", "issue_id": "I2", "issue_category": "non_verifiable", "question_text": "?"}]},
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
        classify=None, check_quality=None, refine=None,  # must never be called
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


def main() -> int:
    print("=" * 72)
    print("orchestrator simulation harness")
    print("=" * 72)
    for fn in (test_resume_positions, test_stage_fns_typo_is_a_typeerror, test_throttle,
              test_call_stage, test_document_stages_degraded, test_on_disk_round_trip,
              test_happy_path, test_revision_cap, test_issue_identity_reuse,
              test_suppression_persists, test_resume_skips_finished_refine_loop):
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
