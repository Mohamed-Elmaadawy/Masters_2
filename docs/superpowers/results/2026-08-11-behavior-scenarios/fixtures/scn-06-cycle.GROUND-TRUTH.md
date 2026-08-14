# scn-06-cycle — ground truth

Spec: `docs/superpowers/plans/2026-08-11-behavior-scenarios.md`, S6.

## Source

All three requirements **planted** — hand-authored for this suite. A real corpus
cycle would have to be found rather than constructed, and none is known to exist in
the spent material (spec doc).

| id | text |
|---|---|
| SCN6-A-P | "The scheduler shall not dispatch a job until the resource monitor has reported the current load." |
| SCN6-B-P | "The resource monitor shall compute the current load only after the audit logger has recorded the previous dispatch decision." |
| SCN6-C-P | "The audit logger shall record a dispatch decision only once the scheduler has dispatched the corresponding job." |

(`source_doc_id` left `None` on all three — they have no source document to name.)

## Ground truth

A→B→C→A, one cycle of length 3 (scheduler depends on resource monitor, resource
monitor depends on audit logger, audit logger depends on scheduler).

## Hard (deterministic, machine-checkable)

- No `DependencyLink` is self-referential (schema-enforced).
- If all three links are found, `DependencyReport.find_cycles()` returns exactly one
  cycle covering all three ids.

## Soft (judged on inspection)

- All three links found, each with the correct direction.
- A `CIRCULAR_DEPENDENCY` issue is raised on at least one of the three requirements.

**Open question this scenario answers, not assumes:** whether the orchestrator
actually routes a detected cycle to the Refiner. The contract says cycles route there
rather than being auto-resolved — verify against the run record, don't trust the
contract text alone.
