# First real run — analysis (2026-08-10)

Per `docs/superpowers/plans/2026-08-08-first-real-run-checklist.md`: two runs, same
document (`themas-fischbach2022`, 8 requirements, extracted via
`orchestrator/extract_document.py`), one per provider, computing the checklist's three
numbers straight from the `attempts` records. No numbers estimated — everything below
is read off `docs/superpowers/results/2026-08-10-first-real-run/groq/`'s actual
`document.json`/`requirements/*.json`.

**This directory is a historical snapshot, not a resumable run.** `groq/run_config.json`
predates `orchestrator/config.py`'s `RateLimitConfig.tokens_per_minute` becoming a
required field (added the same day, after this run, per
`design/ORCHESTRATOR_CONTRACT.md` item 19) — it genuinely didn't exist when this ran, so
the file is left as-is rather than rewritten to look like it did. `read_resolved_run_config`
on this exact file now raises (`tokens_per_minute` — Field required); that's expected for
an archived artifact, not a bug to fix here.

**Gemini has no run in this directory.** See "Gemini: quota exhausted" below for why,
and why none of the three attempts made were worth preserving.

---

## Human-in-the-loop answers — methodology note

The Refiner's clarifying questions were answered by a **fixed AI policy**
(`answer_policy_driver.py`, committed alongside this analysis), not by a live human,
because no interactive terminal was available to the automated process running these
two calls. This is exactly the threat-to-validity `design/ORCHESTRATOR_CONTRACT.md`
item 3 already names ("this makes the pipeline non-deterministic across runs, since a
human judgement sits inside it") — stated here explicitly rather than left implicit.

The policy gives one genuine, reasoned answer per `IssueCategory`, applied
consistently (see the driver script's `_ANSWERS` table): `NON_ATOMIC` questions get a
real judgment call (keep as one requirement, with reasoning, `user_confirms_resolved=
True`); every other category gets a conservative, explicitly-non-resolving answer
(`user_confirms_resolved=False`) that explains *why* the policy can't responsibly
resolve it rather than inventing a number, referent, or side of a conflict not grounded
in the document. At the revision cap, the policy always chooses `CAP_STOPPED` rather
than generating tests from text it never certified as resolved.

**Consequence for reading the results below:** every `cap_stopped` outcome reflects
this conservative policy, not an actual dead end — a human answering the same
`INCOMPLETE`/`VAGUE_PRONOUN` questions with real domain knowledge would very plausibly
resolve some of them and let refinement continue past round 1 or 2.

---

## Gemini: quota exhausted, no usable run

Three attempts were made against `gemini-3.6-flash` (`orchestrator/runs_gemini.yaml`,
configured `requests_per_minute: 15`, taken from the example config's "illustrative"
placeholder — see "Surprises" below):

1. **Interactive attempt** (before the AI answer policy existed): interrupted by
   `EOFError` at the first clarifying question, as expected for a real terminal-reading
   `HumanFns` in a non-interactive environment. Discarded — no usable data, correctly
   caught by the existing `resume`-checkpoint mechanism, just not needed here.
2. **First policy-driven attempt**: **14 successful attempts, 22 `transport_failure`
   attempts** (36 total) before every remaining call failed. The API's own 429 body:
   > `Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 20, model: gemini-3.6-flash`
   Only `THEMAS-REQ-A` reached a terminal outcome (`completed`); the other 7
   requirements errored on `transport_failure` once the 20-request budget ran out.
3. **Second policy-driven attempt, after a 2-minute wait**: failed **immediately** —
   the very first document-level call (`consistency_checker`) returned the identical
   429, before any inference ran (0 tokens spent across the whole document,
   `outcome=degraded`). This rules out a short rolling window: if the limit refilled on
   a per-minute or similar short cycle, 2 minutes would have freed enough budget for at
   least the two document-level calls to succeed.

Each attempt's run directory was deleted before the next was started (to avoid mixing
under one `run_id`), so **no Gemini run survives to commit** — only this record of what
happened. The quantitative finding stands regardless: **`gemini-3.6-flash`'s free tier
enforces an absolute request cap (20) that resets on a longer cycle than a couple of
minutes** — most consistent with a per-day quota, though the exact reset period wasn't
directly measured (that would require waiting hours, out of scope for this session).

---

## Groq: the real data

`docs/superpowers/results/2026-08-10-first-real-run/groq/` — `llama-3.3-70b-versatile`,
`requests_per_minute: 30`. Document outcome: `completed` (both document-level stages
succeeded). Per-requirement outcomes:

| Requirement | Outcome | Rounds |
|---|---|---|
| THEMAS-REQ-A | `cap_stopped` | 3 |
| THEMAS-REQ-B | `error` | 1 |
| THEMAS-REQ-C | `error` | 2 |
| THEMAS-REQ-D | `cap_stopped` | 3 |
| THEMAS-REQ-E | `error` | 1 |
| THEMAS-REQ-F | `cap_stopped` | 3 |
| THEMAS-REQ-G | `completed` | 1 |
| THEMAS-REQ-H | `error` | 2 |

**Every `error` outcome was a transport failure, never a content problem** —
`THEMAS-REQ-B`/`C`/`E` each exhausted `max_attempts=3` on three consecutive Groq TPM
(tokens-per-minute) 429s at whichever stage they happened to be calling when the
budget ran out. **Correction (found by code review, verified against the raw
`attempts` log, not assumed):** an earlier version of this document called
`THEMAS-REQ-H`'s exhaustion "unrelated to rate limiting, a one-off transport blip" —
that was wrong. Its failing `refiner_rewriter` invocation's three attempts were TPM
429, TPM 429, then `RemoteDisconnected('Remote end closed connection without
response')` — two of three exhausting attempts were the same TPM cause as `B`/`C`/`E`;
only the final attempt was the dropped connection. All four `error` outcomes are
predominantly TPM-driven, `THEMAS-REQ-H` merely with one non-TPM attempt mixed in
rather than three uniform ones.

### §1 — id-mismatch rate, per stage, per model

**Zero.** No `StageError`/`DocumentStageError`/`StageAttempt` anywhere in this run has
`kind=VALIDATION` at all, so the `"...the model answered about a different
requirement"` message shape (`call_stage`'s exact string) never appears. **Rate is
0/0 (undefined, not 0%)** for every stage — this run contains no validation failures
to check for id-mismatch among. Gemini: not measured (no run survives).

### §2 — validation-failure rate, per stage, correct denominator

**Zero across every stage**, same reason as §1: not one `VALIDATION_FAILURE` attempt
was recorded anywhere in the run. Denominator (`SUCCESS + VALIDATION_FAILURE`) equals
`SUCCESS` alone in every row below — `llama-3.3-70b-versatile` never produced output
that failed `model_cls.model_validate(...)` in this run, across 51 successful calls and
73,420 tokens.

**Transport-failure rate, reported separately (denominator = all attempts), per stage:**

| Stage | attempts | success | transport_failure | transport rate |
|---|---|---|---|---|
| classifier | 13 | 8 | 5 | 38.5% |
| consistency_checker | 1 | 1 | 0 | 0.0% |
| dependency_mapper | 1 | 1 | 0 | 0.0% |
| quality_checker | 48 | 16 | 32 | 66.7% |
| refiner_questioner | 28 | 12 | 16 | 57.1% |
| refiner_rewriter | 28 | 11 | 17 | 60.7% |
| strategy_selector | 3 | 1 | 2 | 66.7% |
| test_generator | 3 | 1 | 2 | 66.7% |
| **Total** | **125** | **51** | **74** | **59.2%** |

Gemini (first policy attempt only, for reference — not a clean/comparable run since it
was cut off mid-document): 14/36 = 38.9% transport-failure rate before hitting the
absolute request cap, at which point every subsequent attempt failed (rate would
approach 100% for any stage called after the 20th successful request).

### §3 — tokens per stage, cost per document

| Stage | tokens (all successful attempts) |
|---|---|
| classifier | 8,946 |
| consistency_checker | 1,067 |
| dependency_mapper | 1,504 |
| quality_checker | 27,969 |
| refiner_questioner | 15,357 |
| refiner_rewriter | 15,606 |
| strategy_selector | 1,176 |
| test_generator | 1,795 |
| **Total** | **73,420** |

`document_stage_tokens + sum(requirement_records[*].total_tokens)` = **73,420**.
**With vs. without rejected-output tokens: identical (73,420 both ways)** — there is no
"rejected-output" figure to subtract, because zero tokens were spent on a
`VALIDATION_FAILURE` attempt (there were none). Every token spent either produced a
`SUCCESS` or was a `TRANSPORT_FAILURE`/`OTHER_FAILURE` attempt, and per contract item 13
neither of those carries token counts (rejected before inference ran) — confirmed
directly in the data: `tokens_all == tokens_success` in every stage row above.

No cost figure is given: doing so would require a price table dated to when this
analysis was written, which this document does not cite. Tokens are recorded so a price
can be applied later without this run needing to be re-analyzed (see `StageAttempt`'s
own docstring).

---

## Backoff / transport-failure behavior observed

Retries clearly helped *some* calls succeed — every stage shows interleaved
`SUCCESS`/`transport_failure` attempts rather than a clean "succeeds until X, then
always fails" boundary (e.g. `quality_checker`: 16 successes and 32 failures
*interspersed*, not 16-then-32). The `initial_delay_seconds=2.0, multiplier=2.0,
max_attempts=3` backoff schedule (2s then 4s of sleep, 6s total per invocation) was
sometimes enough to let Groq's TPM budget partially refill, and sometimes not — Groq's
own 429 bodies quoted `Please try again in 1.665s`/`430ms`/`465ms`, all *shorter* than
the 2s first backoff, yet three of the four errored requirements still exhausted all 3
attempts. That's consistent with **other concurrent stage calls consuming the same
12,000-TPM budget between the failed attempt's retry and its next try** — a
few-hundred-millisecond quoted wait describes the instant the check ran, not what's
still true a couple of seconds later once a few more large calls have gone out.

---

## Surprises, flagged against ORCHESTRATOR_CONTRACT.md and CLAUDE.md's Known-open list

1. **`Throttle` (`orchestrator/pipeline.py`) has no tokens-per-minute mechanism at
   all — only a requests-per-minute one.** Not listed as a known limitation anywhere in
   `design/ORCHESTRATOR_CONTRACT.md` or CLAUDE.md's "Known-open, deliberately" section
   (checked both — no mention of TPM/tokens-per-minute/token budget). Groq's actual
   binding constraint in this run was tokens (12,000 TPM), not requests (my config's
   `requests_per_minute: 30` was never the limiting factor — 30 rpm was comfortably
   under whatever request-count Groq allows; the run failed on token volume instead).
   `Throttle.wait_for_slot` cannot pace against this because it has nothing to pace on
   — it only tracks `last_call_at` per model, never tokens consumed. This is a real gap
   surfaced by this run, not a design decision the contract already made and accepted.

2. **Gemini's free-tier cap is an absolute request count (20), not a rate.**
   `orchestrator/providers/capabilities.py`'s docstring already flags its own
   output-mode tables as "dated, cited, best-effort... re-verify before trusting deep
   into the future" — but that module's scope is capability (does this model support
   this `output_mode`), never quota size. An absolute per-model request cap this low is
   a different, currently entirely undocumented dimension of "what a real run needs
   to know about a provider before spending quota on it."

