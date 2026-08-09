"""Terminal implementation of orchestrator/stage_fns.py's HumanFns.

Kept deliberately outside orchestrator/pipeline.py -- HumanFns exists specifically so
the human-interaction layer can be swapped (a CLI loop today, a FastAPI backend later)
without touching pipeline.py's control flow (see design/DESIGN_NOTES.md's reasoning for
why RefinerTurn/RefinerAnswer are two schemas instead of one blocking call).

input_fn/output_fn are injected, same pattern orchestrator/pipeline.py's Throttle
already uses for sleep_fn/now_fn: production code omits them (defaults to real
input()/print()), tests inject a scripted stand-in. Neither function catches
EOFError/KeyboardInterrupt -- an interruption propagates to the caller as a real
interruption, rather than being coerced into a silently-recorded "the human answered
nothing".

These two functions are NOT assembled into a HumanFns(...) here -- that's the future
run entrypoint's job, once it also knows how to build StageFns from
orchestrator/config.py + orchestrator/providers/.
"""

from __future__ import annotations

from typing import Callable

from design.schemas import RefinerAnswer, RefinerTurn, RequirementRunRecord, RunOutcome

_YES = {"y", "yes"}
_NO = {"n", "no"}


def _read_nonempty(input_fn: Callable[[str], str], output_fn: Callable[[str], None], prompt: str) -> str:
    while True:
        value = input_fn(prompt).strip()
        if value:
            return value
        output_fn("An empty answer isn't accepted -- please enter something.")


def _read_yes_no(input_fn: Callable[[str], str], output_fn: Callable[[str], None], prompt: str) -> bool:
    while True:
        raw = input_fn(prompt).strip().lower()
        if raw in _YES:
            return True
        if raw in _NO:
            return False
        output_fn("Please answer 'y' or 'n'.")


def answer_questions_cli(
    turn: RefinerTurn,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> list[RefinerAnswer]:
    """Prints each ClarifyingQuestion in turn, reads one non-empty answer plus a y/n
    'confident this is fully resolved' per question. Under normal operation this
    always returns exactly one RefinerAnswer per question in `turn.questions` (which
    itself has min_length=1) -- never an empty list. RefinementRound's schema-valid
    'answers=[] with turn set' (contract item 6) describes a *persisted, resumed*
    state, not something this function is free to return on its own."""
    output_fn(f"Requirement {turn.requirement_id}, revision {turn.revision_number}: "
              f"{len(turn.questions)} clarifying question(s).")
    answers: list[RefinerAnswer] = []
    for i, question in enumerate(turn.questions, start=1):
        output_fn(f"\n[{i}/{len(turn.questions)}] ({question.issue_category.value}) "
                  f"{question.question_text}")
        answer_text = _read_nonempty(input_fn, output_fn, "Your answer: ")
        confirmed = _read_yes_no(
            input_fn, output_fn,
            "Are you confident this is now fully resolved, even if the checker still "
            "has doubts? [y/n]: ")
        answers.append(RefinerAnswer(question_id=question.id, answer_text=answer_text,
                                     user_confirms_resolved=confirmed))
    return answers


def decide_at_cap_cli(
    record: RequirementRunRecord,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> tuple[RunOutcome, str]:
    """Prints the capped requirement's remaining issues, asks generate-anyway vs stop,
    reads a non-empty free-text reason. Loops on invalid input rather than defaulting
    to either choice -- contract item 3: this decision belongs to the human, and a
    silent default here would quietly make it for them."""
    last_round = record.rounds[-1]
    output_fn(f"\nRevision cap reached for requirement {record.requirement.id}.")
    if last_round.quality_report.issues:
        output_fn("Remaining issue(s):")
        for issue in last_round.quality_report.issues:
            output_fn(f"  [{issue.category.value}] {issue.explanation}")
    else:
        output_fn("No remaining issues on record for the last round.")

    while True:
        choice = input_fn("Generate tests from the best-effort text anyway, or stop? "
                          "[generate/stop]: ").strip().lower()
        if choice in ("generate", "g"):
            outcome = RunOutcome.CAP_GENERATED
            break
        if choice in ("stop", "s"):
            outcome = RunOutcome.CAP_STOPPED
            break
        output_fn("Please answer 'generate' or 'stop'.")

    reason = _read_nonempty(input_fn, output_fn, "Reason for this decision: ")
    return outcome, reason
