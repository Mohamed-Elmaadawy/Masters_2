# scn-09-vague — ground truth

Spec: `docs/superpowers/plans/2026-08-11-behavior-scenarios.md`, S9.

## Source

Both requirements verbatim from `datasets/requirements_dataset.json`, doc
`themas-fischbach2022`. Nothing planted.

| id | source id | text |
|---|---|---|
| THEMAS-REQ-D | THEMAS-REQ-D | "Temperatures that do not exceed these limits shall be output for subsequent processing." |
| THEMAS-REQ-E | THEMAS-REQ-E | "If this condition is true, then this module shall output a request to turn on the heating unit in case LO = T_LT." |

(`source_doc_id` left `None` on both — see scn-01-dep-pair.GROUND-TRUTH.md's note.)

**Note added 2026-08-14 — `LO = T_LT` is a corrupted inequality, not domain notation.** The
fixture text above is verbatim from `datasets/requirements_dataset.json` and is kept
unchanged, because three completed runs plus the live-answer session used it and editing it
would break their comparability. But the underlying source
(`datasets/requirements-xml/XMLZIPFile/1998 - themas.xml`, SRS-010) actually reads:

> "…output a request to turn on the heating unit if `LO ≤ T ≤ LT` or the cooling unit if
> `UT ≤ T ≤ UO`."

The `≤` signs were flattened to `=` and a space was lost during PDF-to-XML extraction. The same
damage appears elsewhere in that document ("If `T = LO` or `UO = T`", "Condition 1:
`LT = T = UT`"). Both `LO` and `LT` are defined one paragraph above the requirement that uses
them (`LT = TSET − TD`, `LO = TSET − OD`).

Consequences for anyone reading this fixture later:

- Do **not** treat `LO = T_LT` as an example of undefined domain notation. It is not a
  meaningful expression, so no detector could resolve it, and Known Limitation 5 was reframed
  on that basis (see `design/DESIGN_NOTES.md`).
- "this condition" resolves to Condition 2 of SRS-010 (`LO ≤ T < LT`, or `UT < T ≤ UO`), and
  "this module" to the Determine H/C Mode process (SRS-010). Both are defined in the same
  section the sentence was excerpted from — the vagueness is an artefact of excerpting, not a
  defect in the original document.
- The corruption was measured across the whole committed XML subset on 2026-08-14: THEMAS is
  the only affected file of 18. ERTMS-derived fixtures are unaffected.

## Ground truth

- D → `VAGUE_PRONOUN` ("these limits") and/or `INCOMPLETE` (no actor).
- E → `VAGUE_PRONOUN` ("this condition", "this module").
- `LO = T_LT` in E is undefined domain notation — Known Limitation 5 (`CLAUDE.md`)
  says this is **expected not to be caught**.

## Hard (deterministic, machine-checkable)

- `passed == False` on both first-round `QualityReport`s. (A `QualityReport` with
  `passed=False` and `issues=[]` is schema-rejected, so a non-empty `issues` list on
  both comes for free.)
- Each `Issue.id` stable across rounds per ORCHESTRATOR_CONTRACT.md item 4.
- Suppressions accumulate per ORCHESTRATOR_CONTRACT.md item 5.

## Soft (judged on inspection)

- The categories listed above are the ones actually flagged.
- **Whether `LO = T_LT` is flagged is the interesting number.** If it *is* flagged,
  Known Limitation 5 is less severe than documented, and `design/DESIGN_NOTES.md`
  should say so — with this run cited, not silently.

## Run dependency

Needs a scripted human answer policy, since the refine loop asks questions. Reuse
`docs/superpowers/results/2026-08-10-first-real-run/answer_policy_driver.py` — one
reasoned answer per `IssueCategory`, applied consistently, already documented as
AI-generated rather than live-human (a stated threat to validity,
ORCHESTRATOR_CONTRACT.md item 3). Do not write a second, different policy for this
suite — two policies make the two runs' results incomparable.
