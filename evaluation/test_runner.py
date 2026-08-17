"""
Regression tests for evaluation/runner.py. Run after any change:

    python -m evaluation.test_runner

Plain script, no pytest -- same convention as design/test_schemas.py/
orchestrator/test_stages.py. Zero live network calls: a fake ProviderAdapter
stand-in returns scripted CompletionResults or raises scripted exceptions, so this
never burns real API quota and never depends on network access, matching
orchestrator/test_stages.py's own FakeAdapter pattern exactly.
"""

from __future__ import annotations

import json

from design.schemas import Requirement, RequirementSet
from evaluation.runner import (
    BaselineCallConfig, _positional_answers_from_file, _requirement_set_hash,
    _validate_no_path_collisions, _validate_output_destinations, run_b1, run_b2,
    run_b2_resume,
)
from evaluation.schemas import (
    BaselineAttempt, BaselineRunOutput, ClarificationAnswer, ClarificationQuestion,
)
from orchestrator.providers.base import CompletionResult
from orchestrator.stage_fns import StageCallFailed, StageCallFatal

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


class FakeAdapter:
    """Same shape as orchestrator/test_stages.py's FakeAdapter -- .complete() never
    touches the network. Each scripted response is popped in order; an Exception
    instance is raised instead of returned."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, prompt, *, model, temperature, timeout_seconds,
                output_mode="text", response_schema=None, schema_name=None):
        self.calls.append({"prompt": prompt, "model": model, "temperature": temperature})
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def completion(obj: dict, prompt_tokens: int = 100, completion_tokens: int = 50) -> CompletionResult:
    from design.schemas import OutputMode
    return CompletionResult(text=json.dumps(obj), prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens, output_mode=OutputMode.TEXT)


def malformed_completion() -> CompletionResult:
    from design.schemas import OutputMode
    return CompletionResult(text="not json at all", prompt_tokens=10, completion_tokens=5,
                            output_mode=OutputMode.TEXT)


REQ_SET = RequirementSet(doc_id="DOC-1", requirements=[
    Requirement(id="REQ-1", text="The system shall respond within 2 seconds."),
    Requirement(id="REQ-2", text="The system shall log every failed login attempt."),
])
CONFIG = BaselineCallConfig(provider="gemini", model="fake-model", temperature=1.0)
REQ_HASH = _requirement_set_hash(REQ_SET)
NO_SLEEP = lambda s: None  # noqa: E731 -- deterministic, no real sleeping in tests

CASE = {"id": "TC-1", "requirement_ids": ["REQ-1"], "technique_used": "boundary_value_analysis",
       "title": "t", "steps": ["s"], "expected_result": "e"}


# ---------------------------------------------------------------------------------
# B1
# ---------------------------------------------------------------------------------

section("B1 -- happy path")
fake = FakeAdapter([completion({"test_cases": [CASE]}, prompt_tokens=123, completion_tokens=45)])
out = run_b1(fake, CONFIG, REQ_SET)
ok("B1 happy path succeeds", out.failed is False)
ok("B1 makes exactly one call", len(fake.calls) == 1)
ok("B1 records exactly one attempt", len(out.attempts) == 1)
ok("B1 carries no questions/answers", out.questions == [] and out.answers == [])
ok("B1 records the requested model/temperature", out.model == "fake-model" and out.temperature == 1.0)
ok("B1 records the prompt hash for its one prompt file", set(out.prompt_hash) == {"b1_generate"})
ok("B1 records the real token counts from the fake completion",
   out.attempts[0].prompt_tokens == 123 and out.attempts[0].completion_tokens == 45)
ok("B1 records a non-negative wall-clock", out.total_wall_clock_seconds >= 0.0)
ok("B1 does not leak IssueCategory/ELIGIBLE_TECHNIQUES/refinement-loop language into "
  "its own prompt",
  all(term not in fake.calls[0]["prompt"] for term in
      ("IssueCategory", "ELIGIBLE_TECHNIQUES", "refine", "quality check", "revision cap")))

section("B1 -- malformed model output")
fake = FakeAdapter([malformed_completion(), malformed_completion(), malformed_completion()])
out = run_b1(fake, CONFIG, REQ_SET, max_attempts=3, sleep_fn=NO_SLEEP)
ok("malformed output fails after exhausting retries", out.failed is True)
ok("malformed output makes exactly max_attempts calls", len(fake.calls) == 3)
ok("failed run has no test cases", out.test_cases == [])
ok("every attempt is recorded as 'partial'", all(a.result == "partial" for a in out.attempts))
ok("every partial attempt still records its error message",
   all(a.error_message for a in out.attempts))

section("B1 -- wrong-shaped model output (schema-invalid, not just malformed JSON)")
bad_case = dict(CASE)
del bad_case["technique_used"]  # required field missing
fake = FakeAdapter([completion({"test_cases": [bad_case]})] * 3)
out = run_b1(fake, CONFIG, REQ_SET, max_attempts=3, sleep_fn=NO_SLEEP)
ok("schema-invalid case fails after exhausting retries", out.failed is True)
ok("every failed attempt is recorded, none marked success",
   all(a.result != "success" for a in out.attempts))

section("B1 -- a case naming an id absent from the requirement set still PARSES "
       "(schema has no cross-set closure check -- that is A1's job, tested separately "
       "in test_mechanical_checks.py)")
foreign_case = dict(CASE)
foreign_case["requirement_ids"] = ["REQ-DOES-NOT-EXIST"]
fake = FakeAdapter([completion({"test_cases": [foreign_case]})])
out = run_b1(fake, CONFIG, REQ_SET)
ok("a foreign requirement id does not fail the runner itself", out.failed is False)
ok("the foreign id is preserved verbatim for A1 to catch downstream",
   out.test_cases[0].requirement_ids == ["REQ-DOES-NOT-EXIST"])

section("B1 -- transient transport failure retries, then succeeds")
fake = FakeAdapter([StageCallFailed("429 rate limited"), completion({"test_cases": [CASE]})])
out = run_b1(fake, CONFIG, REQ_SET, max_attempts=3, sleep_fn=NO_SLEEP)
ok("a transient failure followed by success still succeeds overall", out.failed is False)
ok("both attempts are recorded, the first failed and the second succeeded",
   [a.result for a in out.attempts] == ["failed", "success"])

section("B1 -- fatal failure short-circuits immediately, no retry")
fake = FakeAdapter([StageCallFatal("bad credentials"), completion({"test_cases": [CASE]})])
out = run_b1(fake, CONFIG, REQ_SET, max_attempts=3, sleep_fn=NO_SLEEP)
ok("fatal failure fails the run", out.failed is True)
ok("fatal failure makes exactly one call, no retry", len(fake.calls) == 1)
ok("only one attempt is recorded", len(out.attempts) == 1 and out.attempts[0].result == "fatal")


# ---------------------------------------------------------------------------------
# B2
# ---------------------------------------------------------------------------------

section("B2 -- happy path: questions, one round of answers, generate")
fake = FakeAdapter([
    completion({"questions": [{"id": "Q1", "question_text": "which timeout?"}]},
              prompt_tokens=10, completion_tokens=20),
    completion({"test_cases": [CASE]}, prompt_tokens=200, completion_tokens=80),
])
out = run_b2(fake, CONFIG, REQ_SET,
            answer_fn=lambda qs: [ClarificationAnswer(question_id=q.id, answer_text="2s") for q in qs])
ok("B2 happy path succeeds", out.failed is False)
ok("B2 makes exactly two calls total", len(fake.calls) == 2)
ok("B2 records one question and one answer", len(out.questions) == 1 and len(out.answers) == 1)
ok("B2's generate prompt embeds the question and the answer",
  "which timeout?" in fake.calls[1]["prompt"] and "2s" in fake.calls[1]["prompt"])
ok("B2 records prompt hashes for both prompt files",
  set(out.prompt_hash) == {"b2_questions", "b2_generate"})
ok("B2 records per-call token usage separately",
  (out.attempts[0].prompt_tokens, out.attempts[0].completion_tokens) == (10, 20) and
  (out.attempts[1].prompt_tokens, out.attempts[1].completion_tokens) == (200, 80))

section("B2 -- one-round limit: exactly two calls ever appear in .calls, no matter what")
ok("attempt call labels are exactly ['b2_questions', 'b2_generate'], in order",
  [a.call for a in out.attempts] == ["b2_questions", "b2_generate"])

section("B2 -- zero questions is a legitimate outcome; answer_fn is never invoked")
called = []
fake = FakeAdapter([completion({"questions": []}), completion({"test_cases": [CASE]})])
out = run_b2(fake, CONFIG, REQ_SET, answer_fn=lambda qs: called.append(qs) or [])
ok("zero-question B2 run still succeeds", out.failed is False)
ok("answer_fn was never called", called == [])
ok("questions/answers are both empty, not fabricated", out.questions == [] and out.answers == [])

section("B2 -- (2026-08-17, finding 6) answer_fn leaving a question unanswered is "
       "caught and returned as a well-formed failed run, NOT a raised exception that "
       "would discard the successful questions call's data")
fake = FakeAdapter([completion({"questions": [{"id": "Q1", "question_text": "?"}]},
                               prompt_tokens=15, completion_tokens=25)])
out = run_b2(fake, CONFIG, REQ_SET, answer_fn=lambda qs: [])
ok("the run is marked failed, not an uncaught exception", out.failed is True)
ok("failure_reason explains the answer-source failure",
  out.failure_reason is not None and "answer source failed" in out.failure_reason)
ok("the successful questions call's tokens are NOT lost",
  out.attempts[0].result == "success" and out.attempts[0].prompt_tokens == 15
  and out.attempts[0].completion_tokens == 25)
ok("the question itself is preserved on the output", len(out.questions) == 1
  and out.questions[0].id == "Q1")
ok("no generate call was made after the missing answer", len(fake.calls) == 1)

section("B2 -- (2026-08-17, finding 6) answer_fn answering an unknown question id is "
       "caught the same way -- graceful, not a lost-data crash")
fake = FakeAdapter([completion({"questions": [{"id": "Q1", "question_text": "?"}]})])
out = run_b2(fake, CONFIG, REQ_SET,
            answer_fn=lambda qs: [ClarificationAnswer(question_id="Q-WRONG", answer_text="x")])
ok("the run is marked failed, not an uncaught exception", out.failed is True)
ok("failure_reason is set", out.failure_reason is not None)
ok("the question is still preserved", len(out.questions) == 1)
ok("no generate call was made", len(fake.calls) == 1)

section("B2 -- (2026-08-17, finding 6) answer_fn raising EOFError (interactive stdin "
       "closed / interrupted) is caught the same way as any other answer-source failure")


def _eof_answer_fn(qs):
    raise EOFError("stdin closed")


fake = FakeAdapter([completion({"questions": [{"id": "Q1", "question_text": "?"}]},
                               prompt_tokens=7, completion_tokens=9)])
out = run_b2(fake, CONFIG, REQ_SET, answer_fn=_eof_answer_fn)
ok("EOFError from the answer source does not propagate out of run_b2", out.failed is True)
ok("failure_reason names EOFError specifically", "EOFError" in (out.failure_reason or ""))
ok("the questions call's tokens survive the EOF", out.attempts[0].prompt_tokens == 7
  and out.attempts[0].completion_tokens == 9)
ok("questions list is preserved despite the interruption", len(out.questions) == 1)
ok("still no generate call was made -- interruption never routes to a second round or "
  "a retry of the questions call", len(fake.calls) == 1)

section("B2 -- answer_fn returning a hidden second question is not a route back to a "
       "second round -- the runner has no code path that would call the questions "
       "prompt again")
fake = FakeAdapter([
    completion({"questions": [{"id": "Q1", "question_text": "which timeout?"}]}),
    completion({"test_cases": [CASE]}),
])
out = run_b2(fake, CONFIG, REQ_SET,
            answer_fn=lambda qs: [ClarificationAnswer(question_id=q.id, answer_text="ignored, "
                                                       "trying to smuggle a follow-up question here")
                                  for q in qs])
ok("even an answer_fn that tries to smuggle more content back only ever produces "
  "exactly two calls", len(fake.calls) == 2)
ok("attempt log still shows exactly the two allowed call labels",
  [a.call for a in out.attempts] == ["b2_questions", "b2_generate"])

section("B2 -- questions call fails fatally -- no answer_fn call, no generate call")
fake = FakeAdapter([StageCallFatal("bad credentials")])
called2 = []
out = run_b2(fake, CONFIG, REQ_SET, answer_fn=lambda qs: called2.append(qs) or [])
ok("fatal questions-call failure is recorded as failed", out.failed is True)
ok("fatal failure makes no further calls", len(fake.calls) == 1)
ok("answer_fn is never called on a fatal questions failure", called2 == [])
ok("prompt_hash only records the questions prompt (generate was never rendered)",
  set(out.prompt_hash) == {"b2_questions"})

section("B2 -- generate call fails after a successful questions round: Q&A is still "
       "recorded even though the overall run is failed (honest partial provenance)")
fake = FakeAdapter([
    completion({"questions": [{"id": "Q1", "question_text": "which timeout?"}]}),
    malformed_completion(), malformed_completion(), malformed_completion(),
])
out = run_b2(fake, CONFIG, REQ_SET, max_attempts=3, sleep_fn=NO_SLEEP,
            answer_fn=lambda qs: [ClarificationAnswer(question_id=q.id, answer_text="2s") for q in qs])
ok("run is marked failed", out.failed is True)
ok("test_cases is empty on a failed run", out.test_cases == [])
ok("the Q&A round that DID succeed is still on record", len(out.questions) == 1 and len(out.answers) == 1)
ok("four calls total: one questions + three generate retries", len(fake.calls) == 4)


# ---------------------------------------------------------------------------------
# B2 -- checkpoint_fn (2026-08-17, finding 6): the successful questions call is
# persisted BEFORE answer_fn is invoked, so a crash mid-answering doesn't lose it.
# ---------------------------------------------------------------------------------

section("B2 -- checkpoint_fn is called exactly once, after the questions call "
       "succeeds and BEFORE answer_fn runs")
checkpoints = []
call_order = []


def _recording_checkpoint(partial_output):
    checkpoints.append(partial_output)
    call_order.append("checkpoint")


def _recording_answer_fn(qs):
    call_order.append("answer_fn")
    return [ClarificationAnswer(question_id=q.id, answer_text="2s") for q in qs]


fake = FakeAdapter([
    completion({"questions": [{"id": "Q1", "question_text": "which timeout?"}]},
              prompt_tokens=11, completion_tokens=22),
    completion({"test_cases": [CASE]}),
])
out = run_b2(fake, CONFIG, REQ_SET, answer_fn=_recording_answer_fn,
            checkpoint_fn=_recording_checkpoint)
ok("checkpoint_fn was called exactly once", len(checkpoints) == 1)
ok("checkpoint happened strictly before answer_fn", call_order == ["checkpoint", "answer_fn"])
ok("the checkpointed partial output carries the successful questions call's tokens",
  checkpoints[0].attempts[0].prompt_tokens == 11 and checkpoints[0].attempts[0].completion_tokens == 22)
ok("the checkpointed partial output carries the question itself",
  len(checkpoints[0].questions) == 1 and checkpoints[0].questions[0].id == "Q1")
ok("the checkpoint is marked failed=True (it is a checkpoint, not a final outcome)",
  checkpoints[0].failed is True)
ok("the final (non-checkpoint) output still succeeds normally", out.failed is False)

section("B2 -- checkpoint_fn is never called when the questions call itself fails "
       "(nothing successful yet to checkpoint)")
checkpoints2 = []
fake = FakeAdapter([StageCallFatal("bad credentials")])
out = run_b2(fake, CONFIG, REQ_SET, answer_fn=lambda qs: [],
            checkpoint_fn=lambda p: checkpoints2.append(p))
ok("checkpoint_fn is never invoked when the questions call fails", checkpoints2 == [])
ok("the run is still recorded as failed", out.failed is True)

section("B2 -- checkpoint_fn is optional; omitting it changes nothing about the run")
fake = FakeAdapter([
    completion({"questions": [{"id": "Q1", "question_text": "which timeout?"}]}),
    completion({"test_cases": [CASE]}),
])
out = run_b2(fake, CONFIG, REQ_SET,
            answer_fn=lambda qs: [ClarificationAnswer(question_id=q.id, answer_text="2s") for q in qs])
ok("run_b2 works with no checkpoint_fn at all (default None)", out.failed is False)


# ---------------------------------------------------------------------------------
# run_b2_resume (2026-08-17, second-round finding 5): resumes from a checkpoint,
# never re-calls the questions prompt.
# ---------------------------------------------------------------------------------

section("run_b2_resume -- continues from a real checkpoint straight to answer+generate")
fake = FakeAdapter([
    completion({"questions": [{"id": "Q1", "question_text": "which timeout?"}]},
              prompt_tokens=11, completion_tokens=22),
])
checkpoints3 = []
out_before_interrupt = run_b2(
    fake, CONFIG, REQ_SET, answer_fn=lambda qs: (_ for _ in ()).throw(EOFError("simulated interrupt")),
    checkpoint_fn=lambda p: checkpoints3.append(p))
ok("setup: the simulated run failed at the answer step, questions call succeeded",
  out_before_interrupt.failed is True and len(checkpoints3) == 1)
checkpoint = checkpoints3[0]
ok("setup: checkpoint carries the one question and its successful attempt",
  len(checkpoint.questions) == 1 and checkpoint.attempts[0].result == "success")

fake_resume = FakeAdapter([completion({"test_cases": [CASE]}, prompt_tokens=200, completion_tokens=80)])
resumed = run_b2_resume(
    fake_resume, CONFIG, REQ_SET, checkpoint,
    answer_fn=lambda qs: [ClarificationAnswer(question_id=q.id, answer_text="2s") for q in qs])
ok("resumed run succeeds", resumed.failed is False)
ok("resume makes EXACTLY ONE call -- the generate call, never the questions call",
  len(fake_resume.calls) == 1)
ok("the one call made is the GENERATE prompt, not the questions prompt (distinguished "
  "by content unique to each template)",
  "one round of clarifying questions" not in fake_resume.calls[0]["prompt"]
  and "which timeout?" in fake_resume.calls[0]["prompt"])
ok("resumed output still carries the original question and its original attempt "
  "(not re-fetched)", len(resumed.questions) == 1 and resumed.questions[0].id == "Q1")
ok("resumed output's attempts include the ORIGINAL questions-call attempt plus the "
  "new generate attempt", [a.call for a in resumed.attempts] == ["b2_questions", "b2_generate"])
ok("resumed output's total wall-clock includes the checkpoint's prior elapsed time, "
  "not just the resume session's own", resumed.total_wall_clock_seconds >=
  checkpoint.total_wall_clock_seconds)
ok("resumed output carries the config's full effective shape (finding 7)",
  resumed.timeout_seconds == CONFIG.timeout_seconds and resumed.output_mode == CONFIG.output_mode)
ok("(2026-08-17, third-round finding 1) the checkpoint that fed this resume was "
  "structurally marked as the awaiting-answers state",
  checkpoint.checkpoint_phase == "awaiting_answers")
ok("(third-round finding 2) the checkpoint and the resumed output share the same "
  "RequirementSet content hash", checkpoint.requirement_set_hash == REQ_HASH
  and resumed.requirement_set_hash == REQ_HASH)

section("run_b2_resume -- rejects a non-B2 checkpoint before doing anything")
bad_checkpoint = BaselineRunOutput(
    arm="B1", provider="gemini", model="fake-model", temperature=1.0,
    requirement_set_hash=REQ_HASH,
    timeout_seconds=30.0, output_mode=CONFIG.output_mode,
    prompt_hash={"b1_generate": "x"}, test_cases=[CASE], total_wall_clock_seconds=1.0)
spy_fake = FakeAdapter([])  # empty responses -- .complete() would IndexError if ever called
try:
    run_b2_resume(spy_fake, CONFIG, REQ_SET, bad_checkpoint, answer_fn=lambda qs: [])
    ok("resuming a B1 checkpoint as B2 raises", False)
except ValueError:
    ok("resuming a B1 checkpoint as B2 raises", True)
    ok("the rejection happened before any adapter call (an empty-response FakeAdapter "
      "would IndexError, not ValueError, if .complete() were ever reached)",
      spy_fake.calls == [])


def _awaiting_answers_checkpoint(**overrides) -> BaselineRunOutput:
    """A genuinely valid awaiting-answers checkpoint, for tests to mutate one field at
    a time into an invalid one."""
    fields = dict(
        arm="B2", provider="gemini", model="fake-model", temperature=1.0,
        requirement_set_hash=REQ_HASH, timeout_seconds=30.0, output_mode=CONFIG.output_mode,
        prompt_hash={"b2_questions": "x"}, questions=[],
        attempts=[BaselineAttempt(call="b2_questions", attempt_number=1, result="success",
                                  prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1)],
        total_wall_clock_seconds=0.5, failed=True,
        failure_reason="checkpoint before requesting operator answers -- not a final outcome",
        checkpoint_phase="awaiting_answers")
    fields.update(overrides)
    return BaselineRunOutput(**fields)


section("run_b2_resume -- resuming with zero questions (a legitimate B2 outcome) "
       "still makes no questions call and goes straight to generate")
zero_q_checkpoint = _awaiting_answers_checkpoint()
called3 = []
fake_zero = FakeAdapter([completion({"test_cases": [CASE]})])
resumed_zero = run_b2_resume(fake_zero, CONFIG, REQ_SET, zero_q_checkpoint,
                             answer_fn=lambda qs: called3.append(qs) or [])
ok("answer_fn is never called when the checkpoint recorded zero questions",
  called3 == [])
ok("still exactly one call (generate only)", len(fake_zero.calls) == 1)
ok("resumed successfully", resumed_zero.failed is False)

section("(2026-08-17, fourth-round finding 1) run_b2_resume called DIRECTLY (not "
       "through main()) rejects a checkpoint whose requirement_set_hash does not "
       "match the requirement_set it was given -- the CLI checks this, but a direct "
       "caller bypasses the CLI entirely and could otherwise combine an old "
       "checkpoint's questions with changed requirement text")
changed_req_set = RequirementSet(doc_id="DOC-1", requirements=[
    Requirement(id="REQ-1", text="The system shall respond within 2 seconds, CHANGED."),
    Requirement(id="REQ-2", text="The system shall log every failed login attempt."),
])
mismatched_content_checkpoint = _awaiting_answers_checkpoint()  # hash matches REQ_SET, not changed_req_set
spy_content = FakeAdapter([])  # empty -- .complete() would IndexError if ever reached
try:
    run_b2_resume(spy_content, CONFIG, changed_req_set, mismatched_content_checkpoint,
                  answer_fn=lambda qs: [])
    ok("direct call with mismatched requirement_set_hash raises", False)
except ValueError as e:
    ok("direct call with mismatched requirement_set_hash raises", True)
    ok("error names requirement_set_hash", "requirement_set_hash" in str(e))
    ok("no adapter call was made before the rejection", spy_content.calls == [])

section("(2026-08-17, fourth-round finding 1) run_b2_resume called DIRECTLY rejects "
       "a checkpoint whose config does not match the given config -- same bypass "
       "concern as above, for provider/model/temperature/timeout_seconds/output_mode")
mismatched_config_direct_checkpoint = _awaiting_answers_checkpoint(model="a-different-model")
different_config = BaselineCallConfig(provider="gemini", model="fake-model", temperature=1.0)
spy_config = FakeAdapter([])
try:
    run_b2_resume(spy_config, different_config, REQ_SET, mismatched_config_direct_checkpoint,
                  answer_fn=lambda qs: [])
    ok("direct call with mismatched config raises", False)
except ValueError as e:
    ok("direct call with mismatched config raises", True)
    ok("no adapter call was made before the rejection", spy_config.calls == [])

section("(2026-08-17, fourth-round finding 1) run_b2_resume called DIRECTLY still "
       "succeeds when requirement_set and config genuinely match (the fix does not "
       "over-reject the legitimate case)")
matching_checkpoint = _awaiting_answers_checkpoint()
fake_matching = FakeAdapter([completion({"test_cases": [CASE]})])
matching_result = run_b2_resume(fake_matching, CONFIG, REQ_SET, matching_checkpoint,
                                answer_fn=lambda qs: [])
ok("a genuinely matching direct call still resumes successfully", matching_result.failed is False)

section("(2026-08-17, third-round finding 1) run_b2_resume rejects a checkpoint "
       "whose checkpoint_phase is None (e.g. a COMPLETED B2 output) even though its "
       "arm is correct -- this is the exact gap the finding reported: a final or "
       "failed B2 output could otherwise be resumed and generate again")
completed_output = BaselineRunOutput(
    arm="B2", provider="gemini", model="fake-model", temperature=1.0,
    requirement_set_hash=REQ_HASH, timeout_seconds=30.0, output_mode=CONFIG.output_mode,
    prompt_hash={"b2_questions": "x", "b2_generate": "y"},
    attempts=[
        BaselineAttempt(call="b2_questions", attempt_number=1, result="success",
                        prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1),
        BaselineAttempt(call="b2_generate", attempt_number=1, result="success",
                        prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1),
    ],
    questions=[ClarificationQuestion(id="Q1", question_text="which timeout?")],
    answers=[ClarificationAnswer(question_id="Q1", answer_text="2s")],
    test_cases=[CASE], total_wall_clock_seconds=1.0, failed=False)
spy_completed = FakeAdapter([completion({"test_cases": [CASE]})])
try:
    run_b2_resume(spy_completed, CONFIG, REQ_SET, completed_output, answer_fn=lambda qs: [])
    ok("resuming a COMPLETED B2 output raises", False)
except ValueError as e:
    ok("resuming a COMPLETED B2 output raises", True)
    ok("no adapter call was made", spy_completed.calls == [])
    ok("error names checkpoint_phase", "checkpoint_phase" in str(e))

section("(2026-08-17, third-round finding 1) BaselineRunOutput itself refuses to "
       "construct/load an 'awaiting_answers' object that violates any required "
       "structural property -- checked at the schema layer, not just by run_b2_resume")
try:
    _awaiting_answers_checkpoint(failed=False)
    ok("failed=False with checkpoint_phase='awaiting_answers' is rejected", False)
except Exception:
    ok("failed=False with checkpoint_phase='awaiting_answers' is rejected", True)

try:
    _awaiting_answers_checkpoint(
        attempts=[BaselineAttempt(call="b2_questions", attempt_number=1, result="success",
                                  prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1),
                 BaselineAttempt(call="b2_generate", attempt_number=1, result="success",
                                 prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1)],
        prompt_hash={"b2_questions": "x", "b2_generate": "y"})
    ok("a b2_generate attempt in an 'awaiting_answers' checkpoint is rejected", False)
except Exception:
    ok("a b2_generate attempt in an 'awaiting_answers' checkpoint is rejected", True)

# Isolated from the prompt_hash check above: ONLY the attempts list names a
# b2_generate call, prompt_hash still says only "b2_questions" -- proves the
# attempts-call-type check does real work on its own, not just redundantly with the
# prompt_hash check (found by mutation-testing this guarantee: mutating away the
# attempts-call-type check alone left this exact shape silently accepted).
try:
    _awaiting_answers_checkpoint(
        attempts=[BaselineAttempt(call="b2_generate", attempt_number=1, result="success",
                                  prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1)])
    ok("a b2_generate-only attempts list is rejected even with prompt_hash unchanged "
      "(isolates the attempts-call-type check from the prompt_hash check)", False)
except Exception:
    ok("a b2_generate-only attempts list is rejected even with prompt_hash unchanged "
      "(isolates the attempts-call-type check from the prompt_hash check)", True)

try:
    _awaiting_answers_checkpoint(answers=[ClarificationAnswer(question_id="Q1", answer_text="x")])
    ok("answers already present in an 'awaiting_answers' checkpoint is rejected", False)
except Exception:
    ok("answers already present in an 'awaiting_answers' checkpoint is rejected", True)

try:
    _awaiting_answers_checkpoint(
        attempts=[BaselineAttempt(call="b2_questions", attempt_number=1, result="failed",
                                  error_message="429", wall_clock_seconds=0.1),
                 BaselineAttempt(call="b2_questions", attempt_number=2, result="success",
                                 prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1)])
    ok("a valid retry-then-success sequence (failed, then success) IS accepted", True)
except Exception:
    ok("a valid retry-then-success sequence (failed, then success) IS accepted", False)

try:
    _awaiting_answers_checkpoint(
        attempts=[BaselineAttempt(call="b2_questions", attempt_number=1, result="success",
                                  prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1),
                 BaselineAttempt(call="b2_questions", attempt_number=2, result="success",
                                 prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1)])
    ok("two successes (success before the final one too) is rejected", False)
except Exception:
    ok("two successes (success before the final one too) is rejected", True)

try:
    _awaiting_answers_checkpoint(attempts=[])
    ok("zero attempts in an 'awaiting_answers' checkpoint is rejected", False)
except Exception:
    ok("zero attempts in an 'awaiting_answers' checkpoint is rejected", True)

# (2026-08-17, fourth-round finding 2) A "fatal" attempt followed by "success" is a
# shape _call_once can never actually produce -- StageCallFatal short-circuits
# immediately, so a fatal attempt is always the ONLY attempt, never followed by a
# retry. The validator must reject it anyway (a hand-edited/corrupted checkpoint file
# could still contain it), not just accept anything that isn't literally "success"
# before the last entry.
try:
    _awaiting_answers_checkpoint(
        attempts=[BaselineAttempt(call="b2_questions", attempt_number=1, result="fatal",
                                  error_message="bad credentials", wall_clock_seconds=0.1),
                 BaselineAttempt(call="b2_questions", attempt_number=2, result="success",
                                 prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1)])
    ok("a 'fatal' attempt followed by 'success' is rejected -- _call_once cannot "
      "actually produce this sequence", False)
except Exception:
    ok("a 'fatal' attempt followed by 'success' is rejected -- _call_once cannot "
      "actually produce this sequence", True)

# Sanity: "partial" (not just "failed") before the final success must still be
# accepted -- both are legitimate retry-triggering results.
try:
    _awaiting_answers_checkpoint(
        attempts=[BaselineAttempt(call="b2_questions", attempt_number=1, result="partial",
                                  error_message="schema mismatch", wall_clock_seconds=0.1),
                 BaselineAttempt(call="b2_questions", attempt_number=2, result="success",
                                 prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1)])
    ok("a valid retry-then-success sequence ('partial', then success) IS accepted", True)
except Exception:
    ok("a valid retry-then-success sequence ('partial', then success) IS accepted", False)


# ---------------------------------------------------------------------------------
# _validate_output_destinations (2026-08-17, second-round finding 6)
# ---------------------------------------------------------------------------------

section("_validate_output_destinations -- rejects a nonexistent parent directory")
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    good_path = Path(tmp) / "out.json"
    _validate_output_destinations(good_path)  # must not raise
    ok("an existing parent directory passes", True)

    bad_path = Path(tmp) / "does-not-exist" / "out.json"
    try:
        _validate_output_destinations(bad_path)
        ok("a nonexistent parent directory raises", False)
    except ValueError as e:
        ok("a nonexistent parent directory raises", True)
        ok("the error names the missing directory", str(bad_path.parent) in str(e))

    ok("multiple paths are all checked -- one bad path among good ones still raises",
      True)
    try:
        _validate_output_destinations(good_path, bad_path)
        ok("a bad path among several raises even if it is not first", False)
    except ValueError:
        ok("a bad path among several raises even if it is not first", True)


# ---------------------------------------------------------------------------------
# main() -- CLI-level: destination validation and checkpoint/config/input/prompt-
# provenance mismatches are all rejected BEFORE any adapter is constructed
# (2026-08-17, second-round findings 5 & 6), and KeyboardInterrupt is handled
# gracefully with the existing exit-code convention preserved.
# ---------------------------------------------------------------------------------

import builtins  # noqa: E402
import evaluation.runner as runner_module  # noqa: E402


class _SpyFactory:
    """Same shape as evaluation/test_config_parity.py's SpyAdapterFactory -- proves a
    rejection happened before any provider/adapter access."""
    def __init__(self):
        self.called = False

    def __call__(self):
        self.called = True
        raise AssertionError(
            "adapter factory was called -- a check that should have rejected first did not")


def _with_spy_factory(fn):
    """Runs fn(spy) with runner_module._ADAPTER_FACTORIES['gemini'] replaced by a spy,
    restoring the real factories afterward no matter what."""
    original = dict(runner_module._ADAPTER_FACTORIES)
    spy = _SpyFactory()
    runner_module._ADAPTER_FACTORIES["gemini"] = spy
    try:
        fn(spy)
    finally:
        runner_module._ADAPTER_FACTORIES.clear()
        runner_module._ADAPTER_FACTORIES.update(original)


section("main() -- output destination is validated before any adapter is constructed")
with tempfile.TemporaryDirectory() as tmp:
    req_path = Path(tmp) / "reqs.json"
    req_path.write_text(REQ_SET.model_dump_json())
    bad_output = Path(tmp) / "no-such-directory" / "out.json"

    def _check_bad_destination(spy):
        rc = runner_module.main(["b1", str(req_path), "--output", str(bad_output)])
        ok("main() returns EXIT_CONFIG_ERROR for a bad output destination",
          rc == runner_module.EXIT_CONFIG_ERROR)
        ok("the adapter factory was never called", spy.called is False)

    _with_spy_factory(_check_bad_destination)

section("_validate_no_path_collisions -- unit-level: an output resolving to the "
       "same file as an input is rejected")
with tempfile.TemporaryDirectory() as tmp:
    same_path = Path(tmp) / "shared.json"
    try:
        _validate_no_path_collisions(outputs={"output": same_path},
                                     inputs={"requirement_set": same_path})
        ok("output == input (same exact path object) raises", False)
    except ValueError as e:
        ok("output == input (same exact path object) raises", True)
        ok("error names both sides", "output" in str(e) and "requirement_set" in str(e))

    # Different STRINGS, same file -- relative vs absolute -- must still collide.
    abs_path = Path(tmp).resolve() / "same-file.json"
    rel_equivalent = Path(tmp) / "." / "same-file.json"
    try:
        _validate_no_path_collisions(outputs={"output": abs_path},
                                     inputs={"requirement_set": rel_equivalent})
        ok("differently-spelled paths resolving to the same file still collide", False)
    except ValueError:
        ok("differently-spelled paths resolving to the same file still collide", True)

    distinct_a = Path(tmp) / "a.json"
    distinct_b = Path(tmp) / "b.json"
    _validate_no_path_collisions(outputs={"output": distinct_a}, inputs={"requirement_set": distinct_b})
    ok("genuinely distinct paths do not raise", True)

    _validate_no_path_collisions(outputs={"output": distinct_a, "cost": None},
                                 inputs={"requirement_set": distinct_b, "answers_json": None})
    ok("None entries on either side (e.g. --answers-json not given) are skipped, not "
      "treated as a collision with each other", True)

section("(2026-08-17, third-round finding 5) main() -- rejects --output pointed at "
       "the SAME file as the input RequirementSet, before any adapter access")
with tempfile.TemporaryDirectory() as tmp:
    shared_path = Path(tmp) / "shared.json"
    shared_path.write_text(REQ_SET.model_dump_json())

    def _check_output_overwrites_requirement_set(spy):
        rc = runner_module.main(["b1", str(shared_path), "--output", str(shared_path)])
        ok("main() rejects --output colliding with the RequirementSet input",
          rc == runner_module.EXIT_CONFIG_ERROR)
        ok("adapter factory never called", spy.called is False)

    _with_spy_factory(_check_output_overwrites_requirement_set)

section("(2026-08-17, third-round finding 5) main() -- rejects --output pointed at "
       "the SAME file as the b2-resume --checkpoint input, before any adapter access")
with tempfile.TemporaryDirectory() as tmp:
    req_path = Path(tmp) / "reqs.json"
    req_path.write_text(REQ_SET.model_dump_json())
    shared_checkpoint_path = Path(tmp) / "shared.json"
    frozen_now = runner_module.frozen_arm_p_config()
    real_q_hash = runner_module._prompt_hash(runner_module._B2_QUESTIONS_PROMPT_PATH.read_text())
    valid_checkpoint = BaselineRunOutput(
        arm="B2", doc_id=REQ_SET.doc_id, requirement_set_hash=_requirement_set_hash(REQ_SET),
        provider=frozen_now.provider, model=frozen_now.model, temperature=frozen_now.temperature,
        timeout_seconds=frozen_now.timeout_seconds, output_mode=frozen_now.output_mode,
        prompt_hash={"b2_questions": real_q_hash},
        attempts=[BaselineAttempt(call="b2_questions", attempt_number=1, result="success",
                                  prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1)],
        total_wall_clock_seconds=1.0, failed=True,
        failure_reason="checkpoint before requesting operator answers -- not a final outcome",
        checkpoint_phase="awaiting_answers")
    shared_checkpoint_path.write_text(valid_checkpoint.model_dump_json())
    # Real, valid, EMPTY answers file -- must exist and parse (construction reads it
    # eagerly) so this test actually exercises the COLLISION check, not an incidental
    # "answers file missing" rejection that would happen first and mask it.
    real_answers_path = Path(tmp) / "answers.json"
    real_answers_path.write_text(json.dumps([]))

    def _check_output_overwrites_checkpoint(spy):
        rc = runner_module.main(
            ["b2-resume", str(req_path), "--checkpoint", str(shared_checkpoint_path),
             "--answers-json", str(real_answers_path),
             "--output", str(shared_checkpoint_path)])  # collides with --checkpoint
        ok("main() rejects --output colliding with the resume --checkpoint input",
          rc == runner_module.EXIT_CONFIG_ERROR)
        ok("adapter factory never called", spy.called is False)

    _with_spy_factory(_check_output_overwrites_checkpoint)

section("(2026-08-17, third-round finding 5) main() -- the SAME --output path across "
       "TWO SEPARATE invocations (initial b2 run, then b2-resume) is explicitly "
       "PRESERVED as intentional overwrite behavior, not a collision")
ok("this exact scenario is already exercised end-to-end below (the b2 -> b2-resume "
  "test using the same out_path for both calls) and passes -- see "
  "'end-to-end: a real checkpoint written by an interrupted b2 run'", True)

section("main() -- b2-resume rejects a checkpoint produced under a DIFFERENT "
       "configuration, before any adapter access")
with tempfile.TemporaryDirectory() as tmp:
    req_path = Path(tmp) / "reqs.json"
    req_path.write_text(REQ_SET.model_dump_json())
    checkpoint_path = Path(tmp) / "ckpt.json"
    out_path = Path(tmp) / "out.json"
    frozen_now = runner_module.frozen_arm_p_config()
    real_q_hash = runner_module._prompt_hash(runner_module._B2_QUESTIONS_PROMPT_PATH.read_text())
    mismatched_config_checkpoint = BaselineRunOutput(
        arm="B2", doc_id=REQ_SET.doc_id, requirement_set_hash=_requirement_set_hash(REQ_SET),
        provider=frozen_now.provider,
        model="a-totally-different-model",  # mismatch
        temperature=frozen_now.temperature, timeout_seconds=frozen_now.timeout_seconds,
        output_mode=frozen_now.output_mode,
        prompt_hash={"b2_questions": real_q_hash},
        attempts=[BaselineAttempt(call="b2_questions", attempt_number=1, result="success",
                                  prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1)],
        total_wall_clock_seconds=1.0, failed=True,
        failure_reason="checkpoint before requesting operator answers -- not a final outcome",
        checkpoint_phase="awaiting_answers")
    checkpoint_path.write_text(mismatched_config_checkpoint.model_dump_json())
    (Path(tmp) / "unused.json").write_text("[]")

    def _check_config_mismatch(spy):
        rc = runner_module.main(
            ["b2-resume", str(req_path), "--checkpoint", str(checkpoint_path),
             "--answers-json", str(Path(tmp) / "unused.json"), "--output", str(out_path)])
        ok("main() rejects a config-mismatched checkpoint", rc == runner_module.EXIT_CONFIG_ERROR)
        ok("adapter factory never called for a config-mismatched checkpoint", spy.called is False)

    _with_spy_factory(_check_config_mismatch)

section("main() -- b2-resume rejects a checkpoint for a DIFFERENT document (doc_id "
       "mismatch), before any adapter access")
with tempfile.TemporaryDirectory() as tmp:
    req_path = Path(tmp) / "reqs.json"
    req_path.write_text(REQ_SET.model_dump_json())
    checkpoint_path = Path(tmp) / "ckpt.json"
    out_path = Path(tmp) / "out.json"
    frozen_now = runner_module.frozen_arm_p_config()
    real_q_hash = runner_module._prompt_hash(runner_module._B2_QUESTIONS_PROMPT_PATH.read_text())
    mismatched_doc_checkpoint = BaselineRunOutput(
        arm="B2", doc_id="SOME-OTHER-DOCUMENT",  # mismatch vs REQ_SET.doc_id == "DOC-1"
        requirement_set_hash="irrelevant-for-this-test",
        provider=frozen_now.provider, model=frozen_now.model, temperature=frozen_now.temperature,
        timeout_seconds=frozen_now.timeout_seconds, output_mode=frozen_now.output_mode,
        prompt_hash={"b2_questions": real_q_hash},
        attempts=[BaselineAttempt(call="b2_questions", attempt_number=1, result="success",
                                  prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1)],
        total_wall_clock_seconds=1.0, failed=True,
        failure_reason="checkpoint before requesting operator answers -- not a final outcome",
        checkpoint_phase="awaiting_answers")
    checkpoint_path.write_text(mismatched_doc_checkpoint.model_dump_json())
    (Path(tmp) / "unused.json").write_text("[]")

    def _check_doc_mismatch(spy):
        rc = runner_module.main(
            ["b2-resume", str(req_path), "--checkpoint", str(checkpoint_path),
             "--answers-json", str(Path(tmp) / "unused.json"), "--output", str(out_path)])
        ok("main() rejects a doc_id-mismatched checkpoint", rc == runner_module.EXIT_CONFIG_ERROR)
        ok("adapter factory never called for a doc_id-mismatched checkpoint", spy.called is False)

    _with_spy_factory(_check_doc_mismatch)

section("(2026-08-17, third-round finding 2) main() -- b2-resume rejects a checkpoint "
       "whose requirement text CHANGED even though doc_id stayed the SAME -- doc_id "
       "matching alone does not prove content is unchanged")
with tempfile.TemporaryDirectory() as tmp:
    original_req_set = RequirementSet(doc_id="DOC-SAME", requirements=[
        Requirement(id="REQ-1", text="The system shall respond within 2 seconds.")])
    changed_req_set = RequirementSet(doc_id="DOC-SAME", requirements=[
        Requirement(id="REQ-1", text="The system shall respond within 10 seconds.")])
    req_path = Path(tmp) / "reqs.json"
    req_path.write_text(changed_req_set.model_dump_json())  # the CHANGED text is what's given now
    checkpoint_path = Path(tmp) / "ckpt.json"
    out_path = Path(tmp) / "out.json"
    frozen_now = runner_module.frozen_arm_p_config()
    real_q_hash = runner_module._prompt_hash(runner_module._B2_QUESTIONS_PROMPT_PATH.read_text())
    checkpoint_from_original = BaselineRunOutput(
        arm="B2", doc_id="DOC-SAME", requirement_set_hash=_requirement_set_hash(original_req_set),
        provider=frozen_now.provider, model=frozen_now.model, temperature=frozen_now.temperature,
        timeout_seconds=frozen_now.timeout_seconds, output_mode=frozen_now.output_mode,
        prompt_hash={"b2_questions": real_q_hash},
        attempts=[BaselineAttempt(call="b2_questions", attempt_number=1, result="success",
                                  prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1)],
        total_wall_clock_seconds=1.0, failed=True,
        failure_reason="checkpoint before requesting operator answers -- not a final outcome",
        checkpoint_phase="awaiting_answers")
    checkpoint_path.write_text(checkpoint_from_original.model_dump_json())
    (Path(tmp) / "unused.json").write_text("[]")

    def _check_content_mismatch_same_doc_id(spy):
        rc = runner_module.main(
            ["b2-resume", str(req_path), "--checkpoint", str(checkpoint_path),
             "--answers-json", str(Path(tmp) / "unused.json"), "--output", str(out_path)])
        ok("main() rejects a content-changed checkpoint even with a matching doc_id",
          rc == runner_module.EXIT_CONFIG_ERROR)
        ok("adapter factory never called", spy.called is False)

    _with_spy_factory(_check_content_mismatch_same_doc_id)

section("(2026-08-17, third-round finding 2) main() -- b2-resume rejects two "
       "DIFFERENT requirement sets that both have doc_id=None -- doc_id matching is "
       "meaningless when both sides are None")
with tempfile.TemporaryDirectory() as tmp:
    req_set_a = RequirementSet(doc_id=None, requirements=[Requirement(id="REQ-1", text="Text A.")])
    req_set_b = RequirementSet(doc_id=None, requirements=[Requirement(id="REQ-1", text="Text B, totally different.")])
    req_path = Path(tmp) / "reqs.json"
    req_path.write_text(req_set_b.model_dump_json())
    checkpoint_path = Path(tmp) / "ckpt.json"
    out_path = Path(tmp) / "out.json"
    frozen_now = runner_module.frozen_arm_p_config()
    real_q_hash = runner_module._prompt_hash(runner_module._B2_QUESTIONS_PROMPT_PATH.read_text())
    checkpoint_from_a = BaselineRunOutput(
        arm="B2", doc_id=None, requirement_set_hash=_requirement_set_hash(req_set_a),
        provider=frozen_now.provider, model=frozen_now.model, temperature=frozen_now.temperature,
        timeout_seconds=frozen_now.timeout_seconds, output_mode=frozen_now.output_mode,
        prompt_hash={"b2_questions": real_q_hash},
        attempts=[BaselineAttempt(call="b2_questions", attempt_number=1, result="success",
                                  prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1)],
        total_wall_clock_seconds=1.0, failed=True,
        failure_reason="checkpoint before requesting operator answers -- not a final outcome",
        checkpoint_phase="awaiting_answers")
    checkpoint_path.write_text(checkpoint_from_a.model_dump_json())
    (Path(tmp) / "unused.json").write_text("[]")

    def _check_content_mismatch_both_none(spy):
        rc = runner_module.main(
            ["b2-resume", str(req_path), "--checkpoint", str(checkpoint_path),
             "--answers-json", str(Path(tmp) / "unused.json"), "--output", str(out_path)])
        ok("main() rejects two different doc_id=None inputs -- doc_id alone would "
          "have let this through (None == None)", rc == runner_module.EXIT_CONFIG_ERROR)
        ok("adapter factory never called", spy.called is False)

    _with_spy_factory(_check_content_mismatch_both_none)

section("(2026-08-17, third-round finding 4) main() -- b2-resume rejects a checkpoint "
       "whose TIMEOUT alone differs -- the five-field config comparison must include "
       "timeout_seconds, not just provider/model/temperature/output_mode")
with tempfile.TemporaryDirectory() as tmp:
    req_path = Path(tmp) / "reqs.json"
    req_path.write_text(REQ_SET.model_dump_json())
    checkpoint_path = Path(tmp) / "ckpt.json"
    out_path = Path(tmp) / "out.json"
    frozen_now = runner_module.frozen_arm_p_config()
    real_q_hash = runner_module._prompt_hash(runner_module._B2_QUESTIONS_PROMPT_PATH.read_text())
    timeout_mismatched_checkpoint = BaselineRunOutput(
        arm="B2", doc_id=REQ_SET.doc_id, requirement_set_hash=_requirement_set_hash(REQ_SET),
        provider=frozen_now.provider, model=frozen_now.model, temperature=frozen_now.temperature,
        timeout_seconds=frozen_now.timeout_seconds + 100,  # ONLY this differs
        output_mode=frozen_now.output_mode,
        prompt_hash={"b2_questions": real_q_hash},
        attempts=[BaselineAttempt(call="b2_questions", attempt_number=1, result="success",
                                  prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1)],
        total_wall_clock_seconds=1.0, failed=True,
        failure_reason="checkpoint before requesting operator answers -- not a final outcome",
        checkpoint_phase="awaiting_answers")
    checkpoint_path.write_text(timeout_mismatched_checkpoint.model_dump_json())
    (Path(tmp) / "unused.json").write_text("[]")

    def _check_timeout_mismatch(spy):
        rc = runner_module.main(
            ["b2-resume", str(req_path), "--checkpoint", str(checkpoint_path),
             "--answers-json", str(Path(tmp) / "unused.json"), "--output", str(out_path)])
        ok("main() rejects a checkpoint whose timeout_seconds alone differs",
          rc == runner_module.EXIT_CONFIG_ERROR)
        ok("adapter factory never called for a timeout-only mismatch", spy.called is False)

    _with_spy_factory(_check_timeout_mismatch)

section("main() -- b2-resume rejects a checkpoint whose recorded questions-prompt "
       "hash does not match the CURRENT prompt file (prompt-provenance mismatch), "
       "before any adapter access")
with tempfile.TemporaryDirectory() as tmp:
    req_path = Path(tmp) / "reqs.json"
    req_path.write_text(REQ_SET.model_dump_json())
    checkpoint_path = Path(tmp) / "ckpt.json"
    out_path = Path(tmp) / "out.json"
    frozen_now = runner_module.frozen_arm_p_config()
    stale_prompt_checkpoint = BaselineRunOutput(
        arm="B2", doc_id=REQ_SET.doc_id, requirement_set_hash=_requirement_set_hash(REQ_SET),
        provider=frozen_now.provider, model=frozen_now.model,
        temperature=frozen_now.temperature, timeout_seconds=frozen_now.timeout_seconds,
        output_mode=frozen_now.output_mode,
        prompt_hash={"b2_questions": "this-is-not-the-real-hash"},
        attempts=[BaselineAttempt(call="b2_questions", attempt_number=1, result="success",
                                  prompt_tokens=5, completion_tokens=5, wall_clock_seconds=0.1)],
        total_wall_clock_seconds=1.0, failed=True,
        failure_reason="checkpoint before requesting operator answers -- not a final outcome",
        checkpoint_phase="awaiting_answers")
    checkpoint_path.write_text(stale_prompt_checkpoint.model_dump_json())
    (Path(tmp) / "unused.json").write_text("[]")

    def _check_prompt_mismatch(spy):
        rc = runner_module.main(
            ["b2-resume", str(req_path), "--checkpoint", str(checkpoint_path),
             "--answers-json", str(Path(tmp) / "unused.json"), "--output", str(out_path)])
        ok("main() rejects a checkpoint with a stale questions-prompt hash",
          rc == runner_module.EXIT_CONFIG_ERROR)
        ok("adapter factory never called for a prompt-provenance mismatch", spy.called is False)

    _with_spy_factory(_check_prompt_mismatch)

section("main() -- KeyboardInterrupt during answering is caught, preserves the "
       "existing exit-code convention (1 == failed/incomplete), and does not crash "
       "with a raw traceback -- the checkpoint written before the interrupt survives")
with tempfile.TemporaryDirectory() as tmp:
    req_path = Path(tmp) / "reqs.json"
    req_path.write_text(REQ_SET.model_dump_json())
    out_path = Path(tmp) / "out.json"
    checkpoint_path = out_path.with_name(out_path.stem + ".checkpoint.json")

    def _interrupting_input(prompt=""):
        raise KeyboardInterrupt()

    fake_for_main = FakeAdapter([
        completion({"questions": [{"id": "Q1", "question_text": "which timeout?"}]}),
    ])
    original_factories = dict(runner_module._ADAPTER_FACTORIES)
    original_input = builtins.input
    runner_module._ADAPTER_FACTORIES["gemini"] = lambda: fake_for_main
    builtins.input = _interrupting_input
    try:
        rc = runner_module.main(["b2", str(req_path), "--interactive", "--output", str(out_path)])
        ok("main() returns exit code 1 on KeyboardInterrupt, same as any other failed run",
          rc == 1)
        ok("a checkpoint file was written before the interrupt (the questions call "
          "had already succeeded)", checkpoint_path.exists())
        ok("the final run-output file was NOT written -- the run never completed",
          not out_path.exists())
    finally:
        builtins.input = original_input
        runner_module._ADAPTER_FACTORIES.clear()
        runner_module._ADAPTER_FACTORIES.update(original_factories)

section("main() -- end-to-end: a real checkpoint written by an interrupted b2 run "
       "(EOFError on the interactive answer source) is then successfully resumed via "
       "b2-resume, writing a final atomic output")
with tempfile.TemporaryDirectory() as tmp:
    req_path = Path(tmp) / "reqs.json"
    req_path.write_text(REQ_SET.model_dump_json())
    out_path = Path(tmp) / "out.json"
    checkpoint_path = out_path.with_name(out_path.stem + ".checkpoint.json")
    answers_path = Path(tmp) / "answers.json"
    answers_path.write_text(json.dumps(["2 seconds"]))

    def _eof_input(prompt=""):
        raise EOFError("stdin closed")

    interrupted_adapter = FakeAdapter([
        completion({"questions": [{"id": "Q1", "question_text": "which timeout?"}]}),
    ])
    original_factories = dict(runner_module._ADAPTER_FACTORIES)
    original_input = builtins.input
    runner_module._ADAPTER_FACTORIES["gemini"] = lambda: interrupted_adapter
    builtins.input = _eof_input
    try:
        first_rc = runner_module.main(
            ["b2", str(req_path), "--interactive", "--output", str(out_path)])
    finally:
        builtins.input = original_input

    ok("the initial (EOF-interrupted) b2 run is reported failed, not crashed",
      first_rc == 1)
    ok("a checkpoint was written -- the questions call succeeded before the EOF",
      checkpoint_path.exists())
    ok("the initial run's own output file is also written, marked failed",
      out_path.exists() and BaselineRunOutput.model_validate_json(out_path.read_text()).failed)

    resuming_adapter = FakeAdapter([
        completion({"test_cases": [CASE]}, prompt_tokens=50, completion_tokens=20),
    ])
    runner_module._ADAPTER_FACTORIES["gemini"] = lambda: resuming_adapter
    try:
        resume_rc = runner_module.main(
            ["b2-resume", str(req_path), "--checkpoint", str(checkpoint_path),
             "--answers-json", str(answers_path), "--output", str(out_path)])
    finally:
        runner_module._ADAPTER_FACTORIES.clear()
        runner_module._ADAPTER_FACTORIES.update(original_factories)

    ok("resume succeeds end-to-end", resume_rc == runner_module.EXIT_SUCCESS)
    ok("resume makes exactly one call (generate only)", len(resuming_adapter.calls) == 1)
    final = BaselineRunOutput.model_validate_json(out_path.read_text())
    ok("the final output (overwritten by resume) is a successful B2 run with the "
      "expected test case", final.failed is False and final.test_cases[0].id == "TC-1")
    cost_path = out_path.with_name(out_path.stem + ".cost.json")
    ok("a cost report was also written atomically alongside the final output",
      cost_path.exists())


# ---------------------------------------------------------------------------------
# Fairness: B1 and B2's generation instructions must not diverge except for the
# added Q&A block -- a silent drift here would break the "same output shape,
# competent-practitioner" comparison the whole arm depends on.
# ---------------------------------------------------------------------------------

section("Fairness -- B1 and B2 generation prompts share every instruction paragraph")
from evaluation.runner import (  # noqa: E402
    _B1_PROMPT_PATH, _B2_GENERATE_PROMPT_PATH, _B2_QUESTIONS_PROMPT_PATH,
)

b1_text = _B1_PROMPT_PATH.read_text()
b2_generate_text = _B2_GENERATE_PROMPT_PATH.read_text()
b2_questions_text = _B2_QUESTIONS_PROMPT_PATH.read_text()
b1_technique_block = b1_text[b1_text.index("Available test-design techniques"):
                             b1_text.index("<<<UNTRUSTED_CONTENT_START>>>")]
b2_technique_block = b2_generate_text[b2_generate_text.index("Available test-design techniques"):
                                       b2_generate_text.index("<<<UNTRUSTED_CONTENT_START>>>")]
ok("the technique vocabulary block is byte-identical between B1 and B2's generate prompt",
  b1_technique_block == b2_technique_block)

_TREATMENT_ONLY_TERMS = (
    "IssueCategory", "ELIGIBLE_TECHNIQUES", "refinement loop", "revision cap",
    "Quality Checker", "quality_checker", "AMBIGUOUS_TERM", "NON_ATOMIC", "VAGUE_PRONOUN",
)
for prompt_name, text in (("b1_baseline.txt", b1_text), ("b2_generate.txt", b2_generate_text),
                          ("b2_questions.txt", b2_questions_text)):
    ok(f"{prompt_name} withholds every pipeline-only treatment term",
      all(term not in text for term in _TREATMENT_ONLY_TERMS))


# ---------------------------------------------------------------------------------
# --answers-json positional matching (scripted/offline answer source)
# ---------------------------------------------------------------------------------

section("--answers-json positional matching")
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    answers_path = Path(tmp) / "answers.json"
    answers_path.write_text(json.dumps(["2 seconds", "5 attempts"]))
    answer_fn = _positional_answers_from_file(answers_path)
    qs = [ClarificationQuestion(id="Q1", question_text="?"), ClarificationQuestion(id="Q2", question_text="?")]
    answers = answer_fn(qs)
    ok("positional answers matched to question ids in order",
      [(a.question_id, a.answer_text) for a in answers] ==
      [("Q1", "2 seconds"), ("Q2", "5 attempts")])

    mismatched_path = Path(tmp) / "mismatched.json"
    mismatched_path.write_text(json.dumps(["only one answer"]))
    answer_fn2 = _positional_answers_from_file(mismatched_path)
    try:
        answer_fn2(qs)
        ok("a count mismatch between questions and answers raises", False)
    except ValueError:
        ok("a count mismatch between questions and answers raises", True)


print(f"\n{PASSED}/{PASSED + len(FAILED)} checks passed")
if FAILED:
    print("FAILED:")
    for label in FAILED:
        print(f"  - {label}")

import sys  # noqa: E402
sys.exit(0 if not FAILED else 1)
