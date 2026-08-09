"""
Static-shape AND real-call-site regression test for orchestrator/stage_fns.py's ten
Protocols. Run after any change to stage_fns.py or to a StageFns/HumanFns call site in
pipeline.py:

    python -m orchestrator.test_stage_fns

Plain script, no pytest -- same reasoning as design/test_schemas.py.

WHAT CHANGED (2026-08-09, second pass): the first version of this file compared each
Protocol's `__call__` signature against a HAND-WRITTEN stub -- typed by hand to match
the Protocol, not derived from or checked against pipeline.py's actual call sites. That
genuinely verifies "does this Protocol match a stub I wrote to look like it", which is
NOT the same claim as "does this Protocol match what pipeline.py actually calls" -- a
transcription error made once, when first writing both the Protocol and its stub from
the same (possibly wrong) understanding of a call site, would sail through undetected
on both sides. Combined with test_harness.py's separate stub-vs-real-call-site checks,
the two layers were still only transitively linked through a human-authored middleman.

This version closes that gap DIRECTLY for all ten fields in one pass: `_end_to_end()`
drives a real `run_document()` call through every StageFns/HumanFns field via recording
fixtures, capturing the actual positional args pipeline.py passes at each real call
site. Each capture is checked against its Protocol's declared parameter count AND each
argument's runtime type (via `typing.get_type_hints()`, not raw `inspect.signature()`
annotations -- both `stage_fns.py` and this file use `from __future__ import
annotations`, so raw annotations are unresolved forward-reference strings; comparing
two such strings can match by formatting coincidence without meaning anything).

The hand-written-stub comparison from the first version is KEPT, not deleted -- it is
still a real, correctly-scoped check (it catches a Protocol's own internal
inconsistency, e.g. an annotation that doesn't parse or resolve, independent of
call-site behavior) -- but it is no longer the file's ONLY verification, and the module
docstring no longer implies it closes the call-site-drift gap by itself.

Still narrower than running a real type checker: runtime isinstance-based checks accept
any subtype and cannot catch a parameter that is the right runtime type but the wrong
STATIC type (e.g. a function typed to take `object` that happens to receive a
`Requirement` this one time). Running `mypy orchestrator/` would catch that in addition
to everything checked here -- noted as an optional manual step, not added to
requirements.txt (no type checker is used anywhere else in this project).
"""

from __future__ import annotations

import inspect
import typing
from typing import Optional

from design.schemas import (
    ALL_STAGES, Classification, ConsistencyConflict, DependencyLink, IssueCategory,
    QualityReport, RefinerAnswer, RefinerTurn, Requirement, RequirementRunRecord,
    RequirementSet, RunMetadata, RunOutcome, StageConfig, TestStrategy,
    prompt_fingerprint,
)
from orchestrator.pipeline import HumanFns, StageFns, Throttle, run_document
from orchestrator.stage_fns import (
    AnswerQuestionsFn, CheckConsistencyFn, CheckQualityFn, ClassifyFn, DecideAtCapFn,
    GenerateTestsFn, MapDependenciesFn, RefineQuestionerFn, RefineRewriterFn,
    SelectStrategyFn, StageCallResult,
)

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


# ---------------------------------------------------------------------------------
# Layer 1: hand-written stub vs Protocol (kept, correctly scoped -- see module
# docstring for exactly what this does and does not establish on its own).
# ---------------------------------------------------------------------------------

def _hint_mismatches(protocol_call, stub_fn) -> list[str]:
    proto_sig = inspect.signature(protocol_call)
    stub_sig = inspect.signature(stub_fn)
    proto_hints = typing.get_type_hints(protocol_call)
    stub_hints = typing.get_type_hints(stub_fn)

    proto_params = [p for p in proto_sig.parameters.values() if p.name != "self"]
    stub_params = list(stub_sig.parameters.values())

    errors = []
    if len(proto_params) != len(stub_params):
        errors.append(f"param count {len(proto_params)} != {len(stub_params)}")
    for pp, sp in zip(proto_params, stub_params):
        if pp.name != sp.name:
            errors.append(f"param name {pp.name!r} != {sp.name!r}")
        elif proto_hints.get(pp.name) != stub_hints.get(sp.name):
            errors.append(
                f"{pp.name}: {proto_hints.get(pp.name)!r} != {stub_hints.get(sp.name)!r}")
    if proto_hints.get("return") != stub_hints.get("return"):
        errors.append(
            f"return: {proto_hints.get('return')!r} != {stub_hints.get('return')!r}")
    return errors


