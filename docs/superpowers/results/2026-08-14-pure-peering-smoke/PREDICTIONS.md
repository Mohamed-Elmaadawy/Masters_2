# Predictions — pure-peering smoke test (written before running)

**This is a smoke test, not a measurement.** Nothing below is an evaluation result;
it exists so a refuted prediction is visible as a refutation, not rationalized after
the fact. Input: `datasets/pure-extracted/pure-peering.json`, 24 requirements, ~700
tokens of consistency-checker payload. Config:
`docs/superpowers/results/2026-08-14-pure-peering-smoke/configs/pure-peering-smoke.yaml`
(PAID Gemini key, `gemini-3.6-flash`, temperature 1.0, v2 prompts as currently
committed, scripted reasoned-decline answer policy via
`docs/superpowers/results/2026-08-10-gemini-paid-tier-run/paid_gemini_driver.py`,
unchanged). Largest document run through this pipeline before today:
`themas-fischbach2022`, 8 requirements, ~400 tokens of payload.

## Grounding used (checked, not guessed)

Read directly from `docs/superpowers/results/2026-08-10-gemini-paid-tier-run/gemini/
requirements/*.json` (same model, same PAID key, v1 prompts, 8 well-formed
shall-statement requirements): `issues_per_round` was `[0]` for 2/8
(`THEMAS-REQ-G`, `THEMAS-REQ-C`) — **25% clean on the very first Quality Checker
pass.** The other 6/8 all got 1-2 issues on round 1. `NOTE.md` for the same run: 8/8
classifier attempts succeeded, zero validation failures, zero transport failures,
89,173 tokens total, 57 attempts total (~7.1 attempts/requirement including the 2
document-level calls).

## Predictions

1. **First-pass Quality Checker clean rate: lower than themas's 25%, call it
   10-20% (2-5 of 24).** Reasoning: `pure-peering`'s texts are use-case
   postconditions ("Malicious requests are detected and rejected"), not
   shall-statements — no actor/trigger/threshold structure to anchor
   verifiability or completeness checks against, so I expect more
   `non_verifiable`/`incomplete`/`ambiguous_term` flags than themas's engineering
   prose drew.

2. **The 3 verbatim-duplicate-text pairs will NOT be flagged as `inconsistent`
   by the Consistency Checker.** Reasoning: `inconsistent` per the prompt/schema
   means two requirements disagree; identical text cannot disagree with itself.
   I expect either silence (no flag involving either id) or, if flagged at all,
   something outside the `IssueCategory` enum entirely (since nothing there
   targets "duplicate," per Known Limitation 1). I do NOT expect the pipeline to
   deduplicate or merge them — nothing in the design does that.

3. **The Classifier will call most or all of the 24 `other`.** Reasoning:
   postcondition-style security/network texts carry no strong `WEB`/`MOBILE`
   signal, and Known Limitation 9 already found every real run to date
   classifies `other`. Predict 20+/24 `other`; if a `WEB`/`MOBILE` shows up at
   all, that is itself worth noting since it would be new evidence against
   Limitation 9's premise.

4. **All 24 requirement ids (`PURE-PEERING-0001`..`0024`) round-trip
   cross-stage with zero id-mismatch `VALIDATION_FAILURE`s.** Reasoning: zero
   id-mismatches have been recorded across every real run to date (2026-08-10
   Groq/Gemini, 2026-08-11 behavior scenarios) — this is the one prediction I
   expect to hold with high confidence, not a coin-flip.

5. **Outcome mix: majority `cap_stopped` or `completed`, at most 1-2
   `error`.** Reasoning: the scripted answer policy is conservative
   (never certifies an issue resolved except `non_atomic`), so most
   requirements needing revision will ride out to the cap rather than
   completing early — same pattern as both themas runs (5/8 and 4/8
   `cap_stopped` respectively). `error` should stay rare; this run's
   payload (~700 tokens/call) is well within what has run cleanly before,
   so I don't expect transport/rate-limit failures the way Groq's TPM
   budget produced them.

6. **Rough cost: 130-170 total attempts, roughly 180,000-260,000 tokens
   (~2-3x themas's 57 attempts / 89,173 tokens, scaled for 3x the
   requirements plus a modestly larger document-level payload).** This is
   a wide range on purpose — I have one prior data point, not a trend.

## What would surprise me enough to stop and report before drawing conclusions

- Any `VALIDATION_FAILURE` at all (schema parse failure) — zero have been seen
  on Gemini to date.
- Any id-mismatch — same reason.
- Glued text, lost list structure, or an id that doesn't match
  `PURE-PEERING-000N` anywhere in the output — that would point at the
  extractor, not the pipeline, and should route back to the extractor work,
  not be treated as a pipeline finding.
