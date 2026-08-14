# Live-answer session — 2026-08-14

Executes docs/superpowers/plans/2026-08-14-live-answer-policy.md: the first
measurement of what refinement does when a real human engages, instead of
`answer_policy_driver.py`'s deliberate refusal policy.

## Setup

- Provider: paid-tier Gemini (`GEMINI_API_KEY_PAID`), via
  `live_bridge_driver.py` (this directory) -- same construction pattern as
  `docs/superpowers/results/2026-08-10-gemini-paid-tier-run/paid_gemini_driver.py`.
- Configs/fixtures: byte-identical copies of the six scenarios named in the plan's
  section 1, taken from `docs/superpowers/results/2026-08-11-behavior-scenarios/`
  (`configs/`, `fixtures/` here). Copied rather than pointed-at only so each run's
  `output_dir` (resolved relative to the config file's own directory, per
  `orchestrator/config.py::resolve_run_config`) would land under this results
  directory instead of the original suite's. Content is unchanged.
- Human interaction: the real `orchestrator/human_cli.py::answer_questions_cli` /
  `decide_at_cap_cli`, called with injected `input_fn`/`output_fn` (the seam those
  two functions document as existing for exactly this) that talk to a live chat
  session over files in `bridge/<scenario>/`, instead of a real terminal. Every
  answer's *content* came from the user, typed in chat, relayed verbatim into
  `answer.txt` for the blocked pipeline process to consume -- nothing in
  `live_bridge_driver.py` invents, drafts, or edits an answer.
- 11a's cap decision is fixed to `generate`, 11b's to `stop`, per the plan; the
  free-text *reason* at the cap is still the user's own words in both.

## Methodology incidents (report rather than bury)

1. **One answer was briefly not verbatim.** Answering `PURE-THEMAS-R6`'s first
   question, I (the agent) paraphrased the user's terse "3°F" into a longer
   sentence before writing it to `answer.txt`, and it was consumed by the running
   pipeline before it could be corrected. Caught immediately, flagged to the user,
   who chose to kill the run and restart `scn-04-conflict-numeric` from scratch.
   The `PURE-THEMAS-R6`/`PURE-THEMAS-R6-P` records in this directory are from the
   restarted run only; no fabricated answer text is in any run record used below
   or in `answers.json`. Logged to memory
   (`feedback_verbatim_human_answers.md`) so it does not recur.
2. **One echoed message, not used.** Mid-way through `scn-11b-cap-stop`, one chat
   turn came back as a verbatim echo of the agent's own preceding question rather
   than a real answer. Recognized as almost certainly a transport/harness glitch
   rather than a real reply, held rather than recorded, and re-asked; the user's
   next message was a genuine answer, which is what is in the record.

## Per-requirement outcomes

| Requirement | Scenario | Outcome | Rounds | Questions asked | Tokens |
|---|---|---|---|---|---|
| THEMAS-REQ-G | scn-08-clean | `COMPLETED` | 1 | 0 | 5,599 |
| THEMAS-REQ-D | scn-09-vague | `COMPLETED` | 3 | 2 | 15,572 |
| THEMAS-REQ-E | scn-09-vague | `COMPLETED` | 3 | 3 | 16,133 |
| AUTOGEN-US2 | scn-10-atomicity | `CAP_STOPPED` | 3 | 6 | 12,809 |
| LUITEL-R7 | scn-10-atomicity | `CAP_STOPPED` | 3 | 4 | 12,176 |
| PURE-THEMAS-R6-P | scn-04-conflict-numeric | `COMPLETED` | 3 | 2 | 15,767 |
| PURE-THEMAS-R6 | scn-04-conflict-numeric | `CAP_STOPPED` | 3 | 2 | 11,777 |
| AUTOGEN-US3 | scn-11a-cap-generate | `CAP_GENERATED` | 3 | 4 | 14,869 |
| AUTOGEN-US3 | scn-11b-cap-stop | `CAP_STOPPED` | 3 | 4 | 11,819 |

Outcome mix: 4 `COMPLETED`, 4 `CAP_STOPPED`, 1 `CAP_GENERATED` (n=9 requirement-slots
across 6 scenario runs; `AUTOGEN-US3` is the same fixture run twice, once per cap
branch). 27 clarifying questions answered in total, matching `answers.json`'s 27
recorded answers exactly (verified by `extract_answers.py`, which reads only the
run records).

## Original vs. final text, unjudged

> Quoted as recorded; no assessment of better/worse below is mine to make.

**THEMAS-REQ-G** (scn-08-clean) -- unchanged, no questions asked:
> Each thermostat shall have a unique identifier by which that thermostat is
> identified in the THEMAS system.

**THEMAS-REQ-D** (scn-09-vague):
- Original: *"Temperatures that do not exceed these limits shall be output for
  subsequent processing."*
