# scn-10-atomicity — ground truth

Spec: `docs/superpowers/plans/2026-08-11-behavior-scenarios.md`, S10.

## Source

Both requirements verbatim from `datasets/requirements_dataset.json`. Nothing
planted.

| id | source id | source doc | text |
|---|---|---|---|
| LUITEL-R7 | LUITEL-R7 | illustrative-luitel2024 | "The system shall generate reports on inventory levels, product movement, and sales history." |
| AUTOGEN-US2 | AUTOGEN-US2 | autogen-wu2024 | "As a user, I want a product that is reliable and efficient so that I can depend on it." |

(`source_doc_id` left `None` on both — see scn-01-dep-pair.GROUND-TRUTH.md's note.)

## Ground truth

- LUITEL-R7 → `NON_ATOMIC` (three testable behaviors: inventory-level reports,
  product-movement reports, sales-history reports).
- AUTOGEN-US2 → `AMBIGUOUS_TERM` (and plausibly `NON_VERIFIABLE`) — "reliable and
  efficient" are two unmeasurable adjectives with no threshold.

## Hard (deterministic, machine-checkable)

- `RefinedRequirement.refined_text` is a single requirement string — the schema does
  not split it, so if the rewriter returns three requirements joined by newlines, the
  record accepts it as valid. **This must be read by hand** — it is a gap the schema
  cannot close, and this scenario is the only thing in the suite looking at it.

## Soft (judged on inspection)

- The categories above are the ones actually flagged.
- The rewrite for LUITEL-R7 is *one* behavior, not three (read the `refined_text` by
  hand, per the hard-expectation note above).
