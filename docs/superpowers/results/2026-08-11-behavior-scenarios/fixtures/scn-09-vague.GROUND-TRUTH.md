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
