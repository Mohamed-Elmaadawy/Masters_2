# Behavior scenarios — small, ground-truthed runs against real LLM APIs

**Status: specification only. Nothing built, nothing run, no results.** Written
2026-08-11. This file defines the fixtures and the expectations; the fixture JSON
files, the run configs and the analysis are separate, later steps.

---

## What this is, and what it is not

`orchestrator/test_harness.py` (60+ tests, green) drives the pipeline with **fake**
stage functions. It proves the orchestrator's *control flow* — resume positions, the
revision cap, suppression, id-mismatch handling, `None` vs `[]`, `DEGRADED` — and it
proves it deterministically, which is why it can be a pass/fail suite.

It cannot answer a different class of question: **given a real model and the v1
prompts, does the pipeline actually notice the thing that is there?** Does the
Dependency Mapper find a dependency that genuinely exists? Does the Consistency
Checker find a contradiction that is genuinely present — and does it stay quiet when
one is not? That needs real calls against inputs whose correct answer is known in
advance.

So these scenarios are a **second layer**, not a replacement:

| | `test_harness.py` | this file |
|---|---|---|
| Stage fns | fake | real provider adapters |
| Deterministic | yes | no |
| Proves | control flow is correct | detection behavior on known inputs |
| Cost | free | API calls |
| Runs in CI | yes | no — run deliberately, record results |

**Do not merge these into the harness suite.** A non-deterministic assertion in a
suite that is supposed to be green is a suite people learn to ignore.

---

## Data provenance — the rule this suite follows

`datasets/EVALUATION_DATASETS.md` reserves PURE (~77 untouched documents), PROMISE
NFR, Dalpiaz and Riaz for the evaluation phase, and says plainly why: designing or
tuning against a corpus you later evaluate on stops measuring performance and starts
measuring memorisation.

**These scenarios therefore draw only from already-spent material:**

1. `datasets/requirements_dataset.json` — illustrative requirements quoted inside
   papers already in `papers/`, plus `pure-themas-1998-full` and `pure-ertms-2007`,
   the two PURE documents explicitly burnt on the 2026-08-04 schema spot-check and
   already excluded from the evaluation subset.
2. **Planted text**, written by hand for this suite and marked as such in every
   fixture. Necessary because no available corpus labels conflicts or dependency
   ground truth — the one dataset that would have (Zogaan et al.) turned out to be a
   survey paper, not a corpus (`EVALUATION_DATASETS.md` §6).

**Consequence to state in the write-up, not hide:** several fixtures are partly
synthetic, so their conflicts are cleaner and more findable than real SRS
contradictions. These scenarios establish that detection *works at all* on
unambiguous cases. They do not estimate real-world recall — that is the evaluation
phase's job, on untouched PURE documents.

### Fixture conventions

- One JSON file per scenario, `RequirementSet` shape, the same shape
  `orchestrator/cli.py`'s `run` takes — so no new tooling is needed
  (`orchestrator/extract_document.py` already produces this shape from a document
  list, and can be reused if these are collected into one file).
- `doc_id`: `scn-NN-slug`, e.g. `scn-01-dep-pair`.
- Requirement ids keep their **source id** where the text is unmodified
  (`PURE-ERTMS-R4`), so provenance is readable straight off the record. Planted
  requirements get a `-P` suffix (`PURE-THEMAS-R6-P`) — every `-P` id in a record is
  authored text, checkable by grep, not by memory.
- Each fixture file gets a sibling note stating: source, what was planted, and the
  ground truth. Ground truth in a comment nobody can execute drifts; ground truth in
  a file next to the fixture at least sits with it.

---

## Two kinds of expectation, kept apart

Every scenario states both, separately:

- **Hard** — deterministic, machine-checkable properties of the run record.
  `outcome`, which fields are populated vs `None`, list lengths, id agreement,
  `DocumentOutcome`. These hold regardless of what the model said, and a violation is
  an orchestrator bug.
