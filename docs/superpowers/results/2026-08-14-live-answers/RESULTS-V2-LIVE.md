# Prompt v2 vs frozen live-human transcript — results

Plans: `docs/superpowers/plans/2026-08-14-prompt-v2-batch.md`,
`docs/superpowers/plans/2026-08-14-live-answer-policy.md`. Prompt commit: `2178774`.
Baseline: the 2026-08-14 live-answer run (`SESSION.md`, this directory), v1 prompts,
same nine requirement-slots. This run replays the exact same frozen transcript
(`answers.json`) against v2 prompts, via `answering_policy_driver.py` unchanged — the
prompt is the only variable. No prompt, schema, fixture or driver was edited to run
this.

**Two regression guards tripped, both on `THEMAS-REQ-E`: 1 replay miss, 1
`COMPLETED`→`CAP_STOPPED` flip.** Neither is explained by predictions 1–6. Reported in
full below, nothing patched in response.

New configs: `configs/scn-{08-clean,09-vague,10-atomicity,04-conflict-numeric,
11a-cap-generate,11b-cap-stop}-v2-live.yaml` (`prompt_version: v2`, `run_id`/
`output_dir` suffixed `-v2-live`). New run dirs: `configs/runs_scn-*-v2-live/`. Verified
before running: none of the six output dirs existed, and neither the six original
2026-08-14 live-answer dirs nor the `-v2` (refusing-policy) dirs under
`2026-08-11-behavior-scenarios/configs/` were touched. `prompt_hash` in every new
run's `run_config.json` is `1d0f79545e45` for `quality_checker`, matching the
refusing-policy v2 run's hash exactly — same committed prompt file both times.

9 requirement-slots (6 scenarios, `AUTOGEN-US3` run twice — 11a/11b — same as the
v1 baseline).

---

## Replay health

**Misses: 1, on `THEMAS-REQ-E`.**

