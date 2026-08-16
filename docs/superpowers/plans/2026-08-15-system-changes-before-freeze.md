# 2026-08-15 — System changes to make before the evaluation freeze

> **Placement:** append to `design/DESIGN_NOTES.md` after the last dated entry.
>
> **Scope of this entry: changes to the system under test only.** How the system will be
> measured — answer policy, baseline arms, repeat runs, the freeze record — is deliberately
> *not* here. It lives in `docs/EVALUATION_PROTOCOL.md`. The two are kept apart on purpose:
> everything in this entry must be complete and frozen *before* anything in that document
> executes, and mixing them makes it impossible to show that the system was not tuned during
> evaluation.

**What changed, and it changes the filter.** A full re-evaluation is planned from scratch.
Every deferral in this file justified by *"it changes the system under test mid-evaluation
and would invalidate prior runs"* no longer holds — the prior runs are being superseded on
purpose, so the freeze point moves forward to whenever the work below lands. That argument is
retired as of this entry; do not cite it again without checking where the freeze point now
sits.

The replacement filter, applied to every item below: **can this be added after the frozen run
without re-spending the API budget and the operator's hand-answering effort?** If no, it is
built now. If yes, it waits.

---

## S1 — Extraction for the reserved corpora

`orchestrator/extract_document.py` reads only the already-JSON-shaped
`datasets/requirements_dataset.json`. The evaluation corpora are not in that shape: PURE's 79
documents (`datasets/pure-full/`) are PDF/DOC/HTML, and Dalpiaz, PROMISE NFR and Riaz each
have their own (`datasets/EVALUATION_DATASETS.md`). Nothing can be evaluated until
requirements come out of those as `RequirementSet`.

**This does not change pipeline behaviour, but it is frozen at the same point and for the
same reason:** if extraction changes, the inputs change, and every result computed before the
change becomes non-comparable.

**One methodological choice inside it that must be recorded here, not decided silently:** how
requirement boundaries are determined in unstructured documents. That is a decision with a
threat-to-validity attached, not an implementation detail.

**Fold in the corruption scan.** Known Limitation 5 records `1998 - themas.xml` having `<=`
flattened to `=` during PDF-to-XML extraction — `LO = T_LT` is a corrupted inequality, not
domain notation. The 18-file annotated subset was scanned and the damage was confined to that
one document; the 79-document corpus has never been parsed by anything in this project, so
nothing is known there. Scan for the same three signatures (`X = Y = Z` comparison chains,
`T_LT`-shaped underscore tokens, surviving Unicode math) and report per document. Do not
repair silently.

**Exclusions carried forward:** `1998 - themas.xml` and `2007-ertms.xml` are marked spent for
design purposes and must not appear in the evaluation subset.

---

## S2 — Capture the operator's system-type label alongside the model's

The Classifier's accuracy currently has **n=0**, because no human label has ever been
collected. It cannot be reconstructed after a run — this is a capture decision, and missing it
means the Classifier's contribution stays unmeasurable for the whole thesis.

Record both labels per requirement, with provenance for who set each. Per Known Limitation 2,
the audit trail is the only real mitigation this design has, and it degrades if provenance is
implicit.