- Final: *"Whenever a reported temperature or changed setting falls within the
  overtemperature bounds (LO = TSET - OD and UO = TSET + OD) established from the
  initialization file per SRS-005, the Determine Temperature Status process
  (SRS-009) shall output the temperature status to Determine H/C Mode (SRS-010)."*

**THEMAS-REQ-E** (scn-09-vague):
- Original: *"If this condition is true, then this module shall output a request
  to turn on the heating unit in case LO = T_LT."*
- Final: *"If Condition 2 is true (the current temperature T is outside the
  trigger band but within the overtemperature bounds), then the Determine H/C
  Mode process shall output an H/C Request to turn on the heating unit in case LO
  <= T < LT, where LT = TSET - TD and LO = TSET - OD."*

**AUTOGEN-US2** (scn-10-atomicity), `CAP_STOPPED`:
- Original: *"As a user, I want a product that is reliable and efficient so that
  I can depend on it."*
- Final: *"As a user, I want a product that is reliable and efficient according
  to performance and reliability metrics defined by the product owner, so that I
  can depend on it."*

**LUITEL-R7** (scn-10-atomicity), `CAP_STOPPED` -- unchanged across all 3 rounds:
> The system shall generate reports on inventory levels, product movement, and
> sales history.

**PURE-THEMAS-R6-P** (scn-04-conflict-numeric), `COMPLETED`:
- Original: *"The THEMAS system shall permit a temperature deviation of up to 5
  degrees Fahrenheit for any thermostat before reporting a deviation error."*
- Final: *"The THEMAS system shall permit a temperature deviation of up to 3
  degrees Fahrenheit for any thermostat before reporting a deviation error."*

**PURE-THEMAS-R6** (scn-04-conflict-numeric), `CAP_STOPPED`:
- Original: *"The THEMAS system shall ensure the temperature reported by a given
  thermostat shall not exceed a maximum deviation value of 3 degrees Fahrenheit."*
- Final: *"The THEMAS system shall ensure the temperature reported by a given
  thermostat shall not exceed a maximum deviation value of 3°F."* (format-only:
  "degrees Fahrenheit" → "°F"; the number never changed.)

**AUTOGEN-US3** (scn-11a-cap-generate and scn-11b-cap-stop, same fixture, both
runs) -- unchanged in both:
> As a user, I want a product that meets my needs so that I can get value for my
> money.

## Text-change rate

5 of 9 requirement-slots show a different final string than their original
(55.6%). Of those five, one (`PURE-THEMAS-R6`) is a unit-format change only, the
underlying number unchanged -- 4 of 9 (44.4%) carry a substantive content change.

Comparison point, same nine requirement-slots, refusing policy
(`docs/superpowers/results/2026-08-11-behavior-scenarios`, identical fixtures --
confirmed by identical `requirements/*.json` filename hashes, which are
`sha256(requirement_id)`): 4 of 9 changed (44.4%). This is the correct
apples-to-apples baseline for *this* subset -- higher than the full 47-item
suite's 19% (38/47 no-op), because this subset was deliberately chosen where
refinement is expected to help (plan section 1), which the plan itself names as a
threat to validity below.

Per-requirement contrast (refusing → live-human), from each side's own run
records:

| Requirement | Refusing-policy final text change | Live-human final text change |
|---|---|---|
| THEMAS-REQ-G | none | none |
| THEMAS-REQ-D | cosmetic only ("these limits" → "the specified temperature limits" -- still names nothing) | substantive (named referent + values) |
| THEMAS-REQ-E | none (capped, unchanged) | substantive (named referent + inlined formula) |
| AUTOGEN-US2 | substantive-looking but placeholder: Rewriter inserted `[reliability threshold, TBD]`/`[efficiency threshold, TBD]` into the text itself | substantive-looking but deferred: "...metrics defined by the product owner" (no number either way) |
| LUITEL-R7 | none -- but reached `COMPLETED` via a no-op: policy asserted `NON_ATOMIC` was "one causal step" (a false claim for this fixture) and the checker accepted it | none -- capped `CAP_STOPPED`; the human correctly identified it as genuinely non-atomic and said so, but no single-requirement rewrite can act on that |
| PURE-THEMAS-R6-P | none (capped, unchanged) | substantive: 5°F → 3°F, `COMPLETED` |
| PURE-THEMAS-R6 | none (capped, unchanged) | cosmetic only (unit formatting), capped |
| AUTOGEN-US3 (both runs) | placeholder text inserted (`[TBD: ...]` / `[specified user needs]`) in both runs | none in either run -- human explicitly declined to invent content |