def stub_check_consistency(requirement_set: RequirementSet) -> StageCallResult: ...
def stub_map_dependencies(requirement_set: RequirementSet) -> StageCallResult: ...


def stub_classify(
    requirement: Requirement, requirement_set: RequirementSet,
) -> StageCallResult: ...


def stub_check_quality(
    requirement: Requirement, classification: Classification,
    relevant_conflicts: Optional[list[ConsistencyConflict]],
    relevant_dependencies: Optional[list[DependencyLink]],
    suppressed_issue_ids: list[str],
) -> StageCallResult: ...


def stub_refine_questioner(
    requirement: Requirement, quality_report: QualityReport,
) -> StageCallResult: ...


def stub_refine_rewriter(
    requirement: Requirement, answers: list[RefinerAnswer],
) -> StageCallResult: ...


def stub_select_strategy(
    requirement: Requirement, classification: Classification,
    relevant_dependencies: Optional[list[DependencyLink]],
) -> StageCallResult: ...


def stub_generate_tests(
    requirement: Requirement, strategy: TestStrategy,
    relevant_dependencies: Optional[list[DependencyLink]],
) -> StageCallResult: ...


def stub_answer_questions(turn: RefinerTurn) -> list[RefinerAnswer]: ...
def stub_decide_at_cap(record: RequirementRunRecord) -> tuple[RunOutcome, str]: ...


_STUB_FIELDS = [
    ("check_consistency", CheckConsistencyFn, stub_check_consistency),
    ("map_dependencies", MapDependenciesFn, stub_map_dependencies),
    ("classify", ClassifyFn, stub_classify),
    ("check_quality", CheckQualityFn, stub_check_quality),
    ("refine_questioner", RefineQuestionerFn, stub_refine_questioner),
    ("refine_rewriter", RefineRewriterFn, stub_refine_rewriter),
    ("select_strategy", SelectStrategyFn, stub_select_strategy),
    ("generate_tests", GenerateTestsFn, stub_generate_tests),
    ("answer_questions", AnswerQuestionsFn, stub_answer_questions),
    ("decide_at_cap", DecideAtCapFn, stub_decide_at_cap),
]


def test_protocol_matches_hand_written_stub() -> None:
    section("Layer 1: Protocol <-> hand-written stub (internal consistency only)")
    for name, protocol, stub in _STUB_FIELDS:
        errors = _hint_mismatches(protocol.__call__, stub)
        ok(f"{name}: Protocol matches a correctly-typed stub", errors == [])
        for e in errors:
            print(f"        {e}")


def test_the_hint_comparison_can_actually_fire() -> None:
    section("Layer 1 mutation check: the comparison actually discriminates")

    def wrong_param_count(requirement: Requirement) -> StageCallResult: ...
    ok("wrong param count is caught",
       _hint_mismatches(ClassifyFn.__call__, wrong_param_count) != [])

    def wrong_annotation(requirement: str, requirement_set: RequirementSet) -> StageCallResult: ...
    ok("wrong param annotation is caught",
       _hint_mismatches(ClassifyFn.__call__, wrong_annotation) != [])

    ok("a correctly-typed stub is NOT flagged (no false positives)",
       _hint_mismatches(ClassifyFn.__call__, stub_classify) == [])


# ---------------------------------------------------------------------------------
# Layer 2: real call sites, via a real run_document() call. This is the layer that
# closes the gap Layer 1 cannot -- every capture below is what pipeline.py itself
# handed to the field, not a hand-typed re-description of it.
# ---------------------------------------------------------------------------------