- **Soft** — what the model was supposed to *find*. Judged on inspection, recorded as
  hit/miss/partial with the actual output quoted. A miss is a **finding**, not a
  failure — "the Consistency Checker missed a planted 3°F-vs-5°F contradiction" is a
  result worth reporting.

Never promote a soft expectation into an automated assertion. One model update turns
the suite red for a reason that has nothing to do with the code.

---

## Group A — document-level stages (Consistency Checker, Dependency Mapper)

### S1 — Dependency, minimal pair *(native, nothing planted)*

**Probes:** Dependency Mapper finds a real behavioral dependency in the smallest
possible document. Direction matters, not just presence.

**Input** — `scn-01-dep-pair`, 2 requirements, verbatim from `pure-ertms-2007`:

- `PURE-ERTMS-R7` — "At Start Up, the on board equipment shall perform an automatic
  self-test."
- `PURE-ERTMS-R8` — "The DMI shall indicate the result of the self-test."

**Ground truth:** R8 depends on R7 — R8 cannot be tested without R7 having produced a
result. One link, direction `from=R8, to=R7`. No conflict.

**Hard:** `DocumentOutcome.COMPLETED`; both reports present; `doc_id` on both reports
agrees with the set; both requirements reach `RunOutcome.COMPLETED` or a cap outcome;
`dependencies_for("PURE-ERTMS-R8")` is threaded into the Strategy Selector and Test
Generator for R8 (contract item 16).

**Soft:** exactly one link, in that direction, with an explanation naming the
self-test. `conflicts == []`.

**Known risk:** the mapper may invert the direction. The schema cannot catch that —
`DependencyLink` only rejects self-loops. Direction accuracy is a soft result, and if
it is wrong here it will be wrong everywhere; worth its own tally across all Group A
scenarios.

---

### S2 — Negative control: no dependency, no conflict

**Probes:** false positives. **Without this scenario, S1/S3/S4 prove nothing** — a
mapper that links everything to everything passes all of them.

**Input** — `scn-02-null-control`, 2 requirements, verbatim, deliberately from
*different* source documents and different domains:

- `METAGPT-US1` — "As a user, I want to select any color on the screen, so that I can
  get its RGB values."
- `HEY-REQ2` — "The audit report shall include the total number of recycled parts used
  in the estimate."

**Ground truth:** no dependency, no conflict.

**Hard:** `DocumentOutcome.COMPLETED`; both reports present; `relevant_conflicts` and
`relevant_dependencies` passed to each requirement's stages are `[]`, **not** `None`
(contract item 16 — `None` would mean the stage failed).

**Soft:** `conflicts == []` and `dependencies == []`.

**Note:** two requirements from unrelated documents is an easy negative. A harder
one — two requirements from the *same* document that are merely topically adjacent —
is worth adding later; S7 is a step in that direction.

---

### S3 — Inconsistency, minimal pair *(native, nothing planted)*

**Probes:** Consistency Checker on a contradiction that exists in real published
example material, and the downstream consequence — does the Quality Checker raise
`INCONSISTENT` on requirements it was handed conflicts for?

**Input** — `scn-03-conflict-native`, 2 requirements, verbatim from
`actapp-arora2024`:

- `ACTAPP-R1` — "The patients should receive a notification to stand up and move
  around if they have been sitting for long."
- `ACTAPP-R2` — "The patients should not receive notifications when busy."

**Ground truth:** conflicting under the case "the patient has been sitting for a long
time *because* they are busy" — R1 requires a notification, R2 forbids it. Neither is
wrong alone; the pair is unsatisfiable without a stated precedence rule.

**Hard:** `DocumentOutcome.COMPLETED`; one `ConsistencyConflict` (if found) has
exactly these two ids and they are distinct; `conflicts_for()` output reaches the
Quality Checker for **both** requirements.

**Soft:** the conflict is found; the explanation names the overlap case rather than
restating the two requirements; the Quality Checker raises `INCONSISTENT` on at least
one of them.

**Known risk — the honest one:** this is a *latent* conflict, not a flat
contradiction. A miss here is a genuinely interesting result and should not be
patched by rewording the fixture until it passes. If the checker misses it, S4 is the
control that says whether it misses *everything* or only the subtle case.

