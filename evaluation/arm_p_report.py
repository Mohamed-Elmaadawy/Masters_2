"""Arm-P (the full pipeline) cost report -- Task 6 review round 3, finding 3
(2026-08-17). docs/EVALUATION_PROTOCOL.md section 3 requires cost AND wall-clock for
all three arms ("Record cost and wall-clock for all three arms"); evaluation/pricing.py
already covers B1/B2. No complete existing mechanism covers arm P: `orchestrator/
cli.py` prints a raw token total ("Total tokens: N") but never converts it to cost, and
nothing anywhere computes cost at the frozen pricing rate for a pipeline run.

**STATUS: finding 3 is only PARTIALLY resolved by this module (corrected 2026-08-17,
fourth-round finding 3 -- an earlier version of this docstring read as if cost coverage
settled the whole finding; it does not).**

**Cost** is fully recoverable from already-persisted data, so this module computes it:
`design/schemas.py`'s `DocumentRunRecord`/`RequirementRunRecord` already record
`prompt_tokens`/`completion_tokens` per `StageAttempt`/`DocumentStageAttempt` -- no API
call, nothing re-run, just reading what a real orchestrator run already wrote to disk
(e.g. via `orchestrator.pipeline.read_document_run`). This half is done.

**Wall-clock for any run completed so far is NOT recoverable, and this module does not
fabricate it.** Confirmed by reading `design/schemas.py`, not assumed: neither
`StageAttempt` nor `DocumentStageAttempt` records any duration at all, and
`RunMetadata.started_at` is the only timestamp anywhere in the whole pipeline schema --
one value for the entire run, not per call. Adding a wall-clock field there would be a
pipeline-schema change (explicitly out of scope for evaluation-side tooling -- CLAUDE.md:
"do not change pipeline schemas, production prompts"), and even if it were in scope, it
could not retroactively produce a number for a run that already completed without it:
the data was simply never recorded. `arm_p_wall_clock_seconds` below returns `None`,
always, for this reason.

**`None` is a placeholder for "not yet measured", not a satisfied requirement.** It
must NEVER be presented, in the thesis or anywhere else, as satisfying
`docs/EVALUATION_PROTOCOL.md` section 3's three-arm wall-clock comparison. It exists so
a caller building a P/B1/B2 table gets an explicit, checkable "unavailable" instead of a
`KeyError` or a silently-omitted row -- nothing more.

**Before the real (frozen, paid) evaluation run, arm P's wall-clock MUST be measured
externally and persisted alongside its cost report -- for EVERY arm-P document run E2
includes, not a single run.** E2 (`docs/EVALUATION_PROTOCOL.md` section 3) compares the
same documents across all three arms, and arm P persists one `DocumentRunRecord` PER
DOCUMENT (corrected 2026-08-17, fifth-round wording fix -- an earlier version of this
checklist said "the one real arm-P run", which undercounts whenever E2's document set
has more than one document). No pipeline-schema change and no new infrastructure is
needed for this -- a manual procedure, repeated per document, is sufficient at thesis
scale:

1. Immediately before starting arm P on a given document
   (`python -m orchestrator.cli run ...`), record a start timestamp (e.g. `date -u` /
   `Get-Date`, or the shell's own job timer).
2. Immediately after that document's run exits (or is interrupted -- see step 3 for
   resume), record an end timestamp the same way.
3. If that document's run was interrupted and resumed one or more times
   (`python -m orchestrator.cli resume ...`), time each session (start-to-interruption
   or start-to-completion) the same way and SUM the active session durations -- do not
   count idle time between sessions (the gap while the run sat interrupted) as part of
   the document's wall-clock. `wall_clock_seconds` for that document is the total time
   the pipeline was actually running, not the calendar time from first start to final
   completion.
4. Write the resulting per-document `wall_clock_seconds` into the SAME file
   `compute_arm_p_cost`'s `CostReport` for that document's run is saved to -- e.g. one
   `"wall_clock_seconds": <value>` key alongside that document's cost JSON, or a
   one-line sidecar note per document -- so cost and wall-clock for arm P live
   together, the same way `BaselineRunOutput.total_wall_clock_seconds` already lives
   next to B1/B2's cost.
5. Report the measured per-document values (and, if useful, their sum/mean across E2's
   document set) in the thesis's three-arm comparison -- never `None`, and never an
   estimate derived from token counts or `StageAttempt` timing (there is none to
   derive it from).

This is a manual step repeated once per arm-P document run E2 needs, not a single
one-off and not a recurring per-call burden -- building automated wall-clock capture
for it would mean changing `design/schemas.py` (a pipeline-schema change, explicitly
out of scope here) to save what is, per document, one or a few human subtractions.
"""

from __future__ import annotations

from typing import Optional

from design.schemas import DocumentRunRecord
from evaluation.pricing import CostReport, FROZEN_PRICING_SNAPSHOT, PricingSnapshot


def compute_arm_p_cost(
    record: DocumentRunRecord, pricing: PricingSnapshot = FROZEN_PRICING_SNAPSHOT,
) -> CostReport:
    """Same pricing snapshot, same accounting rule as `evaluation/pricing.py`'s
    `compute_cost`: tokens are counted for every attempt that recorded them
    (`StageAttempt`/`DocumentStageAttempt` allow tokens on `SUCCESS` and
    `VALIDATION_FAILURE`, never on `TRANSPORT_FAILURE` -- design/schemas.py's own
    `_attempt_shape_error`), whichever the final outcome. Sums across BOTH the
    document-level attempts (`record.attempts`) and every requirement's own attempts
    (`record.requirement_records[*].attempts`) -- the complete real cost of one
    pipeline run over one document, not just one half of it."""
    all_attempts = list(record.attempts) + [
        a for req in record.requirement_records for a in req.attempts]
    with_tokens = [a for a in all_attempts if a.prompt_tokens is not None]
    without_tokens = [a for a in all_attempts if a.prompt_tokens is None]

    total_in = sum(a.prompt_tokens for a in with_tokens)
    total_out = sum(a.completion_tokens for a in with_tokens)
    cost = ((total_in / 1_000_000) * pricing.usd_per_million_input_tokens
           + (total_out / 1_000_000) * pricing.usd_per_million_output_tokens)

    return CostReport(
        pricing=pricing, total_prompt_tokens=total_in, total_completion_tokens=total_out,
        total_cost_usd=cost, attempts_with_tokens=len(with_tokens),
        attempts_without_tokens=len(without_tokens))


def arm_p_wall_clock_seconds(record: DocumentRunRecord) -> Optional[float]:
    """Always returns `None` for a historical run -- see this module's docstring for
    exactly why, and for the manual measurement procedure required before the real
    evaluation run. `None` is NOT a satisfied wall-clock measurement; it is "not
    available from this record", full stop -- do not report it as arm P's wall-clock
    in the thesis. Takes `record` (unused) so the signature mirrors `compute_arm_p_cost`
    and a caller can call both uniformly when building a comparison table; accepting
    and ignoring the argument is deliberate, not an oversight -- a future
    pipeline-schema change that adds real per-attempt timing has exactly one function
    to update, with a caller contract that does not need to change."""
    return None
