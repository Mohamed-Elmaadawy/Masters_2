"""
Regression tests for orchestrator/cli.py. Run after any change there:

    python -m orchestrator.test_cli

Plain script, no pytest -- same convention as design/test_schemas.py/orchestrator/
test_config.py/orchestrator/test_stages.py. Zero live network calls: `_run`'s
adapter_factories/human_fns_factory seam is used to inject a FakeAdapter (same pattern
as orchestrator/test_stages.py's) and scripted HumanFns, so nothing here ever touches
GEMINI_API_KEY/GROQ_API_KEY or the network.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from design.schemas import (
    ALL_STAGES, ClarifyingQuestion, Classification, ConsistencyReport, DependencyReport,
    DocumentOutcome, DocumentRunRecord, Issue, IssueCategory, OutputMode, QualityReport,
    RefinedRequirement, Requirement, RequirementRunRecord, RequirementSet, RefinerAnswer,
    RefinerTurn, RunOutcome, SystemType, TestCase, TestPlan, TestStrategy, TestTechnique,
)
from orchestrator.cli import EXIT_CONFIG_ERROR, EXIT_INTERRUPTED, EXIT_STAGE_ERRORS, EXIT_SUCCESS, _run
from orchestrator.config import (
    load_run_config, resolve_run_config, run_dir_for, to_run_metadata, write_run_config,
)
from orchestrator.human_cli import decide_at_cap_cli
from orchestrator.pipeline import HumanFns, read_document_run, write_document_run, write_requirement_run
from orchestrator.providers.base import CompletionResult

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


PROMPTS_DIR = (Path(__file__).parent / "example_prompts").resolve()


class FakeAdapter:
    """Same shape as orchestrator/test_stages.py's FakeAdapter -- .complete() never
    touches the network. Each scripted response is popped in order; an Exception
    instance is raised instead of returned."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, prompt, *, model, temperature, timeout_seconds,
                output_mode=OutputMode.TEXT, response_schema=None, schema_name=None):
        self.calls += 1
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def _completion(model) -> CompletionResult:
    return CompletionResult(text=model.model_dump_json(), prompt_tokens=5,
                            completion_tokens=5, output_mode=OutputMode.TEXT)


def _spy_factory(should_not_be_called: bool = True):
    """An adapter_factories entry that fails the test if it's ever called -- used to
    prove the CLI never constructs an adapter after a configuration/collision error."""
    def factory():
        raise AssertionError("adapter factory should never be called after a "
                             "configuration/input/collision error")
    return factory


def write_config_yaml(tmp_path: Path, run_id: str, output_dir: str = "runs",
                      retry_overrides: dict = None) -> Path:
    prompts = {stage: str(PROMPTS_DIR / f"{stage}.txt") for stage in ALL_STAGES}
    config_dict = {
        "run_id": run_id,
        "output_dir": output_dir,
        "max_revisions": 3,
        "rate_limits": {"gemini/fake-model": {"requests_per_minute": None}},
        "defaults": {"provider": "gemini", "model": "fake-model", "prompt_version": "v1"},
        "prompts": prompts,
    }
    if retry_overrides:
        config_dict["retry"] = retry_overrides
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config_dict))
    return config_path


