"""Standalone driver for the 2026-08-10 first real run against real LLM APIs.

Does NOT modify anything under orchestrator/, design/, or stage/prompt code -- it
imports and calls orchestrator/cli.py's own `_run`, the exact function `python -m
orchestrator.cli` itself calls, with one substitution: a fixed AI answer policy in
place of the terminal-reading `answer_questions_cli`/`decide_at_cap_cli`. That
substitution exists only because a live terminal isn't available to this automated
run -- `_run`'s `human_fns_factory` parameter is the test seam orchestrator/cli.py
already exposes for exactly this kind of substitution
(orchestrator/test_cli.py uses the same parameter, the same way, for its own tests).

See ANALYSIS.md, "Human-in-the-loop answers", for why this exists and what the policy
is: a genuine, reasoned answer per IssueCategory, written once and applied
consistently, documented as AI-generated (not live-human) -- a real threat-to-validity
note per design/ORCHESTRATOR_CONTRACT.md item 3, not a hidden one.

    python docs/superpowers/results/2026-08-10-first-real-run/answer_policy_driver.py \
        orchestrator/runs_gemini.yaml /tmp/themas.json

Exit codes are whatever orchestrator.cli._run returns -- see its own docstring.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO_ROOT))

from design.schemas import (  # noqa: E402
    ClarifyingQuestion, IssueCategory, RefinerAnswer, RefinerTurn, RequirementRunRecord,
    RunOutcome,
)
from orchestrator.cli import _run  # noqa: E402
from orchestrator.pipeline import HumanFns  # noqa: E402

# ---------------------------------------------------------------------------------
# The answer policy: one genuine, reasoned answer per IssueCategory (design/schemas.py),
# written once, applied consistently to every question that category produces, across
# both provider runs. `user_confirms_resolved=True` only where the policy makes an
# actual, defensible judgment call (NON_ATOMIC); every other category is deliberately
# conservative -- it explains why this answer can't responsibly resolve the issue,
# rather than inventing a number, referent, or side of a conflict that isn't grounded
# in the document. See ANALYSIS.md for the full rationale and its threat-to-validity
# framing.
# ---------------------------------------------------------------------------------

_ANSWERS: dict[IssueCategory, tuple[str, bool]] = {
    IssueCategory.AMBIGUOUS_TERM: (
        "No numeric threshold is specified in the source document for this term; "
        "treat it as a placeholder the requirements author must supply a measurable "
        "value for (e.g. a specific time, temperature delta, or rate) -- do not invent "
        "a number that isn't grounded in the document.", False),
    IssueCategory.NON_ATOMIC: (
        "Keep this as one requirement. The bundled actions describe a single causal "
        "step (one trigger producing one behavior), not two independently testable "
        "behaviors -- splitting would fragment one atomic step rather than separate "
        "genuinely distinct requirements.", True),
    IssueCategory.INCOMPLETE: (
        "The missing element is not stated anywhere else in the document either; flag "
        "it as a genuine gap rather than an omission this answer can fill in -- "
        "inventing the missing actor/trigger/condition here would misattribute an "
        "assumption to the original requirement.", False),
    IssueCategory.NON_VERIFIABLE: (
        "No pass/fail criterion exists for this in the source document; without a "
        "measurable acceptance threshold this cannot be made verifiable by answering a "
        "clarifying question -- it needs a threshold added by whoever owns the "
        "requirement, not invented here.", False),
    IssueCategory.INFEASIBLE_FOR_TYPE: (
        "Accept the classifier's system type as given for this run; if the requirement "
        "genuinely doesn't fit that type, that is itself the finding worth recording, "
        "not something this answer should paper over by picking a different type.", False),
    IssueCategory.INCONSISTENT: (
        "This is a cross-requirement conflict, not something one requirement's "
        "clarifying answer can resolve in isolation; record the conflict as-is rather "
        "than have this answer silently pick a side.", False),
    IssueCategory.CIRCULAR_DEPENDENCY: (
        "This is a structural dependency-graph issue spanning multiple requirements; it "
        "needs resolving at the document level (breaking the cycle), not by rewriting "
        "one requirement's wording in isolation.", False),
    IssueCategory.VAGUE_PRONOUN: (
        "The referent is not named explicitly nearby in the document; treat the "
        "pronoun as needing an explicit noun substituted once the correct referent is "
        "confirmed by whoever owns the requirement -- guessing the referent here risks "
        "silently attributing the wrong meaning.", False),
}


def _answer_for(question: ClarifyingQuestion) -> RefinerAnswer:
    text, confirmed = _ANSWERS[question.issue_category]
    return RefinerAnswer(question_id=question.id, answer_text=text, user_confirms_resolved=confirmed)


def answer_questions_policy(turn: RefinerTurn) -> list[RefinerAnswer]:
    print(f"[policy] {turn.requirement_id} revision {turn.revision_number}: "
          f"answering {len(turn.questions)} question(s) via fixed AI policy "
          f"({', '.join(q.issue_category.value for q in turn.questions)})")
    return [_answer_for(q) for q in turn.questions]


def decide_at_cap_policy(record: RequirementRunRecord) -> tuple[RunOutcome, str]:
    # Conservative default consistent with the answer policy above: never fabricate
    # confidence the policy doesn't have. Stop rather than generate tests from
    # best-effort text the policy explicitly declined to certify as resolved.
    print(f"[policy] revision cap reached for {record.requirement.id} -- stopping "
          "(policy default: decline to generate tests from unresolved text)")
    return (RunOutcome.CAP_STOPPED,
           "AI answer policy declines to certify outstanding issues as resolved; "
           "stopping at the revision cap rather than generating tests from "
           "best-effort text. See ANALYSIS.md, \"Human-in-the-loop answers\".")


def _human_fns_factory() -> HumanFns:
    return HumanFns(answer_questions=answer_questions_policy, decide_at_cap=decide_at_cap_policy)


def main(argv: list[str]) -> int:
    return _run(["run", *argv], human_fns_factory=_human_fns_factory)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
