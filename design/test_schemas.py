"""
Regression tests for schemas.py. Run after every schema change:

    python design/test_schemas.py

Plain script, no pytest -- one less dependency, and it prints a readable report rather
than a dot per test. Exits non-zero if anything fails, so it can be wired into CI later.

Sections mirror the gaps closed in DESIGN_NOTES.md, so a failure points at the design
decision it belongs to.

Three layers, each catching what the others cannot:

  1. SELF-ENUMERATING -- reads _OUTCOME_RULES / _DOCUMENT_OUTCOME_RULES and violates
     every rule of every outcome, one at a time. Add a rule to either table and its
     tests appear here automatically. Verifies rules are *enforced*.
  2. ANCHORS (test_rule_table_anchors) -- pins the rules that came from real bugs, since
     layer 1 cannot notice a rule being deleted: a deleted rule simply stops being
     enumerated. Verifies rules *exist*.
  3. MUTATION RUNS (manual, see DESIGN_NOTES.md) -- break a check on purpose and confirm
     the suite goes red. Verifies the tests actually discriminate. This layer has twice
     caught weaknesses the first two could not.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError

from design.schemas import (
    ALL_STAGES, Classification, ELIGIBLE_TECHNIQUES, ClarifyingQuestion, ConsistencyConflict,
    ConsistencyReport, DependencyLink, DependencyReport, DocumentOutcome,
    DocumentRunRecord, DocumentStage, DocumentStageError, FailureKind, Issue, IssueCategory,
    PipelineStage, QualityReport, RefinedRequirement, RefinementRound, RefinerAnswer,
    RefinerTurn, Requirement, RequirementRunRecord, RequirementSet, RunMetadata,
    RunOutcome, StageConfig, StageError, SystemType, TestCase, TestPlan, TestStrategy,
    TestTechnique, fields_carrying_requirement_id, prompt_fingerprint,
)
from design.schemas import _DOCUMENT_OUTCOME_RULES, _OUTCOME_RULES

PASSED = 0
FAILED: list[str] = []


def ok(label: str, condition: bool = True) -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(label)
        print(f"    FAIL  {label}")


def accepts(label: str, fn) -> None:
    try:
        fn()
        ok(label)
    except ValidationError as e:
        FAILED.append(label)
        print(f"    FAIL  {label} -- rejected but should be valid: {e.errors()[0]['msg']}")


def rejects(label: str, fn) -> None:
    try:
        fn()
        FAILED.append(label)
        print(f"    FAIL  {label} -- accepted but should be rejected")
    except ValidationError:
        ok(label)


def section(name: str) -> None:
    print(f"\n{name}")


# ---------------------------------------------------------------------------
# Shared fixtures -- real THEMAS requirements (Fischbach et al. 2022)
# ---------------------------------------------------------------------------

T0 = "Temperatures that do not exceed these limits shall be output for subsequent processing."
T1 = "Temperatures within the valid temperature range defined in THEMAS-REQ-B shall be output for subsequent processing."
T2 = "Temperatures within the valid temperature range defined in THEMAS-REQ-B shall be output to the heating/cooling mode process."

REQ_D = Requirement(id="THEMAS-REQ-D", source_doc_id="themas-fischbach2022", text=T0)
REQ_G = Requirement(
    id="THEMAS-REQ-G", source_doc_id="themas-fischbach2022",
    text="Each thermostat shall have a unique identifier by which that thermostat is identified in the THEMAS system.",
)
REQ_B = Requirement(
    id="THEMAS-REQ-B", source_doc_id="themas-fischbach2022",
    text="If the current temperature value is outside the valid temperature range, the THEMAS system shall output an invalid temperature status.",
)
REQ_SET = RequirementSet(doc_id="themas-fischbach2022", requirements=[REQ_D, REQ_G, REQ_B])

CLS = Classification(requirement_id=REQ_D.id, system_type=SystemType.OTHER,
                     rationale="Embedded thermostat controller.")
STRATEGY = TestStrategy(requirement_id=REQ_D.id, system_type=SystemType.OTHER,
                        techniques=[TestTechnique.BOUNDARY_VALUE_ANALYSIS],
                        rationale="Numeric temperature range.")
PLAN = TestPlan(requirement_id=REQ_D.id, test_cases=[TestCase(
    id="TC-D-1", requirement_ids=[REQ_D.id],
    technique_used=TestTechnique.BOUNDARY_VALUE_ANALYSIS,
    title="Temperature at the upper limit", steps=["Set temperature to the upper limit"],
    expected_result="Value is output for subsequent processing.")])

VAGUE = Issue(id="I1", category=IssueCategory.VAGUE_PRONOUN, span="these limits",
              explanation="The referent of 'these limits' is not defined.")
UNVERIFIABLE = Issue(id="I2", category=IssueCategory.NON_VERIFIABLE,
                     span="subsequent processing",
                     explanation="No observable pass/fail criterion.")
A1 = RefinerAnswer(question_id="Q1", answer_text="The valid range defined in THEMAS-REQ-B.")
A2 = RefinerAnswer(question_id="Q2", answer_text="Output to the heating/cooling mode process.")


def mk_round(n, text, issues=(), questions=(), answers=(), rewrite_to=None,
             requirement_id=REQ_D.id, suppressed=()):
    """Build one coherent RefinementRound. `questions` is [(qid, issue), ...]."""
    issues = list(issues)
    turn = RefinerTurn(
        requirement_id=requirement_id, revision_number=n,
        questions=[ClarifyingQuestion(id=qid, issue_id=iss.id, issue_category=iss.category,
                                      question_text=f"About {iss.span!r}?")
                   for qid, iss in questions]) if questions else None
    answers = list(answers)
    rewrite = RefinedRequirement(requirement_id=requirement_id, original_text=text,
                                 refined_text=rewrite_to, revision_number=n,
                                 answers_used=answers) if rewrite_to else None
    return RefinementRound(
        revision_number=n, text_checked=text,
        quality_report=QualityReport(requirement_id=requirement_id, passed=not issues,
                                     issues=issues),
        turn=turn, answers=answers, rewrite=rewrite,
        suppressed_issue_ids=list(suppressed))


# One passing round: the clean, first-try path.
ROUNDS_CLEAN = [mk_round(1, T0)]
# Refine once, then pass: the ordinary refined path.
ROUNDS_REFINED = [
    mk_round(1, T0, [VAGUE], [("Q1", VAGUE)], [A1], rewrite_to=T1),
    mk_round(2, T1),
]
# Refine twice, still failing: the revision-cap path.
ROUNDS_CAPPED = [
    mk_round(1, T0, [VAGUE, UNVERIFIABLE], [("Q1", VAGUE), ("Q2", UNVERIFIABLE)], [A1, A2],
             rewrite_to=T1),
    mk_round(2, T1, [UNVERIFIABLE], [("Q2", UNVERIFIABLE)], [A2], rewrite_to=T2),
    mk_round(3, T2, [UNVERIFIABLE]),
]

VALID_RECORDS: dict[RunOutcome, dict] = {
    RunOutcome.IN_PROGRESS: dict(),
    RunOutcome.COMPLETED: dict(classification=CLS, rounds=ROUNDS_REFINED,
                               test_strategy=STRATEGY, test_plan=PLAN),
    RunOutcome.CAP_GENERATED: dict(classification=CLS, rounds=ROUNDS_CAPPED,
                                   test_strategy=STRATEGY, test_plan=PLAN,
                                   cap_reason="Referent still disputed; tests useful."),
    RunOutcome.CAP_STOPPED: dict(classification=CLS, rounds=ROUNDS_CAPPED,
                                 cap_reason="Too defective to test meaningfully."),
    RunOutcome.ERROR: dict(errors=[StageError(stage=PipelineStage.CLASSIFIER,
                                              kind=FailureKind.TRANSPORT,
                                              message="429 rate limit", retry_count=3)]),
}
RECORD_EXTRAS = {"cap_reason": "x", "classification": CLS,
                 "test_strategy": STRATEGY, "test_plan": PLAN}

STAGES = {s: StageConfig(model="gemini-2.0-flash", prompt_hash=prompt_fingerprint(f"prompt for {s}"))
          for s in ALL_STAGES}
META = RunMetadata(run_id="run-test-001", started_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
                   stages=STAGES, prompt_version="v3")

CONS = ConsistencyReport(doc_id=REQ_SET.doc_id, conflicts=[ConsistencyConflict(
    requirement_ids=["THEMAS-REQ-B", "THEMAS-REQ-D"], explanation="Both constrain the range.")])
DEP = DependencyReport(doc_id=REQ_SET.doc_id, dependencies=[DependencyLink(
    from_requirement_id="THEMAS-REQ-D", to_requirement_id="THEMAS-REQ-B",
    explanation="D's limits are defined by B.")])
CE = DocumentStageError(stage=DocumentStage.CONSISTENCY_CHECKER, kind=FailureKind.TRANSPORT,
                        message="429", retry_count=2)
DE = DocumentStageError(stage=DocumentStage.DEPENDENCY_MAPPER, kind=FailureKind.VALIDATION,
                        message="malformed JSON")

VALID_DOCS: dict[DocumentOutcome, dict] = {
    DocumentOutcome.IN_PROGRESS: dict(),
    DocumentOutcome.COMPLETED: dict(consistency_report=CONS, dependency_report=DEP),
    DocumentOutcome.DEGRADED: dict(errors=[CE], dependency_report=DEP),
}
DOC_EXTRAS = {"consistency_report": CONS, "dependency_report": DEP}
# Aliases used by the reference tests, for readability at the call sites.
CONS_OK, DEP_OK = CONS, DEP


def doc(**kw):
    return DocumentRunRecord(requirement_set=REQ_SET, metadata=META, **kw)


def rec(**kw):
    kw.setdefault("run_id", META.run_id)
    return RequirementRunRecord(requirement=REQ_D, **kw)


# ---------------------------------------------------------------------------

def test_non_empty_guards() -> None:
    """Fields an LLM could return as "" or [] must be rejected at the boundary."""
    section("Non-empty guards")
    rejects("Requirement.text empty", lambda: Requirement(id="R1", text=""))
    rejects("Classification.rationale empty",
            lambda: Classification(requirement_id="R1", system_type=SystemType.WEB, rationale=""))
    rejects("TestStrategy.techniques empty",
            lambda: TestStrategy(requirement_id="R1", system_type=SystemType.WEB,
                                 techniques=[], rationale="r"))
    rejects("TestCase.steps empty",
            lambda: TestCase(id="T", requirement_ids=["R1"], technique_used=TestTechnique.EXPLORATORY,
                             title="t", steps=[], expected_result="e"))
    rejects("TestPlan.test_cases empty", lambda: TestPlan(requirement_id="R1", test_cases=[]))
    rejects("RequirementSet.requirements empty", lambda: RequirementSet(requirements=[]))
    rejects("RefinedRequirement.answers_used empty",
            lambda: RefinedRequirement(requirement_id="R1", original_text="a", refined_text="b",
                                       revision_number=1, answers_used=[]))
    rejects("ConsistencyConflict needs 2 ids",
            lambda: ConsistencyConflict(requirement_ids=["R1"], explanation="e"))
    rejects("QualityReport passed=False with no issues",
            lambda: QualityReport(requirement_id="R1", passed=False, issues=[]))
    rejects("QualityReport passed=True with issues",
            lambda: QualityReport(requirement_id="R1", passed=True, issues=[VAGUE]))
    rejects("RefinementRound.text_checked empty",
            lambda: RefinementRound(revision_number=1, text_checked="",
                                    quality_report=QualityReport(requirement_id="R1",
                                                                 passed=True, issues=[])))
    rejects("RefinementRound.revision_number below 1",
            lambda: mk_round(0, T0))


def test_gap1_both_paths_converge() -> None:
    """Gap 1: stages 3/4 take a Requirement, so clean requirements have a carrier."""
    section("Gap 1 -- clean and refined paths both reach stage 3")
    clean = RequirementRunRecord(
        requirement=REQ_G, run_id=META.run_id, outcome=RunOutcome.COMPLETED,
        classification=Classification(requirement_id=REQ_G.id, system_type=SystemType.OTHER,
                                      rationale="Embedded."),
        rounds=[mk_round(1, REQ_G.text, requirement_id=REQ_G.id)],
        test_strategy=TestStrategy(requirement_id=REQ_G.id, system_type=SystemType.OTHER,
                                   techniques=[TestTechnique.EQUIVALENCE_PARTITIONING],
                                   rationale="Uniqueness."),
        test_plan=TestPlan(requirement_id=REQ_G.id, test_cases=[TestCase(
            id="TC-G-1", requirement_ids=[REQ_G.id],
            technique_used=TestTechnique.EQUIVALENCE_PARTITIONING, title="Duplicate id rejected",
            steps=["Register two thermostats with the same id"], expected_result="Second rejected.")]))
    ok("clean path produced no rewrite", clean.final_requirement is None)
    ok("clean final_text is the original", clean.final_text == REQ_G.text)

    refined = rec(outcome=RunOutcome.COMPLETED, **VALID_RECORDS[RunOutcome.COMPLETED])
    ok("refined final_text is the refined text", refined.final_text == T1)

    for label, r in (("clean", clean), ("refined", refined)):
        carrier = Requirement(id=r.requirement.id, text=r.final_text,
                              source_doc_id=r.requirement.source_doc_id)
        ok(f"{label} path carrier is a Requirement matching final_text",
           isinstance(carrier, Requirement) and carrier.text == r.final_text)

    ok("final_text is read-only", _raises_attribute_error(clean))
    ok("final_text ignored in constructor",
       rec(final_text="spoofed", classification=CLS).final_text == REQ_D.text)


def _raises_attribute_error(record) -> bool:
    try:
        record.final_text = "x"
        return False
    except AttributeError:
        return True


def test_gap2_requirement_outcomes() -> None:
    """Gap 2: outcome states, plus the self-enumerating rule matrix."""
    section("Gap 2 -- requirement run outcomes")
    for outcome, kw in VALID_RECORDS.items():
        accepts(f"valid {outcome.value} record", lambda o=outcome, k=kw: rec(outcome=o, **k))
    ok("bare record defaults to IN_PROGRESS", rec().outcome is RunOutcome.IN_PROGRESS)
    ok("classifier failure is persistable", rec(outcome=RunOutcome.ERROR,
        errors=[StageError(stage=PipelineStage.CLASSIFIER, kind=FailureKind.TRANSPORT,
                           message="429")]).errors[0].stage
        is PipelineStage.CLASSIFIER)
    rejects("negative retry_count",
            lambda: StageError(stage=PipelineStage.REFINER, kind=FailureKind.TRANSPORT,
                               message="x", retry_count=-1))

    override_rounds = [
        mk_round(1, T0, [VAGUE], [("Q1", VAGUE)],
                 [RefinerAnswer(question_id="Q1", answer_text="REQ-B's range.",
                                user_confirms_resolved=True)], rewrite_to=T1),
        mk_round(2, T1),
    ]
    ok("used_human_override true when confirmed",
       rec(classification=CLS, rounds=override_rounds).used_human_override is True)
    ok("used_human_override false otherwise",
       rec(classification=CLS, rounds=ROUNDS_REFINED).used_human_override is False)

    rejects("cap outcome with no rewrite in any round",
            lambda: rec(outcome=RunOutcome.CAP_STOPPED, classification=CLS,
                        cap_reason="x", rounds=[mk_round(1, T0, [VAGUE])]))

    checked = 0
    for outcome, rule in _OUTCOME_RULES.items():
        base = VALID_RECORDS[outcome]
        for name in rule.required:
            rejects(f"{outcome.value}: missing {name}",
                    lambda o=outcome, b=base, n=name: rec(outcome=o, **{k: v for k, v in b.items() if k != n}))
            checked += 1
        for name in rule.forbidden:
            rejects(f"{outcome.value}: forbidden {name} present",
                    lambda o=outcome, b=base, n=name: rec(outcome=o, **{**b, n: RECORD_EXTRAS[n]}))
            checked += 1
        for name in rule.non_empty:
            rejects(f"{outcome.value}: {name} empty",
                    lambda o=outcome, b=base, n=name: rec(outcome=o, **{**b, n: []}))
            checked += 1
        if rule.last_report_passed is not None:
            wrong = ROUNDS_CAPPED if rule.last_report_passed else ROUNDS_REFINED
            rejects(f"{outcome.value}: last round's report in the wrong state",
                    lambda o=outcome, b=base, w=wrong: rec(outcome=o, **{**b, "rounds": w}))
            checked += 1
    print(f"    ({checked} rules enumerated from _OUTCOME_RULES)")


def test_failure_kind() -> None:
    """FailureKind distinguishes why a stage call failed -- see the design doc."""
    section("FailureKind")
    rejects("StageError without kind",
            lambda: StageError(stage=PipelineStage.CLASSIFIER, message="x"))
    accepts("StageError with kind=TRANSPORT",
            lambda: StageError(stage=PipelineStage.CLASSIFIER, kind=FailureKind.TRANSPORT,
                               message="429"))
    accepts("StageError with kind=VALIDATION",
            lambda: StageError(stage=PipelineStage.CLASSIFIER, kind=FailureKind.VALIDATION,
                               message="schema rejected"))
    accepts("StageError with kind=OTHER",
            lambda: StageError(stage=PipelineStage.CLASSIFIER, kind=FailureKind.OTHER,
                               message="KeyError: 'foo'"))
    accepts("DocumentStageError with kind",
            lambda: DocumentStageError(stage=DocumentStage.CONSISTENCY_CHECKER,
                                       kind=FailureKind.TRANSPORT, message="429"))


def test_gap5_refinement_trajectory() -> None:
    """Gap 5: rounds make the trajectory reconstructible and its continuity checkable."""
    section("Gap 5 -- refinement trajectory")
    capped = rec(outcome=RunOutcome.CAP_STOPPED, classification=CLS, rounds=ROUNDS_CAPPED,
                 cap_reason="Still unverifiable after 2 revisions.")
    ok("convergence curve is one attribute", capped.issues_per_round == [2, 1, 1])
    ok("refined record's curve converges to zero",
       rec(classification=CLS, rounds=ROUNDS_REFINED).issues_per_round == [1, 0])
    ok("clean record's curve is a single zero",
       rec(classification=CLS, rounds=ROUNDS_CLEAN).issues_per_round == [0])
    ok("each round records the text it checked",
       [r.text_checked for r in ROUNDS_CAPPED] == [T0, T1, T2])
    ok("final_requirement is the most recent rewrite",
       capped.final_requirement.revision_number == 2)
    ok("final_text is the last text checked", capped.final_text == T2)

    trailing = rec(classification=CLS, rounds=[
        mk_round(1, T0, [VAGUE], [("Q1", VAGUE)], [A1], rewrite_to=T1)])
    ok("a trailing rewrite is the latest text", trailing.final_text == T1)

    # Between rounds
    rejects("round 1 not checking the requirement's own text",
            lambda: rec(rounds=[mk_round(1, "SOMETHING ELSE")]))
    rejects("round 2 not checking round 1's rewrite",
            lambda: rec(rounds=[mk_round(1, T0, [VAGUE], [("Q1", VAGUE)], [A1], rewrite_to=T1),
                                mk_round(2, "UNRELATED TEXT")]))
    rejects("round 2 after a round that produced no rewrite",
            lambda: rec(rounds=[mk_round(1, T0, [VAGUE], [("Q1", VAGUE)], [A1]),
                                mk_round(2, T1)]))
    rejects("revision numbers not starting at 1",
            lambda: rec(rounds=[mk_round(2, T0)]))
    rejects("round reporting on a different requirement",
            lambda: rec(rounds=[mk_round(1, T0, requirement_id="OTHER-REQ")]))

    # Inside a round
    passed_qr = QualityReport(requirement_id=REQ_D.id, passed=True, issues=[])
    failed_qr = QualityReport(requirement_id=REQ_D.id, passed=False, issues=[VAGUE])
    turn = RefinerTurn(requirement_id=REQ_D.id, revision_number=1, questions=[ClarifyingQuestion(
        id="Q1", issue_id=VAGUE.id, issue_category=VAGUE.category, question_text="?")])
    rejects("a passing round that still asked a question",
            lambda: RefinementRound(revision_number=1, text_checked=T0,
                                    quality_report=passed_qr, turn=turn))
    rejects("a passing round that still rewrote",
            lambda: RefinementRound(revision_number=1, text_checked=T0, quality_report=passed_qr,
                                    rewrite=RefinedRequirement(
                                        requirement_id=REQ_D.id, original_text=T0, refined_text=T1,
                                        revision_number=1, answers_used=[A1])))
    rejects("answers with nothing having been asked",
            lambda: RefinementRound(revision_number=1, text_checked=T0,
                                    quality_report=failed_qr, answers=[A1]))
    rejects("answer to a question not asked this round",
            lambda: RefinementRound(revision_number=1, text_checked=T0, quality_report=failed_qr,
                                    turn=turn,
                                    answers=[RefinerAnswer(question_id="Q-NOPE", answer_text="?")]))
    rejects("question about an issue this round did not raise",
            lambda: RefinementRound(
                revision_number=1, text_checked=T0, quality_report=failed_qr,
                turn=RefinerTurn(requirement_id=REQ_D.id, revision_number=1,
                                 questions=[ClarifyingQuestion(
                                     id="Q9", issue_id="I-GHOST",
                                     issue_category=IssueCategory.VAGUE_PRONOUN,
                                     question_text="?")])))
    rejects("same question id asked twice in one round",
            lambda: RefinementRound(
                revision_number=1, text_checked=T0, quality_report=failed_qr,
                turn=RefinerTurn(requirement_id=REQ_D.id, revision_number=1, questions=[
                    ClarifyingQuestion(id="Q1", issue_id=VAGUE.id, issue_category=VAGUE.category,
                                       question_text="a"),
                    ClarifyingQuestion(id="Q1", issue_id=VAGUE.id, issue_category=VAGUE.category,
                                       question_text="b")])))
    rejects("rewrite with no answers behind it",  # caught by the subset check
            lambda: RefinementRound(revision_number=1, text_checked=T0, quality_report=failed_qr,
                                    turn=turn, rewrite=RefinedRequirement(
                                        requirement_id=REQ_D.id, original_text=T0, refined_text=T1,
                                        revision_number=1, answers_used=[A1])))
    rejects("rewrite of text this round did not check",
            lambda: RefinementRound(revision_number=1, text_checked=T0, quality_report=failed_qr,
                                    turn=turn, answers=[A1], rewrite=RefinedRequirement(
                                        requirement_id=REQ_D.id, original_text="WRONG SOURCE",
                                        refined_text=T1, revision_number=1, answers_used=[A1])))
    rejects("rewrite using an answer not given this round",
            lambda: RefinementRound(revision_number=1, text_checked=T0, quality_report=failed_qr,
                                    turn=turn, answers=[A1], rewrite=RefinedRequirement(
                                        requirement_id=REQ_D.id, original_text=T0, refined_text=T1,
                                        revision_number=1,
                                        answers_used=[RefinerAnswer(question_id="Q1",
                                                                    answer_text="DIFFERENT")])))
    rejects("turn numbered differently from its round",
            lambda: RefinementRound(revision_number=2, text_checked=T0, quality_report=failed_qr,
                                    turn=turn))


def test_gap3_document_record() -> None:
    """Gap 3: document-level record, its rules, and its cross-field checks."""
    section("Gap 3 -- document run record")
    for outcome, kw in VALID_DOCS.items():
        accepts(f"valid {outcome.value} document", lambda o=outcome, k=kw: doc(outcome=o, **k))
    accepts("both document stages failed",
            lambda: doc(outcome=DocumentOutcome.DEGRADED, errors=[CE, DE]))
    accepts("mid-run: error recorded while still IN_PROGRESS",
            lambda: doc(outcome=DocumentOutcome.IN_PROGRESS, errors=[CE]))

    checked = 0
    for outcome, rule in _DOCUMENT_OUTCOME_RULES.items():
        base = VALID_DOCS[outcome]
        for name in rule.required:
            rejects(f"doc {outcome.value}: missing {name}",
                    lambda o=outcome, b=base, n=name: doc(outcome=o, **{k: v for k, v in b.items() if k != n}))
            checked += 1
        for name in rule.forbidden:
            rejects(f"doc {outcome.value}: forbidden {name}",
                    lambda o=outcome, b=base, n=name: doc(outcome=o, **{**b, n: DOC_EXTRAS[n]}))
            checked += 1
        for name in rule.non_empty:
            rejects(f"doc {outcome.value}: {name} empty",
                    lambda o=outcome, b=base, n=name: doc(outcome=o, **{**b, n: []}))
            checked += 1
    print(f"    ({checked} rules enumerated from _DOCUMENT_OUTCOME_RULES)")

    rejects("DEGRADED without the non-failed stage's report",
            lambda: doc(outcome=DocumentOutcome.DEGRADED, errors=[CE]))
    # `errors` is a log of failed attempts, not current state -- so a stage that failed
    # once and succeeded on a retry legitimately has both. This is what makes retrying
    # one document stage possible without erasing the failure or redoing the document.
    accepts("COMPLETED carrying an earlier failure (stage retried successfully)",
            lambda: doc(outcome=DocumentOutcome.COMPLETED, consistency_report=CONS,
                        dependency_report=DEP, errors=[CE]))
    rejects("DEGRADED with both reports present",
            lambda: doc(outcome=DocumentOutcome.DEGRADED, errors=[CE], consistency_report=CONS,
                        dependency_report=DEP))
    rejects("a missing report with no failure explaining it",
            lambda: doc(outcome=DocumentOutcome.DEGRADED,
                        errors=[DocumentStageError(stage=DocumentStage.DEPENDENCY_MAPPER,
                                                   kind=FailureKind.TRANSPORT,
                                                   message="x")]))
    rejects("same document stage failing twice",
            lambda: doc(outcome=DocumentOutcome.DEGRADED, errors=[CE, CE], dependency_report=DEP))
    rejects("record for a requirement not in the set",
            lambda: doc(requirement_records=[RequirementRunRecord(
                requirement=Requirement(id="NOT-IN-SET", text="x"), run_id=META.run_id)]))
    rejects("two records for the same requirement",
            lambda: doc(requirement_records=[rec(), rec()]))
    rejects("record text drifted from the set",
            lambda: doc(requirement_records=[RequirementRunRecord(
                requirement=Requirement(id="THEMAS-REQ-D", text="DIFFERENT",
                                        source_doc_id="themas-fischbach2022"),
                run_id=META.run_id)]))
    rejects("StageError cannot name a document stage",
            lambda: StageError(stage=DocumentStage.CONSISTENCY_CHECKER,
                               kind=FailureKind.TRANSPORT, message="x"))
    rejects("DocumentStageError cannot name a requirement stage",
            lambda: DocumentStageError(stage=PipelineStage.CLASSIFIER,
                                       kind=FailureKind.TRANSPORT, message="x"))

    ok("fresh document: everything pending",
       doc().pending_requirement_ids == [REQ_D.id, REQ_G.id, REQ_B.id])
    finished = rec(outcome=RunOutcome.COMPLETED, **VALID_RECORDS[RunOutcome.COMPLETED])
    ok("a finished requirement drops out of pending",
       doc(requirement_records=[finished]).pending_requirement_ids == [REQ_G.id, REQ_B.id])
    # The three states that must STAY pending, so a resume pass picks them up again.
    ok("an errored requirement stays pending",
       doc(requirement_records=[rec(outcome=RunOutcome.ERROR, errors=[StageError(
           stage=PipelineStage.TEST_GENERATOR, kind=FailureKind.TRANSPORT,
           message="429", retry_count=3)])]
           ).pending_requirement_ids == [REQ_D.id, REQ_G.id, REQ_B.id])
    ok("an interrupted (IN_PROGRESS) requirement stays pending",
       doc(requirement_records=[rec()]).pending_requirement_ids
       == [REQ_D.id, REQ_G.id, REQ_B.id])
    for outcome in (RunOutcome.CAP_GENERATED, RunOutcome.CAP_STOPPED):
        ok(f"a {outcome.value} requirement counts as finished",
           REQ_D.id not in doc(requirement_records=[
               rec(outcome=outcome, **VALID_RECORDS[outcome])]).pending_requirement_ids)
    on_disk = doc(outcome=DocumentOutcome.COMPLETED, consistency_report=CONS, dependency_report=DEP)
    ok("D2b: document file valid with requirement_records empty",
       on_disk.requirement_records == [] and len(on_disk.pending_requirement_ids) == 3)


def test_gap4_provenance() -> None:
    """Gap 4: run metadata, per-stage config, prompt fingerprinting."""
    section("Gap 4 -- run provenance")
    text = "You are classifying a software requirement by system type."
    ok("fingerprint stable", prompt_fingerprint(text) == prompt_fingerprint(text))
    ok("fingerprint changes on a one-character edit",
       prompt_fingerprint(text) != prompt_fingerprint(text + "."))
    ok("temperature defaults to 1.0", META.temperature == 1.0)

    mixed = {**STAGES, "test_generator": StageConfig(
        model="llama-3.3-70b-versatile", prompt_hash=prompt_fingerprint("gen prompt"))}
    accepts("mixed models across stages",
            lambda: RunMetadata(run_id="r", started_at=datetime.now(timezone.utc),
                                stages=mixed, prompt_version="v3"))
    rejects("stages missing an entry",
            lambda: RunMetadata(run_id="r", started_at=datetime.now(timezone.utc),
                                stages={k: v for k, v in STAGES.items() if k != "refiner"},
                                prompt_version="v3"))
    rejects("stages with an unknown name",
            lambda: RunMetadata(run_id="r", started_at=datetime.now(timezone.utc),
                                stages={**STAGES, "reviewer": STAGES["classifier"]},
                                prompt_version="v3"))
    rejects("metadata required on a document record",
            lambda: DocumentRunRecord(requirement_set=REQ_SET))

    edited = {**STAGES, "quality_checker": StageConfig(
        model="gemini-2.0-flash", prompt_hash=prompt_fingerprint("EDITED prompt"))}
    b = RunMetadata(run_id="b", started_at=datetime.now(timezone.utc), stages=edited,
                    prompt_version="v3")
    drifted = [s for s in ALL_STAGES if META.stages[s].prompt_hash != b.stages[s].prompt_hash]
    ok("forgotten version bump is visible via hash", drifted == ["quality_checker"])


def test_cross_field_agreement() -> None:
    """Two fields that must agree, with nothing structural forcing them to.

    This is the failure pattern this schema has produced repeatedly. Several of these
    came from external review passes looking for exactly that shape, which is why they
    get their own section rather than being scattered through the gap sections.
    """
    section("Cross-field agreement")
    for cat in (IssueCategory.INCONSISTENT, IssueCategory.CIRCULAR_DEPENDENCY):
        rejects(f"{cat.value} with no related_requirement_ids",
                lambda c=cat: Issue(id="X", category=c, explanation="e"))
    accepts("relational category with related ids",
            lambda: Issue(id="X", category=IssueCategory.INCONSISTENT, explanation="e",
                          related_requirement_ids=["THEMAS-REQ-B"]))
    accepts("non-relational category needs none",
            lambda: Issue(id="X", category=IssueCategory.VAGUE_PRONOUN, explanation="e"))

    for bad in (-5.0, 2.5, 999.0):
        rejects(f"temperature={bad}",
                lambda t=bad: RunMetadata(run_id="r", started_at=datetime.now(timezone.utc),
                                          stages=STAGES, prompt_version="v1", temperature=t))
    for good in (0.0, 1.0, 2.0):
        accepts(f"temperature={good}",
                lambda t=good: RunMetadata(run_id="r", started_at=datetime.now(timezone.utc),
                                           stages=STAGES, prompt_version="v1", temperature=t))

    rejects("run_id missing from a requirement record",
            lambda: RequirementRunRecord(requirement=REQ_D))
    rejects("requirement record from a different run",
            lambda: doc(requirement_records=[RequirementRunRecord(requirement=REQ_D,
                                                                  run_id="some-other-run")]))
    accepts("requirement record from this run", lambda: doc(requirement_records=[rec()]))
    ok("a lone requirement file names its run", rec().run_id == META.run_id)


def test_duplicate_keys() -> None:
    """Lists that are semantically sets or mappings must reject duplicate keys.

    Nine instances of one shape, found one at a time across several review passes: a
    list whose entries are identified by something, with nothing stopping that
    identifier from repeating. A duplicate key is never merely redundant -- it makes
    "the thing with id X" ambiguous, silently breaking lookups, suppression by id, and
    any count taken over the list.
    """
    section("Duplicate keys")
    rejects("two answers to the same question in one round",
            lambda: RefinementRound(
                revision_number=1, text_checked=T0,
                quality_report=QualityReport(requirement_id=REQ_D.id, passed=False,
                                             issues=[VAGUE]),
                turn=RefinerTurn(requirement_id=REQ_D.id, revision_number=1,
                                 questions=[ClarifyingQuestion(
                                     id="Q1", issue_id=VAGUE.id,
                                     issue_category=VAGUE.category, question_text="?")]),
                answers=[RefinerAnswer(question_id="Q1", answer_text="X"),
                         RefinerAnswer(question_id="Q1", answer_text="Y")]))
    rejects("two issues sharing an id in one report",
            lambda: QualityReport(requirement_id=REQ_D.id, passed=False, issues=[
                VAGUE, Issue(id=VAGUE.id, category=IssueCategory.NON_ATOMIC,
                             explanation="a different issue")]))
    rejects("duplicate requirement ids in a set",
            lambda: RequirementSet(requirements=[Requirement(id="R1", text="a"),
                                                 Requirement(id="R1", text="b")]))
    rejects("conflict naming the same requirement twice",
            lambda: ConsistencyConflict(requirement_ids=["R1", "R1"], explanation="e"))
    rejects("dependency link pointing at itself",
            lambda: DependencyLink(from_requirement_id="R1", to_requirement_id="R1",
                                   explanation="e"))
    rejects("the same dependency link twice",
            lambda: DependencyReport(dependencies=[
                DependencyLink(from_requirement_id="A", to_requirement_id="B", explanation="e"),
                DependencyLink(from_requirement_id="A", to_requirement_id="B", explanation="e")]))
    rejects("the same technique listed twice",
            lambda: TestStrategy(requirement_id=REQ_D.id, system_type=SystemType.OTHER,
                                 techniques=[TestTechnique.EXPLORATORY,
                                             TestTechnique.EXPLORATORY], rationale="r"))
    rejects("a test case covering the same requirement twice",
            lambda: TestCase(id="T1", requirement_ids=[REQ_D.id, REQ_D.id],
                             technique_used=TestTechnique.EXPLORATORY, title="a",
                             steps=["s"], expected_result="e"))
    rejects("two test cases sharing an id",
            lambda: TestPlan(requirement_id=REQ_D.id, test_cases=[
                TestCase(id="T1", requirement_ids=[REQ_D.id],
                         technique_used=TestTechnique.EXPLORATORY, title="a", steps=["s"],
                         expected_result="e"),
                TestCase(id="T1", requirement_ids=[REQ_D.id],
                         technique_used=TestTechnique.EXPLORATORY, title="b", steps=["s"],
                         expected_result="e")]))
    # The valid neighbours, so the guards can't be satisfied by rejecting everything.
    accepts("distinct requirement ids", lambda: REQ_SET)
    accepts("distinct conflict members",
            lambda: ConsistencyConflict(requirement_ids=["R1", "R2"], explanation="e"))
    accepts("two links between different pairs",
            lambda: DependencyReport(dependencies=[
                DependencyLink(from_requirement_id="A", to_requirement_id="B", explanation="e"),
                DependencyLink(from_requirement_id="B", to_requirement_id="C", explanation="e")]))
    accepts("two distinct techniques",
            lambda: TestStrategy(requirement_id=REQ_D.id, system_type=SystemType.OTHER,
                                 techniques=[TestTechnique.EXPLORATORY,
                                             TestTechnique.BOUNDARY_VALUE_ANALYSIS],
                                 rationale="r"))


def test_denormalised_fields_agree() -> None:
    """Fields that restate something held elsewhere must restate it correctly.

    Found by a review sweep for denormalisation specifically. The requirement_id part is
    swept by discovery rather than a hand-written list, because the same three fields
    were reported one at a time across separate passes.
    """
    section("Denormalised fields")
    other_cls = Classification(requirement_id="REQ-WRONG", system_type=SystemType.OTHER,
                               rationale="x")
    other_strategy = TestStrategy(requirement_id="REQ-WRONG", system_type=SystemType.OTHER,
                                  techniques=[TestTechnique.BOUNDARY_VALUE_ANALYSIS],
                                  rationale="r")
    other_plan = TestPlan(requirement_id="REQ-WRONG", test_cases=[TestCase(
        id="TC-X", requirement_ids=["REQ-WRONG"],
        technique_used=TestTechnique.BOUNDARY_VALUE_ANALYSIS, title="a", steps=["s"],
        expected_result="e")])

    ok("discovery finds exactly the requirement_id-bearing fields",
       fields_carrying_requirement_id(
           rec(classification=CLS, test_strategy=STRATEGY, test_plan=PLAN))
       == ["classification", "test_strategy", "test_plan"])
    rejects("classification for a different requirement",
            lambda: rec(classification=other_cls))
    rejects("test_strategy for a different requirement",
            lambda: rec(classification=CLS, test_strategy=other_strategy))
    rejects("test_plan for a different requirement",
            lambda: rec(classification=CLS, test_plan=other_plan))

    rejects("strategy's system_type disagreeing with the classification",
            lambda: rec(classification=CLS,
                        test_strategy=TestStrategy(
                            requirement_id=REQ_D.id, system_type=SystemType.MOBILE,
                            techniques=[TestTechnique.BOUNDARY_VALUE_ANALYSIS],
                            rationale="r")))
    accepts("strategy's system_type matching", lambda: rec(classification=CLS,
                                                           test_strategy=STRATEGY))

    rejects("test case using a technique the strategy never selected",
            lambda: rec(classification=CLS, test_strategy=STRATEGY,
                        test_plan=TestPlan(requirement_id=REQ_D.id, test_cases=[TestCase(
                            id="TC-1", requirement_ids=[REQ_D.id],
                            technique_used=TestTechnique.ADVERSARIAL, title="a",
                            steps=["s"], expected_result="e")])))
    accepts("test case using a selected technique",
            lambda: rec(classification=CLS, test_strategy=STRATEGY, test_plan=PLAN))

    rejects("a plan whose case does not cover the plan's requirement",
            lambda: TestPlan(requirement_id=REQ_D.id, test_cases=[TestCase(
                id="TC-1", requirement_ids=["REQ-OTHER"],
                technique_used=TestTechnique.EXPLORATORY, title="a", steps=["s"],
                expected_result="e")]))
    accepts("a case covering the plan's requirement plus another",
            lambda: TestPlan(requirement_id=REQ_D.id, test_cases=[TestCase(
                id="TC-1", requirement_ids=[REQ_D.id, "THEMAS-REQ-B"],
                technique_used=TestTechnique.EXPLORATORY, title="a", steps=["s"],
                expected_result="e")]))

    rejects("Issue.related_requirement_ids with a duplicate",
            lambda: Issue(id="I1", category=IssueCategory.INCONSISTENT, explanation="e",
                          related_requirement_ids=["REQ-B", "REQ-B"]))


def test_technique_eligibility() -> None:
    """Layer 1 of technique selection: the system type constrains the pool.

    Documented in DESIGN_NOTES.md from the start but enforced nowhere, so the failure it
    exists to prevent -- adversarial testing selected for a thermostat -- was accepted.
    """
    section("Technique eligibility")
    strat = lambda st, techs: TestStrategy(requirement_id=REQ_D.id, system_type=st,
                                           techniques=techs, rationale="r")

    ok("every SystemType has a pool", set(ELIGIBLE_TECHNIQUES) == set(SystemType))
    ok("no pool is empty", all(ELIGIBLE_TECHNIQUES.values()))

    rejects("ADVERSARIAL for a non-AI requirement (the thermostat case)",
            lambda: strat(SystemType.OTHER, [TestTechnique.ADVERSARIAL]))
    rejects("METAMORPHIC for a web requirement",
            lambda: strat(SystemType.WEB, [TestTechnique.METAMORPHIC]))
    rejects("STATISTICAL_THRESHOLD for a mobile requirement",
            lambda: strat(SystemType.MOBILE, [TestTechnique.STATISTICAL_THRESHOLD]))
    rejects("EQUIVALENCE_PARTITIONING for an AI requirement",
            lambda: strat(SystemType.AI_SYSTEM, [TestTechnique.EQUIVALENCE_PARTITIONING]))
    rejects("STATE_BASED for an AI requirement",
            lambda: strat(SystemType.AI_SYSTEM, [TestTechnique.STATE_BASED]))
    rejects("one eligible technique alongside one ineligible",
            lambda: strat(SystemType.WEB, [TestTechnique.BOUNDARY_VALUE_ANALYSIS,
                                           TestTechnique.ADVERSARIAL]))

    accepts("BOUNDARY_VALUE_ANALYSIS for an embedded requirement",
            lambda: strat(SystemType.OTHER, [TestTechnique.BOUNDARY_VALUE_ANALYSIS]))
    accepts("METAMORPHIC for an AI requirement",
            lambda: strat(SystemType.AI_SYSTEM, [TestTechnique.METAMORPHIC]))
    for st in SystemType:
        accepts(f"EXPLORATORY allowed for {st.value}",
                lambda s=st: strat(s, [TestTechnique.EXPLORATORY]))
        accepts(f"PERFORMANCE allowed for {st.value}",
                lambda s=st: strat(s, [TestTechnique.PERFORMANCE]))

    # The chain: the pool follows the Classifier's decision, not the Selector's.
    rejects("AI techniques smuggled in by disagreeing with the classification",
            lambda: rec(classification=CLS,  # CLS says OTHER
                        test_strategy=strat(SystemType.AI_SYSTEM,
                                            [TestTechnique.ADVERSARIAL])))


def test_references_resolve() -> None:
    """Copies must restate correctly, and references must point at something real.

    The invented-id checks target an LLM failure mode rather than a coding slip: a model
    asked to find conflicts can return a plausible id for a requirement that does not
    exist, in a report that is otherwise well-formed.
    """
    section("References and denormalised copies")
    failed_qr = QualityReport(requirement_id=REQ_D.id, passed=False, issues=[VAGUE])

    rejects("question restating its issue's category wrongly",
            lambda: RefinementRound(
                revision_number=1, text_checked=T0, quality_report=failed_qr,
                turn=RefinerTurn(requirement_id=REQ_D.id, revision_number=1,
                                 questions=[ClarifyingQuestion(
                                     id="Q1", issue_id=VAGUE.id,
                                     issue_category=IssueCategory.AMBIGUOUS_TERM,
                                     question_text="?")])))
    accepts("question restating it correctly",
            lambda: mk_round(1, T0, [VAGUE], [("Q1", VAGUE)]))

    rejects("an issue listing its own requirement as related",
            lambda: mk_round(1, T0, [Issue(id="I9", category=IssueCategory.INCONSISTENT,
                                           explanation="e",
                                           related_requirement_ids=[REQ_D.id])]))
    accepts("an issue listing a different requirement",
            lambda: mk_round(1, T0, [Issue(id="I9", category=IssueCategory.INCONSISTENT,
                                           explanation="e",
                                           related_requirement_ids=["THEMAS-REQ-G"])]))

    ghost = "REQ-DOES-NOT-EXIST"
    rejects("consistency conflict naming an invented requirement",
            lambda: doc(outcome=DocumentOutcome.COMPLETED, dependency_report=DEP,
                        consistency_report=ConsistencyReport(
                            doc_id=REQ_SET.doc_id,
                            conflicts=[ConsistencyConflict(
                                requirement_ids=[REQ_D.id, ghost], explanation="e")])))
    rejects("dependency link to an invented requirement",
            lambda: doc(outcome=DocumentOutcome.COMPLETED, consistency_report=CONS_OK,
                        dependency_report=DependencyReport(
                            doc_id=REQ_SET.doc_id,
                            dependencies=[DependencyLink(from_requirement_id=REQ_D.id,
                                                         to_requirement_id=ghost,
                                                         explanation="e")])))
    rejects("issue relating to an invented requirement",
            lambda: doc(requirement_records=[rec(classification=CLS, rounds=[
                mk_round(1, T0, [Issue(id="I9", category=IssueCategory.INCONSISTENT,
                                       explanation="e", related_requirement_ids=[ghost])])])]))
    rejects("test case covering an invented requirement",
            lambda: doc(requirement_records=[rec(
                classification=CLS, test_strategy=STRATEGY,
                test_plan=TestPlan(requirement_id=REQ_D.id, test_cases=[TestCase(
                    id="TC-1", requirement_ids=[REQ_D.id, ghost],
                    technique_used=TestTechnique.BOUNDARY_VALUE_ANALYSIS, title="a",
                    steps=["s"], expected_result="e")]))]))
    accepts("references that all resolve",
            lambda: doc(outcome=DocumentOutcome.COMPLETED, consistency_report=CONS_OK,
                        dependency_report=DEP_OK,
                        requirement_records=[rec(classification=CLS,
                                                 test_strategy=STRATEGY, test_plan=PLAN)]))

    rejects("requirement naming a different source document",
            lambda: RequirementSet(doc_id="doc-A", requirements=[
                Requirement(id="R1", text="x", source_doc_id="doc-B")]))
    accepts("requirement with no source_doc_id inside an attributed set",
            lambda: RequirementSet(doc_id="doc-A", requirements=[
                Requirement(id="R1", text="x")]))
    accepts("unattributed set holds requirements from anywhere",
            lambda: RequirementSet(requirements=[
                Requirement(id="R1", text="x", source_doc_id="doc-B")]))
    accepts("matching source_doc_id", lambda: REQ_SET)

    rejects("consistency report from a different document",
            lambda: doc(outcome=DocumentOutcome.COMPLETED, dependency_report=DEP,
                        consistency_report=ConsistencyReport(doc_id="ANOTHER-DOCUMENT")))
    rejects("dependency report from a different document",
            lambda: doc(outcome=DocumentOutcome.COMPLETED, consistency_report=CONS_OK,
                        dependency_report=DependencyReport(doc_id="ANOTHER-DOCUMENT")))


def test_gap6_issue_identity() -> None:
    """Gap 6: an issue id must mean the same defect in every round it appears in.

    Each round's report is a fresh LLM call minting its own ids. The concrete failure:
    round 1 raises ISSUE-1 (vague pronoun) and ISSUE-2 (non-verifiable), the human
    confirms ISSUE-1 resolved, round 2 finds only the non-verifiable one left and
    numbers it ISSUE-1 -- so suppressing "ISSUE-1" drops a real, unresolved defect.
    """
    section("Gap 6 -- issue identity across rounds")
    confirmed = RefinerAnswer(question_id="Q1", answer_text="THEMAS-REQ-B's range.",
                              user_confirms_resolved=True)
    plain = RefinerAnswer(question_id="Q2", answer_text="the mode process")
    collision = Issue(id=VAGUE.id, category=IssueCategory.NON_VERIFIABLE,
                      span="subsequent processing", explanation="e")

    def r1(answers, rewrite=T1):
        return mk_round(1, T0, [VAGUE, UNVERIFIABLE],
                        [("Q1", VAGUE), ("Q2", UNVERIFIABLE)], answers, rewrite_to=rewrite)

    rejects("one id, two different defects across rounds",
            lambda: rec(rounds=[r1([confirmed, plain]),
                                mk_round(2, T1, [collision])]))
    accepts("the same id carrying the same defect across rounds",
            lambda: rec(rounds=[r1([confirmed, plain]),
                                mk_round(2, T1, [UNVERIFIABLE],
                                         suppressed=[VAGUE.id])]))

    # The answer to Q1 exists but is NOT confirmed -- so this fails only because of the
    # confirmation flag, not because the issue was never answered. (A weaker fixture
    # that omitted the answer passed for the wrong reason; mutation testing caught it.)
    unconfirmed = RefinerAnswer(question_id="Q1", answer_text="THEMAS-REQ-B's range.")
    rejects("suppressing an issue answered but not confirmed",
            lambda: rec(rounds=[r1([unconfirmed, plain]),
                                mk_round(2, T1, [UNVERIFIABLE], suppressed=[VAGUE.id])]))
    rejects("suppressing an id no earlier round raised",
            lambda: rec(rounds=[r1([confirmed, plain]),
                                mk_round(2, T1, [UNVERIFIABLE],
                                         suppressed=[VAGUE.id, "GHOST-ISSUE"])]))
    rejects("revision 1 suppressing anything",
            lambda: mk_round(1, T0, [VAGUE], suppressed=["anything"]))
    rejects("suppressing an issue while still raising it",
            lambda: mk_round(2, T1, [UNVERIFIABLE], suppressed=[UNVERIFIABLE.id]))
    rejects("duplicate ids in suppressed_issue_ids",
            lambda: mk_round(2, T1, [UNVERIFIABLE], suppressed=[VAGUE.id, VAGUE.id]))
    rejects("dropping a suppression in a later round",
            lambda: rec(rounds=[
                r1([confirmed, plain]),
                mk_round(2, T1, [UNVERIFIABLE], [("Q3", UNVERIFIABLE)],
                         [RefinerAnswer(question_id="Q3", answer_text="the mode process")],
                         rewrite_to=T2, suppressed=[VAGUE.id]),
                mk_round(3, T2)]))
    accepts("a suppression carried forward through every later round",
            lambda: rec(rounds=[
                r1([confirmed, plain]),
                mk_round(2, T1, [UNVERIFIABLE], [("Q3", UNVERIFIABLE)],
                         [RefinerAnswer(question_id="Q3", answer_text="the mode process")],
                         rewrite_to=T2, suppressed=[VAGUE.id]),
                mk_round(3, T2, suppressed=[VAGUE.id])]))

    traced = rec(rounds=[r1([confirmed, plain]),
                         mk_round(2, T1, [UNVERIFIABLE], suppressed=[VAGUE.id])])
    ok("issue_history traces each defect's life",
       traced.issue_history == {VAGUE.id: [1], UNVERIFIABLE.id: [1, 2]})
    ok("suppressed ids are visible per round",
       [r.suppressed_issue_ids for r in traced.rounds] == [[], [VAGUE.id]])
    ok("clean record has an empty history",
       rec(rounds=ROUNDS_CLEAN).issue_history == {})


def test_self_review_sweep() -> None:
    """Weak points found by an internal sweep on angles the reviews had not used:
    empty identifiers, cross-record id collisions, timestamps, and scale."""
    section("Self-review sweep")

    rejects("empty Requirement.id", lambda: Requirement(id="", text="x"))
    rejects("empty Issue.id",
            lambda: Issue(id="", category=IssueCategory.VAGUE_PRONOUN, explanation="e"))
    rejects("empty ClarifyingQuestion.id",
            lambda: ClarifyingQuestion(id="", issue_id="I1",
                                       issue_category=IssueCategory.VAGUE_PRONOUN,
                                       question_text="q"))
    rejects("empty RefinerAnswer.answer_text",
            lambda: RefinerAnswer(question_id="Q1", answer_text=""))
    rejects("empty TestCase.id / title / expected_result",
            lambda: TestCase(id="", requirement_ids=["R1"],
                             technique_used=TestTechnique.EXPLORATORY, title="",
                             steps=["s"], expected_result=""))
    rejects("a TestCase step that is an empty string",
            lambda: TestCase(id="T", requirement_ids=["R1"],
                             technique_used=TestTechnique.EXPLORATORY, title="t",
                             steps=[""], expected_result="e"))
    rejects("empty entry in TestCase.requirement_ids",
            lambda: TestCase(id="T", requirement_ids=[""],
                             technique_used=TestTechnique.EXPLORATORY, title="t",
                             steps=["s"], expected_result="e"))
    rejects("empty RefinedRequirement texts",
            lambda: RefinedRequirement(requirement_id="R1", original_text="",
                                       refined_text="", revision_number=1,
                                       answers_used=[A1]))
    rejects("empty doc_id on a set",
            lambda: RequirementSet(doc_id="", requirements=[Requirement(id="R1", text="x")]))
    rejects("empty preconditions string",
            lambda: TestCase(id="T", requirement_ids=["R1"],
                             technique_used=TestTechnique.EXPLORATORY, title="t",
                             steps=["s"], expected_result="e", preconditions=""))
    rejects("empty Issue.span",
            lambda: Issue(id="I1", category=IssueCategory.VAGUE_PRONOUN, span="",
                          explanation="e"))
    accepts("a span that is genuinely absent",
            lambda: Issue(id="I1", category=IssueCategory.VAGUE_PRONOUN, explanation="e"))

    def plan_for(req_id, case_id):
        return TestPlan(requirement_id=req_id, test_cases=[TestCase(
            id=case_id, requirement_ids=[req_id],
            technique_used=TestTechnique.BOUNDARY_VALUE_ANALYSIS, title="t",
            steps=["s"], expected_result="e")])

    def record_for(req, case_id):
        return RequirementRunRecord(
            requirement=req, run_id=META.run_id,
            classification=Classification(requirement_id=req.id,
                                          system_type=SystemType.OTHER, rationale="x"),
            test_strategy=TestStrategy(requirement_id=req.id, system_type=SystemType.OTHER,
                                       techniques=[TestTechnique.BOUNDARY_VALUE_ANALYSIS],
                                       rationale="r"),
            test_plan=plan_for(req.id, case_id))

    rejects("the same test case id in two different plans",
            lambda: doc(requirement_records=[record_for(REQ_D, "TC-1"),
                                             record_for(REQ_G, "TC-1")]))
    accepts("distinct test case ids across plans",
            lambda: doc(requirement_records=[record_for(REQ_D, "TC-D-1"),
                                             record_for(REQ_G, "TC-G-1")]))

    rejects("naive (timezone-less) started_at",
            lambda: RunMetadata(run_id="r", started_at=datetime(2026, 8, 5),
                                stages=STAGES, prompt_version="v1"))
    accepts("timezone-aware started_at",
            lambda: RunMetadata(run_id="r", started_at=datetime.now(timezone.utc),
                                stages=STAGES, prompt_version="v1"))

    chain = [DependencyLink(from_requirement_id=f"R{i}", to_requirement_id=f"R{i+1}",
                            explanation="e") for i in range(3000)]
    ok("find_cycles handles a 3000-link chain without recursing",
       DependencyReport(dependencies=chain).find_cycles() == [])
    closed = DependencyReport(dependencies=chain + [DependencyLink(
        from_requirement_id="R3000", to_requirement_id="R0", explanation="e")])
    ok("find_cycles finds a 3001-node cycle",
       len(closed.find_cycles()) == 1 and len(closed.find_cycles()[0]) == 3001)


def test_retry_without_redoing_everything() -> None:
    """Recovering from a failure must not cost the work already done.

    Two separate holes, both found by asking what happens when an error occurs
    mid-experiment: a document-level stage could not be retried without either erasing
    the failure record or starting a new run (which orphans every completed requirement
    record), and a requirement that errored was never listed as pending, so a resume
    pass silently skipped it.
    """
    section("Retry without redoing everything")

    finished = rec(outcome=RunOutcome.COMPLETED, **VALID_RECORDS[RunOutcome.COMPLETED])
    degraded = doc(outcome=DocumentOutcome.DEGRADED, errors=[CE], dependency_report=DEP,
                   requirement_records=[finished])
    ok("degraded document is missing the failed stage's report",
       degraded.consistency_report is None)

    retried = doc(outcome=DocumentOutcome.COMPLETED, errors=[CE], consistency_report=CONS,
                  dependency_report=DEP, requirement_records=[finished])
    ok("the retry produces a completed document", retried.outcome is DocumentOutcome.COMPLETED)
    ok("the original failure is still on record",
       retried.errors[0].stage is DocumentStage.CONSISTENCY_CHECKER
       and retried.errors[0].retry_count == CE.retry_count)
    ok("the already-completed requirement work is kept",
       [r.requirement.id for r in retried.requirement_records] == [REQ_D.id])

    errored = rec(outcome=RunOutcome.ERROR,
                  errors=[StageError(stage=PipelineStage.TEST_GENERATOR,
                                     kind=FailureKind.TRANSPORT, message="429")])
    ok("an errored requirement is offered for retry",
       REQ_D.id in doc(requirement_records=[errored]).pending_requirement_ids)
    ok("retrying it in place is accepted",
       doc(requirement_records=[finished]).pending_requirement_ids == [REQ_G.id, REQ_B.id])

    # Symmetry with the document record: a requirement-level failure survives a
    # successful retry too, so "how many requirements needed a retry" stays countable.
    gen_failed = StageError(stage=PipelineStage.TEST_GENERATOR, kind=FailureKind.TRANSPORT,
                            message="429", retry_count=3)
    accepts("COMPLETED keeping an earlier stage failure",
            lambda: rec(outcome=RunOutcome.COMPLETED,
                        errors=[gen_failed], **VALID_RECORDS[RunOutcome.COMPLETED]))
    accepts("IN_PROGRESS carrying a failure it will retry",
            lambda: rec(errors=[gen_failed], classification=CLS))
    rejects("ERROR with no error recorded", lambda: rec(outcome=RunOutcome.ERROR))
    # The Quality Checker and Refiner run once per round, so unlike a document-level
    # stage the same stage can legitimately fail more than once for one requirement.
    qc = lambda msg: StageError(stage=PipelineStage.QUALITY_CHECKER, kind=FailureKind.TRANSPORT,
                                message=msg)
    accepts("the same stage failing in two different rounds",
            lambda: rec(outcome=RunOutcome.ERROR,
                        errors=[qc("429 in round 1"), qc("429 in round 3")]))
    rejects("CAP_STOPPED recording a failure in a stage that never ran",
            lambda: rec(outcome=RunOutcome.CAP_STOPPED, classification=CLS,
                        rounds=ROUNDS_CAPPED, cap_reason="x",
                        errors=[StageError(stage=PipelineStage.TEST_GENERATOR,
                                           kind=FailureKind.TRANSPORT,
                                           message="429")]))
    accepts("CAP_STOPPED recording a failure in a stage that did run",
            lambda: rec(outcome=RunOutcome.CAP_STOPPED, classification=CLS,
                        rounds=ROUNDS_CAPPED, cap_reason="x",
                        errors=[StageError(stage=PipelineStage.QUALITY_CHECKER,
                                           kind=FailureKind.TRANSPORT,
                                           message="429")]))
    rejects("ERROR when every stage produced its output",
            lambda: rec(outcome=RunOutcome.ERROR, errors=[gen_failed],
                        **VALID_RECORDS[RunOutcome.COMPLETED]))
    ok("an ERROR record keeps the stages that did succeed",
       [f for f in ("classification", "rounds", "test_strategy", "test_plan")
        if getattr(rec(outcome=RunOutcome.ERROR, errors=[gen_failed], classification=CLS,
                       rounds=ROUNDS_REFINED, test_strategy=STRATEGY), f)]
       == ["classification", "rounds", "test_strategy"])


def resume_at(rec):
    """The resume rule from ORCHESTRATOR_CONTRACT.md, kept here so it stays honest.

    The schema deliberately does not contain this -- it encodes pipeline *ordering*,
    which belongs to the orchestrator. But a spec nobody executes drifts: the first
    version of this rule was wrong for one case (see the `last.rewrite` branch), and
    nothing caught it until the records were constructed by hand. Testing the documented
    rule against real records is the cheapest way to keep the document true.
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


