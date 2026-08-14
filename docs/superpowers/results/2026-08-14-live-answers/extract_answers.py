"""Extraction script for docs/superpowers/plans/2026-08-14-live-answer-policy.md,
section 3: builds answers.json from the run records this session produced, and
nothing else.

Every question_text/answer_text/user_confirms_resolved/revision_number in the output
comes from a RefinerTurn/RefinerAnswer pair inside a requirement's on-disk
RequirementRunRecord (orchestrator/pipeline.py's write_requirement_run). No text is
retyped from the bridge's now-deleted context.json files or from chat -- the plan is
explicit that capture comes from the run records, not a second, disagreement-prone
copy (CLAUDE.md, "two fields that must agree").

    python extract_answers.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO_ROOT))

from design.schemas import RequirementRunRecord  # noqa: E402

_RESULTS_DIR = Path(__file__).resolve().parent
_RUN_DIRS = [
    _RESULTS_DIR / "configs" / "runs_scn-08-clean" / "scn-08-clean",
    _RESULTS_DIR / "configs" / "runs_scn-09-vague" / "scn-09-vague",
    _RESULTS_DIR / "configs" / "runs_scn-10-atomicity" / "scn-10-atomicity",
    _RESULTS_DIR / "configs" / "runs_scn-04-conflict-numeric" / "scn-04-conflict-numeric",
    _RESULTS_DIR / "configs" / "runs_scn-11a-cap-generate" / "scn-11a-cap-generate",
    _RESULTS_DIR / "configs" / "runs_scn-11b-cap-stop" / "scn-11b-cap-stop",
]


def _relative(path: Path) -> str:
    return str(path.relative_to(_REPO_ROOT)).replace("\\", "/")


def build_answers() -> dict[str, list[dict]]:
    answers: dict[str, list[dict]] = {}
    for run_dir in _RUN_DIRS:
        for req_path in sorted((run_dir / "requirements").glob("*.json")):
            record = RequirementRunRecord.model_validate_json(req_path.read_text(encoding="utf-8"))
            for round_ in record.rounds:
                if round_.turn is None:
                    continue
                questions_by_id = {q.id: q for q in round_.turn.questions}
                for answer in round_.answers:
                    question = questions_by_id[answer.question_id]
                    key = f"{record.requirement.id}::{question.issue_category.value}"
                    answers.setdefault(key, []).append({
                        "question_text": question.question_text,
                        "answer_text": answer.answer_text,
                        "user_confirms_resolved": answer.user_confirms_resolved,
                        "revision_number": round_.revision_number,
                    })
    return answers


def main() -> None:
    payload = {
        "source_runs": [_relative(d) for d in _RUN_DIRS],
        "captured": "2026-08-14",
        "answers": build_answers(),
        "fallback": {
            "answer_text": "Not covered by the recorded transcript for this requirement and issue category.",
            "user_confirms_resolved": False,
        },
    }
    out_path = _RESULTS_DIR / "answers.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    total_answers = sum(len(v) for v in payload["answers"].values())
    print(f"wrote {out_path} -- {total_answers} answer(s) across {len(payload['answers'])} key(s)")


if __name__ == "__main__":
    main()
