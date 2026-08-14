# scn-07-dilution — ground truth

Spec: `docs/superpowers/plans/2026-08-11-behavior-scenarios.md`, S7.

## Source

The full `pure-ertms-2007` set (R1–R8), verbatim, from
`datasets/requirements_dataset.json`. Nothing planted.

| id | text |
|---|---|
| PURE-ERTMS-R1 | "ETCS shall provide the driver with information to allow him to drive the train safely." |
| PURE-ERTMS-R2 | "ETCS shall be able to supervise train and shunting movements." |
| PURE-ERTMS-R3 | "The current application level shall be indicated on the DMI." |
| PURE-ERTMS-R4 | "The driver shall acknowledge the level transitions, if requested from trackside." |
| PURE-ERTMS-R5 | "If the driver does not acknowledge after the transition the brake shall be applied." |
| PURE-ERTMS-R6 | "If an ETCS equipped train passes a level transition to a line fitted with more than one level, the onboard shall switch to the highest level, according to the priority given by trackside, for which it is equipped." |
| PURE-ERTMS-R7 | "At Start Up, the on board equipment shall perform an automatic self-test." |
| PURE-ERTMS-R8 | "The DMI shall indicate the result of the self-test." |

(`source_doc_id` left `None` on all eight — see scn-01-dep-pair.GROUND-TRUTH.md's
note.)

## Ground truth

At least two genuine dependencies: R8→R7 (self-test result depends on the self-test
having run — same pair as S1) and R5→R4 (brake-on-no-acknowledgement depends on the
acknowledgement request having been made). Other links are *plausible* (R3/R6 both
concern level transitions) but not confirmed — **this fixture's ground truth is a
lower bound, not an exact set.** State that when reporting; count "links found that
are not in the known pair set" separately as *unverified*, never as false positives.

## Hard (deterministic, machine-checkable)

- `DocumentOutcome.COMPLETED`.
- No duplicate ordered pairs among reported links (schema-enforced).
- Every id in every link exists in `{PURE-ERTMS-R1..R8}`.

## Soft (judged on inspection)

- Both known links present: R8→R7, R5→R4.
- Total link count — an explosion (say, 15+ links on 8 requirements) is itself a
  finding about mapper precision, not proof of a well-found document.

**Cost note:** the most expensive scenario (8 requirements × 6 per-requirement stages
plus refinement rounds). Run last, per the spec doc's suggested order, after the
cheap scenarios have shown the pipeline is behaving.

**Cross-reference:** shares the R8→R7 pair with S1 — direction accuracy on that pair
should be compared between the two runs (spec doc, "Known risk" tally across Group A).
Also expect duplicate test cases across R4/R5 and R7/R8 (Known Limitation 1,
`CLAUDE.md`) — note them, don't treat them as bugs.