def test_resume_positions() -> None:
    """A failure at any stage must resume at that stage -- nothing earlier redone."""
    section("Resume positions")
    err = lambda stage: [StageError(stage=stage, kind=FailureKind.TRANSPORT,
                                    message="429", retry_count=3)]
    mid_round = mk_round(1, T0, [VAGUE], [("Q1", VAGUE)], [A1])            # no rewrite yet
    rewritten = mk_round(1, T0, [VAGUE], [("Q1", VAGUE)], [A1], rewrite_to=T1)

    cases = [
        ("classifier failed",
         dict(errors=err(PipelineStage.CLASSIFIER)), PipelineStage.CLASSIFIER),
        ("quality checker failed on round 1",
         dict(errors=err(PipelineStage.QUALITY_CHECKER), classification=CLS),
         PipelineStage.QUALITY_CHECKER),
        ("refiner failed mid-round, nothing rewritten yet",
         dict(errors=err(PipelineStage.REFINER), classification=CLS, rounds=[mid_round]),
         PipelineStage.REFINER),
        ("quality checker failed on round 2, round 1 already rewrote",
         dict(errors=err(PipelineStage.QUALITY_CHECKER), classification=CLS,
              rounds=[rewritten]), PipelineStage.QUALITY_CHECKER),
        ("strategy selector failed",
         dict(errors=err(PipelineStage.STRATEGY_SELECTOR), classification=CLS,
              rounds=ROUNDS_REFINED), PipelineStage.STRATEGY_SELECTOR),
        ("test generator failed",
         dict(errors=err(PipelineStage.TEST_GENERATOR), classification=CLS,
              rounds=ROUNDS_REFINED, test_strategy=STRATEGY), PipelineStage.TEST_GENERATOR),
    ]
    for label, kw, expected in cases:
        got = resume_at(rec(outcome=RunOutcome.ERROR, **kw))
        ok(f"{label} -> resume at {expected.value}", got is expected)

    ok("an interrupted record resumes at the classifier",
       resume_at(rec()) is PipelineStage.CLASSIFIER)
    ok("a finished record resumes nowhere",
       resume_at(rec(outcome=RunOutcome.COMPLETED,
                     **VALID_RECORDS[RunOutcome.COMPLETED])) is None)


