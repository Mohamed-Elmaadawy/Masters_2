# TPM throttle validation — follow-up run (2026-08-10)

A re-run of the checklist against `themas-fischbach2022` (same document,
`orchestrator/extract_document.py`), after adding `Throttle.tokens_per_minute`
(`orchestrator/pipeline.py`, see `design/ORCHESTRATOR_CONTRACT.md` item 19), to check
whether it actually reduces the TPM-driven transport failures the original run
(`docs/superpowers/results/2026-08-10-first-real-run/ANALYSIS.md`) measured.

## Gemini: unaffected, as expected

Re-attempted against `gemini-3.6-flash` first. Failed on the same 429 the original run
hit — `Quota exceeded ... generate_content_free_tier_requests, limit: 20` — after a
single successful call. **This is expected, not a regression**: that quota is an
absolute per-model *request count* cap, not a token-rate limit, and
`tokens_per_minute` is (correctly) `null` for Gemini in `orchestrator/runs_gemini.yaml`
— there is no real TPM number to throttle against, and this fix was never going to
touch that failure mode. No run directory survives (nothing new to preserve beyond
what the original run's ANALYSIS.md already documented).

## Groq: the TPM fix worked — a different limit showed up instead

`docs/superpowers/results/2026-08-10-tpm-throttle-validation/groq/` — same
`runs_groq.yaml` (12,000 TPM, cited from the original run), same document, same AI
answer policy.

**Zero of this run's 19 transport failures mention "tokens per minute."** All 19 say
"tokens per day":

> `Rate limit reached for model llama-3.3-70b-versatile ... on tokens per day (TPD):
> Limit 100000, Used ~99,300, Requested ~1,200-1,500`

This is a **daily** token budget (TPD), separate from the TPM (12,000/minute) budget the
fix addresses, and it is not something `Throttle.tokens_per_minute` was built to pace
against (contract item 19 is scoped to a rolling 60s window; a 24-hour window is a
different mechanism entirely). It got hit because this is the account's **second** Groq
run today: the original run spent 73,420 tokens, this run spent another 30,217 before
hitting the wall — 103,637 combined, consistent with a ~100,000/day ceiling.

**Per-stage breakdown, this run:**

| Stage | attempts | success | transport_failure |
|---|---|---|---|
| classifier | 18 | 3 | 15 |
| consistency_checker | 1 | 1 | 0 |
| dependency_mapper | 1 | 1 | 0 |
| quality_checker | 7 | 7 | 0 |
| refiner_questioner | 7 | 4 | 3 |
| refiner_rewriter | 5 | 4 | 1 |
| **Total** | **39** | **20** | **19** |

Per-requirement: `THEMAS-REQ-A`/`B` (processed first, while daily budget headroom
still existed) both completed real refinement rounds and reached `cap_stopped`
legitimately (the AI answer policy's conservative decline, not a failure).
`THEMAS-REQ-C` got one round in before erroring. `THEMAS-REQ-D`/`E`/`F`/`G`/`H`
(processed later, once the account was already near the daily ceiling) each exhausted
`max_attempts=3` on the very first `classifier` call — `rounds=0`, never reached
refinement at all. This ordering is exactly what a shared, depleting daily budget
predicts: later-processed requirements see a nearly-empty budget and fail immediately,
earlier ones don't.

## Conclusion

The fix does what `design/ORCHESTRATOR_CONTRACT.md` item 19 claims: it eliminates TPM
(per-minute) 429s specifically — confirmed empirically, not just by code review, since
this run made 39 real attempts against the exact model/config that produced 22 TPM
failures in the original run, and produced zero. It was never claimed to address a
daily budget, and it doesn't — that's a distinct, currently-unaddressed constraint,
now measured for the first time. Not fixing it now: today's Groq quota is exhausted
regardless (a TPD limit doesn't clear in minutes), and whether it's worth a
tokens-per-day throttle mechanism is a separate decision, not a foregone conclusion —
build it, or just document the limitation, is the next open question if this pattern
recurs.
