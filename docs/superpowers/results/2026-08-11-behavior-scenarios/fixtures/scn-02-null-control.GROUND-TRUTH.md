# scn-02-null-control — ground truth

Spec: `docs/superpowers/plans/2026-08-11-behavior-scenarios.md`, S2.

## Source

Both requirements verbatim from `datasets/requirements_dataset.json`, deliberately
from *different* source documents and domains. Nothing planted.

| id | source id | source doc | text |
|---|---|---|---|
| METAGPT-US1 | METAGPT-US1 | metagpt-hong2024 | "As a user, I want to select any color on the screen, so that I can get its RGB values." |
| HEY-REQ2 | HEY-REQ2 | promise-fr-hey2020 | "The audit report shall include the total number of recycled parts used in the estimate." |

(`source_doc_id` left `None` on the record — see scn-01-dep-pair.GROUND-TRUTH.md's
note; applies to every fixture in this suite.)

## Ground truth

No dependency, no conflict. Without this scenario, S1/S3/S4 prove nothing — a mapper
that links everything to everything passes all of them.

## Hard (deterministic, machine-checkable)

- `DocumentOutcome.COMPLETED`.
- Both `ConsistencyReport` and `DependencyReport` present.
- `relevant_conflicts` and `relevant_dependencies` passed to each requirement's stages
  are `[]`, **not** `None` (ORCHESTRATOR_CONTRACT.md item 16 — `None` would mean the
  stage failed, not that it found nothing).

## Soft (judged on inspection)

- `conflicts == []`.
- `dependencies == []`.

**Note:** an easy negative (unrelated documents). A harder one — two requirements
from the *same* document that are merely topically adjacent — isn't covered here; S7
is a step in that direction (spec doc).
