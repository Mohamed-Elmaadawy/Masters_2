# Prompt v2 batch — results

Plan: `docs/superpowers/plans/2026-08-14-prompt-v2-batch.md`. Prompt commit: `2178774`
("Prompt v2: sharpen non_atomic definition, add rewriter no-op rules").

Re-run of the four scenarios containing every requirement affected by the two prompt
edits (`quality_checker.txt` non_atomic definition, `refiner_rewriter.txt` three no-op
rules), against the same fixtures, same refusing answer policy
(`answer_policy_driver.py`, imported unchanged), same PAID Gemini key, as the
2026-08-13 baseline run. Only the two prompt files differ between the two runs —
`prompt_version: v1` vs `v2` in the configs, confirmed by different `prompt_hash`
values in each run's `run_config.json`.

Configs: `configs/scn-{04,07,10,12}-*-v2.yaml`. Run dirs:
`configs/runs_scn-{04,07,10,12}-*-v2/`. Baseline dirs (`runs_scn-{04,07,10,12}-*`,
no `-v2` suffix, dated 2026-08-13) were read-only inputs to this comparison, never
written to.

15 requirement-slots run (2 + 8 + 2 + 3, matching the plan's estimate).

---

## A — `non_atomic` definition (Known Limitation 8)

**1. `THEMAS-REQ-B` (scn-12) is no longer flagged `non_atomic` — HELD.**

Baseline round 1 issues: `["non_atomic"]`. v2 round 1 issues: `[]` (requirement
passed quality-check cleanly, no rounds needed). No `non_atomic` issue appears in any
v2 round for this requirement.

**2. `PURE-ERTMS-R2` (scn-07) is no longer flagged `non_atomic` — REFUTED.**

Baseline round 1 issues: `["non_atomic", "incomplete"]`. v2 round 1 issues:
`["non_atomic"]` — the `incomplete` flag dropped, but `non_atomic` still fired.

Model's v2 explanation (unchanged span, `"train and shunting movements"`):

> "The requirement combines the supervision of train movements and shunting
> movements into a single statement. Supervising train movements and supervising
> shunting movements involve distinct operational modes and rules that can be
> specified, implemented, and tested independently."

This is exactly the "independently testable" language the v2 definition asks for —
the model applied the new definition and still judged this a genuine bundling, not a
causal chain. Baseline explanation was similar but shorter, without the
"independently" framing. The requirement still reached `outcome: completed` in both
runs — the flag did not raise the revision count or change the outcome, only whether
the flag fired at all.

**3. `LUITEL-R7` (scn-10) is still flagged — HELD (control holds).**

Baseline round 1 issues: `["non_atomic", "incomplete"]`. v2 round 1 issues:
`["non_atomic"]`. Model's v2 explanation:

> "The requirement bundles three separate and independently testable reporting
> behaviors (inventory levels, product movement, and sales history) into a single
> statement."

The control did not stop firing, so the definition change did not over-correct into
silence on a genuine positive.

**Net reading of A:** 1/2 target non_atomic flags actually cleared; the definition
change removed exactly the flag on the clean example (`THEMAS-REQ-B`, a
list-conjunction with no causal link) but the model still (correctly, by its own
stated reasoning) judges `PURE-ERTMS-R2`'s two movement types as independently
testable rather than a causal chain. That reasoning is defensible on the text itself —
"train movements" and "shunting movements" are two operating modes, not two steps of
one operation, so this may be the definition working as intended on a case the plan's
evidence review mis-categorized, not a definition failure. Recorded as refuted per the
instruction not to explain predictions into passing.

Secondary observation, not predicted: three separate `incomplete` flags disappeared
between baseline and v2 (`PURE-ERTMS-R2`, `LUITEL-R7`, and one of `PURE-ERTMS-R1`'s
duplicate `incomplete` entries) even though `incomplete`'s own definition text did not
change. Not attributable to this batch's edits with the data collected here — noted
for a future measurement, not counted toward any of the six predictions.

---

## B — Rewriter no-op rules (Known Limitation 11)

**4. `LUITEL-R1` (scn-12) keeps its `5s`, no `[TBD]` placeholder — HELD.**

Baseline final text: `"The system shall reach a steady state [TBD: measurable
value] within 5s after reconfiguration to maximize availability [TBD: measurable
value]."`

v2 final text: `"The system shall reach a steady state within 5s after
reconfiguration to maximize availability."` — byte-for-byte the original text, `5s`
untouched, no placeholder inserted anywhere.

**5. `AUTOGEN-US2` (scn-10) gets no deferral phrase, unchanged text instead — HELD.**

Baseline final text: `"As a user, I want a product that is reliable (meeting
[reliability threshold, TBD]) and efficient (meeting [efficiency threshold, TBD]) so
that I can depend on it."`

v2 final text: `"As a user, I want a product that is reliable and efficient so that
I can depend on it."` — the original text, unchanged. No `[TBD]` and no deferral
phrase ("as defined by the product owner" or similar) appears.

**6. `PURE-THEMAS-R6` (scn-04) returned byte-for-byte unchanged — HELD.**

Baseline final text: `"The THEMAS system shall ensure the temperature reported by a
given thermostat shall not exceed a maximum deviation value of 3 degrees
Fahrenheit."`

v2 final text: identical string, including `"3 degrees Fahrenheit"` — no reformatting
to `"3°F"` or any other unit change. (The refusing-policy 2026-08-13 baseline also left
this one unchanged — the reformatting incident the plan cites happened in the
2026-08-14 **live-human** session, `docs/superpowers/results/2026-08-14-live-answers/`,
per `design/DESIGN_NOTES.md` line 1556-1560, not in this refusing-policy baseline. So
this prediction isn't exercised against a baseline regression on this run's own data —
it confirms the rule holds under the refusing policy, not that it fixed a regression
this specific baseline had.)

**Net reading of B:** 3/3 held. All three rewriter regressions the batch targeted
(TBD-beside-a-value, deferral phrase, unwanted reformatting) are gone in this re-run
for the three cited instances.

---

## Regression guards

**7. No new `VALIDATION_FAILURE` at any stage — PASS.**

Scanned every requirement record's `errors` list across all four scenarios, both
baseline and v2 (15 records each side, 30 total). Zero non-empty `errors` lists found
on either side.

**8. Outcome mix doesn't move by more than one slot per outcome, outside predictions
1–6 — PASS.**

Outcome-by-outcome, baseline vs v2, all 15 requirement-slots:

| Scenario | Requirement | Baseline outcome | v2 outcome |
|---|---|---|---|
| scn-04 | PURE-THEMAS-R6 | cap_stopped | cap_stopped |
| scn-04 | PURE-THEMAS-R6-P | cap_stopped | cap_stopped |
| scn-07 | PURE-ERTMS-R1 | cap_stopped | cap_stopped |
| scn-07 | PURE-ERTMS-R2 | completed | completed |
| scn-07 | PURE-ERTMS-R3 | completed | completed |
| scn-07 | PURE-ERTMS-R4 | completed | completed |
| scn-07 | PURE-ERTMS-R5 | cap_stopped | cap_stopped |
| scn-07 | PURE-ERTMS-R6 | completed | completed |
| scn-07 | PURE-ERTMS-R7 | completed | completed |
| scn-07 | PURE-ERTMS-R8 | cap_stopped | cap_stopped |
| scn-10 | AUTOGEN-US2 | cap_stopped | cap_stopped |
| scn-10 | LUITEL-R7 | completed | completed |
| scn-12 | ACTAPP-R2-AC1 | cap_stopped | cap_stopped |
| scn-12 | LUITEL-R1 | cap_stopped | cap_stopped |
| scn-12 | THEMAS-REQ-B | completed | completed |

Zero outcomes changed. The mix is identical on both sides (8 `completed`,
7 `cap_stopped`), so this guard passes with margin to spare — no slot moved at all,
let alone more than one.

**9. No requirement previously `COMPLETED` now caps, or vice versa — PASS.**

Follows directly from the table above: every requirement kept its 2026-08-13 outcome.

---

## Token cost, both sides

Summed per-requirement `total_tokens` (excludes shared document-level stages —
`consistency_checker`/`dependency_mapper`/document-level tokens are identical on both
sides since those two prompts were not touched):

| Scenario | Baseline tokens | v2 tokens | Delta |
|---|---|---|---|
| scn-04-conflict-numeric | 23,707 | 24,997 | +1,290 |
| scn-07-dilution | 72,127 | 74,766 | +2,639 |
| scn-10-atomicity | 23,395 | 23,510 | +115 |
| scn-12-routing | 34,642 | 32,549 | −2,093 |
| **Total** | **153,871** | **155,822** | **+1,951 (+1.3%)** |

Net token count is close to flat; the `quality_checker.txt` addition (examples, longer
category definition) costs input tokens on every call, while shorter model
explanations and fewer follow-up rounds on some requirements (e.g. `scn-12`'s net
decrease) partly offset it. Consistent with the plan's ~$0.38 estimate; no cost
outlier observed on either side.

---

## Summary

| # | Prediction | Verdict |
|---|---|---|
| 1 | `THEMAS-REQ-B` no longer `non_atomic` | **Held** |
| 2 | `PURE-ERTMS-R2` no longer `non_atomic` | **Refuted** |
| 3 | `LUITEL-R7` still `non_atomic` (control) | **Held** |
| 4 | `LUITEL-R1` keeps `5s`, no `[TBD]` | **Held** |
| 5 | `AUTOGEN-US2` no deferral phrase | **Held** |
| 6 | `PURE-THEMAS-R6` byte-for-byte unchanged | **Held** |
| 7 | No new `VALIDATION_FAILURE` | **Pass** |
| 8 | Outcome mix stable | **Pass** |
| 9 | No `COMPLETED`↔cap flips | **Pass** |

5 of 6 predictions held, all three regression guards pass. The one refutation
(prediction 2) is not adjusted for — the v2 prompt was not touched after this run. See
`design/DESIGN_NOTES.md` Known Limitations 8 and 11 for the fold-in of this result,
and `IMPLEMENTATION_LOG.md` for the change record.