def test_rule_table_anchors() -> None:
    """Pin the rules that came from real bugs, so deleting one fails loudly.

    The enumerated sections read the rule tables, so they verify that whatever rules
    exist are *enforced* -- but they cannot notice a rule being *deleted*, since a
    deleted rule simply stops being enumerated (the check count silently drops by one).
    A mutation run confirmed this blind spot. Each assertion below corresponds to a
    contradiction that was once accepted.
    """
    section("Rule table anchors -- rules that must not disappear")
    req, docr = _OUTCOME_RULES, _DOCUMENT_OUTCOME_RULES
    cap_gen, cap_stop = req[RunOutcome.CAP_GENERATED], req[RunOutcome.CAP_STOPPED]
    done = req[RunOutcome.COMPLETED]

    ok("COMPLETED requires a passing last report", done.last_report_passed is True)
    ok("COMPLETED requires a test_plan", "test_plan" in done.required)
    ok("COMPLETED forbids cap_reason", "cap_reason" in done.forbidden)
    ok("COMPLETED requires rounds", "rounds" in done.non_empty)
    for label, rule in (("CAP_GENERATED", cap_gen), ("CAP_STOPPED", cap_stop)):
        ok(f"{label} requires classification", "classification" in rule.required)
        ok(f"{label} requires cap_reason", "cap_reason" in rule.required)
        ok(f"{label} requires rounds", "rounds" in rule.non_empty)
        ok(f"{label} requires a failing last report", rule.last_report_passed is False)
    ok("CAP_GENERATED requires a test_plan", "test_plan" in cap_gen.required)
    ok("CAP_STOPPED forbids a test_plan", "test_plan" in cap_stop.forbidden)
    ok("CAP_STOPPED forbids a test_strategy", "test_strategy" in cap_stop.forbidden)
    ok("ERROR requires at least one error", "errors" in req[RunOutcome.ERROR].non_empty)
    ok("IN_PROGRESS forbids cap_reason", "cap_reason" in req[RunOutcome.IN_PROGRESS].forbidden)

    ok("document COMPLETED requires both reports",
       {"consistency_report", "dependency_report"} <= set(docr[DocumentOutcome.COMPLETED].required))
    ok("document DEGRADED requires errors", "errors" in docr[DocumentOutcome.DEGRADED].non_empty)
    ok("document IN_PROGRESS stays unconstrained (mid-run writes)",
       not (docr[DocumentOutcome.IN_PROGRESS].required
            or docr[DocumentOutcome.IN_PROGRESS].forbidden))

    ok("every RunOutcome has a rule row", set(req) == set(RunOutcome))
    ok("every DocumentOutcome has a rule row", set(docr) == set(DocumentOutcome))


