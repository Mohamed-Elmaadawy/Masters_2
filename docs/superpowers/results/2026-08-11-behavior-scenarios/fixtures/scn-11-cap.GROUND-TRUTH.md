# scn-11-cap — ground truth

Spec: `docs/superpowers/plans/2026-08-11-behavior-scenarios.md`, S11.

Reused, unchanged, by **two** configs: `configs/scn-11a-cap-generate.yaml` and
`configs/scn-11b-cap-stop.yaml` (`max_revisions: 3` in both, the human decision at the
cap scripted differently in each run).

## Source

Verbatim from `datasets/requirements_dataset.json`, doc `autogen-wu2024`. Nothing
planted.

| id | source id | text |
|---|---|---|
| AUTOGEN-US3 | AUTOGEN-US3 | "As a user, I want a product that meets my needs so that I can get value for my money." |

(`source_doc_id` left `None` — see scn-01-dep-pair.GROUND-TRUTH.md's note.)

## Ground truth

An irreducibly vague requirement no rewrite can fix ("meets my needs", "value for my
money" have no testable content). The checker should keep failing it; the revision
cap should fire at round 3. Probes ORCHESTRATOR_CONTRACT.md item 3 end-to-end with a
real model.

## Hard (deterministic, machine-checkable)

- **Run A** (`scn-11a-cap-generate`, human chooses to generate at the cap):
  `RunOutcome.CAP_GENERATED`; `cap_reason` non-empty; `test_strategy`/`test_plan`
  both populated.
- **Run B** (`scn-11b-cap-stop`, human chooses to stop at the cap):
  `RunOutcome.CAP_STOPPED`; `test_strategy`/`test_plan` both `None`; **no**
  `StageError` for `strategy_selector`/`test_generator` (schema-enforced,
  ORCHESTRATOR_CONTRACT.md item 7).
- Both runs: `len(rounds) == 3`.

## Soft (judged on inspection)

- The issues genuinely persist across all three rounds rather than the checker giving
  up and passing the requirement. **If the model passes this requirement, the cap
  never fires and the scenario produces nothing** — in that case, re-run against
  `AUTOGEN-US4` (same shape: "As a user, I want a product that is constantly updated
  and improved so that I can enjoy the best features.") rather than forcing this one
  to fail.