3. **Zero validation failures, zero id-mismatches, across a real 73,420-token run.**
   Item 15's "known risk, accepted deliberately" (a systematically wrong-requirement-id
   model tripling its API cost via retries) did not manifest at all for
   `llama-3.3-70b-versatile` on this document with these v1 prompts. One document is a
   small sample — this is a data point for the eventual "is option A needed after all?"
   question item 15 defers, not an answer to it.

4. **Every incomplete outcome in the Groq run was infrastructure-driven or a
   deliberate policy choice — never a content/schema defect.** `cap_stopped` (3/8)
   reflects the AI answer policy's conservative refusal to certify unresolved issues,
   not the pipeline failing; `error` (4/8) was TPM exhaustion or one dropped
   connection, not invalid model output. Only 1/8 requirements (`THEMAS-REQ-G`) needed
   zero refinement rounds and completed cleanly on the first pass.

5. **The example config's `rate_limits` really were "illustrative," confirmed the hard
   way.** `orchestrator/example_run_config.yaml`'s own comment said the 15 rpm figure
   was illustrative, sourced from no real dashboard — this run is the first time that
   number was checked against reality, and it was wrong in both directions: too high
   for Gemini's actual (much stricter, request-count-based) constraint, and measuring
   the wrong dimension entirely for Groq's actual (token-based) one.