---

### S4 — Inconsistency, unambiguous numeric *(planted)*

**Probes:** the floor of consistency detection. If this is missed, nothing in S3/S5 is
interpretable.

**Input** — `scn-04-conflict-numeric`, 2 requirements:

- `PURE-THEMAS-R6` *(verbatim)* — "The THEMAS system shall ensure the temperature
  reported by a given thermostat shall not exceed a maximum deviation value of 3
  degrees Fahrenheit."
- `PURE-THEMAS-R6-P` *(planted)* — "The THEMAS system shall permit a temperature
  deviation of up to 5 degrees Fahrenheit for any thermostat before reporting a
  deviation error."

**Ground truth:** direct numeric contradiction, 3°F vs 5°F, same subject.

**Hard:** as S3.

**Soft:** conflict found, both ids present, explanation names both numbers.

**Note:** deliberately trivial. Its value is as a floor, and as the calibration point
against which S3's difficulty is read.

---

### S5 — Three-way inconsistency *(planted)*

**Probes:** `ConsistencyConflict.requirement_ids` allows 3+ ids by design
(`design/DESIGN_NOTES.md`). Nothing has ever tested whether the checker actually
produces one. Also the miniature of the planned scale experiment
(`EVALUATION_DATASETS.md`, "Planned experiment") — a conflict no pairwise comparison
can find.

**Input** — `scn-05-conflict-threeway`, 3 requirements:

- `PURE-THEMAS-R4` *(verbatim)* — "There shall be a maximum number of heating or
  cooling units that can be on at any given time."
- `PURE-THEMAS-R4-P1` *(planted)* — "The maximum number of heating or cooling units
  that may be on simultaneously shall be three."
- `PURE-THEMAS-R4-P2` *(planted)* — "During a system-wide cold start, the THEMAS
  system shall turn on the heating unit in every one of the four zones at the same
  time."

**Ground truth:** each requirement is satisfiable alone and each *pair* is
satisfiable; only all three together are contradictory (cap of 3 vs 4 simultaneous
units). One conflict naming all three ids.

**Hard:** if a conflict is reported, its `requirement_ids` are distinct and are a
subset of the fixture's ids.

**Soft:** one conflict naming **all three**. Two pairwise conflicts is a **partial**
result, not a pass — and a specific, reportable finding: the checker reasons pairwise
even when handed the whole document. Zero conflicts is a miss.

---

### S6 — Circular dependency *(planted)*

**Probes:** `DependencyReport.find_cycles()` on real model output rather than
constructed input, and the `CIRCULAR_DEPENDENCY` issue category, which no run has
ever produced.

**Input** — `scn-06-cycle`, 3 requirements, all planted (a real corpus cycle would
have to be found rather than constructed, and none is known to exist in the spent
material):

- `SCN6-A-P` — "The scheduler shall not dispatch a job until the resource monitor has
  reported the current load."
- `SCN6-B-P` — "The resource monitor shall compute the current load only after the
  audit logger has recorded the previous dispatch decision."
- `SCN6-C-P` — "The audit logger shall record a dispatch decision only once the
  scheduler has dispatched the corresponding job."

**Ground truth:** A→B→C→A, one cycle of length 3.

**Hard:** no `DependencyLink` is self-referential (schema-enforced); if all three
links are found, `find_cycles()` returns exactly one cycle covering all three ids.

**Soft:** all three links found with correct directions; a `CIRCULAR_DEPENDENCY`
issue is raised on at least one requirement.

**Open question this scenario answers, and does not assume:** whether the orchestrator
actually routes a detected cycle to the Refiner. The contract says cycles route there
rather than being auto-resolved; verify that against the record rather than trusting
it.

---

### S7 — Signal in a larger document

**Probes:** precision under dilution. S1's dependency is the only thing in the
document, so finding it proves little about a real SRS.

