# First real run checklist

Three questions the harness's fake fixtures can't answer, that a real run against
THEMAS (8 requirements, ~40 calls) can, cheaply, before running anything larger. All
three are already recorded in the records `orchestrator/pipeline.py` produces — this
just says where to look, so the first run gets analyzed once, on purpose, instead of
being reconstructed from memory afterward.

No numbers are estimated here. There is no measurement yet.

## 1. How often does a model name the wrong requirement — per stage, per model

`design/ORCHESTRATOR_CONTRACT.md` item 15's whole reason for existing: a mismatch is
`FailureKind.VALIDATION`, not a separate kind, so it's mixed in with other validation
failures in the data. Isolating it:

- Requirement level: for each `StageError` in a `RequirementRunRecord.errors` where
  `kind == VALIDATION`, check whether `message` matches the shape
  `f"{model_cls}.requirement_id is {x!r}, expected {y!r} -- the model answered about a
  different requirement"` (the exact string `call_stage` produces). Tally by `stage`.
- Document level: same check on `DocumentRunRecord.errors` (`DocumentStageError`),
  matching `call_document_stage`'s `doc_id` message shape instead. Tally by `stage`.
- Cross-reference `stage` against `RunMetadata.stages[stage].model` to break the tally
  down by model, not just by stage — the whole point of counting this is deciding
  whether a *specific model* needs option A (silent overwrite) applied to it, not
  the pipeline as a whole.

## 2. How often does output fail schema validation, and which rule catches it

Broader than #1 — item 15's mismatches are one specific cause of `kind=VALIDATION`
among all the ways a model's output can fail `model_cls.model_validate(...)`.

- Rate: count `kind == VALIDATION` against total attempts per stage (attempts, not just
  failures — a stage that succeeded on retry leaves no failed `StageError`, per
  `retry_count`'s own documented limit in `design/schemas.py`; only failures that
  *exhausted* retries are visible this way, so this rate is a floor, not the true rate).
- Which rule: `StageError.message`/`DocumentStageError.message` is free text (the
  stringified Pydantic `ValidationError`) — per contract item 7, this project
  deliberately does not capture the failing field path as structured data yet.
  Reading which validator fired means reading the message text by hand for this run.
  If per-rule counts turn out to matter beyond this one run, contract item 7 already
  names the fix: capture the Pydantic error's field path when it's built, not after.

## 3. Tokens per stage, so cost-per-document becomes real

- Per requirement: `RequirementRunRecord.usage` (list of `TokenUsage`, one entry per
  call that *returned* — see contract item 13 for why a transport failure never adds
  one) and the computed `.total_tokens`.
- Per document: `DocumentRunRecord.usage` (list of `DocumentTokenUsage`) and the
  computed `.document_stage_tokens` — named that and not `total_tokens` on purpose
  (see the fixes-and-changes log §"naming trap"): it only covers the two document-level
  stages. Whole-document cost is
  `doc.document_stage_tokens + sum(r.total_tokens for r in doc.requirement_records)`,
  computed by whoever reads the records — there is no field that does this for you.
- Cost itself needs a price table applied on top, priced at whatever the provider
  charges *when this analysis is done* — tokens are stored, not cost, specifically so
  the same run can be re-priced later without being wrong (see `TokenUsage`'s
  docstring). Do not write a cost number into this document; write it wherever the
  actual run's results get analyzed.
- A validation failure still spent tokens (contract item 14) — when computing "cost per
  useful output," decide whether to include tokens spent on rejected `kind=VALIDATION`
  output. Both numbers (with and without) are worth having: the second is what the
  pipeline actually cost to run, the first is what it cost per requirement actually
  produced.

## Where these come from, if this checklist itself needs updating later

`design/ORCHESTRATOR_CONTRACT.md` items 13, 14, 15. Don't restate the reasoning here —
this file is the "where to look," not a second copy of "why."