class Recorder:
    """Records every call's positional args (like test_harness.py's Scripted) and
    returns one StageCallResult per scripted raw dict, popped in order -- reused for
    the last entry if called more times than raws were provided."""

    def __init__(self, raws: list[dict]):
        self.calls: list[tuple] = []
        self._raws = list(raws)

    def __call__(self, *args) -> StageCallResult:
        self.calls.append(args)
        raw = self._raws.pop(0) if len(self._raws) > 1 else self._raws[0]
        return StageCallResult(raw=raw, prompt_tokens=1, completion_tokens=1)


class RecordingAnswerQuestions:
    def __init__(self):
        self.calls: list[tuple] = []
        self.returned: list = []

    def __call__(self, turn: RefinerTurn) -> list[RefinerAnswer]:
        self.calls.append((turn,))
        answers = [RefinerAnswer(question_id=turn.questions[0].id, answer_text="an answer",
                                 user_confirms_resolved=False)]
        self.returned.append(answers)
        return answers


class RecordingDecideAtCap:
    def __init__(self):
        self.calls: list[tuple] = []
        self.returned: list = []

    def __call__(self, record: RequirementRunRecord) -> tuple[RunOutcome, str]:
        self.calls.append((record,))
        result = (RunOutcome.CAP_GENERATED, "best-effort is acceptable here")
        self.returned.append(result)
        return result


def _run_end_to_end() -> dict:
    """Drives one real run_document() call, engineered so every one of the 8
    StageFns fields and both HumanFns fields is invoked at least once: check_quality
    fails on round 1 (exercising refine_questioner/refine_rewriter) and fails again on
    round 2 with max_revisions=2 (hitting the cap, exercising decide_at_cap), CAP_
    GENERATED lets select_strategy/generate_tests still run. Returns {field_name:
    recorder} for every field."""
    req = Requirement(id="R1", text="It shall do the thing.", source_doc_id="doc-1")
    requirement_set = RequirementSet(doc_id="doc-1", requirements=[req])
    stage_configs = {s: StageConfig(model="fake-model", prompt_hash=prompt_fingerprint(s),
                                    prompt_version="v1") for s in ALL_STAGES}
    metadata = RunMetadata(run_id="run-protocol-check", started_at=__import__("datetime").datetime.now(
        __import__("datetime").timezone.utc), stages=stage_configs)
    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc))

    check_consistency = Recorder([{"doc_id": "doc-1", "conflicts": []}])
    map_dependencies = Recorder([{"doc_id": "doc-1", "dependencies": []}])
    classify = Recorder([{"requirement_id": "R1", "system_type": "web", "rationale": "r"}])
    check_quality = Recorder([
        {"requirement_id": "R1", "passed": False, "issues": [
            {"id": "R1-ISSUE-1", "category": IssueCategory.VAGUE_PRONOUN.value, "explanation": "e"}]},
        {"requirement_id": "R1", "passed": False, "issues": [
            {"id": "R1-ISSUE-1", "category": IssueCategory.VAGUE_PRONOUN.value, "explanation": "e"}]},
    ])
    refine_questioner = Recorder([{"requirement_id": "R1", "revision_number": 1, "questions": [
        {"id": "Q1", "issue_id": "R1-ISSUE-1", "issue_category": IssueCategory.VAGUE_PRONOUN.value,
         "question_text": "what does it refer to?"}]}])
    refine_rewriter = Recorder([{"requirement_id": "R1", "original_text": req.text,
                                 "refined_text": "The sensor shall do the thing.",
                                 "revision_number": 1, "answers_used": [
                                     {"question_id": "Q1", "answer_text": "an answer",
                                      "user_confirms_resolved": False}]}])
    select_strategy = Recorder([{"requirement_id": "R1", "system_type": "web",
                                 "techniques": ["boundary_value_analysis"], "rationale": "r"}])
    generate_tests = Recorder([{"requirement_id": "R1", "test_cases": [{
        "id": "TC-R1-1", "requirement_ids": ["R1"], "technique_used": "boundary_value_analysis",
        "title": "t", "steps": ["s"], "expected_result": "e"}]}])
    answer_questions = RecordingAnswerQuestions()
    decide_at_cap = RecordingDecideAtCap()

    stage_fns = StageFns(
        check_consistency=check_consistency, map_dependencies=map_dependencies,
        classify=classify, check_quality=check_quality, refine_questioner=refine_questioner,
        refine_rewriter=refine_rewriter, select_strategy=select_strategy,
        generate_tests=generate_tests)
    human_fns = HumanFns(answer_questions=answer_questions, decide_at_cap=decide_at_cap)

    record = run_document(requirement_set, metadata, stage_fns, human_fns, throttle,
                          max_revisions=2, backoff_seconds=lambda a: 0.0)
    ok("the engineered scenario actually reached CAP_GENERATED (sanity check)",
       record.requirement_records
       and record.requirement_records[0].outcome.value == "cap_generated")

    return {
        "check_consistency": (CheckConsistencyFn, check_consistency.calls),
        "map_dependencies": (MapDependenciesFn, map_dependencies.calls),
        "classify": (ClassifyFn, classify.calls),
        "check_quality": (CheckQualityFn, check_quality.calls),
        "refine_questioner": (RefineQuestionerFn, refine_questioner.calls),
        "refine_rewriter": (RefineRewriterFn, refine_rewriter.calls),
        "select_strategy": (SelectStrategyFn, select_strategy.calls),
        "generate_tests": (GenerateTestsFn, generate_tests.calls),
        "answer_questions": (AnswerQuestionsFn, answer_questions.calls),
        "decide_at_cap": (DecideAtCapFn, decide_at_cap.calls),
    }