def write_config_yaml_with_local_prompts(
    tmp_path: Path, run_id: str, output_dir: str = "runs",
) -> tuple[Path, dict[str, str]]:
    """Same shape as write_config_yaml, but copies each stage's prompt text into
    tmp_path/prompts/<stage>.txt instead of pointing at the shared PROMPTS_DIR fixture --
    for tests that need to edit a prompt file's content afterward without mutating a
    fixture every other test in this file also reads."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    prompts: dict[str, str] = {}
    for stage in ALL_STAGES:
        text = (PROMPTS_DIR / f"{stage}.txt").read_text()
        stage_path = prompts_dir / f"{stage}.txt"
        stage_path.write_text(text)
        prompts[stage] = str(stage_path)
    config_dict = {
        "run_id": run_id,
        "output_dir": output_dir,
        "max_revisions": 3,
        "rate_limits": {"gemini/fake-model": {"requests_per_minute": None}},
        "defaults": {"provider": "gemini", "model": "fake-model", "prompt_version": "v1"},
        "prompts": prompts,
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config_dict))
    return config_path, prompts


REQUIREMENT_TEXT = "The system shall respond quickly."


def write_input_json(tmp_path: Path, req_id: str = "REQ-1") -> Path:
    requirement_set = RequirementSet(
        doc_id="DOC-1",
        requirements=[Requirement(id=req_id, text=REQUIREMENT_TEXT)])
    input_path = tmp_path / "input.json"
    input_path.write_text(requirement_set.model_dump_json())
    return input_path


def _happy_path_responses(req_id: str) -> list:
    prefix_len = len(req_id)
    test_case_id = f"TC-{prefix_len}-{req_id}-1"
    return [
        _completion(ConsistencyReport(doc_id="DOC-1", conflicts=[])),
        _completion(DependencyReport(doc_id="DOC-1", dependencies=[])),
        _completion(Classification(requirement_id=req_id, system_type=SystemType.OTHER,
                                   rationale="test rationale")),
        _completion(QualityReport(requirement_id=req_id, passed=True, issues=[])),
        _completion(TestStrategy(requirement_id=req_id, system_type=SystemType.OTHER,
                                 techniques=[TestTechnique.EXPLORATORY],
                                 rationale="nothing else fits")),
        _completion(TestPlan(requirement_id=req_id, test_cases=[TestCase(
            id=test_case_id, requirement_ids=[req_id], technique_used=TestTechnique.EXPLORATORY,
            title="exploratory pass", steps=["do the thing"], expected_result="it works")])),
    ]


def _human_fns_unused() -> HumanFns:
    def _unused_answer(turn):
        raise AssertionError("answer_questions should not be called on this path")
    return HumanFns(answer_questions=_unused_answer, decide_at_cap=decide_at_cap_cli)


# ---------------------------------------------------------------------------------
# Configuration/input errors -- exit 2, before any adapter is constructed.
# ---------------------------------------------------------------------------------

def test_bad_config_shape_rejected() -> None:
    section("a RunConfig missing a required key exits 2, no run dir created")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump({"defaults": {"provider": "gemini"}}))
        input_path = write_input_json(tmp_path)
        code = _run(["run", str(config_path), str(input_path)],
                    adapter_factories={"gemini": _spy_factory(), "groq": _spy_factory()})
        ok("exit code is EXIT_CONFIG_ERROR", code == EXIT_CONFIG_ERROR)
        ok("no runs/ directory was created", not (tmp_path / "runs").exists())


def test_malformed_yaml_rejected() -> None:
    section("syntactically invalid YAML exits 2, not an uncaught exception")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config_path = tmp_path / "config.yaml"
        config_path.write_text("not: valid: yaml: [")
        input_path = write_input_json(tmp_path)
        code = _run(["run", str(config_path), str(input_path)],
                    adapter_factories={"gemini": _spy_factory(), "groq": _spy_factory()})
        ok("exit code is EXIT_CONFIG_ERROR", code == EXIT_CONFIG_ERROR)


def test_bad_input_json_rejected_before_any_adapter() -> None:
    section("input JSON that fails RequirementSet validation exits 2, no adapter built")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config_path = write_config_yaml(tmp_path, run_id="run-bad-input")
        input_path = tmp_path / "input.json"
        input_path.write_text(json.dumps({"requirements": []}))  # min_length=1 violated
        code = _run(["run", str(config_path), str(input_path)],
                    adapter_factories={"gemini": _spy_factory(), "groq": _spy_factory()})
        ok("exit code is EXIT_CONFIG_ERROR", code == EXIT_CONFIG_ERROR)


def test_missing_input_file_rejected() -> None:
    section("a --input path that doesn't exist exits 2")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config_path = write_config_yaml(tmp_path, run_id="run-missing-input")
        code = _run(["run", str(config_path), str(tmp_path / "does-not-exist.json")],
                    adapter_factories={"gemini": _spy_factory(), "groq": _spy_factory()})
        ok("exit code is EXIT_CONFIG_ERROR", code == EXIT_CONFIG_ERROR)


# ---------------------------------------------------------------------------------
# Run-directory collision -- exit 2, before any adapter is constructed, existing
# files untouched.
# ---------------------------------------------------------------------------------

def test_run_dir_collision_rejected() -> None:
    section("an existing run_dir is never reused or mixed with a new run")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config_path = write_config_yaml(tmp_path, run_id="run-collide")
        input_path = write_input_json(tmp_path)
        existing_run_dir = tmp_path / "runs" / "run-collide"
        existing_run_dir.mkdir(parents=True)
        sentinel = existing_run_dir / "document.json"
        sentinel.write_text('{"sentinel": true}')

        code = _run(["run", str(config_path), str(input_path)],
                    adapter_factories={"gemini": _spy_factory(), "groq": _spy_factory()})

        ok("exit code is EXIT_CONFIG_ERROR", code == EXIT_CONFIG_ERROR)
        ok("existing document.json is byte-for-byte unchanged",
           sentinel.read_text() == '{"sentinel": true}')
        ok("no run_config.json was written into the existing run dir",
           not (existing_run_dir / "run_config.json").exists())


# ---------------------------------------------------------------------------------
# Full happy path -- exit 0.
# ---------------------------------------------------------------------------------

def test_happy_path_completes_with_exit_0() -> None:
    section("a clean run with no stage errors exits 0 and writes run_config.json + records")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        req_id = "REQ-1"
        config_path = write_config_yaml(tmp_path, run_id="run-happy")
        input_path = write_input_json(tmp_path, req_id=req_id)
        fake = FakeAdapter(_happy_path_responses(req_id))

        code = _run(["run", str(config_path), str(input_path)],
                    adapter_factories={"gemini": lambda: fake, "groq": _spy_factory()},
                    human_fns_factory=_human_fns_unused)

        run_dir = tmp_path / "runs" / "run-happy"
        ok("exit code is EXIT_SUCCESS", code == EXIT_SUCCESS)
        ok("exactly 6 stage calls made (2 document + 4 per-requirement)", fake.calls == 6)
        ok("run_config.json was written", (run_dir / "run_config.json").exists())
        ok("document.json was written", (run_dir / "document.json").exists())
        req_files = list((run_dir / "requirements").glob("*.json"))
        ok("exactly one requirement record file", len(req_files) == 1)


# ---------------------------------------------------------------------------------
# A permanently-failing stage -- exit 1 (completed record, but with recorded errors).
# ---------------------------------------------------------------------------------

def test_stage_error_exits_1() -> None:
    section("a requirement that ends in RunOutcome.ERROR exits 1, not 0")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        req_id = "REQ-1"
        config_path = write_config_yaml(tmp_path, run_id="run-error",
                                        retry_overrides={"max_attempts": 1})
        input_path = write_input_json(tmp_path, req_id=req_id)
        responses = [
            _completion(ConsistencyReport(doc_id="DOC-1", conflicts=[])),
            _completion(DependencyReport(doc_id="DOC-1", dependencies=[])),
            RuntimeError("classifier boom -- simulated permanent failure"),
        ]
        fake = FakeAdapter(responses)

        code = _run(["run", str(config_path), str(input_path)],
                    adapter_factories={"gemini": lambda: fake, "groq": _spy_factory()},
                    human_fns_factory=_human_fns_unused)

        ok("exit code is EXIT_STAGE_ERRORS", code == EXIT_STAGE_ERRORS)


# ---------------------------------------------------------------------------------
# EOFError from HumanFns mid-run -- exit 130, no overclaiming about what was saved.
# ---------------------------------------------------------------------------------

def test_interrupted_by_eof_exits_130() -> None:
    section("EOFError from answer_questions exits 130 with a non-overclaiming message")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        req_id = "REQ-1"
        config_path = write_config_yaml(tmp_path, run_id="run-interrupted")
        input_path = write_input_json(tmp_path, req_id=req_id)
        issue = Issue(id=f"{req_id}-ISSUE-1", category=IssueCategory.AMBIGUOUS_TERM,
                     span="quickly", explanation="no measurable threshold")
        failing_report = QualityReport(requirement_id=req_id, passed=False, issues=[issue])
        question = ClarifyingQuestion(id="Q1", issue_id=issue.id, issue_category=issue.category,
                                      question_text="how fast, precisely?")
        responses = [
            _completion(ConsistencyReport(doc_id="DOC-1", conflicts=[])),
            _completion(DependencyReport(doc_id="DOC-1", dependencies=[])),
            _completion(Classification(requirement_id=req_id, system_type=SystemType.OTHER,
                                       rationale="test")),
            _completion(failing_report),
            _completion(RefinerTurn(requirement_id=req_id, revision_number=1,
                                    questions=[question])),
        ]
        fake = FakeAdapter(responses)

        def human_fns_factory() -> HumanFns:
            def answer_questions(turn):
                raise EOFError("stdin closed")
            return HumanFns(answer_questions=answer_questions, decide_at_cap=decide_at_cap_cli)

        messages: list[str] = []
        code = _run(["run", str(config_path), str(input_path)],
                    adapter_factories={"gemini": lambda: fake, "groq": _spy_factory()},
                    human_fns_factory=human_fns_factory, output_fn=messages.append)

        run_dir = tmp_path.resolve() / "runs" / "run-interrupted"
        ok("exit code is EXIT_INTERRUPTED", code == EXIT_INTERRUPTED)
        ok("message names the run dir, doesn't claim state was definitely saved",
           messages == [f"Run interrupted. Inspect {run_dir} for any saved state."])
        ok("run_config.json was still written before the interrupt", (run_dir / "run_config.json").exists())


def test_cap_stopped_alone_does_not_exit_1() -> None:
    """max_revisions=2, the requirement fails both rounds, the human stops at the cap.
    No StageError/DocumentStageError anywhere and no RunOutcome.ERROR -- CAP_STOPPED is
    a valid terminal outcome on its own and must not trip exit 1."""
    section("CAP_STOPPED with no recorded stage errors exits 0, not 1")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        req_id = "REQ-1"
        config_dict_path = write_config_yaml(tmp_path, run_id="run-cap-stopped")
        config_text = config_dict_path.read_text().replace("max_revisions: 3", "max_revisions: 2")
        config_dict_path.write_text(config_text)
        input_path = write_input_json(tmp_path, req_id=req_id)

        issue = Issue(id=f"{req_id}-ISSUE-1", category=IssueCategory.AMBIGUOUS_TERM,
                     span="quickly", explanation="no measurable threshold")
        failing_report = QualityReport(requirement_id=req_id, passed=False, issues=[issue])
        question = ClarifyingQuestion(id="Q1", issue_id=issue.id, issue_category=issue.category,
                                      question_text="how fast, precisely?")
        answer = RefinerAnswer(question_id=question.id, answer_text="under 200ms",
                               user_confirms_resolved=False)
        rewrite = RefinedRequirement(
            requirement_id=req_id, original_text=REQUIREMENT_TEXT,
            refined_text="The system shall respond within 200ms.", revision_number=1,
            answers_used=[answer])
        responses = [
            _completion(ConsistencyReport(doc_id="DOC-1", conflicts=[])),
            _completion(DependencyReport(doc_id="DOC-1", dependencies=[])),
            _completion(Classification(requirement_id=req_id, system_type=SystemType.OTHER,
                                       rationale="test")),
            _completion(failing_report),                                        # round 1 check
            _completion(RefinerTurn(requirement_id=req_id, revision_number=1,
                                    questions=[question])),                       # round 1 questioner
            _completion(rewrite),                                                # round 1 rewriter
            _completion(failing_report),                                         # round 2 check -> cap
        ]
        fake = FakeAdapter(responses)

        def human_fns_factory() -> HumanFns:
            return HumanFns(
                answer_questions=lambda turn: [answer],
                decide_at_cap=lambda record: (
                    RunOutcome.CAP_STOPPED, "operator chose to stop rather than accept risk"))

        code = _run(["run", str(config_dict_path), str(input_path)],
                    adapter_factories={"gemini": lambda: fake, "groq": _spy_factory()},
                    human_fns_factory=human_fns_factory)

        ok("all 7 scripted stage calls were consumed", fake.calls == 7)
        ok("exit code is EXIT_SUCCESS, not EXIT_STAGE_ERRORS", code == EXIT_SUCCESS)


# ---------------------------------------------------------------------------------
# resume -- continuing an interrupted run.
# ---------------------------------------------------------------------------------

def test_resume_completes_an_eof_interrupted_run() -> None:
    section("resume re-asks the human and finishes a run that hit EOFError mid-round")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        req_id = "REQ-1"
        config_path = write_config_yaml(tmp_path, run_id="run-resume-happy")
        input_path = write_input_json(tmp_path, req_id=req_id)
        issue = Issue(id=f"{req_id}-ISSUE-1", category=IssueCategory.AMBIGUOUS_TERM,
                     span="quickly", explanation="no measurable threshold")
        failing_report = QualityReport(requirement_id=req_id, passed=False, issues=[issue])
        question = ClarifyingQuestion(id="Q1", issue_id=issue.id, issue_category=issue.category,
                                      question_text="how fast, precisely?")
        first_responses = [
            _completion(ConsistencyReport(doc_id="DOC-1", conflicts=[])),
            _completion(DependencyReport(doc_id="DOC-1", dependencies=[])),
            _completion(Classification(requirement_id=req_id, system_type=SystemType.OTHER,
                                       rationale="test")),
            _completion(failing_report),
            _completion(RefinerTurn(requirement_id=req_id, revision_number=1,
                                    questions=[question])),
        ]
        first_fake = FakeAdapter(first_responses)

        def eof_human_fns_factory() -> HumanFns:
            def answer_questions(turn):
                raise EOFError("stdin closed")
            return HumanFns(answer_questions=answer_questions, decide_at_cap=decide_at_cap_cli)

        code = _run(["run", str(config_path), str(input_path)],
                    adapter_factories={"gemini": lambda: first_fake, "groq": _spy_factory()},
                    human_fns_factory=eof_human_fns_factory)
        ok("initial run is interrupted (exit 130)", code == EXIT_INTERRUPTED)

        run_dir = tmp_path.resolve() / "runs" / "run-resume-happy"
        answer = RefinerAnswer(question_id=question.id, answer_text="under 200ms",
                               user_confirms_resolved=False)
        rewrite = RefinedRequirement(
            requirement_id=req_id, original_text=REQUIREMENT_TEXT,
            refined_text="The system shall respond within 200ms.", revision_number=1,
            answers_used=[answer])
        resume_responses = [
            _completion(rewrite),                                                  # round 1 rewriter
            _completion(QualityReport(requirement_id=req_id, passed=True, issues=[])),  # round 2 check
            _completion(TestStrategy(requirement_id=req_id, system_type=SystemType.OTHER,
                                     techniques=[TestTechnique.EXPLORATORY],
                                     rationale="nothing else fits")),
            _completion(TestPlan(requirement_id=req_id, test_cases=[TestCase(
                id=f"TC-{len(req_id)}-{req_id}-1", requirement_ids=[req_id],
                technique_used=TestTechnique.EXPLORATORY, title="exploratory pass",
                steps=["do the thing"], expected_result="it works")])),
        ]
        resume_fake = FakeAdapter(resume_responses)

        def resume_human_fns_factory() -> HumanFns:
            def decide_at_cap_unused(record):
                raise AssertionError("decide_at_cap should not be called -- round 2 passes")
            return HumanFns(answer_questions=lambda turn: [answer],
                            decide_at_cap=decide_at_cap_unused)

        code = _run(["resume", str(run_dir)],
                    adapter_factories={"gemini": lambda: resume_fake, "groq": _spy_factory()},
                    human_fns_factory=resume_human_fns_factory)

        ok("resume completes (exit 0)", code == EXIT_SUCCESS)
        ok("all 4 resume-side stage calls were consumed", resume_fake.calls == 4)
        record = read_document_run(run_dir)
        ok("requirement outcome is completed",
           record.requirement_records[0].outcome == RunOutcome.COMPLETED)
        ok("the round the questioner asked before the interrupt is still on record",
           len(record.requirement_records[0].rounds) == 2)


def test_resume_missing_run_dir_rejected() -> None:
    section("resuming a run directory that doesn't exist exits 2, no adapter built")
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "no-such-run"
        code = _run(["resume", str(run_dir)],
                    adapter_factories={"gemini": _spy_factory(), "groq": _spy_factory()})
        ok("exit code is EXIT_CONFIG_ERROR", code == EXIT_CONFIG_ERROR)


def test_resume_prompt_drift_rejected() -> None:
    section("editing a prompt file after the run started makes resume refuse")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        req_id = "REQ-1"
        config_path, prompts = write_config_yaml_with_local_prompts(tmp_path, run_id="run-drift")
        input_path = write_input_json(tmp_path, req_id=req_id)
        fake = FakeAdapter(_happy_path_responses(req_id))

        code = _run(["run", str(config_path), str(input_path)],
                    adapter_factories={"gemini": lambda: fake, "groq": _spy_factory()},
                    human_fns_factory=_human_fns_unused)
        ok("initial run completes", code == EXIT_SUCCESS)

        classifier_prompt_path = Path(prompts["classifier"])
        classifier_prompt_path.write_text(
            classifier_prompt_path.read_text() + "\nEDITED AFTER RUN STARTED\n")

        messages: list[str] = []
        run_dir = tmp_path / "runs" / "run-drift"
        code = _run(["resume", str(run_dir)],
                    adapter_factories={"gemini": _spy_factory(), "groq": _spy_factory()},
                    output_fn=messages.append)

        ok("exit code is EXIT_CONFIG_ERROR", code == EXIT_CONFIG_ERROR)
        ok("message names the drifted stage", any("classifier" in m for m in messages))
        ok("the shared example_prompts fixture was never touched",
           "EDITED AFTER RUN STARTED" not in (PROMPTS_DIR / "classifier.txt").read_text())


def test_resume_mismatched_requirement_file_rejected() -> None:
    section("a requirements/*.json from a different run_id fails to load, refusing resume")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        req_id = "REQ-1"
        config_path, _ = write_config_yaml_with_local_prompts(tmp_path, run_id="run-mixed")
        run_config = load_run_config(config_path)
        resolved = resolve_run_config(run_config, config_path)
        run_dir = run_dir_for(resolved)
        write_run_config(run_dir, resolved)

        requirement_set = RequirementSet(
            doc_id="DOC-1", requirements=[Requirement(id=req_id, text=REQUIREMENT_TEXT)])
        metadata = to_run_metadata(resolved, datetime.now(timezone.utc))
        doc_record = DocumentRunRecord(
            requirement_set=requirement_set, metadata=metadata,
            outcome=DocumentOutcome.COMPLETED,
            consistency_report=ConsistencyReport(doc_id="DOC-1", conflicts=[]),
            dependency_report=DependencyReport(doc_id="DOC-1", dependencies=[]))
        write_document_run(run_dir, doc_record)
        foreign_record = RequirementRunRecord(
            requirement=Requirement(id=req_id, text=REQUIREMENT_TEXT),
            run_id="a-different-run-id")
        write_requirement_run(run_dir, foreign_record)

        code = _run(["resume", str(run_dir)],
                    adapter_factories={"gemini": _spy_factory(), "groq": _spy_factory()})
        ok("exit code is EXIT_CONFIG_ERROR", code == EXIT_CONFIG_ERROR)


def test_resume_after_completion_makes_no_stage_calls() -> None:
    section("resuming a fully COMPLETED run makes no stage calls and exits 0")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        req_id = "REQ-1"
        config_path = write_config_yaml(tmp_path, run_id="run-done")
        input_path = write_input_json(tmp_path, req_id=req_id)
        fake = FakeAdapter(_happy_path_responses(req_id))
        code = _run(["run", str(config_path), str(input_path)],
                    adapter_factories={"gemini": lambda: fake, "groq": _spy_factory()},
                    human_fns_factory=_human_fns_unused)
        ok("initial run completes", code == EXIT_SUCCESS)

        run_dir = tmp_path / "runs" / "run-done"
        resume_fake = FakeAdapter([])
        code = _run(["resume", str(run_dir)],
                    adapter_factories={"gemini": lambda: resume_fake, "groq": _spy_factory()},
                    human_fns_factory=_human_fns_unused)
        ok("exit code is EXIT_SUCCESS", code == EXIT_SUCCESS)
        ok("no stage calls made on resume", resume_fake.calls == 0)


ALL_TESTS = [
    test_bad_config_shape_rejected,
    test_malformed_yaml_rejected,
    test_bad_input_json_rejected_before_any_adapter,
    test_missing_input_file_rejected,
    test_run_dir_collision_rejected,
    test_happy_path_completes_with_exit_0,
    test_stage_error_exits_1,
    test_interrupted_by_eof_exits_130,
    test_cap_stopped_alone_does_not_exit_1,
    test_resume_completes_an_eof_interrupted_run,
    test_resume_missing_run_dir_rejected,
    test_resume_prompt_drift_rejected,
    test_resume_mismatched_requirement_file_rejected,
    test_resume_after_completion_makes_no_stage_calls,
]


if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
    print(f"\n{PASSED} checks passed, {len(FAILED)} failed")
    if FAILED:
        raise SystemExit(1)
