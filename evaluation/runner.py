"""Task 6 runner: executes B1 (one prompt, no human interaction) and B2 (one prompt
asking clarifying questions, one operator-answered round, then generation) against a
`RequirementSet`, using the SAME provider adapter, model, temperature and output mode
arm P uses. No pipeline stage sequencing, revision-cap, or retry-with-cap machinery --
B1 makes exactly one LLM call, B2 makes exactly two, every time (a resumed B2 makes
exactly one, the generate call -- see `run_b2_resume`).

Reuses `orchestrator/stages.py`'s prompt-rendering/JSON-extraction helpers
(`_render_prompt`, `_json_dynamic`, `_doc_id_field`, `_requirements_json`,
`_extract_json`) -- generic, injection-hardened plumbing, not pipeline control flow --
rather than re-implementing the same escaping/parsing logic here. Does NOT reuse
`orchestrator/pipeline.py`'s `call_stage`: that function is built around
`PipelineStage`/`req_id`/`extra_check`/`StageAttempt`, none of which fits a whole-set,
non-per-requirement call. `_call_once` below is this module's own, much smaller,
retry loop with the same shape (attempt, then backoff; `StageCallFatal` short-circuits
immediately) minus everything pipeline-specific.

CLI usage:

    python -m evaluation.runner b1 REQUIREMENT_SET.json --output b1-result.json

    python -m evaluation.runner b2 REQUIREMENT_SET.json --interactive \\
        --output b2-result.json

    python -m evaluation.runner b2 REQUIREMENT_SET.json \\
        --answers-json answers.json --output b2-result.json

    python -m evaluation.runner b2-resume REQUIREMENT_SET.json \\
        --checkpoint b2-result.checkpoint.json --interactive --output b2-result.json

`--provider`/`--model`/`--temperature`/`--timeout-seconds`/`--output-mode` all default
to arm P's own frozen experiment config (`evaluation/config_parity.py`) -- a plain
invocation with none of them automatically matches it, and passing one that diverges is
rejected before any adapter is constructed.

`--interactive` prints each clarifying question and reads the operator's answer from
stdin -- the live-answering path for a real run. `--answers-json` instead reads a JSON
array of answer strings, matched POSITIONALLY to the order the model returned its
questions in (question ids are minted by the model per run and cannot be known ahead
of time, unlike the pipeline's frozen `answers.json` transcript replay -- see
`docs/superpowers/results/2026-08-14-live-answers/`) -- for scripted/offline testing
only, documented as such so it is never mistaken for the live path.

`b2-resume` continues a B2 run from the checkpoint `b2`'s `--interactive`/
`--answers-json` run writes right after its questions call succeeds and before the
operator is asked to answer (`<output-stem>.checkpoint.json`) -- for recovering from an
interrupted or failed answering step (Ctrl+C, closed stdin, a crashed answer source)
without re-spending the questions call. It never renders or sends the questions prompt
again (see `run_b2_resume`'s own docstring), and rejects a checkpoint that does not
match the CURRENT config, requirement set, or questions-prompt content before
constructing an adapter.

Neither CLI mode makes a network call by itself. See `evaluation/test_runner.py` for
this module's test suite, run entirely against a scripted fake adapter -- no network
call, no API key, ever (`python -m evaluation.test_runner`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, ValidationError

from design.schemas import OutputMode, RequirementSet
from evaluation.atomic_io import atomic_write_json
from evaluation.config_parity import enforce_fair_config, frozen_arm_p_config
from evaluation.pricing import compute_cost
from evaluation.schemas import (
    BaselineAttempt, BaselineCallConfig, BaselineQuestionBatch, BaselineRunOutput,
    BaselineTestCaseBatch, ClarificationAnswer, ClarificationQuestion,
)
from orchestrator.providers.base import CompletionResult, ProviderAdapter
from orchestrator.providers.gemini import GeminiAdapter
from orchestrator.providers.groq import GroqAdapter
from orchestrator.stage_fns import StageCallFailed, StageCallFatal, StageCallPartial
from orchestrator.stages import (
    _doc_id_field, _extract_json, _json_dynamic, _render_prompt, _requirements_json,
)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_B1_PROMPT_PATH = _PROMPTS_DIR / "b1_baseline.txt"
_B2_QUESTIONS_PROMPT_PATH = _PROMPTS_DIR / "b2_questions.txt"
_B2_GENERATE_PROMPT_PATH = _PROMPTS_DIR / "b2_generate.txt"

EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 2
EXIT_INTERRUPTED = 1  # same code as any other failed run -- see main()'s docstring note

AnswerFn = Callable[[list[ClarificationQuestion]], list[ClarificationAnswer]]

_ADAPTER_FACTORIES = {
    "gemini": lambda: GeminiAdapter.from_env(),
    "groq": lambda: GroqAdapter.from_env(),
}

CheckpointFn = Callable[[BaselineRunOutput], None]


def _prompt_hash(text: str) -> str:
    """Same idea as `ResolvedStageConfig.prompt_hash` (orchestrator/config.py):
    computed from the prompt file's actual content, never authored, so a silent prompt
    edit between runs is visible in the recorded provenance."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _requirement_set_hash(requirement_set: RequirementSet) -> str:
    """Deterministic content hash of the canonical RequirementSet (2026-08-17,
    third-round finding 2). `doc_id` alone does not prove the requirement TEXT is
    unchanged -- a document can be edited in place without renaming it -- and `doc_id`
    can be `None` for two genuinely different inputs, which makes it useless as a
    match key exactly then. `model_dump_json()`'s field order is stable (pydantic v2
    preserves declared field order on serialization), so the same RequirementSet
    always hashes to the same value and a changed one -- different text, different
    requirement ids, different order -- always hashes differently. Same sha256-hex-
    prefix style as `_prompt_hash`."""
    return hashlib.sha256(requirement_set.model_dump_json().encode("utf-8")).hexdigest()[:12]


