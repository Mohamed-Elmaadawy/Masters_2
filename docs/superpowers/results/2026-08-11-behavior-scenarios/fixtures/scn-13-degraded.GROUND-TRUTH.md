# scn-13-degraded — ground truth

Spec: `docs/superpowers/plans/2026-08-11-behavior-scenarios.md`, S13.

## Source

**Config-only scenario — no new fixture file.** Reuses `fixtures/scn-01-dep-pair.json`
unchanged (see that file's sibling `scn-01-dep-pair.GROUND-TRUTH.md` for its own
source/provenance). Only the run config differs: `configs/scn-13-degraded.yaml`
overrides `consistency_checker`'s model to `gemini-nonexistent-model-x9` (provider
stays `gemini`), so the real Gemini adapter fails fatally on that stage's first call.
`rate_limits` carries an explicit entry for the bogus model
(`gemini/gemini-nonexistent-model-x9`), required because `RunConfig` treats a missing
rate-limit key as a config error on purpose — a model must never be silently
unthrottled by omission, not even one that will never actually make a successful call.

## Why this resolves offline without an API call

`orchestrator/providers/capabilities.py`'s `supports_output_mode` returns `True`
unconditionally for `OutputMode.TEXT` regardless of model name — the capability gate
only restricts JSON output modes. Since this config's `output_mode` is `text`
(inherited from `defaults`, unchanged from `runs_gemini.yaml`), `resolve_run_config`
resolves this config successfully even though `gemini-nonexistent-model-x9` does not
exist: the model-name check only happens at the real adapter call, not at config
resolution. Confirmed by running `resolve_run_config` on this file — see
`docs/superpowers/results/2026-08-11-behavior-scenarios/RUNBOOK.md`'s validation
section.

## Ground truth

Probes ORCHESTRATOR_CONTRACT.md item 8 and item 16's `None`-vs-`[]` distinction
through a **real** provider adapter, not `test_harness.py`'s fakes: that a real
adapter classifies a real provider rejection (unknown model) as `StageCallFatal`, and
that the resulting `None` genuinely reaches the Quality Checker's prompt.

## Hard (deterministic, machine-checkable)

- `DocumentOutcome.DEGRADED`.
- `consistency_report is None`; `dependency_report` present.
- One `DocumentStageError` with `kind=FailureKind.FATAL` and `retry_count == 0`
  (ORCHESTRATOR_CONTRACT.md item 17 — **exactly one attempt**, not `max_attempts`).
- `relevant_conflicts is None` while `relevant_dependencies == []` or a real list.
- Processing continues to completion for both requirements
  (`PURE-ERTMS-R7`/`PURE-ERTMS-R8`).

## Soft

None. This scenario is entirely about record shape.

**Also worth checking here:** that `retry_document_stage` refuses once any requirement
has been processed (ORCHESTRATOR_CONTRACT.md item 6). `test_harness.py` tests this
with fakes; this run confirms the guard is wired into the path the CLI actually takes.
