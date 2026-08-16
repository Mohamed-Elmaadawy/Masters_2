# Evaluation Protocol

**Status: living document. Edit freely until the freeze date is set in §5, and not after.**

This document defines *how the pipeline is measured*. It deliberately contains no changes to
the pipeline itself — those are in `design/DESIGN_NOTES.md`, dated entry "2026-08-15 — System
changes to make before the evaluation freeze" (items S1–S4).

**The separation is the point.** Every system change must be complete and frozen before
anything here executes. A protocol document that also describes changes to the system under
test cannot demonstrate that the system was not tuned during evaluation, which is the specific
criticism this design is most exposed to.

Written 2026-08-15. Nothing here has been run.

---

## 1. What is being claimed

The pipeline refines under-specified natural-language requirements through human-in-the-loop
clarification, and generates test cases from the refined result. The claim to be supported or
refuted is that **the staged pipeline produces better refined requirements and better test
cases than a competent single-prompt use of the same model on the same inputs**, and that the
improvement is attributable to the pipeline's structure rather than merely to having a human
in the loop.

Both halves of that sentence are testable only because of the baseline arms in §3. Without
them there is no claim, only a description.

---

## 2. E1 — Who answers the Refiner

**The operator answers live, against the source documents. This is the primary arm.**

The scripted refusing answer policy is retained **only** as a contrast arm and never as the
primary. The reason is measured, not stylistic: under prompt v2, the Rewriter rule *"if an
answer supplies no concrete value, make no change"* fires universally against a refusing
answerer, so the Rewriter becomes formally inert (`DESIGN_NOTES.md`, Known Limitation 11).
Under that policy, *"the rules work as intended"* and *"the refinement stage is disabled"* are
indistinguishable by construction. A primary run under it would measure nothing about
refinement.

Two supporting results already on record:

- The 2026-08-13 suite produced 38 no-op rewrites out of 47, every one traceable to an answer
  that declined to supply information. **No no-op followed an answer carrying real content.**
- The 2026-08-14 live session produced the only substantive changes this project has seen —
  one cross-requirement fix (`PURE-THEMAS-R6-P`, 5°F → 3°F, reaching `COMPLETED`) and two real
  referent resolutions.

**Procedure.** Answers are given once, live, and frozen in the shape of
`docs/superpowers/results/2026-08-14-live-answers/answers.json`. All repeat runs (§4) replay
that frozen transcript, so human input is held constant and only model variation is measured.

**Threats this introduces, to be reported and not absorbed:**

- One operator, who is also the author. No second annotator, no inter-rater reliability, and a
  direct experimenter-bias exposure.
- The corpus splits in two and aggregates hide it: requirements from real SRS documents have a
  source to answer *from*; illustrative or LLM-generated sentences do not, so an honest answer
  there is indistinguishable from a refusal. **Report per requirement and split by group.**
- A refusing answer can manufacture a false success. On `LUITEL-R7` the scripted policy
  asserted the requirement was "one causal step" — false for that fixture — and the checker
  accepted it, yielding `COMPLETED`. Any contrast-arm `COMPLETED` count is biased in the
  direction that flatters the pipeline.

---

## 3. E2 — Baseline arms

Three arms, all on the same documents, same model, same temperature, same output shape, scored
with the same rubric:

| Arm | Description |
|---|---|
| **P** | The full pipeline, operator answering (§2). |
| **B1** | Naive. Whole requirement set in, test cases out, one prompt, no human interaction. |
| **B2** | Naive + human. Same as B1, plus one round of clarifying questions the model asks and the operator answers. |

Neither baseline receives the `IssueCategory` taxonomy, the `ELIGIBLE_TECHNIQUES` rules, or
the refinement loop. Those are the treatment being tested.

**B2 is the load-bearing arm.** It separates *the pipeline's structure helps* from *having a
human in the loop helps*. That is the first question an examiner asks, and P-versus-B1 alone
cannot answer it.

**The main risk here is a strawman.** A baseline the reviewer believes was handicapped
discredits the comparison rather than merely failing to support it. The B1/B2 prompts should
be what a competent practitioner would actually write, and they are to be reviewed before any
arm executes.

**Record cost and wall-clock for all three arms.** "The pipeline costs 8× the baseline" is a
legitimate result, and a thesis that reports it is more credible than one that does not
mention cost.

---

## 4. E3 — Repeat runs

Two levels, both replaying the frozen transcript from §2.

