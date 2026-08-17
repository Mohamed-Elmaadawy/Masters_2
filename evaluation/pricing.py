"""Frozen pricing snapshot for baseline-arm cost estimation (Task 6 finding 5,
2026-08-17). NEVER fetched live during a run -- the rate is a hand-verified constant,
re-checked and re-dated by hand when needed, not queried from a provider API or a
pricing page at run time. The snapshot itself is persisted alongside every computed
cost so a later reader knows exactly which rate produced which number, without having
to trust that "the rate" was constant across the whole evaluation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from evaluation.schemas import BaselineRunOutput


class PricingSnapshot(BaseModel):
    source: str
    captured_date: str  # ISO date, hand-recorded -- see design/DESIGN_NOTES.md's ban
                        # on datetime.now() for anything that must stay reproducible
    usd_per_million_input_tokens: float = Field(..., ge=0.0)
    usd_per_million_output_tokens: float = Field(..., ge=0.0)


# This project's own already-cited, already-used rate -- see
# docs/superpowers/results/2026-08-11-behavior-scenarios/RESULTS.md and
# docs/superpowers/results/2026-08-14-live-answers/{SESSION,RESULTS-V2-LIVE}.md, both of
# which computed cost from real recorded prompt_tokens/completion_tokens at this exact
# rate. Reused here, not re-fetched -- update this constant by hand, with a fresh
# `captured_date` and a fresh citation, if the rate is ever re-verified.
FROZEN_PRICING_SNAPSHOT = PricingSnapshot(
    source="docs/superpowers/results/2026-08-11-behavior-scenarios/RESULTS.md",
    captured_date="2026-08-11",
    usd_per_million_input_tokens=1.50,
    usd_per_million_output_tokens=7.50,
)


class CostReport(BaseModel):
    pricing: PricingSnapshot
    total_prompt_tokens: int = Field(..., ge=0)
    total_completion_tokens: int = Field(..., ge=0)
    total_cost_usd: float = Field(..., ge=0.0)
    attempts_with_tokens: int = Field(..., ge=0)     # success + partial: tokens were spent
    attempts_without_tokens: int = Field(..., ge=0)  # failed/fatal: request never billed


def compute_cost(
    run_output: BaselineRunOutput, pricing: PricingSnapshot = FROZEN_PRICING_SNAPSHOT,
) -> CostReport:
    """Sums tokens across EVERY attempt that actually spent them -- `result="success"`
    AND `result="partial"` both had tokens billed by the provider (a partial attempt
    got real output that failed to parse/validate; the call still happened and the
    provider still charged for it). `result` in `("failed", "fatal")` never reached a
    billable response (`BaselineAttempt`'s own validator guarantees those have no
    `prompt_tokens`/`completion_tokens` at all), so they contribute nothing and are
    counted separately rather than silently ignored."""
    with_tokens = [a for a in run_output.attempts if a.prompt_tokens is not None]
    without_tokens = [a for a in run_output.attempts if a.prompt_tokens is None]

    total_in = sum(a.prompt_tokens for a in with_tokens)
    total_out = sum(a.completion_tokens for a in with_tokens)
    cost = ((total_in / 1_000_000) * pricing.usd_per_million_input_tokens
           + (total_out / 1_000_000) * pricing.usd_per_million_output_tokens)

    return CostReport(
        pricing=pricing, total_prompt_tokens=total_in, total_completion_tokens=total_out,
        total_cost_usd=cost, attempts_with_tokens=len(with_tokens),
        attempts_without_tokens=len(without_tokens))