Round 2 of the v2 run raised a new `incomplete` issue on `THEMAS-REQ-E` that never
existed in the v1 transcript — v2's own round-1 rewrite introduced a genuinely new gap
(see below), and no recorded answer exists for `THEMAS-REQ-E::incomplete`. The fallback
fired verbatim ("Not covered by the recorded transcript for this requirement and issue
category."), the policy declined to resolve it (as it always does on a miss), and the
requirement hit the revision cap instead of completing. This requirement's comparison
is **not clean** — the flip below is a byproduct of the miss, not of the three
no-op rules.

Every other requirement/scenario: 0 misses.

**Drift warnings: 21 total**, one per scenario: `scn-08-clean` 0, `scn-09-vague` 3,
`scn-10-atomicity` 6, `scn-04-conflict-numeric` 4, `scn-11a-cap-generate` 4,
`scn-11b-cap-stop` 4. Expected, per both plans — v2 rewords every question, and drift
is keyed on exact `question_text` match. Three examples:

> `LUITEL-R7::non_atomic` (scn-10, revision 1) —
> captured: *"Regarding the phrase 'inventory levels, product movement, and sales
> history', should report generation for each of these three distinct areas be split
> into separate requirements?"*
> actual: *"Requirement LUITEL-R7 combines multiple reporting functions in the phrase
> 'reports on inventory levels, product movement, and sales history'. Should this
> requirement be split into three separate requirements—one for inventory levels, one
> for product movement, and one for sales history—so each function can be
> independently specified, implemented, and tested?"*

> `PURE-THEMAS-R6::inconsistent` (scn-04, revision 1) —
> captured: *"Requirement PURE-THEMAS-R6 specifies a 'maximum deviation value of 3
> degrees Fahrenheit', but related requirement PURE-THEMAS-R6-P permits a temperature
> deviation of up to 5 degrees Fahrenheit before reporting an error. Should the maximum
> allowable deviation be 3 degrees Fahrenheit, 5 degrees Fahrenheit, or a different
> threshold?"*
> actual: *"Requirement PURE-THEMAS-R6 specifies that the maximum temperature deviation
> shall not exceed '3 degrees Fahrenheit', whereas requirement PURE-THEMAS-R6-P permits
> a temperature deviation of up to 5 degrees Fahrenheit before reporting an error. What
> is the correct maximum temperature deviation threshold that should be enforced?"*

> `AUTOGEN-US3::ambiguous_term` (scn-11a, revision 1) —
> captured: *"In requirement AUTOGEN-US3, what specific functional capabilities or user
> goals should replace the vague phrase 'meets my needs'?"*
> actual: *"In requirement AUTOGEN-US3, the phrase 'meets my needs' is vague. What
> specific user needs, functionalities, or performance criteria should the product
> satisfy?"*

The stored answer was reused verbatim in every drift case (per the driver's own rule —
drift only warns, it never blocks the match), so drift by itself did not create any
miss. The one miss above is a distinct, separate event (a new category, not a reworded
question for an existing one).

---

## Predictions

**1. `THEMAS-REQ-D` and `THEMAS-REQ-E` still change substantively — HELD for
`THEMAS-REQ-D`; HELD-WITH-CAVEAT for `THEMAS-REQ-E` (see regression guard below).**

`THEMAS-REQ-D`:
- v1 final: *"Whenever a reported temperature or changed setting falls within the
  overtemperature bounds (LO = TSET - OD and UO = TSET + OD) established from the
  initialization file per SRS-005, the Determine Temperature Status process (SRS-009)
  shall output the temperature status to Determine H/C Mode (SRS-010)."*
- v2 final: *"Temperatures that do not exceed the overtemperature limits defined in
  SRS-009 and established from the initialization file per SRS-005 shall be output for
  subsequent processing."*

Both name concrete document sections in place of "these limits" — different wording,
same referent resolved from the same human answer. Not suppressed.

`THEMAS-REQ-E`: v2's round-1 rewrite *did* incorporate the human's real content —
> *"If Condition 2 (LO <= T < LT or UT < T <= UO) is true, then the Determine H/C Mode
> process shall output an H/C Request to turn on the heating unit in case LO = T_LT."*

— naming "Condition 2" and both temperature bounds, same substance the human supplied.
So the rule change did not suppress this rewrite. What happened next is a different,
unpredicted failure: the Quality Checker's round-2 pass caught that this exact sentence
only specifies an action for the lower bound (`LO <= T < LT`) and leaves the upper bound
(`UT < T <= UO`) unhandled — a real incompleteness, and arguably v2's rewrite
introduced it (v1's round-1 rewrite took a different tack, replacing the trailing "in
case LO = T_LT" with a fully resolved formula instead of leaving it as-is). No answer
for `incomplete` exists in the frozen transcript because this issue never arose in the
v1 run, so the replay's fallback fired and the requirement capped. This is not the
three no-op rules suppressing content — it's the rewrite itself creating a new gap that
the frozen transcript cannot answer. Recorded as held on its own narrow terms (the
answer's content was not suppressed) but not a clean measurement, per the miss above.

**2. `PURE-THEMAS-R6-P` still reaches `COMPLETED` with 5°F → 3°F — HELD.**

v2 final: *"The THEMAS system shall permit a temperature deviation of up to 3 degrees
Fahrenheit for any thermostat before reporting a deviation error."* Identical
substantive fix to v1, `outcome: completed`. The one genuine cross-requirement fix in
the project survives the prompt change.

**3. `PURE-THEMAS-R6` comes back byte-for-byte unchanged — HELD.**

v2 final: *"The THEMAS system shall ensure the temperature reported by a given
thermostat shall not exceed a maximum deviation value of 3 degrees Fahrenheit."* —
identical to the input text, both rounds. v1's same run reformatted this to "3°F"; v2
does not. This is the first confirmation of rule 3 (Known Limitation 11, variant 3)
holding against a **real** human answer ("already correct, fix belongs to the other
requirement") rather than only the refusing policy's canned version of the same
sentiment.

**4. `AUTOGEN-US2` gets no deferral phrase — HELD.**

v2 final: *"As a user, I want a product that is reliable and efficient so that I can
depend on it."* — unchanged from the original in both rounds. v1's same run produced
*"...according to performance and reliability metrics defined by the product owner..."*
— the exact deferral pattern rule 2 targets. Gone under v2, confirmed against the real
human answer ("not specified anywhere... I'm not going to pick one," paraphrased from
the transcript) rather than only the refusing policy's blanket refusal.

**5. `AUTOGEN-US3` unchanged in both 11a and 11b — HELD.**

v2 final text equals the original in both scenario runs (verified by direct string
comparison, not just outcome). Matches v1 exactly.

**6. `LUITEL-R7` still flagged `non_atomic` and still caps — HELD (control holds).**

All three v2 rounds carry a `non_atomic` issue, e.g. round 3: *"The requirement
combines three distinct, independently testable reporting behaviors (inventory levels,
product movement, and sales history) into a single statement."* `outcome:
cap_stopped`, same as v1. The tightened definition did not silence the one genuine
positive case in this run either.

---

## Regression guards

**No new `VALIDATION_FAILURE` — PASS.** Scanned every requirement record's `errors`
list across all six v2-live scenarios (9 requirement-slots). Zero non-empty lists.

**No replay miss — TRIPPED.** 1 miss, `THEMAS-REQ-E::incomplete` (detailed above).

**No `COMPLETED`↔cap flip unless a prediction explains it — TRIPPED.**
`THEMAS-REQ-E`: `COMPLETED` (v1) → `CAP_STOPPED` (v2). No prediction 1–6 covers this —
prediction 1 only commits to "still change substantively," which held; it says nothing
about an outcome flip. The flip is a direct consequence of the miss above, not of the
rewriter no-op rules or the non_atomic definition change (`THEMAS-REQ-E` never involved
either — its issues are `vague_pronoun` and, newly in v2, `incomplete`). Every other
requirement kept its v1 outcome:

| Requirement | Scenario | v1-live outcome | v2-live outcome |
|---|---|---|---|
| THEMAS-REQ-G | scn-08-clean | completed | completed |
| THEMAS-REQ-D | scn-09-vague | completed | completed |
| THEMAS-REQ-E | scn-09-vague | completed | **cap_stopped** |
| AUTOGEN-US2 | scn-10-atomicity | cap_stopped | cap_stopped |
| LUITEL-R7 | scn-10-atomicity | cap_stopped | cap_stopped |
| PURE-THEMAS-R6 | scn-04-conflict-numeric | cap_stopped | cap_stopped |
| PURE-THEMAS-R6-P | scn-04-conflict-numeric | completed | completed |
| AUTOGEN-US3 (11a) | scn-11a-cap-generate | cap_generated | cap_generated |
| AUTOGEN-US3 (11b) | scn-11b-cap-stop | cap_stopped | cap_stopped |

8 of 9 unchanged; the one change traces cleanly to the miss, not to either prompt edit.

---

## Headline: text-changing rewrite count, v1-live vs v2-live

Computed directly from each side's run records (`final_text != requirement.text`),
same nine requirement-slots:

| | v1-live (SESSION.md) | v2-live (this run) |
|---|---|---|
| Text-changing rewrites | 5 / 9 (55.6%) | **3 / 9 (33.3%)** |
| No-op rewrites | 4 / 9 | 6 / 9 |

This is the answer to the open question from the same-day IMPLEMENTATION_LOG.md
amendment to the refusing-policy batch result ("replay the frozen live transcript... the
only run where answers carry content and the rules can be selective"): **the rules are
selective, not just silencing.** Under the refusing policy, text-changing rewrites went
5 → 0 — every change vanished, because rule 2 ("no concrete value supplied → no
change") fires universally against a policy that supplies no content by design. Here,
against real answers, the count went 5 → 3, and the count is exactly explained by which
three v1 changes were real content vs. artifacts:

| Requirement | v1-live change | v2-live change | Explanation |
|---|---|---|---|
| THEMAS-REQ-D | substantive (named referent + values) | substantive (named referent + values) | kept |
| THEMAS-REQ-E | substantive (named referent + formula) | substantive (named referent + bounds), then capped on an unrelated new `incomplete` flag | kept (see miss above) |
| PURE-THEMAS-R6-P | substantive (5°F → 3°F) | substantive (5°F → 3°F) | kept |
| AUTOGEN-US2 | deferral phrase inserted, no value | **unchanged** | suppressed — rule 2 target |
| PURE-THEMAS-R6 | cosmetic reformat only | **unchanged** | suppressed — rule 3 target |
| LUITEL-R7 | unchanged | unchanged | unaffected either way |
| AUTOGEN-US3 (11a) | unchanged | unchanged | unaffected either way |
| AUTOGEN-US3 (11b) | unchanged | unchanged | unaffected either way |
| THEMAS-REQ-G | unchanged | unchanged | unaffected either way |

Every requirement whose v1 change was real content (3 of 3) kept its change. Every
requirement whose v1 "change" was one of Known Limitation 11's two named artifacts
(deferral phrase, cosmetic reformat — 2 of 2) had it suppressed. Nothing that should
have changed stopped changing, and nothing the rules targeted survived. n is small (3
kept, 2 suppressed, on one transcript) — this is a clean directional result, not a
distribution.

---

## Tokens and cost

Computed from every `attempts` entry's `prompt_tokens`/`completion_tokens` (not
estimated), same rate as `SESSION.md` ($1.50/1M in, $7.50/1M out):

| Scenario | v1-live cost | v2-live cost |
|---|---|---|
| scn-08-clean | $0.0151 | $0.0158 |
| scn-09-vague | $0.0796 | $0.0684 |
| scn-10-atomicity | $0.0602 | $0.0535 |
| scn-04-conflict-numeric | $0.0650 | $0.0611 |
| scn-11a-cap-generate | $0.0354 | $0.0377 |
| scn-11b-cap-stop | $0.0279 | $0.0303 |
| **Total** | **$0.2833** | **$0.2667** |

Within the plan's ~$0.28 estimate, slightly under v1-live — consistent with `scn-09` and
`scn-10` each losing rounds of back-and-forth that v1 spent on issues v2 didn't raise
(or, for `scn-09`, capping one round earlier on `THEMAS-REQ-E`).

---

## Summary

| # | Prediction | Verdict |
|---|---|---|
| 1 | `THEMAS-REQ-D`/`THEMAS-REQ-E` still change substantively | **Held** (D clean; E held-with-caveat, see guards) |
| 2 | `PURE-THEMAS-R6-P` reaches `COMPLETED`, 5°F→3°F | **Held** |
| 3 | `PURE-THEMAS-R6` byte-for-byte unchanged | **Held** |
| 4 | `AUTOGEN-US2` no deferral phrase | **Held** |
| 5 | `AUTOGEN-US3` unchanged in both 11a/11b | **Held** |
| 6 | `LUITEL-R7` still `non_atomic`, still caps | **Held** |
| Guard | No new `VALIDATION_FAILURE` | **Pass** |
| Guard | No replay miss | **Tripped** (1, `THEMAS-REQ-E`) |
| Guard | No `COMPLETED`↔cap flip unexplained by 1–6 | **Tripped** (1, `THEMAS-REQ-E`, traces to the miss) |

6 of 6 predictions held (one with a caveat). Both guard trips are the same single
requirement and the same root cause — a new `incomplete` flag v2's own round-1 rewrite
introduced, which the frozen transcript has no answer for. No prompt was changed in
response. The headline result stands independent of that trip: text-changing rewrites
went 5/9 → 3/9 against real content, not 5/9 → 0/9 as under the refusing policy — the
three no-op rules are measurably selective, confirming the fold-in already added to
`design/DESIGN_NOTES.md`, Known Limitation 11.