**Input** — `scn-07-dilution`, 8 requirements: the full `pure-ertms-2007` set
(R1–R8), which contains two genuine dependencies (R8→R7 self-test; R5→R4
acknowledgement) among six other requirements.

**Ground truth:** at least R8→R7 and R5→R4. Other links are *plausible* (R3/R6 both
concern level transitions), so this fixture's ground truth is a **lower bound, not an
exact set** — say so when reporting, and count "links found that are not in the known
pair set" separately as *unverified*, never as false positives.

**Hard:** `DocumentOutcome.COMPLETED`; no duplicate ordered pairs (schema-enforced);
every id in every link exists in the set.

**Soft:** both known links present. Total link count — an explosion (say, 15+ links on
8 requirements) is itself a finding about mapper precision.

**Cost note:** 8 requirements × 6 per-requirement stages plus refinement rounds. This
is the expensive scenario. Run it last, after the cheap ones have shown the pipeline
is behaving.

---

## Group B — per-requirement stages

### S8 — Clean requirement, control

**Probes:** the Quality Checker does not invent issues, and the refine loop does not
fire when it should not. The per-requirement mirror of S2.

**Input** — `scn-08-clean`, 1 requirement, verbatim:

- `THEMAS-REQ-G` — "Each thermostat shall have a unique identifier by which that
  thermostat is identified in the THEMAS system." *(atomic, has an actor, verifiable,
  no vague term)*

**Hard:** `RunOutcome.COMPLETED`; if `passed` is true, `rounds` has length 1 and
`rounds[0].turn`/`rewrite` are `None`; `test_strategy` and `test_plan` both populated;
`final_text` equals the original text.

**Soft:** `passed == True`, `issues == []`.

**Known risk:** `VAGUE_PRONOUN` is documented as expected-noisy (Known Limitation 4),
and "that thermostat" is exactly the shape that trips it. A flag here is a
*calibration* data point for that limitation, not automatically a bug — record it as
such.

---

### S9 — Ambiguity that must be caught, plus one that is known not to be

**Probes:** the Quality Checker's core job, and the boundary of Known Limitation 5
(undefined domain notation) in the same run.

**Input** — `scn-09-vague`, 2 requirements, verbatim from `themas-fischbach2022`:

- `THEMAS-REQ-D` — "Temperatures that do not exceed these limits shall be output for
  subsequent processing."
- `THEMAS-REQ-E` — "If this condition is true, then this module shall output a request
  to turn on the heating unit in case LO = T_LT."

**Ground truth:** D → `VAGUE_PRONOUN` ("these limits") and/or `INCOMPLETE` (no actor).
E → `VAGUE_PRONOUN` ("this condition", "this module"). `LO = T_LT` is undefined
notation, which Known Limitation 5 says is **expected not to be caught**.

**Hard:** `passed == False` on both first-round reports (a `QualityReport` with
`passed=False` and `issues=[]` is schema-rejected, so a non-empty `issues` list comes
for free); each `Issue.id` stable across rounds per contract item 4; suppressions
accumulate per item 5.

**Soft:** the categories above. **Whether `LO = T_LT` is flagged is the interesting
number.** If it *is* flagged, Known Limitation 5 is less severe than documented and
`DESIGN_NOTES.md` should say so — with this run cited, not silently.

**Depends on:** a scripted human answer policy, since the refine loop asks questions.
Reuse `docs/superpowers/results/2026-08-10-first-real-run/answer_policy_driver.py` —
one reasoned answer per `IssueCategory`, written once, applied consistently, and
already documented as AI-generated rather than live-human (a stated threat to
validity, contract item 3). Do not write a second, different policy for this suite;
two policies make the two runs' results incomparable.

---

### S10 — Non-atomic and unmeasurable

**Probes:** `NON_ATOMIC` and `AMBIGUOUS_TERM` specifically, and whether the Rewriter's
output for a non-atomic requirement is one requirement or a smuggled-in list.

**Input** — `scn-10-atomicity`, 2 requirements, verbatim:

- `LUITEL-R7` — "The system shall generate reports on inventory levels, product
  movement, and sales history." *(three testable behaviors)*
