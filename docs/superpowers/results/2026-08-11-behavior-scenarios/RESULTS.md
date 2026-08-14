# Results — 2026-08-11 behavior scenarios, real run

Executed 2026-08-13 against the real Gemini API, paid tier (`GEMINI_API_KEY_PAID`),
model `gemini-3.6-flash`, `temperature: 1.0`, via `run_driver.py` (this directory) —
`orchestrator.cli._run` with the paid-key adapter factory and the shared answer policy
from `docs/superpowers/results/2026-08-10-first-real-run/answer_policy_driver.py`,
imported unchanged. n=1 per scenario (see spec doc, "Threats to validity" — not
repeated 3×; that is a stated limitation of this run, not an oversight).

All 14 runs (13 scenarios, S11 run twice) executed in the spec doc's suggested order.
Both controls (S2, S8) behaved correctly — no STOP condition was hit at any point in
the suite. No scenario needed a fixture substitution (S11 did not need
`AUTOGEN-US4`). `orchestrator/`, `design/`, and the prompts were not modified.

**Suite-wide headline, stated up front because it recurs in nearly every scenario
below:** zero schema-validation failures, zero transport failures, zero wrong-id
mismatches, across all 263 attempts in the whole suite. One `fatal_failure`, and it was
the deliberately forced one (S13). See "Cross-cutting checklist tallies" for the exact
numbers.

---

## S2 — negative control

**HARD**
- `DocumentOutcome.COMPLETED` — pass.
- Both `ConsistencyReport`/`DependencyReport` present — pass.
- `relevant_conflicts`/`relevant_dependencies` are `[]` not `None` — pass (verified via
  code guarantee: `consistency_report`/`dependency_report` are non-`None`, so
  `conflicts_for`/`dependencies_for` — both list comprehensions, never return `None` —
  are what gets passed; `RequirementRunRecord` does not persist these as its own
  fields, so this is confirmed by report-presence + code inspection, not a stored
  field read).

**SOFT** — HIT. `conflicts == []`, `dependencies == []`. Both requirements classified
`other`, passed the Quality Checker first round with `issues == []`.

**Tokens/cost:** in 11,820, out 1,629 → **$0.0299**.

**Surprising:** nothing. This is the cleanest possible result for a negative control.

---

## S8 — clean requirement, control

**HARD**
- `RunOutcome.COMPLETED` — pass.
- `rounds` length 1, `rounds[0].turn`/`rewrite` both `None` — pass.
- `test_strategy`/`test_plan` both populated — pass.
- `final_text == original text` — pass.

**SOFT** — HIT. `passed == True`, `issues == []`.

**Tokens/cost:** in 6,687, out 874 → **$0.0166**.