def _runtime_type_ok(value, expected) -> bool:
    """Permissive structural check: does `value`'s runtime type match the resolved
    type hint `expected`? Handles Optional[X] (Union[X, None]), list[X], tuple[X, Y],
    and plain classes. Anything it doesn't recognize is allowed through rather than
    spuriously failed -- this is a floor, not a full type checker (see module
    docstring)."""
    origin = typing.get_origin(expected)
    if origin is typing.Union:
        return any(_runtime_type_ok(value, a) for a in typing.get_args(expected))
    if expected is type(None):
        return value is None
    if origin is list:
        if not isinstance(value, list):
            return False
        (item_type,) = typing.get_args(expected) or (object,)
        return all(_runtime_type_ok(v, item_type) for v in value)
    if origin is tuple:
        item_types = typing.get_args(expected)
        if not isinstance(value, tuple) or len(item_types) != len(value):
            return False
        return all(_runtime_type_ok(v, t) for v, t in zip(value, item_types))
    if isinstance(expected, type):
        return isinstance(value, expected)
    return True


def test_real_call_sites_match_their_protocols() -> None:
    section("Layer 2: every field's REAL pipeline.py call site matches its Protocol")
    captured = _run_end_to_end()

    for name, (protocol, calls) in captured.items():
        ok(f"{name}: was actually called at least once by the real pipeline", len(calls) >= 1)
        if not calls:
            continue
        proto_params = [p for p in inspect.signature(protocol.__call__).parameters.values()
                        if p.name != "self"]
        proto_hints = typing.get_type_hints(protocol.__call__)
        for call_index, args in enumerate(calls):
            ok(f"{name} call #{call_index}: arg count matches the Protocol "
               f"({len(args)} vs {len(proto_params)})",
               len(args) == len(proto_params))
            for param, value in zip(proto_params, args):
                expected = proto_hints.get(param.name)
                ok(f"{name} call #{call_index}: arg {param.name!r} runtime type matches "
                   f"({type(value).__name__} vs {expected})",
                   _runtime_type_ok(value, expected))


def main() -> int:
    print("=" * 72)
    print("stage_fns.py Protocol verification (hand-stub layer + real call-site layer)")
    print("=" * 72)
    for fn in (test_protocol_matches_hand_written_stub, test_the_hint_comparison_can_actually_fire,
              test_real_call_sites_match_their_protocols):
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
