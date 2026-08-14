"""One-off driver: run one scenario with a REAL live human at the two HumanFns seams,
for docs/superpowers/plans/2026-08-14-live-answer-policy.md.

Does not touch orchestrator/, design/, or the prompts. It imports orchestrator/cli.py's
own test seam (`_run`, the same function `python -m orchestrator.cli` calls) and supplies
two substitutions, same shape as docs/superpowers/results/2026-08-10-gemini-paid-tier-run/
paid_gemini_driver.py:

- adapter_factories: GEMINI_API_KEY_PAID instead of the free-tier GEMINI_API_KEY.
- human_fns_factory: orchestrator/human_cli.py's real answer_questions_cli/decide_at_cap_cli,
  UNCHANGED, called with injected input_fn/output_fn (the seam those two functions already
  expose -- see their module docstring) that talk to a human over two files in a "bridge"
  directory instead of over a real terminal's stdin/stdout. No answer text is generated or
  guessed here; every byte written to answer.txt comes from whoever is watching this
  process's bridge directory and typing into it live.

Bridge protocol (one bridge dir per scenario run):
    context.json      -- written once per RefinerTurn, before delegating to
                          answer_questions_cli: {requirement_id, revision_number,
                          text_checked, questions: [{id, issue_id, issue_category,
                          question_text}, ...]}. text_checked is read back from this run's
                          own on-disk requirement record (checkpointed by pipeline.py
                          immediately before this callback fires), never invented here.
    cap_context.json  -- written once per decide_at_cap call, before delegating to
                          decide_at_cap_cli: {requirement_id, text_checked, issues: [...],
                          fixed_choice}. fixed_choice is this scenario's prescribed
                          generate/stop branch (11a/11b), or null when the human must
                          choose.
    prompt.txt         -- overwritten on every input_fn() call with that call's literal
                          prompt string (e.g. "Your answer: ").
    answer.txt         -- polled for; once present, its content is the answer, and both
                          prompt.txt/answer.txt are removed before returning.
    output.log         -- append-only, every output_fn() print.

Usage:
    python live_bridge_driver.py CONFIG.yaml INPUT.json BRIDGE_DIR [fixed_choice]

fixed_choice is "generate" or "stop" (scn-11a/11b only); omit for every other scenario.
Exit codes are whatever orchestrator.cli._run returns.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO_ROOT))

from design.schemas import RequirementRunRecord  # noqa: E402
from orchestrator.cli import _run  # noqa: E402
from orchestrator.config import load_run_config, resolve_run_config, run_dir_for  # noqa: E402
from orchestrator.human_cli import answer_questions_cli, decide_at_cap_cli  # noqa: E402
from orchestrator.pipeline import HumanFns  # noqa: E402
from orchestrator.providers.base import ProviderAdapter  # noqa: E402
from orchestrator.providers.gemini import GeminiAdapter  # noqa: E402
from orchestrator.providers.groq import GroqAdapter  # noqa: E402

_POLL_SECONDS = 0.4


def _env_or_dotenv(name: str) -> str | None:
    """os.environ first; falls back to parsing _REPO_ROOT/.env directly, since nothing
    in this repo calls python-dotenv and the shell that launches this script may not
    have exported the var. Strips a single layer of matching quotes -- the only .env
    quoting style this repo's keys use."""
    value = os.environ.get(name)
    if value:
        return value
    env_path = _REPO_ROOT / ".env"
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        if key.strip() != name:
            continue
        v = raw.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        return v
    return None


def _paid_gemini_adapter() -> ProviderAdapter:
    key = _env_or_dotenv("GEMINI_API_KEY_PAID")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY_PAID is not set (checked environment and .env) -- this "
            "driver is opt-in only, by design; it never falls back to GEMINI_API_KEY")
    return GeminiAdapter(api_key=key)


def _wait_for_file(path: Path) -> str:
    while not path.exists():
        time.sleep(_POLL_SECONDS)
    text = path.read_text(encoding="utf-8")
    path.unlink()
    return text


def _make_output_fn(bridge_dir: Path):
    log_path = bridge_dir / "output.log"

    def output_fn(text: str) -> None:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")

    return output_fn


