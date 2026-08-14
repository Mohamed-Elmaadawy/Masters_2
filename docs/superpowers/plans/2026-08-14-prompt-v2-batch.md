# Prompt v2 — one batch, four scenarios, pre-registered predictions

Every change here is prompt-only. No schema, no orchestrator, no fixtures. All are applied
**together** so a single re-run measures the batch; applying them one at a time triples the
cost and lets effects mask each other.

Provenance is automatic: `prompt_hash` is recorded per attempt, so v1 and v2 runs are
distinguishable in the record without any extra bookkeeping.

---

## 1. What changes

### A. Quality Checker — `non_atomic` definition (Known Limitation 8)

`orchestrator/example_prompts/quality_checker.txt`, current text:

> - "non_atomic": the requirement bundles more than one testable behavior into
>   one statement.

Proposed:

> - "non_atomic": the requirement bundles more than one **independently** testable
>   behavior into one statement — behaviors that could be specified, implemented and
>   tested separately, in any order, without one depending on the other.
>   Example (non_atomic): "The system shall generate reports on inventory levels,
>   product movement, and sales history" — three separate reports, three separate tests.
>   Example (NOT non_atomic): "the process is activated and makes a request", or
>   "shall identify the value as invalid and shall output an invalid status" — one
>   causal or sequential step described in two clauses. A conjunction is not by itself
>   evidence of bundling.

Evidence: of 5 `non_atomic` flags in the 2026-08-13 suite, `LUITEL-R7` was a correct catch and
two (`THEMAS-REQ-B`, `PURE-ERTMS-R2`) were conjunction-splits of causal chains.

### B. Refiner Rewriter — three "do not change the text" rules (Known Limitation 11)

`orchestrator/example_prompts/refiner_rewriter.txt`, appended to the existing Rules block:

> - If the requirement **already states a measurable value** for the property an answer
>   is about, leave that value alone and do not add a placeholder beside it.
> - If an answer supplies **no** concrete value, actor or condition, make **no change** —
>   return the original text unchanged. Do not write text that only defers the decision
>   ("as defined by the product owner", "per the applicable standard"): that reads like a
>   specification without being one, and it is worse than leaving the gap visible.
> - If an answer states the requirement is **already correct**, or that the fix belongs to a
>   different requirement, return the original text **byte-for-byte unchanged**, including
>   units, formatting and abbreviations.

Evidence, one instance each: `LUITEL-R1` got `[TBD: measurable value]` beside its own `5s`;
`AUTOGEN-US2` became "…according to metrics defined by the product owner"; `PURE-THEMAS-R6`
was reformatted "3 degrees Fahrenheit" -> "3°F" after the human said not to change it.

### C. Deliberately NOT in this batch — system-type definitions (Known Limitation 9)

The `infeasible_for_type` prompt fix is written up and still correct, but the category fired
**zero** times across 34 requirements in the 2026-08-13 suite. Including it would add a
variable whose effect cannot be observed in this re-run, muddying attribution for A and B.
Ship it with whatever batch follows a run where the category actually fires.

---

## 2. Re-run set, and which answer policy

**Scenarios: `scn-04-conflict-numeric`, `scn-07-dilution`, `scn-10-atomicity`,
`scn-12-routing`** — the four containing every affected requirement. 15 requirement-slots,
about **$0.38** at the measured rate of ~$0.025/requirement.

**Run with the refusing policy (`answer_policy_driver.py`), not the live transcript.** This is
the point most likely to be got wrong:

- the 2026-08-13 suite baseline used the refusing policy and covers all four scenarios; the
  live-human baseline covers only two of them;
- the refusing policy is deterministic, so **the prompt is the only variable that changed**.

Replaying the frozen live transcript against v2 prompts is a *different* experiment (does a
prompt change alter what a human's answers achieve?) and should not be mixed into this one. The
drift-warning threshold discussed earlier applies to *that* run, not this one — v2 prompts will
reword questions, and the transcript is keyed on requirement + issue category, so drift
warnings are expected there and meaningless here.

---

## 3. Predictions, recorded before the run

Stated in advance so the re-run confirms or refutes rather than being read in hindsight.

**A — `non_atomic`:**

1. `THEMAS-REQ-B` (scn-12) is **no longer** flagged `non_atomic`.
2. `PURE-ERTMS-R2` (scn-07) is **no longer** flagged `non_atomic`.
3. `LUITEL-R7` (scn-10) **is still** flagged. This is the control: if it stops firing, the
   definition over-corrected and the fix is worse than the bug.

**B — Rewriter:**

4. `LUITEL-R1` (scn-12) keeps its `5s` with no `[TBD]` placeholder attached to it.
5. `AUTOGEN-US2` (scn-10) gets no deferral phrase; expect an unchanged text instead.
6. `PURE-THEMAS-R6` (scn-04) is returned byte-for-byte unchanged — no unit reformatting.

**Regression guards** (any of these means stop and investigate rather than declare success):

7. No new `VALIDATION_FAILURE` attempts at any stage.
8. Outcome mix across the four scenarios does not move by more than one slot per outcome
   against the 2026-08-13 baseline, other than through predictions 1–6.
9. No requirement that previously reached `COMPLETED` now caps, or vice versa, unless
   predictions 1–6 explain it.

## 4. Before running

- Re-run the offline suites after editing the prompts — they are fingerprinted by
  `orchestrator/config.py`, so a malformed file should surface immediately:
  `python -m design.test_schemas`, `python -m orchestrator.test_config`,
  `python -m orchestrator.test_stages`, `python -m orchestrator.test_cli`.
- Commit the prompt edits **before** the run, so the `prompt_hash` in the run records points at
  a committed version of the file.
- Do not touch the fixtures, the configs, or `answer_policy_driver.py`.

## 5. After running

Report per prediction: held / refuted / not exercised. A refuted prediction is a result. Then
fold into `design/DESIGN_NOTES.md` (Known Limitations 8 and 11) and append to
`IMPLEMENTATION_LOG.md` with the before/after counts — an entry saying "improved the prompt"
records nothing.