**Level 1 — stage only.** The Quality Checker alone: one requirement text, 5 calls, no
refinement loop, no other stage. This isolates detector stability from loop dynamics and
directly addresses the defect recorded under Known Limitation 10 — the checker returned
opposite verdicts on character-identical input in consecutive rounds. Cheapest measurement in
this document, and until it exists every per-category precision/recall figure is a single draw
from an unmeasured distribution.

**Level 2 — whole pipeline.** 5 runs over a subset of 8–10 requirements chosen to span outcome
types (`COMPLETED`, `CAP_STOPPED`, `CAP_GENERATED`, and a document-stage failure).

Everything else runs once. Report full-corpus numbers with the Level 2 stability figure as a
stated caveat.

**Replay drift is data.** A repeat run can raise a question the frozen transcript has no answer
for — this already happened (`THEMAS-REQ-E` under prompt v2: one miss, one outcome flip, both
reported as regression-guard trips in `RESULTS-V2-LIVE.md` rather than absorbed). Count
fallback trips per run and report them.

---

## 5. E4 — The freeze record

State these at the top of the evaluation chapter and change none of them once the run starts:

- prompt file commit hash
- model identifier, provider and tier
- temperature and any sampling parameters
- extraction commit (S1)
- answer transcript hash (§2)
- freeze date
- the commit at which S1–S4 were all complete

Comparability has already been lost once, when prompt v1 → v2 landed between measurement
batches. The `prompt_hash`-per-attempt provenance work exists to make that visible after the
fact; this record is what makes it unnecessary.

---

## 6. What gets measured

To be finalised before the freeze — this section is the one most likely to still be wrong.

**Per stage:**

- Quality Checker: precision and recall per `IssueCategory`, against an annotated subsample.
  Expect `VAGUE_PRONOUN` to underperform (Known Limitation 4 — it is one of the two hardest
  smells to detect even with dedicated tooling); a low score there is not evidence of an
  implementation bug.
- Classifier: accuracy against the operator's label (S2).
- Strategy Selector: agreement with a stated expected technique per requirement.
- Test Generator: quality against the rubric in §6.1. There is no reference test suite for any
  document in these corpora and the specified systems do not exist, so execution, coverage and
  mutation testing are all unavailable. §6.1 is what replaces them.

**Per requirement:**

- Outcome, and — separately — whether the text actually changed. "Refined" and "refined, text
  unchanged" are different numbers (Known Limitation 10, fix (a)). Every recorded run can be
  re-scored retrospectively, since the texts are persisted.
- **Text-change rate is not a proxy for improvement.** On the same nine requirement-slots the
  refusing policy and the live operator both changed 4/9, with completely different substance.
  Report *what* changed, per requirement.

**Per document:**

- The diff between the initial and final consistency/dependency analysis (available only
  because S3 keeps both).
- Suspected duplicate test cases, as an advisory count.

**Denominators must be explicit.** Once S4 lands, a split turns one requirement into several,
so every per-requirement rate needs to state whether the denominator is pre-split or
post-split.

**One open item, flagged rather than assumed:**

- **Outcome labels alone cannot rank two configurations**, because a rewrite can clear one
  issue category and create another (`THEMAS-REQ-E` flipped `COMPLETED` → `CAP_STOPPED` under
  v2 for a genuine new incompleteness). Report final text quality alongside outcomes.

---

## 6.1 Test-case quality rubric

**The problem this solves.** The pipeline's headline deliverable is test cases, and it is the
one output with no objective comparator: no reference suite exists for any document in these
corpora, and the specified systems are not available to run tests against. Human judgement is
therefore the only instrument left — and it is being applied by the author, to his own system,
against baselines he also wrote. The rubric below exists to make that judgement reproducible
rather than to pretend it is objective.

**Split the score in two.** Part A is mechanical and unarguable. Part B is judged. Report them
separately and never merge them into a single quality number — the mechanical half is evidence,
the judged half is an opinion made systematic, and combining them disguises which is which.

### Part A — mechanical (computed, no rater)

Confirm every field name against `design/schemas.py` before implementing; these describe
properties, not an assumed schema.

- **A1 Traceability.** Every id in `requirement_ids` resolves to a requirement in the input
  set, including derived fragment ids once S4 lands.
- **A2 Technique eligibility.** The declared technique is in `ELIGIBLE_TECHNIQUES` for the
  requirement's classified `SystemType`. (Already enforced for arm P; for B1/B2 it is a real
  measurement, since nothing constrains a baseline.)
