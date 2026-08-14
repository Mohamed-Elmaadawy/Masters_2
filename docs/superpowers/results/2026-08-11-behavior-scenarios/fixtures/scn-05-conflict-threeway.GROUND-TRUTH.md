# scn-05-conflict-threeway — ground truth

Spec: `docs/superpowers/plans/2026-08-11-behavior-scenarios.md`, S5.

## Source

| id | status | source | text |
|---|---|---|---|
| PURE-THEMAS-R4 | verbatim | `datasets/requirements_dataset.json`, doc `pure-themas-1998-full`, id `PURE-THEMAS-R4` | "There shall be a maximum number of heating or cooling units that can be on at any given time." |
| PURE-THEMAS-R4-P1 | **planted** | hand-authored for this suite | "The maximum number of heating or cooling units that may be on simultaneously shall be three." |
| PURE-THEMAS-R4-P2 | **planted** | hand-authored for this suite | "During a system-wide cold start, the THEMAS system shall turn on the heating unit in every one of the four zones at the same time." |

Planted for the same reason as S4 — no available corpus labels multi-way conflict
ground truth. (`source_doc_id` left `None` on all three records.)

## Ground truth

Each requirement is satisfiable alone, and each *pair* is satisfiable; only all three
together are contradictory (cap of 3 simultaneous units vs a cold start that turns on
4). One conflict naming all three ids. This is the miniature of the planned
scale experiment (`datasets/EVALUATION_DATASETS.md`, "Planned experiment") — a
conflict no pairwise comparison can find, and probes whether
`ConsistencyConflict.requirement_ids` (allows 3+ ids by design, `design/
DESIGN_NOTES.md`) is ever actually produced by a real model.

## Hard (deterministic, machine-checkable)

- If a conflict is reported, its `requirement_ids` are distinct and are a subset of
  `{PURE-THEMAS-R4, PURE-THEMAS-R4-P1, PURE-THEMAS-R4-P2}`.

## Soft (judged on inspection)

- One conflict naming **all three** ids — a pass.
- Two pairwise conflicts instead of one three-way conflict is a **partial** result,
  not a pass, and a specific, reportable finding: the checker reasons pairwise even
  when handed the whole document.
- Zero conflicts is a miss.