def test_helpers_and_round_trip() -> None:
    section("Helpers and serialisation")
    ok("conflicts_for finds the requirement",
       [c.explanation for c in CONS.conflicts_for("THEMAS-REQ-D")] ==
       ["Both constrain the range."])
    ok("dependencies_for matches either side",
       len(DEP.dependencies_for("THEMAS-REQ-B")) == 1)

    cyclic = DependencyReport(dependencies=[DependencyLink(
        from_requirement_id=a, to_requirement_id=b, explanation="e")
        for a, b in [("A", "B"), ("B", "C"), ("C", "A"), ("D", "E"), ("E", "D")]])
    ok("find_cycles finds both cycles", len(cyclic.find_cycles()) == 2)
    ok("find_cycles is deterministic",
       len({str(cyclic.find_cycles()) for _ in range(5)}) == 1)
    ok("find_cycles survives a round trip",
       DependencyReport.model_validate_json(cyclic.model_dump_json()).find_cycles()
       == cyclic.find_cycles())

    full = doc(outcome=DocumentOutcome.COMPLETED, consistency_report=CONS, dependency_report=DEP,
               requirement_records=[rec(outcome=RunOutcome.CAP_GENERATED,
                                        **VALID_RECORDS[RunOutcome.CAP_GENERATED])])
    reloaded = DocumentRunRecord.model_validate_json(full.model_dump_json())
    ok("document round trip preserves outcome", reloaded.outcome is DocumentOutcome.COMPLETED)
    ok("document round trip preserves metadata", reloaded.metadata.run_id == META.run_id)
    ok("document round trip preserves the trajectory",
       reloaded.requirement_records[0].issues_per_round == [2, 1, 1])
    ok("document round trip preserves final_text",
       reloaded.requirement_records[0].final_text == T2)

    dumped = full.model_dump()
    ok("computed pending_requirement_ids in dump", "pending_requirement_ids" in dumped)
    for key in ("final_text", "final_requirement", "used_human_override", "issues_per_round"):
        ok(f"computed field {key} in dump", key in dumped["requirement_records"][0])


def main() -> int:
    print("=" * 72)
    print("schemas.py regression")
    print("=" * 72)
    for fn in (test_non_empty_guards, test_gap1_both_paths_converge,
               test_gap2_requirement_outcomes, test_failure_kind, test_gap5_refinement_trajectory,
               test_gap3_document_record, test_gap4_provenance,
               test_cross_field_agreement, test_duplicate_keys,
               test_denormalised_fields_agree, test_technique_eligibility,
               test_references_resolve, test_gap6_issue_identity,
               test_self_review_sweep, test_retry_without_redoing_everything,
               test_resume_positions, test_rule_table_anchors,
               test_helpers_and_round_trip):
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
