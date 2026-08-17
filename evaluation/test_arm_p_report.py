"""
Regression tests for evaluation/arm_p_report.py (Task 6 review round 3, finding 3,
2026-08-17). Run after any change:

    python -m evaluation.test_arm_p_report

Plain script, no pytest, no network, no API call. Reads one real historical run
directory under docs/superpowers/results/ (read-only -- never modified) as a smoke
test that this module works against actual persisted data, not just synthetic
fixtures.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from design.schemas import (
    ALL_STAGES, AttemptResult, DocumentRunRecord, DocumentStage, DocumentStageAttempt,
    FailureKind, PipelineStage, Requirement, RequirementRunRecord, RequirementSet,
    RunMetadata, StageAttempt, StageConfig, StageError, prompt_fingerprint,
)
from evaluation.arm_p_report import arm_p_wall_clock_seconds, compute_arm_p_cost
from evaluation.pricing import FROZEN_PRICING_SNAPSHOT, PricingSnapshot
from orchestrator.pipeline import read_document_run

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


_STAGE_CONFIGS = {
    s: StageConfig(model="fake-model", prompt_hash=prompt_fingerprint(f"prompt for {s}"),
                   prompt_version="test-v1")
    for s in ALL_STAGES
}


def _metadata() -> RunMetadata:
    return RunMetadata(run_id="arm-p-test", started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                       stages=_STAGE_CONFIGS)


section("compute_arm_p_cost -- sums document-level AND per-requirement attempts")
record = DocumentRunRecord(
    requirement_set=RequirementSet(doc_id="DOC-1", requirements=[
        Requirement(id="REQ-1", text="The system shall respond within 2 seconds.")]),
    metadata=_metadata(),
    attempts=[
        DocumentStageAttempt(stage=DocumentStage.CONSISTENCY_CHECKER, invocation_id="inv-1",
                             attempt_number=1, result=AttemptResult.SUCCESS,
                             prompt_tokens=1_000_000, completion_tokens=200_000),
    ],
    requirement_records=[
        RequirementRunRecord(
            requirement=Requirement(id="REQ-1", text="The system shall respond within 2 seconds."),
            run_id="arm-p-test",
            attempts=[
                StageAttempt(stage=PipelineStage.QUALITY_CHECKER, invocation_id="inv-2",
                            attempt_number=1, result=AttemptResult.SUCCESS,
                            prompt_tokens=500_000, completion_tokens=100_000),
                # A transport failure never billed -- carries no tokens at all.
                StageAttempt(stage=PipelineStage.CLASSIFIER, invocation_id="inv-3",
                            attempt_number=1, result=AttemptResult.TRANSPORT_FAILURE,
                            error_message="429"),
            ],
            errors=[StageError(stage=PipelineStage.CLASSIFIER, invocation_id="inv-3",
                               kind=FailureKind.TRANSPORT, message="429")]),
    ],
)
report = compute_arm_p_cost(record)
ok("prompt tokens sum across document-level AND requirement-level attempts",
  report.total_prompt_tokens == 1_500_000)
ok("completion tokens sum across both too", report.total_completion_tokens == 300_000)
ok("cost = 1.5M*$1.50/1M + 0.3M*$7.50/1M = $4.50", abs(report.total_cost_usd - 4.50) < 1e-9)
ok("two attempts counted as having tokens (document + requirement SUCCESS)",
  report.attempts_with_tokens == 2)
ok("one attempt counted as tokenless (the transport failure)",
  report.attempts_without_tokens == 1)
ok("the SAME frozen pricing snapshot B1/B2 use is reused, not a separate rate",
  report.pricing == FROZEN_PRICING_SNAPSHOT)

section("compute_arm_p_cost -- a custom pricing snapshot can be passed explicitly, "
       "same contract as evaluation/pricing.py's compute_cost")
custom = PricingSnapshot(source="hypothetical", captured_date="2099-01-01",
                         usd_per_million_input_tokens=0.0, usd_per_million_output_tokens=100.0)
custom_report = compute_arm_p_cost(record, pricing=custom)
ok("a custom snapshot changes the computed cost", custom_report.total_cost_usd == 30.0)

section("compute_arm_p_cost -- a document with zero requirement_records (still valid "
       "on disk per DocumentRunRecord's own D2b on-disk layout) does not crash")
empty_record = DocumentRunRecord(
    requirement_set=RequirementSet(doc_id="DOC-2", requirements=[
        Requirement(id="REQ-1", text="x")]),
    metadata=_metadata(), attempts=[], requirement_records=[])
empty_report = compute_arm_p_cost(empty_record)
ok("zero attempts anywhere produces zero cost, not an error",
  empty_report.total_cost_usd == 0.0 and empty_report.total_prompt_tokens == 0)

section("arm_p_wall_clock_seconds -- always None, honestly, not fabricated")
ok("returns None for a record with real attempts", arm_p_wall_clock_seconds(record) is None)
ok("returns None for an empty record too (same, not data-dependent)",
  arm_p_wall_clock_seconds(empty_record) is None)

section("Smoke test against REAL historical data (read-only -- never modified)")
real_run_dir = Path("docs/superpowers/results/2026-08-10-first-real-run/groq")
if real_run_dir.exists():
    real_record = read_document_run(real_run_dir)
    real_report = compute_arm_p_cost(real_record)
    ok("a real historical run's attempts produce a non-negative, computable cost",
      real_report.total_cost_usd >= 0.0)
    ok("attempts_with_tokens + attempts_without_tokens accounts for every real attempt",
      real_report.attempts_with_tokens + real_report.attempts_without_tokens ==
      len(real_record.attempts) + sum(len(r.attempts) for r in real_record.requirement_records))
    ok("wall-clock is still honestly None for real historical data too",
      arm_p_wall_clock_seconds(real_record) is None)
else:
    ok(f"real historical run directory not found at {real_run_dir} -- skipping smoke test "
      "(not a failure of this module)", True)


print(f"\n{PASSED}/{PASSED + len(FAILED)} checks passed")
if FAILED:
    print("FAILED:")
    for label in FAILED:
        print(f"  - {label}")

import sys
sys.exit(0 if not FAILED else 1)
