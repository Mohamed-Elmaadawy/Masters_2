# Claude Code handover — evaluation-readiness work (2026-08-15)

Paste the section below into Claude Code, in the repo root. It is self-contained.

---

## Context

This repo is a Master's thesis pipeline: an 8-stage LLM system that refines natural-language
requirements and generates test cases. Python, Pydantic, no agent framework, free-tier
Gemini/Groq. The design rationale and every known limitation live in
`design/DESIGN_NOTES.md`; read the "Known Limitations" section before starting.

Two documents define the work, and they are deliberately separate:

- **`docs/superpowers/plans/2026-08-15-system-changes-before-freeze.md`** — changes to the
  system under test (S1–S4), plus the declined list. This is what Tasks 0–5 implement.
- **`docs/EVALUATION_PROTOCOL.md`** — how the system will be measured (E1–E4). Tasks 6–8
  build the machinery this needs, but the protocol itself is not yours to change.

Keep them apart. Everything in the first must be complete and frozen before anything in the
second executes; that boundary is what shows the system was not tuned during evaluation. If a
task tempts you to put a system change in the protocol document or a measurement decision in
the design notes, stop and say so.

A full evaluation is about to be run from scratch on corpora that have never been used
(`datasets/EVALUATION_DATASETS.md`). All prior runs are being superseded deliberately. The
work below is everything that must exist *before* that run, because none of it can be added
afterwards without re-spending the API budget and the operator's answering effort.

## Ground rules

- **Simplest thing that works.** No new dependencies, no new abstraction layers, no agent
  framework. If a change needs complexity, say why before writing it.
- **Do not merge stages or introduce dynamic orchestration.** The linear pipeline is a
  deliberate design constraint.
- **Challenge anything below that is wrong.** These are decisions made in discussion, not
  verified against the code. If a proposed field name, outcome member, or control-flow
  assumption does not match `design/schemas.py` or `orchestrator/pipeline.py`, say so and
  propose the correct shape rather than forcing it.
- **Full suites stay green after every task.** Last recorded baseline: schemas 326, arch
  diagrams 88, diagrams 13, harness 464, CLI 39, stages 163, rotation 18, config 95 = 1,206
  checks, plus 14 scenario configs loading and 12 fixtures validating.
- **Record each task in `IMPLEMENTATION_LOG.md`** in the existing style (Changed / Why /
  Impact), and update `design/DESIGN_NOTES.md` where a decision or a measured result belongs
  there.
- **Do not touch any prompt in `orchestrator/example_prompts/` unless a task below names
  it.** Prompt changes move the freeze point.

## Task 0 — record the decisions

Append `docs/superpowers/plans/2026-08-15-system-changes-before-freeze.md` to
`design/DESIGN_NOTES.md` as a new dated section after the last dated entry, keeping the
existing heading style. Do not restructure the Known Limitations section; the new entry
references it.

Leave `docs/EVALUATION_PROTOCOL.md` where it is — it is a standing document, not a design
note, and does not belong inside `DESIGN_NOTES.md`.

## Task 1 — capture the operator's system-type label (S2; small, do it early)

The Classifier's accuracy currently has n=0 because no human label has ever been collected,
and it cannot be reconstructed after a run.