def _make_input_fn(bridge_dir: Path):
    prompt_path = bridge_dir / "prompt.txt"
    answer_path = bridge_dir / "answer.txt"

    def input_fn(prompt: str) -> str:
        prompt_path.write_text(prompt, encoding="utf-8")
        answer = _wait_for_file(answer_path)
        prompt_path.unlink(missing_ok=True)
        return answer

    return input_fn


def _requirement_record_path(run_dir: Path, requirement_id: str) -> Path:
    digest = hashlib.sha256(requirement_id.encode("utf-8")).hexdigest()
    return run_dir / "requirements" / f"{digest}.json"


def _read_text_checked(run_dir: Path, requirement_id: str, revision_number: int) -> str:
    path = _requirement_record_path(run_dir, requirement_id)
    record = RequirementRunRecord.model_validate_json(path.read_text(encoding="utf-8"))
    for round_ in reversed(record.rounds):
        if round_.revision_number == revision_number:
            return round_.text_checked
    return record.rounds[-1].text_checked


def _make_answer_questions(bridge_dir: Path, run_dir: Path):
    input_fn = _make_input_fn(bridge_dir)
    output_fn = _make_output_fn(bridge_dir)
    context_path = bridge_dir / "context.json"

    def answer_questions(turn):
        context = {
            "requirement_id": turn.requirement_id,
            "revision_number": turn.revision_number,
            "text_checked": _read_text_checked(run_dir, turn.requirement_id, turn.revision_number),
            "questions": [
                {
                    "id": q.id,
                    "issue_id": q.issue_id,
                    "issue_category": q.issue_category.value,
                    "question_text": q.question_text,
                }
                for q in turn.questions
            ],
        }
        context_path.write_text(json.dumps(context, indent=2), encoding="utf-8")
        return answer_questions_cli(turn, input_fn=input_fn, output_fn=output_fn)

    return answer_questions


def _make_decide_at_cap(bridge_dir: Path, fixed_choice: str | None):
    input_fn = _make_input_fn(bridge_dir)
    output_fn = _make_output_fn(bridge_dir)
    cap_context_path = bridge_dir / "cap_context.json"

    def decide_at_cap(record):
        last_round = record.rounds[-1]
        cap_context = {
            "requirement_id": record.requirement.id,
            "text_checked": last_round.text_checked,
            "issues": [
                {"category": i.category.value, "explanation": i.explanation}
                for i in last_round.quality_report.issues
            ],
            "fixed_choice": fixed_choice,
        }
        cap_context_path.write_text(json.dumps(cap_context, indent=2), encoding="utf-8")

        if fixed_choice is not None:
            def choice_input_fn(prompt: str) -> str:
                if prompt.startswith("Generate tests"):
                    return fixed_choice
                return input_fn(prompt)
            return decide_at_cap_cli(record, input_fn=choice_input_fn, output_fn=output_fn)

        return decide_at_cap_cli(record, input_fn=input_fn, output_fn=output_fn)

    return decide_at_cap


def main(argv: list[str]) -> int:
    config_path = Path(argv[0])
    input_path = Path(argv[1])
    bridge_dir = Path(argv[2])
    fixed_choice = argv[3] if len(argv) > 3 and argv[3] else None
    if fixed_choice not in (None, "generate", "stop"):
        raise ValueError(f"fixed_choice must be 'generate', 'stop', or omitted, got {fixed_choice!r}")

    bridge_dir.mkdir(parents=True, exist_ok=True)

    # Read-only recomputation of run_dir, same inputs orchestrator/cli.py's own _do_run
    # will independently resolve -- needed here only so answer_questions can read back
    # this run's own on-disk requirement records (see _read_text_checked).
    resolved = resolve_run_config(load_run_config(config_path), config_path)
    run_dir = run_dir_for(resolved)

    def human_fns_factory() -> HumanFns:
        return HumanFns(
            answer_questions=_make_answer_questions(bridge_dir, run_dir),
            decide_at_cap=_make_decide_at_cap(bridge_dir, fixed_choice),
        )

    return _run(
        ["run", str(config_path), str(input_path)],
        adapter_factories={"gemini": _paid_gemini_adapter, "groq": GroqAdapter.from_env},
        human_fns_factory=human_fns_factory,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