def _validate_output_destinations(*paths: Path) -> None:
    """Checked before any adapter is constructed (2026-08-17, Task 6 finding 6): a
    successful -- possibly paid -- call must not be lost because the directory meant
    to receive its output does not exist. Checks parent-directory existence only; a
    full writability probe would itself have side effects and still could not
    guarantee a LATER write succeeds (disk fills up mid-run, permissions change), so
    this catches the common, cheap-to-check failure -- a typo'd or missing output
    directory -- without pretending to guarantee more than that."""
    for p in paths:
        parent = p.parent
        if not parent.exists():
            raise ValueError(f"output directory does not exist: {parent} (for {p})")
        if not parent.is_dir():
            raise ValueError(f"output path's parent is not a directory: {parent} (for {p})")


def _validate_no_path_collisions(outputs: dict, inputs: dict) -> None:
    """Checked before any adapter is constructed (2026-08-17, third-round finding 5):
    rejects if any OUTPUT artifact path this run is about to WRITE resolves to the
    same file as any INPUT path it reads FROM -- writing results over an input (the
    RequirementSet, an answers-json file, or the resume checkpoint being read) is
    never intentional and would corrupt or destroy something the run itself needed.

    Deliberately does NOT check output-to-output collisions across two SEPARATE
    invocations -- `--output out.json` legitimately names the SAME file on both an
    initial `b2` run and a later `b2-resume` run, overwriting the earlier partial/
    failed result with the final one. That is the one intentional overwrite this CLI
    supports, and it never appears as a collision within one invocation's own path set
    (this function only ever sees one invocation's paths at a time), so no special
    case is needed to preserve it."""
    for out_name, out_path in outputs.items():
        if out_path is None:
            continue
        out_resolved = out_path.resolve()
        for in_name, in_path in inputs.items():
            if in_path is None:
                continue
            if out_resolved == in_path.resolve():
                raise ValueError(
                    f"--{out_name} ({out_path}) resolves to the same file as "
                    f"{in_name} ({in_path}) -- an output must not overwrite an input")