**Surprising:** the known risk called out in the ground truth (`VAGUE_PRONOUN` on "that
thermostat") did **not** fire. No false positive here — worth noting since Known
Limitation 4 (`CLAUDE.md`) documents this category as expected-noisy in general.

---

## S4 — inconsistency, unambiguous numeric (floor case)

**HARD**
- `DocumentOutcome.COMPLETED` — pass.
- Conflict names exactly `PURE-THEMAS-R6`/`PURE-THEMAS-R6-P`, distinct — pass.
- `conflicts_for()` reaches the Quality Checker for both — pass (both got an
  `inconsistent` issue every round).

**SOFT** — HIT. One conflict, both ids, explanation: *"PURE-THEMAS-R6 states that the
temperature reported shall not exceed a maximum deviation of 3 degrees Fahrenheit,
whereas PURE-THEMAS-R6-P permits a temperature deviation of up to 5 degrees Fahrenheit
before reporting an error."* Both numbers named explicitly.

**Tokens/cost:** in 23,382, out 2,271 → **$0.0521**.

**Surprising:** both requirements hit `RunOutcome.CAP_STOPPED` — the answer policy's
`INCONSISTENT` response is deliberately conservative ("this is a cross-requirement
conflict... record it as-is rather than have this answer silently pick a side"), so
neither requirement ever resolves and the cap fires at round 3 for both. This is
expected behavior given the policy, not a scenario failure — the floor case for
*detection* is a full hit; it just never reaches test generation because of how the
scripted human answers this category. Also: `Issue.id` stability (contract item 4) was
**brittle** here — the checker reworded the `span` field between identical-text rounds
(e.g. `"maximum deviation value of 3 degrees Fahrenheit"` → `"a maximum deviation value
of 3 degrees Fahrenheit"`, one added word), which broke the (category, span)-based
reuse match and minted a fresh id even though nothing about the underlying defect
changed. Four fresh ids minted across the two requirements' 6 rounds.

---

## S1 — dependency, minimal pair

**HARD**
- `DocumentOutcome.COMPLETED` — pass.
- Both reports present, `doc_id` agrees — pass.
- Both requirements reach `COMPLETED` or a cap outcome — pass (`PURE-ERTMS-R7`
  completed, `PURE-ERTMS-R8` hit `CAP_STOPPED`, both accepted by this bullet).
- `dependencies_for("PURE-ERTMS-R8")` threaded into Strategy Selector/Test Generator
  **for R8** — **NOT EXERCISED.** `PURE-ERTMS-R8` hit the revision cap on an
  `INCOMPLETE` issue before ever reaching those stages (the answer policy's
  `INCOMPLETE` response declines to invent the missing trigger/condition, so the
  requirement never passes). `test_strategy`/`test_plan` are both `None` for R8. This
  hard bullet cannot be verified this run — reported plainly rather than marked pass.

**SOFT** — HIT. Exactly one link, `from=PURE-ERTMS-R8, to=PURE-ERTMS-R7`, correct
direction, explanation: *"The DMI requirement to indicate the self-test result depends
on the on-board equipment performing the self-test defined in PURE-ERTMS-R7."*
`conflicts == []`.

**Tokens/cost:** in 17,605, out 1,422 → **$0.0371**.

**Surprising:** although R8 (the dependent/"from" side) never reached test generation,
R7 (the "to" side) **did** — and `dependencies_for` is symmetric (matches either side
of a link), so R7's Strategy Selector also received this dependency and visibly used
it: rationale explicitly says *"downstream dependencies (such as PURE-ERTMS-R8
depending on this requirement), justifying 'use_case' testing,"* and the generated test
plan includes a case (`TC-13-PURE-ERTMS-R7-2`) listing **both** `PURE-ERTMS-R7` and
`PURE-ERTMS-R8` in `requirement_ids`. See ANALYSIS.md for the full discussion — this
directly bears on Known Limitations 1, 6, and 7.

---

## S9 — ambiguity that must be caught, plus one known not to be

**HARD**
- `passed == False` on both first-round reports, non-empty `issues` — pass.
- `Issue.id` stable across rounds (contract item 4) — **largely held this run**:
  `THEMAS-REQ-E`'s two issue ids were identical across all 3 rounds; `THEMAS-REQ-D`'s
  id changed once between round 0→1 (expected — the text itself changed then) and
  stayed stable round 1→2 (text unchanged, id unchanged). Contrast with S4, where the
  same mechanism broke on unchanged text.
- Suppression accumulation (item 5) — **not exercised**: the shared answer policy never
  sets `user_confirms_resolved=True` for `VAGUE_PRONOUN`, so `suppressed_issue_ids`
  stayed `[]` every round for both requirements. This scenario, with this policy,
  cannot test that mechanism.

**SOFT**
- `THEMAS-REQ-D` → `VAGUE_PRONOUN` on "these limits" / "the specified temperature
  limits" — HIT.
- `THEMAS-REQ-E` → `VAGUE_PRONOUN` ×2 ("this condition", "this module") every round —
  HIT.
- `LO = T_LT` — **never flagged, in any round, under any category.** Known Limitation
  5 is confirmed exactly as documented in `CLAUDE.md`; this run does not weaken it. No
  `DESIGN_NOTES.md` update is warranted (the note's caveat only applies if it *is*
  flagged, which it was not).

**Tokens/cost:** in 22,926, out 2,187 → **$0.0508**.

**Surprising:** both requirements hit `CAP_STOPPED` since `VAGUE_PRONOUN` is never
confirmed-resolved by the shared policy.

---

## S10 — non-atomic and unmeasurable

**HARD** — `refined_text` is a single string in both records, schema-trivially — but
for `LUITEL-R7` this check is **moot**: the Rewriter's round-0 output was a **no-op**
(`refined_text == original_text`, byte for byte), so there was never an actual rewrite
to inspect for smuggled-in list structure. The scenario's intended check (does the
Rewriter split three behaviors into one string vs three) went untested because the
Rewriter did not attempt a rewrite at all.

