# Results — pure-peering smoke test

**Smoke test, not a measurement.** Confirms the pipeline runs end-to-end on a freshly
extracted PURE document. Predictions were written and committed before this run
(`PREDICTIONS.md`, commit `6a16957`) — compared against actual output below, not
rationalized after seeing it.

Run: `docs/superpowers/results/2026-08-14-pure-peering-smoke/configs/runs_pure-peering-smoke/pure-peering-smoke/`.
Input: `datasets/pure-extracted/pure-peering.json`, 24 requirements. PAID Gemini key,
`gemini-3.6-flash`, temperature 1.0, v2 prompts as committed, scripted reasoned-decline
answer policy, via `paid_gemini_driver.py` unchanged. Exit code 0.

## Prediction-by-prediction

| # | Predicted | Actual | Verdict |
|---|---|---|---|
| 1 | First-pass Quality Checker clean: 10-20% (2-5/24) | **0/24 (0%)** | **Refuted** — worse than themas's 25% (2/8), not just lower |
| 2 | Duplicate texts not flagged `inconsistent` | `consistency_report.conflicts` is `[]`; none of the 7 duplicate-text ids appear anywhere in a conflict | **Held** |
| 3 | Classifier mostly `other` (20+/24) | **17/24 `other`, 7/24 `web`** | **Partially refuted** — majority holds, but 7/24 is a real minority, not a one-off |
| 4 | Zero id-mismatches, high confidence | **0/24** single-value `requirement_id` mismatches across classification/quality_report/test_strategy/test_plan | **Held** |
| 5 | Majority `cap_stopped`/`completed`, ≤1-2 `error` | **22 `cap_stopped`, 2 `completed`, 0 `error`** | **Held** |
| 6 | 130-170 attempts, 180k-260k tokens | **192 real calls (190 req-level + 2 doc-level), 310,542 tokens** | **Refuted** — above the upper bound on both counts |

## Outcome mix

`completed`: `PURE-PEERING-0004`, `PURE-PEERING-0012` (2/24). Every other requirement
(`22/24`) reached `cap_stopped` — the scripted policy's conservative decline to certify
outstanding issues, not a pipeline failure, consistent with both prior themas runs.
Zero `error` outcomes.

## Schema-validation failures per stage

**Zero, everywhere.** All 192 real API calls (24 classifier, 70 quality_checker, 46
refiner_questioner, 46 refiner_rewriter, 2 strategy_selector, 2 test_generator, plus
the 2 document-level calls) succeeded on the first attempt — `result: success` on
every single one, no `validation_failure`, no `transport_failure`, no retries needed.
Cleaner than the 2026-08-10 themas runs (which were also zero-validation-failure but
did retry on transport issues on Groq) and at 3x the requirement count and ~2.75x the
document-level payload of the largest prior run.

## Real token cost

**310,542 total** (`document_stage_tokens` 3,350 + 307,192 across requirements).
Consistency checker: 1,428 prompt + 17 completion tokens. Dependency mapper: 1,587
prompt + 318 completion tokens. ~3.5x themas's 89,173-token run for 3x the
requirements — the per-requirement cost did not scale down with document size the
way the prediction assumed.

## The three watch items

**Duplicate-text requirements.** Three distinct duplicated texts, seven requirements
involved: `{0001, 0002, 0010}`, `{0003, 0006}`, `{0023, 0024}`. Confirmed: the
Consistency Checker raised zero conflicts touching any of them — duplicates are
neither merged nor flagged, consistent with Known Limitation 1 (nothing in the design
deduplicates). Round-1 Quality Checker verdicts were identical within each duplicate
group (`['incomplete']` for all of `0001`/`0002`/`0010`/`0003`/`0023`/`0024`), but
`0003` and `0006` (same source text) diverged by round 2 — `0006` picked up
`non_verifiable` alongside `incomplete` where `0003` did not. Both requirements
answer the same scripted policy the same way each round, so the divergence traces to
temperature-1.0 sampling variance on the Quality Checker call itself, not to anything
in the refinement loop. Worth recording as a live instance of the variance
`docs/superpowers/plans/2026-08-14-evaluation-design.md` section 4 already flags as a
finding ("the Quality Checker has been observed giving opposite verdicts on identical
text") — this is the same phenomenon on nearly-identical text, one step removed.

**Classifier on postcondition-style text.** 17/24 `other`, 7/24 `web`
(`0003`, `0006`, `0007`, `0008`, `0009`, `0015`, `0021`). Worth noting: the duplicate
pair `{0003, 0006}` (identical source text) got the *same* classification both times
— unlike the Quality Checker's round-2 divergence on the same pair, the Classifier was
consistent here. This is new evidence against Known Limitation 9's stated
premise ("every real run classified `other`") — the first run on record where a
non-trivial share landed on a different `SystemType`. Not a large enough sample to
settle Limitation 9 either way (that needs the Q2 study), but the premise as currently
worded in CLAUDE.md/DESIGN_NOTES.md is now measurably wrong and should be corrected
there, separately from this smoke test.

**Id round-trip.** Clean. Every `classification.requirement_id`,
`quality_report.requirement_id` (every round), `test_strategy.requirement_id`, and
`test_plan.requirement_id` equals the requirement's own id, for all 24. One thing
that is *not* a mismatch, checked against the schema before concluding otherwise:
`PURE-PEERING-0012`'s `TestPlan` has a test case (`TC-17-PURE-PEERING-0012-3`) whose
`requirement_ids` list is `["PURE-PEERING-0012", "PURE-PEERING-0013"]`. `TestCase`
allows this by design (`design/schemas.py` line ~634: "a single test case can
legitimately verify more than one requirement at once"), and `dependency_report`
independently reported `PURE-PEERING-0013 -> PURE-PEERING-0012` as a real dependency.
The test_strategy's own rationale names it explicitly: "protocol interactions ...
serve as a dependency for operational flows like PURE-PEERING-0013, making 'use_case'
testing highly appropriate." **This is direct evidence the Test Generator used
dependency context**, bearing on the open question CLAUDE.md's Known-open list
attaches to Limitations 1, 6 and 7 ("whether the Test Generator uses dependency
context at all — n=1 to date"). This run makes it n=2, both showing dependency-aware
generation. Not a resolution of those limitations — a second data point toward one.

## Extraction artifacts

None found. `pure-peering.manifest.json` records zero bullet items and zero
id-prefix-stripping for this document, so neither of the two known extraction risk
shapes (flattened lists, mangled id-fused text) applies here — there was nothing of
that kind for the pipeline to receive. Spot-checked the two `completed` requirements'
final text against their original: identical (the Rewriter made no change under the
refusing policy, per Known Limitation 10) — no glued text, no stray characters, ids
throughout the output consistently `PURE-PEERING-0001`..`0024`.

## Bottom line

The pipeline ran a freshly extracted PURE document end to end, 3x the size of
anything run before, with zero schema-validation failures, zero transport failures,
zero id-mismatches. Two predictions held cleanly (duplicates ignored, id agreement),
one held loosely (outcome mix), two were refuted in ways worth carrying forward: the
first-pass clean rate was worse than the one prior data point suggested (0/24, not
2-5/24), and both attempts and tokens ran higher than predicted. The Classifier
result is the most consequential surprise — it bears on an open Known Limitation, not
just this run's own cost. **This is a smoke test result, not an evaluation result: n=1,
no ground truth, not blinded, and not part of the Q1/Q2 design.** Do not scale up to a
larger document without discussing this first, per instructions.