def _call_once(
    adapter: ProviderAdapter, config: BaselineCallConfig, prompt: str, call: str,
    model_cls: type[BaseModel], attempts: list[BaselineAttempt],
    max_attempts: int = 3, backoff_seconds: Callable[[int], float] = lambda a: 2.0 ** a,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Optional[BaseModel]:
    """One logical call, retried on transient failure. Records exactly one
    `BaselineAttempt` per try (success or not), same "a retry that ultimately succeeds
    still leaves a full record of what came before it" reasoning as `call_stage`'s own
    docstring. A `StageCallFailed` (transport) or a parse/schema-validation failure both
    retry, up to `max_attempts`; a `StageCallFatal` short-circuits immediately, no
    retry, no backoff sleep -- same distinction `call_stage` makes. Returns the
    validated `model_cls` instance on success, `None` once every attempt is exhausted.
    """
    for attempt in range(1, max_attempts + 1):
        start = time.perf_counter()
        try:
            result: CompletionResult = adapter.complete(
                prompt, model=config.model, temperature=config.temperature,
                timeout_seconds=config.timeout_seconds, output_mode=config.output_mode)
        except StageCallFatal as e:
            attempts.append(BaselineAttempt(
                call=call, attempt_number=attempt, result="fatal", error_message=str(e),
                wall_clock_seconds=time.perf_counter() - start))
            return None
        except StageCallFailed as e:
            attempts.append(BaselineAttempt(
                call=call, attempt_number=attempt, result="failed", error_message=str(e),
                wall_clock_seconds=time.perf_counter() - start))
            if attempt < max_attempts:
                sleep_fn(backoff_seconds(attempt))
            continue

        elapsed = time.perf_counter() - start
        try:
            data = _extract_json(result.text, result.prompt_tokens, result.completion_tokens)
            validated = model_cls.model_validate(data)
        except StageCallPartial as e:
            attempts.append(BaselineAttempt(
                call=call, attempt_number=attempt, result="partial", error_message=str(e),
                prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
                wall_clock_seconds=elapsed))
            if attempt < max_attempts:
                sleep_fn(backoff_seconds(attempt))
            continue
        except ValidationError as e:
            attempts.append(BaselineAttempt(
                call=call, attempt_number=attempt, result="partial",
                error_message=f"parsed JSON did not match {model_cls.__name__}: {e}",
                prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
                wall_clock_seconds=elapsed))
            if attempt < max_attempts:
                sleep_fn(backoff_seconds(attempt))
            continue

        attempts.append(BaselineAttempt(
            call=call, attempt_number=attempt, result="success",
            prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
            wall_clock_seconds=elapsed))
        return validated
    return None


def run_b1(
    adapter: ProviderAdapter, config: BaselineCallConfig, requirement_set: RequirementSet,
    prompt_path: Path = _B1_PROMPT_PATH, max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda a: 2.0 ** a,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> BaselineRunOutput:
    """B1: one prompt, whole requirement set in, test cases out. No human interaction,
    no second call under any outcome. `backoff_seconds`/`sleep_fn` are injectable so
    tests never actually sleep through a real retry backoff (same reasoning as
    `orchestrator/pipeline.py`'s `Throttle`)."""
    template = prompt_path.read_text()
    prompt = _render_prompt(
        template, doc_id=_doc_id_field(requirement_set),
        requirements_json=_requirements_json(requirement_set))

    attempts: list[BaselineAttempt] = []
    start = time.perf_counter()
    batch = _call_once(adapter, config, prompt, "b1_generate", BaselineTestCaseBatch,
                       attempts, max_attempts=max_attempts, backoff_seconds=backoff_seconds,
                       sleep_fn=sleep_fn)
    total = time.perf_counter() - start

    return BaselineRunOutput(
        arm="B1", doc_id=requirement_set.doc_id,
        requirement_set_hash=_requirement_set_hash(requirement_set),
        provider=config.provider,
        model=config.model, temperature=config.temperature,
        timeout_seconds=config.timeout_seconds, output_mode=config.output_mode,
        prompt_hash={"b1_generate": _prompt_hash(template)},
        attempts=attempts, test_cases=(batch.test_cases if batch else []),
        total_wall_clock_seconds=total, failed=batch is None)


def _validate_answers_cover_questions(
    questions: list[ClarificationQuestion], answers: list[ClarificationAnswer],
) -> None:
    """Fails loud, before the (paid) generate call, if `answer_fn` didn't actually
    answer everything it was asked -- rather than silently sending a partial Q&A
    context to the model and finding out later. Not a retry point: there is no
    mechanism anywhere in this module to go back and ask `answer_fn` again, which is
    exactly what makes the one-round limit real rather than just documented."""
    asked = {q.id for q in questions}
    answered = [a.question_id for a in answers]
    if len(answered) != len(set(answered)):
        raise ValueError(f"answer_fn returned more than one answer for the same question id: {answered}")
    missing = asked - set(answered)
    if missing:
        raise ValueError(f"answer_fn did not answer question id(s): {sorted(missing)}")
    unknown = set(answered) - asked
    if unknown:
        raise ValueError(f"answer_fn answered unknown question id(s): {sorted(unknown)}")


def _b2_answer_and_generate(
    adapter: ProviderAdapter, config: BaselineCallConfig, requirement_set: RequirementSet,
    req_hash: str, questions: list[ClarificationQuestion], attempts: list[BaselineAttempt],
    prompt_hash_so_far: dict, prior_wall_clock_seconds: float, answer_fn: AnswerFn,
    generate_prompt_path: Path, max_attempts: int,
    backoff_seconds: Callable[[int], float], sleep_fn: Callable[[float], None],
) -> BaselineRunOutput:
    """The shared tail of a B2 run, used by BOTH `run_b2` (right after a live
    questions call) and `run_b2_resume` (right after loading a checkpoint) -- exactly
    one call to `answer_fn`, then (if that succeeds) exactly one generate call. Never
    touches the questions prompt: `questions`/`attempts`/`prompt_hash_so_far` arrive
    already produced, by whichever caller produced them, and this function has no
    reference to `_B2_QUESTIONS_PROMPT_PATH` or any questions-rendering call at all --
    the one-round limit and "never re-ask" guarantee hold for a resumed run for the
    exact same structural reason they hold for a fresh one.

    `req_hash` is passed in already computed (not recomputed here from
    `requirement_set`) so a resumed run's final record carries the SAME hash the
    checkpoint recorded, not a fresh one computed from whatever `RequirementSet` this
    call happened to receive -- the caller (`run_b2_resume`) is the one that already
    validated the two agree before ever getting here."""
    start = time.perf_counter()
    try:
        answers = answer_fn(questions) if questions else []
        _validate_answers_cover_questions(questions, answers)
    except Exception as e:
        # Answer-source failure (EOF, interrupted, malformed answers, or any other
        # exception the answer source raises): the questions call's own tokens/
        # wall-clock/attempts are still on `attempts` and returned below, not
        # discarded. No generate call is made -- the code simply never reaches it.
        return BaselineRunOutput(
            arm="B2", doc_id=requirement_set.doc_id, requirement_set_hash=req_hash,
            provider=config.provider,
            model=config.model, temperature=config.temperature,
            timeout_seconds=config.timeout_seconds, output_mode=config.output_mode,
            prompt_hash=dict(prompt_hash_so_far), attempts=attempts, questions=questions,
            total_wall_clock_seconds=prior_wall_clock_seconds + (time.perf_counter() - start),
            failed=True, failure_reason=f"answer source failed: {type(e).__name__}: {e}")

    answers_by_id = {a.question_id: a.answer_text for a in answers}
    qa_json = _json_dynamic([
        {"question_id": q.id, "question_text": q.question_text,
         "answer_text": answers_by_id[q.id]}
        for q in questions
    ])

    g_template = generate_prompt_path.read_text()
    g_prompt = _render_prompt(
        g_template, doc_id=_doc_id_field(requirement_set),
        requirements_json=_requirements_json(requirement_set), qa_json=qa_json)
    batch = _call_once(adapter, config, g_prompt, "b2_generate", BaselineTestCaseBatch,
                       attempts, max_attempts=max_attempts, backoff_seconds=backoff_seconds,
                       sleep_fn=sleep_fn)
    total = prior_wall_clock_seconds + (time.perf_counter() - start)

    return BaselineRunOutput(
        arm="B2", doc_id=requirement_set.doc_id, requirement_set_hash=req_hash,
        provider=config.provider,
        model=config.model, temperature=config.temperature,
        timeout_seconds=config.timeout_seconds, output_mode=config.output_mode,
        prompt_hash={**prompt_hash_so_far, "b2_generate": _prompt_hash(g_template)},
        attempts=attempts, questions=questions, answers=answers,
        test_cases=(batch.test_cases if batch else []),
        total_wall_clock_seconds=total, failed=batch is None,
        failure_reason=None if batch is not None else "the generate call did not succeed -- see attempts")


def run_b2(
    adapter: ProviderAdapter, config: BaselineCallConfig, requirement_set: RequirementSet,
    answer_fn: AnswerFn,
    questions_prompt_path: Path = _B2_QUESTIONS_PROMPT_PATH,
    generate_prompt_path: Path = _B2_GENERATE_PROMPT_PATH, max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda a: 2.0 ** a,
    sleep_fn: Callable[[float], None] = time.sleep,
    checkpoint_fn: Optional[CheckpointFn] = None,
) -> BaselineRunOutput:
    """B2: one prompt asking clarifying questions, exactly one operator-answered round
    (via `answer_fn`, called at most once), then one generation prompt. Two calls total
    in the code path (questions, then generate) -- never a third, and nothing here can
    route back to ask a second round of questions: `answer_fn` is called exactly once,
    and its result feeds straight into the single generate call.

    Interruption-safe: the successful questions call's `attempts`/tokens/wall-clock and
    the questions themselves are handed to `checkpoint_fn` (if given) BEFORE
    `answer_fn` is invoked -- so a crash or a hard kill during the operator's answering
    step still leaves that work recoverable from the checkpoint (see `run_b2_resume`
    for the resumption path -- it never re-calls the questions prompt). If `answer_fn`
    itself raises (EOF on stdin, an interrupted/failed answer source, a
    malformed-answers ValueError) it is caught, not left to propagate and discard
    everything already gathered -- see `_b2_answer_and_generate`, which this delegates
    to for that handling."""
    q_template = questions_prompt_path.read_text()
    attempts: list[BaselineAttempt] = []
    start = time.perf_counter()
    req_hash = _requirement_set_hash(requirement_set)

    q_prompt = _render_prompt(
        q_template, doc_id=_doc_id_field(requirement_set),
        requirements_json=_requirements_json(requirement_set))
    q_batch = _call_once(adapter, config, q_prompt, "b2_questions", BaselineQuestionBatch,
                         attempts, max_attempts=max_attempts, backoff_seconds=backoff_seconds,
                         sleep_fn=sleep_fn)
    if q_batch is None:
        return BaselineRunOutput(
            arm="B2", doc_id=requirement_set.doc_id, requirement_set_hash=req_hash,
            provider=config.provider,
            model=config.model, temperature=config.temperature,
            timeout_seconds=config.timeout_seconds, output_mode=config.output_mode,
            prompt_hash={"b2_questions": _prompt_hash(q_template)},
            attempts=attempts, total_wall_clock_seconds=time.perf_counter() - start, failed=True,
            failure_reason="the questions call itself did not succeed -- see attempts")

    questions = q_batch.questions
    prompt_hash_so_far = {"b2_questions": _prompt_hash(q_template)}
    elapsed_before_answering = time.perf_counter() - start

    if checkpoint_fn is not None:
        checkpoint_fn(BaselineRunOutput(
            arm="B2", doc_id=requirement_set.doc_id, requirement_set_hash=req_hash,
            provider=config.provider,
            model=config.model, temperature=config.temperature,
            timeout_seconds=config.timeout_seconds, output_mode=config.output_mode,
            prompt_hash=dict(prompt_hash_so_far), attempts=list(attempts), questions=questions,
            total_wall_clock_seconds=elapsed_before_answering, failed=True,
            failure_reason="checkpoint before requesting operator answers -- not a "
                           "final outcome",
            checkpoint_phase="awaiting_answers"))

    return _b2_answer_and_generate(
        adapter, config, requirement_set, req_hash, questions, attempts, prompt_hash_so_far,
        elapsed_before_answering, answer_fn, generate_prompt_path, max_attempts,
        backoff_seconds, sleep_fn)


def run_b2_resume(
    adapter: ProviderAdapter, config: BaselineCallConfig, requirement_set: RequirementSet,
    checkpoint: BaselineRunOutput, answer_fn: AnswerFn,
    generate_prompt_path: Path = _B2_GENERATE_PROMPT_PATH, max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda a: 2.0 ** a,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> BaselineRunOutput:
    """Resumes a B2 run from a checkpoint `run_b2`'s `checkpoint_fn` wrote right
    before the operator was asked to answer. NEVER calls the questions prompt again:
    this function has no `questions_prompt_path` parameter, never imports or reads
    `_B2_QUESTIONS_PROMPT_PATH`, and never renders a questions prompt -- `checkpoint.
    questions` and `checkpoint.attempts` (the already-successful questions call) are
    simply carried forward into the same `_b2_answer_and_generate` tail `run_b2`
    itself uses after its own live questions call. Structurally, not just by
    discipline: there is no code path in this function that could reach a second
    questions call even if it tried to.

    Validates `checkpoint.arm`, `checkpoint.checkpoint_phase == "awaiting_answers"`,
    `checkpoint.requirement_set_hash` against `requirement_set`, and all five
    `checkpoint` config fields against `config` -- ALL before `_b2_answer_and_generate`
    is ever called, so before any `adapter.complete` call (2026-08-17, third-round
    finding 1; requirement-set/config checks moved here from CLI-only 2026-08-17,
    fourth-round finding 1). `main`'s `b2-resume` path already checks all of this
    before constructing an adapter at all, but a caller invoking this function
    directly -- bypassing `main` entirely -- was not covered by that: it could combine
    an old checkpoint's questions with a changed `requirement_set` or a different
    `config`, silently. This function is now safe to call directly, not just from
    `main`; the CLI's own checks are no longer this function's only line of defense,
    they are simply the ones that also get to run before adapter CONSTRUCTION (this
    function receives an already-constructed `adapter`, so it cannot re-create that
    guarantee -- it can only guarantee no CALL happens on that adapter, which it
    does)."""
    if checkpoint.arm != "B2":
        raise ValueError(f"checkpoint.arm must be 'B2' to resume as B2, got {checkpoint.arm!r}")
    if checkpoint.checkpoint_phase != "awaiting_answers":
        raise ValueError(
            "checkpoint.checkpoint_phase must be 'awaiting_answers' to resume -- got "
            f"{checkpoint.checkpoint_phase!r}. Only a checkpoint written by run_b2's own "
            "checkpoint_fn, right before the operator was asked to answer, can be resumed; "
            "a completed or differently-failed B2 output would re-run generation.")
    current_req_hash = _requirement_set_hash(requirement_set)
    if checkpoint.requirement_set_hash != current_req_hash:
        raise ValueError(
            f"checkpoint.requirement_set_hash ({checkpoint.requirement_set_hash!r}) does not "
            f"match the given requirement_set's content hash ({current_req_hash!r}) -- "
            "resuming would combine this checkpoint's questions with different requirement "
            "text than they were actually asked about")
    checkpoint_config = (checkpoint.provider, checkpoint.model, checkpoint.temperature,
                        checkpoint.timeout_seconds, checkpoint.output_mode)
    given_config = (config.provider, config.model, config.temperature,
                    config.timeout_seconds, config.output_mode)
    if checkpoint_config != given_config:
        raise ValueError(
            f"checkpoint's config {checkpoint_config} does not match the given config "
            f"{given_config} -- all five (provider, model, temperature, timeout_seconds, "
            "output_mode) must match exactly to resume")
    return _b2_answer_and_generate(
        adapter, config, requirement_set, checkpoint.requirement_set_hash, checkpoint.questions,
        list(checkpoint.attempts), dict(checkpoint.prompt_hash), checkpoint.total_wall_clock_seconds,
        answer_fn, generate_prompt_path, max_attempts, backoff_seconds, sleep_fn)


# ---------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------

def _interactive_answer_fn(questions: list[ClarificationQuestion]) -> list[ClarificationAnswer]:
    print(f"\n{len(questions)} clarifying question(s) from the model -- answer each once "
         f"(this is the only round):")
    answers = []
    for q in questions:
        print(f"\n[{q.id}] {q.question_text}")
        text = input("> ").strip()
        answers.append(ClarificationAnswer(question_id=q.id, answer_text=text or "(no answer given)"))
    return answers


def _positional_answers_from_file(path: Path) -> AnswerFn:
    """Scripted/offline answer source: a JSON array of answer strings, matched
    POSITIONALLY to the order the model's questions came back in. Documented as
    scripted-only (see this module's docstring) because, unlike the pipeline's frozen
    `answers.json` (keyed by a question id known in advance, from a fixed prompt), a
    baseline's question ids are minted by the model fresh each run and cannot be
    pre-keyed."""
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain a JSON array of answer strings, got {type(raw).__name__}")

    def answer_fn(questions: list[ClarificationQuestion]) -> list[ClarificationAnswer]:
        if len(raw) != len(questions):
            raise ValueError(
                f"{path} has {len(raw)} answer(s) but the model asked {len(questions)} "
                f"question(s) -- positional matching requires an exact count")
        return [ClarificationAnswer(question_id=q.id, answer_text=str(a))
               for q, a in zip(questions, raw)]
    return answer_fn


def _build_arg_parser(frozen: BaselineCallConfig) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="arm", required=True)

    for name in ("b1", "b2", "b2-resume"):
        p = sub.add_parser(name)
        p.add_argument("requirement_set", type=Path)
        p.add_argument("--provider", choices=["gemini", "groq"], default=frozen.provider)
        p.add_argument("--model", default=frozen.model)
        p.add_argument("--temperature", type=float, default=frozen.temperature)
        p.add_argument("--timeout-seconds", type=float, default=frozen.timeout_seconds)
        p.add_argument("--output-mode", choices=[m.value for m in OutputMode],
                       default=frozen.output_mode.value)
        p.add_argument("--output", type=Path, required=True)
        if name in ("b2", "b2-resume"):
            group = p.add_mutually_exclusive_group(required=True)
            group.add_argument("--interactive", action="store_true")
            group.add_argument("--answers-json", type=Path)
        if name == "b2-resume":
            p.add_argument("--checkpoint", type=Path, required=True)

    return parser


def main(argv: list[str]) -> int:
    # Loaded first, no adapter/network involved: arm P's own frozen experiment config
    # becomes every flag's default below, so a plain invocation with no --provider/
    # --model/--temperature/--output-mode automatically matches it.
    try:
        frozen = frozen_arm_p_config()
    except (OSError, ValueError, KeyError) as e:
        print(f"Configuration error: could not load the frozen arm-P reference config: {e}")
        return EXIT_CONFIG_ERROR

    args = _build_arg_parser(frozen).parse_args(argv)

    # ---- Config validated and checked against the frozen reference BEFORE any
    # adapter is constructed or any file (other than argument parsing itself) is
    # touched. BaselineCallConfig's own Field bounds (temperature 0.0-2.0,
    # timeout_seconds > 0) reject an invalid value at construction; enforce_fair_config
    # then rejects a value that parses fine but diverges from arm P's frozen config.
    try:
        candidate = BaselineCallConfig(
            provider=args.provider, model=args.model, temperature=args.temperature,
            timeout_seconds=args.timeout_seconds, output_mode=OutputMode(args.output_mode))
        enforce_fair_config(candidate, frozen)
    except (ValidationError, ValueError) as e:
        print(f"Configuration error: {e}")
        return EXIT_CONFIG_ERROR

    try:
        requirement_set = RequirementSet.model_validate_json(args.requirement_set.read_text())
    except (OSError, ValueError) as e:
        print(f"Configuration/input error: {e}")
        return EXIT_CONFIG_ERROR

    checkpoint: Optional[BaselineRunOutput] = None
    if args.arm == "b2-resume":
        # Checkpoint/input/config/prompt-provenance mismatches all rejected here,
        # before any adapter is constructed (2026-08-17, Task 6 finding 5).
        try:
            checkpoint = BaselineRunOutput.model_validate_json(args.checkpoint.read_text())
        except (OSError, ValidationError) as e:
            print(f"Configuration/input error: could not load checkpoint {args.checkpoint}: {e}")
            return EXIT_CONFIG_ERROR

        if checkpoint.arm != "B2":
            print(f"Configuration error: checkpoint {args.checkpoint} has arm={checkpoint.arm!r}, "
                 f"expected 'B2'")
            return EXIT_CONFIG_ERROR
        # Structural checkpoint-lifecycle check (2026-08-17, third-round finding 1):
        # rejects a completed or differently-failed B2 output BEFORE any of the
        # config/content checks below even run -- checkpoint_phase is enforced at
        # construction/load time by BaselineRunOutput's own validator (evaluation/
        # schemas.py's _awaiting_answers_checkpoint_is_valid), so reaching this line
        # with checkpoint_phase == "awaiting_answers" already means every structural
        # property (only b2_questions attempts, no answers/test_cases, valid
        # retry-then-success sequence, only the questions prompt hash) held at load
        # time -- this check only needs to confirm the marker itself is present.
        if checkpoint.checkpoint_phase != "awaiting_answers":
            print(f"Configuration error: checkpoint {args.checkpoint} is not an "
                 "awaiting-answers checkpoint (checkpoint_phase="
                 f"{checkpoint.checkpoint_phase!r}) -- only a checkpoint written by a "
                 "b2 run's own checkpoint_fn, right before the operator was asked to "
                 "answer, can be resumed. A completed or differently-failed B2 output "
                 "would re-run generation, violating the one-questions-call/"
                 "one-generate-call design.")
            return EXIT_CONFIG_ERROR
        if (checkpoint.provider, checkpoint.model, checkpoint.temperature,
            checkpoint.timeout_seconds, checkpoint.output_mode) != \
           (candidate.provider, candidate.model, candidate.temperature,
            candidate.timeout_seconds, candidate.output_mode):
            print(f"Configuration error: checkpoint {args.checkpoint} was produced under a "
                 f"different configuration (provider={checkpoint.provider!r} "
                 f"model={checkpoint.model!r} temperature={checkpoint.temperature!r} "
                 f"timeout_seconds={checkpoint.timeout_seconds!r} "
                 f"output_mode={checkpoint.output_mode!r}) than the one being resumed with "
                 f"(provider={candidate.provider!r} model={candidate.model!r} "
                 f"temperature={candidate.temperature!r} timeout_seconds={candidate.timeout_seconds!r} "
                 f"output_mode={candidate.output_mode!r}) -- all five effective config "
                 "fields must match exactly")
            return EXIT_CONFIG_ERROR
        if checkpoint.doc_id != requirement_set.doc_id:
            print(f"Configuration/input error: checkpoint {args.checkpoint} was produced for "
                 f"doc_id={checkpoint.doc_id!r}, but the requirement set given now has "
                 f"doc_id={requirement_set.doc_id!r}")
            return EXIT_CONFIG_ERROR
        # Content-binding check (2026-08-17, third-round finding 2): doc_id matching
        # (even matching because both are None) does not prove the requirement TEXT is
        # unchanged. Compares a deterministic hash of the canonical RequirementSet.
        current_req_hash = _requirement_set_hash(requirement_set)
        if checkpoint.requirement_set_hash != current_req_hash:
            print(f"Configuration/input error: checkpoint {args.checkpoint} was produced "
                 f"against a RequirementSet whose content hash was "
                 f"{checkpoint.requirement_set_hash!r}, but the requirement set given now "
                 f"hashes to {current_req_hash!r} -- the requirement text has changed "
                 "since the checkpoint was written (doc_id matching alone does not prove "
                 "this, and doc_id can be None for two different inputs)")
            return EXIT_CONFIG_ERROR
        current_q_hash = _prompt_hash(_B2_QUESTIONS_PROMPT_PATH.read_text())
        if checkpoint.prompt_hash.get("b2_questions") != current_q_hash:
            print(f"Configuration error: checkpoint {args.checkpoint}'s recorded "
                 f"b2_questions prompt_hash ({checkpoint.prompt_hash.get('b2_questions')!r}) "
                 f"does not match the current prompt file's hash ({current_q_hash!r}) -- "
                 "the questions prompt changed since the checkpoint was written; refusing "
                 "to resume with mismatched provenance")
            return EXIT_CONFIG_ERROR

    # answer_fn is built here, before adapter access, not lazily inside the dispatch
    # below -- --answers-json's file is read and validated (JSON array, right shape)
    # by _positional_answers_from_file AT CONSTRUCTION, so a missing or malformed
    # answers file is caught here as a config/input error, never discovered only
    # after a (possibly paid) questions call already ran.
    answer_fn: Optional[AnswerFn] = None
    if args.arm in ("b2", "b2-resume"):
        try:
            answer_fn = (_interactive_answer_fn if args.interactive
                        else _positional_answers_from_file(args.answers_json))
        except (OSError, ValueError) as e:
            print(f"Configuration/input error: {e}")
            return EXIT_CONFIG_ERROR

    cost_path = args.output.with_name(args.output.stem + ".cost.json")
    write_checkpoint_path = (
        args.output.with_name(args.output.stem + ".checkpoint.json") if args.arm == "b2" else None)
    try:
        _validate_output_destinations(
            args.output, cost_path, *([write_checkpoint_path] if write_checkpoint_path else []))
    except ValueError as e:
        print(f"Configuration error: {e}")
        return EXIT_CONFIG_ERROR

    # Path-collision check (2026-08-17, third-round finding 5): no output artifact
    # this run is about to WRITE may resolve to the same file as any input it reads
    # FROM. Checked before any adapter is constructed, same as every other pre-flight
    # check above.
    try:
        _validate_no_path_collisions(
            outputs={"output": args.output, "cost-output (derived)": cost_path,
                    "checkpoint-output (derived)": write_checkpoint_path},
            inputs={"requirement_set": args.requirement_set,
                   "answers-json": (args.answers_json if args.arm in ("b2", "b2-resume")
                                    and not args.interactive else None),
                   "checkpoint (resume input)": (args.checkpoint if args.arm == "b2-resume" else None)})
    except ValueError as e:
        print(f"Configuration error: {e}")
        return EXIT_CONFIG_ERROR

    # Adapter constructed only now -- every configuration/input/checkpoint/
    # provenance/destination/collision/answer-source check above has already passed.
    try:
        adapter = _ADAPTER_FACTORIES[candidate.provider]()
    except (OSError, ValueError, RuntimeError) as e:
        print(f"Configuration/input error: {e}")
        return EXIT_CONFIG_ERROR

    try:
        if args.arm == "b1":
            out = run_b1(adapter, candidate, requirement_set)
        elif args.arm == "b2":
            checkpoint_path = write_checkpoint_path
            checkpoint_fn: CheckpointFn = (
                lambda partial: atomic_write_json(checkpoint_path, partial.model_dump(mode="json")))
            out = run_b2(adapter, candidate, requirement_set, answer_fn, checkpoint_fn=checkpoint_fn)
        else:  # b2-resume
            out = run_b2_resume(adapter, candidate, requirement_set, checkpoint, answer_fn)
    except KeyboardInterrupt:
        # Handled here, not inside run_b2/_b2_answer_and_generate: an interrupt during
        # answer_fn (real Ctrl+C, not EOF -- EOFError is caught and turned into a
        # graceful failed BaselineRunOutput already, see _b2_answer_and_generate) is
        # NOT an Exception subclass, so it is never swallowed mid-run -- it propagates
        # here. Any checkpoint that had already been written (before answer_fn started)
        # is still on disk, untouched; this just avoids a raw traceback and keeps the
        # same "1 = failed/incomplete" exit-code convention every other failure path
        # in this CLI already uses.
        print("\nInterrupted. If a checkpoint was written (B2 only, after the questions "
             "call succeeded), resume with:\n  python -m evaluation.runner b2-resume "
             f"{args.requirement_set} --checkpoint <output>.checkpoint.json --output <output>")
        return EXIT_INTERRUPTED

    atomic_write_json(args.output, out.model_dump(mode="json"))

    cost = compute_cost(out)
    atomic_write_json(cost_path, cost.model_dump(mode="json"))

    print(f"{out.arm}: {'FAILED' if out.failed else f'{len(out.test_cases)} test case(s)'} "
         f"in {out.total_wall_clock_seconds:.1f}s (~${cost.total_cost_usd:.4f}). "
         f"Written to {args.output} (cost: {cost_path})")
    return EXIT_SUCCESS if not out.failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
