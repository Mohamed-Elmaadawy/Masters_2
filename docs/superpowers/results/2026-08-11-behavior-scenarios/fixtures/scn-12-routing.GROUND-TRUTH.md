# scn-12-routing — ground truth

Spec: `docs/superpowers/plans/2026-08-11-behavior-scenarios.md`, S12.

## Source

All three requirements verbatim from `datasets/requirements_dataset.json`. Nothing
planted.

| id | source id | source doc | text |
|---|---|---|---|
| LUITEL-R1 | LUITEL-R1 | illustrative-luitel2024 | "The system shall reach a steady state within 5s after reconfiguration to maximize availability." |
| THEMAS-REQ-B | THEMAS-REQ-B | themas-fischbach2022 | "If the current temperature value is strictly less than the lower value of the valid temperature range or if the received temperature value is strictly greater than the upper value of the valid temperature range, then the THEMAS system shall identify the current temperature value as an invalid temperature and shall output an invalid temperature status." |
| ACTAPP-R2-AC1 | ACTAPP-R2-AC1 | actapp-arora2024 | "Accurately identifies when the user is driving." |

(`source_doc_id` left `None` on all three — see scn-01-dep-pair.GROUND-TRUTH.md's
note.)

## Ground truth — expected routes

Chosen for three different expected routes, probing Layer 1 (schema-enforced: which
techniques a `SystemType` may use) and Layer 2 (prompt guidance only,
ORCHESTRATOR_CONTRACT.md item 11, auditable solely through `TestStrategy.rationale`):

- LUITEL-R1 — numeric threshold (5s) → `boundary_value_analysis`, `performance`.
- THEMAS-REQ-B — strictly-less-than / strictly-greater-than range →
  `equivalence_partitioning` + `boundary_value_analysis`.
- ACTAPP-R2-AC1 — ML classifier with no single correct output →
  `SystemType.AI_SYSTEM`, techniques among `metamorphic` / `statistical_threshold` /
  `adversarial`.

## Hard (deterministic, machine-checkable)

- Every selected technique is Layer-1-legal for the classified `SystemType`
  (schema-enforced — a violation would be a schema bug, not a model one).
- Every `TestCase` covers the plan's requirement (schema-enforced, Known Limitation 6,
  `CLAUDE.md`) — note it, don't loosen it, if real generator output starts getting
  rejected here; that's exactly the condition `design/DESIGN_NOTES.md` says would
  justify loosening the rule.

## Soft (judged on inspection)

- The routes above are the ones actually produced.
- `rationale` states reasoning traceable to ORCHESTRATOR_CONTRACT.md item 11's Layer-2
  rules, rather than restating the requirement text.

**Known risk:** `ACTAPP-R2-AC1` may classify as `MOBILE` rather than `AI_SYSTEM`. Both
are arguably right — the app is mobile, the behavior is ML. Record which was chosen;
do not treat a `MOBILE` classification as a failure — treat it as evidence that
`SystemType` is under-specified for hybrid cases, which belongs in threats to
validity, not in the pass/fail tally.
