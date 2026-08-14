# scn-04-conflict-numeric — ground truth

Spec: `docs/superpowers/plans/2026-08-11-behavior-scenarios.md`, S4.

## Source

| id | status | source | text |
|---|---|---|---|
| PURE-THEMAS-R6 | verbatim | `datasets/requirements_dataset.json`, doc `pure-themas-1998-full`, id `PURE-THEMAS-R6` | "The THEMAS system shall ensure the temperature reported by a given thermostat shall not exceed a maximum deviation value of 3 degrees Fahrenheit." |
| PURE-THEMAS-R6-P | **planted** | hand-authored for this suite | "The THEMAS system shall permit a temperature deviation of up to 5 degrees Fahrenheit for any thermostat before reporting a deviation error." |

Planted because `datasets/requirements_dataset.json` labels no conflict ground truth
(see spec doc, "Data provenance"); the `-P` suffix marks it as authored text, per the
suite's fixture conventions. (`source_doc_id` left `None` on both records — see
scn-01-dep-pair.GROUND-TRUTH.md's note.)

## Ground truth

Direct numeric contradiction: 3°F vs 5°F, same subject (per-thermostat maximum
temperature deviation before an error/report). Deliberately trivial — its value is as
a floor for consistency detection, and as the calibration point against which S3's
difficulty is read.

## Hard (deterministic, machine-checkable)

Same as S3:
- `DocumentOutcome.COMPLETED`.
- If a `ConsistencyConflict` is found, it names exactly `PURE-THEMAS-R6` and
  `PURE-THEMAS-R6-P`, and they are distinct ids.
- `conflicts_for()` output reaches the Quality Checker for both requirements.

## Soft (judged on inspection)

- Conflict found.
- Both ids present in the conflict.
- Explanation names both numbers (3°F and 5°F).

**If this is missed, nothing in S3/S5 is interpretable** — this is the floor case.
