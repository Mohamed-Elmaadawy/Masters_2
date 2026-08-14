# scn-08-clean — ground truth

Spec: `docs/superpowers/plans/2026-08-11-behavior-scenarios.md`, S8.

## Source

Verbatim from `datasets/requirements_dataset.json`, doc `themas-fischbach2022`.
Nothing planted.

| id | source id | text |
|---|---|---|
| THEMAS-REQ-G | THEMAS-REQ-G | "Each thermostat shall have a unique identifier by which that thermostat is identified in the THEMAS system." |

(`source_doc_id` left `None` — see scn-01-dep-pair.GROUND-TRUTH.md's note.)

## Ground truth

Atomic, has an actor, verifiable, no vague term (by construction) — the per-requirement
mirror of S2: a control that should pass clean.

## Hard (deterministic, machine-checkable)

- `RunOutcome.COMPLETED`.
- If `passed` is true: `rounds` has length 1, and `rounds[0].turn`/`rounds[0].rewrite`
  are both `None`.
- `test_strategy` and `test_plan` both populated.
- `final_text` equals the original text.

## Soft (judged on inspection)

- `passed == True`.
- `issues == []`.

**Known risk:** `VAGUE_PRONOUN` is documented as expected-noisy (Known Limitation 4,
`CLAUDE.md`), and "that thermostat" is exactly the shape that trips it. A flag here is
a *calibration* data point for that limitation, not automatically a bug — record it
as such, don't treat it as a scenario failure.
