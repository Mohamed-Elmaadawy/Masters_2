"""
Regression tests for orchestrator/human_cli.py. Run after any change there:

    python -m orchestrator.test_human_cli

Plain script, no pytest, same convention as design/test_schemas.py. Drives
answer_questions_cli/decide_at_cap_cli entirely through injected input_fn/output_fn --
never touches real stdin/stdout.
"""

from __future__ import annotations

from design.schemas import (
    ClarifyingQuestion, Issue, IssueCategory, QualityReport, RefinementRound,
    RefinerTurn, Requirement, RequirementRunRecord, RunOutcome,
)
from orchestrator.human_cli import answer_questions_cli, decide_at_cap_cli

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


class ScriptedInput:
    """A FIFO queue of scripted input() responses -- same shape as
    orchestrator/test_harness.py's Scripted: each item is either a string (returned) or
    an Exception instance (raised, e.g. EOFError to simulate an interrupted session)."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.prompts: list[str] = []

    def __call__(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        if not self._responses:
            raise EOFError("scripted input exhausted -- test forgot to script enough answers")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            # BaseException, not Exception: KeyboardInterrupt/SystemExit inherit from
            # BaseException directly, not Exception, and this fixture needs to be able
            # to script either.
            raise item
        return item


def noop_output(_: str) -> None:
    pass


TURN = RefinerTurn(requirement_id="R1", revision_number=1, questions=[
    ClarifyingQuestion(id="Q1", issue_id="R1-ISSUE-1", issue_category=IssueCategory.VAGUE_PRONOUN,
                       question_text="What does 'it' refer to in this requirement?"),
    ClarifyingQuestion(id="Q2", issue_id="R1-ISSUE-2", issue_category=IssueCategory.VAGUE_PRONOUN,
                       question_text="What does 'this' refer to?"),
])


def capped_record() -> RequirementRunRecord:
    return RequirementRunRecord(
        requirement=Requirement(id="R1", text="It shall do this.", source_doc_id="doc-1"),
        run_id="run-1",
        rounds=[RefinementRound(
            revision_number=1, text_checked="It shall do this.",
            quality_report=QualityReport(requirement_id="R1", passed=False, issues=[
                Issue(id="R1-ISSUE-1", category=IssueCategory.VAGUE_PRONOUN,
                     explanation="'it' has no clear referent"),
            ]))],
    )


def test_answer_questions_normal_path() -> None:
    section("answer_questions_cli: normal path returns one answer per question")
    input_fn = ScriptedInput(["it refers to the sensor", "y", "this refers to the alarm", "n"])
    answers = answer_questions_cli(TURN, input_fn=input_fn, output_fn=noop_output)
    ok("returns exactly one answer per question", len(answers) == len(TURN.questions))
    ok("question_id linkage is correct, in order",
       [a.question_id for a in answers] == ["Q1", "Q2"])
    ok("answer text is what was typed",
       answers[0].answer_text == "it refers to the sensor"
       and answers[1].answer_text == "this refers to the alarm")
    ok("y/n parsed correctly (y -> True, n -> False)",
       answers[0].user_confirms_resolved is True
       and answers[1].user_confirms_resolved is False)


def test_answer_questions_yes_no_variants() -> None:
    section("answer_questions_cli: yes/no/Yes/No all accepted, case-insensitively")
    input_fn = ScriptedInput(["a1", "yes", "a2", "No"])
    answers = answer_questions_cli(TURN, input_fn=input_fn, output_fn=noop_output)
    ok("'yes' parses as True", answers[0].user_confirms_resolved is True)
    ok("'No' parses as False (case-insensitive)", answers[1].user_confirms_resolved is False)


def test_answer_questions_garbage_reprompts() -> None:
    section("answer_questions_cli: garbage confirmation input re-prompts, does not default")
    single_question_turn = RefinerTurn(requirement_id="R1", revision_number=1, questions=[
        ClarifyingQuestion(id="Q1", issue_id="R1-ISSUE-1", issue_category=IssueCategory.VAGUE_PRONOUN,
                           question_text="What does 'it' refer to?"),
    ])
    input_fn = ScriptedInput(["an answer", "maybe", "sure", "y"])
    answers = answer_questions_cli(single_question_turn, input_fn=input_fn, output_fn=noop_output)
    ok("eventually succeeds after two garbage attempts", len(answers) == 1)
    ok("the confirmation that finally stuck is the correct one",
       answers[0].user_confirms_resolved is True)
    ok("all garbage prompts were actually re-asked (4 input() calls, not 2)",
       len(input_fn.prompts) == 4)


def test_answer_questions_blank_answer_reprompts() -> None:
    section("answer_questions_cli: a blank answer re-prompts rather than being accepted")
    single_question_turn = RefinerTurn(requirement_id="R1", revision_number=1, questions=[
        ClarifyingQuestion(id="Q1", issue_id="R1-ISSUE-1", issue_category=IssueCategory.VAGUE_PRONOUN,
                           question_text="What does 'it' refer to?"),
    ])
    input_fn = ScriptedInput(["", "   ", "a real answer", "n"])
    answers = answer_questions_cli(single_question_turn, input_fn=input_fn, output_fn=noop_output)
    ok("blank/whitespace-only answers are rejected, real one accepted",
       answers[0].answer_text == "a real answer")


def test_answer_questions_interruption_propagates() -> None:
    section("answer_questions_cli: EOFError from input_fn propagates, is not swallowed")
    input_fn = ScriptedInput(["an answer"])  # confirmation prompt has nothing scripted -> EOFError
    try:
        answer_questions_cli(TURN, input_fn=input_fn, output_fn=noop_output)
        ok("EOFError propagates uncaught", False)
    except EOFError:
        ok("EOFError propagates uncaught", True)


def test_decide_at_cap_normal_path() -> None:
    section("decide_at_cap_cli: normal generate/stop paths")
    input_fn = ScriptedInput(["generate", "best effort is good enough"])
    outcome, reason = decide_at_cap_cli(capped_record(), input_fn=input_fn, output_fn=noop_output)
    ok("outcome is CAP_GENERATED", outcome is RunOutcome.CAP_GENERATED)
    ok("reason is what was typed", reason == "best effort is good enough")

    input_fn2 = ScriptedInput(["stop", "too many unresolved issues"])
    outcome2, reason2 = decide_at_cap_cli(capped_record(), input_fn=input_fn2, output_fn=noop_output)
    ok("outcome is CAP_STOPPED", outcome2 is RunOutcome.CAP_STOPPED)
    ok("reason is what was typed", reason2 == "too many unresolved issues")


def test_decide_at_cap_invalid_choice_reprompts() -> None:
    section("decide_at_cap_cli: an invalid choice re-prompts rather than defaulting")
    input_fn = ScriptedInput(["quit", "whatever", "generate", "ok"])
    outcome, reason = decide_at_cap_cli(capped_record(), input_fn=input_fn, output_fn=noop_output)
    ok("eventually resolves to a valid outcome despite two bad attempts",
       outcome is RunOutcome.CAP_GENERATED)
    ok("both bad attempts were actually re-asked (4 input() calls, not 2)",
       len(input_fn.prompts) == 4)


def test_decide_at_cap_empty_reason_reprompts() -> None:
    section("decide_at_cap_cli: an empty reason re-prompts")
    input_fn = ScriptedInput(["stop", "", "  ", "a real reason"])
    outcome, reason = decide_at_cap_cli(capped_record(), input_fn=input_fn, output_fn=noop_output)
    ok("the real reason is what's returned, not the blanks", reason == "a real reason")


def test_decide_at_cap_interruption_propagates() -> None:
    section("decide_at_cap_cli: KeyboardInterrupt from input_fn propagates")
    input_fn = ScriptedInput([KeyboardInterrupt()])
    try:
        decide_at_cap_cli(capped_record(), input_fn=input_fn, output_fn=noop_output)
        ok("KeyboardInterrupt propagates uncaught", False)
    except KeyboardInterrupt:
        ok("KeyboardInterrupt propagates uncaught", True)


def main() -> int:
    print("=" * 72)
    print("orchestrator/human_cli.py regression")
    print("=" * 72)
    for fn in (
        test_answer_questions_normal_path, test_answer_questions_yes_no_variants,
        test_answer_questions_garbage_reprompts, test_answer_questions_blank_answer_reprompts,
        test_answer_questions_interruption_propagates,
        test_decide_at_cap_normal_path, test_decide_at_cap_invalid_choice_reprompts,
        test_decide_at_cap_empty_reason_reprompts, test_decide_at_cap_interruption_propagates,
    ):
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