- **A3 Placeholder contamination.** The case text contains no unresolved marker
  (`[TBD…]`, `[configurable…]`, and similar). These were emitted by the Rewriter in earlier
  runs and can propagate into generated cases.
- **A4 Duplicate candidates.** Count cases whose `requirement_ids` sets are identical.
  **Advisory only — never merged, never deleted** (see the declined list in `DESIGN_NOTES.md`).
- **A5 Volume.** Cases per requirement, per arm. Not a quality measure on its own; needed to
  stop a high-volume arm looking better by producing more of everything.

### Part B — judged, blind, yes/no

Five criteria. Each is answered **yes or no**, never a 1–5 rating: two raters agree on yes/no
questions and diverge on opinion scales, and reproducibility is the entire point here.

- **B1 Concrete inputs.** Could a tester execute this without asking a follow-up question?
  Specific values, states or preconditions are named.
- **B2 Checkable oracle.** The expected result is observable and unambiguous — you could tell
  pass from fail without judgement.
- **B3 Requirement fidelity.** The case tests what the cited requirement actually says, rather
  than something adjacent, invented, or assumed about the system.
- **B4 Technique conformance.** The case genuinely exercises its declared technique — a
  boundary-value case tests a boundary, an equivalence-partitioning case picks a representative
  of a named class. A correct label on a case that does not do this scores no.
- **B5 Non-redundant.** Within the whole pooled set, this case tests something no other case
  already tests. Scored in a **second pass**, after B1–B4, because it requires seeing the pool.

**Decision rule: if unsure, score no.** This makes the rubric conservative and reduces drift
between raters — and it biases against the pipeline, which is the correct direction when the
author is the rater.

**Report per criterion, per arm.** A total hides which dimension actually differs, and "the
pipeline wins on B4 and ties everywhere else" is a more useful and more defensible finding than
a single averaged score.

### Procedure

1. Pool every test case from arms P, B1 and B2 into one set.
2. Strip arm identity, assign fresh random ids, shuffle. Keep the mapping in a separate file
   that is not opened until step 5.
3. Score Part A mechanically over the pool.
4. Score Part B by hand, B1–B4 first, then B5 in a second pass.
5. Unblind and compute per-arm, per-criterion rates.
6. **Second rater on a subsample of at least 20 cases**, drawn from the same shuffled pool.
   Report raw agreement and Cohen's kappa.

Record who rated, when, and how long it took.

### Pre-registration

**This rubric is frozen together with the system, under the freeze record in §5.** It must not
be adjusted after any output has been seen. A rubric written or revised after looking at
results measures the results.

### Threats specific to this rubric

- **The author designed the rubric and is the primary rater.** The second rater (step 6) and
  the default-to-no rule (Part B) are mitigations, not solutions. State it.
- **Blinding is imperfect.** Pipeline output may be stylistically recognisable — formatting,
  phrasing, or the presence of technique labels — so a rater may infer the arm despite the
  shuffle. Report this honestly rather than claiming a clean blind, and if a systematic tell is
  noticed during scoring, record what it was.
- **Part B measures whether a test case is *well-formed and faithful*, not whether it would
  find a real defect.** Nothing here is executed, so fault-detection capability is out of reach
  entirely. Do not let rubric scores be described as effectiveness.

---

## 7. Threats to validity, to carry into the write-up

1. **One operator, who is the author.** No second annotator (§2).
2. **Stage output is non-deterministic on identical input** — quantified by §4 Level 1, not
   eliminated by it.
3. **Partial design/evaluation separation.** THEMAS and ERTMS are correctly marked spent in
   `datasets/EVALUATION_DATASETS.md`, but prompt v2 was tuned against behaviour fixtures drawn
   from those same documents, so the v1/v2 comparison measures fit to its own tuning set.
   Report when prompts were frozen.
4. **Single model family.** A Groq adapter exists; no cross-model comparison does. Free-tier
   rate limits shaped the design itself (throttling, key rotation, and the rejection of at
   least one option under Known Limitation 5), so a resource constraint is also a scope
   constraint.
5. **Source-data corruption.** Confined to one of 18 annotated files when measured; the
   79-document corpus is unscanned until S1 does it.
6. **Small n throughout the prior record.** Most cited findings are n=1 to n=3. State n inline.
7. **The system verifies testability-structure, not domain truth** (Known Limitation 2). The
   record isolates every human-supplied domain claim for audit, which is the difference between
   *unverified* and *unverified and untraceable* — state it as that, and not as a narrower gap
   than it is.