**Constraint, from the discussion recorded under Known Limitation 9** ("Should the human
confirm or override the system type?"): this must not silently become a third blocking
interaction point in `HumanFns` unless that is separately justified. Capturing a label for
comparison is not the same as letting the human override the pipeline's label, and only the
first is being adopted here.

---

## S3 — Phase the pipeline

Adopts **option B** under Known Limitation 7, not the advisory post-pass (option A).

Today `run_document_stages` runs the Consistency Checker and Dependency Mapper once, on the
original text, and `run_requirement` computes `relevant_conflicts` / `relevant_dependencies`
once from that report and holds them constant through every refinement round and into
strategy selection and test generation. A rewrite that introduces a new conflict or dependency
is never seen. This was observed live: `PURE-THEMAS-R6-P` was corrected by the operator, the
Rewriter applied the fix, and the Quality Checker flagged `inconsistent` again in the next
round from the stale report — the loop terminated only because the operator set
`user_confirms_resolved`.

**Target shape:** pass A classifies, quality-checks and refines every requirement;
document-level analysis re-runs on the refined set; pass B does strategy selection and test
generation from the fresh reports.

**Four constraints, each one a place this goes wrong:**

1. **Keep both generations of reports — do not overwrite.** The diff between the initial and
   final consistency/dependency picture is the frequency number Known Limitation 7 has been
   asking for. Overwriting destroys a result.
2. **Update `orchestrator/test_harness.py::test_resume_positions` first.** That executable
   spec exists precisely because the resume spec drifted once before. Change the test, watch
   it fail, then make it pass.
3. **Model the second analysis as a distinct document-stage phase, not a re-run of the
   first.** The moment a "this analysis is stale, redo it" state is introduced,
   `resume_document`'s position logic and the terminal `DocumentOutcome` semantics both get
   complicated. A second phase keeps both simple, and is the reason option C (re-run after
   every rewrite) stays rejected.
4. **A cycle found by the second analysis is reported, not routed.**
   `IssueCategory.CIRCULAR_DEPENDENCY` routes to the Refiner, but refinement is finished by
   then; routing backwards re-opens a completed phase. Record it as a document-level finding
   and stop.

**Pass A's inputs are unchanged** — it still receives the original analysis. Only what
strategy selection and test generation see is different. Smallest blast radius that still
fixes the thing.

**Why option B and not option A.** Asked "does your consistency analysis describe the refined
requirements or the original ones?", the honest answer today is "the original." Running the
evaluation on the current shape bakes that into every result permanently. The post-pass
detects; phasing fixes. Option A remains a reasonable fallback if S3 proves harder than
expected — it is one extra call per document and still produces the frequency number.

---

## S4 — Human-supplied fragments for `NON_ATOMIC`

Resolves Known Limitation 8. **Build after S1 supplies a frequency count and after S3 has
landed.**

**S3 is what dissolves the blocker.** Known Limitation 8 records that a split is not a rewrite
because it changes requirement-set membership, so both document-level reports describe a
document that no longer exists. Once S3 re-runs that analysis after refinement, that objection
is gone — the second analysis runs on the post-split set. S3 and S4 are one fix; S4 is only
affordable because S3 is happening.

**Proposed shape** — field names to be confirmed against `design/schemas.py`, not assumed:

- The Refiner never splits. The operator may answer a `NON_ATOMIC` question with the split
  itself: an optional list of fragment texts on `RefinerAnswer`.
- The requirement terminates with an outcome meaning *split*, distinct from failure. A
  dedicated `RunOutcome` member is preferred over the earlier proposal of reusing
  `CAP_STOPPED` with a `cap_reason`, because a split is a success and reporting it as a cap
  corrupts the outcome counts.

  **This must answer an objection already on record before it is adopted.**
  `DocumentOutcome`'s own comments reject `HUMAN_OVERRIDE` as a `RunOutcome` member on the
  grounds that it would be *"a second, independent axis"* — and "was this requirement split?"
  is arguably that same shape, orthogonal to whether the requirement converged. That is very
  likely why the earlier proposal reached for `CAP_STOPPED` plus a reason. Either show why the
  objection does not apply here (the argument available: a split *terminates* the requirement,
  so it is on the same axis as `COMPLETED`/`CAP_STOPPED`, unlike an override which annotates a
  run that continues) or concede it and use the reason-string form. Do not adopt a new member
  without settling this — the precedent is explicit and a reviewer of this file will find it.

  Whichever is chosen, `TERMINAL_OUTCOMES` is a `frozenset` that the `_OutcomeRule` table keys
  off, so a new member must be added there deliberately, with its own required/forbidden
  field rule. It will not inherit sensible defaults.
- Between pass A and the second document analysis, the orchestrator materialises fragments
  with derived ids (`REQ-7.1`, `REQ-7.2`, …), each recording its origin requirement.
- Fragments re-enter pass A. **A fragment cannot itself be split** — one generation only,
  which is what makes this terminate.
- `_test_generator_extra_check`'s `known_requirement_ids` extends to derived ids; the origin
  field preserves traceability back to the requirement as written in the source document.
- Dependency links naming the original id are resolved by **re-derivation** in the second
  analysis, not by patching. This is the part that only works because of S3.

**Why this is the simple option.** No model invents anything, no new stage, no new LLM call,
no similarity heuristic, no accuracy evaluation of its own. The silent-drop failure mode
(Known Limitation 8, case 2 — the model returns one clean behaviour and discards the others,
passing the next check with no trace) disappears entirely, because the operator enumerates the
fragments rather than the model choosing which to keep.

It also closes the human-channel gap recorded under Known Limitation 8: "this flag is correct
and cannot be fixed at this level" was previously inexpressible, since
`user_confirms_resolved: True` means resolved (false here) and `False` re-asks until the cap.
Supplying the split *is* that answer.

**Costs, stated:**

- Operator effort rises — fragment texts are typed by hand.
- Any per-requirement rate now needs an explicit denominator, before or after splitting. State
  it wherever a rate is reported. (This is a reporting obligation and is repeated in
  `docs/EVALUATION_PROTOCOL.md`.)
- One more human decision to carry in the record, including who made it (see S2).

**Read the frequency count carefully before building.** Exactly one genuine `NON_ATOMIC` case
has appeared in 34 requirements (`LUITEL-R7`), and the detector over-flags — 2 of 5 flags in
the 2026-08-13 suite were conjunction-splits rather than genuine bundling. Raw counts will
overstate the need. Since S1 precedes everything anyway, the number arrives before it is
needed; there is no reason to build machinery on n=1.

---

## Declined, with the measurement behind each

These are not deferred on cost or scope. Each was measured, and the measurement is the reason.

- **Test-case de-duplication** (KL1) — spanning cases are confirmed reachable
  (`TC-13-PURE-ERTMS-R7-2`), but an actual duplicate has never been observed, and a
  false-positive merge silently deletes real coverage. If a number is wanted, emit suspected
  duplicates as an advisory count and act on nothing.
- **`refined_text: list[str]` as a general redesign** (KL8) — superseded by S4, which obtains
  the same capability from one optional answer field plus the phasing that is happening
  anyway.
- **Deterministic undefined-notation pre-pass, and a new `IssueCategory` for it** (KL5) — S9
  measured the Quality Checker as *blind* to `LO = T_LT`, not confused by it. There is no
  wrong flag to correct, and the anchor example turned out to be a corrupted inequality
  rather than domain notation. Nothing to build.
- **Hard-real-time / soft `PERFORMANCE` split, and an `EMBEDDED` `SystemType`** (KL3) —
  `PERFORMANCE` was selected zero times across 34 requirements, including on `LUITEL-R1`
  where S12's ground truth expected it. A distinction qualifying a technique that is never
  chosen is unreachable.
- **Collapsing `SystemType` to `{AI_SYSTEM, OTHER}`** (KL9) — the 2026-08-13 suite refuted the
  empirical half: 31 `other`, 2 `mobile`, 1 `ai_system`. The Classifier discriminates. The
  structural point (three members, one technique pool) stands and is *reported* as a
  limitation rather than fixed.
- **Web technique pool** (KL9) — no web requirements exist in the design corpus, so building
  it repeats the mistake KL3 stays open for. **Conditional, not closed:** revisit after S1,
  since PURE holds 79 documents and a web SRS is likely among them.
- **Pairwise / combinatorial testing** — deferred, unchanged.
- **The no-op-rewrite validator, fixes (b) and (c)** (KL10) — the 2026-08-13 suite traced
  every one of 38 no-ops to an answer supplying no information. No defect for the rule to
  catch. Fix (a), *counting* no-ops, is kept and belongs to the protocol document.
- **Loosening `TestPlan`'s strict "every case covers this requirement" rule** (KL6) — never
  fired in any run.

**Also conditional on S1:** mobile CT-MAT prompt content, worth doing only if mobile
requirements survive into the evaluation corpus, and at prompt level rather than in the enum
unless mobile coverage must be a *reported* metric. Standards-cited measurable-property
rewrites (the verified `STANDARDS_REFERENCE` table above), worth doing only if a model rather
than the operator answers Refiner questions — its purpose is to convert the fabrication mode
measured in the n=3 answerer pilot into a citation, and a human answerer does not exhibit it.
Under the protocol's chosen answer policy, it is not needed.