- `AUTOGEN-US2` — "As a user, I want a product that is reliable and efficient so that
  I can depend on it." *(two unmeasurable adjectives, no threshold)*

**Ground truth:** R7 → `NON_ATOMIC`; US2 → `AMBIGUOUS_TERM` (and plausibly
`NON_VERIFIABLE`).

**Hard:** `RefinedRequirement.refined_text` is a single requirement string — the
schema does not split it, so if the rewriter returns three requirements joined by
newlines, the record accepts it. **Read the refined text by hand.** This is a gap the
schema cannot close and this scenario is the only thing looking at it.

**Soft:** the categories above; the rewrite for R7 is *one* behavior, not three.

---

### S11 — The revision cap, both branches

**Probes:** contract item 3 end-to-end with a real model — an irreducibly vague
requirement no rewrite can fix, so the cap actually fires.

**Input** — `scn-11-cap`, 1 requirement, verbatim:

- `AUTOGEN-US3` — "As a user, I want a product that meets my needs so that I can get
  value for my money."

Run **twice**, `max_revisions: 3`, with the human decision at the cap scripted
differently each time.

**Ground truth:** the checker keeps failing; the cap fires at round 3.

**Hard:** run A → `RunOutcome.CAP_GENERATED`, `cap_reason` non-empty,
`test_strategy`/`test_plan` populated. Run B → `RunOutcome.CAP_STOPPED`,
`test_strategy`/`test_plan` both `None`, and **no** `StageError` for
`strategy_selector`/`test_generator` (schema-enforced, contract item 7). Both:
`len(rounds) == 3`.

**Soft:** the issues genuinely persist across all three rounds rather than the checker
giving up and passing it. If the model passes this requirement, the cap never fires
and the scenario produces nothing — pick a second unfixable requirement
(`AUTOGEN-US4` is the same shape) rather than forcing it.

---

### S12 — Classification and technique routing

**Probes:** Layer 1 (schema-enforced: which techniques a `SystemType` may use) and
Layer 2 (prompt guidance only, contract item 11, auditable solely through
`TestStrategy.rationale`).

**Input** — `scn-12-routing`, 3 requirements, verbatim, chosen for three different
expected routes:

- `LUITEL-R1` — "The system shall reach a steady state within 5s after reconfiguration
  to maximize availability." → numeric threshold: `boundary_value_analysis`,
  `performance`.
- `THEMAS-REQ-B` — the strictly-less-than / strictly-greater-than temperature range
  requirement → `equivalence_partitioning` + `boundary_value_analysis`.
- `ACTAPP-R2-AC1` — "Accurately identifies when the user is driving." → an ML
  classifier with no single correct output: `SystemType.AI_SYSTEM`, and
  `metamorphic` / `statistical_threshold` / `adversarial`.

**Hard:** every selected technique is Layer-1-legal for the classified `SystemType`
(schema-enforced — a violation is a schema bug, not a model one); every `TestCase`
covers the plan's requirement (schema-enforced, Known Limitation 6 — note it if real
generator output starts getting rejected here, which is exactly the condition
`DESIGN_NOTES.md` says would justify loosening the rule).

**Soft:** the routes above; `rationale` states reasoning traceable to contract item
11's Layer-2 rules rather than restating the requirement.

**Known risk:** `ACTAPP-R2-AC1` may classify as `MOBILE` rather than `AI_SYSTEM`.
Both are arguably right — the app is mobile, the behavior is ML. Record which, and do
not treat a `MOBILE` classification as a failure; treat it as evidence that
`SystemType` is under-specified for hybrid cases, which belongs in threats to
validity.

---

## Group C — degradation

### S13 — Forced `DEGRADED` document, real adapter

**Probes:** contract item 8 and item 16's `None`-vs-`[]` distinction through a **real**
provider adapter. `test_harness.py` proves this with fakes; what it cannot prove is
that a real adapter classifies a real provider rejection as `StageCallFatal` and that
the resulting `None` genuinely reaches the Quality Checker's prompt.

