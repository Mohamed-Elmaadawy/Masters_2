# scn-01-dep-pair — ground truth

Spec: `docs/superpowers/plans/2026-08-11-behavior-scenarios.md`, S1.

## Source

Both requirements verbatim from `datasets/requirements_dataset.json`, doc
`pure-ertms-2007`. Nothing planted.

| id | source id | text |
|---|---|---|
| PURE-ERTMS-R7 | PURE-ERTMS-R7 | "At Start Up, the on board equipment shall perform an automatic self-test." |
| PURE-ERTMS-R8 | PURE-ERTMS-R8 | "The DMI shall indicate the result of the self-test." |

`source_doc_id` is left `None` on both `Requirement` records rather than set to
`pure-ertms-2007`: `RequirementSet._requirements_belong_to_this_document` (design/
schemas.py) rejects a requirement naming a source document other than the set's own
`doc_id` (here, `scn-01-dep-pair`). Provenance is recorded here instead — that is
exactly the "provenance wasn't recorded on the record itself" case the validator's
docstring says is legitimate. Every fixture in this suite follows this same rule; not
restated in the notes below.

## Ground truth

R8 depends on R7 — R8 cannot be tested without R7 having produced a result. One link,
direction `from=R8, to=R7`. No conflict.

## Hard (deterministic, machine-checkable)

- `DocumentOutcome.COMPLETED`.
- Both `ConsistencyReport` and `DependencyReport` present.
- `doc_id` on both reports agrees with the set (`scn-01-dep-pair`).
- Both requirements reach `RunOutcome.COMPLETED` or a cap outcome.
- `dependencies_for("PURE-ERTMS-R8")` is threaded into the Strategy Selector and Test
  Generator for R8 (ORCHESTRATOR_CONTRACT.md item 16).

## Soft (judged on inspection)

- Exactly one dependency link, direction `from=PURE-ERTMS-R8, to=PURE-ERTMS-R7`, with
  an explanation naming the self-test.
- `conflicts == []`.

**Known risk:** the mapper may invert the direction; the schema only rejects
self-loops, not wrong direction. Record direction accuracy here and tally it across
all Group A scenarios (S1, S3–S7) — if wrong here, expect it wrong elsewhere.

**Reused by S13** (`docs/.../configs/scn-13-degraded.yaml`) unchanged, to force a
`consistency_checker` failure through a real adapter while checking that dependency
mapping still completes normally on the same input.
