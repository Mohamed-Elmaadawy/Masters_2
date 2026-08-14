# scn-03-conflict-native — ground truth

Spec: `docs/superpowers/plans/2026-08-11-behavior-scenarios.md`, S3.

## Source

Both requirements verbatim from `datasets/requirements_dataset.json`, doc
`actapp-arora2024`. Nothing planted.

| id | source id | text |
|---|---|---|
| ACTAPP-R1 | ACTAPP-R1 | "The patients should receive a notification to stand up and move around if they have been sitting for long." |
| ACTAPP-R2 | ACTAPP-R2 | "The patients should not receive notifications when busy." |

(`source_doc_id` left `None` — see scn-01-dep-pair.GROUND-TRUTH.md's note.)

## Ground truth

Conflicting under the case "the patient has been sitting for a long time *because*
they are busy" — R1 requires a notification, R2 forbids it. Neither is wrong alone;
the pair is unsatisfiable without a stated precedence rule. This is a **latent**
conflict, not a flat contradiction — genuinely harder than S4.

## Hard (deterministic, machine-checkable)

- `DocumentOutcome.COMPLETED`.
- If a `ConsistencyConflict` is found, it names exactly `ACTAPP-R1` and `ACTAPP-R2`,
  and they are distinct ids.
- `conflicts_for()` output reaches the Quality Checker for **both** requirements.

## Soft (judged on inspection)

- The conflict is found.
- The explanation names the overlap case ("sitting because busy") rather than
  restating the two requirements.
- The Quality Checker raises `INCONSISTENT` on at least one of `ACTAPP-R1`/`ACTAPP-R2`.

**Known risk — the honest one:** a miss here is a genuinely interesting result and
must not be patched by rewording the fixture until it passes. S4 is the control that
says whether a miss here means "misses everything" or "misses only the subtle case."
