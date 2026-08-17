# Schema Design Notes

Rationale behind the choices in `schemas.py`, kept separate so the code itself stays
skimmable. Organized to match the file's section order. Doubles as reference material
for the thesis methodology chapter.

See `SCHEMA_AUDIT_CHECKLIST.md` for the repeatable methodology used to find gaps in
this schema (cardinality, symmetry, degenerate-case, lifecycle, traceability,
concurrency, evaluability, and empirical audits). `TestStrategy.techniques` picking up
`min_length=1` (nothing previously stopped a strategy selecting zero techniques) was
found by that checklist's degenerate-case lens. The same empirical-audit lens (real
THEMAS thermostat requirements) also surfaced two more gaps, both now fixed:
`TestTechnique.PERFORMANCE` ("no real time delay" fit no existing technique -- see the
`TestTechnique` section below) and `IssueCategory.VAGUE_PRONOUN` ("these limits,"
"this condition," "this module" had no resolvable antecedent and fit no existing issue
category -- see the Quality Checker section below). A third finding -- undefined
domain-specific abbreviations like "LO = T_LT" -- was investigated and deliberately
**not** fixed; see Known Limitations #5 for why.

## Pipeline overview

```
0. Input                    -> RequirementSet
1. Consistency Checker      (RequirementSet -> ConsistencyReport)
1b. Dependency Mapper       (RequirementSet -> DependencyReport)
2. Per requirement:
   a. Classifier            (Requirement, RequirementSet -> Classification)
   b. Quality Checker       (Requirement, Classification, ConsistencyReport.conflicts_for(id),
                              DependencyReport.dependencies_for(id) -> QualityReport)
   c. Refiner (only if QualityReport.passed is False)
        (Requirement, QualityReport -> RefinerTurn)     -- questions to human
        (RefinerAnswer[] -> RefinedRequirement)          -- human's answers back in
        loops back to Quality Checker
3. Test Design Strategy Selector (Requirement, Classification,
                                   DependencyReport.dependencies_for(id) -> TestStrategy)
4. Test Case Generator            (Requirement, TestStrategy,
                                   DependencyReport.dependencies_for(id) -> TestPlan)
```

Every model carries a `requirement_id` so pipeline stages can be logged/joined
independently — this is what makes the JSON-file run logs (`RequirementRunRecord`)
and later evaluation possible without a database.

## 1. Consistency Checker — why conflicts are groups, not pairs

A conflict isn't always between exactly two requirements. Three or more requirements
can be jointly contradictory with no single pair at fault (e.g. REQ-3 + REQ-7 + REQ-9
only contradict together — removing any one of them removes the contradiction). That's
why `ConsistencyConflict.requirement_ids` is a list (min length 2), not a fixed pair.

This is different from a dependency (see below): a conflict can be genuinely
inseparable as a group, whereas "A depends on B" and "A depends on C" are each
independently true even in isolation — which is why dependencies stay as simple
pairwise edges instead.

## 1b. Dependency Mapper — reference vs. dependency, and why cycles matter

A `DependencyLink` means "`from_requirement_id`'s correctness/testability depends on
`to_requirement_id` in some behavioral way" — e.g. a precondition like "shall not
proceed until X completes."

This is deliberately **not** the same as a requirement merely *mentioning* another one
for navigation or description ("see also REQ-5", "the Login screen links to
Registration (REQ-5)"). Those are harmless cross-references and should not be encoded
as a `DependencyLink` — only encode a link when it actually matters for testing order
or for whether the requirement can be verified in isolation. This distinction is a
judgment call for whichever agent extracts these links; the schema doesn't enforce it.

**Fan-out / fan-in is already supported without any special modeling**: one
requirement can depend on several others, or several requirements can depend on one,
just by adding multiple `DependencyLink` edges that share a `from_` or `to_` id. No
wrapper/group type needed here (unlike consistency conflicts) because dependency edges
are independently true statements, not an inseparable group.

**Circular dependencies** (REQ-A depends on REQ-B depends on ... back to REQ-A) are
detected via `DependencyReport.find_cycles()` (simple DFS — fine at the scale of a
handful-to-dozens of requirements, not meant to scale to huge sets). A cycle can't be
resolved by picking a processing order — there is no valid order left, that's what
makes it circular. So it surfaces as `IssueCategory.CIRCULAR_DEPENDENCY` on every
requirement in the cycle and routes to the Refiner: a human decides how to break it
(one of the "depends on" claims is probably wrong, or the requirements need
restructuring). It must never be silently auto-resolved.

A "these two screens link to each other" style mutual reference is not a defect and
should never have become a `DependencyLink` in the first place, so it will never show
up as a cycle here — this only bites when the dependency was a genuine behavioral one.

## 2a. Classifier — document context, but still per-requirement output

The Classifier's input is `(target: Requirement, context: RequirementSet)` — the whole
document, not just the isolated requirement — because short/generic requirements carry
no type-revealing signal alone ("the system shall respond within 2 seconds" says
nothing about web/mobile/AI on its own, but the rest of the document usually makes it
obvious). No new wrapper schema needed for this: it's just the existing `Requirement`
and `RequirementSet` models passed together.

Giving the Classifier more context does **not** mean deciding one type for the whole
document. Output stays exactly one `Classification` per requirement. Different
requirements in the same document can legitimately get different system types — a
mobile app requirement next to a web-admin-panel requirement next to an AI-backend
requirement in the same SRS is a normal, heterogeneous system, not an inconsistency.
Nothing downstream requires document-wide type uniformity.

## 2b. Quality Checker — IssueCategory taxonomy, and the VAGUE_PRONOUN addition

Eight categories, each grounded in ISTQB/IEEE 29148 or the requirement-smells
literature (arxiv 2403.17479, itself extending Femmer et al.'s smell catalog) rather
than invented: `AMBIGUOUS_TERM`, `NON_ATOMIC`, `INCOMPLETE`, `NON_VERIFIABLE`,
`INFEASIBLE_FOR_TYPE`, `INCONSISTENT`, `CIRCULAR_DEPENDENCY`, and `VAGUE_PRONOUN`.

`VAGUE_PRONOUN` was added after the empirical audit found real requirements that
didn't fit any existing category. THEMAS spec examples (Fischbach et al. 2022):

- REQ D: "Temperatures that do not exceed **these limits** shall be output for
  subsequent processing."
- REQ E: "If **this condition** is true, then **this module** shall output a request
  to turn on the heating unit in case LO = T_LT."
- REQ F: "The heating/cooling unit shall have no real time delay when **these
  statuses** are sent to the THEMAS system." (This requirement also independently
  triggered the `TestTechnique.PERFORMANCE` addition above — one requirement, two
  unrelated defects.)

None of these fit `AMBIGUOUS_TERM` (that category is for terms that are inherently
subjective/unmeasurable, e.g. "fast" — "these limits" isn't vague, it would be
perfectly precise *if* its referent were known) or `INCOMPLETE` (that category is for
a missing actor/trigger/condition — these requirements are grammatically complete;
what's missing is the definition of a referenced entity, not a structural part of the
sentence itself).

The category is grounded precisely, not approximately: it maps to smell S7 in the
taxonomy, defined as "the pronouns whose reference or relation is not clear to the
reader based on the context," detected via POS tagging — which covers demonstratives
(this/these/that/those) as well as bare pronouns, so extending it to "this module" is
within the smell's actual defined scope, not a stretch (unlike the CT-AuT/embedded
question considered and rejected for Gap 1).

Costs no new schema structure: it flows through the exact same
`Issue` -> `ClarifyingQuestion` -> `RefinerAnswer` -> `RefinedRequirement` loop as
every other category. The Refiner asks what the referent is; if the human's answer
turns out to name another requirement's behavior, that can surface separately as a
`DependencyLink` on the next Dependency Mapper pass — the two mechanisms compose
without new coupling.

**Known risk, not hidden:** the same paper that supplies this category's definition
also reports, from its own detection experiments, that "Vague pronouns and
Non-verifiable terms are more difficult to detect even when using an automated
dictionary mechanism" -- i.e. this is one of the two hardest smells in their benchmark
to detect reliably, even with tooling built specifically for it. Expect the Quality
Checker's LLM to be noisier on this category than on the others, and don't assume it
classifies as cleanly. Also worth naming: there's still no explicit rule for when a
newly-found gap earns its own `IssueCategory` value versus getting folded into an
existing one -- this is the second category (following `TestTechnique.PERFORMANCE`)
added by empirical finding in this pass, and the taxonomy could keep growing without
one. And boundary fuzziness with `AMBIGUOUS_TERM` isn't fully resolved: a sentence
like "respond quickly to these requests" has both smells adjacent to each other, and
nothing but LLM judgment decides which span gets which tag.

This design also always escalates to the human rather than attempting cheap automatic
resolution first (e.g. checking whether an antecedent exists earlier in the same
`RequirementSet` before flagging it as unresolved) -- the simplest possible choice,
left as a potential future accuracy improvement rather than built now.

## 2c. Refiner — why it's request/response instead of a blocking call

`RefinerTurn` (questions out) and `RefinerAnswer` (answers in) are two separate
schemas rather than one blocking function call, so the same contract works whether the
caller is a CLI loop, a notebook cell, or — per the planned future extension — a
FastAPI backend serving a web frontend. A blocking `input()`-style design would have to
be rewritten when the web layer arrives; this shape doesn't.

`IssueCategory` is a fixed, bounded taxonomy (not free-form) by design: the Quality
Checker tags an issue with a fixed category, and the Refiner phrases the actual
question text with an LLM *within* that category. This keeps question-asking
reproducible enough to evaluate (which category got asked about) while still letting
the wording be natural rather than a rigid template string.

## 2c continued — how "correct" answers are checked, and avoiding repeat questions

The system cannot verify domain truth (e.g. whether 2 seconds is really the right SLA
for this system) — only a human has that authority. What it *can* verify is
testability-structure: whether the same objective criteria that flagged the issue
(ISTQB word list, IEEE 29148 attributes, the smell taxonomy) still trigger once the
answer is folded into a rewritten requirement. That's why the Refiner doesn't grade
answers directly — it rewrites the requirement and loops back to the Quality Checker,
which re-runs the same checks. "Resolved" means "no longer fails the check," not
"matches ground truth." This is a real limitation worth stating explicitly rather than
overselling — no design closes this gap without an independent domain oracle.

To avoid re-asking a question the user believes they already answered correctly:

- `Issue` now has a stable `id` (not just a category), because a requirement can have
  two issues of the same category (e.g. two different vague terms) that a bare
  category label can't tell apart.
- `ClarifyingQuestion.issue_id` links a question to the *specific* issue it addresses,
  not just its category, so a question/answer pair can be traced precisely across
  revisions.
- `RefinerAnswer.user_confirms_resolved` is a human override: if the user is confident
  their answer fully resolves the issue, they can say so explicitly, and the Quality
  Checker (agent logic, not schema-enforced) should treat that issue id as accepted
  and not re-flag it, even if a fresh automated check would still have doubts. Without
  this, an LLM re-check that's slightly too strict could loop forever on a question the
  human has already answered in good faith.
- Not schema-enforced but worth building into the orchestrator: a max-revision cap
  (e.g. 3 rounds) per requirement, so a genuinely stuck disagreement between the
  checker and the human terminates instead of looping indefinitely.

**2026-08-08 addendum:** the Quality Checker now also receives this requirement's own
filtered document-level context — `ConsistencyReport.conflicts_for(id)` and
`DependencyReport.dependencies_for(id)`, never the whole report, same shape as §3/4
below. `None` (the document-level stage failed) and `[]` (it ran, nothing named this
requirement) are kept distinct, not collapsed. See
`docs/superpowers/specs/2026-08-08-document-context-wiring-design.md` for the reasoning
and `design/ORCHESTRATOR_CONTRACT.md` for the orchestrator-level contract this creates.

## 3/4. Test Design Strategy Selector & Test Case Generator — per-requirement, with targeted dependency context

Both stay per-requirement: one `Requirement` in (not `RefinedRequirement` — see
`design/schemas.py`'s module docstring, "stages 3 and 4 ... take a plain `Requirement`,
NOT a `RefinedRequirement`"), one `TestStrategy` or `TestPlan` out — never bulk across
the whole document. But each is now also given
that one requirement's own dependency links (`DependencyReport.dependencies_for(id)`),
not the whole `DependencyReport` and not the whole document. This is the same shape as
the Classifier fix: widen the *input context* for a single requirement without
changing the *output granularity*.

Why it's worth the small addition: if REQ-A genuinely depends on REQ-B (e.g. checkout
depends on cart), that's exactly the kind of fact that should show up in
`TestCase.preconditions` ("REQ-B must already be satisfied") -- and the Test Case
Generator has no way to know that unless it's told. No new schema fields were needed
for this: `TestCase.preconditions` already existed as free text; this just wires
already-built `DependencyReport` data into a stage that wasn't using it before.

## 4 continued — a test case can cover multiple requirements

`TestCase.requirement_ids` is a list, not a single id — same reasoning as
`ConsistencyConflict.requirement_ids`. A single test case very often verifies more
than one requirement in one execution: an end-to-end test like "add item to cart,
then check out" naturally exercises both the cart requirement and the checkout
requirement together, and forcing it to be filed under only one of them would lose
real traceability information (a requirement-to-test-case matrix is genuinely
many-to-many, not many-to-one).

`TestPlan.requirement_id` stays singular, though: the Test Case Generator still runs
once per requirement (see the per-requirement/dependency-context note above), so a
`TestPlan` is still "the plan produced while processing this one requirement." Its
`test_cases` can each separately claim to also cover other requirement ids.

**Known consequence, deliberately left unresolved for now — see Known Limitations
below.**

## TestTechnique — sources and what each one is for

Ten techniques, each grounded in an ISTQB syllabus rather than invented:

**ISTQB Foundation Level (general black-box techniques):**
- `EQUIVALENCE_PARTITIONING` / `BOUNDARY_VALUE_ANALYSIS` -- single-variable ranges.
- `DECISION_TABLE` -- rule combinations (conditions -> actions), for business-logic-
  heavy requirements. One test case per rule/column in the table.
- `STATE_BASED` -- state transition testing: valid/invalid transitions of one
  stateful object.
- `USE_CASE` -- multi-step user journeys (main/alternative/exception flows). This is
  the technique that justifies `TestCase.requirement_ids` spanning multiple
  requirements (see below) -- an end-to-end scenario naturally touches more than one
  requirement.
- `EXPLORATORY` -- experience-based, same family as ISTQB's "error guessing."

**ISTQB CT-AI v2.0 (AI-specific "model testing" techniques):**
- `METAMORPHIC` -- checks a relation between related inputs' outputs, rather than one
  fixed expected output (useful precisely because AI systems often have no single
  deterministic "correct" answer to check against).
- `ADVERSARIAL` -- deliberately perturbed input, checking the model still behaves
  correctly (robustness). Describable as ordinary text steps in a `TestCase`, though
  actually executing the perturbation needs real tooling outside this pipeline.
- `STATISTICAL_THRESHOLD` -- our own label, not a CT-AI technique name verbatim; maps
  to CT-AI's "ML functional performance metrics" (accuracy/precision/recall/F1 against
  a required threshold). Worth noting its execution granularity differs from the
  others: one "test case" here really means "run against a labeled evaluation set,"
  not one single input.