**Input:** the `scn-01-dep-pair` fixture, unchanged. Only the run config changes: point
`consistency_checker` at a nonexistent model name so the adapter fails fatally.

**Hard:** `DocumentOutcome.DEGRADED`; `consistency_report is None`,
`dependency_report` present; one `DocumentStageError` with `kind=FailureKind.FATAL`
and `retry_count == 0` (item 17 — **exactly one attempt**, not `max_attempts`);
`relevant_conflicts is None` while `relevant_dependencies == []` or a real list;
processing continues to completion for both requirements.

**Soft:** none. This scenario is entirely about record shape.

**Also worth checking here:** that `retry_document_stage` refuses once any requirement
has been processed (contract item 6). The harness tests this with fakes; doing it once
for real confirms the guard is wired into the path the CLI actually takes.

---

## Cross-cutting, measured on every scenario above

These need no extra fixtures — they come out of the records the runs already produce.
Per `docs/superpowers/plans/2026-08-08-first-real-run-checklist.md`, which is the
authority on *where* to look; do not restate its method here:

1. **Wrong-requirement-id rate**, per stage, per model (checklist §1).
2. **Schema-validation-failure rate**, denominator `SUCCESS + VALIDATION_FAILURE`
   attempts only (checklist §2).
3. **Tokens per stage** and total cost per scenario (checklist §3).
4. **Transport-failure rate** — reported separately, never folded into (2). The
   2026-08-10 run measured 59.2% on Groq free tier, so budget for it; the TPM throttle
   (contract item 19) reduces but does not eliminate it.

Run each scenario on **both** providers where quota allows, same fixtures, same
prompts, same answer policy — the only variable being the model. That is what makes a
model-comparison table possible later.

---

## Threats to validity these scenarios carry

State these wherever the results are written up; do not let them surface as
reviewer questions:

- **n = 1 per scenario.** One run of a non-deterministic system. A miss may be
  sampling, not capability. Repeating each scenario 3× and reporting hit rate costs
  little and is the honest version — decide before running, not after seeing a result
  you dislike.
- **Planted conflicts are cleaner than real ones.** S4, S5 and S6 are authored.
  Detection on them bounds nothing about real SRS documents.
- **The human is scripted and AI-written.** Contract item 3 already flags that a human
  inside the loop makes runs non-reproducible; the fixed answer policy trades that for
  reproducibility and introduces a different bias — the answers were generated by a
  model, and are documented as such.
- **Fixtures are tiny.** Two to eight requirements. Whether whole-document consistency
  checking survives a 200-requirement document is the separate planned experiment in
  `EVALUATION_DATASETS.md`; S5 and S7 gesture at it, they do not answer it.
- **`temperature: 1.0`** in the existing run configs. Non-zero temperature widens
  run-to-run variance. Consider lowering it for these scenarios and say which was
  used — but note the first real run used 1.0, so changing it makes results
  non-comparable with that run.

---

## Deliberately not covered here

- **Resume and interruption** — `test_harness.py` covers all six resume positions
  deterministically. Reproducing an interruption against a live API adds cost and
  proves nothing new.
- **Prompt-provenance drift on resume** (contract item 18) — pure file-hash logic, no
  model involved.
- **Throttle behavior** — already validated,
  `docs/superpowers/results/2026-08-10-tpm-throttle-validation/`.
- **Pairwise/combinatorial testing** — deferred by design (CLAUDE.md, known-open).
- **Duplicate test cases across dependent requirements** — Known Limitation 1. S1 and
  S7 will produce examples of it; note them, do not treat them as bugs.

---

## What this suite costs on Gemini

**Derived from measurement, not estimated.** Every unit cost below comes from
`docs/superpowers/results/2026-08-10-gemini-paid-tier-run/` — the 8-requirement THEMAS
run on `gemini-3.6-flash`: 57 calls, 78,983 input + 10,190 output tokens, zero
failures. Recompute from the records, not from this table, if the prompts change.

