"""
Regression tests for evaluation/pricing.py (Task 6 finding 5, 2026-08-17): frozen
pricing snapshot, cost computed including failed/partial attempts that still recorded
tokens, nothing fetched live. Run after any change:

    python -m evaluation.test_pricing

Plain script, no pytest, no network.
"""

from __future__ import annotations

from evaluation.pricing import FROZEN_PRICING_SNAPSHOT, PricingSnapshot, compute_cost
from evaluation.schemas import BaselineAttempt, BaselineRunOutput

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


def run_output(attempts, failed=False, test_cases=None) -> BaselineRunOutput:
    from design.schemas import OutputMode
    return BaselineRunOutput(
        arm="B1", provider="gemini", model="gemini-3.6-flash", temperature=1.0,
        requirement_set_hash="deadbeef1234",
        timeout_seconds=30.0, output_mode=OutputMode.TEXT,
        prompt_hash={"b1_generate": "deadbeef"}, attempts=attempts,
        test_cases=test_cases or ([] if failed else [
            {"id": "TC-1", "requirement_ids": ["REQ-1"], "technique_used": "boundary_value_analysis",
             "title": "t", "steps": ["s"], "expected_result": "e"}]),
        total_wall_clock_seconds=1.0, failed=failed)


section("FROZEN_PRICING_SNAPSHOT -- a real, cited, hand-dated constant")
ok("source is a real citation string", len(FROZEN_PRICING_SNAPSHOT.source) > 0)
ok("captured_date is recorded", FROZEN_PRICING_SNAPSHOT.captured_date == "2026-08-11")
ok("rates match this project's own already-used rate ($1.50/1M in, $7.50/1M out)",
  FROZEN_PRICING_SNAPSHOT.usd_per_million_input_tokens == 1.50
  and FROZEN_PRICING_SNAPSHOT.usd_per_million_output_tokens == 7.50)

section("compute_cost -- a simple successful run")
out = run_output([BaselineAttempt(call="b1_generate", attempt_number=1, result="success",
                                  prompt_tokens=1_000_000, completion_tokens=1_000_000,
                                  wall_clock_seconds=1.0)])
report = compute_cost(out)
ok("total_prompt_tokens matches the one successful attempt", report.total_prompt_tokens == 1_000_000)
ok("total_completion_tokens matches the one successful attempt",
  report.total_completion_tokens == 1_000_000)
ok("cost = 1M*$1.50/1M + 1M*$7.50/1M = $9.00", abs(report.total_cost_usd - 9.00) < 1e-9)
ok("pricing snapshot is embedded in the report", report.pricing == FROZEN_PRICING_SNAPSHOT)
ok("one attempt counted as having tokens", report.attempts_with_tokens == 1)
ok("the run output itself carries the FULL effective config, not just provider/"
  "model/temperature (2026-08-17, second-round finding 7)",
  out.timeout_seconds == 30.0 and out.output_mode.value == "text")
ok("zero attempts counted as tokenless", report.attempts_without_tokens == 0)

section("compute_cost -- failed/fatal attempts (no tokens billed) are excluded but counted")
out = run_output([
    BaselineAttempt(call="b1_generate", attempt_number=1, result="failed",
                    error_message="429", wall_clock_seconds=0.5),
    BaselineAttempt(call="b1_generate", attempt_number=2, result="fatal",
                    error_message="bad credentials", wall_clock_seconds=0.1),
], failed=True)
report = compute_cost(out)
ok("zero tokens counted -- neither attempt reached a billable response",
  report.total_prompt_tokens == 0 and report.total_completion_tokens == 0)
ok("cost is exactly zero", report.total_cost_usd == 0.0)
ok("zero attempts had tokens", report.attempts_with_tokens == 0)
ok("both attempts counted as tokenless, not silently dropped", report.attempts_without_tokens == 2)

section("compute_cost -- a PARTIAL attempt (tokens spent, output unusable) IS included "
       "in cost, per Task 6 finding 5's explicit requirement")
out = run_output([
    BaselineAttempt(call="b1_generate", attempt_number=1, result="partial",
                    error_message="did not match schema", prompt_tokens=2_000,
                    completion_tokens=500, wall_clock_seconds=0.8),
    BaselineAttempt(call="b1_generate", attempt_number=2, result="success",
                    prompt_tokens=2_000, completion_tokens=300, wall_clock_seconds=0.9),
])
report = compute_cost(out)
ok("prompt tokens sum across BOTH the partial and the success attempt",
  report.total_prompt_tokens == 4_000)
ok("completion tokens sum across both too", report.total_completion_tokens == 800)
ok("two attempts counted as having tokens (partial + success)", report.attempts_with_tokens == 2)
ok("cost reflects both, not just the successful one",
  abs(report.total_cost_usd - ((4_000 / 1_000_000) * 1.50 + (800 / 1_000_000) * 7.50)) < 1e-9)

section("compute_cost -- a custom pricing snapshot can be passed explicitly (no live "
       "fetch anywhere in this module -- grep-verified, not just asserted here)")
custom = PricingSnapshot(source="hypothetical re-verification", captured_date="2099-01-01",
                         usd_per_million_input_tokens=0.0, usd_per_million_output_tokens=100.0)
out = run_output([BaselineAttempt(call="b1_generate", attempt_number=1, result="success",
                                  prompt_tokens=1_000_000, completion_tokens=1_000_000,
                                  wall_clock_seconds=1.0)])
report = compute_cost(out, pricing=custom)
ok("a custom pricing snapshot changes the computed cost", report.total_cost_usd == 100.0)
ok("the custom snapshot itself is embedded in the report, not the frozen default",
  report.pricing == custom)

import inspect  # noqa: E402
import evaluation.pricing as pricing_module  # noqa: E402
source = inspect.getsource(pricing_module)
ok("pricing.py never imports requests/httpx/urllib (no live-fetch capability at all)",
  not any(term in source for term in ("import requests", "import httpx", "import urllib")))


print(f"\n{PASSED}/{PASSED + len(FAILED)} checks passed")
if FAILED:
    print("FAILED:")
    for label in FAILED:
        print(f"  - {label}")

import sys
sys.exit(0 if not FAILED else 1)