**ISTQB CT-PT (Performance Testing) -- cross-cutting, not AI-specific:**
- `PERFORMANCE` -- added after the empirical audit found a real requirement ("shall
  have no real time delay," from the THEMAS thermostat spec) that fit no existing
  technique. It was tempting to read this as evidence `SystemType` needed a new
  `EMBEDDED`/hardware bucket grounded in ISTQB CT-AuT (Automotive Software Tester,
  the closest existing cert to "embedded systems") -- rejected, because CT-AuT is
  automotive-specific (ISO 26262, AUTOSAR) and too narrow to justify a general
  category, and because a timing/latency/throughput constraint isn't unique to
  embedded systems anyway -- a web app's "respond within 2 seconds" has the exact
  same problem under the current techniques. So `SystemType` was left as-is (`OTHER`
  is the correct, honest bucket for the thermostat) and `PERFORMANCE` was added as a
  technique available to *every* system type instead (see Layer 1 below), grounded in
  ISTQB's own Performance Testing syllabus rather than the automotive one.

  Deliberately scoped narrow: this is one technique label covering CT-PT's general
  concept of a measurable timing/throughput/load constraint, not a separate technique
  per CT-PT sub-discipline (load/stress/soak/spike/scalability testing are all real,
  distinct designs, but splitting them out now would be scope creep past what the
  empirical audit actually found). `TestCase`'s existing generic shape
  (`preconditions`/`steps`/`expected_result`) is reused as-is -- no new
  performance-specific fields (load profile, target metric, threshold value) were
  added, for consistency with every other technique, which already accepts the same
  tradeoff (a `DECISION_TABLE` test case doesn't get a rule-matrix field either).
  Also deliberately not addressed: the difference between a hard real-time deadline
  (the thermostat missing one could be a safety-relevant failure) and a soft
  performance target (a slow web page). Both currently map to the same
  `PERFORMANCE` label. There's no existing home in the schema for a
  criticality/severity concept, and nothing in the current reference documents
  establishes that safety-critical embedded testing is actually in scope for this
  thesis -- so this is left as an open item, not solved speculatively. Revisit only if
  a real safety-critical requirement set becomes part of the evaluation data.

**Deliberately left out** (documented, not oversights): white-box/code-coverage
techniques (statement/branch coverage) and neural-network structural coverage measures
(neuron coverage, kMNC, NBC) -- both require access to source code or model internals,
which this pipeline never has; it only ever works from requirement text. `A/B_TESTING`,
`BACK_TO_BACK`, and `DRIFT` (also CT-AI techniques) -- all three are fundamentally
about comparing multiple runs/versions over time or live traffic, which doesn't map
onto a single `TestCase` (one execution, one `expected_result`) without a real schema
extension; left out rather than force-fit. `PAIRWISE` -- see "Deferred" section below.

`RequirementRunRecord` bundles one requirement's whole trace (classification -> issues
found -> Q&A -> refined text -> chosen technique(s) -> generated test cases) so dumping
it to JSON per requirement gives you both a run log and your evaluation dataset for
free.

## How techniques get selected — rules, not free choice

Same philosophy as the Quality Checker (ISTQB word list + IEEE 29148 rules, not "let
the LLM freely decide what's ambiguous") and the Refiner (fixed issue categories, LLM
only phrases the question text within them). Technique selection follows the same
pattern: rules constrain the choice, the LLM applies judgment within those rules. Pure
LLM autonomy here was rejected because it breaks reproducibility (no fixed rulebook to
evaluate technique choices against) and risks real correctness bugs (nothing would stop
the model picking `ADVERSARIAL` for a plain web login form, or `EQUIVALENCE_PARTITIONING`
for a probabilistic AI requirement with no fixed expected value).

**Layer 1 -- hard constraint from `Classification.system_type` (not negotiable):**
- `AI_SYSTEM` -> eligible pool: `{METAMORPHIC, ADVERSARIAL, STATISTICAL_THRESHOLD, EXPLORATORY, PERFORMANCE}`
- `WEB` / `MOBILE` / `OTHER` -> eligible pool: `{EQUIVALENCE_PARTITIONING, BOUNDARY_VALUE_ANALYSIS, DECISION_TABLE, STATE_BASED, USE_CASE, EXPLORATORY, PERFORMANCE}`

`PERFORMANCE` is the one technique added to *both* pools rather than gated to one
system type -- a timing/latency/throughput constraint can show up in a requirement
regardless of what kind of system it's for.

This alone rules out the "adversarial testing for a login form" failure mode entirely
-- the model is never even offered that option for a non-AI requirement.

**Layer 2 -- content heuristics that narrow within the eligible pool (soft guidance,
not exclusive -- more than one can apply, which is why `TestStrategy.techniques` is a
list):**
- Numeric range/threshold in the text ("between X and Y," "at least N," "no more than
  N") -> `EQUIVALENCE_PARTITIONING` / `BOUNDARY_VALUE_ANALYSIS`.
- Multiple combined conditions ("if X and Y, then Z") -> `DECISION_TABLE`.
- A described sequence of states for one object (order: pending -> shipped ->
  delivered) -> `STATE_BASED`.
- A multi-step scenario, or a requirement that showed up in
  `DependencyReport.dependencies_for()` -> `USE_CASE`.
- A probabilistic/ML output with no single correct value ("classify," "predict,"
  "recommend") -> `METAMORPHIC` and/or `STATISTICAL_THRESHOLD`.
- A security/robustness concern for an AI system specifically -> `ADVERSARIAL`.
- A timing/latency/throughput/response-time constraint ("within N seconds," "no
  delay," "shall handle N requests/second") -> `PERFORMANCE`, for any system type.
- Nothing above clearly matches -> fall back to `EXPLORATORY`.

**Layer 1 is now enforced, not just documented (2026-08-05).** See "Technique
eligibility enforced" below — the paragraph immediately following was written before
that and understated what the schema was actually doing.

**Why this doesn't need a schema change:** the `TestTechnique` enum already constrains
the model to a fixed set of valid values (it cannot invent a new technique name), and
`TestStrategy.rationale` already requires a written justification for whatever it
picks. That `rationale` field is the audit mechanism: a human reviewer (or an
evaluation script) can check whether the stated reasoning actually matches these rules,
rather than just trusting a bare label. This is prompt/agent-design guidance for
whoever builds the Test Design Strategy Selector, not something Pydantic enforces on
its own.

## Verification pass — non-empty guards added (2026-08-05)

Before treating the schema as committed, ran a fresh check (not just re-reading old
audit notes) by actually constructing degenerate Pydantic instances in code. Found ten
fields that silently accepted an empty value they logically shouldn't: `Requirement.text`,
`Classification.rationale`, `TestStrategy.rationale`, `Issue.explanation`,
`ConsistencyConflict.explanation`, `DependencyLink.explanation`,
`ClarifyingQuestion.question_text` (all plain `str` with no `min_length`), plus
`RefinerTurn.questions`, `TestCase.steps`, and `TestPlan.test_cases` (all `list[...]`
with no `min_length`). An LLM call returning `""` for a rationale, or an empty steps
list for a test case, would have passed validation and only surfaced as a confusing
downstream failure (or silently useless output) rather than an immediate, clear error
at the source. All ten now require `min_length=1`. Re-ran both the degenerate-case
tests (confirm each now raises `ValidationError`) and the full `RequirementRunRecord`
round-trip test (confirm nothing legitimate broke) — both pass. Also fixed a stale
claim in `SCHEMA_AUDIT_CHECKLIST.md` that still said `TestStrategy.techniques` was
missing `min_length=1`, when that had already been fixed earlier.

**Left open, not part of this pass:** three judgment calls that are more than a
one-line `Field` tweak, still undecided —
1. ~~`QualityReport.passed=False` with `issues=[]` (or `passed=True` with non-empty
   `issues`) is internally inconsistent but not currently prevented.~~ Fixed below.
2. ~~Whether `RequirementSet.requirements` should require `min_length=1`.~~ Fixed below.
3. ~~Whether `RefinedRequirement.answers_used` should require `min_length=1`.~~ Fixed
   below. All three verification-pass judgment calls are now resolved.

## `QualityReport` consistency fix (2026-08-05)

`passed` and `issues` are supposed to move together (passed means "no issues"), but
nothing enforced that — `QualityReport(passed=False, issues=[])` and
`QualityReport(passed=True, issues=[some_issue])` both validated cleanly. The first is
the more dangerous case: the Refiner only triggers when `passed=False`, so an LLM
returning `passed=False, issues=[]` would route to the Refiner with nothing to ask
about (and now hard-crashes against the `RefinerTurn.questions` `min_length=1` guard,
rather than silently producing an empty question set). The second is quieter but worse
for data quality: a requirement gets treated as clean downstream while a real issue
sits ignored in the record.

Fixed with a `@model_validator(mode="after")` enforcing `passed == (len(issues) == 0)`
— rejected at the point the LLM output first enters the schema, not somewhere
downstream. Chosen over a `Field` constraint because the invariant spans two fields, not
one. Tested all four combinations (both valid states accepted, both contradictory
states rejected) plus the full `RequirementRunRecord` round trip — all pass.

**Deliberately not built now, worth exploring later: a two-tier issue/warning system.**
The current model treats every `Issue` as blocking — any non-empty `issues` list forces
`passed=False` and routes to the Refiner. A real Quality Checker might reasonably want
to flag something minor/non-blocking ("this phrasing is a little informal, but it's
still testable") without stopping the pipeline on it. That would need `Issue` (or a new
field) to carry a severity/blocking distinction, and this validator's invariant would
have to change from "passed iff issues is empty" to something like "passed iff no
*blocking* issues" — the `len(issues) == 0` check isn't the right one anymore once a
non-blocking category exists. Not adding this now because there's no concrete evidence
yet (from real requirement text) that the pipeline needs a middle ground between "clean"
and "must go to the Refiner" — same "revisit once there's demonstrated need" pattern
used for Known Limitation #5. Revisit if the empirical/evaluation passes show issues
that are technically real but too minor to justify a human round-trip every time.

## Known Limitations

**1. Duplicate/near-duplicate test cases across dependent requirements.** Because Test
Case Generation runs once per requirement (not bulk), two requirements linked by a
`DependencyLink` (e.g. REQ-A/checkout depends on REQ-B/cart) are processed in separate,
independent calls, each unaware of the other's output. Both can correctly decide an
integration test spanning the pair is warranted, producing two different `TestCase`
objects (different ids, different wording) that cover the same underlying scenario.
Assembling all `TestPlan`s into one final suite will contain this redundancy.

Not fixed yet because the obvious fix is riskier than the problem: de-duplicating by
matching `requirement_ids` sets is unsound, since two test cases can legitimately share
the same requirement pair while testing different scenarios (e.g. "checkout succeeds
with 1 item in cart" vs. "checkout fails with an empty cart" both touch `[REQ-A,
REQ-B]` but are not duplicates). A correct fix needs semantic judgment about whether
two test cases test the *same* scenario, which means either another LLM call (a new
failure point whose mistakes -- wrongly merging/deleting a genuinely distinct edge-case
test -- are silent and hard to catch by inspection) or a similarity heuristic (blunt in
both directions: misses true duplicates worded differently, flags genuinely distinct
boundary-value tests as too similar). Either approach would also need its own accuracy
evaluation (precision/recall on duplicate detection), adding scope. Given that a wrong
merge silently destroys real test coverage -- worse, for a test-generation thesis, than
harmless redundancy -- this is left as a documented limitation rather than solved with
an unvalidated heuristic. Revisit once there's a de-duplication approach that doesn't
risk false-positive merges.

*Deferred alternatives (2026-08-14) — recorded, not adopted.* Reviewing this limitation
surfaced two directions that avoid de-duplication's central risk entirely, because
neither deletes anything. Both left unimplemented; noted here so the option set doesn't
have to be rediscovered.

- **Prevent instead of delete: show the generator what already exists.** `run_document`
  walks `requirement_set.requirements` in document order, sequentially
  (`orchestrator/pipeline.py`), and each `run_requirement` call receives nothing from the
  others — but by the time a dependent requirement is generated, its dependency's
  `TestPlan` is already in the accumulated records. Passing those already-generated cases
  into `stage_fns.generate_tests` as context ("these exist, don't restate them") is the
  same shape as the targeted `relevant_dependencies` context the generator already gets.
  No new LLM call, no similarity threshold, no precision/recall evaluation, and nothing
  is destroyed, so the wrong-merge failure mode that rules out de-duplication cannot
  occur. Real costs: it only helps when the dependency appears *earlier* in document
  order (processing in dependency order instead is a genuine pipeline change, and is
  undefined for the cyclic subsets `find_cycles()` exists to detect); it *reduces*
  duplication rather than eliminating it, since the model may restate anyway; and it
  grows the generator prompt, which is also another surface for untrusted requirement
  text.
Both are premature for a reason found later and recorded under Known Limitation 6: the
duplication this limitation describes requires two plans to each emit a test case spanning
the same requirement pair, and **no real run has produced a multi-requirement test case at
all** (13 cases, all single-requirement). There may be nothing here to prevent. Settle that
with behaviour scenario S1 before implementing either option below.

- **Measure it instead of removing it.** Emit suspected duplicate pairs as an advisory
  finding and never act on them. Costs one comparison pass and no API calls, converts a
  documented limitation into a reported number, and keeps every generated case. Weak as a
  fix, useful as evaluation evidence.

**Suite result 2026-08-13 — the premise is now live.** S1 produced
`TC-13-PURE-ERTMS-R7-2`, citing both `PURE-ERTMS-R7` and `PURE-ERTMS-R8` — the first
multi-requirement test case this project has produced. So spanning cases *do* occur when a
dependency link exists, and the duplication this limitation describes is reachable rather
than hypothetical. It was not observed here because only one plan in the suite had a
dependency to span. Revisit with a fixture where *both* ends of a link reach test
generation.

**2. The system verifies testability-structure, not domain truth.** Covered in the
Refiner section above: the pipeline can confirm a refined requirement no longer fails
the same objective checks that originally flagged it, but it has no way to verify the
human's answer is factually correct for the actual system being specified. Only the
human has that authority; no design closes this gap without an independent domain
oracle.

*Two clarifications added 2026-08-14 — this stays unfixed, but it is not unmitigated.*

**The record already isolates every domain claim for audit, which is the only useful
thing available here.** `RefinedRequirement.answers_used` stores the human's answers
verbatim, and `RefinementRound` records the whole trajectory
(`text_checked`/`turn`/`answers`/`rewrite` per round), so any factual claim that entered
a refined requirement is traceable to the specific answer that introduced it. That is not
verification and must not be presented as it: it is the difference between "unverified"
and "unverified *and* untraceable". State it as the mitigation in the threat-to-validity
discussion rather than implying the gap is narrower than it is. Attempting actual
verification — asking a model whether "3 seconds" is plausible for this system — is
rejected: it manufactures domain authority the model does not have, and its errors are
confident, silent, and indistinguishable from correct output on inspection.

**One weak automated check on domain claims does exist, and Known Limitation 7 is
currently blocking it: internal contradiction.** If a human answer supplies 3°F and a
sibling requirement already says 5°F, at least one of them is factually wrong about the
system, and the Consistency Checker is precisely the stage that would surface it. It
cannot today, because document-level analysis ran before the answer existed (see
Known Limitation 7). This does not verify domain truth — both requirements can agree and
both be wrong — but it is the only mechanical check on human-supplied facts within reach,
and it raises the value of addressing 7 above what that entry alone suggests.

**3. `PERFORMANCE` doesn't distinguish hard real-time from soft performance targets.**
A missed deadline in an embedded control system (potentially a safety-relevant
failure) and a slow-loading web page (a tunable UX issue) both currently map to the
same `TestTechnique.PERFORMANCE` label -- see the `TestTechnique` section above.
Left unresolved because there's no existing criticality/severity concept anywhere
else in the schema, and nothing in the current reference documents establishes that
safety-critical embedded testing needs to be in scope. Revisit only if that changes.

*Correction to the reason, 2026-08-14.* The scope half of that justification is false and
should not be repeated. Embedded control requirements are already in this project's data:
`datasets/requirements_dataset.json`'s `themas-fischbach2022` is a thermostat control
system and has run end-to-end against real APIs
(`docs/superpowers/results/2026-08-10-gemini-paid-tier-run/`), and
`pure-ertms-2007` — reserved in `datasets/EVALUATION_DATASETS.md` — is rail signalling,
which is both safety-critical and hard real-time. "Out of scope" was never checked
against the corpus and does not hold.

The limitation stays open, on two different and verifiable grounds:

1. **No observed consequence.** `TestTechnique.PERFORMANCE` was selected zero times
   across every real run recorded under `docs/superpowers/results/`. A distinction the
   Strategy Selector has never once drawn cannot yet be shown to matter.
2. **Nowhere to put it, and nothing gained by adding one.** `SystemType` has no embedded
   member, and `ELIGIBLE_TECHNIQUES` maps `WEB`, `MOBILE` and `OTHER` to the *identical*
   `_NON_AI_TECHNIQUES` pool. Adding an `EMBEDDED` member would change nothing at the
   eligibility layer unless it also received a different pool — and there is no citation
   establishing what that pool should contain. Adding an unsourced one is the same
   overreach rejected in Known Limitation 5.

Revisit when `PERFORMANCE` actually gets selected on a hard-real-time requirement
(ERTMS is the likely trigger) — at that point the need is demonstrated rather than
assumed, and a grounded technique pool can be argued for. See also Known Limitation 9,
which is the more consequential half of what this review turned up.

**Suite result 2026-08-13 — now a measured negative, not an open question.**
`TestTechnique.PERFORMANCE` was selected **zero** times across 34 requirements, including on
`LUITEL-R1` ("shall reach a steady state within 5s"), the one requirement S12's ground truth
expected to select it. Techniques actually chosen: `equivalence_partitioning` 8,
`state_based` 7, `use_case` 6, `boundary_value_analysis` 3, `decision_table` 3,
`exploratory` 2. The hard-vs-soft distinction remains unreachable because the technique it
would qualify is never picked — and the Strategy Selector missing an explicit numeric latency
target is a finding about *selection*, not about this taxonomy gap.

**4. `VAGUE_PRONOUN` is expected to be noisy to detect.** Per the sourcing paper's own
detection results (see the Quality Checker section above), this is one of the two
hardest requirement smells to detect reliably even with dedicated automated tooling.
The schema doesn't (and can't) fix this -- it's a property of the underlying detection
task, not the data model. Worth tracking separately if/when the Quality Checker's
per-category precision/recall is evaluated: expect this category to underperform the
others, and don't treat a low score on it as evidence of an implementation bug.

**5. Undefined domain-specific abbreviations/notation aren't reliably caught.**
Empirical audit example: REQ E's "LO = T_LT" (THEMAS spec) -- an abbreviation with no
definition anywhere in the requirement or the rest of the document. Doesn't fit
`AMBIGUOUS_TERM` (that's for subjective everyday words, not domain notation), doesn't
fit `Polysemy` from the smell taxonomy (S9 -- a word with *multiple* candidate
meanings across domains; "LO = T_LT" has *zero* meaning without external knowledge,
not several competing ones), and doesn't fit `VAGUE_PRONOUN` (S7 is specifically
about pronouns, detected via POS tagging -- "LO" and "T_LT" aren't pronouns). No
category in either the smell taxonomy or IEEE 29148 maps onto this precisely.

Traced through the current schema: an LLM Quality Checker given only the existing
eight categories would most likely either miss this defect entirely (nothing prompts
it to look for undefined notation specifically), mis-tag it as `VAGUE_PRONOUN` (the
closest category thematically, even though it doesn't fit the citation), or scatter it
inconsistently across `AMBIGUOUS_TERM` / `NON_VERIFIABLE` / `INCOMPLETE`.

Two fixes were considered and both rejected for now:
- Generalize `VAGUE_PRONOUN` to cover undefined domain terms too -- rejected because
  it would dilute a citation that's currently precise (S7 is pronouns specifically),
  the same kind of overreach that ruled out CT-AuT for `SystemType` in Gap 1, just
  smaller in degree.
- Add a new, separate `IssueCategory` for this -- rejected because no specific smell
  or standard clause maps onto it precisely (the best available grounding is IEEE
  29148's general unambiguous/completeness principle, which is markedly weaker than
  the specific, named citations behind every other category), and because the
  evidence for it is a single occurrence in one paper -- not a demonstrated recurring
  pattern the way "systems can have timing constraints" or "specs contain dangling
  pronouns" clearly are.

Left unfixed in the schema, but not undocumented: this is a real, demonstrated blind
spot today, deliberately left open rather than closed with a weakly-grounded fix.
Revisit once a real target requirement dataset is chosen -- if undefined
domain-specific terminology shows up as a recurring pattern across that dataset (not a
single anecdote), choose between generalizing `VAGUE_PRONOUN` or adding a dedicated
category then, informed by actual frequency rather than one example. See
`EVALUATION_DATASETS.md` for candidate datasets (PURE, PROMISE NFR, etc.) -- reserved
for the post-implementation evaluation phase, deliberately not pulled from during
schema design to avoid tuning the schema against the same data it'll later be
evaluated on.

*Reframed 2026-08-14: this is probably an input problem, not a taxonomy problem.* Both
rejected fixes above argue about **which category** `T_LT` belongs to. That framing misses
the more basic issue, which is that no category can be applied correctly because the stage
doing the categorising cannot see the evidence.

**The Quality Checker never sees the document.** `CheckQualityFn`
(`orchestrator/stage_fns.py`) takes `(requirement, classification, relevant_conflicts,
relevant_dependencies, suppressed_issue_ids)`, and `orchestrator/example_prompts/
quality_checker.txt` passes only the requirement text, its system type, the filtered
conflicts/dependencies and the suppression list. No document text, no glossary. So the
checker is permanently in the isolated-excerpt condition — and `T_LT` has two opposite
readings it has no way to separate: a genuine defect (defined nowhere, untestable) or
ordinary jargon defined in a glossary or a sibling requirement. Flag it and well-defined
notation produces false positives; stay silent and real defects pass. Either behaviour is
a guess.

This matters because `datasets/EVALUATION_DATASETS.md`'s PURE spot-check log (2026-08-04)
found that exactly these terms — `"DMI"`, `"this file"` — usually *do* resolve once the
surrounding document is read, and concluded the gap is milder at full-document level than
the isolated-excerpt audits suggested. That conclusion does not transfer to the pipeline
as built: the pipeline never puts the checker in the full-document condition where the
terms resolve.

**Asymmetry worth noting:** the Classifier *does* get document context — `ClassifyFn`
takes `(requirement, requirement_set)` and `classifier.txt` passes `requirements_json` —
and uses it to produce one `SystemType`. The Consistency Checker and Dependency Mapper
also read the whole set. So the document is available inside the pipeline at three points,
just never at the one stage whose job is finding defects. No reason for that split is
recorded anywhere; it appears to be incidental rather than decided.

**Rejected fix (added here so it isn't revisited): pass the whole `RequirementSet` to the
Quality Checker.** It is the obvious symmetric change and the wrong one. The checker runs
once per requirement *per refinement round*, so this attaches a document copy to roughly
N x rounds calls — quadratic in tokens, spent against tokens-per-minute on the free tier,
which is the exact constraint that already required `Throttle`'s TPM pacing and
`RotatingKeyAdapter`. Highest cost in the scarcest resource, for the smallest documented
defect class.

**Candidate fix if the measurements support it: one deterministic pre-pass, no LLM call,
no new stage.** Scan the document once for terms it *defines* (`X = Y`, `X means Y`,
`Full Name (ABC)`, glossary lines) and, per requirement, for notation-shaped tokens it
*uses* (all-caps, underscores, mixed alphanumerics). Used-but-never-defined tokens are
candidates, passed to the Quality Checker as a short constant-size list rather than a
document. Properties that make this the cheap option: deterministic and unit-testable,
cannot hallucinate, zero API cost, no change to the eight-stage story, no change to
`StageFns`' shape or resume semantics. It produces *candidates* — the model or the human
still judges.

It also repairs the grounding objection that killed both earlier options. "Which of the
eight categories does this fit?" had no citation behind any answer. "This term is used and
defined nowhere in the document" is an objective, mechanically checkable property, which
IEEE 29148's completeness principle supports far better than stretching `VAGUE_PRONOUN`
(S7, pronouns specifically) to cover tokens that are not pronouns. A dedicated
`IssueCategory` becomes arguable once the thing it names is precise.

**Do not build it before two measurements, in this order.** Both are cheap and neither is
wasted:

1. **Run behaviour scenario S9** (`docs/superpowers/plans/2026-08-11-behavior-scenarios.md`;
   fixtures and configs exist under
   `docs/superpowers/results/2026-08-11-behavior-scenarios/`, nothing has been run). It
   probes `LO = T_LT` directly, and the three possible outcomes point at three different
   responses: flagged correctly -> the limitation is milder than documented, build nothing;
   missed silently -> the missing-input diagnosis holds and the pre-pass is the fix;
   mis-tagged as `VAGUE_PRONOUN`/`AMBIGUOUS_TERM` -> this is a prompt/taxonomy problem and
   the pre-pass would be the wrong fix. Only one of the three leads to the code above.
2. **Run the token scan as a throwaway script over THEMAS and ERTMS**, both already marked
   spent for design purposes in `EVALUATION_DATASETS.md`, so no evaluation contamination.
   This produces the recurring-frequency evidence this entry already demands before any new
   category. Same code as the candidate fix, run as a measurement first.

**S9 result 2026-08-14 — the gate question is answered: the checker is blind to it, not
confused by it.** The behaviour suite's S9 ran `THEMAS-REQ-E` ("…in case `LO = T_LT`") for three
rounds. Across all three, the Quality Checker flagged only `vague_pronoun` on "this condition"
and "this module". **`LO = T_LT` was never flagged at all** — not raised, not mis-tagged into a
neighbouring category. The prediction recorded above (that an LLM checker would "most likely
either miss this defect entirely… or mis-tag it") is confirmed in its first branch.

Consequence, and it cuts against the fix proposed below: there is no wrong flag to correct and
no judgement to inform. Supplying the checker with term definitions would change nothing
observable unless a new `IssueCategory` were added to give it something to report — the option
rejected above for lack of grounding. The pre-pass therefore stays unbuilt, now on measured
grounds rather than on the "not yet measured" grounds recorded above.

Also measured on the same requirement: both `THEMAS-REQ-D` and `THEMAS-REQ-E` failed all three
rounds on *pronoun referents*, and `THEMAS-REQ-D`'s rewrite ("these limits" -> "the specified
temperature limits") was flagged again in rounds 2 and 3. The demonstrated weakness is
referents, not terminology.

**Live-session evidence 2026-08-14 — the input diagnosis is confirmed, and the anchor example
is worse than "undefined".** Answering the scenarios by hand against the source document
(`datasets/requirements-xml/XMLZIPFile/1998 - themas.xml`, quotations verified by direct
inspection) produced three for three:

| Requirement | Phrase | Resolves to | Where in the source |
|---|---|---|---|
| `THEMAS-REQ-D` | "these limits" | the overtemperature limits | previous sentence, SRS-009 |
| `THEMAS-REQ-E` | "this condition" | Condition 2 (`LO <= T < LT` or `UT < T <= UO`) | same section, SRS-010 |
| `THEMAS-REQ-E` | "this module" | Determine H/C Mode (SRS-010) | the process's own section |

Every one is resolvable from context the Quality Checker never sees. The vagueness is an
artefact of **excerpting one sentence from a structured SRS**, and it applies to
`VAGUE_PRONOUN` generally, not only to notation.

**And `LO = T_LT` is not domain notation at all — it is a corrupted inequality.** The source
reads "...turn on the heating unit if `LO <= T <= LT` or the cooling unit if `UT <= T <= UO`".
The `<=` signs were flattened to `=` and a space lost during PDF-to-XML extraction; the same
damage appears throughout ("If `T = LO` or `UO = T`", "Condition 1: `LT = T = UT`"). Both `LO`
and `LT` are defined one paragraph above the requirement that uses them. No detector could
resolve `LO = T_LT`, because it is not a meaningful expression.

**Unmeasured risk this raises, larger than this limitation:** PURE's XML is extracted from
PDFs and mathematical notation did not survive in this document. Any requirement containing a
comparison is suspect. Check how widespread this is before PURE carries evaluation weight — a
corpus that silently turns `<=` into `=` corrupts far more than one example.

**Measured 2026-08-14 — the risk is real but confined to one document, not corpus-wide.**
Scanned all 18 files of the committed annotated subset (`datasets/requirements-xml/XMLZIPFile/`)
for three signatures: `X = Y = Z` comparison chains, `T_LT`-shaped underscore tokens, and
surviving Unicode math (`<=`, `>=`, `!=`, `+/-`).

- Only **6 of 18** documents contain any mathematical `=` at all — SRS text is overwhelmingly
  prose, so most files have nothing to corrupt.
- Of those six, most `=` uses are key-value labels, not comparisons: `0000 - gamma j.xml`
  ("ID = User ID of the...", 21 occurrences), `2005 - microcare.xml`, `1995 - gemini.xml`.
- **`1998 - themas.xml` is the only file showing the flattened-comparison signature** — the
  `X = Y = Z` chains ("If `T = LO` or `UO = T`", "Condition 1: `LT = T = UT`"). Note that its
  other `=` uses are legitimate definitions (`LO : Lower Overtemperature Value = TSET - OD`),
  so the damage is specifically to *comparisons*, not to all notation.
- **`2006 - eirene sys 15.xml` retained 7 Unicode math symbols**, which proves extraction
  *can* preserve them. So the corruption is per-document — a function of the source PDF's
  fonts or glyph encoding — rather than a systemic property of the PURE pipeline.
- `2007-ertms.xml` has one math-ish `=` ("speed = zero"), which is prose. The ERTMS-derived
  fixtures are unaffected.

**Why it looked systemic from inside this project:** THEMAS is the single document this work
has leaned on most — the schema spot-check, all three 2026-08-10 runs, and several behaviour
fixtures. The one damaged document is the one everything was built on.

**Scope of this measurement, stated honestly:** it covers the 18-file annotated XML subset
only. The 79-document full corpus (`datasets/pure-full/`, PDF/DOC/HTML) is not parsed by
anything in this project yet, so nothing is known about corruption there. Re-run this scan as
part of whatever extraction is eventually built for it — the three signatures above are cheap
and the script is trivial to reproduce.

**Third observation, from the live loop:** naming a referent is not sufficient.
`THEMAS-REQ-E` round 1 replaced "this condition" with "Condition 2" — an improvement, and it
silently repaired the mangled inequality — and round 2 flagged `VAGUE_PRONOUN` *again*,
because "Condition 2" is itself defined outside the requirement. Left alone this recurses to
the cap. The loop terminated only when the human inlined the actual criteria, which is content
the pipeline structurally cannot fetch for itself.

**6. `TestPlan` requires *every* case to cover the plan's own requirement — revisit if
it rejects real generator output.** A plan for REQ-1 holds test cases, each listing the
requirements it covers. The rule chosen is strict: every case must list REQ-1, though it
may list others too (an end-to-end test spanning REQ-1 and REQ-2 is fine).

The looser alternative — *at least one* case in the plan mentions REQ-1 — was rejected
because its failure is silent: a plan for REQ-1 could contain nothing that tests REQ-1
and validate cleanly. The strict rule's failure is a loud `ValidationError` naming the
offending case ids.

Known risk: if the Test Case Generator legitimately emits a case in REQ-1's plan that
covers only a dependency (say REQ-2 as a precondition), this rejects it. Watch for that
once the generator is running against real requirements. Loosening is a one-line change
to `TestPlan._cases_cover_this_requirement` — swap the "every case" check for "any
case". Decide from observed generator behaviour rather than in advance.

*Measured 2026-08-14 — keep the strict rule; the risk has not been approached.* Across
all three recorded real runs (`docs/superpowers/results/2026-08-10-first-real-run/`,
`2026-08-10-gemini-paid-tier-run/`, `2026-08-10-tpm-throttle-validation/`): 4 `TestPlan`s,
13 `TestCase`s, **zero** cases citing more than one requirement, and no
`VALIDATION_FAILURE` at `test_generator` in any run — the only `test_generator` failures
recorded are transport. Nothing has pushed against this rule, so there is no reason to
loosen it.

**But the sample is much weaker than those counts suggest, and this is the important
part.** Cross-referencing each run's `dependency_report` against which requirements
actually produced a plan: run 1's links were A->C, B->D, E->C, F->C while the only plan
produced was for **G**, which appears in no link; run 2's links were D->B and F->C with
plans for A, C and G, of which only **C** appears in a link (`dependencies_for` matches
either side, so F->C counts); run 3 produced no plans at all. So the Test Generator has
received a non-empty `relevant_dependencies` **exactly once** in the project's history —
one link, on THEMAS-REQ-C — and that plan's 4 cases cite only C and mention no sibling
requirement anywhere, including in preconditions. That is n=1, which is not evidence.

The cause is mundane rather than interesting: most requirements never reached test
generation, dying at `cap_stopped` or transport errors, and the survivors happened to be
the ones with no dependency links.

Consequence worth carrying: **Known Limitations 1, 6 and 7 are all blocked on the same
single unmeasured behaviour** — whether the Test Generator uses dependency context at all.
1 needs both plans to emit a spanning case for duplicates to exist; 6's risk case *is* a
spanning case; 7's argument that stale dependencies corrupt the deliverable assumes
dependencies influence output. None of the three can be settled without that measurement.
Behaviour scenario S1 (`scn-01-dep-pair`, two ERTMS requirements, one real link) already
exists to produce it and lists the threading into Strategy Selector/Test Generator as a
hard criterion — and being two requirements, it cannot get lost in the cap/transport noise
that swallowed THEMAS. The specific post-run check is recorded in
`docs/superpowers/plans/2026-08-11-behavior-scenarios-RUN-PROMPT.md`.

**Suite result 2026-08-13 — measurement obtained; keep the strict rule.** S1's
`TC-13-PURE-ERTMS-R7-2` lists both `R7` and `R8` and sits in `R7`'s plan, so it *includes*
the plan's own requirement and the strict rule was satisfied, not challenged. Across the
whole suite the rule again never fired. The risk case (a case covering *only* the
dependency) is now demonstrably closer than "never approached" — the generator will write
spanning cases — but it has still not occurred.

**7. Document-level analysis (Consistency Checker, Dependency Mapper) never re-runs
after refinement changes a requirement's text — a rewrite can silently introduce a new
conflict or a new dependency that nothing in the pipeline ever checks for.**
`run_document_stages` (`orchestrator/pipeline.py`) runs both document-level stages
exactly once, on the original `RequirementSet`'s text, before any requirement enters its
refine loop. `run_requirement` then computes `relevant_conflicts`/`relevant_dependencies`
once from that same original-text report and holds them constant across every
refinement round and into the Strategy Selector/Test Generator calls
(`_run_refine_loop`'s own docstring: "the document-level analysis doesn't change
between rounds"). If a Refiner Rewriter pass changes a requirement's wording enough to
create a genuine new dependency on a sibling requirement, or to contradict one, neither
the mapper nor the checker that would have caught it ever runs again — they already
finished before the rewrite existed.

Mechanism that makes this a real risk, not just a theoretical one: the Refiner Rewriter
is called with only `(current, answers, revision_number)` — no `RequirementSet`, no
visibility into any other requirement's text at all. Its prompt
(`orchestrator/example_prompts/refiner_rewriter.txt`) explicitly forbids inventing new
behavior, restricting it to folding the human's answer into the existing wording — but
the human's answer itself is exactly where a concrete number, actor, or condition gets
introduced (that's what a `VAGUE_PRONOUN`/`INCOMPLETE` fix requires), and nothing checks
whether that concrete detail collides with what a sibling requirement already says.
Structurally the same shape as the numeric conflict deliberately planted in behavior
scenario S4 (3°F vs. 5°F, same subject, different requirement,
`docs/superpowers/plans/2026-08-11-behavior-scenarios.md`) — except here the model, not
a fixture author, would be the one introducing it, during a stage the design never
re-checks.

The two halves are not equally damaging, and the note above understates the dependency
half. `relevant_conflicts` is only ever passed to the Quality Checker inside
`_run_refine_loop`, so a missed *conflict* is a reporting loss: the final consistency
picture is wrong, and — separately — rounds 2..n are still being told about round 1's
conflicts, because the filtered list is computed once before the loop starts.
`relevant_dependencies` is passed further: into `stage_fns.select_strategy`
(`(current, record.classification, relevant_dependencies)`) and into
`stage_fns.generate_tests` (`(current, strategy, relevant_dependencies)`). A missed
*dependency* therefore corrupts the deliverable itself — strategy selection and test
generation run on an incomplete dependency picture for the very text the human just
approved. Concretely: `R5` = "the report is generated" is flagged `INCOMPLETE`, the
human answers "after the nightly sync completes", and the rewrite now depends on the
sync requirement `R2` — a `DependencyLink` that did not exist when the mapper ran, so
no integration test spanning the pair is ever considered.

*Qualification added 2026-08-14, after the check recorded under Known Limitation 6.* The
claim above that a missed dependency "corrupts the deliverable" is structurally true but
empirically unproven, and should be stated that way. The Test Generator has received a
non-empty `relevant_dependencies` exactly once across every real run (n=1), and that plan's
cases showed no sign of using it. If the generator turns out to ignore dependency context,
the practical damage from a stale dependency list is much smaller than the wiring suggests
— the conflict half would then be the more consequential one after all. Behaviour scenario
S1 settles this; do not weight the dependency half of this limitation above the conflict
half until it has run.

**S1 has now run (2026-08-13), and this qualification is withdrawn.** The Test Generator does
use dependency context: `TC-13-PURE-ERTMS-R7-2` cites both ends of the `R8 -> R7` link. So the
dependency half of this limitation is the damaging one after all, as originally written — a
stale or missing link changes generated output. The qualification above was correct to demand
evidence and wrong in its guess.

**Observed live 2026-08-14 — predicted in advance, then confirmed.** `PURE-THEMAS-R6-P`
(planted at 5°F) conflicts with `PURE-THEMAS-R6` (3°F, the source value).

- Round 1: the human answered "3°F — this requirement has the wrong number."
- The Rewriter **applied it**: the text became "up to 3 degrees Fahrenheit". The conflict was
  genuinely gone.
- Round 2: the Quality Checker flagged `inconsistent` **anyway**, from the consistency report
  computed once on the original text before refinement began.

This is stronger evidence than the reasoning recorded above: the human's answer worked, the
pipeline fixed the document, and the pipeline then failed to notice its own fix.

**The exit was manual.** The loop only terminated because the human set
`user_confirms_resolved: True` in round 2 — the sole legitimate use of that flag in the whole
session. So the design currently depends on a person noticing that its document-level analysis
has gone stale; without one, a corrected requirement fails every remaining round and caps.
That is a stronger argument for the advisory post-pass (option A) or phasing (option B) than
the cost analysis above.

**The pair isolates two different failures cleanly**, and is worth citing together:
`PURE-THEMAS-R6` — human gave a correct, complete, actionable answer and the pipeline could
not use it, because the fix belonged to a *different* requirement (architectural).
`PURE-THEMAS-R6-P` — human's answer was used, and the pipeline then could not see the result
(staleness).

Cycles are a third case, and the sharpest one: `DependencyGraph.find_cycles()`
(`design/schemas.py`) is a one-shot DFS over the edges the mapper produced from the
original text, and `IssueCategory.CIRCULAR_DEPENDENCY` routes a cycle back to the
Refiner. A new edge added by a rewrite can *close* a cycle that `find_cycles()` never
sees — and there is no mechanism by which a rewrite-created cycle could route anywhere,
since the routing decision was already made and consumed.

Not fixed because the obvious fix — re-run `run_document_stages` after every rewrite, or
after every requirement finishes — has real costs of its own: it multiplies
document-level API calls (potentially once per revision round, not once per document),
it re-opens documents that already reached `DocumentOutcome.COMPLETED`/`DEGRADED` (no
state currently models "document-level analysis is now stale, redo it"), and it
interacts awkwardly with resumability — `resume_document` currently treats a completed
document-level stage as permanently settled, and has no equivalent of
ORCHESTRATOR_CONTRACT.md item 18's prompt-provenance check for "the input text itself
changed after this stage ran." No design has been chosen; this is recorded as an open
gap, not a rejected fix.

Worth more than it looks: re-checking consistency after a rewrite is also the *only*
mechanical check on human-supplied domain facts anywhere in the design (see Known
Limitation 2). Two requirements can agree and both be wrong, so this is weak — but a
human answer that contradicts a sibling requirement is the one case where the pipeline
can catch a domain error rather than only a structural one.

### Limitation 7 — the order to attack it in (decided 2026-08-14)

Constraint that shapes every option: the Quality Checker needs conflict context *during*
refinement, so document-level analysis cannot simply be moved later. Anything that gives
refinement's output a fresh document view is an *addition*, not a reordering.

**Step 0 first, and it is not about staleness at all: make the Test Generator actually use
dependency context.** The `relevant_dependencies` wiring exists (contract item 16), but
`orchestrator/example_prompts/test_generator.txt` only mentions it as a closing aside
inside the untrusted block — "a real precondition named here *can* inform a test case's
`preconditions`" — with no rule in the Rules section requiring anything. Contrast the
technique rule, which is emphatic, repeated, and enforced by
`_test_generator_extra_check`. The multi-requirement rule is permissive too ("*may* also
name other requirement ids"). If S1 shows dependency context has no visible effect, the
most likely cause is that the prompt asks for nothing — a version-1 prompt defect, not a
model limitation, and not a reason to design around the behaviour. The design's own claim
is that dependencies inform test generation; a generator that ignores them contradicts the
design and is a bug to fix.

The ladder, in order, each step cheap and each one informing the next:

1. **Run S1** and answer the three questions recorded in
   `docs/superpowers/plans/2026-08-11-behavior-scenarios-RUN-PROMPT.md`.
2. **If no effect: promote dependencies from aside to rule** in the Test Generator prompt —
   if a dependency names a real precondition, state it in `preconditions` or say why it does
   not apply. Prompt-only: no schema change, no pipeline change, no extra API call.
3. **Re-run S1 on the v2 prompt.** `prompt_hash` is recorded per attempt, so v1 vs v2 on an
   identical fixture is a clean, citable comparison — this is what the prompt-provenance
   work was for.
4. **Only then decide on phasing.** Plumbing fresh dependency reports into a stage that
   demonstrably ignores them is work with no observable effect. Once step 3 shows the input
   matters, "the input must not be stale" stops being speculative.

Two candidate designs for step 4, plus one rejected:

- **A. Advisory post-pass.** After every requirement finishes, run the Consistency Checker
  once more on the final texts and record the diff against the original report as a finding
  on `DocumentRunRecord`. One extra call per document, bounded, no reordering, no change to
  resume positions. Detects rather than prevents — tests were already generated from the
  stale picture — but it produces the frequency number this entry asks for and is worth
  doing regardless of step 4's outcome.
- **B. Phase the pipeline.** Split the per-requirement loop in two: pass 1 classifies and
  refines every requirement; document-level analysis then re-runs on the refined set; pass 2
  does strategy selection and test generation from the fresh reports. Document stages run
  twice per document — bounded, not per-round. This is the real fix, and it also catches
  rewrite-created cycles. Academic argument in its favour, which is the strongest one: asked
  "does your consistency analysis describe the refined requirements or the original ones?",
  the honest answer today is "the original", and that is a threat to validity that has to be
  declared. B makes the answer "the refined ones". Costs: control-flow restructure,
  `DocumentRunRecord` must hold two generations of reports, and `resume_document`'s position
  logic needs a second document-stage phase — that last one is the real cost, since resume
  positions are executed by `orchestrator/test_harness.py::test_resume_positions` precisely
  because that spec drifted once before.
- **C. Re-run document analysis after every rewrite. Rejected** — multiplies document calls
  by revision rounds, requires a "stale analysis" state nothing models, and re-opens
  documents that already reached a terminal `DocumentOutcome`.

Revisit once measured: none of the behavior scenarios (S1–S13,
`docs/superpowers/results/2026-08-11-behavior-scenarios/`) combine multi-round
refinement with a document whose requirements share overlapping domain vocabulary, so
how often this actually happens is currently unmeasured, not just unfixed. A scenario
built specifically to force it — two or more requirements sharing numeric/actor
vocabulary, at least one needing 2+ refinement rounds, then hand-diffing the final
consistency/dependency picture against the original report — would turn this from a
reasoned risk into a measured one.

**8. The Refiner is structurally one-requirement-in, one-requirement-out, but the
correct fix for `NON_ATOMIC` is a split into N requirements — so the schema cannot
express the right answer, and accepts two wrong ones without complaint.**
`IssueCategory.NON_ATOMIC` ("bundles more than one testable behavior",
`design/schemas.py`) is raised on requirements like *"The system shall generate reports
on inventory levels, product movement, and sales history"* — three testable behaviors in
one statement. The only place a rewrite can land is
`RefinedRequirement.refined_text: NonEmptyStr`, and `NonEmptyStr` is
`Annotated[str, Field(min_length=1)]` — nothing more. One id in, one string out. The
Rewriter's prompt (`orchestrator/example_prompts/refiner_rewriter.txt`) reinforces this
from the other side: "produce a **single** rewritten version", "Do not introduce new
requirements."

So a `NON_ATOMIC` flag has exactly two reachable outcomes, both wrong and both accepted:

1. **Cram.** The model returns all three behaviors in one string (newline-joined,
   semicolon-joined, or "and"-joined). The schema accepts it — there is no atomicity or
   newline constraint on `refined_text`. The next round's Quality Checker sees the
   crammed text as `text_checked` and flags `NON_ATOMIC` again, so the loop burns
   revisions on an issue it structurally cannot fix and terminates at the cap
   (`CAP_GENERATED`/`CAP_STOPPED`). Downstream, the orchestrator builds one
   `Requirement(id=req.id, text=refined.refined_text, ...)` from it, so a
   three-behaviour string is what strategy selection and test generation actually
   receive.
2. **Drop.** The model returns one clean atomic behavior and silently discards the other
   two. This *passes* the next quality check, is recorded as a successful refinement, and
   nothing anywhere states that two specified behaviors left the pipeline. Requirements
   coverage is lost with no trace — worse than case 1, which at least fails loudly.

Not fixed because the honest fix is a real design change, not a validator.
`refined_text` becoming `refined_texts: list[str]` ripples into: which text becomes the
next `RefinementRound.text_checked` (a round checks one text, and the whole
round-continuity chain in `RequirementRunRecord` is built on that being singular); what
ids the split requirements get, since they are not in the input `RequirementSet` and
`_test_generator_extra_check`'s `known_requirement_ids` check would reject test cases
citing them; and traceability from every `TestPlan` back to a requirement that, as
written in the source document, no longer exists. Splitting is arguably not the Refiner's
job at all — it changes the requirement set, which is a document-level edit performed by
a stage that only ever sees one requirement (the same blind spot as Known Limitation 7).

Two candidate directions, **neither adopted yet**:

- *Declare `NON_ATOMIC` non-refinable.* The Quality Checker still flags it; refinement is
  not expected to resolve it; the cap fires and the human splits the requirement upstream
  of the pipeline. Zero schema change, and it makes case 2 (silent drop) the documented
  failure mode rather than an unnoticed one. Costs: `NON_ATOMIC` requirements can never
  reach `COMPLETED`, which will show up in the evaluation as refinement "failures" that
  are really scope exclusions, and must be reported as such.
- *Reject multi-line `refined_text`.* A one-line guard that catches the mechanical form of
  case 1 (newline-joined lists) without any semantic heuristic. Deliberately *not*
  counting "shall" occurrences or conjunctions — that misfires on legitimate compound
  conditions ("when X and Y, the system shall Z") and would be a check whose false
  positives are invisible. Partial by construction: it does nothing about
  semicolon/"and"-joined cramming, and nothing at all about case 2.

### Measured 2026-08-14 — the pressing problem is detector precision, not the schema

Checking how often `NON_ATOMIC` actually fires changed the priority of this entry. It
fires often — **14 flags** across the three recorded real runs, behind only `incomplete`
(23) and `vague_pronoun` (25) — but every flag is the same mistake, so most of them should
never have been raised, and the missing 1->N representation is not what is hurting.

Every `non_atomic` flag in the real runs splits on a conjunction, and the splits are
causal or sequential chains rather than independent behaviors:

- `THEMAS-REQ-A` — "the determine heating/cooling mode process is activated **and** makes a
  heating/cooling request". One causal step: the trigger activates the process, the process
  emits the request. One test covers it — request a temperature change, observe a
  heating/cooling request. There is no second test to write.
- `THEMAS-REQ-B` — "shall identify the current temperature value as an invalid temperature
  **and** shall output an invalid temperature status". Detect, then report. Sequential.
- `THEMAS-REQ-H` — "identify the event type **and** format an appropriate event message".
  Sequential again.
- `THEMAS-REQ-C` — "maintaining the ON/OFF status of both heating **and** cooling units".
  One behaviour applied to two objects.
- `THEMAS-REQ-B` (round 1) — flagged as *"bundles two conditions (temperature less than
  lower value and temperature greater than upper value)"*. Those are **conditions**, not
  behaviours: together they are one range-validity rule. This flag is wrong by the prompt's
  own definition, not merely by judgement.

Contrast a genuine case, the one S10 uses: "generate reports on inventory levels, product
movement, and sales history" — three independent outputs, three separate tests, no ordering
between them.

**Cause is in the prompt, not the model.** `orchestrator/example_prompts/quality_checker.txt`
defines the category as *"the requirement bundles more than one testable behavior into one
statement"* — no requirement that the behaviours be **independently** testable, and no
example either way. Splitting on "and" is a reasonable reading of that text. As currently
prompted, `non_atomic` is a conjunction detector.

**Cheapest fix, and it should come before anything in this entry:** tighten the definition
to *independently* testable, and give one positive and one negative example — the
three-reports case as `NON_ATOMIC`, a causal chain like `THEMAS-REQ-A` as *not*
`NON_ATOMIC`. Prompt-only, version-1 prompt, `prompt_hash` already recorded per attempt so
a v1/v2 comparison on the same fixtures is clean.

**Ripple worth measuring at the same time:** false `non_atomic` flags push requirements into
extra refinement rounds and toward the revision cap. 10 of the 19 requirement records with
rounds ended `cap_stopped`. How much of that is detector noise rather than genuinely
irreducible text is unknown, and the v1/v2 comparison would show it.

This does not close the structural gap above — a real `NON_ATOMIC` still has no expressible
fix — but it reorders the work: fix the detector first, then see how many real cases remain
to justify the `list[str]` redesign.

#### Why a split is not a rewrite: it invalidates the document, not just the requirement (2026-08-14)

A rewrite changes one requirement's text. A split changes the requirement **set**, and that
difference is what actually blocks the `list[str]` redesign — not the field type.

If `REQ-7` becomes `REQ-7a`/`REQ-7b`/`REQ-7c`, then in the same instant:

- the set has N+2 requirements, so both document-level reports (consistency and dependency)
  describe a document that no longer exists — not merely stale as in Known Limitation 7, but
  *invalid*, since the membership itself changed;
- each fragment needs its own `Classification`, `QualityReport`, `TestStrategy` and
  `TestPlan` — a per-requirement restart, not a continuation;
- every `DependencyLink` naming `REQ-7` is now ambiguous: which fragment does it mean?
- every `TestCase` whose `requirement_ids` cites `REQ-7` has the same ambiguity;
- the fragment ids do not exist in the source document, so `_test_generator_extra_check`'s
  `known_requirement_ids` rejects them, and SRS traceability breaks at the same time.

So splitting inside the refine loop is a **restart with a modified document**, dressed up as a
stage output. That is the honest reason it is not a one-field change.

**Consequence, and it points at the cheap option.** If a split forces a document-level
re-analysis anyway, the cheapest correct place to split is *before the pipeline runs*. That
makes the "declare `NON_ATOMIC` non-refinable" direction listed above the principled choice
rather than a concession: the pipeline **detects and reports**, the requirement terminates
with a reason recorded (reuse `CAP_STOPPED` with an explicit `cap_reason` rather than adding
an outcome member), the rest of the document completes normally, and the human splits the
requirement in the SRS and re-runs. No id invention, no partial re-analysis, no traceability
loss — the split happens where the document is authored.

#### Prompt v2 measured 2026-08-14 — definition fix held on the clean case, refuted on a real one

The "independently testable + one positive/one negative example" fix proposed above shipped
in `orchestrator/example_prompts/quality_checker.txt` (commit `2178774`) and was re-run
against the same four scenarios covering every affected requirement, refusing answer policy
unchanged. Full numbers: `docs/superpowers/results/2026-08-11-behavior-scenarios/RESULTS-V2.md`.

- `THEMAS-REQ-B` (the causal-chain misfire cited above) **stopped firing** — held as predicted.
- `LUITEL-R7` (the genuine three-reports case, this entry's own contrast example) **still
  fires** — the definition did not over-correct into silence on a real positive.
- `PURE-ERTMS-R2` ("train and shunting movements") was predicted to stop firing and **did
  not** — v2's own explanation invokes the new "independently testable" language and judges
  the two movement types genuinely separable, which is defensible on the text (two operating
  modes, not two steps of one operation). The 2026-08-14 evidence table above miscategorized
  this one as a conjunction-split; it is closer to the three-reports case than to
  `THEMAS-REQ-A`/`THEMAS-REQ-B`/`THEMAS-REQ-H`.

So the detector-precision fix is real but partial: it removes the mechanical
split-on-"and" failure mode for causal chains without suppressing genuine bundling, which is
what it was supposed to do. It is not a blanket cure for over-flagging — `PURE-ERTMS-R2`
shows the model can still classify a boundary case as bundling under the tightened wording.
No prompt change made in response to this result (per the batch's own rule: a refuted
prediction is a result, not a failure to patch away). The structural gap this entry opened
with — no expressible fix once a genuine `NON_ATOMIC` is confirmed — is unaffected either
way; this result is entirely about detector precision, the concern the 2026-08-14
"Measured" note above already reprioritized ahead of the schema question.

**Do not build any of this yet.** All 14 real `non_atomic` flags measured above are false
positives, so a split workflow built today would be machinery serving wrong flags. Fix the
category definition first, then count what genuine cases remain.

**Suite result 2026-08-13 — the detector is better than the 2026-08-10 data implied.** 5
`non_atomic` flags across 34 requirements, not the near-universal over-flagging predicted:

- `LUITEL-R7` — "inventory levels, product movement, and sales history" — **correct catch**,
  the genuine three-behaviour case.
- `AUTOGEN-US2` — "reliable and efficient" — defensible either way (two unmeasurable
  qualities).
- `THEMAS-REQ-B` ("identify ... and output ...") and `PURE-ERTMS-R2` ("train and shunting
  movements") — the conjunction-split shape, still wrong.

So roughly 2 of 5 are wrong rather than 14 of 14. Generalising from a single document
(THEMAS) overstated this; the definition fix is still worth making, but it is polish, not a
rescue. The `list[str]` redesign remains unjustified: exactly one genuine case appeared, and
it was handled by a no-op rewrite plus a `COMPLETED` outcome (see Known Limitation 10).

**Live-session evidence 2026-08-14 — the genuine case, with a human on record.** `LUITEL-R7`
was answered by hand: yes, three independently testable behaviours, split it, and that cannot
be done by rewriting this requirement in place. Result: text unchanged across all three
rounds, `CAP_STOPPED`, cap reason recorded as "three separate report behaviours plus an
unspecified trigger — needs a document-level split, not a rewrite."

Contrast the refusing policy on the identical fixture, which reached **`COMPLETED`** on the
same requirement by asserting the bundle was "one causal step" — a claim that is false for
this fixture — and having the checker accept it. So the same genuine defect produced a clean
success under one answer policy and an honest cap under the other. Attribute `LUITEL-R7`'s cap
to *both* of its issues, not the split alone: its `incomplete` flag (no trigger) is unknowable
too, since the requirement is an isolated illustrative sentence with no source document.

**Also learned: `NON_ATOMIC` flags structure, not whether splitting is worth doing.**
`AUTOGEN-US2` ("reliable and efficient") is technically non-atomic as well, but splitting it
yields two equally unmeasurable requirements, because its real defect is undefined terms. A
`list[str]` split mechanism would fire on both cases identically and only one would benefit.

**And the human channel has the same gap as the schema.** Answering "this flag is correct, and
it cannot be fixed at this level" is not expressible: `user_confirms_resolved: True` means
"resolved, stop raising it" (false here), and `False` means the issue is re-asked every round
until the cap. So a genuinely unfixable-in-place issue is indistinguishable in the record from
one the human simply keeps failing to resolve. The proposal above to reuse `CAP_STOPPED` with
an explicit `cap_reason` has a human-side equivalent — a `RefinerAnswer` flag meaning
"acknowledged, out of scope for refinement". Not designed, not adopted, recorded so it is not
rediscovered.

Decide from data, not in advance: how common `NON_ATOMIC` actually is in the reserved
corpora (`datasets/EVALUATION_DATASETS.md`) determines whether the `list[str]` redesign
is worth its cost. Currently unmeasured. Behaviour scenario S10
(`docs/superpowers/plans/2026-08-11-behavior-scenarios.md`) is the only thing looking at
this, and it looks by hand — it has no automatic assertion, because the schema it would
assert against is exactly what is missing.

**9. Three of `SystemType`'s four members are behaviorally identical, so the Classifier —
a full LLM call per requirement — may be contributing almost nothing to technique
selection.** Found while checking Known Limitation 3's justification (2026-08-14).

`ELIGIBLE_TECHNIQUES` (`design/schemas.py`) maps `SystemType.WEB`, `SystemType.MOBILE`
and `SystemType.OTHER` to the *same* `_NON_AI_TECHNIQUES` frozenset; only
`SystemType.AI_SYSTEM` gets a different pool (`_AI_TECHNIQUES`). At the eligibility layer
the four-way classification is therefore a binary: AI or not-AI. Whether a requirement is
web, mobile or "other" constrains nothing.

That alone might be acceptable — layer 1 is described in "How techniques get selected" as
a hard *constraint*, not as the whole selection, and `system_type` also feeds
`IssueCategory.INFEASIBLE_FOR_TYPE` in the Quality Checker, which is a genuinely
per-type judgement. What sharpens it into a limitation is the observed data: **every
classification in every recorded real run returned `other`**
(`docs/superpowers/results/2026-08-10-first-real-run/`,
`2026-08-10-gemini-paid-tier-run/`, `2026-08-10-tpm-throttle-validation/`). So on the only
document run so far, the Classifier spent one LLM call per requirement to select the
default pool.

Do not overstate this — the sample is one document (`themas-fischbach2022`, 8
requirements) run three times, not 23 independent observations, and the wider corpus does
contain requirements that should classify as `AI_SYSTEM` (`autogen-wu2024`,
`metagpt-hong2024`), which would exercise the one pool that differs. The durable claim is
the structural one (three members, one pool); the empirical claim is provisional.

Two questions to answer with data before changing anything, both cheap:

- **Does the Classifier ever return anything but `other`?** Run the AI-system documents
  through and look. If `AI_SYSTEM` fires correctly there, the stage is doing real work and
  this limitation is about the WEB/MOBILE/OTHER redundancy only.
- **Does `system_type` change the Quality Checker's output at all?** `INFEASIBLE_FOR_TYPE`
  is the only place the distinction between web, mobile and other could still matter.
  If that category never fires either, the Classifier's cost/benefit is genuinely in
  question and collapsing `SystemType` to `{AI_SYSTEM, OTHER}` — or deriving it without an
  LLM call — becomes the simpler design.

Not fixed now because both fixes are premature without those two measurements: merging
enum members is a schema change that invalidates recorded runs' `system_type` values, and
removing the Classifier stage entirely would drop `INFEASIBLE_FOR_TYPE` and change the
pipeline's eight-stage story, which is described in the thesis prose. Measure first.

#### Third part, found while trying to answer the second (2026-08-14): the one observed use of `system_type` was a false positive, and the cause is an undefined label

`INFEASIBLE_FOR_TYPE` has fired exactly once in the project's history — `THEMAS-REQ-C`,
classified `other`, on the requirement *"The THEMAS system shall maintain the ON/OFF status
of each heating and cooling unit"*, with this explanation:

> "The classified system type is 'other', but the requirement seems to imply a level of
> automation or control typically associated with more specific system types, which might not
> be feasible for a system classified as 'other'."

Nothing is wrong with that requirement. The model inferred a *capability limitation* from a
label that only means *not one of the other three*. So the only observed effect of the
Classifier's per-requirement call, across every real run, is one false flag — and a false
flag is not free: it fails the quality check, which sends a clean requirement into the
refinement loop, spends a human interaction and a rewrite on it, and pushes it toward the
revision cap.

**A first fix was proposed and is wrong — recorded so it is not tried again.** The proposal
was: never raise `INFEASIBLE_FOR_TYPE` when `system_type` is `other`. It fails because the
category's own worked example in `orchestrator/example_prompts/quality_checker.txt` is *"an
adversarial-robustness expectation on a system that is not an AI system"* — and in this
taxonomy "not an AI system" means `web`, `mobile` **or `other`**. The rule would delete the
category's flagship legitimate use: an AI-specific demand (adversarial robustness,
retraining, accuracy thresholds) sitting in a document classified `other`, which nothing else
in the pipeline catches. Silencing a real check to remove one false positive is the wrong
trade.

**Actual root cause: the Quality Checker is never told what the labels mean.** Its prompt
passes the bare string via `TARGET requirement's classified system type:
<<<FIELD:system_type>>>` and defines none of `web`/`mobile`/`ai_system`/`other` anywhere. The
model had to guess what `other` implies and guessed "unspecific, therefore possibly limited".
This is Known Limitation 5's failure mode applied by the pipeline to itself: an undefined term
handed to a stage that cannot resolve it.

**Corrected fix — prompt-only, and needed regardless of anything else here:**

1. Define the four labels in the prompt. In particular: `other` means "not web, not mobile,
   not an AI system"; it does **not** mean unknown, limited, or unusual.
2. State the trigger positively: raise `INFEASIBLE_FOR_TYPE` only when the requirement demands
   a capability the classified type excludes.
3. State the anti-trigger explicitly: never raise it because the label is broad or unspecific.

The observed false positive cannot be derived from those instructions, and the AI-expectation
catch survives.

**Inherited-error caveat, to carry into threats to validity:** this category's correctness
depends on the Classifier being right. A genuine AI system mislabelled `other` will produce a
false `INFEASIBLE_FOR_TYPE` on a perfectly good AI requirement, with the real fault upstream.
`INFEASIBLE_FOR_TYPE` is therefore inherently second-order evidence.

#### Should the human confirm or override the system type? (discussed 2026-08-14, not adopted)

Reasonable, but **document-level, not per-requirement.** THEMAS is one thermostat system and
all 8 of its requirements share a type; asking a human N times produces one answer. The cheap
shape is: classify once per document, human confirms or overrides, done — one question per
document instead of N.

That shape also dissolves this limitation rather than working around it: if system type is
genuinely a document-level property, the per-requirement Classifier call is N calls buying one
answer, and 8 LLM calls collapse to 1.

Costs, stated honestly rather than glossed:

- A **third** human interaction point. `HumanFns` has two; a third has to be carried by the
  protocol, the CLI, `resume_document`'s positions and the record — including *who* set the
  label, or the audit trail (Known Limitation 2's only real mitigation) degrades.
- Human effort per document rises. Small (one decision), but it is a number the evaluation
  should report, not absorb silently.
- Mixed documents break the assumption — a system with an AI component has requirements of two
  types, so per-requirement override for exceptions comes back, and the simplicity leaks.

Benefit available either way, and worth taking on its own: **record both the model's label and
the human's**. That yields Classifier accuracy on real data, which currently has n=0.

**Order of work for this limitation:** (1) the prompt fix above — cheap, independent; (2) run
an AI-system document plus scenario S12, to find out whether the Classifier is actually wrong
or merely redundant; (3) only then decide document-level classification, which is a pipeline
change and should not be made before (2) says whether it is fixing an error or just removing
waste.

#### Per-type technique pools: what the syllabi actually support, and a corpus check that reframes the whole entry (2026-08-14)

**The problem is per-type test *content*, not type identification.** The Classifier's label
looks correct — THEMAS is genuinely `other` — and nothing suggests it is bad at labelling. The
failure is downstream: a correct answer arrives and nothing consumes it, because `WEB`,
`MOBILE` and `OTHER` share one pool. Framing this as "the Classifier is inaccurate" would be
wrong; it is "there is nothing type-specific to do with the answer."

**Mobile: real, citable content exists.** ISTQB Certified Tester Mobile Application Testing
(CT-MAT), Foundation Level, **version 2019 (3 May 2019)** — read from the BCS-hosted syllabus
PDF, `https://www.bcs.org/media/6355/swt-mobile-application-testing-syllabus.pdf`. Its
chapter 2 is mobile *test types*: device features, different displays, device temperature,
input sensors, input methods, screen orientation change, typical interrupts, access
permissions, power consumption and state, notifications, quick-access links, OS user
preferences, interoperability across platforms/OS versions, co-existence with other apps,
connectivity methods. Section 3.3, "Experience-based Testing Techniques", covers personas and
mnemonics, heuristics, tours and session-based test management — learning objective MAT-3.3.3
(K3) is *"Make use of a mobile specific tour (such as the Feature tour) to test a mobile
application."*

**Important distinction: CT-MAT adds test *types* and experience-based specialisations, not
black-box design techniques.** There is no chapter introducing anything of the same kind as
equivalence partitioning or boundary value analysis. Putting `INTERRUPT_TESTING` into
`TestTechnique` would place a test type beside design techniques — a category mix. Note the
existing precedent, though: `PERFORMANCE` is already a test type (from CT-PT), added
deliberately because a real THEMAS requirement fit nothing else. Extending that precedent is
defensible; doing it without noticing it is a precedent is not.

**Web: the reasoning is sound, the grounding is thinner, and it is blocked on the corpus.**
No ISTQB specialist syllabus for web testing was found. Cross-browser/configuration concerns
can instead be grounded in **ISO/IEC 25010's Compatibility** characteristic, with
configuration adaptability under what the **2023** revision renamed **Flexibility** (it was
Portability in the 2011 edition — cite the edition explicitly, the 2023 revision reorganised
several characteristics; see `https://iso25000.com/en/iso-25000-standards/iso-25010` and
`https://quality.arc42.org/articles/iso-25010-update-2023`). But compatibility is **not
web-exclusive**: CT-MAT 2.2.5 is device/OS-version interoperability, the same idea over a
different configuration space (browsers and versions vs. devices and OS versions). So it does
not differentiate `WEB` from `MOBILE`; if added, it belongs to both, with the configuration
dimension named per type in the prompt.

**Corpus check, and a correction.** An earlier claim in this entry — that the corpus contains
requirements which should classify as `AI_SYSTEM`, naming `autogen-wu2024` and
`metagpt-hong2024` — **is wrong**. Those documents are requirements *produced by* AI agent
frameworks (a generic product, a colour picker), not requirements *for* AI systems. Scanning
all 52 requirements across all 10 documents in `datasets/requirements_dataset.json`: **zero**
requirements contain explicit AI/ML vocabulary (three regex hits in ERTMS were the word
"train", the vehicle), and **zero** web requirements (no browser/page/URL/session/login/
portal/HTTP vocabulary anywhere).

*Correction to that scan, same day:* keyword scanning is the wrong instrument for the AI half.
`ACTAPP-R2-AC1` — "Accurately identifies when the user is driving" — contains no AI vocabulary
at all, yet scenario S12's ground-truth file classifies it as `SystemType.AI_SYSTEM` (an ML
classifier with no single correct output, expected to route to
`metamorphic`/`statistical_threshold`/`adversarial`). So the corpus holds **at least one**
expected `AI_SYSTEM` requirement, identified by judgement rather than vocabulary. The claim
that survives is narrower and still decisive for this entry: no `AI_SYSTEM` classification has
ever occurred in a *real run*, because no document containing one has been run.
`actapp-arora2024` *is* genuinely mobile — "The patients should receive a notification to
stand up and move around...", "The patients should not receive notifications when busy" —
mapping onto CT-MAT 2.2.1 (notifications), 2.2.3 (OS user preferences) and 2.1.7 (interrupts).

That reframes the entry: `AI_SYSTEM` is the only label that currently changes the technique
pool, and no document in the design corpus contains one. **So today the Classifier cannot
change the technique pool for any document this project has.** Adding mobile content would be
the first thing to make the stage do work, and ActApp is the document that would demonstrate
it.

**Chosen direction (2026-08-14):**

1. The Quality Checker prompt fix above — independent of everything else, fixes an active
   false positive.
2. Give `MOBILE` real content **at the prompt level**, not in the enum: when the type is
   `mobile`, have the Strategy Selector and Test Generator consider CT-MAT's mobile conditions
   (interrupts, orientation change, notifications, permissions, connectivity loss, power
   state). Citable section by section, testable immediately on ActApp, and no new pool,
   eligibility validator, test churn or diagram regeneration.
3. **Escalation rule, and the honest weakness of (2):** prompt guidance is not enforced —
   nothing will check that a mobile requirement actually received an interrupt test, and "a
   spec nobody executes drifts" applies. So: if mobile coverage only needs to *improve test
   generation*, the prompt is enough; if it needs to be a **reported metric** ("X% of mobile
   requirements have an interrupt test"), it must be in the schema, because an unmeasurable
   claim cannot go in the evaluation. Decide from the thesis, not from the code.
4. **Web pool: not now.** The citation is available but the corpus has no web requirements, so
   building the pool repeats exactly the mistake Known Limitation 3 stays open for — a
   category that can never fire on the available data. The first step is corpus work, not
   schema work: PURE holds 79 documents and only two have been pulled (THEMAS, ERTMS, both
   marked spent in `datasets/EVALUATION_DATASETS.md`), so a web SRS is very likely available
   there.

**Suite result 2026-08-13 — the empirical half of this entry is refuted; the structural half
stands.** Classifications across 34 requirements: `other` 31, **`mobile` 2, `ai_system` 1**.
The Classifier does discriminate, so "every real run classifies `other`" is dead — it was an
artefact of running only THEMAS. And `INFEASIBLE_FOR_TYPE` fired **zero** times, so the
`THEMAS-REQ-C` false positive did not recur; the prompt fix drops in priority accordingly
(keep it recorded, it is still correct, but it is fixing something that has happened once in
two suites).

What survives unchanged is the structural point: `WEB`, `MOBILE` and `OTHER` still share one
technique pool, so the two `mobile` labels bought nothing at the eligibility layer even
though they were correct. That is now the whole of this limitation — and it makes the
prompt-level mobile-conditions work (item 2 above) the only part still worth doing.

**10. A rewrite that changes nothing is accepted, and a requirement can reach
`RunOutcome.COMPLETED` with its text unchanged and a flagged issue never addressed.**
Observed in real data 2026-08-14, not hypothesised.

The trace, `THEMAS-REQ-A` in `docs/superpowers/results/2026-08-10-gemini-paid-tier-run/`:

- Round 1 on the original text raises `ISSUE-1` (`non_atomic`) and `ISSUE-2` (`incomplete`
  — "lacks the specific actor, trigger, or conditions that define how or by whom a
  temperature change is requested"). The round fails.
- The answers: Q1 -> "Keep this as one requirement", `user_confirms_resolved: True`.
  Q2 -> "The missing element is not stated anywhere else in the document either; flag it as
  a genuine gap rather than an omission this answer can fill in",
  `user_confirms_resolved: False`.
- The rewrite's `refined_text` is **character-identical** to `original_text` (137 chars
  both, verified equal).
- Round 2 checks that same text and returns `passed: true`, zero issues.
- Record: `outcome: completed`, `final_requirement.refined_text == requirement.text`.

Half of this is the design working. `ISSUE-1` appears in round 2's `suppressed_issue_ids`,
which is exactly what `user_confirms_resolved` is for — the human overrode the flag, and the
suppression machinery carried it. Not a bug.

`ISSUE-2` is the bug. It was never suppressed (the human explicitly declined to confirm it),
never fixed (the text did not change), and it disappeared anyway: the Quality Checker
returned opposite verdicts on character-identical input in consecutive rounds. The correct
behaviour was for the loop to keep failing until the revision cap fired — `CAP_STOPPED` with
the unresolved issue on record, which is what the cap exists for. Instead the run reports a
clean success.

**Two distinct defects, and they should not be conflated:**

1. **Nothing forbids a no-op rewrite.** There is no validator requiring
   `refined_text != original_text` — checked; the phrase in the `answers_used` note above is
   prose about *why* a rewrite happens, not an enforced rule. The 2026-08-05 fix
   ("`COMPLETED` after refinement required no rewrite") ensured `final_requirement` is
   *present* when refinement occurred; it never required the refinement to change anything.
2. **The Quality Checker is not deterministic on identical text**, demonstrated here rather
   than argued. This is a measurement problem for any per-category precision/recall work, and
   it is also the mechanism that let defect 1 pass unnoticed.

**Why the no-op is structurally invited, not just a model slip.** When every issue in a round
is either overridden by the human or genuinely unfixable by rewording, a no-op is the only
move the Rewriter can express: the round-continuity rule (`cur.text_checked !=
prev.rewrite.refined_text`, `RequirementRunRecord`) requires *some* text to carry into the
next round, and there is no representation for "nothing here can be fixed by rewriting". Same
shape as Known Limitation 8 one level up.

**Cost to the thesis, which is the reason this matters:** any reported "refinement success
rate" counts this requirement as a success, and nothing in the record distinguishes "the
defect was fixed" from "the checker changed its mind". That is a threat to validity on the
project's headline metric.

Two fixes, in order:

- **(a) Count it — do this first.** Record whether a rewrite actually changed the text, so
  "refined" and "refined, text unchanged" are separate numbers. Purely additive: no behaviour
  change, no extra API call, no retry churn, and it gives the frequency immediately. Every
  recorded run can be re-scored retrospectively, since the texts are already persisted.
- **(b) Then consider making it a rule:** a no-op rewrite is valid only if every issue raised
  that round was suppressed or confirmed-resolved. This is the project's own "two fields that
  must agree need something forcing it" pattern, and it is *reachable* — it would have fired
  on `THEMAS-REQ-A`, because `ISSUE-2` was neither. Failure is a loud `ValidationError`, which
  is better than a silent false `completed`. Cost: real runs error more often, and a retry may
  simply produce another no-op, so this trades a wrong success for a possible dead end. Decide
  after (a) shows how often it happens.

### Suite result 2026-08-13 — downgraded: this is a threat to validity, not a defect

The suite produced **38 no-op rewrites out of 47 (81%)**, which looked alarming and is not.
Every one traces to the scripted answer policy: 3 followed a human override
(`user_confirms_resolved: True`), and 35 followed an answer that declines to supply
information — the policy's own texts are refusals ("No numeric threshold is specified in the
source document...", "This is a cross-requirement conflict, not something one requirement's
clarifying answer can resolve in isolation", "The missing element is not stated anywhere else
in the document either"). **No no-op followed an answer that carried real information.** The
Rewriter is behaving correctly: told never to invent, and handed nothing, it returns the
input.

The mirror question was checked too — does the Rewriter invent facts when it *does* change
text? No. All 9 text-changing rewrites inserted explicit placeholders
(`[configurable duration]`, `[TBD: measurable safety parameters]`,
`[reliability threshold, TBD]`), exactly as the policy instructed. Zero fabricated values or
actors anywhere in the suite.

So fixes (b) and (c) above are **not needed** — there is no defect for them to catch. Fix (a),
counting no-ops, is still worth having. What replaces this limitation is a threat to validity,
and a serious one: **refinement effectiveness cannot be measured with an answer policy that
refuses to answer.** Every `COMPLETED` and every cap outcome in the 2026-08-10 and 2026-08-13
runs describes the policy as much as the pipeline. Settling it needs a *second* policy that
answers substantively, run alongside the refusing one — not an edit to the existing one, which
is what keeps the earlier runs comparable.

**That second policy now exists and has been run (2026-08-14).** Six scenarios, nine
requirement-slots, answered live by the operator against the source documents:
`docs/superpowers/results/2026-08-14-live-answers/` (`SESSION.md`, frozen transcript in
`answers.json`, replay driver verified at 0 misses / 0 drift warnings over 16 turns and 27
questions, $0.2833).

Headline: **the change *rate* is identical and the change *substance* is not.** Live-human
4/9 substantive; refusing policy 4/9 on the same fixtures — but the refusing policy's changes
were bracket-placeholder insertions, while the live-human run produced one genuine
cross-requirement fix (`PURE-THEMAS-R6-P`, 5°F -> 3°F, reaching `COMPLETED`) and two real
referent resolutions (`THEMAS-REQ-D`, `THEMAS-REQ-E`). So refinement *can* improve
requirements, and only when the human supplies content the pipeline cannot derive.

Two results that sharpen the threat to validity rather than removing it:

- **A refusing answer can manufacture a false success.** On `LUITEL-R7` the scripted policy
  asserted the requirement was "one causal step" — false for that fixture — and the checker
  accepted it, yielding `COMPLETED`. The live human called the same requirement genuinely
  non-atomic and it capped. The earlier runs' `COMPLETED` counts therefore include at least one
  requirement that was not fixed, in the direction that flatters the pipeline.
- **The corpus splits in two, and aggregates hide the effect.** Requirements from real SRS
  documents (THEMAS, ERTMS) have a source to answer *from*, and live answers added real
  content. Illustrative or LLM-generated sentences (`AUTOGEN-*`, `LUITEL-*`) have no document
  behind them, so the honest live answer matches the refusing one. Report per requirement and
  split by group; aggregate outcome counts understate the difference.

Subset caveat, stated in the plan and confirmed by the numbers: these six scenarios were chosen
where refinement was expected to help, so both policies score above the full suite's 19%
baseline. This measures a direction, not a rate.

Two residual observations from the 9 text-changing rewrites:

- `PURE-ERTMS-R2`: "shall **be able to** supervise" -> "shall supervise". A real improvement,
  but no answer asked for it — a mild deviation from "fold in the answer and nothing else".
- `THEMAS-REQ-D`: "these limits" -> "the specified temperature limits". Referent made explicit
  without inventing its value. Correct behaviour for `VAGUE_PRONOUN` given a refusing answer.

**11. The Rewriter can mark an already-measurable requirement as needing a measurable value.**
Found 2026-08-13, S12. `LUITEL-R1` — "The system shall reach a steady state **within 5s** after
reconfiguration to maximize availability" — was rewritten to "reach a steady state **[TBD:
measurable value]** within 5s after reconfiguration to maximize availability [TBD: measurable
value]". The `5s` threshold sits in the very sentence being annotated as unmeasurable. The
second placeholder ("maximize availability") is defensible; the first is straightforwardly
wrong, and it makes a good requirement worse.

Not invention (no fabricated values appeared anywhere in the suite), so this is a quality
regression rather than a hallucination. Likely cause: the Questioner/Rewriter pair acting on an
`AMBIGUOUS_TERM`/`NON_VERIFIABLE` flag without checking whether the requirement already carries
a threshold. Candidate fix is prompt-level and cheap — before inserting a placeholder, check for
an existing measurable value and leave it alone if one is present. Frequency unmeasured (n=1), so
count it in the next suite before changing anything. The same requirement also failed to select
`PERFORMANCE` (Known Limitation 3), and the two belong together: the pipeline both ignored an
explicit latency target and flagged it as missing.

**Generalised 2026-08-14 after the live session: this is one pattern with three variants, not
three defects.** In each, the Rewriter alters the text's *appearance* without altering its
*testability*:

1. **Placeholder where a value already exists** — `LUITEL-R1`, `[TBD: measurable value]`
   inserted beside its own `5s` (the original observation above).
2. **Deferral where no value exists** — `AUTOGEN-US2` became "...reliable and efficient
   **according to performance and reliability metrics defined by the product owner**". The
   human had supplied no threshold because none exists; the Rewriter converted that absence
   into specification-shaped prose. Arguably the most dangerous variant: `[TBD: ...]` is
   visibly unfinished, whereas "as defined by the product owner" reads like a deliberate
   design decision and could survive a careless review. The Quality Checker did re-flag it
   here, which is the good news — the failure mode only bites if it slips through.
3. **Cosmetic edit where the human said not to change anything** — `PURE-THEMAS-R6`, where the
   human stated the requirement was already correct at 3°F and the fix belonged to the *other*
   requirement. The Rewriter reformatted "3 degrees Fahrenheit" to "3°F" and changed nothing
   semantic. Harmless in itself, but it inflates any text-change metric: the live session's raw
   rate is 5/9, and 4/9 once this is excluded.

**Consequence for the evaluation, and it matters:** *text-change rate is not a proxy for
improvement*. On the same nine requirement-slots the refusing policy also changed 4/9 — its
"changes" being bracket-placeholder insertions. The two policies are indistinguishable on rate
and completely different on substance. Report *what* changed, per requirement, not how often.

Candidate prompt fix now covers all three variants: before altering the text, check whether the
requirement already states a measurable value (leave it), whether the answer supplied one at
all (do not manufacture specification-shaped deferrals), and whether the human asked for no
change (make none). Frequency still small — n=1, n=1, n=1 — so count them in the next suite
before changing anything.

**Prompt v2 measured 2026-08-14 — all three variants held.** The three rules above shipped in
`orchestrator/example_prompts/refiner_rewriter.txt` (commit `2178774`) and were re-run against
the refusing answer policy on the same four scenarios covering all three cited instances. Full
numbers: `docs/superpowers/results/2026-08-11-behavior-scenarios/RESULTS-V2.md`.

- `LUITEL-R1`: v2 final text is the original, unchanged — no `[TBD]` beside the `5s`.
- `AUTOGEN-US2`: v2 final text is the original, unchanged — no deferral phrase.
- `PURE-THEMAS-R6`: v2 final text is byte-for-byte identical to the input in both runs (the
  refusing-policy baseline had already left it unchanged here — the reformatting instance
  originally cited was from the 2026-08-14 live-human session, not this baseline — so this
  result confirms the rule holds under the refusing policy rather than fixing a regression
  this particular baseline had).

n is still 1 per variant — this is confirmation on the same three cited instances, not a
frequency measurement across a wider corpus. No outcome changed anywhere in the four-scenario
re-run (15/15 requirement-slots kept their 2026-08-13 outcome).

**Correction to the sentence that used to end this entry.** It claimed the three rules
"did not introduce any new failure to generate text at all in place of a legitimate rewrite".
Counting rewrites directly in both sets of run records shows otherwise:

| | v1 (2026-08-13) | v2 |
|---|---|---|
| rewrites | 19 | 18 |
| no-ops | 14 | **18** |
| text-changing rewrites | **5** | **0** |

Every text change disappeared, not only the three targeted ones. The two collateral losses:

- `PURE-ERTMS-R2` — v1 rewrote "shall **be able to** supervise" to "shall supervise", a genuine
  improvement (unprompted by any answer, but an improvement).
- `ACTAPP-R2-AC1` — v1 rewrote "Accurately identifies when the user is driving" to "**The
  system** identifies when the user is driving within [measurable accuracy threshold TBD]",
  which supplied a missing actor — a real `INCOMPLETE` fix — alongside the placeholder the new
  rules correctly suppress.

**This is logical, not a bug, and it has a consequence worth stating plainly.** New rule 2
("if an answer supplies no concrete value, make no change") fires *universally* under the
refusing policy, because that policy never supplies one. So under this answer policy the
Rewriter is now formally inert: it cannot change text at all. Refusing-policy runs can
therefore no longer distinguish "the rules work as intended" from "the refinement stage is
disabled" — the two are indistinguishable by construction.

Two things follow. First, the rules must next be measured against the **frozen live
transcript** (`docs/superpowers/results/2026-08-14-live-answers/answers.json`), the only run
where answers carry content and the rules can be selective; that is the test that can refute
them. Second, the unchanged outcome mix (15/15) despite losing every text change is itself a
result: these outcomes were never driven by rewriting, which corroborates the same finding
under Known Limitation 10.

**Measured 2026-08-14 — replay of the frozen live-human transcript against v2: selective, not
silencing.** Full numbers:
`docs/superpowers/results/2026-08-14-live-answers/RESULTS-V2-LIVE.md`. Same nine
requirement-slots as the 2026-08-14 live session (`SESSION.md`, same directory), same frozen
`answers.json`, only the prompt version differs. Text-changing rewrites: **5/9 (v1) -> 3/9
(v2)**, not 5/9 -> 0/9 as under the refusing policy. The count is exactly explained by which
v1 changes were real content versus artifacts: all three substantive changes survived
(`THEMAS-REQ-D`'s named referent, `THEMAS-REQ-E`'s named condition and bounds,
`PURE-THEMAS-R6-P`'s 5°F -> 3°F cross-requirement fix); both named artifacts were suppressed
(`AUTOGEN-US2`'s deferral phrase, `PURE-THEMAS-R6`'s cosmetic reformat — this entry's own
variants 2 and 3, now confirmed against a real human answer rather than only the refusing
policy's canned refusal). Answers the open question directly: the rules distinguish "supply no
value" from "supply real content" correctly on this transcript.

One requirement's comparison broke, unrelated to either prompt edit: `THEMAS-REQ-E` flipped
`COMPLETED` (v1) -> `CAP_STOPPED` (v2). v2's own round-1 rewrite named the referent correctly
but specified an action for only one of the condition's two branches, and the Quality Checker
caught the resulting `incomplete` gap in round 2 — a category that never arose under v1, so
the frozen transcript has no answer for it, the replay fallback fired, and the requirement
capped instead of completing. A transcript-replay limitation (one miss, one outcome flip, both
reported as regression-guard trips in RESULTS-V2-LIVE.md rather than absorbed silently), not a
defect in either edited prompt.

**Verified against the round texts, and it settles a misreading worth recording.** The two
rewrites in full:

- v1 round 2: "If Condition 2 is true (the current temperature T is outside the trigger band
  but within the overtemperature bounds), then the Determine H/C Mode process shall output an
  H/C Request to turn on the heating unit **in case LO <= T < LT**."
- v2 round 1: "If Condition 2 **(LO <= T < LT or UT < T <= UO)** is true, then the Determine
  H/C Mode process shall output an H/C Request to turn on the heating unit **in case
  LO = T_LT**."

Two differences, and they cut against v2, not for it:

1. **v1 repaired the corrupted clause; v2 did not.** v1's tail reads `LO <= T < LT`; v2 left
   the source's mangled `LO = T_LT` intact (see Known Limitation 5 on that corruption).
2. **v2 introduced a real incompleteness.** It inlined *both* branches of Condition 2
   (`LO <= T < LT` heating, `UT < T <= UO` cooling) into the condition, while the action still
   covers only the heating unit. The cooling branch now has a stated trigger and no stated
   behaviour. The `INCOMPLETE` flag in rounds 2 and 3 is therefore **correct**, not checker
   noise.

An earlier reading of this comparison — that v2 produced the better requirement and merely
suffered the worse label — was wrong, and the round texts refute it. The `COMPLETED` ->
`CAP_STOPPED` flip reflects a genuine defect in v2's rewrite, compounded by the replay
fallback firing because the frozen transcript has no answer for an `INCOMPLETE` question that
never arose under v1.

The general point still stands and is worth keeping separate from this instance: outcome
labels alone cannot rank two runs, because a rewrite can clear one category and create
another. Report final text quality alongside outcomes. Here, doing that happens to confirm the
label rather than contradict it.

### Future work, adjacent to Limitation 11 — cite a standard instead of inventing a threshold (proposed 2026-08-15, not implemented)

**Status: documentation only.** No prompt, schema, or orchestrator change accompanies this
entry. Recorded so the idea and its evidence exist in one place before anyone is tempted to
build it mid-evaluation.

**The proposal.** When the Refiner cannot get a concrete value from the answerer, it should
rewrite the requirement to name a **measurable property** and its citable **source standard**,
while leaving the target value explicitly unset. Example shape:

> "Usability shall be measured using the System Usability Scale, per the Interaction
> Capability characteristic of ISO/IEC 25010:2023. Target score not specified in the source
> document; to be set by the project."

This is a stronger claim than "the pipeline fills in values": the pipeline converts an
*unmeasurable* requirement into a *measurable* one with a cited metric, without ever
supplying the number itself. Record it that way, not as a value-filling feature.

**Why it was proposed — the evidence already exists in the repo.** The 2026-08-14
refiner-answerer pilot (n=3, `pure-gamma-j`;
`docs/superpowers/results/2026-08-14-refiner-answerer-pilot/RESULTS.md`) measured an LLM
answerer inventing acceptance thresholds — SUS ≥ 70, 5/10/15-minute windows — that read as
normal professional criteria with no tell, and flipped all three requirements from
`cap_generated` to `completed`. That is fabrication, not refinement: the pipeline accepted
invented precision as if it were real. This proposal converts that exact failure mode into a
citation instead of a number: state *what* to measure and *where the definition comes from*,
and never invent *how much*.

**Why it is not being done now.** It changes the system under test mid-evaluation. Every prior
run (the behaviour scenario suite, the live-answer session, the v1/v2 prompt comparison, this
pilot itself) would become non-comparable against a run using this rewrite behaviour, and it
would need a prompt v3 with its own re-runs across all of them to re-establish comparability.
That cost is not justified by an n=3 pilot. This is a deferral, recorded as a decision, not an
oversight.

**The verified reference table.** Below is `STANDARDS_REFERENCE`, verified 2026-08-15 against
the ISO/IEC 25010:2023 sample text and an independent breakdown (two sources, in agreement).
Reproduced verbatim — do not regenerate this from model memory and do not add entries beyond
what is here. That is the exact failure this entry exists to prevent: an earlier draft in
conversation cited "usability per ISO 25010," a characteristic name that does not exist in the
2023 model.

```python
# Verified 2026-08-15 against ISO/IEC 25010:2023 (ISO sample text + an independent
# breakdown, two sources in agreement). Sub-characteristic names are the 2023 ones.
#
# VERSION MISMATCH, state it wherever this is cited: ISO/IEC 25023:2016 supplies the
# measures but normatively references ISO/IEC 25010:2011, so its measures sit under the
# OLD names "Usability" and "Portability". Map 2023 -> 2016 explicitly:
#   Interaction Capability (2023)  ==  Usability (2011/25023)
#   Flexibility (2023)             ==  Portability (2011/25023)
#
# NO thresholds anywhere. The standards define characteristics and measurement
# functions; they do not set target values. That is the project's decision.

STANDARDS_REFERENCE = {
    "ease_of_use": {
        "triggers": ["easy to use", "user-friendly", "intuitive", "simple to use"],
        "characteristic": "Interaction Capability > operability (ISO/IEC 25010:2023)",
        "measure": "effectiveness, efficiency and satisfaction in a specified context "
                   "of use (ISO 9241-11:2018)",
        "instrument": "System Usability Scale — an instrument, not a standard",
        "threshold": None,
    },
    "learnability": {
        "triggers": ["easy to learn", "quick to pick up", "minimal training"],
        "characteristic": "Interaction Capability > learnability (ISO/IEC 25010:2023)",
        "measure": "learnability measures, ISO/IEC 25023:2016 §8.5.2 (filed under Usability)",
        "threshold": None,
    },
    "speed": {
        "triggers": ["fast", "quick", "responsive", "without delay"],
        "characteristic": "Performance Efficiency > time behaviour (ISO/IEC 25010:2023)",
        "measure": "time behaviour measures, ISO/IEC 25023:2016 §8.3.1",
        "threshold": None,
    },
    "availability": {
        "triggers": ["always available", "uptime", "24/7"],
        "characteristic": "Reliability > availability (ISO/IEC 25010:2023)",
        "measure": "availability ratio over a stated period",
        "threshold": None,
    },
    "reliability": {
        "triggers": ["reliable", "stable", "shall not crash"],
        "characteristic": "Reliability > faultlessness, fault tolerance, recoverability",
        "measure": "state which of the three is meant; each is measured separately",
        "threshold": None,
    },
    "security": {
        "triggers": ["secure", "protected", "safe from attack"],
        "characteristic": "Security > confidentiality, integrity, non-repudiation, "
                          "accountability, authenticity, resistance",
        "measure": "state which property is meant; each is measured separately",
        "threshold": None,
    },
    "scalability": {
        "triggers": ["scalable", "handles growth", "supports more users"],
        "characteristic": "Flexibility > scalability (ISO/IEC 25010:2023, new in 2023)",
        "measure": "state the load level and the metric that must hold at it",
        "threshold": None,
    },
    "portability": {
        "triggers": ["portable", "runs anywhere", "easy to install"],
        "characteristic": "Flexibility > adaptability, installability, replaceability",
        "measure": "ISO/IEC 25023:2016 portability measures",
        "threshold": None,
    },
    "maintainability": {
        "triggers": ["maintainable", "easy to change", "easy to update"],
        "characteristic": "Maintainability > modularity, reusability, analysability, "
                          "modifiability, testability",
        "measure": "ISO/IEC 25023:2016 maintainability measures",
        "threshold": None,
    },
    "accessibility": {
        "triggers": ["accessible", "usable by all", "disability"],
        "characteristic": "Interaction Capability > inclusivity, user assistance "
                          "(2023 decomposition of the former 'accessibility')",
        "measure": "for web interfaces, WCAG 2.2 Level AA — current W3C Recommendation. "
                   "WCAG 3.0 is a Working Draft as of March 2026, not citable as a standard.",
        "threshold": None,
    },
    "safety": {
        "triggers": ["safe", "shall not cause harm"],
        "characteristic": "Safety > operational constraint, risk identification, "
                          "fail safe, hazard warning, safe integration (new in 2023)",
        "measure": "state which is meant",
        "threshold": None,
    },
}
```

**Three caveats that must travel with this table wherever it is cited:**

1. **Version mismatch.** ISO/IEC 25023:2016 supplies the measures but normatively references
   ISO/IEC 25010:2011, so its measures are filed under the old names "Usability" and
   "Portability", which do not exist in the 2023 model. Any citation chain must state the
   mapping: Interaction Capability (2023) == Usability (2011/25023); Flexibility (2023) ==
   Portability (2011/25023).
2. **Thresholds are never supplied.** These standards define characteristics and measurement
   functions, not target values. The proposal's whole defensibility rests on the Refiner never
   filling one in — the moment it does, it has reproduced Known Limitation 11 with a citation
   attached instead of fixing it.
3. **SUS benchmark unverified.** The commonly quoted average of 68 comes from Jeff Sauro's own
   analysis of 500+ studies, with no formal published citation on the source page. Mark it
   unverified. It must not enter the thesis without a peer-reviewed source, or SUS should be
   cited as an instrument with no number.

**Separate threat-to-validity note, prompted by the same pilot.** On a third-party corpus like
PURE there is no stakeholder who can answer the Refiner's questions — `pure-gamma-j`'s authors
are unreachable, so any answerer who commits a value is inventing it, human or LLM alike.
Consequence: "does a human in the loop help?" is not answerable on PURE. The banked 2026-08-14
live-answer comparison (`docs/superpowers/results/2026-08-14-live-answers/`) measures "does
*supplying* values help" — a weaker claim — and should be described that way in the evaluation
plan rather than as evidence about human input specifically.

## `RequirementSet.requirements` min_length fix (2026-08-05)

`RequirementSet.requirements` had no `min_length`, so a zero-requirement set could be
constructed and would silently flow through every downstream stage doing nothing
useful. Resolved: a `RequirementSet` must always start with at least one requirement
-- there's no legitimate use case for constructing it empty and building it up from
zero. It's still free to grow after construction (`requirements.append(...)` isn't
blocked -- `min_length` is a construction/validation-time check, not a standing
invariant Pydantic re-checks on mutation), which matches the intended usage: always
start non-empty, allowed to add more later. Fixed with `Field(..., min_length=1)`.
Tested: empty construction now rejected, append-after-construction still works, full
`RequirementRunRecord` round trip still holds.

## `RefinedRequirement.answers_used` min_length fix (2026-08-05)

`RefinedRequirement.answers_used` defaulted to `[]` with no `min_length`, so a
`RefinedRequirement` could be constructed with no traceable justification for why
`refined_text` differs from `original_text`. Per the pipeline docstring, a
`RefinedRequirement` only ever comes from folding `RefinerAnswer[]` into the text --
confirmed there's no intended auto-fix or no-op-revision path that bypasses the human
Q&A loop, so an empty `answers_used` is never legitimate. Fixed with
`Field(..., min_length=1)`. Tested: empty construction now rejected, a populated
`answers_used` still works, full `RequirementRunRecord` round trip still holds. This
closes out all three judgment calls raised in the 2026-08-05 verification pass.

## Stages 3/4 take `Requirement`, not `RefinedRequirement` (2026-08-05)

**The gap.** The pipeline docstring declared stages 3 and 4 as consuming a
`RefinedRequirement`. But `RefinedRequirement` only exists on the refined path — a
requirement that passes the Quality Checker on the first try correctly skips the
Refiner and never produces one. Worked example: `THEMAS-REQ-G` ("Each thermostat
shall have a unique identifier by which that thermostat is identified in the THEMAS
system") is atomic, verifiable, and carries no vague pronoun, so `QualityReport.passed`
is `True`, `issues` is empty, and the Refiner never runs. There was then nothing of
the declared type to hand to the Test Design Strategy Selector.

This became strictly unfixable-in-place with the `answers_used` `min_length=1` fix
earlier the same day: a no-op pass-through `RefinedRequirement` (original text ==
refined text, no answers) can no longer even be constructed. Verified — it raises
`ValidationError: List should have at least 1 item`. That fix was correct on its own
terms; the mistaken part was the accompanying claim that "there's no intended
no-op-revision path," which read the *absence of a carrier* as evidence that the clean
path didn't need one.

**Important distinction:** this was never a control-flow bug. Clean requirements
skipping the Refiner is the intended and correct behaviour, and is unchanged. The bug
was purely in the declared data type flowing *into* stage 3 — the skip path had no
carrier.

**Fix.** Stages 3 and 4 now take a plain `Requirement`. Both paths converge on that
type: the clean path passes the original object through untouched, and the refined
path has the orchestrator build `Requirement(id=req.id, text=refined.refined_text,
source_doc_id=req.source_doc_id)`. Neither stage needs to know which branch ran. See
the module docstring in `schemas.py` for the orchestrator sketch.

`RefinedRequirement` is unchanged structurally but is now explicitly scoped as an
*audit record* — what changed, and which answers drove the change — rather than the
pipeline's transport type. That is the role it was already good at; it was
additionally being asked to be the carrier, which is what broke. This also retroactively
justifies `answers_used=min_length=1`: the model is only ever constructed when a human
Q&A round genuinely happened.

**Rejected alternative:** leave the schema alone and put
`rec.final_requirement.refined_text if rec.final_requirement else rec.requirement.text`
in the orchestrator. It works, and for a single-orchestrator project it is close to
adequate. Rejected because that branch isn't needed once — stages 3 and 4, the JSON
dump, the evaluation scripts, and the planned FastAPI layer each need it, and a
consumer that forgets it doesn't crash. It silently generates test cases from the
*original, defective* text, which is precisely the outcome this pipeline exists to
prevent, and produces output that looks entirely plausible on inspection. A silent
wrong answer beats a loud one only for whoever isn't reading the results.

**`RequirementRunRecord.final_text` added** as a Pydantic `@computed_field` rather
than a stored field, which writes that branch exactly once. Stored would have
denormalised (the same string living in two places, free to drift); a plain
`@property` would have been invisible to `.model_dump_json()` and so absent from the
records that double as the evaluation dataset. `@computed_field` gives both: single
definition, and it serialises. It is read-only and constructor-ignored by
construction, so it cannot desync from `final_requirement`.

**Known boundary, not fixed here:** `final_requirement is None` currently means "was
clean." Once run-outcome states are added (see the record-restructure items), it will
*also* mean "a stage errored" and "revision cap reached with issues outstanding," and
`final_text` would report the original text for all of them identically. Preventing
stage 3 from running at all in those states is orchestrator logic, deliberately not
pushed into this property.

Tested: clean path (no `RefinedRequirement`) and refined path both construct, both
report the correct `final_text`, both survive a full JSON round trip, and `final_text`
appears in the dumped JSON.

## Run outcome and stage failures (2026-08-05)

**The gap.** `RequirementRunRecord` could only describe a run that went perfectly. Two
symptoms, one fix.

*Symptom A — a stage errors and the whole record is lost.* `classification` was a
required field, so a requirement whose Classifier call failed could not be persisted at
all (verified: `ValidationError: classification — Field required`). The only options
were to silently drop the requirement or crash the document. On a free-tier API this is
not an edge case: Gemini's free tier is 15 requests/minute, so rate-limit failures are
the normal operating condition for any run over a document of real size.

*Symptom B — the refinement loop gives up and nothing says so.* When the orchestrator's
revision cap fires with issues outstanding, the resulting record was structurally
identical to one that converged cleanly. Consequence: "N% of requirements converged
within 3 rounds" — a headline number for a refinement chapter — was not computable from
the pipeline's own output.

**Fix.** `RunOutcome` (five states), `StageError`, and `PipelineStage` added;
`classification` and all downstream stage outputs are now `Optional`, with a
`@model_validator` enforcing that the outcome label matches what the record actually
holds. Same intent as `QualityReport._passed_matches_issues`: reject a contradictory
record where it is created, rather than discovering it downstream.

`PipelineStage` is an enum rather than a free string so "which stage fails most often"
stays countable — `"classifier"` vs `"Classifier"` would silently split the tally. It
covers per-requirement stages only; Consistency Checker and Dependency Mapper failures
are document-level and have nowhere to live until the document record exists (gap 3).

**Decision D1 — behaviour at the revision cap: ask the human per requirement.**
Considered: (a) generate tests anyway from the best-effort text, flagged;
(b) stop, produce no test plan; (c) ask the human each time. (c) chosen, as the most
consistent with the pipeline's human-in-the-loop philosophy.

The objection to (c) was reproducibility: a human answering differently on two runs of
the same document produces different output with nothing recording why. That is
answered by making the decision *data* rather than orchestrator behaviour —
`CAP_REACHED` is therefore split into two terminal states, `CAP_GENERATED` and
`CAP_STOPPED`, with an optional free-text `cap_reason`. The validator ties each to its
evidence: `CAP_GENERATED` requires a `test_plan`, `CAP_STOPPED` forbids one.

**Threat to validity, to state explicitly in the write-up:** under (c) the pipeline is
not deterministic across runs, because a human judgement call sits inside it. The
recorded outcome and `cap_reason` make each individual run auditable and let capped
requirements be filtered out of any analysis, but they do not make two runs comparable
without accounting for the operator. Anyone reporting aggregate numbers over a document
containing capped requirements needs to say so.

**Why the cap question does not reuse the `RefinerTurn`/`RefinerAnswer` pattern**
(symmetry audit, checklist lens 2): the Refiner needs that machinery because its
questions are LLM-phrased, plural, and tied to specific `Issue` ids. The cap question
is a single fixed binary prompt with no LLM involvement and no issue to trace. Reusing
the request/response models would add structure carrying no information, so the answer
is recorded directly in the outcome instead. The asymmetry is intentional.

**Decision D2 — resumability: yes.** `IN_PROGRESS` is the default outcome, so a record
exists from the moment a requirement is picked up and can be written out incrementally.
No `NOT_STARTED` state is needed — a requirement in the `RequirementSet` with no record
has not been started. This also happens to be what makes Symptom A's bare record legal.

No `last_completed_stage` field was added: resume position is fully derivable from
which fields are populated (`classification is None` → resume at the Classifier, last
`QualityReport` not passed → resume in the Refiner, and so on). LLM calls are atomic
from the pipeline's point of view — either a parsed object came back or it did not — so
there is no half-finished stage to record. A `resume_stage` computed field was
considered and rejected: it would bake pipeline *ordering* into the schema, which is
exactly the coupling avoided elsewhere (and the reason `pipeline.mermaid` is declared by
hand rather than introspected). Resume order belongs to the orchestrator.

**`used_human_override` is a computed field, not a `RunOutcome` value.** The original
audit proposed `HUMAN_OVERRIDE` as an outcome state; that was wrong. A run can be both
*completed* and *human-overridden* — two independent axes an enum would force a choice
between. It is also fully derivable (`any(a.user_confirms_resolved for a in
refiner_answers)`), so as a computed field it cannot drift. Same reasoning as
`final_text`.

**Caught while testing this change:** a `COMPLETED` record with an empty
`quality_reports` list validated cleanly — asserting a requirement had passed checks
that never ran. Any terminal non-error outcome now requires at least one
`QualityReport`, since "converged" and "hit the cap" are both claims about quality
checks having happened.

### `StageError.retry_count` (2026-08-05)

Added `retry_count: int = Field(0, ge=0)` — retries attempted before giving up and
recording the error, so `0` means "failed on the first attempt with no retry."

Justified by the tech stack rather than added speculatively: on free-tier Gemini/Groq,
retry-with-backoff is the normal path, not an exception, and "did retrying actually
rescue runs, or just delay failures?" is otherwise unanswerable from the records.

**Read it carefully when reporting:** a `StageError` only exists for a call that
*ultimately failed*. Retries that succeeded leave no trace anywhere in the schema, so
this field measures "how hard we tried before losing," not retry effectiveness overall.
Computing a success rate from it would be wrong — the denominator isn't here. If
retry effectiveness turns out to matter, it needs a counter on successful calls too,
which is a bigger change and not made on speculation.

### Validator rewrite — the first version under-checked eight contradictions (2026-08-05)

The first `_outcome_matches_contents` was hand-written conditionals, and a review found
it accepted eight contradictory records. All eight were reproduced before fixing:

| # | Accepted but incoherent | Why it matters |
|---|---|---|
| 1 | `COMPLETED` whose last `QualityReport` has `passed=False` | Only non-emptiness was checked, never the *state*. A requirement that never passed was recorded as having converged. |
| 2 | `CAP_GENERATED` with `classification=None` | A cap is unreachable without the Classifier having run. |
| 3 | **`CAP_GENERATED` with `final_requirement=None`** | The worst one. `final_text` silently falls back to the *original, unrefined* text — so a record asserting "tests generated from best-effort refined text" actually carries tests generated from the defective original. Exactly the silent-wrong-answer failure this file warns about in the stages 3/4 section. |
| 4 | `CAP_STOPPED` with `classification=None` | Same as #2 on the other branch (one fix, not two — the check lived only in the `COMPLETED` branch). |
| 5 | Cap outcomes with empty `refiner_turns` / `refiner_answers` | A cap is only reachable after at least one refinement round. |
| 6 | `cap_reason` on `COMPLETED`; absent on either cap outcome | Discards the audit trail that decision D1=C was chosen to capture, and lets a non-cap record claim a cap reason. |
| 7 | *(this file)* "all six contradictory combinations rejected" | Inaccurate as written — it implied exhaustiveness the test never had. Corrected below. |
| 8 | Cap outcome whose last `QualityReport` has `passed=True` | Found while fixing the others: if the last check passed, no cap was hit. Mirror image of #1. |

**Root cause, and why the fix is structural rather than more conditionals.** A chain of
`if` statements checks what its author happened to think of, and nothing about the code
reveals which combinations were forgotten. The rules are now a declarative table,
`_OUTCOME_RULES`, mapping each `RunOutcome` to `required` / `forbidden` / `non_empty`
fields plus a required `last_report_passed` state. The validator is a short generic
loop over that table. This makes the rule set *readable off the page* and, more
usefully, lets the test suite enumerate itself: it walks every rule of every outcome and
violates them one at a time, so coverage grows automatically whenever a rule is added
rather than depending on someone remembering to write a matching test.

Two rules are deliberately loose and worth stating so they don't look like oversights.
`COMPLETED` does **not** require `final_requirement` — a requirement that was clean on
the first pass legitimately has none. `ERROR` constrains almost nothing and explicitly
permits `cap_reason`, because a stage can fail anywhere, including after the human has
already chosen "generate anyway" and the Test Case Generator then hits a rate limit.

`CAP_STOPPED` forbids `test_strategy` as well as `test_plan`: the human is asked before
stage 3, so neither stage runs.

`cap_reason` carries `min_length=1` so `""` cannot satisfy the requirement vacuously.
**Known limitation:** requiring free text guarantees a field is filled, not that it says
anything useful — a human under time pressure can type "n/a". The schema cannot check
that, and no design can. It is still worth requiring, since an absent reason is
unrecoverable while a poor one is at least visible in the record.

Tested: one valid record per outcome constructs; the enumerated matrix generates 30
contradictory records (every rule of every outcome, violated one at a time) and all 30
are rejected with specific messages; all eight bugs above re-verified as fixed;
`final_text` on a valid `CAP_GENERATED` now returns the refined text; empty
`cap_reason` rejected; resume position derives correctly at three points; `outcome`,
`error`, `used_human_override` and `final_text` all appear in the dumped JSON; round
trip holds.

## Document-level run record (2026-08-05)

**The gap.** The Consistency Checker and Dependency Mapper run once per document, and
nothing in the schema could hold what they produced. Both objects existed for one
function call and were then dropped. Four consequences:

1. **Two of the seven modules were unevaluable.** Measuring the Consistency Checker
   against a gold standard requires its output to be saved; it wasn't.
2. **Document-level failures were homeless.** Gap 2 gave per-requirement failures a
   home, but `PipelineStage` deliberately excludes document stages.
3. **Decision D2 (resumability) had nothing to resume into** — records need a container
   to be written into incrementally.
4. Provenance (gap 4) is per-run, i.e. document-level, and would have had nowhere to go.

**Decision D1 — if a document-level stage fails: proceed, flagged (option b).**
Considered: (a) abort the document, (b) continue with that report absent and record the
degradation, (c) ask the human. (b) chosen: on a free tier, (a) means routinely losing
whole documents to one rate-limit blip, and the failure is still fully auditable in the
output. **Consequence to respect when analysing results:** a `DEGRADED` document's
requirements were quality-checked without inherited consistency flags, or had stages
3/4 run without dependency context. They are not comparable with requirements from a
`COMPLETED` document and should be excluded from consistency- or dependency-related
analysis rather than pooled.

**A failure mode `DEGRADED` cannot catch.** The flag covers a document-level stage that
*failed*. It says nothing about one that succeeded badly. The Consistency Checker takes
the whole document in one call, and with a 1M-token context a full SRS fits — so on a
large document the call will not error or truncate, it will return `conflicts: []` and
validate cleanly. No schema check can distinguish that from a genuinely conflict-free
document. Every document tested so far is tiny (largest: 8 requirements, ~274 tokens),
so the whole-document assumption is currently untested rather than confirmed. A planned
experiment to measure it — recall of a planted conflict against document size — is
written up in `datasets/EVALUATION_DATASETS.md`, along with why chunking would be the
wrong fix if the curve does drop off. Not addressed in the schema, because there is
nothing to fix until the measurement exists.

**Decision D2 — storage: document file plus one file per requirement (option b).**

    <run_dir>/document.json                   <- DocumentRunRecord, requirement_records=[]
    <run_dir>/requirements/THEMAS-REQ-A.json  <- one RequirementRunRecord each

This forced a design point worth stating: the persisted document file carries an
**empty** `requirement_records` list, so nothing may require that list to be non-empty
— not even on a `COMPLETED` document — or the on-disk file would not validate against
its own schema. Verified both directions (write with empty list, reassemble with
records). It also means `Requirement` text is duplicated between `requirement_set` and
each requirement file. That duplication is intentional: it keeps each requirement file
self-contained and independently readable.

**`DocumentOutcome` is a separate enum from `RunOutcome`, and describes less.** It
covers only the document-level stage phase, which finishes before per-requirement
processing begins. It deliberately says nothing about whether every requirement has
been processed — that is a second, independent axis (the same mistake `HUMAN_OVERRIDE`
would have been on `RunOutcome`) and is derivable anyway via the computed
`pending_requirement_ids`, which also drives D2's resume logic. Deriving it rather than
storing it means it cannot disagree with the records actually present, which is exactly
what matters when recovering an interrupted run.

**`errors` is a list, not a single error** (cardinality audit, checklist lens 1): the
two document-level stages run independently, so both can fail in the same run. Tested.

**`DocumentStage` / `DocumentStageError` are separate from `PipelineStage` /
`StageError` rather than unified.** A `PipelineStage | DocumentStage` union, or one
shared enum, would let a `RequirementRunRecord` record an error naming
`consistency_checker` — the exact nonsense the split prevents. Two duplicated fields
(`message`, `retry_count`) is a cheaper price than that hole, and a shared base class
was rejected as adding an inheritance concept to save two lines. Verified both
directions: `StageError` rejects a `DocumentStage` and vice versa.

**Rejected: storing `detected_cycles`.** An earlier sketch proposed persisting
`find_cycles()` output alongside the dependency report, arguing recomputation might not
reproduce the same cycle representatives. That was wrong, and checking it took a minute:
`find_cycles()` is deterministic, and rebuilding a `DependencyReport` from its own saved
JSON returns identical output. (Reordering the *links* does change which node a cycle is
reported from, but the saved order is fixed.) A stored copy would be pure redundancy
with a chance to drift, so it isn't stored.

**Validator reuse rather than a parallel implementation.** `_apply_outcome_rule` was
extracted from `RequirementRunRecord` and is now shared by both records, each with its
own rule table. Cross-field checks that a table row cannot express stay in each
record's validator: errors-present-implies-`DEGRADED`, a failed stage not also having
produced a report, and `requirement_records` only containing ids present in
`requirement_set`.

Tested: all four document states construct; the self-enumerating matrix covers the
document table rules; all five cross-field contradictions rejected with specific
messages; both enum-confusion directions rejected; on-disk empty-list layout validates
and reassembles; `pending_requirement_ids` correct at three points; round trip holds;
the requirement-level matrix is unchanged at 30/30 (no regression).

### Follow-up review — four more holes (2026-08-05)

A review of the above found two gaps; fixing them exposed two more. All four reproduced
before changing anything.

**1. `DEGRADED` didn't require the *non-failed* stage's report.** `errors=[consistency]`
with `dependency_report=None` validated cleanly, making "the Dependency Mapper succeeded
and was recorded" indistinguishable from "the Dependency Mapper's result was never
saved" — very different situations when interpreting results.

The root cause was a rule written one step earlier: *errors present implies `DEGRADED`*.
Document-level stages run in sequence, so "the Consistency Checker failed, the
Dependency Mapper hasn't run yet" is a real intermediate state — and under decision D2b
it gets written to disk. That rule forced `DEGRADED` at that moment, making `DEGRADED`
reachable *before the phase was over*, so it could not also mean "phase finished." One
label covering both a mid-run and a terminal state is what allowed the missing report to
look legitimate.

Fixed by inverting which state carries the constraint. `IN_PROGRESS` may now hold errors
(that is the mid-run state), and both terminal outcomes require every stage to be
**accounted for** — each either recorded as failed or having filed its report.
`COMPLETED` gets this from its `required` row; `DEGRADED` gets an explicit check.
`COMPLETED` still may not carry errors.

**2. Duplicate `requirement_records` for the same id.** Two records for `REQ-1`
validated. Resume was unaffected (`pending_requirement_ids` dedupes via a set), but the
persisted document doubles as the evaluation dataset, where a duplicated requirement
silently double-counts in any aggregate.

**3. The same document stage failing twice** (`errors=[consistency, consistency]`).
Retries are already counted in `retry_count`; a repeated entry would double-count that
stage in failure tallies. Each stage now may appear at most once in `errors`.

**4. A requirement record drifting from the set.** The `Requirement` duplication between
`requirement_set` and each record file is deliberate (D2b keeps requirement files
self-contained), and this is the second way that duplication can go wrong: a record can
name `THEMAS-REQ-B` while carrying entirely different text. Records must now equal the
requirement the set declares. **Consequence worth knowing:** editing a `RequirementSet`
and then loading requirement files written against the older version will now fail
loudly instead of silently mixing two document versions. That is the intended behaviour,
but it will look like a bug the first time it happens.

Tested: both reported holes and both follow-ons rejected with specific messages; the
mid-run `IN_PROGRESS`-with-errors state now writable; both-stages-failed still valid
(nothing left to require); all previously passing cases still pass; requirement-level
matrix unchanged at 30/30.

#### Known nuisance: the drift check also rejects cosmetic differences

The drift check (#4 above) compares `Requirement` objects exactly. Tested behaviour:

| Difference between record and set | Result |
|---|---|
| none | accepted |
| trailing space | rejected |
| newline instead of a space | rejected |
| `source_doc_id` present on one side only | rejected |
| genuinely different text | rejected (intended) |

Only the last row is a real change; the middle three are false positives. The likely
trigger is re-parsing `datasets/requirements-xml` with a parser that wraps lines or
trims whitespace differently — old requirement files would then stop loading despite
nothing meaningful having changed.

Left strict deliberately. It fires only when *loading* records for analysis, never
during a run, so it cannot lose work; the message names the offending requirement; and
in the case that matters (a requirement's text was genuinely edited) refusing to load is
correct, since those results came from a different input.

If it becomes a nuisance, the fix is to compare `" ".join(text.split())` instead of raw
text — collapsing all whitespace differences while still catching real edits. Not done
pre-emptively, since whitespace-only differences in requirement text are semantically
meaningless and may never occur here. `source_doc_id` is the likeliest tripwire, being
optional and easy to omit when building a record by hand; the narrower fix there is to
compare only `id` and `text`.

## Run provenance (2026-08-05)

**The gap.** Nothing recorded what produced a run. Running THEMAS on Gemini and again
on Groq produced two sets of files with no way to tell them apart, and any prompt edit
left a before/after comparison with no "before" label. These records are the evaluation
dataset, so an unattributable run is an unusable one.

**Fix.** `RunMetadata` on `DocumentRunRecord`, required rather than optional — the full
config is known before a run starts, so there is no point at which it would legitimately
be missing.

**Per-stage model config.** `stages: dict[str, StageConfig]`, because the plan is to
experiment with mixed setups (a cheap model for classification, a stronger one for test
generation). A validator requires the dict to cover exactly `ALL_STAGES` — every
document-level and per-requirement stage — so "which model ran the Classifier?" always
has an answer, and a typo'd stage name is rejected rather than silently ignored.

**Model and prompt grouped in one `StageConfig`, not two parallel dicts.** Two dicts
(`models` and `prompt_hashes`) could end up with different key sets and disagree about
which stages exist. One dict of small objects cannot.

**Prompt identity is hashed, not just labelled.** `prompt_version` is a human label you
read; `StageConfig.prompt_hash` is a 12-character SHA-256 prefix of the prompt's actual
text, via `prompt_fingerprint()`. The label is what makes results readable; the hash is
what makes them trustworthy. Verified: two runs both labelled `"v3"`, one with an edited
Quality Checker prompt, differ visibly at exactly that stage's hash. Without it, a
forgotten version bump would silently mislabel a run and no later inspection could
recover the truth — the same "make forgetting visible" principle as the outcome rule
table.

Deliberately not stored: the prompt *text*. If the exact wording needs recovering later,
save each unique prompt once as `prompts/<hash>.txt` — the records already carry the
key. Not built now.

**`temperature` is a single run-level value defaulting to 1.0** (matching the Gemini and
Groq API defaults), not per-stage. If per-stage temperature is ever needed it belongs in
`StageConfig` alongside model and prompt, not as a second parallel mapping.

**No `provider` field.** Model identifiers already distinguish them in practice
(`gemini-2.0-flash` vs `llama-3.3-70b-versatile`). If that ever becomes ambiguous,
prefix the model string (`google/gemini-2.0-flash`) rather than adding a field that must
be kept consistent with it.

Tested: fingerprint stable across calls and changes on a one-character edit; mixed-model
config accepted; missing and unknown stage names rejected with specific messages;
`metadata` required on `DocumentRunRecord`; mislabelled-run detection demonstrated;
metadata present in the dumped JSON; round trip holds.

## External review — five more cross-field holes (2026-08-05)

An independent review searched specifically for the failure pattern this file has
produced three times already (two fields that must agree, with nothing enforcing it) and
found five more. All five reproduced before fixing.

**1. `RefinedRequirement.answers_used` could disagree with `refiner_answers`.** One
record could hold two different `RefinerAnswer` objects with the same `question_id` and
different `answer_text` — disagreeing about what the human actually said. Identical in
kind to the `requirement_records` / `requirement_set` drift check, one level down.

*Fixed as a subset check, not equality.* `answers_used` is legitimately a **subset** of
`refiner_answers`: it holds the answers behind the final rewrite, while `refiner_answers`
logs every round. Requiring equality would wrongly reject any multi-round refinement.
Membership is tested by whole-object comparison, which is what catches the same question
carrying two different answers.

**2. `RefinerAnswer.question_id` could name a question that was never asked.** The
traceability chain `Issue → ClarifyingQuestion.issue_id → RefinerAnswer.question_id →
RefinedRequirement.answers_used` had every link individually typed and no link checked.
Every answer must now match a question in `refiner_turns`.

Also added while fixing: question ids must be unique within a record. Without it, "the
question this answers" is ambiguous when two turns reuse an id — which would defeat the
point of tracing by id at all. This is a partial down-payment on gap 6 (`Issue.id`
stability), which has the same root cause one level up.

**3. `INCONSISTENT` / `CIRCULAR_DEPENDENCY` accepted an empty
`related_requirement_ids`.** Both categories are claims *about other requirements*, so
either without a counterpart is a relationship with nothing on the other end — and
unactionable for the Refiner, which would have nothing concrete to ask about. The
field's description already said "Set for INCONSISTENT/CIRCULAR_DEPENDENCY"; that intent
is now enforced rather than documented.

**4. `RunMetadata.temperature` was unbounded** — `-5.0` and `999.0` both validated,
while `retry_count` a few lines away had `ge=0`. Now `ge=0.0, le=2.0`, the range both
the Gemini and Groq APIs accept.

**5. `RunMetadata` lived only on `DocumentRunRecord`.** The reviewer framed this as a
tension between D2b ("requirement files are self-contained and independently readable")
and gap 4's purpose ("a record without provenance cannot be attributed afterwards") —
open `requirements/THEMAS-REQ-A.json` alone and you cannot answer the question
provenance exists to answer.

Checking it surfaced something worse than un-attributability: **records from a different
run could be assembled into a document with nothing detecting it** — precisely the
failure the per-file layout makes possible.

Resolved with `RequirementRunRecord.run_id`, required. Not the full `RunMetadata`, which
would repeat seven stage configs per requirement; the id is enough to locate the
provenance, and `DocumentRunRecord` now checks every record's `run_id` matches its own
`metadata.run_id`. The pointer earns its place by enabling that check, not merely by
documenting the link.

**Caught by the new checks: an invalid fixture in the test suite itself.** The
`used_human_override` test built a record with a `RefinerAnswer` and no `refiner_turns`
— an answer to a question never asked, exactly the shape check 2 rejects. The fixture
was wrong, not the check.

Tested: all five reproduced then rejected; multi-round subset case still accepted; a
7-mutation run (deleting each new check plus one older rule) is caught by the suite.
Suite now at 138 checks.

## `COMPLETED` after refinement required no rewrite (2026-08-05)

Found by a second external review pass. Reproduced before fixing.

A `COMPLETED` record with `quality_reports=[failed, passed]` — a genuine
refine-then-pass cycle, with matching `refiner_turns` and `refiner_answers` — validated
with `final_requirement=None`. `final_text` then reported the *original, unrefined* text
as the text stages 3/4 ran on, on a record whose own history proves it was rewritten.

Same silent fallback already blocked on `CAP_GENERATED` and `CAP_STOPPED`; it simply
never got extended to `COMPLETED`. The old comment — "`final_requirement` deliberately
NOT required, a requirement that was clean first time legitimately has none" — was true
only while nothing had refined it, and nothing checked that condition.

**Fixed with an explicit check, not a table row.** `_OutcomeRule` expresses static
presence/absence; this rule is conditional on *another field's contents*, so it lives in
`_refinement_is_recorded` alongside the `last_report_passed` special case, for the same
reason.

**Broader condition than the review proposed.** The suggestion was
`len(quality_reports) > 1`. Testing found a second accepted case that misses: refiner
activity with only *one* passing report — the Refiner ran, yet nothing was rewritten and
the single check passed. Both are now signals that refinement occurred.

**`refiner_answers` deliberately excluded as a third signal.** `_qa_chain_is_traceable`
already rejects an answer with no matching question, so answers cannot appear without
turns — the branch would be unreachable for any otherwise-valid record, hence untestable
in isolation and therefore untested. Dropped rather than kept as dead defensive code.

**Mutation testing found a weakness in the tests, not the schema.** The first fixtures
tripped several refinement signals at once, so deleting any single signal left the suite
green. Each signal now has an isolated fixture. Full mutation run: **15 deliberate rule
deletions, 15 caught.** Suite at 144 checks.

Worth recording as a method note: the enumerated tests verify that rules are *enforced*,
the anchor tests verify that rules *exist*, and mutation runs verify the tests actually
discriminate. The third caught things the first two could not — twice now (rule deletion
going unnoticed, then multi-signal fixtures masking single-signal deletion).

## Refinement trajectory: `rounds` replaces three parallel lists (2026-08-05)

**The gap.** `quality_reports`, `refiner_turns` and `refiner_answers` were three flat
lists linked only by position and by chasing ids. Three questions were unanswerable:
which text version each check ran on (`QualityReport` had no revision number); which
answers belonged to which round (you had to take each `question_id` and scan every turn);
and what the text looked like after an intermediate round (discarded — only the final
rewrite survived). "Issues remaining per round" — the convergence curve, the number the
refinement loop exists to move — was only inferable from list ordering.

**Fix: `RefinementRound`.** One round holds the text it checked, its `QualityReport`, its
`RefinerTurn`, its answers, and its rewrite. `RequirementRunRecord.rounds` replaces all
three lists.

**Why grouping beats numbering.** The rejected alternative was to keep the flat lists and
add `revision_number` to `QualityReport` and `RefinerAnswer`. That leaves intermediate
texts homeless (a fourth parallel list would be needed), and — decisively — it recreates
the failure pattern responsible for nearly every bug found in this schema: parallel
structures that must agree with nothing forcing them to. Four lists keyed by revision
number can have gaps, duplicates and mismatched key sets, each needing its own validator.
The "simpler" option needs *more* validation code. Grouped rounds need none of it: a
round cannot disagree with itself.

**Two whole classes of bug became structurally impossible**, rather than newly guarded:

- `_refinement_is_recorded` (the "COMPLETED after refinement with no rewrite" fix) was
  **deleted**. `final_text` now reads the last round's own recorded text, so there is no
  path by which it can silently report the original text for a rewritten requirement.
- `_qa_chain_is_traceable` was **deleted** as a record-level check; question/answer
  matching is now per-round and local, where it belongs.

**New: continuity is checkable, which it never was before.** `_trajectory_is_continuous`
enforces that rounds are numbered 1..N with no gaps, that round 1 checks the
requirement's own text, and that every later round checks exactly the text its
predecessor rewrote. Previously a "history" could contain a check on text that came from
nowhere; now the chain either holds or is rejected.

**`final_requirement` became a computed field**, derived as the most recent rewrite among
the rounds, so it cannot disagree with them. It was consequently removed from the cap
rows of `_OUTCOME_RULES`; the equivalent guarantee is now an explicit check that a cap
outcome has at least one round with a rewrite.

**`final_text` handles a trailing rewrite.** If the cap fires *after* refining but before
re-checking, the last round has a rewrite and no successor — so that rewrite, not the
text it replaced, is the latest version. Both shapes are supported and tested.

**New computed field `issues_per_round`** — `[len(r.quality_report.issues) for r in
rounds]`. The convergence curve is now one attribute rather than a reconstruction, and
it survives a JSON round trip.

**Per-round checks added** (each one a contradiction the old flat lists could express):
a passing round that still asked a question or rewrote; answers with no turn; an answer
to a question not asked that round; a question addressing an issue that round's report
never raised; a duplicate question id; a rewrite whose `original_text` is not the text
checked; a rewrite using an answer not given that round; a turn numbered differently
from its round.

**One check deliberately not written.** "A rewrite requires answers" can never fire on
its own: `answers_used` has `min_length=1`, so an empty `answers` list always trips the
subset check first. Found by mutation testing — the branch was unreachable, therefore
untestable in isolation, therefore untested. Dropped rather than kept as dead defensive
code, the same call made earlier for the redundant `refiner_answers` signal.

Tested: 154 checks, and a 20-mutation run (every round check, every chain check, plus
the older rules) with 20 caught.

## Duplicate keys — one shape, nine instances (2026-08-05)

A review found that `RefinementRound.answers` allowed two different answers to the same
`question_id` in one round — the mirror of the duplicate-question check written a few
lines above it, never applied to the answers side.

Checking whether that shape existed elsewhere found it in **eight more places**, all
unguarded:

| Was accepted | Consequence |
|---|---|
| two answers to one question in a round | ambiguous which answer the human gave, and which the rewrite used |
| two `Issue`s sharing an `id` in one report | breaks issue identity — the basis of `ClarifyingQuestion.issue_id` and of `user_confirms_resolved` suppression, which can then suppress a different issue than the human meant |
| duplicate requirement ids in a `RequirementSet` | breaks every by-id lookup: `conflicts_for`, `dependencies_for`, the document record's join |
| `ConsistencyConflict(["R1","R1"])` | satisfies `min_length=2` vacuously — a requirement conflicting with itself |
| `DependencyLink` from a requirement to itself | trivial 1-node cycle; `find_cycles()` reports it and the human is asked to break a self-dependency |
| the same `DependencyLink` twice | inflates the graph; the same cycle reported more than once |
| the same `TestTechnique` twice in a strategy | meaningless |
| a `TestCase` naming one requirement twice | double-counts coverage in the traceability matrix |
| two `TestCase`s sharing an `id` | untraceable results |

**Fixed as a class, not an instance.** A shared `_require_unique(values, what, where)`
helper is now used at all nine sites. Fixing only the reported one would have left eight
live instances of a shape that four separate review passes had already found in
different places — the pattern, not the instance, is the bug.

**Why duplicates are never merely redundant here.** Each of these lists is semantically a
set or a mapping: its entries are identified by something. A repeated identifier makes
"the thing with id X" ambiguous, which silently breaks lookups, suppression by id, and
any count taken over the list. None of it errors — it just quietly returns the wrong
member.

The `Issue` id case is the most consequential and closes part of gap 6: issue ids can no
longer collide *within* a report. Collisions *across* rounds (a fresh LLM call reusing
`REQ-D-ISSUE-1` for a different defect) remain open — that is the rest of gap 6.

This also closes most of gap 7 as a side effect: duplicate requirement ids, self-
dependencies and vacuous conflicts were all on that list. What remains of gap 7 is the
`TestStrategy` / `Classification` system-type agreement and technique-eligibility rules.

Tested: all nine rejected, plus valid neighbours accepted so the guards cannot pass by
rejecting everything. Mutation run: 10 deliberate deletions (each guard, plus the shared
helper's raise), 10 caught. Suite at 167 checks.

## Denormalised fields swept, not patched (2026-08-05)

A review sweep for denormalisation — a field restating something held elsewhere, with
nothing checking the restatement — found five more. All confirmed by construction.

1. `Classification.requirement_id`, `TestStrategy.requirement_id` and
   `TestPlan.requirement_id` were never checked against `RequirementRunRecord.
   requirement.id`. A record for `REQ-1` could carry a classification for `REQ-WRONG`.
   The identical check already existed for `rounds[i].quality_report.requirement_id`;
   it had simply never been applied to the other three.
2. `TestCase.technique_used` did not have to be among `TestStrategy.techniques`. This
   one hollows out a documented design claim: the technique-selection rules exist so
   `TestStrategy.rationale` can be audited against them, but if generation is free to
   use a technique that was never selected, auditing the rationale proves nothing about
   the tests.
3. A `TestPlan` for `REQ-1` could consist entirely of cases covering other requirements
   — nominally REQ-1's tests, with nothing in it testing REQ-1.
4. `Classification.system_type` and `TestStrategy.system_type` could disagree (`WEB` on
   one, `MOBILE` on the other, same record).
5. `Issue.related_requirement_ids` allowed duplicates — missed by the `_require_unique`
   sweep the round before.

**Done as a sweep, per the reviewer's suggestion, and it changed the implementation.**
The `requirement_id` checks are driven by `fields_carrying_requirement_id()`, which
*discovers* nested models exposing a `requirement_id` at runtime rather than listing
them. Three separate reviews reported these three fields one at a time, which is the
evidence that a hand-maintained list does not hold. A model added later is swept without
anyone remembering. An anchor test pins the discovered set, so silent narrowing of the
sweep is caught — verified by a mutation that neuters the discovery predicate.

**On #4, the copy stays.** `TestStrategy.system_type` could have been deleted and read
from the classification instead, but `TestStrategy` is a standalone stage output whose
technique eligibility depends on knowing its own system type. So the denormalisation is
kept deliberately and checked.

**On #3, the strict reading was chosen.** *Every* case must cover the plan's
requirement, not merely one of them, matching the design note that cases may name
additional ids *beyond this one*. Risk worth naming: if the generator ever legitimately
emits a case for REQ-1's plan covering only a dependency, this rejects it. That failure
is loud and trivially loosened to "at least one case"; the opposite failure — a plan
silently not testing its own requirement — is not.

**Still open from gap 7:** technique *eligibility* (that an `AI_SYSTEM` requirement
cannot draw `EQUIVALENCE_PARTITIONING`, and a `WEB` one cannot draw `ADVERSARIAL`). #2
above ensures generated cases match the selected strategy; it does not yet constrain
what the strategy may select. The eligibility pools are specified in "How techniques get
selected" above and remain prose.

Tested: all five rejected with specific messages, valid neighbours accepted. Mutation
run: 13 deletions across the new sweep and older guards, 13 caught. Suite at 178 checks.

## Technique eligibility enforced (2026-08-05)

**The gap.** "How techniques get selected" above calls the system-type constraint a
"hard constraint, not negotiable" and states the eligible pool for each `SystemType`. It
had lived only in prose since the schema was written. Nothing stopped a `TestStrategy`
selecting `ADVERSARIAL` for a thermostat — the exact failure the layer was designed to
prevent, and the one the note names.

An earlier paragraph claimed no schema change was needed, on the grounds that the
`TestTechnique` enum limits values and `rationale` records a justification. That was
wrong: the enum stops an *invented* technique, not an *ineligible* one, and a rationale
can be audited only by a human who happens to look.

**Fix.** `ELIGIBLE_TECHNIQUES: dict[SystemType, frozenset[TestTechnique]]` plus a
validator on `TestStrategy`. `EXPLORATORY` and `PERFORMANCE` are in every pool
deliberately (experience-based testing applies to anything; a timing constraint can
appear in any system type). The white-box and multi-run CT-AI techniques appear in no
pool, per "Deliberately left out" above.

**Checked on `TestStrategy` itself, not at record level**, because a strategy carries its
own `system_type` and is a standalone stage output — it should not be constructible in an
invalid state outside a run record either.

**The chain that makes it meaningful**, each link added in a different pass:

    Classification.system_type
      -> (record) test_strategy.system_type must equal it
      -> (strategy) techniques must come from that type's pool
      -> (record) each TestCase.technique_used must be one the strategy selected

Without the first link, a Selector could grant itself the AI pool by disagreeing with the
Classifier; without the last, generation could ignore the strategy entirely. Any one link
missing makes the other two decorative — tested by mutation.

Tested: each ineligible pairing rejected, each pool's shared techniques accepted for
every system type, mixed eligible/ineligible lists rejected, and the smuggling route
(strategy claiming `AI_SYSTEM` against an `OTHER` classification) rejected. Mutation run:
9 deletions including widening and narrowing each pool, 9 caught. Suite at 197 checks.

## References must resolve, copies must restate correctly (2026-08-05)

A review found `ClarifyingQuestion.issue_category` was never checked against the actual
`category` of the `Issue` its `issue_id` names. `_round_is_coherent` verified the issue
*exists* in that round's report and stopped there, so a question could point at an
`AMBIGUOUS_TERM` issue while declaring itself `VAGUE_PRONOUN`. Confirmed and fixed. It
matters for the same reason `TestStrategy.system_type` did: any per-category metric over
the questions asked ("how many `VAGUE_PRONOUN` clarifications did the Refiner need?")
would silently disagree with the taxonomy the Quality Checker actually assigned.

**On the reviewer's suggested heuristic — grep for fields commented "denormalized".**
Tried it. The file marks exactly *one* field that way: the one already reported. The
heuristic finds what has already been noticed, because the comment is written by whoever
noticed. Looking structurally instead — for any field naming something that lives
elsewhere — found three more, and one is more consequential than the reported bug.

**The more consequential one: invented requirement ids.** Nothing checked that a
requirement id mentioned anywhere actually exists in the document's set. A
`ConsistencyReport` could name `REQ-12` in a document that stops at `REQ-8`; so could a
`DependencyLink`, an `Issue.related_requirement_ids`, or a `TestCase.requirement_ids`.

This is not a coding slip, it is an LLM failure mode. A model asked to find conflicts
across a document can return a confident, well-formed, fully-populated report about a
requirement that does not exist. Nothing about it looks wrong on inspection, so it would
flow through the pipeline and into the results as a real finding.

`DocumentRunRecord._references_resolve` now checks all four sources against the set. The
document record is the only level where this is possible — it is the only place holding
both the requirement set and everything pointing into it.

**Two smaller ones found in the same sweep.** `ConsistencyReport.doc_id` /
`DependencyReport.doc_id` could disagree with `requirement_set.doc_id` — a report from a
different document entirely, silently attached. Checked when both are present, since
either being `None` is simply no claim. And an `Issue` could list *its own* requirement
in `related_requirement_ids`: "REQ-D is inconsistent with REQ-D", the same nonsense as a
self-dependency, now rejected at round level where the owning requirement is known.

**One found and deliberately not fixed — this call was wrong, see the correction below.**
`Requirement.source_doc_id` can differ from its `RequirementSet.doc_id`. That looks like
the same shape but is not: a `RequirementSet` is legitimately allowed to be a curated
collection drawn from several source documents — which is exactly what
`datasets/requirements_dataset.json` is. Enforcing agreement would break a valid use case
to catch a hypothetical one.

Tested: all six rejected with specific messages, valid neighbours accepted. Mutation run:
11 deletions, 11 caught. Suite at 208 checks.

## Correction: `source_doc_id` agreement is enforced after all (2026-08-05)

The section above declined to check `Requirement.source_doc_id` against its
`RequirementSet.doc_id`, justifying it with the claim that a set is "legitimately a
curated collection drawn from several source documents — which is exactly what
`datasets/requirements_dataset.json` is."

**That claim was asserted without checking the data, and it is false.** All ten documents
in that file are homogeneous: every requirement carries its own document's id, no
document mixes sources, and none omits `source_doc_id`. The multi-source case was
invented to justify not doing the work.

The design reasons point the same way once the fact is corrected: the pipeline treats one
`RequirementSet` as one document throughout, and PURE documents are individual SRSs. So
the check is now enforced — a requirement may not name a source document other than its
set's. Verified that all ten real documents still validate.

**The `None` case is deliberately allowed** (raised separately by the reviewer as a
judgment call, correctly). A requirement with `source_doc_id=None` inside an attributed
set is accepted: "provenance wasn't recorded" is a legitimate state and materially
different from claiming the *wrong* provenance. Requiring it would also force every
constructed `Requirement` to carry one, including throwaway ones. Both directions tested,
and a mutation that turns the None-allowance into a requirement is caught.

**If cross-document consistency checking ever comes into scope**, drop this check then —
deliberately, with the use case in front of you, rather than having it silently never
have existed.

**Process note.** This is the only finding in the whole review sequence that was
originally dismissed rather than fixed, and the dismissal rested on a fact about the
project's own data that took one command to check and was never run. Worth remembering
when the next "that's legitimate, actually" argument appears: check the claim before
resting a decision on it.

## Gap 6: issue identity across rounds (2026-08-05)

**The gap.** Every round's `QualityReport` is a fresh LLM call minting its own issue ids,
so nothing linked round 2's `REQ-D-ISSUE-1` to round 1's. Demonstrated on THEMAS-REQ-D:

    rev 1: REQ-D-ISSUE-1 = vague_pronoun    span='these limits'
    rev 1: REQ-D-ISSUE-2 = non_verifiable   span='subsequent processing'
    rev 2: REQ-D-ISSUE-1 = non_verifiable   span='subsequent processing'

The pronoun genuinely got fixed, so round 2 found one issue left and numbered it
`ISSUE-1` -- it was the first issue *it* found. The human had ticked
`user_confirms_resolved` on `ISSUE-1` in round 1, meaning the pronoun. An orchestrator
suppressing that id in round 2 suppresses the **non-verifiable** issue instead: a real,
unresolved defect silently dropped, after which the requirement passes.

**Decision: option A — ids are stable across rounds.** The orchestrator matches each
round's issues against the previous round's (on category and span) and reuses the id when
it is the same defect. Rejected alternative (option B): treat ids as per-round only and
suppress on `(category, span)` instead. Both need the same matching; A persists the
result rather than discarding it, which is what makes per-defect numbers ("a
`VAGUE_PRONOUN` takes 2.1 rounds to resolve") available without re-deriving the matching
at analysis time and defending it separately.

**What the schema now enforces** (`_issue_identity_is_stable`, plus local checks on
`RefinementRound`):

- a reused issue id must carry the same category and span -- one id, one defect;
- `suppressed_issue_ids` records what the checker was told not to re-flag, so an issue
  that vanishes between rounds is no longer indistinguishable from one actually fixed;
- a suppression must name an issue the human confirmed resolved in an earlier round;
- revision 1 cannot suppress anything;
- a round may not suppress an issue its own report still raises;
- **suppressions accumulate** -- dropping one in a later round lets the issue reappear,
  which is the exact loop `user_confirms_resolved` exists to break.

**New computed field `issue_history`**: issue id -> the revisions it appeared in.
`[1]` was gone by round 2; `[1, 2, 3]` survived three rounds. Read it alongside each
round's `suppressed_issue_ids`: an id that stops appearing because it was *suppressed*
was not fixed, and pooling the two would overstate how well the loop converges.

**What no schema can fix, stated plainly.** The matching is a heuristic. If the Refiner
rewrites the sentence, the span text changes and the same defect may look new -- the id
stops and a new one starts. That is inherent to matching defects across rewrites. The
gain is that it is now *visible in the record* rather than invisible; the loss shows up
as an artificially short `issue_history`, not as a wrong suppression.

**Removed as unreachable: a separate "was this id ever raised?" check.** A question may
only reference an issue raised in its own round, so a confirmation implies the issue was
raised; an unraised id is therefore always caught as unconfirmed. Third time this
redundancy pattern has appeared, and the third time mutation testing was what surfaced
it -- the enumerated and anchor layers cannot see a branch that no valid record reaches.

**Mutation testing also caught a bad fixture of mine.** The "suppressing an issue the
human never confirmed" test originally *omitted* the answer entirely, so it failed
because no answer existed rather than because the confirmation flag was unset -- it
passed for the wrong reason, and a mutation making every answer count as a confirmation
went undetected. The fixture now includes an explicit unconfirmed answer to the same
question.

Tested: 224 checks; mutation run of 14 deletions across the new rules and older guards,
14 caught.

**Still the orchestrator's job, not the schema's:** performing the match itself, and
carrying suppressions forward. The schema rejects a record where either was done wrong;
it cannot do them.

## Self-review sweep: five weak points on untried angles (2026-08-05)

With all seven gaps closed, a deliberate sweep on angles the review passes had *not*
used — empty identifiers, cross-record id collisions, timestamps, scale, and mutability.
Five findings, four fixed.

**1. Empty identifiers and text.** The earlier non-empty sweep covered
explanation-style fields but never ids, titles, answers, or list *items*, so
`Requirement(id="")`, `Issue(id="")`, `TestCase(steps=[""])` and
`RefinerAnswer(answer_text="")` all validated. An empty id is worse than an empty
explanation: it makes every by-id lookup and the whole issue-identity mechanism
meaningless, while the record still looks populated.

Fixed with a `NonEmptyStr = Annotated[str, Field(min_length=1)]` alias applied across
~31 fields, rather than that many separate `Field(min_length=1)` calls — which is also
what lets the constraint reach inside list items (`list[NonEmptyStr]`), the case
per-field constraints could not express. All ten real dataset documents still validate.

**2. Test case ids colliding across plans.** `TestPlan` guarded uniqueness within
itself, but two plans in one document could each number their first case `TC-1`. Since
the plans are assembled into a single suite (Known Limitation 1), a result would be
untraceable to the case that produced it. Now checked across the whole document.

**3. Naive timestamps.** `started_at` accepted a timezone-less `datetime`. Comparing a
naive datetime with an aware one raises `TypeError`, so a run recorded with
`datetime.now()` and another with `datetime.now(timezone.utc)` cannot be ordered — and
the failure surfaces during analysis, long after the run. Now required to be aware. No
bound on the value itself: an implausible date is a different and far less likely
mistake.

**4. `find_cycles` crashed on long chains.** The recursive DFS raised `RecursionError`
on a 1200-link dependency chain. Every document tested so far is tiny; real PURE
documents are not, and a crash mid-run on a large document is exactly the scenario the
resumability work exists for. Rewritten iteratively, and verified equivalent to the
recursive version on 400 randomised graphs plus the fixed cases before replacing it.
Now handles a 5000-link chain, and finds a 3001-node cycle.

**5. Not fixed — mutation after construction bypasses every validator.** Pydantic
validates at construction, so `record.rounds.append(...)` or
`requirement_set.requirements.append(...)` skips all of it. Demonstrated: appending a
duplicate id *and* a foreign `source_doc_id` to a validated `RequirementSet` succeeds.

Deliberately not "fixed":

- `frozen=True` would forbid the incremental construction the orchestrator needs
  (rounds are appended as the loop runs, records as a document proceeds).
- `validate_assignment=True` catches `rec.rounds = [...]` but *not* `.append()` — it
  would give false confidence against the more likely mistake, which is worse than no
  guard.

**The practical mitigation, for the orchestrator:** re-validate before persisting —
`RequirementRunRecord.model_validate(rec.model_dump())` re-runs every check. Loading a
record from disk already validates, so a mutated record is caught on the next read;
re-validating on write just moves that detection closer to the cause.

Tested: 242 checks; mutation run of 8 deletions including weakening `NonEmptyStr` back
to `str`, 8 caught.

## Retry without redoing everything (2026-08-05)

Prompted by a practical question — during experimentation, an error should not cost a
whole document's work. Two holes, both confirmed by construction.

### 1. `pending_requirement_ids` skipped failed requirements — a real bug

It was defined as "requirements with no record". But a requirement that **errored** has
a record. So a document with one completed, one errored and one interrupted requirement
reported `pending == []`: a resume pass would say "nothing to do" on an incomplete run,
and the two unfinished requirements would never be retried.

That silently defeated the purpose of recording `ERROR` at all, and of decision D2
(resumability) along with it. The first definition was simply the wrong question — "has
this been started?" instead of "does this still need work?".

Now: pending = no record, **or** an outcome of `IN_PROGRESS` (interrupted) or `ERROR`
(a stage failed). `TERMINAL_OUTCOMES` names the three that count as finished
(`COMPLETED`, `CAP_GENERATED`, `CAP_STOPPED`), so the distinction is stated once.

### 2. A document-level stage could not be retried without losing something

Three routes were tested, all bad:

- retry in place, keeping the failure → **rejected**, because "a failed stage produced
  no report" fired once the report appeared;
- retry in place, deleting the failure → worked, but the record then claimed the stage
  never failed, discarding its `retry_count` too;
- start a new run → worked, but completed requirement records could not be carried over
  (their `run_id` belongs to the old run), so every requirement would be reprocessed.
  On a 30-requirement document that is 120+ API calls to repair two.

So the options were "lie about the history" or "redo the expensive work".

**Cause: `errors` meant current state.** Under that reading a stage could not hold both
a failure and a report, because a failure *was* the statement "this stage has no
report".

**Fix: `errors` is now a log of failed attempts.** A stage may hold an earlier failure
and a later successful report — which is exactly what a retry looks like. The rules
become:

- `COMPLETED` — both reports present. `errors` may be non-empty (earlier attempts).
- `DEGRADED` — at least one report **still** missing, and every missing report's stage
  must have a recorded failure explaining its absence.
- a document whose stages all eventually succeeded is `COMPLETED`, even if they failed
  first. `DEGRADED` with both reports present is now rejected.

**A guard was deliberately removed** — "a failed stage must not have a report", added
three passes earlier. Under the old meaning that combination was incoherent; under the
new one it is the normal shape of a successful retry. The mirror rule was kept and is
what keeps the log honest: a *missing* report must have a failure explaining it.

**Still one error entry per stage.** A second manual retry bumps `retry_count` rather
than appending another entry, which keeps "which stage fails most often" a straight
count over `errors`.

**Bonus for the write-up:** "3 of 40 documents degraded on the first pass, re-run
successfully" is now computable from a single record — `outcome=COMPLETED` with a
non-empty `errors` list — instead of requiring two run directories to be compared.

Tested: 253 checks. Mutation run of 9 deletions, including reverting `pending` to the
old definition and re-allowing `DEGRADED` with both reports, 9 caught. Two existing
tests asserted the old semantics and were updated deliberately rather than deleted.

## Requirement-level errors made symmetric, and the sweep that followed (2026-08-05)

`RequirementRunRecord.error: Optional[StageError]` became
`errors: list[StageError]`, matching the document record: a log of failed attempts, not
a statement of current state. A requirement that failed and succeeded on a retry now
keeps its failure, so "how many requirements needed a retry" is countable
(`outcome in TERMINAL_OUTCOMES and record.errors`).

`ERROR` no longer *forbids* being carried elsewhere; it requires at least one entry,
meaning the run stopped **because of** a failure.

**One asymmetry kept, deliberately.** The document record allows at most one error per
stage — each document-level stage runs once. A requirement's Quality Checker and Refiner
run once per round, so the same stage can legitimately fail more than once, and
duplicates are allowed here.

**Known consequence of that:** two byte-identical entries cannot be distinguished from a
double-append bug. Adding `at_revision: Optional[int]` to `StageError` would fix both
that and traceability, but it is not built — the retry *count* is what the reporting
needs, and which round a failure happened in is not yet a question anyone is asking.
Revisit if it becomes one.

### Sweep of the changed schema

Making `errors` a log widened what a record can say, so the new surface was swept
immediately. Four findings, two fixed:

**Fixed — `CAP_STOPPED` could record a failure in a stage that never ran.** That outcome
forbids `test_strategy` and `test_plan`, because the human stopped before stage 3. An
error naming `STRATEGY_SELECTOR` or `TEST_GENERATOR` is therefore impossible, and is now
rejected.

**Fixed — `ERROR` with every stage's output present.** The mirror of the document rule
added in the same session ("`DEGRADED` but both reports present"): if classification,
rounds, strategy and plan are all there, nothing is missing, so the run did not stop
because of a failure. Recorded errors on such a record are earlier attempts that
succeeded on retry — which makes it `COMPLETED`, not `ERROR`. Symmetry found this one;
it was not reported.

**Not a bug — a document can be `COMPLETED` while requirements inside it are `ERROR`.**
`DocumentOutcome` describes the document-level stage phase only, deliberately (see
"Document-level run record"). Whether requirements are finished is
`pending_requirement_ids`, which correctly lists the errored one. Working as designed.

**Not built — a computed field for "needed a retry".** It is a single expression over
fields already present, and the computed-field count is high enough that adding one for
every derivable question is not obviously an improvement. The expression is recorded in
`ORCHESTRATOR_CONTRACT.md` instead.

Tested: 257 checks; mutation run of 8 deletions covering both new rules and older
guards, 8 caught.

## Generated diagrams (2026-08-05)

`generate_diagrams.py` emits two Mermaid files from `schemas.py`. Rerun it after every
schema change: `python design/generate_diagrams.py`.

- `models.mermaid` — every model and enum with its fields, plus containment/reference
  arrows. **Fully introspected** from Pydantic's `model_fields` at runtime, so it
  cannot drift: add a field, rerun, it appears. Required fields are marked `*`;
  `final_text` is marked `$computed$` since it is derived rather than stored.
- `pipeline.mermaid` — stages 0–4 with the schema type carried along each arrow.

**Path trees (added 2026-08-05).** Three more generated files, showing every route
through the pipeline rather than its structure:

- `paths_document.mermaid` — the four routes a document can take (both stages succeed,
  either one fails, both fail).
- `paths_requirement.mermaid` — every route a requirement can take to a *successful*
  terminal outcome: first-try pass, refine-then-pass, and both cap branches, including
  the `user_confirms_resolved` side-path into `suppressed_issue_ids`.
- `paths_failure.mermaid` — what happens when a stage fails or the process dies:
  retry, `ERROR`, and `IN_PROGRESS` as the resume marker.

Split into three deliberately: one tree covering the refinement loop, both cap branches
and five failure points is unreadable, and the three answer different questions.

Like `pipeline.mermaid` the trees are hand-declared, since execution order is not in the
models. But their *leaves* are checked: `validate_path_trees()` requires the terminal
nodes to cover every `RunOutcome` and `DocumentOutcome` value exactly. Add an outcome to
the schema without drawing its path and generation fails; draw a path ending in an
outcome that does not exist and it fails too. Both directions verified.

**Why `pipeline.mermaid` is only partly generated.** Execution *order* is not recorded
anywhere in the Pydantic models — nothing in `QualityReport` says it runs after
`Classification`. That ordering lives in the orchestrator, which doesn't exist yet. So
the stage graph is declared by hand in `PIPELINE_NODES` / `PIPELINE_EDGES`.

To stop the hand-written half going stale, every schema type named in the declaration
is checked against `schemas.py` at generation time and the script exits with an error
if one is renamed or deleted (verified: renaming a type to a non-existent one fails
loudly rather than emitting a wrong diagram). **What this does not catch:** a stage
whose real input type changes to a *different type that also still exists* — the check
confirms the named types exist, not that they're the right ones. Accepted rather than
papered over; the proper fix is generating the flow from the orchestrator's actual call
graph, worth revisiting once the orchestrator is written.

Both outputs carry a `%% GENERATED ... do not edit by hand` header, and both are plain
text so a git diff shows exactly which arrows or fields a schema change moved.

## Deferred: Pairwise Testing

Considered for `TestTechnique` (ISTQB-recognized, used both generally for input-
combination testing and specifically in CT-AI for ML input testing) but deliberately
**not added yet**. Pairwise coverage is a checkable, binary, mathematical property: a
test suite either covers every pair of parameter values or it doesn't. Generating a
suite that actually satisfies that property requires systematic combinatorial search
(e.g. an IPO/greedy algorithm, or a library like `allpairspy`) -- it is not something
an LLM can reliably produce by prompting alone. A worked example: for 3 parameters (OS
x network x language, 2x3x2 values), a genuine minimal pairwise set needs 6 test cases
with zero missing pairs; a plausible-looking LLM-style guess with 4 test cases (varied
values, nothing obviously wrong on inspection) was missing 5 of the 16 required pairs
-- silently, with no visible sign of the gap unless you run a coverage checker against
it. Labeling LLM-generated test cases "pairwise" without that guarantee would be a
false claim in the pipeline's output.

Path to adding it later: a hybrid design where the LLM extracts the parameters and
their possible values from the requirement text (a task it's good at), a small
deterministic algorithm selects the actual minimal covering combinations (not the
LLM), and the LLM then writes human-readable steps/expected results for each
algorithmically-chosen row. That's meaningfully more implementation work than the
other techniques (which are single-parameter or single-relation reasoning tasks an LLM
can do directly), which is why it's being deferred rather than added alongside them.
Revisit once the rest of the pipeline (agents, orchestrator) is implemented and
working well.

## Refiner split into REFINER_QUESTIONER / REFINER_REWRITER (2026-08-08)

`PipelineStage.REFINER` was one enum member covering two LLM calls with different
inputs and outputs: `(Requirement, QualityReport) -> RefinerTurn` (ask) and
`(requirement, RefinerAnswer[]) -> RefinedRequirement` (rewrite) -- see "2c. Refiner —
why it's request/response instead of a blocking call" for why those were already two
separate schemas rather than one blocking call. Sharing one stage identity meant
sharing one `StageConfig` (model + prompt hash) and one bucket for `TokenUsage`/
`StageError`, so the two calls could not be configured, measured, or retried
independently -- e.g. a cheaper model for phrasing a question than for producing a
rewrite, or "how often does the questioner fail" as a distinct number from "how often
does the rewriter fail," were both unanswerable from the records.

**Decision:** split into `REFINER_QUESTIONER` / `REFINER_REWRITER`, each with its own
`StageFns` callable (`refine_questioner` / `refine_rewriter`), `StageConfig` entry (auto-
derived from `ALL_STAGES`, no separate registry to maintain), and `PipelineStage` value
passed to `call_stage`. No schema type changed: `RefinerTurn`, `RefinerAnswer`, and
`RefinedRequirement` already had exactly the right shape for one call each, and
`RefinementRound.turn`/`.rewrite` already recorded which half had completed
independently of the other -- only the stage-identity/config plumbing was shared and
needed splitting.

**Resume position.** `resume_at`'s old two-way branch (`rewrite is None -> REFINER`,
else `-> QUALITY_CHECKER`) became three-way: `last.turn is None -> REFINER_QUESTIONER`
(nothing asked yet), `last.turn is not None and last.rewrite is None ->
REFINER_REWRITER` (the questioner has produced a turn, rewrite outstanding -- this
does NOT imply the human has answered yet; see the 2026-08-08 fix below), else
`-> QUALITY_CHECKER`. See ORCHESTRATOR_CONTRACT.md item 6 for the updated pseudocode
and the one ambiguity that survives the split unchanged (an already-capped round looks
identical to a genuinely mid-rewrite one; both land on `REFINER_REWRITER`, harmlessly).

**Missed resume state, fixed same day.** The first version of this split conflated
`turn is not None` with "the human has already answered": `_run_refine_loop` only
called `human_fns.answer_questions` inside the `if turn is None:` branch, so a round
resumed with `turn` set but `answers` still empty (interrupted between the questioner's
call and the human's answer -- schema-valid: `RefinementRound` only rejects `answers`
non-empty with `turn is None`, never the reverse) skipped the human entirely and handed
the rewriter an empty `answers` list. Fixed by keying the human-ask on `answers`, not
`turn`: ask iff `not answers`, regardless of whether `turn` was just produced or was
already on the resumed record. `resume_at` needed no change -- `REFINER_REWRITER`
already meant "questioner done, rewrite outstanding," which was always correct; only
`_run_refine_loop`'s handling of that position was incomplete. Regression test:
`orchestrator/test_harness.py::test_resume_mid_round_asks_human_when_answers_missing`.

**Diagrams.** `pipeline.mermaid` and `paths_requirement.mermaid` keep a single "Refiner"
node deliberately -- these are hand-declared (see "Generated diagrams" above) and the
two calls are still one conceptual step to a reader of the pipeline shape. `models.mermaid`
is fully introspected, so it lists both new enum values automatically on regeneration
with no hand edit.

**Rejected: keep one stage, add a sub-field distinguishing ask/rewrite calls within
`TokenUsage`/`StageError`.** Would have kept `RunMetadata.stages` at one entry, but
every consumer of `RunMetadata.stages[stage].model` (`call_stage`'s only source of
"which model runs this call") would need a second lookup key anyway, so the saving is
illusory -- and `ALL_STAGES`, `RunMetadata._covers_every_stage`, `TokenUsage.stage`, and
`StageError.stage` already exist specifically to make "which stage" a first-class,
countable value; adding a second axis under one stage duplicates that machinery instead
of reusing it.

## Per-attempt observability replaces the token-only usage log (2026-08-08)

Full design, including the two revisions made during review and the exact agreement
rules, is in
`docs/superpowers/specs/2026-08-08-per-attempt-observability-design.md` -- not restated
here. Summary of what changed and why, for anyone scanning this file for the decision
rather than the reasoning:

- `TokenUsage`/`DocumentTokenUsage` deleted. `RequirementRunRecord.usage`/
  `DocumentRunRecord.usage` renamed to `attempts: list[StageAttempt]`/
  `list[DocumentStageAttempt]` -- one row per attempt, success or failure, not just
  calls that returned. `total_tokens`/`document_stage_tokens` now sum over `attempts`.
- New `AttemptResult` enum (`SUCCESS`/`TRANSPORT_FAILURE`/`VALIDATION_FAILURE`/
  `OTHER_FAILURE`) -- kept separate from `FailureKind` rather than reusing it, since
  `FailureKind` has no success case and was scoped to "why did the stage *finally*
  fail," not "what happened on this one try."
- `StageError`/`DocumentStageError` gained `invocation_id`, linking an error directly
  to the attempts-log invocation it summarises. This replaced an earlier draft (rev 1
  of the linked spec) that matched errors to attempts by list position at requirement
  level and by aggregating retry counts across merged invocations at document level --
  rejected on review for depending on append order and for a stale-`kind`/`message`
  artifact in the document-level merge arithmetic.
- `retry_document_stage` no longer merges a repeated failure into an existing
  `DocumentStageError`; each manual retry is its own invocation and, if it fails,
  appends its own error -- symmetric with how `RequirementRunRecord.errors` already
  worked. `DocumentRunRecord` lost its "at most one error per stage" rule as a direct
  consequence.
- Schema version bumped `1.0` -> `1.1` on `RunMetadata` -- no real run predates this (no
  `document.json`/`runs/` anywhere in the repo or its history), so nothing needed
  migrating; the bump exists only so a future reader can tell the two record shapes
  apart.
- Two checks were written, then deleted after mutation-testing proved them
  unreachable: a standalone "more than one SUCCESS" check (subsumed by "the first
  SUCCESS's index must equal the last position," which already implies at most one),
  and a `_require_unique` on `(invocation_id, attempt_number)` (subsumed by the
  attempt-number-sequence check, since a duplicate number inside one invocation always
  already breaks that group away from `range(1, len+1)`). Both are CLAUDE.md's "don't
  write a check that can't fire" caught in the act, not by inspection but by mutation
  runs -- the second and third time this exact failure mode has shown up in this
  project (see `_run_refine_loop`'s already-capped-round short-circuit, deleted the
  same way, in "Refiner split into REFINER_QUESTIONER / REFINER_REWRITER" above).

## Run config, provider adapters, CLI HumanFns (2026-08-09)

Orchestrator phase continues: typed `StageFns`/`HumanFns` Protocols, a validated YAML
run configuration, real Gemini/Groq adapters, and a terminal `HumanFns` implementation.
`orchestrator/stages.py` (the 8 real prompts/parsers) stays out of scope, unchanged --
its own docstring already said "not built this phase," and nothing here needed to touch
it. This went through three plan revisions before implementation; what's recorded below
is the shipped design, not the rejected drafts -- the rejections themselves aren't
restated, only what they changed.

**Typed Protocols (`orchestrator/stage_fns.py`, new module).** `StageCallResult`,
`StageCallFailed`, `StageFns`, `HumanFns` moved out of `orchestrator/pipeline.py` into
this new module (re-exported from `pipeline.py`, so existing imports there keep
working) so `orchestrator/providers/` and the future `orchestrator/stages.py` can
import them without pulling in `pipeline.py`'s control-flow code. Ten `Protocol`
classes now give each `StageFns`/`HumanFns` field a real signature (verified against
every call site in `pipeline.py`), replacing `Callable[..., StageCallResult]`.
`StageFns`/`HumanFns` stay frozen dataclasses, not Protocol-typed containers -- a
typo'd field name is still an immediate `TypeError`, a property a bare `Protocol`
container wouldn't give for free. Per `ORCHESTRATOR_CONTRACT.md` item 16, this typing
pass does not and cannot enforce the `None`-vs-`[]` document-context distinction on
`check_quality`/`select_strategy`/`generate_tests` -- a `Callable`- or `Protocol`-typed
parameter accepts either equally happily; that stays a runtime-tested invariant, not a
static one. Verified by `orchestrator/test_stage_fns.py` using
`typing.get_type_hints()`, not raw `inspect.signature()` annotations -- both
`stage_fns.py` and that test file use `from __future__ import annotations`, so a raw
annotation is an unresolved forward-reference string; comparing two such strings can
match by formatting coincidence or fail on trivial reformatting without the comparison
meaning anything. `get_type_hints()` resolves them into real class objects first.

**`StageConfig` gains `prompt_version`/`temperature`/`output_mode`; `RunMetadata` loses
its own copies of the first two (schema `1.1` -> `1.2`).** `orchestrator/config.py`'s
`RunConfig` allows per-stage overrides of all three, but `RunMetadata` (the persisted
provenance record) had only one run-level copy of `temperature`/`prompt_version`. Two
independently-settable copies -- one on `RunMetadata`, one implied by a per-stage
override -- could disagree, which is the "two fields that must agree" failure pattern
CLAUDE.md names as the cause of most bugs in this project. Fix: remove the redundant
`RunMetadata`-level fields entirely rather than add a validator to police them. No
"mixed"/computed-summary replacement either -- that would be exactly the accidental
complexity CLAUDE.md says to avoid; a per-stage value is one dict lookup away
(`stages[stage].temperature`). `OutputMode` (new enum: `TEXT`/`JSON_OBJECT`/
`JSON_SCHEMA`) lives in `design/schemas.py`, not in `orchestrator/providers/`, so
`StageConfig.output_mode` is typed with it directly rather than persisted as a bare
string, and `orchestrator/config.py`/`orchestrator/providers/` import the same enum
rather than risk a second one drifting from it.

**`FailureKind.FATAL` / `AttemptResult.FATAL_FAILURE` (same schema bump).** Needed by
the fail-fast mechanism below. Checked the coupling before adding anything: the three
failure variants of `AttemptResult` map 1:1 onto `FailureKind` via
`_ATTEMPT_RESULT_TO_FAILURE_KIND`, and `_attempt_shape_error` applies a different shape
rule per `AttemptResult` value. Reusing `TRANSPORT` for a fatal error would contradict
that kind's own documented meaning ("retry usually helps" -- false by construction for
a fatal error); falling through to `OTHER_FAILURE`'s catch-all would wrongly let a
fatal attempt carry token counts. Added both the `FailureKind` and `AttemptResult`
member, the map entry, and an explicit shape-rule branch (mirroring
`TRANSPORT_FAILURE`'s: error_message required, no tokens -- rejected before inference)
placed before the catch-all. Both `StageAttempt`/`StageError` and their document-level
twins share these enums, so this one change covers both levels at once -- the
"`StageError`/`DocumentStageError` mirror each other" pattern CLAUDE.md already flags,
gotten right in one pass rather than found missing on one side by a later review.

**`StageCallFatal` -- the one deliberate change to retry behavior.** The original task
instructions for this phase said not to change sequencing/retry/resume/persistence/
issue-identity/`None`-vs-`[]` behavior, and to report any incompatibility rather than
silently changing it. That incompatibility was found and reported: `call_stage`/
`call_document_stage` retried *every* exception type identically, so a provider
adapter's bad-credentials/unsupported-capability error would still burn the full
`max_attempts` retry budget against a request that could never succeed, no matter how
clearly its `StageCallFailed`-vs-something-else exception type was labelled. On review,
this was called out as needing a genuine fix, not just a clearer log message --
`orchestrator/stage_fns.py` gained `StageCallFatal`, and `call_stage`/
`call_document_stage` gained one new `except` branch each (before the existing
`except StageCallFailed`): a fatal failure records exactly one `StageAttempt`/
`DocumentStageAttempt` (`FATAL_FAILURE`, `FailureKind.FATAL`) and `break`s out of the
retry loop immediately, no backoff sleep. `retry_count` on the resulting `StageFailed`
changed from a hardcoded `max_attempts - 1` to `attempt` (the loop variable, in scope
after the loop exits either way) -- identical value on a normal exhaustion, `0` on a
fatal first-try failure, matching `StageFailed`'s existing "`0` means failed on the
first try with no retry" docstring exactly, no new field needed anywhere. Deliberately
narrow: `StageCallFatal`'s own docstring states it is reserved for provider
configuration/capability/authentication errors specifically, not a general early-exit
a stage fn can reach for to skip retries it doesn't feel like doing. See
`design/ORCHESTRATOR_CONTRACT.md`'s new item for this.

**Retry is global, not per-stage (`orchestrator/config.py`'s `RetryConfig`).**
`call_stage`/`call_document_stage` take `max_attempts`/`backoff_seconds` as single
values threaded through one `run_document()` call -- there is no per-stage retry
mechanism in the orchestrator, and building one was out of scope. A per-stage
`RunConfig` retry override would have been accepted by a schema and silently ignored
by the orchestrator -- exactly the shape of gap this task exists to close, not add.
`RetryConfig` is `initial_delay_seconds * multiplier ** attempt`, not
`backoff_base_seconds ** attempt` -- the latter conflates "how long the first wait is"
with "how fast it grows" into one number.

**Rate limits are keyed by `"provider/model"`, and coverage is required, not
optional.** `Throttle.min_interval_seconds` (`orchestrator/pipeline.py`) is keyed by
model string, because a free-tier quota is a fact about the (provider, model) pair,
shared across every stage that hits it. `RunConfig.rate_limits` is one top-level
mapping keyed the same way -- a YAML mapping cannot hold two different values under one
key, so two stages sharing a model authoring different limits is structurally
impossible, not just checked for. `resolve_run_config` additionally requires every
distinct resolved model to be a key in that mapping; the value can be `null`, meaning
deliberately unthrottled, but the key must exist -- a model silently absent from the
mapping used to mean silently-unthrottled-by-omission, indistinguishable from a
deliberate choice. Both are the same instinct: make an ambiguous state impossible to
reach by accident, not merely caught if it happens.

**Prompt hashes are always computed, never authored or inherited.**
`RunConfig.prompts` is a required mapping covering exactly `ALL_STAGES`, one file path
per stage -- no field anywhere accepts a hand-typed `prompt_hash` (the exact staleness
`prompt_fingerprint()` exists to prevent -- "cannot be forgotten"), and no stage falls
back to a shared "default" prompt file, since each of the 8 stages has a structurally
different prompt by nature. This is also `ORCHESTRATOR_CONTRACT.md` item 12's "not
built this phase" gap, now closed: `resolve_run_config` reads each file and computes
`prompt_fingerprint(text)` itself. Paths resolve relative to the YAML file's own
directory (`config_path.resolve().parent`), not the working directory the command
happens to run from, and the resolved, normalized, absolute path is stored on
`ResolvedStageConfig.prompt_path` alongside its hash -- "which file produced this hash"
is recoverable from the persisted resolved config alone.

**`RunConfig` (authored) vs `ResolvedRunConfig` (persisted) are two different models on
purpose.** "Save the fully resolved, non-secret configuration with each run for
reproducibility" means the resolved one, not the authored YAML -- the authored form has
`Optional` overrides and file paths, not hashes; a later reader would have to
re-resolve it (and re-read prompt files that might have since changed) to know what a
run actually used. `write_run_config`/`read_resolved_run_config` operate on
`ResolvedRunConfig` only, mirroring `orchestrator/pipeline.py`'s
`write_document_run`/`read_document_run` pattern (`run_dir / "run_config.json"`, a
sibling of `document.json`). `run_dir_for(resolved) = resolved.output_dir /
resolved.run_id` is the one place a run's directory is computed, so nothing recomputes
it slightly differently elsewhere.

**No `provider` field on `RunMetadata`/`StageConfig`.** This was flagged, not silently
decided either way: the "Run provenance" section above deliberately rejected a
`provider` field, floating a `"provider/model"` string prefix instead *if* ambiguity
ever became real. `RunConfig.defaults.provider`/`StageOverride.provider` make provider
selection a first-class authored value (needed to dispatch to the right adapter and to
reject typos before any API call), but `resolve_run_config`/`to_run_metadata` write the
resolved model into `StageConfig.model` as `"{provider}/{model}"` -- implementing that
section's own stated fallback, not overriding it. `design/schemas.py`'s `RunMetadata`
gained no new field for this.

**Provider adapters (`orchestrator/providers/`) -- capability tables and error
classification are dated and cited, not memorized.** `capabilities.py` (no `requests`
import, so `orchestrator/config.py` can call `supports_output_mode()` during
`resolve_run_config` -- before any API key is read -- without a transitive dependency
on the HTTP layer) is deny-by-default: an (provider, model, output_mode) combination
not found in its tables is rejected even if it might well work. Both tables cite the
exact doc URL and fetch date (2026-08-09) they came from, in-code, next to the table --
not asserted from training-data memory, per CLAUDE.md's "never invent results... mark
anything unverified as unverified." Two fetched Gemini docs pages disagreed with each
other on the error-response body's own shape (one showed uppercase `error.status`
values, the other a different lowercase `error.code` taxonomy with no `status` field at
all) -- rather than trust either over the other, `gemini.py`'s `_classify_gemini_error`
checks both shapes and falls back to a pure HTTP-status heuristic if neither matches.
Groq's structured-output support is asymmetric by what its own docs actually say:
`JSON_OBJECT` mode is documented as broadly available across models (encoded as
allow-by-default, for Groq specifically, not as an exception to the module's general
deny-by-default policy), while `JSON_SCHEMA` (schema-guaranteed) is restricted to a
named, finite model list -- encoded as an allowlist. Every table and mapping is marked
in-code as best-effort and due for re-verification before depending on it much later,
given how fast these APIs move.

**Fail-fast is genuine, but has one honestly-reported remaining gap.**
`StageCallFatal` now actually short-circuits the retry loop (above) -- draft 2 of this
plan had instead raised a differently-named exception that `call_stage` didn't
recognize specially, which was rejected on review for not being fail-fast at all, just
better-labelled retrying. What's *not* fixed, and is recorded here rather than
silently left inconsistent: nothing currently distinguishes "retry the same failed
call" from "the human should be told before any more attempts happen" -- a
`StageCallFatal` still only surfaces via the normal `StageError`/`DocumentStageError`
path, read after the fact. That's an acceptable gap for this phase (the orchestrator
already has no synchronous human-notification channel for any other failure kind
either), not something this task silently introduced.

**CLI `HumanFns` (`orchestrator/human_cli.py`).** `answer_questions_cli`/
`decide_at_cap_cli` take injected `input_fn`/`output_fn` (defaulting to real
`input`/`print`), the same pattern `orchestrator/pipeline.py`'s `Throttle` already uses
for `sleep_fn`/`now_fn` -- not a new idiom. Neither function catches
`EOFError`/`KeyboardInterrupt`; an interruption propagates to the caller as a real
interruption rather than being coerced into a silently-recorded "the human answered
nothing." Under normal operation `answer_questions_cli` always returns exactly one
`RefinerAnswer` per question in the turn (never `[]`) -- `RefinementRound`'s
schema-valid `answers=[]` with `turn` set (contract item 6) describes a *persisted,
resumed* state, not something this function returns on a whim. Both functions loop on
invalid input rather than defaulting to either choice, matching contract item 3: the
cap decision belongs to the human, and a silent default would quietly make it for
them. Neither function is assembled into a `HumanFns(...)` here -- that's the future
run entrypoint's job, keeping terminal I/O swappable for a FastAPI backend later
without touching `pipeline.py`.

## Post-implementation review fixes, two passes (2026-08-09)

Two rounds of code-level review, after the section above was built and merged, found
real defects that plan review couldn't have caught (they only exist once real code
exists to read). Recorded here because none of them made it into this file the first
time -- a gap in itself, closed by this section.

**`StageCallPartial` -- a third stage-fn exception, for a case that isn't fatal or
plain-retryable.** A 200 HTTP response can still fail to produce usable output (Gemini
safety-filtering removes every candidate; a response body is truncated after usage
accounting) -- and unlike a rejected request, tokens were genuinely spent. Neither
existing exception fit: `StageCallFailed`/`StageCallFatal` both mean "rejected before
inference," and their `AttemptResult`/`FailureKind` shape rules forbid token counts
accordingly -- forcing this case through either would silently drop real spend from the
record. `orchestrator/stage_fns.py` gained `StageCallPartial(message, prompt_tokens,
completion_tokens)`; `call_stage`/`call_document_stage` catch it, record
`AttemptResult.OTHER_FAILURE` (which already permitted, without requiring, token
counts -- no schema change needed) with the preserved counts, and retry normally, same
as `StageCallFailed` -- a malformed response on one attempt doesn't mean the next one
will be. `orchestrator/providers/gemini.py`/`groq.py` raise it specifically when a 200
response's usage accounting parses but its content doesn't; when usage ALSO doesn't
parse, there is nothing to preserve and it's an ordinary `StageCallFailed`.

**Checkpointing before a human-interaction call, then again after -- two passes, not
one.** `run_document`/`resume_document` now pass an optional `checkpoint` callback
(`write_requirement_run`, partially bound to `run_dir`) through `run_requirement` into
`_run_refine_loop`. First pass: fired right before `human_fns.answer_questions`,
persisting the round with its `turn` recorded, no answers yet -- so an `EOFError`
(terminal closed) or `KeyboardInterrupt` there doesn't lose the classifier/quality-
checker/questioner calls that already succeeded; a resume re-asks the human instead of
redoing them. **Second pass, added on review** (the first pass alone was an incomplete
fix): the SAME round, checkpointed AGAIN right after `answers` comes back, before the
`refine_rewriter` call. `KeyboardInterrupt` is not scoped to terminal-input calls the
way `EOFError` effectively is -- it can land during any line of code, including the
HTTP request inside `call_stage(refine_rewriter, ...)` -- and without this second
checkpoint, an interruption there lost the human's already-collected answer, the
expensive-to-redo part of the round (a person's time, not just an API call). Both
checkpoints reuse the exact same, already-tested resume machinery
(`resume_at`/`_run_refine_loop`'s `pending_round` logic): a round with `turn` set and
empty `answers` resumes by re-asking; a round with `turn` AND non-empty `answers`
resumes straight at the rewriter, answers already in hand. Neither checkpoint changes
what gets *returned* by `_run_refine_loop`/`run_requirement` -- purely an
observational side-channel for persistence, default `None`, fully backward compatible.
Regression tests: `orchestrator/test_harness.py::test_interruption_during_human_input_
checkpoints_and_resumes` and `::test_interruption_after_answers_checkpoints_and_resumes`.

**Provider REST payload corrections, verified against docs, not against the existing
tests.** `orchestrator/providers/gemini.py`'s `generationConfig` fields were corrected
from snake_case to the real camelCase (`responseMimeType`), and -- caught on a SECOND
review pass, after the first correction still got the schema field wrong -- from
`responseSchema` to `responseJsonSchema`. The first pass had explicitly claimed
`responseJsonSchema` "does not appear" anywhere, based on several WebFetch summaries of
the same long prose reference page, none of which surfaced it. The correction came from
checking a generated SDK reference instead
(`googleapis.github.io/js-genai/release_docs/interfaces/types.GenerateContentConfig.html`),
which states plainly: `responseSchema` accepts only an OpenAPI 3.0 *subset* and
suggests `responseJsonSchema` "if `response_schema` doesn't process your schema
correctly"; `responseJsonSchema` accepts full JSON Schema and requires `response_schema`
be omitted when set. A Pydantic model's `model_json_schema()` routinely emits
`$defs`/`$ref` for nested or recursive models -- exactly the shape an OpenAPI-3.0-subset
field isn't guaranteed to accept -- so `responseJsonSchema` is the correct field for
this project's use, not merely the newer-sounding one. `orchestrator/providers/groq.py`'s
`response_format.json_schema` was corrected from a flat `{type, strict, schema}` to the
documented nested shape (`strict`/`name`/`schema` all inside `json_schema`), and a
`name` field (required, previously missing) is now derived from the schema's own
`title` or a fixed fallback. Groq's best-effort (`strict: false`) JSON Schema mode's
documented HTTP 400 "Generated JSON does not match the expected schema" is now
classified `StageCallFailed` (retryable, per Groq's own guidance), not fatal --
scoped narrowly to that exact message, that exact mode, and non-strict models only, so
an unrelated 400 or the same message on a strict (schema-guaranteed) model still fails
fast. Every corrected shape is asserted directly in
`orchestrator/test_providers.py`, independent of whatever the implementation happens to
do -- the whole point, after a first version of that file asserted the wrong shapes and
would have passed against a same-shaped-but-wrong implementation forever.

**`call_stage`/`call_document_stage` reject `max_attempts < 1` up front.** Previously,
`max_attempts=0` made `range(max_attempts)` empty, so the loop body -- including the
`attempt` variable the final `raise StageFailed(..., retry_count=attempt)` reads --
never ran, a `NameError` instead of a catchable failure. Both functions now raise
`ValueError` immediately if `max_attempts < 1`, before touching the loop at all.

**`ResolvedStageConfig`/`ResolvedRunConfig` validate as strongly as the authored
config, not less.** `read_resolved_run_config` loads a persisted `run_config.json`
straight through `ResolvedRunConfig.model_validate_json` -- it never passes through any
of `RunConfig`'s own validators. A first version left `ResolvedStageConfig.temperature`/
`timeout_seconds` unbounded and `ResolvedRunConfig.max_revisions` unbounded, on the
(wrong) theory that `RunConfig`'s own bounds already covered it -- true only for a
freshly-resolved config, not a hand-edited or corrupted file loaded later. Bounds
restored, plus two new validators: `stages` must cover exactly `ALL_STAGES`, and
`rate_limits` must match the resolved models EXACTLY (not just be a superset) -- an
entry for a model nothing uses is either a typo silently not applying to the model it
was meant for, or a stale leftover, and both are worth rejecting rather than carrying
forward quietly.

**`orchestrator/test_stage_fns.py` now verifies Protocols against REAL pipeline call
sites, not a hand-written stand-in.** The first version compared each Protocol's
`__call__` signature against a stub typed BY HAND to match it -- which verifies "does
this Protocol match a stub I wrote to look like it," not "does this Protocol match what
`pipeline.py` actually calls." A transcription error made once, writing both from the
same (possibly wrong) understanding of a call site, would pass on both sides
undetected. The fix drives one real `run_document()` call through every one of the ten
`StageFns`/`HumanFns` fields via recording fixtures, capturing pipeline.py's actual
positional args at each real call site, and checks each capture's arity and each
argument's runtime type against the Protocol via `typing.get_type_hints()` (not raw
`inspect.signature()` annotations, which are unresolved forward-reference strings under
`from __future__ import annotations`). The hand-stub comparison is kept as a secondary,
honestly-scoped layer (it still catches a Protocol's own internal inconsistency) -- it
just no longer stands in for the real-call-site check on its own.

## Real stage functions -- cross-stage validation (2026-08-09)

`orchestrator/stages.py` (the 8 real LLM-calling stage functions) surfaced two classes
of bug in the already-built, already-tested `pipeline.py`/`stage_fns.py` -- neither
found by inspection, both found by asking "what does a REAL stage function, wired to a
real model, actually need to guarantee its own output, given only what its Protocol
hands it?" the same way item 14 (`ORCHESTRATOR_CONTRACT.md`) was found by building the
harness rather than by reading the code.

**Bug 1 (blocking): `RefineQuestionerFn`/`RefineRewriterFn` could not know the round
number.** `RefinerTurn.revision_number`/`RefinedRequirement.revision_number` are
checked against the orchestrator's own round counter `n` inside
`RefinementRound._round_is_coherent` -- but neither Protocol's given arguments
(`Requirement`/`QualityReport` for the questioner; `Requirement`/`RefinerAnswer[]` for
the rewriter) contain `n` anywhere, and neither does anything reachable from them. Round
1 happened to work by accident (`RefinerTurn.revision_number` defaults to `1`); round 2
onward could never work, for any implementation, real or fixture -- confirmed by
tracing `test_harness.py`'s own fixtures, which only ever "work" because the *test
author*, who already knows the round number when writing the fixture, hardcodes the
right value into each scripted response. A real stage fn has no such foreknowledge.
Fixed by adding `revision_number: int` to both Protocols (`orchestrator/stage_fns.py`)
and threading `_run_refine_loop`'s own local `n` into both call sites -- the value was
already computed there, just never passed to the callable that needed it.

**Bug 2 (probabilistic, but real): several cross-stage agreements were checked only at
final record assembly, unguarded.** Contract item 15 fixed this exact shape for
`requirement_id`/`doc_id` specifically -- `call_stage`/`call_document_stage` check
immediately after `model_validate` succeeds, before returning, so a mismatch becomes an
ordinary retried `VALIDATION` failure instead of an uncaught `pydantic.ValidationError`
surfacing deep inside `RefinementRound`/`RequirementRunRecord`/`DocumentRunRecord`
construction after later stages already ran and were paid for. That fix was never
generalized to every OTHER field with the same shape. A full audit of every
`model_validator` in `design/schemas.py` found the complete set (bucket labels are
"which of the three groups every validator fell into," not schema terms):

- **Bucket A -- self-contained, already safe.** Both sides of the check live in the
  same object from one stage call (e.g. `TestPlan._cases_cover_this_requirement`,
  `TestStrategy._techniques_are_eligible`). Caught immediately inside `call_stage`'s
  existing `model_cls.model_validate(...)` already -- no new work needed.
- **Bucket B -- orchestrator-internal bookkeeping.** Built entirely from `pipeline.py`'s
  own control flow (attempt logs, outcome labels, round text-chaining). A bug here is an
  orchestrator bug and correctly still crashes, per `FailureKind.OTHER`'s own docstring
  ("must never be used for a bug in the orchestrator's own control flow").
- **Bucket C -- genuine cross-stage risk, needed the new mechanism** (13 rows,
  enumerated in the table below): compares a freshly-parsed stage output against an
  *earlier* stage's output, or against a full id-set no single object holds.
- **Bucket D -- a different shape, not fixable the same way.** Document-wide test-case
  id uniqueness (`DocumentRunRecord._references_resolve`'s whole-document
  `_require_unique`) can't be checked inside one requirement's `call_stage` invocation
  at all -- no single call has visibility into another requirement's output. Solved
  differently (see "TC-id convention" below), not via `extra_check`.

**The mechanism: `extra_check`, one new optional parameter on both `call_stage` and
`call_document_stage`.** `ExtraCheck = Callable[[BaseModel], Optional[str]]` -- `None`
means the parsed object is fine, a string is a failure message. Run immediately after
the existing `requirement_id`/`doc_id` check passes, before returning `SUCCESS`; a
non-`None` return is recorded exactly like any other `VALIDATION_FAILURE` (tokens
preserved, normal retry/backoff, an exhausted `StageError`/`DocumentStageError` gets
`kind=VALIDATION`). Default is a no-op lambda, so every one of `test_harness.py`'s ~25
existing direct `call_stage`/`call_document_stage` calls kept working unchanged --
confirmed by grep before adding the parameter, not assumed. Each of `pipeline.py`'s 9
real call sites (2 in `run_document_stages`, 2 in `retry_document_stage`'s stage-keyed
dispatch, 5 in `_run_refine_loop`/`run_requirement`) now builds its own closure from
data already in scope there:

| Stage | Checked |
|---|---|
| `check_consistency` | every `ConsistencyConflict.requirement_ids` names only ids in the document |
| `map_dependencies` | every `DependencyLink.from/to` names only ids in the document |
| `check_quality` | every `Issue.related_requirement_ids` excludes this requirement's own id, and names only other known ids |
| `refine_questioner` | `RefinerTurn.revision_number == n`; each question's `issue_id`/`issue_category` matches a real issue raised this round; no duplicate question id within the turn |
| `refine_rewriter` | `RefinedRequirement.revision_number == n`; `original_text == text_checked`; `answers_used` is a subset of this round's actual answers |
| `select_strategy` | `TestStrategy.system_type is Classification.system_type` |
| `generate_tests` | every `TestCase.technique_used ∈ strategy.techniques`; every `TestCase.requirement_ids` names only known document ids; every `TestCase.id` follows the required prefix convention (below) |

One new required parameter, `known_requirement_ids: frozenset[str]` (keyword-only),
had to be threaded into `_run_refine_loop` -- it didn't previously receive the document's
id set at all, needed for `check_quality`'s row. `run_document_stages`/
`retry_document_stage` already had `requirement_set` directly, no new parameter needed
there.

**Values are never silently corrected.** `extra_check` only ever returns a message or
`None` -- it never mutates the parsed object. A model that disagrees with an earlier
stage either retries or exhausts into a recorded, countable `StageError`; it is never
quietly patched to look consistent. This was floated (overwrite `TestStrategy.
system_type` with the Classifier's value, since it's mechanically always knowable) and
explicitly rejected: contract item 15 already rejected the identical move for
`requirement_id` ("option A -- overwrite silently everywhere"), specifically because it
destroys the "how often does this model produce internally-inconsistent output"
measurement this thesis wants. Applying that same silent fix here, to a different
field, would be the exact same mistake with new paint. Every one of the 13 rows above is
tested twice in `orchestrator/test_harness.py::test_cross_stage_extra_checks` -- an
invalid attempt followed by a corrected retry (ends cleanly, no `StageError`, both
attempts token-accounted) and all-invalid exhaustion (`StageFailed(kind=VALIDATION)`,
every attempt token-accounted, and critically: returns normally, does not raise a bare
`pydantic.ValidationError` up through the caller the way it would have before this bug
was found).

**TC-id convention: `TC-<len>-<id>-<suffix>`, not `TC-<id>-<suffix>`.** Bucket D's
whole-document test-case-id collision can be prevented structurally at the SOURCE
(the Test Generator's own output) rather than caught after the fact, but only if the
prefix scheme is genuinely unambiguous. A first draft used `TC-{requirement_id}-`,
rejected on review: a requirement id can be a prefix of another's (`"REQ-1"` and
`"REQ-1-X"`), so a case in `"REQ-1"`'s namespace suffixed `"X-5"` and a case in
`"REQ-1-X"`'s namespace suffixed `"5"` are the byte-identical string
`"TC-REQ-1-X-5"` -- the exact collision the convention exists to prevent, just moved
from "detected too late" to "not detected at all." Announcing the id's length up front
(`test_case_id_prefix()`, `orchestrator/pipeline.py`) removes the ambiguity the same way
a netstring removes it from length-prefixed byte strings: for two different `(len, id)`
pairs to produce the same `TC-<len>-<id>-` text, the length digits would have to match
(forcing equal length, hence identical content) or differ (visible at the first
differing character) -- either way, two different ids can never produce the same
prefix. Combined with `RequirementSet._ids_are_unique` (document-wide) and
`TestPlan._case_ids_are_unique` (already-existing, bucket A, within one plan), this
makes cross-requirement collision structurally impossible, not merely checked for --
proven, not just asserted, in
`test_harness.py::test_test_case_id_prefix_avoids_requirement_id_ambiguity` (constructs
the naive scheme's actual collision, then shows the adopted scheme's prefixes are
provably distinct and non-prefixing). `test_case_id_prefix` lives in `pipeline.py`
(where the enforcing `extra_check` lives) and is imported, not duplicated, by
`stages.py` (which needs the identical literal string to embed in the Test Generator's
prompt) -- one function, two call sites, so the enforced convention and the documented
one can never drift apart.

## Real stage functions -- prompt provenance (2026-08-09)

**The gap.** `resolve_run_config` (`orchestrator/config.py`) computes
`StageConfig.prompt_hash` from `prompt_path.read_text()` alone -- a hash of the prompt
FILE, nothing else. A first draft of `stages.py` built each stage's actual prompt by
appending the JSON output schema and the untrusted-content delimiters/warning in Python
code at call time, on top of the file's own instructional text. That is real,
model-facing prompt text `prompt_hash` would never see -- two runs sharing an identical
`prompt_hash` could, under that design, send genuinely different prompts to the model if
either unrecorded piece ever changed, silently defeating the exact guarantee
`prompt_fingerprint()` exists for ("two runs labelled the same but hashed differently
are visibly mislabelled" -- the inverse failure, hashed the same but actually different,
was never guarded against because nothing generating the hash's input was ever supposed
to be incomplete).

**Fix: the prompt file IS the complete static prompt.** Every one of the 8
`orchestrator/example_prompts/*.txt` files now contains everything that does not vary
per call: stage instructions, the untrusted-content warning and its
`<<<UNTRUSTED_CONTENT_START>>>`/`<<<UNTRUSTED_CONTENT_END>>>` delimiter markers, and the
literal, pretty-printed output-schema JSON between
`<<<OUTPUT_SCHEMA_START>>>`/`<<<OUTPUT_SCHEMA_END>>>` markers -- captured once, by
running `model_cls.model_json_schema()` at authoring time and pasting the result in, not
generated at request time. `stages.py` never appends anything: it only replaces
`<<<FIELD:name>>>` tokens already present in the file with per-call dynamic values
(a `Requirement`'s text, a document's requirement list, a computed id prefix). The
prompt sent to the model is therefore always `template.replace(placeholders)`, verified
by `_render_prompt`'s own contract (below) -- never `template + extra_static_text`.
Consequence: prompt content is now provably identical across `TEXT`/`JSON_OBJECT`/
`JSON_SCHEMA` output modes for a given stage/version, since `output_mode` only changes
the separate `response_schema`/`schema_name` parameters passed to
`ProviderAdapter.complete()`, never the prompt text itself.

Two places the schema now legitimately appears, reconciled by a test rather than by
sharing code: (1) the static JSON block in the file, hashed, is what the model reads in
every mode; (2) `model_cls.model_json_schema()` computed live is used only as the
`JSON_SCHEMA`-mode API parameter, never as prompt text. `test_stages.py::
test_embedded_schema_matches_pydantic_schema` extracts (1) via the markers and asserts
it equals a fresh computation of (2) for all 8 stages -- a `design/schemas.py` change
with no matching prompt-file edit fails this test immediately, and since the file must
then change to fix it, `prompt_fingerprint()` changes too: schema drift cannot happen
without also producing a new, honestly-labelled prompt identity.

**`_render_prompt`: one pass over the ORIGINAL template, validated against the
template, never the rendered result, raising `ValueError` not `assert`.** Three
rejected designs, in the order they were tried:

1. *Sequential `str.replace()` per field.* Rejected: an untrusted field value can
   itself contain text shaped like a DIFFERENT placeholder (`<<<FIELD:other_field>>>`).
   Substituting field A first inserts that literal text; a later, separate
   `.replace()` call for field B would then find and wrongly substitute it -- the
   order of substitution becomes an attacker-controllable variable. `re.sub` with a
   callback, in one pass over the original `template` string, never re-scans inserted
   replacement text for further matches, by construction -- this is not a performance
   optimization, it is the actual safety property. Proven, not just asserted, by
   `test_stages.py::test_render_prompt_one_pass_no_double_substitution`.
2. *Validating "did every placeholder get resolved" by scanning the rendered output.*
   Rejected: untrusted content can legitimately contain text that looks like a
   placeholder or a marker (that's the whole point of the adversarial tests below).
   Scanning `rendered` for leftover `<<<...>>>`-shaped text would either wrongly flag
   inert data as an unresolved placeholder, or -- worse, if "found one, so substitute
   it" logic were ever added -- wrongly treat untrusted content as template syntax on
   a notional second pass. `_render_prompt` discovers every placeholder name from
   `template` ONCE, validates that discovered set against the supplied fields (both
   directions -- a placeholder with no field, and a field with no placeholder, are both
   `ValueError`s), and only then substitutes -- so "left unresolved" is impossible by
   construction, not something checked for afterward. A separate marker-shaped-token
   scan (`_ANY_MARKER_RE`) additionally catches a typo'd/malformed marker in the
   TEMPLATE itself (e.g. `<<<FEILD:x>>>`) that would otherwise sit there forever,
   neither substituted nor erroring -- also validated against `template`, never
   `rendered`.
3. *`assert` for a missing placeholder/field.* Rejected outright, per this project's
   general practice of not using bare `assert` for anything that isn't a pure internal
   invariant check with test coverage proving it fires -- a missing field, an unused
   field, and an unrecognized marker-shaped token in the template are all ordinary,
   expected-to-happen-sometimes authoring mistakes, not "should never happen" bugs, so
   each raises `ValueError` with a message naming exactly what's wrong.

**`_json_dynamic`: JSON-encode, then escape `<`/`>` as their Unicode JSON escape
sequences, applied uniformly to every dynamic value, no exceptions.** Even with one-pass substitution
(above), a value could still contain the LITERAL text of a real marker
(`<<<UNTRUSTED_CONTENT_END>>>`) -- harmless to `_render_prompt`'s own logic (it never
re-scans replacement text) but not obviously harmless to the MODEL reading the final
prompt, which has no such guarantee about how it parses lookalike delimiters. Every
dynamic value is JSON-encoded (so it's syntactically quoted data) and then has its `<`/
`>` characters replaced with the six-character Unicode JSON escape sequences for them
(backslash, u, 0, 0, 3, c for `<`; backslash, u, 0, 0, 3, e for `>`) -- both are
valid JSON string escapes, so `json.loads` (or a model reading the prompt) recovers the
exact original text, but no raw `<`/`>` character survives in the rendered prompt to be
mistaken for `<<<...>>>` syntax by anything scanning for it, human or model. Applied to
EVERY dynamic value, including ones that look obviously safe (a bare requirement id) --
"this field is surely fine unescaped" is exactly the assumption that stops being true
the next time the helper is reused for a new field, so there is deliberately no
per-field judgment call to get wrong. `test_stages.py::
test_adversarial_requirement_text_cannot_break_out` drives three payloads
(`<<<FIELD:doc_id>>>`, `<<<UNTRUSTED_CONTENT_END>>>`, `<<<OUTPUT_SCHEMA_START>>>`)
through a real requirement's text and a real factory function, and proves: the
untrusted-content and output-schema marker pairs each still appear exactly once in the
rendered prompt (the payload created no second, fake pair), no `<<<FIELD:` token is
left unresolved, exactly 4 marker-shaped tokens exist in the whole rendered prompt (the
2 real pairs, nothing else), and the payload itself survives only in its escaped,
inert form inside the untrusted-content section.

**Duplication accepted, deliberately, as the cost of the fix.** The untrusted-content
warning paragraph is now near-duplicated across all 8 prompt files rather than shared
from one Python constant, because anything living only in Python code is invisible to
`prompt_hash` by definition -- sharing it at runtime would silently reintroduce exactly
the gap this section closes. `test_stages.py::
test_golden_safety_sentence_present_in_every_template` keeps one canonical reference
sentence (the safety-critical core, not the whole paragraph -- the surrounding wording
legitimately differs slightly per stage, e.g. `refiner_rewriter.txt` describes human
answers, not "an earlier pipeline stage") and asserts it survives, verbatim, in every
file -- a test fixture only, never touched by `stages.py`'s runtime rendering path, so
it cannot itself become a second source of truth the hash misses.

**Groq + `JSON_SCHEMA` rejected for v1 at the capability table, not inside
`stages.py`.** Every stage's `response_schema` is `model_cls.model_json_schema()`,
unadapted to Groq's own structured-outputs constraints (strict mode's
`additionalProperties`/`required` conventions -- `orchestrator/providers/
capabilities.py`'s own citations). A first version rejected the combination inside
`stages.py`'s `_complete_and_parse`, per-stage, at call time -- caught on review as the
wrong layer: `resolve_run_config` (`orchestrator/config.py`) already calls
`supports_output_mode()` for every stage before any run starts, so a rejection living
one layer further out (inside a single stage fn) let every EARLIER stage in the same
run spend real tokens before the run ever reached the one that would fail. Moved into
`supports_output_mode()` itself instead: `provider == "groq" and output_mode is
JSON_SCHEMA` now returns `False` unconditionally, regardless of model. One function,
already called by both `resolve_run_config` (primary, pre-run) and every adapter's own
`complete()` (defensive, secondary) -- the same shared gate item 12's prompt-hash
validation and the rest of `resolve_run_config`'s checks already run through, so a YAML
config requesting this combination now fails at config-resolution time, before any API
key is read, same as every other unsupported (provider, model, output_mode) triple.
`stages.py` carries no rejection of its own -- duplicating a check the adapter already
performs defensively would just be a second place for the two to drift apart. The
allowlist tables and `groq_json_schema_is_strict()` in `capabilities.py` are left in
place, unchanged, as accurate documentation of what Groq's docs actually say -- not
deleted, just not reachable through `supports_output_mode()` for v1. Three
`test_providers.py` tests that drove a previously-allowlisted model's JSON_SCHEMA
request all the way to a successful/mismatched response (to exercise request-body-shape
and error-classification code paths beneath the capability check) were removed, not
adapted -- that code path is now genuinely unreachable through the public API, and
CLAUDE.md's "don't write a check that can't fire" applies equally to a test exercising
a scenario that can't occur.

## CLI resume wiring (2026-08-10)

`orchestrator/cli.py` gained a `resume` subcommand alongside the existing `run` one --
`orchestrator.pipeline.resume_document` existed and was harness-tested
(`test_resume_positions` etc.) since the control-flow phase, but had no CLI path calling
it. See `design/ORCHESTRATOR_CONTRACT.md` item 18 for the two decisions this required
(why `resume` takes only a run directory, and why a drifted prompt file makes it refuse
rather than warn) -- this note only adds what the contract item doesn't cover: the CLI
shape itself, and what was rejected.

**Why a subcommand, not a flag on the existing invocation.** An earlier sketch kept one
command (`python -m orchestrator.cli CONFIG.yaml INPUT.json [--resume]`) and had the
existing `run_dir.exists()` guard branch on the flag: refuse unless `--resume` was
passed, then resume if it was. Rejected: this still takes a config/input pair for the
resume case, and doing anything with them -- even just to compute `run_dir_for` and find
the existing directory -- reopens exactly the "did the config change since the run
started" question item 18 has to answer for the *prompt* file specifically. It would also
have to re-derive `run_dir` from a possibly-different config and then discover it happens
to collide with an old one, rather than the operator naming the directory to resume
directly. Two subcommands, `resume` taking a bare `RUN_DIR` and nothing else, sidesteps
the question instead of answering it -- there is no config path fresh enough to disagree
with anything.

**Why `resume` re-checks `read_document_run` even though `resume_document` (pipeline.py)
does that anyway.** `resume_document` only reads the run directory *after*
`orchestrator/cli.py` has already constructed every provider adapter for it (`main`'s
existing `EXIT_CONFIG_ERROR`-before-any-adapter rule, unchanged from the `run` path).
Reading it once, deliberately, in `cli._do_resume`, before adapter construction, is what
keeps that rule true for `resume` too -- a foreign requirement file (item 18) is
discovered before any adapter exists, not after, even though `resume_document` would
have discovered it a few lines later anyway on its own read.

**Exit codes: no new one.** `resume` reuses all four existing codes unchanged -- a
prompt-provenance refusal and a missing/foreign run directory are both
`EXIT_CONFIG_ERROR`, matching every other "discovered before any adapter is built"
failure on the `run` path. `_do_run`/`_do_resume` share a `_finish` helper for
everything after adapter construction (build `StageFns`, `Throttle`, retry args, call
the pipeline function, translate the outcome to an exit code) specifically so the two
subcommands cannot drift apart on that translation -- see `_finish`'s own docstring in
`orchestrator/cli.py`.

Test coverage: `orchestrator/test_cli.py` -- a resume that completes an EOFError-
interrupted run (re-asks the human, does not repeat already-succeeded calls), a missing
run directory, a prompt-drift refusal (edits a local copy of one stage's prompt file
between `run` and `resume`, confirmed not to touch the shared `example_prompts/`
fixtures other tests depend on), a hand-constructed foreign-`run_id` requirement file
(proves the existing schema check, not new code, catches it), and a resume of an
already-`COMPLETED` run (zero stage calls, exit 0). The prompt-drift guard
(`_prompt_provenance_mismatches`) was mutation-tested by hand: inverting its `!=` to `==`
turns `test_resume_prompt_drift_rejected` red.

## Multi-key rotation for free-tier rate limits (2026-08-10)

**Problem, precisely.** Free-tier Gemini/Groq keys don't bill past quota, they hard-cut
(429). Running several experiments back-to-back hits that cut well before any cost is
incurred -- "will bankrupt me" was the trigger phrasing, but the actual failure mode is a
stalled run, not a bill. The user holds several accounts' worth of keys for both
providers and wanted them used as a fallback chain instead of one key stalling the whole
run.

**Design: a wrapper, not a pipeline change.** `orchestrator/providers/rotating.py`'s
`RotatingKeyAdapter` implements the same `ProviderAdapter` Protocol
(`orchestrator/providers/base.py`) as `GeminiAdapter`/`GroqAdapter` and wraps a list of
them. Nothing above the adapter layer -- `orchestrator/stages.py`, `StageFns` wiring,
`pipeline.py`'s retry loop -- needed to change or even know rotation exists. This is the
whole reason `ProviderAdapter` was a `Protocol` and not a concrete base class already;
this is the first thing to actually exercise that.

**Rotates on `StageCallFailed` only, never `StageCallFatal`/`StageCallPartial`.** Both
`gemini.py` and `groq.py` already classify 429/`RESOURCE_EXHAUSTED`/`rate_limit_exceeded`
(and plain transport failures -- timeout, 5xx) as `StageCallFailed`; that's the exact
signal to switch keys on. `StageCallFatal` (bad credentials, capability mismatch,
malformed request) is a property of the *request*, not the key -- equally fatal on every
other key, so rotating past it would burn the whole key list reproducing the same error
N times before raising, and misreport a single config bug as "N keys exhausted."
`StageCallPartial` (inference happened, tokens spent, output unusable) isn't a key
problem either. Both propagate immediately, same as a bare single-key adapter would.

**Sticky index, not round-robin-per-call.** Once a key is exhausted for the rest of its
quota window, retrying it first on every subsequent `complete()` call wastes one request
per call just to reconfirm what's already known. `RotatingKeyAdapter` remembers which key
last succeeded and starts there; only wraps forward through the list when the current key
starts failing.

**Env vars: new plural names, not a shared name reinterpreted.** `GEMINI_API_KEYS`/
`GROQ_API_KEYS` (comma-separated) are new and separate from `GeminiAdapter.from_env`'s/
`GroqAdapter.from_env`'s existing singular `GEMINI_API_KEY`/`GROQ_API_KEY`. A one-key
setup keeps working unchanged; setting the plural var is what opts a provider into
rotation (`orchestrator/cli.py`'s `_gemini_adapter_from_env`/`_groq_adapter_from_env`
check the plural var first, fall back to the existing single-key `from_env()`
otherwise). Rejected: silently accepting a comma inside `GEMINI_API_KEY` itself --
ambiguous with a key that legitimately contains no comma today but might collide with a
provider that later uses one in a key's own format, and it would make "one key" and
"many keys" indistinguishable from the variable name alone.

**Deliberately NOT done here -- read this before extending it:**
- **No per-key rate-limit tracking.** `Throttle` (`orchestrator/pipeline.py`) still paces
  by `"provider/model"` as a single shared bucket (`RateLimitConfig` in
  `orchestrator/config.py`), unaware that N keys means roughly N times the real capacity.
  Simplest fix, left to the operator: multiply `requests_per_minute`/`tokens_per_minute`
  in the YAML by the key count. Making `Throttle` key-aware (a separate bucket per key,
  informed by which key `RotatingKeyAdapter` is currently on) would pace more precisely,
  but is real added complexity for a problem the YAML multiply already solves adequately
  -- not worth it unless the manual multiply is measured to pace wrong in practice.
- **No cross-provider fallback.** Gemini keys don't fall back to Groq keys or vice versa;
  each provider rotates only within its own key list. `StageDefaults.provider`/
  `StageOverride.provider` in `orchestrator/config.py` already pick a provider per stage
  deliberately -- silently substituting a different provider on exhaustion would change
  which model actually produced a stage's output without that being visible anywhere in
  `ResolvedStageConfig`/`RunMetadata`.
- **ToS risk is the user's call, not a code decision.** Using several personal/institutional
  accounts' keys to aggregate free-tier quota may or may not be within a given provider's
  terms of service; this was flagged, not resolved, before building this. Not re-litigated
  here -- if it needs revisiting, revisit the ToS question itself, not this file.

Test coverage: `orchestrator/test_rotating.py` -- rotates past `StageCallFailed` to a
working key; does not rotate past `StageCallFatal`/`StageCallPartial` (second adapter's
`.calls` stays 0, proving it was never invoked, not just that the right exception came
back); raises the *last* key's failure when every key is exhausted; sticky-index behavior
(second call starts at the key that succeeded last, first key not retried); empty adapter
list rejected in `__init__`; `from_env` comma-parsing (whitespace-trimmed, trailing comma
ignored) and its missing/all-blank-var `RuntimeError` cases.

## System changes to make before the evaluation freeze (2026-08-15)

**Scope of this entry: changes to the system under test only.** How the system will be
measured -- answer policy, baseline arms, repeat runs, the freeze record -- is deliberately
*not* here. It lives in `docs/EVALUATION_PROTOCOL.md`. The two are kept apart on purpose:
everything in this entry must be complete and frozen *before* anything in that document
executes, and mixing them makes it impossible to show that the system was not tuned during
evaluation.

**What changed, and it changes the filter.** A full re-evaluation is planned from scratch.
Every deferral in this section justified by *"it changes the system under test
mid-evaluation and would invalidate prior runs"* no longer holds -- the prior runs are
being superseded on purpose, so the freeze point moves forward to whenever the work below
lands. That argument is retired as of this entry; do not cite it again without checking
where the freeze point now sits.

The replacement filter, applied to every item below: **can this be added after the frozen
run without re-spending the API budget and the operator's hand-answering effort?** If no,
it is built now. If yes, it waits.

### S1 -- Extraction for the reserved corpora

`orchestrator/extract_document.py` reads only the already-JSON-shaped
`datasets/requirements_dataset.json`. The evaluation corpora are not in that shape: PURE's
79 documents (`datasets/pure-full/`) are PDF/DOC/HTML, and Dalpiaz, PROMISE NFR and Riaz
each have their own (`datasets/EVALUATION_DATASETS.md`). Nothing can be evaluated until
requirements come out of those as `RequirementSet`.

**This does not change pipeline behaviour, but it is frozen at the same point and for the
same reason:** if extraction changes, the inputs change, and every result computed before
the change becomes non-comparable.

**One methodological choice inside it that must be recorded here, not decided silently:**
how requirement boundaries are determined in unstructured documents. That is a decision
with a threat-to-validity attached, not an implementation detail.

**Fold in the corruption scan.** Known Limitation 5 records `1998 - themas.xml` having
`<=` flattened to `=` during PDF-to-XML extraction -- `LO = T_LT` is a corrupted
inequality, not domain notation. The 18-file annotated subset was scanned and the damage
was confined to that one document; the 79-document corpus has never been parsed by
anything in this project, so nothing is known there. Scan for the same three signatures
(`X = Y = Z` comparison chains, `T_LT`-shaped underscore tokens, surviving Unicode math)
and report per document. Do not repair silently.

**Exclusions carried forward:** `1998 - themas.xml` and `2007-ertms.xml` are marked spent
for design purposes and must not appear in the evaluation subset.

### S2 -- Capture the operator's system-type label alongside the model's

The Classifier's accuracy currently has **n=0**, because no human label has ever been
collected. It cannot be reconstructed after a run -- this is a capture decision, and
missing it means the Classifier's contribution stays unmeasurable for the whole thesis.

Record both labels per requirement, with provenance for who set each. Per Known
Limitation 2, the audit trail is the only real mitigation this design has, and it degrades
if provenance is implicit.

**Constraint, from the discussion recorded under Known Limitation 9** ("Should the human
confirm or override the system type?"): this must not silently become a third blocking
interaction point in `HumanFns` unless that is separately justified. Capturing a label for
comparison is not the same as letting the human override the pipeline's label, and only
the first is being adopted here.

### S3 -- Phase the pipeline

Adopts **option B** under Known Limitation 7, not the advisory post-pass (option A).

Today `run_document_stages` runs the Consistency Checker and Dependency Mapper once, on
the original text, and `run_requirement` computes `relevant_conflicts` /
`relevant_dependencies` once from that report and holds them constant through every
refinement round and into strategy selection and test generation. A rewrite that
introduces a new conflict or dependency is never seen. This was observed live:
`PURE-THEMAS-R6-P` was corrected by the operator, the Rewriter applied the fix, and the
Quality Checker flagged `inconsistent` again in the next round from the stale report --
the loop terminated only because the operator set `user_confirms_resolved`.

**Target shape:** pass A classifies, quality-checks and refines every requirement;
document-level analysis re-runs on the refined set; pass B does strategy selection and
test generation from the fresh reports.

**Four constraints, each one a place this goes wrong:**

1. **Keep both generations of reports -- do not overwrite.** The diff between the initial
   and final consistency/dependency picture is the frequency number Known Limitation 7 has
   been asking for. Overwriting destroys a result.
2. **Update `orchestrator/test_harness.py::test_resume_positions` first.** That executable
   spec exists precisely because the resume spec drifted once before. Change the test,
   watch it fail, then make it pass.
3. **Model the second analysis as a distinct document-stage phase, not a re-run of the
   first.** The moment a "this analysis is stale, redo it" state is introduced,
   `resume_document`'s position logic and the terminal `DocumentOutcome` semantics both
   get complicated. A second phase keeps both simple, and is the reason option C (re-run
   after every rewrite) stays rejected.
4. **A cycle found by the second analysis is reported, not routed.**
   `IssueCategory.CIRCULAR_DEPENDENCY` routes to the Refiner, but refinement is finished by
   then; routing backwards re-opens a completed phase. Record it as a document-level
   finding and stop.

**Pass A's inputs are unchanged** -- it still receives the original analysis. Only what
strategy selection and test generation see is different. Smallest blast radius that still
fixes the thing.

**Why option B and not option A.** Asked "does your consistency analysis describe the
refined requirements or the original ones?", the honest answer today is "the original."
Running the evaluation on the current shape bakes that into every result permanently. The
post-pass detects; phasing fixes. Option A remains a reasonable fallback if S3 proves
harder than expected -- it is one extra call per document and still produces the frequency
number.

### S4 -- Human-supplied fragments for `NON_ATOMIC`

Resolves Known Limitation 8. **Build after S1 supplies a frequency count and after S3 has
landed.**

**S3 is what dissolves the blocker.** Known Limitation 8 records that a split is not a
rewrite because it changes requirement-set membership, so both document-level reports
describe a document that no longer exists. Once S3 re-runs that analysis after
refinement, that objection is gone -- the second analysis runs on the post-split set. S3
and S4 are one fix; S4 is only affordable because S3 is happening.

**Proposed shape** -- field names to be confirmed against `design/schemas.py`, not
assumed:

- The Refiner never splits. The operator may answer a `NON_ATOMIC` question with the
  split itself: an optional list of fragment texts on `RefinerAnswer`.
- The requirement terminates with an outcome meaning *split*, distinct from failure. A
  dedicated `RunOutcome` member is preferred over the earlier proposal of reusing
  `CAP_STOPPED` with a `cap_reason`, because a split is a success and reporting it as a
  cap corrupts the outcome counts.

  **This must answer an objection already on record before it is adopted.**
  `DocumentOutcome`'s own comments reject `HUMAN_OVERRIDE` as a `RunOutcome` member on the
  grounds that it would be *"a second, independent axis"* -- and "was this requirement
  split?" is arguably that same shape, orthogonal to whether the requirement converged.
  That is very likely why the earlier proposal reached for `CAP_STOPPED` plus a reason.
  Either show why the objection does not apply here (the argument available: a split
  *terminates* the requirement, so it is on the same axis as `COMPLETED`/`CAP_STOPPED`,
  unlike an override which annotates a run that continues) or concede it and use the
  reason-string form. Do not adopt a new member without settling this -- the precedent is
  explicit and a reviewer of this file will find it.

  Whichever is chosen, `TERMINAL_OUTCOMES` is a `frozenset` that the `_OutcomeRule` table
  keys off, so a new member must be added there deliberately, with its own
  required/forbidden field rule. It will not inherit sensible defaults.
- Between pass A and the second document analysis, the orchestrator materialises
  fragments with derived ids (`REQ-7.1`, `REQ-7.2`, ...), each recording its origin
  requirement.
- Fragments re-enter pass A. **A fragment cannot itself be split** -- one generation only,
  which is what makes this terminate.
- `_test_generator_extra_check`'s `known_requirement_ids` extends to derived ids; the
  origin field preserves traceability back to the requirement as written in the source
  document.
- Dependency links naming the original id are resolved by **re-derivation** in the second
  analysis, not by patching. This is the part that only works because of S3.

**Why this is the simple option.** No model invents anything, no new stage, no new LLM
call, no similarity heuristic, no accuracy evaluation of its own. The silent-drop failure
mode (Known Limitation 8, case 2 -- the model returns one clean behaviour and discards the
others, passing the next check with no trace) disappears entirely, because the operator
enumerates the fragments rather than the model choosing which to keep.

It also closes the human-channel gap recorded under Known Limitation 8: "this flag is
correct and cannot be fixed at this level" was previously inexpressible, since
`user_confirms_resolved: True` means resolved (false here) and `False` re-asks until the
cap. Supplying the split *is* that answer.

**Costs, stated:**

- Operator effort rises -- fragment texts are typed by hand.
- Any per-requirement rate now needs an explicit denominator, before or after splitting.
  State it wherever a rate is reported. (This is a reporting obligation and is repeated in
  `docs/EVALUATION_PROTOCOL.md`.)
- One more human decision to carry in the record, including who made it (see S2).

**Read the frequency count carefully before building.** Exactly one genuine `NON_ATOMIC`
case has appeared in 34 requirements (`LUITEL-R7`), and the detector over-flags -- 2 of 5
flags in the 2026-08-13 suite were conjunction-splits rather than genuine bundling. Raw
counts will overstate the need. Since S1 precedes everything anyway, the number arrives
before it is needed; there is no reason to build machinery on n=1.

#### S4 evaluated and deferred, not built (2026-08-17)

**Decision: defer.** Task 5 of the handover (`docs/superpowers/plans/2026-08-15-CLAUDE-CODE-HANDOVER.md`)
is not implemented. No runtime `NON_ATOMIC` splitting exists in `orchestrator/pipeline.py`
or `design/schemas.py`. This is a deliberate stop, scoped and reasoned here so it reads as
evaluated, not forgotten.

**`1/34` is design-stage evidence, not a held-out measurement, and must not be generalized
to the evaluation corpus.** Those 34 requirements are the project's own illustrative/spot-check
set (`requirements_dataset.json` and the behavior-scenario fixtures), assembled while building
the schema and prompts -- not a sample of the frozen 805-requirement PURE subset in
`datasets/pure-extracted/`. A frequency measured on the design set says nothing calibrated
about the held-out set; treating `1/34` as if it were would be exactly the kind of unverified
claim this file's own house rule ("verify before asserting") warns against.

**The Task 3 frequency gate was not satisfied by substituting this number.** The handover
doc's Task 5 entry requires "Task 3 reports the genuine `NON_ATOMIC` frequency" over the
now-extracted, frozen corpus before Task 5 starts. That measurement was scoped (script,
manual-review split, cost/call-count estimate) but deliberately **not run** -- see next
paragraph for why -- so the gate remains open, not waived. `1/34` closing it would be the
substitution the handover explicitly guards against.

**Why the frozen 805 requirements were not scanned before the freeze.** Running the Quality
Checker over the held-out evaluation corpus specifically to decide whether to build a
feature is using evaluation data to shape the system under evaluation -- the same
contamination risk `docs/EVALUATION_PROTOCOL.md`'s freeze boundary exists to prevent, and
the handover doc's own ground rules say to keep system-change decisions and evaluation
data apart. A feature-existence decision made by peeking at the frozen set first would
weaken the study regardless of which way the number came out.

**Reasons the decision does not rest on the frequency number alone:**

- Runtime splitting is substantial machinery for what design-stage evidence suggests is a
  rare case: new schema fields (`RefinerAnswer` fragments), a provenance/origin-id scheme,
  requirement-identity changes (`_known_requirement_ids`, `_test_generator_extra_check`),
  resume-position logic for a requirement set whose membership changes mid-run, and
  dependency-link re-derivation -- five distinct places to get wrong for one outcome type.
- Automatic/model-generated splitting stays rejected on its own terms, independent of
  frequency: a model deciding what counts as one behaviour can silently omit, duplicate, or
  alter required behaviour with no trace (the case-2 failure mode recorded earlier in this
  Known Limitation). Nothing about the frequency count changes that risk.
- Using the frozen corpus itself to justify building the feature would be measuring the
  wrong thing at the wrong time, independent of what the measurement would show.

**Current practical workaround: manual preprocessing, before the pipeline runs -- an
operational option outside this thesis evaluation, not something applied to the frozen
evaluation inputs.** A human identifies a genuinely non-atomic requirement in the source
SRS and splits it there, with each fragment entering the pipeline as its own requirement.
Original-to-fragment traceability is kept as an operator-maintained external sidecar or
source-document record -- not invented or inferred by any pipeline stage, and not a field
the run schema provides: `RunMetadata`/`RequirementRunRecord` have no
original-to-fragment mapping today. This is the same "split happens where the document is
authored" conclusion reached on 2026-08-14, restated as the standing default rather than a
rejected alternative.

**This workaround must not touch the frozen 805-requirement evaluation subset.** Splitting
a requirement there after the freeze changes requirement-set membership and every
per-requirement denominator computed against it -- the same contamination this section
already rejects scanning for. During the evaluation run itself, a genuine `NON_ATOMIC`
case is left as-is and reported as a documented limitation of that run's results, not
worked around.

**Remaining research gap, recorded as future work, not scheduled.** Provenance-preserving
human decomposition -- an operator-driven split that the pipeline records and traces
end-to-end (the S4 shape proposed above), rather than one done invisibly before the run
starts -- remains a real, documented gap. Building it needs: (1) a genuine-frequency
measurement taken *after* the evaluation freeze closes, over the frozen corpus, without
that measurement feeding back into system design; and (2) the outcome-axis question (new
`RunOutcome` member vs. `cap_reason` reuse) already flagged above as unresolved. Revisit
after the evaluation phase, not before.

### Declined, with the measurement behind each

These are not deferred on cost or scope. Each was measured, and the measurement is the
reason.

- **Test-case de-duplication** (KL1) -- spanning cases are confirmed reachable
  (`TC-13-PURE-ERTMS-R7-2`), but an actual duplicate has never been observed, and a
  false-positive merge silently deletes real coverage. If a number is wanted, emit
  suspected duplicates as an advisory count and act on nothing.
- **`refined_text: list[str]` as a general redesign** (KL8) -- superseded by S4, which
  obtains the same capability from one optional answer field plus the phasing that is
  happening anyway.
- **Deterministic undefined-notation pre-pass, and a new `IssueCategory` for it** (KL5) --
  S9 measured the Quality Checker as *blind* to `LO = T_LT`, not confused by it. There is
  no wrong flag to correct, and the anchor example turned out to be a corrupted
  inequality rather than domain notation. Nothing to build.
- **Hard-real-time / soft `PERFORMANCE` split, and an `EMBEDDED` `SystemType`** (KL3) --
  `PERFORMANCE` was selected zero times across 34 requirements, including on `LUITEL-R1`
  where S12's ground truth expected it. A distinction qualifying a technique that is never
  chosen is unreachable.
- **Collapsing `SystemType` to `{AI_SYSTEM, OTHER}`** (KL9) -- the 2026-08-13 suite
  refuted the empirical half: 31 `other`, 2 `mobile`, 1 `ai_system`. The Classifier
  discriminates. The structural point (three members, one technique pool) stands and is
  *reported* as a limitation rather than fixed.
- **Web technique pool** (KL9) -- no web requirements exist in the design corpus, so
  building it repeats the mistake KL3 stays open for. **Conditional, not closed:** revisit
  after S1, since PURE holds 79 documents and a web SRS is likely among them.
- **Pairwise / combinatorial testing** -- deferred, unchanged.
- **The no-op-rewrite validator, fixes (b) and (c)** (KL10) -- the 2026-08-13 suite traced
  every one of 38 no-ops to an answer supplying no information. No defect for the rule to
  catch. Fix (a), *counting* no-ops, is kept and belongs to the protocol document.
- **Loosening `TestPlan`'s strict "every case covers this requirement" rule** (KL6) --
  never fired in any run.

**Also conditional on S1:** mobile CT-MAT prompt content, worth doing only if mobile
requirements survive into the evaluation corpus, and at prompt level rather than in the
enum unless mobile coverage must be a *reported* metric. Standards-cited
measurable-property rewrites (the verified `STANDARDS_REFERENCE` table above), worth
doing only if a model rather than the operator answers Refiner questions -- its purpose
is to convert the fabrication mode measured in the n=3 answerer pilot into a citation, and
a human answerer does not exhibit it. Under the protocol's chosen answer policy, it is not
needed.

## S2 implemented -- operator system-type label, as a run-record field plus a third CLI subcommand (2026-08-15)

S2 above deliberately left the shape open ("decide from the code whether this belongs on
the classification record, the run record, or the human-interaction protocol"). Resolved
as follows.

**Field lives on `RequirementRunRecord`, not `Classification`.** `Classification` is a
pure stage output -- its `system_type` is carried, unmodified, into `TestStrategy` and
checked there (`_denormalised_fields_agree`). Putting the operator's label inside
`Classification` itself would blur a model artifact with a human one and risk a future
validator trying to reconcile them, which is exactly what must not happen: the two labels
are allowed to disagree, because disagreement is the measurement. `RequirementRunRecord`
already holds `classification: Optional[Classification]` as one field among the record's
other provenance data, so `operator_system_type: Optional[SystemType] = None` sits next to
it as a sibling, not a patch to the stage output.

**Provenance is the two field names, not a third marker.** "Record both labels ... with
provenance for who set each" does not require a `source` enum: `classification.system_type`
is unambiguously the Classifier's (it is only ever set inside `run_requirement`'s
`PipelineStage.CLASSIFIER` branch), and `operator_system_type` is unambiguously the
operator's (nothing else ever writes it). A separate provenance field would restate what
the two names already say.

**Capture mechanism: a third CLI subcommand, not a `resume` flag and not a third `HumanFns`
callable.** Both alternatives were rejected on the same file's own stated contracts:

- A `resume --operator-labels FILE` flag would reopen the exact gap
  `orchestrator/cli.py`'s module docstring explains `resume` was deliberately closed
  against: "a resume that accepted a fresh config or input path could point at something
  that disagrees with what is already on disk." An operator-labels file is precisely that
  kind of fresh input.
- A new `HumanFns` field (`label_system_type`, called once per requirement) would be the
  "third human interaction point" the discussion under Known Limitation 9 explicitly
  flagged as a real cost, and that document only reasoned about it in the *document-level
  confirm/override* shape -- which was not adopted. Adding a blocking per-requirement
  version here, even framed as "just recording," would be adopting a bigger version of the
  thing that discussion declined.

`python -m orchestrator.cli label-system-type RUN_DIR LABELS.json` (`orchestrator/cli.py`)
instead reads a JSON `{requirement_id: system_type}` object once, offline -- no adapter, no
`StageFns`, no `HumanFns` call, so it blocks nothing and calls no LLM. It validates every id
against the run before writing anything (a typo'd id fails the whole call, nothing written,
mirroring `retry_document_stage`'s "check everything before spending anything" shape), then
rewrites each matching `requirements/*.json` file via the existing `write_requirement_run`.
Prints an immediate agree/disagree count against `classification.system_type` as a
side-effect of already having both values in hand -- not a new metrics system, just what
falls out of the loop that writes the label.

Tested: `design/test_schemas.py::test_operator_system_type_capture` (defaults to `None`,
agreeing and disagreeing values both accepted, no validator between them, round-trips
through JSON); `orchestrator/test_cli.py`'s four `test_label_system_type_*` cases (records
the label without touching `classification`, disagreement stored as-is, unknown requirement
id rejected with nothing written, an invalid `SystemType` string rejected). Suites at 330
(schemas) and 55 (CLI) after this change.

## S1 in progress -- evaluation-subset freeze, corpus extraction, and what the boundary question actually looks like against real data (2026-08-16)

Working session on S1 (extraction for the reserved corpora). Corrects course partway
through: the first plan (scope PDF/DOC/HTML/RTF extraction by which formats are cheap to
parse) was rejected on methodological grounds before any format-specific extractor was
built, and replaced with the approach below.

**Why the format-first plan was wrong.** Deciding to build PDF+HTML extraction now and
defer `.doc`/`.rtf` "until needed" means the evaluation subset ends up shaped by which
formats happen to be easy to parse, not by any property of the documents themselves --
exactly the kind of selection bias a defensible evaluation cannot carry silently. Corrected
approach: **freeze which documents are the evaluation subset first, independent of parsing
difficulty; only build format-specific extraction for documents the frozen subset actually
needs.**

**The primary PURE evaluation corpus is the already-extracted annotated subset, not
`pure-full/`.** Of the 18 files in `datasets/requirements-xml/XMLZIPFile/`, exactly 6 carry
real `<req>` annotations (`tools/extract_pure_xml.py`'s own docstring) -- the other 12 are
unannotated PURE XML exports, same boundary problem as the 79-doc PDF/DOC/HTML corpus, just
in an XML container. Of those 6, two (`1998 - themas.xml`, `2007-ertms.xml`) are spent for
design purposes (`datasets/EVALUATION_DATASETS.md`). The remaining **5 documents -- cctns
(115), gamma-j (51), eirene-fun-7-2 (583), keepass (32), peering (24): 805 requirements
total -- are genuinely reserved, already `RequirementSet`-shaped, and already extracted with
zero inference** (PURE's own annotators decided the boundaries). This is the primary PURE
evaluation corpus. No new extraction work was needed to reach it -- it already existed in
`datasets/pure-extracted/`.

**Format-specific extraction for `pure-full/`'s 79 PDF/DOC/HTML/RTF documents is deferred**,
not built, pending whether the frozen subset ever needs to expand past the 5 above.
`pure-full/` breaks down as 62 PDF / 13 legacy `.doc` (verified by magic bytes,
`D0 CF 11 E0...` -- genuine OLE compound files, not renamed `.docx`) / 2 HTML+HTM / 1 RTF.
**If extraction is built later, do not exclude a document from the selected subset merely
because its format is harder to parse than another's** -- the same selection-bias argument
that killed the format-first plan applies retroactively to any later subset decision too.

**The corruption scan (Known Limitation 5's fold-in) is NOT gated on that decision --
built and run now, over all 79 documents, regardless of extraction scope.** Scanning for
corruption signatures is diagnostic reporting, not a boundary/extraction decision, so the
selection-bias argument above does not apply to it.
`tools/scan_pure_corruption.py` reproduces the exact 2026-08-14 finding when re-run
against the 18-file XML subset (`1998 - themas.xml` is the only file with
`chained_comparisons`; `2006 - eirene sys 15.xml` shows exactly 7 `±` -- both match this
file's earlier entry verbatim), which is the validation that it is measuring the same thing
before trusting it on new data. Text is pulled best-effort per format for scanning purposes
only (pdfplumber for PDF; a printable-ASCII-run regex for `.doc`, verified empirically to
pull real prose rather than internal structure names from these specific files; a
tag-strip regex for HTML/RTF) -- explicitly NOT the same bar as a real requirement
extractor, and the script's own docstring says so, so nobody later mistakes the scan's
crude text pull for a boundary-decision method.

**pdfplumber is a recorded, approved exception to the handover doc's "no new
dependencies" ground rule** (`docs/superpowers/plans/2026-08-15-CLAUDE-CODE-HANDOVER.md`),
not a silent one. Scoped narrowly on purpose: `requirements.txt` marks it
diagnostic-tooling-only, imported by `tools/scan_pure_corruption.py` alone, never by
`design/` or `orchestrator/` -- reproducibility for the thesis was judged to outweigh the
"no new deps" rule for this one case, since the alternative (hand-rolling PDF text
extraction) would be worse for reproducibility, not better.

**This scan's text pull is NOT the same extraction path as whatever corrupted
`1998 - themas.xml`.** That damage happened inside PURE's own original PDF-to-XML
conversion pipeline, which this project has never had access to and does not reproduce --
`scan_pure_corruption.py` reads the PDF directly with pdfplumber, an entirely different
tool. So a clean result here does not mean "the original converter would not have
corrupted this document too"; it means "pdfplumber's own text extraction, independently,
did not surface one of these three signatures." The scan is a second, independent check
on the same source PDFs, not a re-run of the process that produced the known corruption,
and the conclusion below is phrased to reflect that.

**Dalpiaz and PROMISE NFR needed extractors, not a boundary heuristic.** Both datasets are
already segmented by their own creators -- Dalpiaz one user story per line, PROMISE NFR one
labeled sentence per `@DATA` row -- so, like PURE's `<req>` elements, there is no inference
to make about where a requirement starts or ends. `tools/extract_dalpiaz.py` (1,677
requirements, 22 files) and `tools/extract_promise_nfr.py` (625 requirements, 15 projects)
are built and run. Two real exceptions surfaced by surveying all 22 Dalpiaz files before
writing the extractor, not assumed: a handful of stories wrap across two physical lines with
no terminal punctuation, and several use "As ROLE" without an article ("As User", "As lab
administrator") -- both handled by the extractor's line-continuation rule (continue any line
not starting with "As "), which also means it cannot distinguish a genuine wrapped
continuation from an unrelated line that happens to follow one. Exactly one such case exists
in the real data (`dalpiaz-15`, a stray "Auditing & Reporting." heading merged into the
preceding story) -- every merge the extractor performs is recorded in its manifest sidecar
under `line_merges` specifically so this is reviewable, not silently trusted.

**Riaz is deferred, not attempted.** Investigated because it looked like it might be
another "already segmented" case (its own sentence-level annotations, including a
`securityObjectiveAnnotations` field marking security-relevant sentences) -- but real
examples show many annotated sentences are bullet-list *fragments*
("-identifiers and other registration details of system users..."), not standalone
requirement-shaped sentences, so reconstructing a coherent requirement means solving PURE's
bullet-list-assembly problem (`tools/extract_pure_xml.py`'s itemize/enum handling) a second
time for a different schema, not a one-row-one-requirement parse like Dalpiaz/PROMISE. Given
`datasets/EVALUATION_DATASETS.md` already scopes Riaz as relevant "only if the thesis scope
grows to cover... security-specific requirements," this is left unbuilt rather than rushed.

**Empirical check on the requirement-boundary question, before choosing a heuristic for
whatever of `pure-full/` eventually needs one.** Modal-verb prevalence (`shall`/`must`/
`should`/`will`) was measured across all 5 real extracted PURE documents, since a
modal-verb-triggered splitter was the naive first candidate: **cctns 99%, gamma-j 94%,
eirene-fun-7-2 83%, keepass 44%, peering 4%.** A single modal-verb rule would perform
unevenly to the point of uselessness on peering-shaped documents -- its 24 real requirements
read as plain declaratives ("The format for service information description is defined.",
"Resource provisioning, delegation and reservation policies are in place.") with no
normative modal at all. This is a real, data-backed reason to reject "modal verb" as a
general-purpose rule, not a guess. **Not yet resolved:** whether a paragraph/numbered-clause
rule fares better depends on whether the source PDFs actually carry a numbered/tabular
requirements layout pdfplumber could detect structurally -- unchecked, because it requires
looking at real `pure-full/` PDF pages, which is exactly the "only if the subset needs it"
gate above. Decide this if/when the frozen subset ever pulls a `pure-full/` document in.

Not run in this session (no `GEMINI_API_KEY`/`GROQ_API_KEY` here, same constraint as the
2026-08-16 Quality Checker stability harness): the `NON_ATOMIC` frequency measurement the
handover doc's Task 3 asks for "before Task 5" over the now-extracted corpus. That needs a
real Quality Checker call and is separate follow-up work.

**Corruption scan result, all 79 `pure-full/` documents (`docs/superpowers/pure-full-corruption-scan.json`):**
28/79 show at least one signature; 0 files failed to read (PDF/legacy-`.doc`/HTML all
produced text). **No document reproduces themas's actual damage pattern.** Most flagged
documents only trip `underscore_tokens` on legitimate API/constant identifiers a
software SRS is full of (`DT_NULL`, `GUI_BACKGROUND`, `HW_TurnOn`, ...) -- expected noise
on this signature, not evidence of anything. Exactly one document, `2007 - nlm.doc`,
trips `chained_comparisons` (`'H9=H9=F6'`, `'H=EI=GE9E'`, `'xSuh=jtFwk=nf8i'`) -- but
these are not English/identifier-shaped the way themas's `'LT = T = UT'` was, and the
same document separately shows 27 clean `<=` and 24 clean `>=` occurrences from this
scan's own text pull. The likelier explanation is the scan's own crude `.doc` text pull
(a printable-ASCII-run regex, not a real OLE-stream parser) misfiring on binary noise,
not a second corrupted document -- exactly the under/over-detection risk the tool's
docstring already flags for that format.

**Conclusion, stated conservatively:** no additional corruption was detected by this
diagnostic across the 79-document corpus. This is not the same claim as "no other
document is corrupted" -- as noted above, the scan checks these signatures against
pdfplumber's own text extraction, not against whatever PURE's original PDF-to-XML
converter produced, so it cannot rule out damage that a different converter introduced
and this scan's own extraction path happens not to reproduce or surface. What it does
support: the three named signatures, run over pdfplumber's text, do not turn up a second
themas-shaped case. Re-scan if a different or better `.doc`/PDF text extraction is ever
built, since that would check a path closer to (though still not identical to) the one
that actually damaged `1998 - themas.xml`.

## S3 implemented -- the pipeline is phased, option B under Known Limitation 7 (2026-08-16)

Full implementation of S3 (`docs/superpowers/plans/2026-08-15-system-changes-before-freeze.md`),
resolving Known Limitation 7's staleness problem the way option B proposed: document-level
analysis now runs twice, once on the original text and once on the refined text, with
strategy selection and test generation fed by the second (refined) run, never the first.
Built on a branch (`task-4-phase-pipeline`), suites kept green throughout.

**Schema changes.** `DocumentStage` gains `CONSISTENCY_CHECKER_REFINED`/
`DEPENDENCY_MAPPER_REFINED` -- genuinely distinct stage identities, not the original two
reused under a different label, so `DocumentStageError`/`DocumentStageAttempt` can always
say which generation of analysis a failure or a call belongs to.
`DocumentRunRecord` gains `refined_consistency_report`/`refined_dependency_report`
(never overwriting `consistency_report`/`dependency_report` -- the diff between the two
generations is itself a reportable result) and `refined_analysis_outcome`
(`DocumentOutcome`, same three states, describing only the second phase; defaults
`IN_PROGRESS`, meaning "hasn't run yet"). `DocumentOutcome`'s docstring is corrected: it
now says explicitly that it describes only phase 1, not "the document-level phase" as a
whole, since that phrase is no longer singular. A new computed field,
`refined_cycles`, exposes `refined_dependency_report.find_cycles()` directly --
constraint 5's "reported, not routed to the Refiner" needs no stored field at all, since
`find_cycles()` is already deterministic on data already persisted; storing a snapshot
would be exactly the "two fields that must agree" pattern this project keeps finding bugs
in. `RunOutcome.IN_PROGRESS` no longer forbids `cap_reason` -- pass A can now conclude
with the cap decision already made ("generate anyway") while the record stays IN_PROGRESS,
because pass B (strategy selection + test generation) hasn't run yet; before phasing,
`decide_at_cap` and stage 3/4 always ran in the same call, so the two fields never needed
to coexist on a non-terminal record. `pending_requirement_ids`'s docstring is amended: the
field's own definition ("has this requirement's whole lifecycle concluded") did not need
to change, but "pending" is no longer one thing -- a caller must additionally ask which
phase a pending requirement needs, which only `orchestrator.pipeline.resume_at`'s return
value (bucketed into pass-A vs. pass-B stages) and `refined_analysis_outcome` can answer.

**`orchestrator/pipeline.py` control flow.** `run_requirement` split into
`run_requirement_pass_a` (classifier, quality-check/refine loop, revision-cap decision)
and `run_requirement_pass_b` (strategy selection, test generation) -- the natural
boundary `resume_at` already expressed structurally (`STRATEGY_SELECTOR`/`TEST_GENERATOR`
vs. the four earlier stages, now named `PASS_A_STAGES`). `run_document_stages` gained
`consistency_stage`/`dependency_stage` keyword parameters (default: the original pair),
so the exact same function runs both phases -- called once by `run_document`/
`resume_document` for the original text, and again for `_refined_requirement_set`
(every requirement's `final_text`, including CAP_STOPPED/ERROR ones -- their text is
still part of the document and can still conflict with or depend on a sibling headed for
pass B). `run_document`: doc analysis (1) -> pass A for every requirement (2) -> doc
analysis on the refined set, both generations of reports kept, `refined_analysis_outcome`
set (3) -> pass B for every non-terminal requirement (4). `resume_document`: the same four
steps, each skipped if already done -- pass A only for requirements not yet past it
(`_pass_a_concluded`), the second analysis only once `_pass_a_concluded` is true for
*every* requirement and only if it has not already run (`refined_analysis_outcome` still
`IN_PROGRESS`), pass B only for requirements not yet terminal. `retry_document_stage` was
NOT extended to the refined stages (out of scope for S3) -- it now raises a clear
`ValueError` naming the unsupported stage instead of a confusing `KeyError` from its
internal dispatch dict.

**A real bug found by running the existing suite, not by inspection.** `resume_at`
reasons purely from `rounds`/`turn`/`rewrite` and has no notion of "the cap decision was
already made" -- the round the cap fires on can leave `turn` still `None` (the cap check
in `_run_refine_loop` fires before that round's own questioner is ever called), which
`resume_at` reads identically to a genuinely unfinished round. Without an explicit
override, an already-cap-decided record would be sent BACK into the refine loop by
`run_requirement_pass_a`'s entry guard, asking the questioner again after the human
already chose to stop refining -- caught by `orchestrator/test_harness.py::test_revision_cap`'s
CAP_GENERATED case, run end to end through the new split, not by reading the code. Fixed
by checking `record.cap_reason is not None` BEFORE consulting `resume_at` at both of
pass A's and pass B's entry points (and in `_pass_a_concluded`), a strictly cheaper and
more reliable check than trying to teach `resume_at` about a decision it was never
designed to know.

**Preserved, not silently dropped: re-asking the cap decision on a resumed pass-B
failure.** The pre-S3 design let the human change their mind on ANY resume of an
already-capped record whose stage 3/4 work had not yet succeeded (documented at
`orchestrator/test_harness.py::test_resumed_cap_generated_then_stopped_strips_stage34`
and its own commentary: "an earlier call chose CAP_GENERATED, then the Strategy
Selector or Test Generator failed, and the human now says stop instead of retrying").
Phasing nearly dropped this by accident -- `run_requirement_pass_b` was written with no
`human_fns` parameter at all ("pass B has no human-interaction point"), which is false
for exactly this resumed-after-failure case. Fixed: `run_requirement_pass_b` takes
`human_fns` after all, and re-invokes `decide_at_cap` when (and only when) `cap_reason`
is already set AND `outcome is RunOutcome.ERROR` -- a prior pass-B stage call failed on
an already-cap-decided record. A fresh cap-generate decision handed straight from pass A
has `outcome=IN_PROGRESS`, not `ERROR`, so it is not asked about a second time
immediately.

**`StageFns` gains two genuinely independent, optional fields** --
`check_consistency_refined`/`map_dependencies_refined` -- rather than reusing
`check_consistency`/`map_dependencies` for both phases. The reuse alternative was
tried first and rejected: `orchestrator/stages.py`'s factories build one closure per
call, with the model/prompt baked in at construction time, so reusing the phase-1
closure for phase 2 would mean an operator's YAML override for
`consistency_checker_refined` was validated but silently never actually applied --
exactly the "config accepted, not honored" shape this project's rules warn against.
Defaulting both new fields to `None` (with `orchestrator/pipeline.py` falling back to
`stage_fns.check_consistency`/`map_dependencies` when unset) meant the ~60 existing
`StageFns(...)` fixtures across `orchestrator/test_harness.py` that predate S3 and never
exercise the two-phase document flow needed no changes at all; only
`orchestrator/cli.py`'s `_build_stage_fns` (the real, production wiring) sets both
explicitly, from the refined stages' own resolved config. Two new prompt files,
`orchestrator/example_prompts/consistency_checker_refined.txt`/
`dependency_mapper_refined.txt`, are byte-identical copies of their siblings (no prompt
content decision was made -- same analysis, same instructions, just a second
independently-hashed, independently-configurable file, per S3's "distinct phase, not a
re-run" framing applied to prompt provenance too).

**`design/generate_arch_diagrams.py`'s `validate_stage_wiring` needed a real loosening,
not just new rows.** `STAGE_WIRING`'s declared field-name column was checked against
`StageFns`'s fields in DECLARATION ORDER, which used to coincide with `ALL_STAGES`'
order by construction. It no longer can: Python requires every dataclass field with a
default to follow every field without one, so the two new optional fields must sit at
the very END of `StageFns`, while `ALL_STAGES` (enum-derived) puts their stage keys
right after their non-refined siblings. Declaration order isn't behaviourally meaningful
for a dataclass -- the check was changed from sequence equality to set equality
(coverage: every field wired, no extras), which is what the check was actually protecting.
`design/test_generate_arch_diagrams.py`'s matching anchor test was updated the same way.

**Tests.** `orchestrator/test_harness.py::test_resume_positions` updated FIRST per S3's
own instruction (constructed the new `cap_decided_awaiting_pass_b` case, confirmed it
failed with a `ValidationError` under the unmodified schema, then made the schema change
that turned it green) -- `docs/superpowers/plans/2026-08-15-CLAUDE-CODE-HANDOVER.md`'s
Task 4 names this test specifically because the resume spec drifted once before.
`test_run_document_happy_path`'s own fixture had a latent, pre-existing bug once phasing
existed: `check_consistency`/`map_dependencies` each had exactly one scripted response,
so the second (phase 2) call popped from an empty queue, which `call_document_stage`'s
retry logic silently absorbed into a DEGRADED second phase -- the test's own assertions
never noticed, because a DEGRADED second phase doesn't block per-requirement COMPLETED
outcomes (D1=b's policy, correctly, applied to the new phase too). Fixed by giving both
two responses and asserting `refined_analysis_outcome is COMPLETED` explicitly, so a
silently-degraded second phase now fails the test that exists to prove it succeeded.
Two new tests, both mutation-tested (broke the guarantee on purpose, confirmed the
suite went red, reverted): `test_phased_pipeline_pass_b_sees_refined_analysis_not_original`
(the core guarantee -- pass 2 is scripted to find a dependency cycle pass 1 didn't have;
Strategy Selector/Test Generator receive it, Quality Checker is never called a second
time proving the cycle is never routed to the Refiner) and
`test_resume_gates_second_analysis_until_every_requirement_concludes_pass_a` (one
requirement still stuck in pass A after a resume; the second analysis must stay
`IN_PROGRESS`, not run and DEGRADE, and pass B must not run for the OTHER requirement
either, even though it individually finished pass A cleanly).

Suites after this change: schemas 336, generate_diagrams 13, arch diagrams 88, harness
480, CLI 55, stages 163, stage_fns 67, config 95, rotation 18 -- all green.

**Not done, deliberately out of scope for S3:** `retry_document_stage` extended to the
refined stages (no task asked for it; the new `ValueError` at least fails clearly rather
than with a raw `KeyError` if someone tries); auditing every pre-existing document-stage
test fixture in `orchestrator/test_harness.py` for under-provisioned Scripted mocks that
now silently retry-then-degrade through phase 2 rather than genuinely exercising it --
the ones inspected either don't assert anything about phase 2 (so remain correct, just
slightly wasteful) or were the one (`test_run_document_happy_path`) that did and is now
fixed. Revisit if a future change needs to trust phase 2 succeeding in one of those
fixtures specifically.

## S3 review fixes -- fresh-run gate, legacy schema compatibility, active configs, CLI summary (2026-08-16)

An independent review of the uncommitted S3 work (still on `task-4-phase-pipeline`, not
committed) found four real gaps -- each verified empirically before fixing, not assumed.

**1. `run_document` had no fresh-run equivalent of `resume_document`'s all-requirements
gate.** `resume_document` already required `_pass_a_concluded(record)` for EVERY
requirement before running the second document analysis; `run_document` ran it
unconditionally after one pass-A sweep, so a document where one requirement's pass A
genuinely failed (stays `ERROR`) would still run the second analysis over a mixture of
that requirement's original text and its siblings' refined text -- an accident of which
requirement happened to fail, not the methodological choice the gate exists to enforce
elsewhere. Fixed: `run_document` now applies the exact same
`all(_pass_a_concluded(r) for r in pass_a_records)` check and returns the partial record
(both requirement records included, `refined_analysis_outcome` still `IN_PROGRESS`) when
it fails; a later `resume_document` call finishes pass A for whatever is left and then
proceeds through the second analysis and pass B itself, unchanged.

**A second, independent bug surfaced by actually exercising this gate, not by
inspection.** `orchestrator/test_harness.py::test_error_resume_finish` (an existing,
previously-green test -- a single requirement whose Classifier fails, then a resume with
working stage fns) started failing once the new gate made `resume_document`'s pass-A
retry path genuinely exercised end to end for the first time under S3. Root cause:
`run_requirement_pass_a` never reset a stale `outcome=ERROR` (carried in from an earlier
failed attempt at the SAME stage) back to `IN_PROGRESS` on a successful retry -- neither
the clean-pass fall-through nor the cap-generate branch ever touched `outcome`, so a
requirement that failed once and then fully succeeded on retry still carried `ERROR`
forward. This is invisible when `run_requirement_pass_b` runs immediately afterward in
the same call (its own final line always explicitly sets the finished outcome, so the
stale value gets overwritten anyway) -- but `_pass_a_concluded` requires `outcome is
RunOutcome.IN_PROGRESS` before consulting `resume_at`, so it wrongly read a
successfully-recovered-but-stale-ERROR record as "still not concluded," which blocked the
new gate (finding 1) from ever passing for a document that had ever needed a retry. Fixed
by resetting `record.outcome` to `IN_PROGRESS` the moment `run_requirement_pass_a` is
about to retry a record that arrived with `outcome=ERROR` -- every failure path already
sets `"outcome": "error"` explicitly if the retry fails again, so nothing is lost.

Both fixes verified with a new test,
`test_run_document_gates_second_analysis_until_every_requirement_concludes_pass_a`
(two requirements, one's Classifier exhausts, one concludes cleanly; asserts neither the
second analysis nor pass B ran for either, then that a follow-up resume finishes
correctly) -- mutation-tested (the gate condition replaced with `False`, confirmed the
new test's four assertions go red, reverted).

**2. `ALL_STAGES` grew from eight stages to ten, but every real run recorded before S3
is schema_version "1.2" with exactly the original eight** -- confirmed empirically, not
assumed: loading `docs/superpowers/results/2026-08-10-first-real-run/groq/document.json`
(a real file) through the current `DocumentRunRecord` failed outright before this fix,
naming the two refined stages as "missing config for." Historical
`docs/superpowers/results/` files are evidence for the thesis and are never edited to
match a later pipeline -- the schema has to meet them where they are.

Fixed with the smallest explicit versioned mechanism available at each layer:
- `design.schemas.RunMetadata.schema_version` bumped 1.2 -> 1.3 (documented inline
  alongside the 1.0->1.1 and 1.1->1.2 history, which explicitly noted "no real run
  predates either bump" -- this one is different, and says so).
  `_covers_every_stage` now branches: `schema_version == "1.2"` is checked against a
  new frozen constant, `SCHEMA_VERSION_1_2_STAGES` (the exact original eight, a literal,
  never to be derived from any enum again -- it must stay fixed regardless of how
  `ALL_STAGES` keeps growing); anything else is checked against the current
  `ALL_STAGES`. A legacy record loads with `refined_consistency_report`/
  `refined_dependency_report` absent and `refined_analysis_outcome` at its
  `IN_PROGRESS` default -- nothing invented, both fields are already optional/defaulted
  for exactly this reason.
- `orchestrator.config.ResolvedRunConfig` has no `schema_version` field of its own (it
  never needed one before S3), so `_stages_cover_exactly_all_stages` checks the SHAPE of
  `stages` directly: current `ALL_STAGES` or the same frozen legacy eight-stage set.
  This is deliberately a *read* concession only -- it makes a pre-S3 `run_config.json`
  loadable via `read_resolved_run_config` for inspection, nothing more.
- **Loading and resuming are two different gates.** `orchestrator/cli.py`'s
  `_do_resume` now explicitly checks `set(resolved.stages) != set(ALL_STAGES)`
  immediately after loading (before `read_document_run`, before any adapter is
  constructed, before any provider is touched) and refuses with a message naming the
  actual vs. current stage counts and saying plainly that a new run is required. Without
  this second check, the loosened `ResolvedRunConfig` validator alone would have let a
  legacy run silently attempt to resume THROUGH the phased pipeline -- pass B fed a
  refined dependency report that was never computed, or a second document analysis run
  against a schema_version that never expected one.

Tests: `design.test_schemas::test_schema_version_1_2_legacy_compatibility` (synthetic
1.2/eight-stage and 1.3/ten-stage fixtures in both directions, plus loading a REAL file
from `docs/superpowers/results/` and asserting no fabricated refined-phase data appears
on it) and `orchestrator.test_cli::test_resume_pre_s3_legacy_run_rejected_before_any_adapter`
(a hand-built legacy `ResolvedRunConfig`/`RunMetadata` pair, resumed with spy adapter
factories that raise if ever called -- confirming the refusal happens before any
adapter is touched, not just eventually).

**3. Three active, reusable YAML configs under `orchestrator/`** (`example_run_config.yaml`,
`runs_gemini.yaml`, `runs_groq.yaml` -- reusable templates an operator re-runs, distinct
from the FROZEN, already-executed `docs/superpowers/results/**/run_config.json` files,
which are left untouched) failed `resolve_run_config` the moment `RunConfig`'s exact
ten-stage coverage check applied to their eight-stage `prompts`/`stages` sections --
confirmed by actually resolving each one before touching anything. Fixed by adding
`consistency_checker_refined`/`dependency_mapper_refined` to all three, pointed at the
already-existing copied prompt files (no new prompt content). Guarded by a new test,
`orchestrator.test_config::test_active_yaml_configs_resolve_with_all_ten_stages`, which
loads and resolves all three and asserts full `ALL_STAGES` coverage -- so this cannot
silently go stale again the next time a stage is added.

**4. `orchestrator/cli.py`'s `_print_summary` printed only the original document
analysis's outcome.** A run where phase 1 completed but the second (refined) analysis
degraded could print "Document outcome: completed" while returning the stage-error exit
code (a DEGRADED phase records a real `DocumentStageError`, which `_has_stage_errors`
already counts) -- correct exit code, misleading explanation. Fixed: the line is now two
lines, "Original analysis outcome: ..." and "Refined analysis outcome: ...", neither
mislabeling a DEGRADED phase as a failure (DEGRADED is a real, valid, non-terminal
outcome under D1=b, reported as data). New test,
`orchestrator.test_cli::test_degraded_refined_analysis_reported_and_exits_1`: phase 1
completes, phase 2's Consistency Checker call fails permanently, dependency mapper
still runs (D1=b) and the requirement itself still reaches COMPLETED -- asserts both
summary lines are accurate and the requirement is not mislabeled as failed alongside
the correct exit code.

Suites after these fixes: schemas 347, generate_diagrams 13, arch diagrams 88, harness
489, CLI 62, stages 163, stage_fns 67, config 101, rotation 18 -- all green. `git diff
--check` reports only pre-existing CRLF-normalization notices (this repo's own line-
ending convention), no real whitespace or conflict-marker issues. No historical
`docs/superpowers/results/` file was modified; no API call was made (all four fixes and
their tests run entirely against scripted/synthetic fixtures). Not yet committed --
reported for review first, per instruction.

## S3 review fixes, round 2 -- explicit schema-version allow-list, document-metadata resume check (2026-08-16)

A second independent review of the still-uncommitted S3 work found two more real gaps
in round 1's own legacy-compatibility fix, both confirmed empirically before fixing.

**1. `_covers_every_stage` silently accepted any unrecognized `schema_version`.** Round
1's fix branched "if `schema_version == '1.2'`, use the legacy set; else, use current
`ALL_STAGES`" -- so `schema_version="9.9"` (or `"1.1"`, `"1.4"`, a typo) fell into the
`else` and validated successfully as long as it happened to list the current ten stages.
Confirmed by constructing exactly that record before touching anything: it validated.
Fixed with an explicit allow-list instead of an else-branch: only `"1.2"` (legacy,
checked against `SCHEMA_VERSION_1_2_STAGES`) and a newly-named `CURRENT_SCHEMA_VERSION`
constant (`"1.3"`, checked against `ALL_STAGES`) are recognized; anything else raises,
naming both supported versions. `CURRENT_SCHEMA_VERSION` replaces the bare `"1.3"`
literal in both the field default and the validator -- the exact "two things that must
agree" shape CLAUDE.md warns about, since a future bump that updated one and forgot the
other would reopen this same hole under the new version string. The comment at
`SCHEMA_VERSION_1_2_STAGES`'s definition already says what the NEXT bump needs (a new
frozen constant, `CURRENT_SCHEMA_VERSION` moved to the new string, a new branch) --
unchanged, still accurate. Tests: four new `rejects()` cases in
`design.test_schemas::test_schema_version_1_2_legacy_compatibility` for `"1.1"`, `"1.4"`,
`"9.9"`, and `"not-a-version"`, all with the full current ten-stage set (proving the
rejection is about the version string itself, not a stage-count mismatch).

**2. `_do_resume` checked only `run_config.json`'s stage set, never
`document.json`'s.** Round 1's resume-refusal gate (`set(resolved.stages) != set
(ALL_STAGES)`) inspects `ResolvedRunConfig` alone. `DocumentRunRecord.metadata` is a
SEPARATE file, loaded later (`read_document_run`), and nothing compared its
`schema_version`/stages against either the current pipeline or against
`run_config.json`'s own stages. Confirmed by reproducing exactly this pairing -- a
current (ten-stage) `run_config.json` next to a legacy (schema_version "1.2",
eight-stage) `document.json`, with matching prompt hashes so `_prompt_provenance_mismatches`
could not catch it either -- and observing a spy adapter factory (raises if called) get
called. The run genuinely reached `adapter_factories[provider]()` before this fix.

Fixed with two checks added to `_do_resume`, right after `read_document_run`, both
before any adapter is constructed: (a) `record.metadata.schema_version !=
CURRENT_SCHEMA_VERSION or set(record.metadata.stages) != set(ALL_STAGES)` -- the
document-side twin of round 1's config-side check, refusing with a message naming the
recorded version/stage-count and saying a new run is required; (b) `set(resolved.stages)
!= set(record.metadata.stages)` -- config and metadata can each independently look
current while still disagreeing with EACH OTHER (can't happen from normal use today,
since `ALL_STAGES` is one global set, but a run_dir assembled from two otherwise-valid
files should not be trusted to agree just because each individually passes). New test,
`orchestrator.test_cli::test_resume_current_config_with_legacy_document_metadata_rejected`,
reproduces the exact pairing that was confirmed broken, with spy adapter factories, and
asserts `EXIT_CONFIG_ERROR` with a message naming the recorded version and stage count --
mutation-tested (the new gate replaced with `if False`, confirmed the test's assertions
went red, reverted).

Suites after these fixes: schemas 351, generate_diagrams 13, arch diagrams 88, harness
489, CLI 65, stages 163, stage_fns 67, config 101, rotation 18 -- all green. `git diff
--check` clean (pre-existing CRLF notices only). No historical
`docs/superpowers/results/` file modified; no API call made. Not committed -- reported
for review first.

## 2026-08-17 -- Task 6 (E2 baseline arms) offline machinery, and a review round that found seven real gaps in it

`evaluation/` (new package, sibling to `orchestrator/`/`design/`/`tools/`, per the
handover doc's own instruction to keep evaluation tooling separate from production
pipeline behavior): B1/B2 prompts, a runner making exactly 1 (B1) or 2 (B2) LLM calls
per document, the Part A mechanical checks (`docs/EVALUATION_PROTOCOL.md` section 6.1),
and the blinding tool. Built offline, no API calls, reviewed before any real execution
per the handover's own "write the B1/B2 prompts and the runner for review before
executing anything." A first pass was reviewed and seven gaps were found and fixed, all
recorded here so the reasoning survives the fix, not just the diff.

**Decisions kept from the first pass, reconfirmed correct on review:**

- `BaselineTestCaseBatch` (a flat `list[TestCase]`) instead of forcing B1/B2 output
  into per-requirement `TestPlan` wrappers -- `TestPlan.requirement_id` and its
  `_cases_cover_this_requirement` validator are shaped around arm P's
  one-call-per-requirement Test Case Generator, and B1/B2 see the whole set in one
  call by design. Forcing that shape would fabricate a "requirement_id" a baseline
  call never actually had.
- B2 may legitimately ask zero clarifying questions -- the prompt explicitly tells the
  model not to invent one it doesn't need, so "found nothing worth asking" is a real
  outcome, not a parse failure.
- `--answers-json` positional replay stays scripted/offline-only, documented as
  distinct from `--interactive` (the live path) -- question ids are minted by the
  model per run, unlike the pipeline's fixed transcript, so they cannot be pre-keyed.

**Gap 1 -- A2 was scoring every arm against arm P's own Classifier output, not an
independent reference.** The Classifier's accuracy is itself a thing this evaluation
measures (`docs/EVALUATION_PROTOCOL.md` section 6: "Classifier: accuracy against the
operator's label"). Scoring technique eligibility against it would silently launder
Classifier error into every arm's eligibility rate equally, and a wrong Classifier call
would corrupt A2 for all three arms identically -- invisible, because they'd all be
wrong together. Fixed: `check_technique_eligibility` now takes
`operator_system_type_by_requirement_id`, sourced from `RequirementRunRecord.
operator_system_type` (S2, Task 1 of the handover) via the new
`operator_system_type_map()`, never from `.classification.system_type`. A requirement
id a pooled case names with NO entry at all in that mapping is a config error and
raises; a requirement id present with value `None` (operator hasn't labeled it yet) is
reported in `unlabeled_requirement_ids`, not silently skipped -- "missing" and
"genuinely unlabeled" are different findings and must not collapse into one.

**Gap 2 -- A5 only emitted `(arm, requirement_id)` entries that had >=1 case.** A
baseline that silently omitted an entire requirement -- arguably A5's single most
important finding -- was indistinguishable from "this combination was never checked."
Fixed: `volume_per_requirement` now pre-populates every combination in
`{P, B1, B2} x known_requirement_ids` to zero before counting, so an omission stays a
visible zero rather than a missing key.

**Gap 3 -- the blinding tool had no operational writer, only in-memory pooling.**
Added `write_blinding_result`: separate `scoring_path`/`mapping_path` required to
differ (rejected before any write happens if they're the same file); both written via
a shared `evaluation/atomic_io.atomic_write_json` (write to `.tmp`, `os.replace` into
place, so a reader never observes a half-written file, on POSIX or Windows); the
shuffle seed recorded ONLY in the mapping file's metadata, never the scoring file;
`pool_and_blind` now rejects a duplicate case id within one arm before blinding
(ambiguous `original_case_id` otherwise); `BlindingResult`'s own validator already
rejected mismatched scoring/mapping cardinality and disjoint blind-id sets, now also
covers a mapping-side internal duplicate, not just the scoring side. A CLI
(`python -m evaluation.blinding POOLED.json --scoring-output ... --mapping-output ...
--seed N`) was added, `--seed` required (no default) so every real run is
reproducible and the seed is always on record.

**Gap 4 -- nothing enforced B1/B2 using arm P's actual frozen model/temperature/
output_mode, and CLI flags could silently diverge.** New `evaluation/config_parity.py`:
`frozen_arm_p_config()` reads the real values out of `orchestrator/runs_gemini.yaml`
(not hand-copied constants that could drift from the shipped file);
`enforce_fair_config()` compares candidate vs. frozen on provider/model/temperature/
output_mode (deliberately NOT `timeout_seconds` -- a client-side transport setting, not
part of the experiment) and names every mismatch in one error. `BaselineCallConfig`
(moved from `evaluation/runner.py` to `evaluation/schemas.py` so `config_parity.py`
doesn't have to import back into `runner.py`) changed from a bare `NamedTuple` to a
`BaseModel` with the same `temperature` (0.0-2.0) / `timeout_seconds` (>0) bounds as
`orchestrator/config.py`'s `ResolvedStageConfig`, so an invalid value is rejected at
construction. `runner.py`'s CLI now loads the frozen config first (so un-overridden
flags default to matching it automatically), builds and validates `BaselineCallConfig`
via `enforce_fair_config`, and constructs the provider adapter only after both pass --
proven with a spy adapter factory (`evaluation/test_config_parity.py`) that raises if
ever called, confirming a rejected config never reaches provider access.

**Gap 5 -- no pricing snapshot or cost was persisted, and nothing accounted for
partial-attempt tokens.** New `evaluation/pricing.py`: `FROZEN_PRICING_SNAPSHOT`, a
hand-recorded constant (this project's own already-used rate, `docs/superpowers/
results/2026-08-11-behavior-scenarios/RESULTS.md`, $1.50/1M input, $7.50/1M output) --
never fetched live (the module imports no HTTP client at all, verified by the test
itself grepping its own source for one). `compute_cost()` sums tokens across every
attempt with `result` in `("success", "partial")` -- a partial attempt still spent
billable tokens even though its output didn't parse/validate -- and separately counts
`("failed", "fatal")` attempts, which never reached a billable response
(`BaselineAttempt`'s own validator already guarantees those carry no tokens). The CLI
writes a sibling `<output>.cost.json` alongside every run's result, embedding the
pricing snapshot used.

**Gap 6 -- B2 had no interruption safety: an `answer_fn` failure (EOF on stdin,
malformed answers, anything) propagated as an uncaught exception and discarded the
already-successful questions call's tokens/wall-clock/questions.** Fixed two ways:
(a) `run_b2` gained an optional `checkpoint_fn`, called exactly once, right after the
questions call succeeds and BEFORE `answer_fn` runs, with a partial `BaselineRunOutput`
(`failed=True`, carrying the questions and that call's attempt) -- so a hard crash
during answering still leaves that work recoverable; (b) `answer_fn`'s call and the
existing `_validate_answers_cover_questions` check are now wrapped in `try/except
Exception`, converting any answer-source failure into a well-formed `failed=True`
return (new `BaselineRunOutput.failure_reason` field records what happened) instead of
an uncaught exception -- the questions call's `attempts`/tokens/wall-clock and the
`questions` list are still on the returned object, not lost. No new path to a second
questions call exists anywhere in this recovery branch: the generate call is simply
never reached, same as before.

**Gap 7 -- A3's placeholder check flagged ANY square-bracket span, which is a real
false-positive generator (`[0,1]`, `[Ctrl+C]`, `[REQ-1]`, a literal array `[1, 2, 3]`
all matched).** `docs/EVALUATION_PROTOCOL.md` section 6.1 says Part A must be
"mechanical and unarguable" -- a check that flags a keystroke shortcut is arguable, and
arguable defeats the entire point of a mechanical check. Replaced with a pre-registered
keyword list (`tbd`, `todo`, `fixme`, `configurable`, `placeholder`), matched only
inside brackets, case-insensitive. Traded away on purpose: a placeholder shaped like
`[specified user needs]` (a real observed instance, Known Limitation 11) contains none
of these keywords and is no longer caught -- stated as a known, accepted precision-
over-recall tradeoff, not silently dropped. `evaluation/test_mechanical_checks.py` has
an explicit test asserting this specific shape is now NOT flagged, so the gap stays
visible in the suite rather than being rediscovered later as a surprise.

**Verification.** All five `evaluation/` suites green (72 runner + 51 mechanical + 40
blinding + 22 config_parity + 20 pricing = 205 checks) and all nine existing suites
unchanged (schemas 351, arch diagrams 88, harness 489, CLI 65, stages 163, stage_fns 67,
config 101, rotation 18). Four guarantees mutation-tested (broke each on purpose,
confirmed the associated test went red, reverted, confirmed green again): A5's
zero-count pre-population, A2's missing-key rejection, `enforce_fair_config`'s
temperature comparison, and `write_blinding_result`'s same-path rejection. `git diff
--check` clean. No API call made anywhere (`GEMINI_API_KEY`/`GEMINI_API_KEYS`/
`GROQ_API_KEY`/`GROQ_API_KEYS` all confirmed unset before and after). No frozen
evaluation input, historical result, or `docs/EVALUATION_PROTOCOL.md` touched. Not
committed -- reported for another review.

## 2026-08-17 -- Task 6 review round 2: eight more edge cases in the first fix round itself

A second review of the previous entry's fixes found eight real edge cases -- several
introduced BY the first round's own fixes, not missed by the original build. Recorded
here so the corrections are traceable to what they correct, not just described in
isolation.

**Finding 1 -- A1 and A2 fought each other.** Round 1's A2 fix (independent operator
label) required an entry in `operator_system_type_by_requirement_id` for EVERY id a
pooled case named, including ids A1 already reports as unknown -- forcing a caller to
invent an operator label for a requirement that was never real just to avoid A2's
rejection. Fixed: `check_technique_eligibility` now takes `known_requirement_ids` too,
and skips any id not in that set entirely -- no label required, no violation, no
`unlabeled_requirement_ids` entry either. A1 owns "this id doesn't exist"; A2 only
speaks about ids that do.

**Finding 2 -- A3 traded away the one real placeholder it was built to catch.** The
round-1 keyword rule fixed the false-positive problem but, as a side effect, stopped
catching `[specified user needs]` (the actual historical instance motivating A3 in the
first place) since that phrase shares no keyword with `tbd`/`todo`/`fixme`/
`configurable`/`placeholder`. Fixed: a second, separate pre-registered list,
`_PLACEHOLDER_EXACT_PHRASES`, matches specific known bracket-content phrases by exact
text (case-insensitive) rather than keyword -- `[specified user needs]` is now
registered there by name. Still pre-registered, not a return to "any bracket": `[0,1]`/
`[Ctrl+C]`/`[REQ-1]`/`[1, 2, 3]` match neither list and stay unflagged. A genuinely new,
unregistered placeholder shape still isn't caught -- narrower gap, not zero.

**Finding 3 -- blinding identity was `(arm, original_case_id)`, missing `doc_id`.**
Round 1 already carried `doc_id` through the mapping for provenance, but the actual
uniqueness check `pool_and_blind` enforced was keyed by arm alone -- two different
documents pooled into the same arm and both containing a case id "TC-1" would have
collided. Fixed: the key is `(arm, doc_id)`. New rule that falls out of this: pooling
cases from more than one distinct `doc_id` now requires every entry to carry a real
(non-`None`) `doc_id`, or the ambiguity is rejected up front -- a single-document pool
(everything sharing one `doc_id`, including all sharing `None`) is unaffected.

**Finding 4 -- the recorded seed could lie.** Round 1's `pool_and_blind(pooled_cases,
rng: random.Random, ...)` took an already-built RNG, while `write_blinding_result`
separately took its own `seed: int` argument -- nothing enforced the two matched, so a
caller could shuffle with one seed and record a different one as "the" seed. Fixed:
`pool_and_blind` now takes `seed: int` as its only randomness input, builds
`random.Random(seed)` internally, and carries that exact `seed` on the returned
`BlindingResult`; `write_blinding_result` lost its own `seed` parameter entirely and
reads `result.seed`. There is exactly one seed value in the whole call chain now --
"the mapping's seed is the exact seed that produced the shuffle" is true by
construction, not by caller discipline.

**Finding 5 -- a B2 checkpoint could be written but never actually resumed.** Round 1
added `checkpoint_fn` (fires after the questions call, before the operator answers) but
built no path back INTO a checkpoint -- interruption safety without a recovery
mechanism is half a feature. Fixed: `_b2_answer_and_generate` is now the shared tail of
both `run_b2` (after a live questions call) and the new `run_b2_resume` (after loading
a checkpoint) -- `run_b2_resume` has no `questions_prompt_path` parameter and no
reference to the questions prompt file anywhere in its body, so "never asks the
question again" holds structurally, not by discipline. The CLI gained a `b2-resume`
subcommand that rejects, before constructing any adapter: a checkpoint with the wrong
`arm`, a checkpoint produced under a different `(provider, model, temperature,
output_mode)` than the one being resumed with, a checkpoint for a different `doc_id`
than the requirement set given now, and a checkpoint whose recorded `b2_questions`
prompt hash no longer matches the current prompt file's content. `main()` also now
catches `KeyboardInterrupt` around the whole dispatch (EOF is already an `Exception`,
caught inside `_b2_answer_and_generate` since round 1) and returns exit code 1 -- the
same convention every other failure path already uses -- printing the resume command
rather than a raw traceback; any checkpoint already written survives, since it happens
before `answer_fn` runs.

**A gap this uncovered, fixed alongside it:** `--answers-json`'s file was read
(`_positional_answers_from_file`) AFTER the adapter was already constructed in `main()`
-- a missing or malformed answers file would only surface after the questions call had
already run (and, on a real provider, been paid for). Moved to before adapter
construction, alongside the other input/config/checkpoint checks.

**Finding 6 -- destinations weren't validated, and the final write wasn't atomic.**
New `_validate_output_destinations` (parent-directory existence, checked for `--output`
and its derived `.cost.json` path) runs before adapter construction, alongside every
other pre-flight check. The final run-output write changed from a plain
`Path.write_text` to `atomic_write_json` -- a successful, possibly paid, call's result
can no longer be lost to an interrupted write or a missing directory discovered only
after the call already happened.

**Finding 7 -- `BaselineRunOutput` recorded only 3 of 5 config scalars.** Added
`timeout_seconds`/`output_mode` fields -- the complete effective `BaselineCallConfig`
is now on every run record, not just provider/model/temperature. A freeze record with
a silent gap in its own provenance was the actual defect; this closes it.

**Finding 8 -- `frozen_arm_p_config` picked one stage's config without checking every
stage agreed.** `orchestrator/config.py`'s `StageOverride` legitimately allows a
per-stage override, and "resolves cleanly" (what `orchestrator/test_config.py` checks)
says nothing about uniformity. Fixed: `frozen_arm_p_config` now computes the
`(provider, model, temperature, output_mode)` tuple for every resolved stage and
raises, naming the diverging stage(s), if they don't all match -- rather than silently
returning `reference_stage`'s value while a different stage quietly used something
else. New test builds a real temporary run-config YAML (real prompt files, real
`resolve_run_config` call) with one stage's temperature overridden and confirms
rejection.

**Verification.** All five `evaluation/` suites green -- runner 109 (was 72), mechanical
57 (was 51), blinding 50 (was 40), config_parity 26 (was 22), pricing 21 (was 20); 263
total, up from 205. All nine existing suites unchanged and green (schemas 351, arch
diagrams 88, harness 489, CLI 65, stages 163, stage_fns 67, config 101, rotation 18).
Six guarantees mutation-tested this round (A1/A2 coexistence, cross-document blinding
identity, seed truthfulness, no-second-question resume, atomic destination preflight,
all-stage config parity): each broken on purpose, confirmed the associated test went
red -- three as explicit assertion failures, three as an uncaught exception/
`AssertionError` from a spy adapter, both counted as valid red -- reverted, confirmed
green again. `git diff --check` clean. No API call made
(`GEMINI_API_KEY`/`GEMINI_API_KEYS`/`GROQ_API_KEY`/`GROQ_API_KEYS` confirmed unset). No
frozen evaluation input, historical result, or `docs/EVALUATION_PROTOCOL.md` touched.
Not committed -- staged for another review.

## 2026-08-17 -- Task 6 review round 3: five findings, kept thesis-scale on purpose

A third review found five more real gaps. Scope was explicitly bounded mid-review to
"the smallest local solution" for research software, not production infrastructure --
recorded here since it shaped which fix was chosen for finding 3 specifically.

**Finding 1 -- a completed or differently-failed B2 output could be resumed and
generate again.** `b2-resume`'s checks (round 2) verified arm/config/doc_id/prompt hash
but never proved the FILE was actually the pre-answer checkpoint state -- a final
`BaselineRunOutput` (with its own `b2_generate` attempt, answers, test_cases) passes
every one of those checks and would silently re-run generation, corrupting the
one-questions-call/one-generate-call design. Fixed with the smallest structural marker
that closes it: `BaselineRunOutput` gains `checkpoint_phase: Optional[Literal[
"awaiting_answers"]]`, set ONLY at `run_b2`'s `checkpoint_fn` call site, nowhere else in
the codebase. A single new model validator (`_awaiting_answers_checkpoint_is_valid`)
enforces every property the finding listed -- B2 arm, `failed=True`, no answers/
test_cases yet, only `b2_questions` attempts, a valid 1..N retry sequence ending in
exactly one success with nothing after it, only the `b2_questions` prompt hash --
so an invalid "awaiting_answers"-tagged object cannot even be constructed or loaded,
not just discouraged by convention. `run_b2_resume` checks the marker itself (defense
in depth); `main`'s `b2-resume` path checks it first, before any of the other
checkpoint checks, and before any adapter. One field, one validator, one check at each
of two call sites -- no new state machine, no separate checkpoint type hierarchy.

**Finding 2 -- `doc_id` matching does not prove the requirement TEXT is unchanged, and
`doc_id` can be `None` on both sides.** A resumed run could generate against requirement
text that differs from what the questions were actually asked about, silently,
whenever `doc_id` happened to still match (or both were `None`). Fixed with one new
required field: `BaselineRunOutput.requirement_set_hash`, a sha256-hex-prefix of
`RequirementSet.model_dump_json()` (pydantic v2's stable field order makes this
deterministic), computed once by a new `_requirement_set_hash()` helper -- same
one-hash-field pattern this module already uses for `prompt_hash`, not a new concept.
`main`'s `b2-resume` path compares it before constructing an adapter, in addition to
(not instead of) the `doc_id` check, since `doc_id` still gives a clearer error message
for the common case.

**Finding 3 -- arm P had no persisted cost or wall-clock, and `docs/EVALUATION_
PROTOCOL.md` section 3 requires both for all three arms.** Checked whether an existing
mechanism already covers this before building anything, per the review's explicit
instruction: it does not -- `orchestrator/cli.py` prints a raw token total but never
converts it to cost, and `design/schemas.py`'s `StageAttempt`/`DocumentStageAttempt`
record tokens but no wall-clock duration at all; `RunMetadata.started_at` is the only
timestamp anywhere in the pipeline schema, one value for the whole run, not per call --
confirmed by reading the schema, not assumed. **Cost is recoverable from already-
persisted data**, so the smallest fix that closes it was built: `evaluation/
arm_p_report.py`, two functions, no new schema, no new persistence -- `compute_arm_p_cost`
reuses `evaluation/pricing.py`'s exact `FROZEN_PRICING_SNAPSHOT`/accounting rule over a
real `DocumentRunRecord`'s `attempts` + every `requirement_records[*].attempts`.
**Wall-clock is genuinely not recoverable** -- not a pipeline-schema change (out of
scope for evaluation-side tooling) and not reconstructible for an already-completed
run regardless, since the data was simply never recorded. Per the review's own
allowance ("a clear limitation or documented manual procedure is acceptable when
automation would be disproportionate"), `arm_p_wall_clock_seconds` returns `None`,
always, with the reason in its own docstring -- an honest, explicit "unavailable" a
P/B1/B2 comparison table can display, not a fabricated number, an approximation from
token counts, or a silent omission of arm P from the table entirely. Verified against
one real historical run directory (`docs/superpowers/results/2026-08-10-first-real-run/
groq`, read-only, never modified) as a smoke test, not just a synthetic fixture.

**Finding 4 -- resume's five-field config claim was actually a four-field comparison.**
`BaselineRunOutput` carries the complete effective config since round 2 (finding 7:
provider/model/temperature/timeout_seconds/output_mode), but `main`'s `b2-resume`
comparison tuple only ever checked four of the five -- `timeout_seconds` was omitted,
so a checkpoint whose questions call used one timeout and whose resumed generate call
used another would pass silently. One-line fix: `timeout_seconds` added to the tuple
on both sides, per the review's own stated preference ("the simpler expected fix is
exact five-field equality") over the alternative (persisting per-call configuration).

**Finding 5 -- no check stopped an output from overwriting an input.**
`_validate_output_destinations` (round 2) only checked that parent directories exist,
never that a file about to be WRITTEN wasn't also a file the run needed to READ FROM.
One new function, `_validate_no_path_collisions`, checked before adapter construction
alongside every other pre-flight check: rejects if any output artifact
(`--output`, the derived `.cost.json`, and for `b2` the derived `.checkpoint.json`)
resolves (`Path.resolve()`, so relative-vs-absolute spelling of the same file still
collides) to the same file as any input (the `RequirementSet`, `--answers-json`, or
the resume `--checkpoint`). Deliberately does NOT check output-to-output reuse across
two SEPARATE invocations -- `--output out.json` naming the SAME file on an initial `b2`
run and a later `b2-resume` run is the one intentional overwrite this CLI supports
(the later run's final result replacing the earlier partial/failed one), and it never
appears as a same-invocation collision in the first place, so no special case was
needed to preserve it -- stated explicitly in the function's own docstring and in a
dedicated (trivial) test, per the review's instruction to justify any preserved
overwrite behavior rather than leave it implicit.

**A masking bug found and fixed while writing these tests, not a sixth finding:**
several new CLI-level tests pointed `--answers-json` at a nonexistent file
(`unused.json`), relying on an EARLIER checkpoint check to reject first. When mutation-
testing the content-hash and path-collision guarantees, this masked the mutation --
the missing-answers-file error fired instead and the test still reported the expected
exit code, for the wrong reason. Fixed by writing a real, valid, empty answers file at
every such call site (six in total) so each test can only pass for the reason it
claims to. Recorded because it is exactly the kind of test-suite gap mutation testing
exists to catch, and it did.

**Scope, stated explicitly.** No checkpoint framework, workflow engine, storage
abstraction, or migration system was introduced. Finding 1 is one field plus one
validator; finding 2 is one field plus one helper function; finding 3 is one new
two-function module that computes what is computable and honestly declines what is
not; finding 4 is a one-line comparison fix; finding 5 is one function. All five reuse
existing patterns already established in rounds 1-2 (`_prompt_hash`'s hash-field
convention, `FROZEN_PRICING_SNAPSHOT`'s reuse-not-reinvent precedent, the
before-any-adapter pre-flight check list) rather than introducing new ones.

**Verification.** All six `evaluation/` suites green -- runner 137 (was 109), mechanical
57, blinding 50, config_parity 26, pricing 21 (all unchanged from round 2), plus new
arm_p_report 13; 304 total, up from 263. All nine existing suites unchanged and green
(schemas 351, arch diagrams 88, harness 489, CLI 65, stages 163, stage_fns 67, config
101, rotation 18). Four central guarantees mutation-tested (checkpoint structural
validity, requirement-set content-hash binding, timeout comparison, path-collision
check): each broken on purpose, confirmed the associated test(s) went red -- including
one case (the checkpoint attempts-shape check) where the FIRST mutation attempt was
masked by a redundant check and passed green, caught only by writing a properly
isolated test after noticing the false pass -- reverted, confirmed green again. `git
diff --check` clean. No API call made anywhere
(`GEMINI_API_KEY`/`GEMINI_API_KEYS`/`GROQ_API_KEY`/`GROQ_API_KEYS` confirmed unset
throughout). No frozen evaluation input, historical result, or
`docs/EVALUATION_PROTOCOL.md` touched (the one real historical run directory used in
`test_arm_p_report.py` was only ever read). Not committed -- staged for another review.

## 2026-08-17 -- Task 6 review round 4: two direct-call gaps, one documentation correction

A fourth review found two code gaps in round 3's own fixes and one place round 3's own
write-up overclaimed what it had actually done. Kept to the same thesis-scale
constraint as round 3 -- no new framework, two small direct checks and one tightened
condition.

**Finding 1 -- `run_b2_resume` called directly bypassed the requirement-set/config
checks entirely.** Round 3 added these checks to `main`'s `b2-resume` CLI path, but
`run_b2_resume` itself only ever checked `arm`/`checkpoint_phase` -- a caller invoking
the function directly (not through `main`) could combine an old checkpoint's questions
with a `requirement_set` whose text had since changed, or a different `config`, and
nothing would stop it. Confirmed with a direct repro before fixing: a mismatched-hash
call went through and made a real (fake-adapter) call. Fixed: `run_b2_resume` now
compares `checkpoint.requirement_set_hash` against `_requirement_set_hash(
requirement_set)` and all five checkpoint config fields against `config`, both BEFORE
calling `_b2_answer_and_generate` -- so before any `adapter.complete` call, for any
caller, not just ones going through `main`. `main`'s own checks are unchanged (they
still reject before adapter CONSTRUCTION, which `run_b2_resume` -- receiving an
already-built adapter -- structurally cannot do); the two layers are now genuinely
redundant defense in depth rather than the CLI being the only real gate.

**Finding 2 -- the retry-sequence check accepted an impossible-but-uncaught shape.**
The round-3 validator rejected "success" anywhere before the final attempt, but not
"fatal" -- so a `fatal`-then-`success` sequence passed, even though `_call_once`
(`evaluation/runner.py`) can never actually produce it: a `StageCallFatal` short-
circuits immediately, recording exactly one attempt and returning, never retrying.
Confirmed with a direct repro before fixing: constructing that exact shape succeeded.
Tightened to require every attempt before the last be `"failed"` or `"partial"` --
the only two results `_call_once` ever retries after -- which rejects both `"success"`
and `"fatal"` in that position with one condition instead of one. Guards against a
hand-edited or corrupted checkpoint file carrying the impossible shape, even though no
code path in this repository can produce it live.

**Finding 3 -- round 3's finding-3 write-up read as resolved; it was only half
resolved.** `evaluation/arm_p_report.py`'s docstring is corrected (not the code -- this
finding asked for no new infrastructure) to state explicitly: arm-P cost is genuinely
recoverable and computed; historical arm-P wall-clock is genuinely NOT recoverable and
`arm_p_wall_clock_seconds` returning `None` must never be read as satisfying
`docs/EVALUATION_PROTOCOL.md` section 3's three-arm wall-clock requirement; and before
the real (frozen, paid) evaluation run, arm P's wall-clock must be measured externally
(a start/end timestamp around the one real run, five-second manual subtraction) and
persisted alongside its cost report, the same way `BaselineRunOutput.
total_wall_clock_seconds` already sits next to B1/B2's cost. A five-step manual
checklist is now in the module docstring. No pipeline-schema change, no timing
infrastructure -- a one-time manual step for the one real arm-P run this evaluation
needs, which is what the review asked to confirm before building anything larger.

**Verification.** `evaluation.test_runner` grew from 137 to 145 checks (four new: two
proving the direct-call `run_b2_resume` rejections with a spy adapter, one confirming a
matching direct call still succeeds -- not over-rejected, two for the tightened
retry-sequence check -- fatal-then-success rejected, partial-then-success still
accepted). All other `evaluation/` suites unchanged: mechanical 57, blinding 50,
config_parity 26, pricing 21, arm_p_report 13. All nine existing suites unchanged and
green. Both new code guarantees mutation-tested (the direct-call requirement-set-hash
check, the tightened retry-sequence check): each broken on purpose -- one crashed with
`IndexError: pop from empty list` (the spy adapter's `.complete()` was actually
reached), one produced an explicit assertion failure -- reverted, confirmed green
again. `git diff --check` clean. No API call made anywhere
(`GEMINI_API_KEY`/`GEMINI_API_KEYS`/`GROQ_API_KEY`/`GROQ_API_KEYS` confirmed unset). No
frozen evaluation input, historical result, or `docs/EVALUATION_PROTOCOL.md` touched.
Not committed -- staged for another review.