The one place the live-human policy produced a fix the refusing policy
structurally cannot (`PURE-THEMAS-R6-P`, a cross-requirement numeric conflict
resolved because the human could say which side was correct) is the clearest
single result of this session. The one place refusing policy's text "changed"
more than the live-human policy's (`AUTOGEN-US3`, `AUTOGEN-US2`) is bracket-
placeholder insertion, not real content -- a caution against reading "text-change
rate" as a proxy for "improved," matching Known Limitation 11.

## Tokens and cost

Computed from every `attempts` entry's `prompt_tokens`/`completion_tokens` across
all 6 run directories' `document.json` and `requirements/*.json` (not estimated),
at this project's established paid-tier rate ($1.50/1M input, $7.50/1M output --
`docs/superpowers/results/2026-08-11-behavior-scenarios/RESULTS.md`).

| Scenario | Input tokens | Output tokens | Cost |
|---|---|---|---|
| scn-08-clean | 6,665 | 686 | $0.0151 |
| scn-09-vague | 28,647 | 4,890 | $0.0796 |
| scn-10-atomicity | 23,465 | 3,340 | $0.0602 |
| scn-04-conflict-numeric | 26,019 | 3,461 | $0.0650 |
| scn-11a-cap-generate | 14,879 | 1,746 | $0.0354 |
| scn-11b-cap-stop | 12,327 | 1,248 | $0.0279 |
| **Total** | **112,002** | **15,371** | **$0.2833** |

Within the plan's estimated $0.20-0.35 range. The refusing-policy runs of these
same six scenarios cost $0.2432 combined (from `RESULTS.md`'s per-scenario
figures) -- the live-human runs cost about 16% more, consistent with more rounds
occasionally continuing where the refusing policy's shorter canned answers would
have already hit the cap with less to say.

## Fallback/drift verification

`answering_policy_driver.py --self-test` replays every `ClarifyingQuestion`
actually asked across this session's six run directories through
`TranscriptAnswerPolicy.answer_questions` (built from `answers.json`) with no
network call:

```
self-test: replayed 16 turn(s) -- misses=0 drift_warnings=0
```

16 turns (one per round-that-asked-something, i.e. every round with a `turn` set)
covering the 27 individual questions, zero misses, zero question-text drift.

## Threats to validity

Per the plan's section 6, plus what this run surfaced:

- The requirements' author, the pipeline's builder, and the live answerer are the
  same person (unavoidable in a solo thesis).
- Answers were given knowing the pipeline's issue taxonomy, which a naive user
  would not know.
- n=1 per requirement; this shows a direction, not a distribution.
- The subset is chosen where refinement is expected to help -- confirmed above:
  this subset's refusing-policy baseline (44.4% changed) is already well above
  the full-suite average (19%), so both policies are being compared on favorable
  ground.
- One answer was briefly non-verbatim before being caught and the run restarted
  (see "Methodology incidents" above) -- the final records contain no fabricated
  answer text, but the incident is itself evidence of how easily a "helpful"
  paraphrase can slip into a transcript meant to be attributable to a specific
  person.
- `LUITEL-R7`'s comparison is not a clean two-policy contrast: the refusing
  policy reached `COMPLETED` by asserting something untrue (that the requirement
  is one causal step), not by resolving the actual defect. Reading that as
  "refusing policy succeeded here" would be wrong.

## Artifacts in this directory

- `configs/`, `fixtures/` -- byte-identical copies of the six scenarios' configs
  and fixtures.
- `live_bridge_driver.py` -- the file-bridge `HumanFns` used to run the live
  session.
- `bridge/<scenario>/` -- transient hand-off files from the live session (mostly
  already consumed/deleted during the run; `output.log`/`driver.stdout`/
  `driver.stderr` remain per scenario).
- `configs/runs_<scenario>/<scenario>/` -- the six run directories themselves
  (`document.json` + `requirements/*.json`), the actual source of truth for
  everything in this file.
- `extract_answers.py` -- builds `answers.json` from the run records above.
- `answers.json` -- the frozen transcript, in the shape the plan's section 3
  specifies.
- `answering_policy_driver.py` -- the replay driver (plan section 4), with
  `--self-test` for the offline check reported above.
- `OBSERVATIONS-DURING-SESSION.md` -- notes written *during* the session by the
  assistant session assisting with the answers, at the operator's request, in
  parallel with the runs (not by the agent executing them, and not by a
  background process -- an earlier draft of this file guessed otherwise and was
  wrong). Its quotations from
  `datasets/requirements-xml/XMLZIPFile/1998 - themas.xml` were read directly out
  of that file and are reliable; its frequency and recurrence claims are marked
  in the file itself as unverified leads. The file carries its own provenance
  block making that split explicit. Kept separate from the measured numbers above
  rather than folded in, because the two have different evidentiary standing.