**SOFT**
- `LUITEL-R7` → `NON_ATOMIC` on span *"inventory levels, product movement, and sales
  history"* — HIT, and **genuine** (three independently testable report types, exactly
  the shape the task's own definition of "genuine" describes).
- `AUTOGEN-US2` → `AMBIGUOUS_TERM` ×2 ("reliable", "efficient") — HIT. `NON_ATOMIC` was
  also raised on "reliable and efficient," which ground truth did **not** predict —
  extra flag, conjunction-split (two adjectives describing one behavior, not two
  independently testable behaviors).

**Tokens/cost:** in 21,872, out 3,343 → **$0.0579**.

**Surprising, and significant:**
1. **`LUITEL-R7` reached `RunOutcome.COMPLETED` via a no-op rewrite** — round 0 flagged
   `NON_ATOMIC` (confirmed-resolved=True by the policy) and `INCOMPLETE`
   (confirmed=False); the Rewriter changed nothing; round 1 re-checked the *identical*
   text and returned `passed=True, issues=[]`. This is Known Limitation 10's exact
   pattern, this suite's first observed instance (in addition to `THEMAS-REQ-A` from
   2026-08-10).
2. That round-0→1 transition is also a **verdict flip on unchanged text**: `passed`
   went `False → True` on identical `text_checked`, and not only was the confirmed
   issue (`NON_ATOMIC`) dropped (expected, via suppression) but the *unconfirmed*
   `INCOMPLETE` issue also silently disappeared, with no suppression recorded for it.
   The Quality Checker did not re-flag something it had itself flagged one round
   earlier on the exact same text.
3. `AUTOGEN-US2`'s Rewriter inserted literal bracket-placeholder text into the
   requirement itself — `"reliable (meeting [reliability threshold, TBD]) and
   efficient (meeting [efficiency threshold, TBD])"` — in response to the
   `AMBIGUOUS_TERM` policy answer's "treat it as a placeholder" instruction, taken
   literally. The requirement's own text now contains `[TBD]` markers. Not caught by
   any schema rule (any string is a valid `refined_text`). This exact pattern recurred
   in S3 and S12 below — worth treating as a general Rewriter behavior, not a one-off.

---

## S12 — classification and technique routing

**HARD** — every technique selected for the one requirement that reached Strategy
Selection (`THEMAS-REQ-B`) is schema-legal (0 validation failures on `strategy_selector`
and `test_generator`) — pass. Every `TestCase` covers its plan's requirement,
schema-enforced — pass.

