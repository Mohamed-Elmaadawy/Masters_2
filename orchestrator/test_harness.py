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
from orchestrator.pipeline import resume_at

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


# ---------------------------------------------------------------------------

def test_resume_positions() -> None:
    """Scenario 5: a failure at any stage must resume at that stage -- nothing earlier redone."""
    section("Scenario 5 -- resume_at correctness")
    err = lambda stage: [StageError(stage=stage, kind=FailureKind.TRANSPORT, message="429",
                                    retry_count=3)]
    mid_round = mk_round(1, T0, passed=False)                        # no rewrite yet
    rewritten = mk_round(1, T0, passed=False, rewrite_to=T1)
    from design.schemas import Classification, SystemType, TestStrategy, TestTechnique
    cls = Classification(requirement_id=REQ_A.id, system_type=SystemType.OTHER, rationale="r")
    strategy = TestStrategy(requirement_id=REQ_A.id, system_type=SystemType.OTHER,
                            techniques=[TestTechnique.BOUNDARY_VALUE_ANALYSIS], rationale="r")
    rounds_refined = [rewritten, mk_round(2, T1, passed=True)]

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


def main() -> int:
    print("=" * 72)
    print("orchestrator simulation harness")
    print("=" * 72)
    for fn in (test_resume_positions,):
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
