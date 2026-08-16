"""Task 2 of docs/superpowers/plans/2026-08-15-CLAUDE-CODE-HANDOVER.md (E3 level 1):
calls the Quality Checker stage ALONE -- one requirement, N calls (default 5), no
Classifier, no refinement loop, no other stage -- and reports whether the returned
issue sets are identical across calls.

Motivation: design/DESIGN_NOTES.md Known Limitation 10 records the checker returning
opposite verdicts on character-identical input in consecutive rounds. Every
per-category precision/recall figure in the evaluation is a single draw until this is
quantified.

Throwaway-grade on purpose (see the handover doc): lives here, not as a new module in
orchestrator/. No Classifier call either -- system_type is supplied on the command line
and used to build a placeholder Classification, since this measures the Quality Checker
in isolation, not the pair.

    python docs/superpowers/quality_checker_stability.py REQUIREMENT_SET.json REQ-ID
    python docs/superpowers/quality_checker_stability.py REQUIREMENT_SET.json REQ-ID \\
        --calls 10 --provider groq --model llama-3.3-70b-versatile --system-type web \\
        --output results.json

Exit codes: 0 the harness ran and produced a report (instability found is data, not a
script failure); 2 configuration/input error (bad file, unknown requirement id, missing
API key) -- always before any provider call.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from design.schemas import (  # noqa: E402
    Classification, Issue, IssueCategory, OutputMode, QualityReport, Requirement,
    RequirementSet, SystemType,
)
from orchestrator.config import ResolvedStageConfig  # noqa: E402
from orchestrator.providers.base import ProviderAdapter  # noqa: E402
from orchestrator.providers.gemini import GeminiAdapter  # noqa: E402
from orchestrator.providers.groq import GroqAdapter  # noqa: E402
from orchestrator.providers.base import CompletionResult  # noqa: E402
from orchestrator.stage_fns import StageCallFailed, StageCallFatal, StageCallPartial  # noqa: E402
from orchestrator.stages import make_check_quality_fn  # noqa: E402

EXIT_SUCCESS = 0
EXIT_CONFIG_ERROR = 2

_DEFAULT_PROMPT_PATH = _REPO_ROOT / "orchestrator" / "example_prompts" / "quality_checker.txt"

_ADAPTER_FACTORIES = {
    "gemini": lambda: GeminiAdapter.from_env(),
    "groq": lambda: GroqAdapter.from_env(),
}


def _issue_set(report: QualityReport) -> frozenset:
    """(category, span) pairs, not issue.id -- the Quality Checker mints a fresh id
    every call (design/DESIGN_NOTES.md, gap 6), so comparing ids would always disagree
    even when the actual defects found are identical. category+span is what
    RequirementRunRecord._issue_identity_is_stable already treats as a defect's real
    identity across rounds; reused here for the same reason."""
    return frozenset((issue.category.value, issue.span) for issue in report.issues)


def run_stability_check(
    requirement_set: RequirementSet,
    requirement_id: str,
    check_quality,
    system_type: SystemType,
    calls: int,
    output_fn=print,
) -> dict:
    matches = [r for r in requirement_set.requirements if r.id == requirement_id]
    if not matches:
        raise ValueError(f"requirement id {requirement_id!r} not found in {requirement_set.doc_id!r}")
    requirement = matches[0]
    classification = Classification(
        requirement_id=requirement.id, system_type=system_type,
        rationale="quality_checker_stability harness placeholder -- Classifier not run")

    reports: list[QualityReport] = []
    failures: list[str] = []
    for call_index in range(calls):
        try:
            result = check_quality(requirement, classification, None, None, [])
            report = QualityReport.model_validate(result.raw)
        except (StageCallFailed, StageCallFatal, StageCallPartial) as e:
            failures.append(f"call {call_index + 1}: {e}")
            output_fn(f"  call {call_index + 1}: FAILED -- {e}")
            continue
        reports.append(report)
        issue_summary = sorted(f"{cat}:{span!r}" for cat, span in _issue_set(report))
        output_fn(f"  call {call_index + 1}: passed={report.passed} issues={issue_summary}")

    distinct_sets = {_issue_set(r) for r in reports}
    counts: dict[frozenset, int] = {s: 0 for s in distinct_sets}
    for r in reports:
        counts[_issue_set(r)] += 1
    majority_size = max(counts.values()) if counts else 0

    return {
        "requirement_id": requirement_id,
        "calls_requested": calls,
        "calls_succeeded": len(reports),
        "calls_failed": len(failures),
        "failures": failures,
        "issue_sets": [sorted(f"{cat}:{span!r}" for cat, span in _issue_set(r)) for r in reports],
        "passed_flags": [r.passed for r in reports],
        "distinct_issue_sets": len(distinct_sets),
        "identical_across_all_successful_calls": len(distinct_sets) <= 1,
        "majority_set_agreement": f"{majority_size}/{len(reports)}" if reports else "0/0",
    }


class _FakeAdapter:
    """Same shape as orchestrator/test_stages.py's FakeAdapter -- .complete() never
    touches the network. Each scripted response is popped in order; an Exception
    instance is raised instead of returned. Used only by --self-test, below."""

    def __init__(self, responses: list):
        self._responses = list(responses)

    def complete(self, prompt, *, model, temperature, timeout_seconds,
                output_mode=OutputMode.TEXT, response_schema=None, schema_name=None):
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def _fake_stage_config() -> ResolvedStageConfig:
    return ResolvedStageConfig(
        provider="gemini", model="fake-model", prompt_version="v1", prompt_hash="deadbeef",
        prompt_path=_DEFAULT_PROMPT_PATH, temperature=0.5, timeout_seconds=30.0,
        output_mode=OutputMode.TEXT)


def _completion(report: QualityReport) -> CompletionResult:
    return CompletionResult(text=report.model_dump_json(), prompt_tokens=5, completion_tokens=5,
                            output_mode=OutputMode.TEXT)


def _self_test() -> int:
    """Offline check, no network call, no API key needed: exercises
    run_stability_check's own logic (issue-set comparison, failure tolerance, majority
    agreement) against a scripted FakeAdapter, so the harness's correctness doesn't
    depend on having live API access at build time."""
    req = Requirement(id="REQ-1", text="The system shall respond quickly.")
    req_set = RequirementSet(doc_id="DOC-1", requirements=[req])
    prompt_template = _DEFAULT_PROMPT_PATH.read_text()

    vague_issue = Issue(id="I1", category=IssueCategory.AMBIGUOUS_TERM, span="quickly",
                        explanation="no measurable threshold")
    same_report = QualityReport(requirement_id="REQ-1", passed=False, issues=[vague_issue])
    clean_report = QualityReport(requirement_id="REQ-1", passed=True, issues=[])

    failures = []

    def check(label, condition):
        print(f"  {'ok' if condition else 'FAIL'}  {label}")
        if not condition:
            failures.append(label)

    # Case 1: five identical reports -> STABLE, distinct_issue_sets == 1.
    fake = _FakeAdapter([_completion(same_report) for _ in range(5)])
    result = run_stability_check(
        req_set, "REQ-1", make_check_quality_fn(fake, _fake_stage_config(), prompt_template),
        SystemType.OTHER, calls=5, output_fn=lambda _msg: None)
    check("identical reports -> STABLE", result["identical_across_all_successful_calls"] is True)
    check("identical reports -> 1 distinct set", result["distinct_issue_sets"] == 1)
    check("all 5 calls succeeded", result["calls_succeeded"] == 5)
    check("majority agreement is 5/5", result["majority_set_agreement"] == "5/5")

    # Case 2: opposite verdicts across calls (Known Limitation 10's actual failure
    # mode) -> UNSTABLE, distinct_issue_sets == 2.
    fake = _FakeAdapter([_completion(same_report), _completion(clean_report),
                        _completion(same_report), _completion(clean_report),
                        _completion(same_report)])
    result = run_stability_check(
        req_set, "REQ-1", make_check_quality_fn(fake, _fake_stage_config(), prompt_template),
        SystemType.OTHER, calls=5, output_fn=lambda _msg: None)
    check("flip-flopping reports -> UNSTABLE",
         result["identical_across_all_successful_calls"] is False)
    check("flip-flopping reports -> 2 distinct sets", result["distinct_issue_sets"] == 2)
    check("majority agreement is 3/5", result["majority_set_agreement"] == "3/5")

    # Case 3: one transport failure tolerated -- the other 4 calls still complete and
    # get compared, rather than the whole run crashing.
    fake = _FakeAdapter([_completion(same_report), StageCallFailed("simulated 429"),
                        _completion(same_report), _completion(same_report),
                        _completion(same_report)])
    result = run_stability_check(
        req_set, "REQ-1", make_check_quality_fn(fake, _fake_stage_config(), prompt_template),
        SystemType.OTHER, calls=5, output_fn=lambda _msg: None)
    check("one transport failure tolerated, not fatal to the run", result["calls_failed"] == 1)
    check("the other 4 calls still ran", result["calls_succeeded"] == 4)
    check("4 identical survivors -> still STABLE", result["identical_across_all_successful_calls"] is True)

    # Case 4: an unknown requirement id is a configuration error, not a crash.
    try:
        run_stability_check(req_set, "NO-SUCH-REQ",
                            make_check_quality_fn(_FakeAdapter([]), _fake_stage_config(), prompt_template),
                            SystemType.OTHER, calls=1, output_fn=lambda _msg: None)
        check("unknown requirement id raises ValueError", False)
    except ValueError:
        check("unknown requirement id raises ValueError", True)

    total = 11
    print(f"\nself-test: {total - len(failures)}/{total} checks passed")
    return EXIT_SUCCESS if not failures else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("requirement_set", type=Path, nargs="?",
                        help="Path to a RequirementSet JSON file")
    parser.add_argument("requirement_id", nargs="?", help="Which requirement in that set to check")
    parser.add_argument("--self-test", action="store_true",
                        help="Offline check of this script's own logic against a scripted "
                             "FakeAdapter. No network call, no API key, no other arguments needed.")
    parser.add_argument("--calls", type=int, default=5, help="Number of repeat calls (default 5)")
    parser.add_argument("--prompt", type=Path, default=_DEFAULT_PROMPT_PATH,
                        help="Quality Checker prompt file (default: orchestrator/example_prompts/quality_checker.txt)")
    parser.add_argument("--provider", choices=["gemini", "groq"], default="gemini")
    parser.add_argument("--model", default="gemini-3.6-flash")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--system-type", choices=[t.value for t in SystemType], default="other",
                        help="Placeholder Classification.system_type -- the Classifier is not run")
    parser.add_argument("--output", type=Path, help="Optional path to write the JSON report to")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if not args.requirement_set or not args.requirement_id:
        parser.error("requirement_set and requirement_id are required unless --self-test is given")

    if args.calls < 1:
        print(f"--calls must be >= 1 (got {args.calls})")
        return EXIT_CONFIG_ERROR

    try:
        requirement_set = RequirementSet.model_validate_json(args.requirement_set.read_text())
        prompt_template = args.prompt.read_text()
        adapter: ProviderAdapter = _ADAPTER_FACTORIES[args.provider]()
    except (OSError, ValueError, RuntimeError) as e:
        print(f"Configuration/input error: {e}")
        return EXIT_CONFIG_ERROR

    stage_config = ResolvedStageConfig(
        provider=args.provider, model=args.model, prompt_version="stability-harness",
        prompt_hash="unused", prompt_path=args.prompt, temperature=args.temperature,
        timeout_seconds=args.timeout_seconds, output_mode="text")
    check_quality = make_check_quality_fn(adapter, stage_config, prompt_template)

    print(f"Quality Checker stability: {args.requirement_id} x {args.calls} call(s) "
         f"({args.provider}/{args.model}, temperature={args.temperature})")
    try:
        result = run_stability_check(
            requirement_set, args.requirement_id, check_quality,
            SystemType(args.system_type), args.calls)
    except ValueError as e:
        print(f"Configuration/input error: {e}")
        return EXIT_CONFIG_ERROR

    print(f"\n{result['calls_succeeded']}/{result['calls_requested']} call(s) succeeded "
         f"({result['calls_failed']} failed).")
    print(f"Distinct issue sets seen: {result['distinct_issue_sets']} "
         f"({'STABLE' if result['identical_across_all_successful_calls'] else 'UNSTABLE'})")
    print(f"Majority-set agreement: {result['majority_set_agreement']}")

    if args.output:
        args.output.write_text(json.dumps(result, indent=2))
        print(f"Full report written to {args.output}")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