**SOFT**
- `THEMAS-REQ-B` → completed. Techniques: `equivalence_partitioning`,
  `boundary_value_analysis` (both expected) plus `decision_table` (reasonable Layer-2
  addition, rationale ties it to the requirement's combined-condition structure) — HIT.
- `ACTAPP-R2-AC1` → classified `AI_SYSTEM` — **the first non-`other` classification in
  any real run of this pipeline** (Known Limitation 9's every-run-so-far-`other`
  pattern is broken here). Rationale: *"a human activity recognition classification
  task where the system behavior relies on a machine-learning model."* But the
  requirement hit `CAP_STOPPED` before Strategy Selection, so which of
  `metamorphic`/`statistical_threshold`/`adversarial` it would have picked is
  **unanswerable this run.**
- `LUITEL-R1` → classified `other` (not `AI_SYSTEM`/`PERFORMANCE` — `PERFORMANCE` is a
  technique, not a `SystemType`, so this is not a contradiction). Also hit
  `CAP_STOPPED` before Strategy Selection, so whether `performance` would be selected
  for it — Known Limitation 3's open question — **remains open; this run does not
  answer it.**

**Tokens/cost:** in 32,577, out 4,015 → **$0.0790**.

**Surprising:** `THEMAS-REQ-B` shows the same no-op-rewrite + verdict-flip pattern as
`LUITEL-R7` above — round 0 flagged `NON_ATOMIC` on *"shall identify the current
temperature value as an invalid temperature and shall output an invalid temperature
status"* (a clean conjunction-split of one causal step), the rewrite was a no-op, and
round 1 passed clean on identical text. Third real-run instance of this pattern
(`THEMAS-REQ-A` 2026-08-10, `LUITEL-R7` S10, `THEMAS-REQ-B` here).

---

## S3 — inconsistency, minimal pair (native, hard case)

**HARD** — conflict names exactly `ACTAPP-R1`/`ACTAPP-R2`, distinct — pass.
`conflicts_for()` reaches the Quality Checker for both — pass (`INCONSISTENT` raised on
both every round).

**SOFT** — **HIT, the genuinely hard case was caught.** Conflict found: *"ACTAPP-R1
requires that patients receive a notification to move if they have been sitting for a
long period, whereas ACTAPP-R2 states patients should not receive notifications when
busy. These requirements directly contradict each other when a patient has been
sitting for a long period while being busy."* This names the overlap case, not just a
restatement. `INCONSISTENT` raised on **both** requirements every round (exceeds the
"at least one" bar).

**Tokens/cost:** in 24,894, out 4,262 → **$0.0693**.

**Surprising:** the Dependency Mapper found an **unplanted** bidirectional pair — `R1→R2`
*and* `R2→R1` — a 2-node cycle this fixture was never designed to contain.
`CIRCULAR_DEPENDENCY` was raised on both requirements every round, with issue id
`ISSUE-3` staying perfectly stable across all 3 rounds for both (one of the few fully
stable ids in the whole suite). Both requirements hit `CAP_STOPPED` (the policy declines
to resolve either `INCONSISTENT` or `CIRCULAR_DEPENDENCY` from a single requirement's
answer). This is a second, independent real-world confirmation (alongside S6, which was
purpose-built for it) that a detected cycle routes to the Refiner.

---

## S5 — three-way inconsistency

**HARD** — conflict's `requirement_ids` distinct, subset of the 3 fixture ids — pass
(exact match).

**SOFT — FULL HIT, the top bar, not the partial one.** One conflict naming **all
three** ids, with an explanation that reasons about the three-way interaction, not two
pairwise restatements: *"PURE-THEMAS-R4 mandates a maximum limit on active units,
PURE-THEMAS-R4-P1 sets this maximum to three units, and PURE-THEMAS-R4-P2 requires
turning on four heating units simultaneously during a system-wide cold start. These
requirements jointly contradict each other because activating four units
simultaneously exceeds the specified maximum limit of three."* This directly answers
the ANALYSIS.md question: **one three-way conflict, not two pairwise ones.**

**Tokens/cost:** in 37,475, out 5,898 → **$0.1004**.

**Surprising:** the Dependency Mapper again produced an unplanted 2-cycle
(`PURE-THEMAS-R4 ↔ PURE-THEMAS-R4-P1`), with `PURE-THEMAS-R4-P2` correctly **excluded**
from `CIRCULAR_DEPENDENCY` (it points into the cycle but isn't part of it) — a third
unplanted real-world instance of cycle detection, this time showing correct membership
selectivity, not just presence. All three requirements no-op-rewrote every round and
hit `CAP_STOPPED`.

---

## S6 — circular dependency

**HARD**
- No self-referential `DependencyLink` — trivially pass.
- `find_cycles()` returns exactly one cycle covering all three ids — **verified
  programmatically**: `DependencyReport.find_cycles()` on the actual run record returns
  `[['SCN6-A-P', 'SCN6-B-P', 'SCN6-C-P']]`. Pass.

**SOFT — FULL HIT.** All three links found, correct directions (A→B→C→A exactly).
`CIRCULAR_DEPENDENCY` raised on all three requirements (exceeds "at least one"). The
open question — does a detected cycle actually route to the Refiner — is answered:
**yes**, confirmed against the real record (all three hit the refine loop over this
category, then `CAP_STOPPED` since the policy declines structural fixes).

**Tokens/cost:** in 35,871, out 3,764 → **$0.0820**.

**Surprising:** the Consistency Checker *independently* flagged the same three
requirements as a "conflict," describing it in its own words as a "circular dependency
deadlock." Two document-level stages, unaware of each other's output, converged on the
same structural signal from two different angles.

---

## S11 — the revision cap, both branches

`AUTOGEN-US3` genuinely never resolved in either run — `ambiguous_term`/`incomplete`/
`non_verifiable` persisted across all 3 rounds in both. No need for the `AUTOGEN-US4`
fallback.

**HARD**
- Run A (`scn-11a-cap-generate`): `RunOutcome.CAP_GENERATED`, `cap_reason` non-empty,
  `test_strategy`/`test_plan` both populated, `len(rounds) == 3` — pass, exactly.
- Run B (`scn-11b-cap-stop`): `RunOutcome.CAP_STOPPED`, `test_strategy`/`test_plan`
  both `None`, `errors == []` (no `StageError` for `strategy_selector`/
  `test_generator`), `len(rounds) == 3` — pass, exactly.

**SOFT** — HIT. Issues genuinely persist all three rounds in both runs; the checker
never gave up and passed the requirement to make the cap fire artificially.

**Tokens/cost:** run A in 15,038, out 1,793 → $0.0360. Run B in 12,463, out 1,480 →
$0.0298. Combined **$0.0658**.

**Surprising:** nothing beyond what the scenario was built to show — this is the
cleanest binary contract-behavior confirmation in the suite.

---

## S13 — forced `DEGRADED` document, real adapter

**HARD — every bullet confirmed exactly.**
- `DocumentOutcome.DEGRADED` — pass.
- `consistency_report is None`, `dependency_report` present — pass.
- One `DocumentStageError`, `kind=FATAL`, `retry_count == 0` (exactly one attempt) —
  pass. Real adapter behavior: Gemini returned HTTP 404 ("models/
  gemini-nonexistent-model-x9 is not found"), classified as `fatal_failure`, zero
  retries attempted.
- `relevant_conflicts is None` / `relevant_dependencies` real list — confirmed by code
  guarantee (same reasoning as S2).
- Processing continued to completion for both requirements: `PURE-ERTMS-R7` completed
  normally; `PURE-ERTMS-R8` hit `CAP_STOPPED` on `INCOMPLETE` — same terminal outcome as
  in S1 and S7 below, on the identical requirement text.

**"Also worth checking" item (contract item 6, `retry_document_stage` guard):** **not
exercised.** No resume was attempted in this run — this would require a separate
`resume` invocation, out of scope for a single `run`.

**SOFT** — none required.

**Tokens/cost:** in 16,864, out 1,511 → **$0.0366**.

**Surprising:** `dependency_mapper` ran and completed normally on the same input,
unaffected by `consistency_checker`'s fatal failure — confirming the two document-level
stages fail independently, as the contract requires.

---

## S7 — signal in a larger document (8-req ERTMS, run last)

**HARD**
- `DocumentOutcome.COMPLETED` — pass.
- No duplicate ordered pairs (schema-enforced) — pass.
- Every id in every link exists in `{R1..R8}` — pass.

**SOFT** — **both known links found, and nothing else: total link count = 2.** `R5→R4`
and `R8→R7`, both correct direction. This is **better than the "lower bound" framing
in the ground truth expected** — zero unverified/extra links, no explosion (the
document's other plausible pair, R3/R6's shared level-transition topic, was correctly
*not* linked).

**Tokens/cost:** in 65,761, out 8,792 → **$0.1646**. (8 requirements, most expensive
scenario as expected — 22% of the suite's total cost.)

**Surprising, and important for reproducibility:**
1. `PURE-ERTMS-R8` hit `CAP_STOPPED` on the same `INCOMPLETE` issue as in S1 and S13 —
   **the third separate real run** in which this specific requirement text never
   reaches test generation under this answer policy, regardless of surrounding
   document size (2 requirements in S1/S13, 8 here). This is a reproducible property of
   the requirement text + policy combination, not a one-off sampling artifact.
2. `PURE-ERTMS-R7`'s Strategy Selector rationale **again** explicitly cites the
   dependency on R8 (*"direct dependency relationship with downstream requirements such
   as result display (PURE-ERTMS-R8)"*), and its test plan **again** includes a joint
   test case (`TC-13-PURE-ERTMS-R7-2`, `requirement_ids=[R7, R8]`) — an independent
   second replication of the S1 finding, in a different document context. See
   ANALYSIS.md.
3. `PURE-ERTMS-R2` was flagged `NON_ATOMIC` on "train and shunting movements" —
   borderline-genuine (two distinct operational modes, arguably independently
   testable, not a causal chain).

---

## Cross-cutting checklist tallies (`2026-08-08-first-real-run-checklist.md` method)

Computed directly from every `attempts`/`document.json` `attempts` array across all 14
run directories (263 total attempts, doc-level + per-requirement, all scenarios).

| Metric | Value |
|---|---|
| Wrong-requirement-id rate (all stages, all models) | **0 / 263 validation failures — 0%** (there were zero validation failures to check, so this rate has nothing to divide into; report as 0 occurrences, not as an undefined percentage) |
| Schema-validation-failure rate (denom: SUCCESS+VALIDATION_FAILURE) | **0 / 262 = 0%** |
| Transport-failure rate (denom: all attempts) | **0 / 263 = 0%** |
| Fatal-failure count (not folded into either rate above) | **1** (S13, deliberately forced) |
| Total attempts | 263 (262 success, 1 fatal, 0 validation, 0 transport) |
| Total tokens | **345,235 in / 43,241 out** |
| Total cost ($1.50/1M in, $7.50/1M out) | **$0.5179 + $0.3243 = $0.8422** |

By stage (success counts; every stage had 0 validation/transport failures across the
whole suite):

| Stage | Successes |
|---|---|
| consistency_checker | 13 (+ 1 forced fatal) |
| dependency_mapper | 14 |
| classifier | 34 |
| quality_checker | 81 |
| refiner_questioner | 47 |
| refiner_rewriter | 47 |
| strategy_selector | 13 |
| test_generator | 13 |

This suite's real-run total ($0.84) landed inside the spec doc's predicted n=1 full-pass
range ($0.55–$1.05).

---

## Four suite-wide tallies

### No-op rewrites (Known Limitation 10)

**38 rounds** where `rewrite.refined_text == rewrite.original_text`, across every
scenario except S2/S8 (no rewrite loop entered) and S1's `PURE-ERTMS-R7` (never entered
the refine loop). Full per-requirement counts: `PURE-THEMAS-R6-P` (S4) ×2,
`PURE-THEMAS-R6` (S4) ×2, `PURE-ERTMS-R8` (S1) ×2, `THEMAS-REQ-D` (S9) ×1,
`THEMAS-REQ-E` (S9) ×2, `AUTOGEN-US2` (S10) ×1, `LUITEL-R7` (S10) ×1, `ACTAPP-R2-AC1`
(S12) ×1, `THEMAS-REQ-B` (S12) ×1, `LUITEL-R1` (S12) ×1, `ACTAPP-R2` (S3) ×2,
`ACTAPP-R1` (S3) ×1, `PURE-THEMAS-R4-P1` (S5) ×2, `PURE-THEMAS-R4` (S5) ×2,
`PURE-THEMAS-R4-P2` (S5) ×2, `SCN6-C-P`/`SCN6-B-P`/`SCN6-A-P` (S6) ×2 each,
`AUTOGEN-US3` (S11a and S11b) ×1 each, `PURE-ERTMS-R8` (S13) ×2, `PURE-ERTMS-R8`/
`PURE-ERTMS-R5` (S7) ×2 each, `PURE-ERTMS-R1` (S7) ×1.

**Requirements that reached `RunOutcome.COMPLETED` via a no-op rewrite: 2.**
- `LUITEL-R7` (S10) — see S10 write-up above.
- `THEMAS-REQ-B` (S12) — see S12 write-up above.

Both are new instances beyond `THEMAS-REQ-A` (2026-08-10) — Known Limitation 10 is now
observed 3 times across 2 real-run sessions, not a single occurrence.

### Verdict flips on unchanged text

**2 flips**, both the same shape: consecutive rounds with identical `text_checked` but
`quality_report.passed` flipping `False → True`.
- `LUITEL-R7` (S10), round 0→1.
- `THEMAS-REQ-B` (S12), round 0→1.

In both cases, one issue was legitimately suppressed (confirmed-resolved by the
answer policy) but the *other*, unconfirmed issue also silently disappeared from the
next round's `issues` list with no corresponding entry in `suppressed_issue_ids` — the
Quality Checker did not just accept the suppression, it stopped re-flagging something
it had itself found one round earlier on byte-identical text.

### `infeasible_for_type` issues

**Zero**, across the entire suite — including `ACTAPP-R2-AC1` (S12), which classified
`AI_SYSTEM` for the first time in any real run. The category did not fire at all this
run, so the "false positive against `other`" shape from Known Limitation 9's
`THEMAS-REQ-C` precedent was not reproduced or contradicted — simply not observed.

### `non_atomic` issues

**5 flags**, across 4 distinct requirements:

| Requirement | Scenario | Span | Classification |
|---|---|---|---|
| `AUTOGEN-US2` | S10 | "reliable and efficient" (round 0) | **Conjunction-split** — two adjectives on one behavior, not independently testable |
| `AUTOGEN-US2` | S10 | "reliable (meeting [...TBD]) and efficient (meeting [...TBD])" (round 1) | **Conjunction-split** — same underlying defect re-flagged after the rewrite inserted TBD placeholders |
| `LUITEL-R7` | S10 | "inventory levels, product movement, and sales history" | **Genuine** — three independently testable report types, the task's own canonical example of a genuine case |
| `THEMAS-REQ-B` | S12 | "shall identify...and shall output..." | **Conjunction-split** — one causal step (identify, then output), joined by "and" |
| `PURE-ERTMS-R2` | S7 | "train and shunting movements" | **Borderline/genuine** — two distinct operational modes, arguably independently testable, not a causal chain |

**3 of 5 (60%) are conjunction-splits; 2 of 5 (40%) are genuine or borderline-genuine.**
This is a materially different mix than the 2026-08-10 runs, where all 14 flags were
conjunction-splits — see Predictions below.

---

## Predictions — stated in advance, checked against this run

1. **"`non_atomic` will over-flag: most or all flags will be conjunction-splits."**
   **Partially refuted.** 3 of 5 flags this run were conjunction-splits, but 2 were
   genuine or borderline-genuine (`LUITEL-R7`'s three-report case matches the task's
   own textbook example of a real multi-behavior requirement). The prior 14/14
   conjunction-split rate does not hold up at this small additional sample — record
   this plainly rather than force the earlier framing.

2. **"`infeasible_for_type`, if it fires, may fire against `other`."** **Not
   testable this run** — the category never fired at all, including on
   `ACTAPP-R2-AC1`, which is classified `AI_SYSTEM` (not `other`) for the first time.
   No evidence either way.

3. **"Dependency context may show no visible effect on generated test cases (S1)."**
   **Refuted, with two independent replications.** In both S1 and S7, the requirement
   on the *dependent* side of the link (`PURE-ERTMS-R8`) never reached test generation
   (hit the cap first), so that half of the question is unanswered both times. But the
   requirement on the *depended-upon* side (`PURE-ERTMS-R7`) received the same
   dependency (`dependencies_for` matches either side of a link) and its Strategy
   Selector rationale and generated test cases visibly and reproducibly referenced the
   dependency in both runs. Dependency context does change generated test content —
   just not (yet, in any real run) demonstrated on the side the contract's item 16
   language emphasizes.

4. **"No-op rewrites will occur, most likely where a human answer overrides an issue
   or declares it unfixable."** **Confirmed, but incompletely.** 38 no-op rewrites
   occurred. Most are on categories the answer policy declines to resolve
   (`INCONSISTENT`, `CIRCULAR_DEPENDENCY`, `INCOMPLETE`, `VAGUE_PRONOUN`) — consistent
   with the prediction's mechanism. But the two instances that reached `COMPLETED`
   (`LUITEL-R7`, `THEMAS-REQ-B`) involved a *confirmed-resolved* `NON_ATOMIC` answer,
   and the no-op was on the Rewriter's side (it made no edit despite being told to
   "keep as one requirement," which does not itself require zero changes) — not a
   simple "the human declared it unfixable" case.

5. **"The Quality Checker will contradict itself on at least one unchanged text."**
   **Confirmed**, twice (`LUITEL-R7`, `THEMAS-REQ-B`) — see "Verdict flips" above.
