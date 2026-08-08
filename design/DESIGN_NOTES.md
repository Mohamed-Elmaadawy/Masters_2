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
   b. Quality Checker       (Requirement, Classification, ConsistencyReport, DependencyReport -> QualityReport)
   c. Refiner (only if QualityReport.passed is False)
        (Requirement, QualityReport -> RefinerTurn)     -- questions to human
        (RefinerAnswer[] -> RefinedRequirement)          -- human's answers back in
        loops back to Quality Checker
3. Test Design Strategy Selector (RefinedRequirement, Classification,
                                   DependencyReport.dependencies_for(id) -> TestStrategy)
4. Test Case Generator            (RefinedRequirement, TestStrategy,
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

## 3/4. Test Design Strategy Selector & Test Case Generator — per-requirement, with targeted dependency context

Both stay per-requirement: one `RefinedRequirement` in, one `TestStrategy` or
`TestPlan` out — never bulk across the whole document. But each is now also given
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

**2. The system verifies testability-structure, not domain truth.** Covered in the
Refiner section above: the pipeline can confirm a refined requirement no longer fails
the same objective checks that originally flagged it, but it has no way to verify the
human's answer is factually correct for the actual system being specified. Only the
human has that authority; no design closes this gap without an independent domain
oracle.

**3. `PERFORMANCE` doesn't distinguish hard real-time from soft performance targets.**
A missed deadline in an embedded control system (potentially a safety-relevant
failure) and a slow-loading web page (a tunable UX issue) both currently map to the
same `TestTechnique.PERFORMANCE` label -- see the `TestTechnique` section above.
Left unresolved because there's no existing criticality/severity concept anywhere
else in the schema, and nothing in the current reference documents establishes that
safety-critical embedded testing needs to be in scope. Revisit only if that changes.

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
