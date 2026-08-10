# Gemini paid-tier run (2026-08-10)

One-off experiment, at the user's request: re-run the checklist against
`gemini-3.6-flash` using a **paid-tier** API key (`GEMINI_API_KEY_PAID`), to check
whether the free tier's absolute 20-request cap
(`docs/superpowers/results/2026-08-10-first-real-run/ANALYSIS.md`) was specific to the
free tier.

**Not a change to default behavior.** `orchestrator/providers/gemini.py`'s
`GeminiAdapter.from_env()` is untouched and still reads `GEMINI_API_KEY` only — every
normal run (`python -m orchestrator.cli`, the original `answer_policy_driver.py`) keeps
using the free-tier key by default. `paid_gemini_driver.py` (this directory) is a
separate, opt-in-only script that constructs a `GeminiAdapter` directly with
`GEMINI_API_KEY_PAID`'s value — it raises rather than silently falling back to the free
key if that variable isn't set. It reuses the exact same AI answer policy as the
original driver (loaded by file path, not duplicated) — only the Gemini adapter differs.

## Result: clean end-to-end run, zero failures

`docs/superpowers/results/2026-08-10-gemini-paid-tier-run/gemini/` — same document
(`themas-fischbach2022`), same `runs_gemini.yaml` (`gemini-3.6-flash`, `requests_per_minute:
15`, `tokens_per_minute: null`), same AI answer policy.

| Stage | attempts | success | failures |
|---|---|---|---|
| classifier | 8 | 8 | 0 |
| consistency_checker | 1 | 1 | 0 |
| dependency_mapper | 1 | 1 | 0 |
| quality_checker | 19 | 19 | 0 |
| refiner_questioner | 11 | 11 | 0 |
| refiner_rewriter | 11 | 11 | 0 |
| strategy_selector | 3 | 3 | 0 |
| test_generator | 3 | 3 | 0 |
| **Total** | **57** | **57** | **0** |

**89,173 tokens**, zero transport failures, zero validation failures, zero id-mismatches.
Every one of the 8 requirements reached a terminal outcome: `THEMAS-REQ-A`/`C`/`G`
completed cleanly; `B`/`D`/`E`/`F`/`H` hit `cap_stopped` (the AI answer policy's
conservative decline to certify unresolved issues — not a pipeline failure, see the
original `ANALYSIS.md`'s methodology note).

## Conclusion

The free tier's 20-request absolute cap does not apply to the paid tier — confirmed
directly, not inferred. This run made 57 real calls (nearly 3x the free tier's entire
daily budget) with zero rate-limit friction of any kind. This is the first run in the
whole 2026-08-10 series where `document.json`'s `errors` list is empty and every
requirement reached a terminal (non-`error`) outcome.