**Price applied** (Gemini API paid tier, `gemini-3.6-flash`, read 2026-08-11 from
https://ai.google.dev/gemini-api/docs/pricing): **$1.50 / 1M input, $7.50 / 1M output**
(output price includes thinking tokens). Free tier: $0. Per contract item 13, tokens
are stored and cost is not — so re-price by re-running this arithmetic, and do not
write a cost field into any record.

### Measured unit costs

| Unit | Calls | Input | Output | Cost |
|---|---|---|---|---|
| Document stages (consistency + dependency), 8-req document | 2 | 2,307 | 321 | $0.0059 |
| Requirement, passes first time (1 round) | 4 | ~5,360 | ~690 | $0.0132 |
| Requirement, one refinement round (2 rounds) | 7 | 9,460 | 1,477 | $0.0253 |
| Requirement, hits the revision cap (3 rounds) | 8 | ~11,300 | ~1,400 | $0.0275 |
| **Whole 8-requirement THEMAS run** | **57** | **78,983** | **10,190** | **$0.1949** |

### Per scenario

Bounded low (every requirement passes first time) to high (every requirement reaches
the cap). Real runs land between.

| Scenario | Reqs | Calls | Cost |
|---|---|---|---|
| S1 dependency pair | 2 | 10–18 | $0.03–0.06 |
| S2 null control | 2 | 10–18 | $0.03–0.06 |
| S3 conflict, native | 2 | 10–18 | $0.03–0.06 |
| S4 conflict, numeric | 2 | 10–18 | $0.03–0.06 |
| S5 three-way conflict | 3 | 14–26 | $0.05–0.09 |
| S6 cycle | 3 | 14–26 | $0.05–0.09 |
| S7 dilution (full ERTMS) | 8 | 34–66 | $0.11–0.23 |
| S8 clean control | 1 | 6–10 | $0.02–0.03 |
| S9 vague | 2 | 10–18 | $0.03–0.06 |
| S10 atomicity | 2 | 10–18 | $0.03–0.06 |
| S11 revision cap (two runs) | 1 ×2 | 20 | ~$0.07 |
| S12 routing | 3 | 14–26 | $0.05–0.09 |
| S13 forced DEGRADED | 2 | 10–18 | $0.03–0.06 |
| **Full pass** | **~33** | **172–300** | **$0.55–$1.05** |

At the n=3 repetition this file's threats-to-validity section argues for:
**$1.70–$3.10** per provider.

### Two things that matter more than the dollar figure

**The free tier is not usable for this, despite being $0.** The 2026-08-10 first real
run measured an absolute **20-request-per-day** cap on the Gemini free tier. A full
pass is 172–300 requests, so one pass would take 9–15 days and S7 alone exceeds three
days of budget. Use `GEMINI_API_KEY_PAID`. (Groq's free tier fails differently — a
measured 59.2% transport-failure rate from token-per-minute limits, contract item 19 —
so a Groq comparison run needs the TPM throttle configured, not just retries.)

**These bounds are conservative — expect the low end.** The measured units come from
an 8-requirement document; most fixtures here are 2–3 requirements, so the document
context inlined into each per-requirement prompt is smaller. Input tokens are 89% of
this run's total, so the saving lands where it matters most.

**Unverified in these numbers:** context caching ($0.15/1M) is not used by
`GeminiAdapter` and is not modelled. If the same prompt prefix were cached across the
~19 quality-checker calls in a run, input cost would drop substantially — worth
measuring before scaling to real PURE documents, but it is a change to the adapter,
not a config setting, and nothing here assumes it.

---

## Suggested order

Cheap and diagnostic first, so an early failure stops the run before it spends quota:

`S2 → S8` (controls — if these fail, nothing downstream is interpretable)
→ `S4 → S1` (floor cases: does detection work at all)
→ `S9 → S10 → S12` (per-requirement behavior)
→ `S3 → S5 → S6` (the hard detection cases)
→ `S11` (two runs, refinement-heavy)
→ `S13` (config-only, cheap, run any time)
→ `S7` (most expensive, last).
