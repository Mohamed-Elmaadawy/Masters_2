"""Second human-answer policy, beside answer_policy_driver.py (2026-08-10) and
run_driver.py (2026-08-11): instead of a scripted, AI-authored answer per
IssueCategory, replays the REAL answers a live human gave during the 2026-08-14
session (docs/superpowers/plans/2026-08-14-live-answer-policy.md, section 4), as
captured verbatim in answers.json by extract_answers.py.

Does not modify orchestrator/, design/, or the original drivers, and does not touch
answer_policy_driver.py's texts. Reuses decide_at_cap_policy (default: stop at the
cap) and _decide_at_cap_generate (S11a's one deliberate deviation) from the existing
drivers by import, unchanged -- this driver's only new logic is answer_questions.

Pop-in-order lookup: for each ClarifyingQuestion, pops the next unused entry for its
`requirement_id::issue_category` key from answers.json's `answers` map (in the order
the human answered them). On a miss -- an unknown key, or a key whose list is already
exhausted -- returns the fallback block VERBATIM and counts it; never improvises,
never falls back to the refusing policy's texts (see the plan, section 4). When a
matched entry's stored question_text differs from the question actually being asked,
this prints one stderr warning naming both and counts it, but still uses the stored
answer -- a high warning count means the transcript and this replay are answering two
different conversations.

    python answering_policy_driver.py CONFIG.yaml INPUT.json [--cap-decision generate|stop]
    python answering_policy_driver.py --self-test [--answers PATH]

Exit codes are whatever orchestrator.cli._run returns, same as every other driver
here -- except --self-test, which returns 0 (zero misses and zero drift) or 1, and
never calls _run or any provider adapter.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO_ROOT))


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_original_driver = _load_module(
    "answer_policy_driver",
    _REPO_ROOT / "docs" / "superpowers" / "results" / "2026-08-10-first-real-run"
    / "answer_policy_driver.py",
)
_suite_driver = _load_module(
    "run_driver",
    _REPO_ROOT / "docs" / "superpowers" / "results" / "2026-08-11-behavior-scenarios"
    / "run_driver.py",
)
_extract_answers = _load_module(
    "extract_answers", Path(__file__).resolve().parent / "extract_answers.py",
)

from design.schemas import RefinerAnswer, RefinerTurn  # noqa: E402
from orchestrator.cli import _run  # noqa: E402
from orchestrator.pipeline import HumanFns  # noqa: E402
from orchestrator.providers.base import ProviderAdapter  # noqa: E402
from orchestrator.providers.groq import GroqAdapter  # noqa: E402

_DEFAULT_ANSWERS_PATH = Path(__file__).resolve().parent / "answers.json"


class TranscriptAnswerPolicy:
    """Replays answers.json's captured live-human answers, pop-in-order per
    requirement_id::issue_category key. See module docstring for the miss/fallback/
    drift rules -- this class only tracks the counts; the rules themselves live in
    answer_questions."""

    def __init__(self, answers_path: Path = _DEFAULT_ANSWERS_PATH):
        payload = json.loads(answers_path.read_text(encoding="utf-8"))
        self.source_runs: list[str] = payload["source_runs"]
        self.captured: str = payload["captured"]
        self._queues: dict[str, list[dict]] = {
            key: list(entries) for key, entries in payload["answers"].items()
        }
        self._fallback = payload["fallback"]
        self.miss_count = 0
        self.drift_count = 0

    def answer_questions(self, turn: RefinerTurn) -> list[RefinerAnswer]:
        answers = []
        for question in turn.questions:
            key = f"{turn.requirement_id}::{question.issue_category.value}"
            queue = self._queues.get(key)
            if not queue:
                self.miss_count += 1
                answers.append(RefinerAnswer(
                    question_id=question.id,
                    answer_text=self._fallback["answer_text"],
                    user_confirms_resolved=self._fallback["user_confirms_resolved"],
                ))
                continue
            entry = queue.pop(0)
            if entry["question_text"] != question.question_text:
                self.drift_count += 1
                print(
                    f"[answering-policy] question_text drift for {key} "
                    f"(revision {turn.revision_number}):\n"
                    f"  captured: {entry['question_text']!r}\n"
                    f"  actual:   {question.question_text!r}",
                    file=sys.stderr,
                )
            answers.append(RefinerAnswer(
                question_id=question.id,
                answer_text=entry["answer_text"],
                user_confirms_resolved=entry["user_confirms_resolved"],
            ))
        return answers


def _paid_gemini_adapter() -> ProviderAdapter:
    return _suite_driver._paid_gemini_adapter()


def _human_fns_factory(policy: TranscriptAnswerPolicy, cap_decision: str) -> HumanFns:
    decide_at_cap = (
        _suite_driver._decide_at_cap_generate if cap_decision == "generate"
        else _original_driver.decide_at_cap_policy
    )
    return HumanFns(answer_questions=policy.answer_questions, decide_at_cap=decide_at_cap)


def _self_test(answers_path: Path) -> int:
    """Offline verification (plan section 4 / the live session's step 3): replays
    every ClarifyingQuestion actually asked across this session's six run directories
    -- read straight back from each requirement's on-disk RequirementRunRecord, the
    same records extract_answers.py built answers.json from -- through
    TranscriptAnswerPolicy.answer_questions, and reports the miss/drift counts. Since
    answers.json was built from exactly these records, a correct driver must produce
    zero of each. Makes no network call and never touches orchestrator.cli._run."""
    policy = TranscriptAnswerPolicy(answers_path)
    turns_replayed = 0
    for run_dir in _extract_answers._RUN_DIRS:
        for req_path in sorted((run_dir / "requirements").glob("*.json")):
            record = _extract_answers.RequirementRunRecord.model_validate_json(
                req_path.read_text(encoding="utf-8"))
            for round_ in record.rounds:
                if round_.turn is None:
                    continue
                policy.answer_questions(round_.turn)
                turns_replayed += 1
    print(f"self-test: replayed {turns_replayed} turn(s) -- "
          f"misses={policy.miss_count} drift_warnings={policy.drift_count}")
    return 0 if policy.miss_count == 0 and policy.drift_count == 0 else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", nargs="?")
    parser.add_argument("input", nargs="?")
    parser.add_argument("--cap-decision", choices=["generate", "stop"], default="stop",
                         help="S11 only: which decide_at_cap branch to use. Every other "
                              "scenario uses the shared stop-by-default policy regardless "
                              "of this flag, since the cap is not expected to fire.")
    parser.add_argument("--answers", type=Path, default=_DEFAULT_ANSWERS_PATH,
                         help="Path to answers.json (default: alongside this script).")
    parser.add_argument("--self-test", action="store_true",
                         help="Offline check: replay this session's own transcript and "
                              "confirm zero misses/drift. No network call, no --config/--input.")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test(args.answers)

    if not args.config or not args.input:
        parser.error("config and input are required unless --self-test is given")

    policy = TranscriptAnswerPolicy(args.answers)
    exit_code = _run(
        ["run", args.config, args.input],
        adapter_factories={"gemini": _paid_gemini_adapter, "groq": GroqAdapter.from_env},
        human_fns_factory=lambda: _human_fns_factory(policy, args.cap_decision),
    )
    print(f"[answering-policy] miss_count={policy.miss_count} drift_count={policy.drift_count}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