Add capture of an operator-supplied `SystemType` alongside the model's, recorded per
requirement, with provenance for who set each. Decide from the code whether this belongs on
the classification record, the run record, or the human-interaction protocol — propose the
shape before implementing. Two constraints: it must not silently become a third blocking
human interaction point in `HumanFns` unless that is explicitly justified (see
`DESIGN_NOTES.md`, Known Limitation 9, "Should the human confirm or override the system
type?"), and the model's own label must remain untouched so the two can be compared.

## Task 2 — Quality Checker stability harness (E3 level 1; small, no pipeline change)

A standalone script that calls the Quality Checker stage alone — one requirement, 5 calls,
no refinement loop, no other stage — and reports whether the returned issue sets are
identical across calls. (5 is set by `docs/EVALUATION_PROTOCOL.md` §4 level 1; make it a
parameter, default 5.)

Motivation: `DESIGN_NOTES.md` Known Limitation 10 records the checker returning opposite
verdicts on character-identical input in consecutive rounds. Every per-category
precision/recall figure in the evaluation is a single draw until this is quantified.

Keep it a throwaway-grade measurement script under `docs/superpowers/`, not a new module in
`orchestrator/`. Output: per requirement, the 5 issue sets and a simple agreement figure.

## Task 3 — document extraction for the reserved corpora (S1; the big one)

`orchestrator/extract_document.py` reads only the already-JSON-shaped
`datasets/requirements_dataset.json`. The evaluation corpora are not in that shape:

- `datasets/pure-full/` — 79 PURE documents, PDF/DOC/HTML, unstructured
- `datasets/requirements-xml/` — the 18-file annotated XML subset, already committed
- Dalpiaz, PROMISE NFR, Riaz — each in their own shape, see `datasets/EVALUATION_DATASETS.md`

Produce `RequirementSet`-shaped output from these. Propose the approach before building:
which formats to support first, whether per-corpus adapters or one path, and how
requirement boundaries are decided in unstructured documents — that last one is a
methodological choice that needs recording in `DESIGN_NOTES.md`, not a silent heuristic.

**Build this in `tools/`, not in `orchestrator/extract_document.py`.** That module's docstring
states its contract explicitly — *"deliberately not a data pipeline: one file in, one doc_id
selected, one file out — nothing about the extraction is configurable beyond that"* — and
corpus extraction violates it. `tools/extract_pure_xml.py` already exists and is the right
neighbourhood. Leave `extract_document.py` alone; if the two need to meet, they meet at the
`RequirementSet` JSON file on disk, which is already the CLI's input shape.

**Fold in the corruption scan.** `DESIGN_NOTES.md` Known Limitation 5 records that
`1998 - themas.xml` had `<=` flattened to `=` during PDF-to-XML extraction, and that the
18-file subset was scanned but the 79-document corpus never was. Scan for three signatures:
`X = Y = Z` comparison chains, `T_LT`-shaped underscore tokens, and surviving Unicode math
(`<=`, `>=`, `!=`, `+/-`). Report per document; do not repair silently.

**Exclusions:** `1998 - themas.xml` and `2007-ertms.xml` are marked spent for design
purposes in `datasets/EVALUATION_DATASETS.md` and must be excluded from the evaluation
subset.

**Then measure, before Task 5:** how often a genuine `NON_ATOMIC` requirement occurs in the
extracted corpus. The detector over-flags (2 of 5 flags in the 2026-08-13 suite were
conjunction-splits), so report flagged-vs-genuine separately rather than a raw count.

## Task 4 — phase the pipeline (S3)

Implements S3, which is option B under `DESIGN_NOTES.md` Known Limitation 7.

Currently `run_document_stages` runs the Consistency Checker and Dependency Mapper once, on
the original text, and `run_requirement` computes `relevant_conflicts` /
`relevant_dependencies` once from that report and holds them constant through every
refinement round and into strategy selection and test generation. A rewrite that introduces
a new conflict or dependency is never seen.

Target shape: pass A classifies, quality-checks and refines every requirement; document-level
analysis re-runs on the refined set; pass B does strategy selection and test generation from
the fresh reports.

Five constraints, each one a place this goes wrong:

1. **Keep both generations of reports on the document record — do not overwrite.** The diff
   between initial and final analysis is a reportable result.
2. **Update `orchestrator/test_harness.py::test_resume_positions` first.** That executable
   spec exists because the resume spec drifted once before. Change the test, watch it fail,
   then make it pass.
3. **Update `DocumentOutcome`'s docstring as part of this change, not afterwards.** It
   currently states that the document-level phase *"finishes before per-requirement processing
   begins"* — this change makes that sentence false, and it is the kind of spec drift
   `test_resume_positions` exists to catch. Check the surrounding comments too: the reasoning
   about `pending_requirement_ids` being derivable may also need revisiting once there are two
   phases.
4. **Model the second analysis as a distinct document-stage phase, not a re-run of the
   first.** Do not introduce a "this analysis is stale, redo it" state — it complicates
   `resume_document`'s position logic and the terminal `DocumentOutcome` semantics.
5. **A cycle found by the second analysis is reported, not routed back to the Refiner.**
   Refinement is finished by then; routing re-opens a completed phase. Record it as a
   document-level finding.

Pass A's inputs are unchanged — it still receives the original analysis. Only what strategy
selection and test generation see changes.

Do this on a branch and keep the suites green throughout.

## Task 5 — human-supplied `NON_ATOMIC` splits (S4; after Tasks 3 and 4)

Implements S4. **Do not start until Task 3 reports the genuine `NON_ATOMIC`
frequency and Task 4 has landed.** Task 4 is what makes this affordable: a split changes
requirement-set membership, which invalidates the document-level reports — and phasing
already re-derives them.

Proposed shape. Confirm every name against `design/schemas.py` before implementing; these
are proposals from discussion, not verified fields:

- The Refiner never splits. The operator may answer a `NON_ATOMIC` question with the split
  itself — an optional list of fragment texts on `RefinerAnswer`.
- The requirement terminates with an outcome meaning *split*, distinct from failure. A
  distinct `RunOutcome` member is preferred over reusing `CAP_STOPPED` with a reason, because
  a split is a success and reporting it as a cap corrupts the outcome counts — but check
  which is cheaper against the existing `RunOutcome` validators and say which you chose.
- Between pass A and the second document analysis, the orchestrator materialises fragments
  with derived ids (`REQ-7.1`, `REQ-7.2`, …), each recording its origin requirement.
- Fragments re-enter pass A. **A fragment cannot itself be split.** One generation only —
  this is what makes it terminate.
- `_test_generator_extra_check`'s `known_requirement_ids` extends to derived ids. The origin
  field preserves traceability to the requirement as written in the source document.
- Dependency links naming the original id are resolved by re-derivation in the second
  analysis, not by patching.

No model splits anything, no new stage, no new LLM call, no similarity heuristic.

Also record, for the evaluation: any per-requirement rate now needs an explicit denominator
(before or after splitting). Make that explicit wherever counts are produced.

## Task 6 — baseline arms (E2; independent, can run in parallel with 3–5)

Two comparison arms, same model, temperature, documents and output shape as the pipeline,
scored with the same rubric:

- **B1, naive:** whole requirement set in, test cases out, one prompt, no human interaction.
- **B2, naive + human:** same, plus one round of clarifying questions the model asks and the
  operator answers.

Neither receives the `IssueCategory` taxonomy, the `ELIGIBLE_TECHNIQUES` rules, or the
refinement loop — those are the treatment being tested.

**Write the B1/B2 prompts and the runner for review before executing anything.** The main
risk here is a strawman: a baseline the reviewer believes was handicapped discredits the
comparison rather than merely failing to support it. The prompts should be what a competent
practitioner would actually write.

Record cost and wall-clock for all arms.

**Also build the Part A mechanical checks** from `docs/EVALUATION_PROTOCOL.md` §6.1 —
traceability, technique eligibility, placeholder contamination, duplicate-candidate count,
volume per requirement. These run over the pooled output of all three arms and need no rater.
Part B is scored by hand and is not your work; what you provide for it is the **blinding
step**: pool every case from P/B1/B2, strip arm identity, assign fresh random ids, shuffle,
and write the mapping to a separate file that scoring does not read.

## Task 7 — repeat-run support (E3)

- **Level 1** is Task 2 (Quality Checker alone, 5 calls).
- **Level 2:** 5 pipeline runs over a subset of 8–10 requirements spanning outcome types
  (`COMPLETED`, `CAP_STOPPED`, `CAP_GENERATED`, a document-stage failure), all replaying the
  same frozen answer transcript — same mechanism as
  `docs/superpowers/results/2026-08-14-live-answers/answers.json` and its replay driver.

**Count and report replay fallback trips per run.** A repeat run can raise a question the
transcript has no answer for; this already happened once (`THEMAS-REQ-E`, v1 -> v2, one miss
and one outcome flip). Report them as data, do not absorb them.

## Task 8 — the freeze record (E4)

Emit, and store with the run outputs: prompt file commit hash, model identifier,
temperature, provider and tier, answer transcript hash, freeze date, and the extraction
commit from Task 3. Comparability was already lost once when prompt v1 -> v2 landed between
measurement batches.

## Suggested order

Task 0 → Task 1 → Task 2 → Task 3 → Task 4 → Task 5, with Task 6 in parallel from the
start, then Tasks 7 and 8 immediately before the run.

## Explicitly out of scope — do not build these

Each was measured, and the measurement is the reason. See the "Declined" list in
`docs/superpowers/plans/2026-08-15-system-changes-before-freeze.md`.

Test-case de-duplication; `refined_text: list[str]` as a general redesign; the undefined-
notation pre-pass or a new `IssueCategory` for it; a hard-real-time/soft `PERFORMANCE` split
or an `EMBEDDED` `SystemType`; collapsing `SystemType` to a binary; a web technique pool
(revisit only if Task 3 turns up web SRS documents); pairwise testing; the no-op-rewrite
validator; loosening `TestPlan`'s strict coverage rule.
