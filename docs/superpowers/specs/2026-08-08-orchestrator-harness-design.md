# Orchestrator + Simulation Harness — Design

Date: 2026-08-08
Status: approved, pre-implementation

## Purpose

The schema design phase is finished (`design/schemas.py`, `design/test_schemas.py`,
265 checks). Per `CLAUDE.md`, the next phase is the orchestrator, the seven stages, and
their prompts. Before writing real LLM-calling code, build a **simulation harness**: a
fake end-to-end run through all seven stages, driven by scripted fixtures instead of
real API calls.

The harness is not a throwaway script. It **is** the orchestrator's control-flow code,
built with every LLM call and every human-interaction point passed in as a parameter.
The harness test wires in fake implementations of those parameters; the real run later
wires in real ones. No logic gets built twice.

This document also records two amendments to `design/schemas.py`, discovered by
designing the harness before either mattered enough to notice by accident.

## Layout

```
design/
  __init__.py            # new — design becomes an importable package
  schemas.py              # + FailureKind, TokenUsage, DocumentTokenUsage (see below)
  test_schemas.py         # loses resume_at / test_resume_positions (moves to harness)
orchestrator/
  __init__.py
  pipeline.py             # StageFns, HumanFns, Throttle, run_document(), resume_at(), retry
  test_harness.py         # fake StageFns/HumanFns fixtures + full-contract scenarios
  stages.py               # real LLM-calling stage fns — not built this phase, stubbed only
```

Run from the repo root; imports as `from design.schemas import ...`. No `sys.path`
hacks — `test_schemas.py` currently has one; not repeated in a second file.

## Schema amendments

Both executed as the first implementation step, followed by the mandatory
`python design/test_schemas.py` and `python design/generate_diagrams.py` per
`CLAUDE.md`. `ORCHESTRATOR_CONTRACT.md` gets a line added for each, citing this doc.

### 1. `FailureKind` — `StageError` and `DocumentStageError` currently can't distinguish
why a call failed. `message` is free text; a rate limit and a schema-rejected LLM output
look identical to any code that reads it.

```python
class FailureKind(str, Enum):
    TRANSPORT = "transport"   # network/rate-limit — retry usually helps
    VALIDATION = "validation" # model output failed schema — retrying may help, means something different
    OTHER = "other"           # caught-but-unanticipated (e.g. HTTP 500) — NOT for orchestrator bugs
```

Add `kind: FailureKind` to both `StageError` and `DocumentStageError`.

This makes "how often did the model produce schema-invalid output" countable. It does
**not** make "which validation rule caught it" countable — that stays free text in
`message`. If per-rule counts turn out to matter later, capture the Pydantic error's
field path then; don't add enum members speculatively now.

### 2. Token usage — a usage log, mirroring the `StageError`/`DocumentStageError` split.

```python
class TokenUsage(BaseModel):
    stage: PipelineStage
    prompt_tokens: int = Field(..., ge=0)
    completion_tokens: int = Field(..., ge=0)

class DocumentTokenUsage(BaseModel):
    stage: DocumentStage
    prompt_tokens: int = Field(..., ge=0)
    completion_tokens: int = Field(..., ge=0)
```

- `RequirementRunRecord.usage: list[TokenUsage] = []`, plus:
  ```python
  @computed_field
  @property
  def total_tokens(self) -> int:
      return sum(u.prompt_tokens + u.completion_tokens for u in self.usage)
  ```
  (`@computed_field` makes a derived property serialize like a real field, without
  storing a value that could drift from `usage`.)
- `DocumentRunRecord.usage: list[DocumentTokenUsage] = []`, plus a computed field named
  **`document_stage_tokens`**, not `total_tokens` — see "Naming trap" below.

Stores tokens, not cost: prices change; a stored cost freezes today's price into a
record read months later. Tokens × a price table kept separately gives cost at whatever
price is current. Does not store which model was used — `RunMetadata.stages[stage].model`
already says that for the run; repeating it on `TokenUsage` would be a denormalized copy
requiring agreement, the exact pattern behind most bugs this project has hit. Does not
separate cached-prompt tokens — irrelevant until it's known whether caching is in use.

**Naming trap avoided:** `DocumentRunRecord.total_tokens` would invite reading it as
whole-document cost, but it can only sum the two document-level stages
(`consistency_checker`, `dependency_mapper`) — never the requirement records, because
under decision D2b those arrive empty in `document.json` and are only populated after
assembly from the `requirements/*.json` files. A field named `total_tokens` would return
near-zero on-disk and a large number post-assembly, same name, two answers depending on
when it's read. Named `document_stage_tokens` instead. Whole-document cost is
`doc.document_stage_tokens + sum(r.total_tokens for r in doc.requirement_records)`,
computed by the caller, not implied by a field name.

**When usage is recorded:** whenever a call *returns* — success or a validation
failure, both of which mean inference happened and tokens were spent. Never on
`StageCallFailed` (transport failure): the request was rejected before inference, no
tokens were spent, and the stage fn *raises* in that case so there is no result object to
carry counts. A validation failure recording tokens is a feature, not a gap — it makes
"how much did this model cost us in output we had to throw away" a real number.

## Stage & human interaction interface

Two frozen dataclasses, not dicts — a typo in a dict key silently returns nothing and
the stage gets skipped; a typo'd field name is an immediate `TypeError`. Same reasoning
`_OutcomeRule` already uses as a `NamedTuple` in `schemas.py`.

```python
class StageCallResult(NamedTuple):
    raw: dict                 # simulated/real parsed LLM JSON, not yet validated
    prompt_tokens: int
    completion_tokens: int

class StageCallFailed(Exception):
    """Transport-level failure: network error, rate limit, timeout."""

@dataclass(frozen=True)
class StageFns:
    check_consistency: Callable[..., StageCallResult]
    map_dependencies:  Callable[..., StageCallResult]
    classify:          Callable[..., StageCallResult]
    check_quality:     Callable[..., StageCallResult]
    refine:            Callable[..., StageCallResult]
    select_strategy:   Callable[..., StageCallResult]
    generate_tests:    Callable[..., StageCallResult]

@dataclass(frozen=True)
class HumanFns:
    answer_questions: Callable[[RefinerTurn], list[RefinerAnswer]]
    decide_at_cap: Callable[
        [RequirementRunRecord],
        tuple[Literal[RunOutcome.CAP_GENERATED, RunOutcome.CAP_STOPPED], str],
    ]
```

`stage_fns` entries return a raw dict (simulated parsed LLM JSON); `pipeline.py`
validates it centrally via `Model.model_validate(raw)` — one validation point shared by
fake and real stage fns alike. `human_fns` exists because `RefinerTurn`/`RefinerAnswer`
were deliberately split in the schema so "the same contract works whether the caller is
a CLI loop, a notebook cell, or a FastAPI backend" (`DESIGN_NOTES.md`) — a blocking
`input()` inside the orchestrator would discard that on day one. Kept as a separate
dataclass from `StageFns` because the source is categorically different (a person or a
web request, not an LLM call), not because the injection mechanism differs.

No new `CapAction` enum: `decide_at_cap` returns the existing `RunOutcome` members,
narrowed with `Literal` so it structurally cannot return `COMPLETED` or anything else.

### Failure handling — narrow `except`, so every branch has a producer

```python
try:
    result = stage_fns.classify(req, doc)         # only this line is guarded
except StageCallFailed:
    kind = FailureKind.TRANSPORT
    # no tokens recorded — request never reached the model
except Exception as e:
    kind = FailureKind.OTHER
    message = f"{type(e).__name__}: {e}"
    # no tokens recorded — same reason
else:
    try:
        parsed = Classification.model_validate(result.raw)
    except ValidationError as e:
        kind = FailureKind.VALIDATION
        message = str(e)
        record_usage(result)   # the call succeeded; tokens were spent on rejected output
    else:
        record_usage(result)
```

Only the stage-function call itself is wrapped in `except Exception` — not the
surrounding orchestrator code. A bug in the orchestrator's own loop still crashes
instead of being filed as `kind=OTHER`, which would otherwise be an enum member with no
real producer (`CLAUDE.md`: "don't write a check that can't fire").

## Retry and throttle

Two distinct mechanisms, both necessary:

- **Backoff** fires *after* a `StageCallFailed`, for the case that's supposed to be rare.
- **Throttle** fires *before every call*, to keep the rare case rare. At a tight per-model
  RPM limit, backoff alone means spending most of a multi-requirement run re-hitting the
  same limit. A minimum interval between calls avoids most 429s outright.

```python
@dataclass(frozen=True)
class Throttle:
    sleep_fn: Callable[[float], None] = time.sleep
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    min_interval_seconds: dict[str, float] = field(default_factory=dict)  # keyed by model

def wait_for_slot(throttle: Throttle, model: str, last_call_at: dict[str, datetime]) -> None:
    interval = throttle.min_interval_seconds.get(model, 0.0)
    last = last_call_at.get(model)
    now = throttle.now_fn()
    if last is not None:
        elapsed = (now - last).total_seconds()
        if elapsed < interval:
            throttle.sleep_fn(interval - elapsed)
    last_call_at[model] = throttle.now_fn()
```

Bundled into one dataclass because it's three runtime-plumbing pieces working together
(pacing calls), distinct from the business-logic callables in `StageFns`/`HumanFns`.
`sleep_fn` and `now_fn` are both injected: production defaults to `time.sleep` /
`datetime.now(timezone.utc)`; the harness passes a no-op recorder and a fake fixed clock,
so tests are deterministic and never actually wait. `now_fn` also supplies
`RunMetadata.started_at`, which must be timezone-aware — previously that would have been
a real timestamp captured inside a test.

Keyed **by model**, not global: `RunMetadata.stages[stage].model` already allows
different stages to use different models (e.g. a cheap classifier, a stronger
generator), and those are separate quotas. A single global interval is either too slow
for the cheap model or too fast for the strong one — "too fast" means the exact 429s the
throttle exists to prevent.

**No default interval value is hardcoded.** Neither Gemini's nor Groq's official docs
expose a static free-tier RPM number in text — both defer to a live, logged-in dashboard
(AI Studio / Groq console) that varies by model and account. `min_interval_seconds` is a
required argument for a real run, to be filled in from whichever dashboard is
authoritative for the model in use, not assumed. The harness passes `0.0` for most
scenarios (fixtures don't hit real quota) and a nonzero value in the one scenario that
tests the throttle itself.

**Backoff policy** (separate from throttle, fires on `StageCallFailed` after
`min_interval` has already reduced the failure rate): capped at 3 attempts. Concrete
delay schedule to be tuned once real dashboard limits are read, since a 1s/2s/4s
schedule is meaningless against a per-minute quota. Not fixed as a number in this doc for
the same reason `min_interval_seconds` isn't.

## `resume_at`

Moves from `test_schemas.py` (where it lived only because there was nowhere else for it)
into `orchestrator/pipeline.py` as the real implementation the orchestrator calls. Its
test (`test_resume_positions`) moves to `orchestrator/test_harness.py` with it.
`design/test_schemas.py` goes back to schema-only, matching its role in `CLAUDE.md`. No
import from `test_schemas.py` into `orchestrator/` or vice versa at the schema-test
level — `design/` stays self-contained.

## On-disk layout

Per `ORCHESTRATOR_CONTRACT.md` item 9: `document.json` written with
`requirement_records=[]`, each requirement in its own
`requirements/<id>.json`, reassembled on load. Every write re-validates
(`Model.model_validate(record.model_dump())`) before persisting, per contract item 10.

## Harness scenarios

Plain script, no pytest — matches `test_schemas.py`'s style and its reasoning (one less
dependency, readable pass/fail report, wireable into CI later). Each scenario maps to a
contract item so a failure points at the decision it belongs to.

1. **Happy path** — one document, two requirements: one passes quality check cleanly,
   one fails once then passes after one refinement round. Runs straight through all 7
   stages. Explicitly asserts contract item 2: the `Requirement` handed to stage 3/4 has
   `.text` equal to the original text (clean path) or `refined_text` (refined path) —
   never read from `record.final_text`, which is a reporting convenience only.
2. **Revision cap hit** — quality check fails every round up to the cap;
   `human_fns.decide_at_cap` is invoked; both `CAP_GENERATED` and `CAP_STOPPED` responses
   are exercised, each producing the matching `RunOutcome` and a `cap_reason`.
3. **Issue identity across rounds** — two rounds produce issues at the same
   `(category, span)`; the orchestrator (not the LLM) reuses the same `Issue.id`.
4. **Suppression persists** — `user_confirms_resolved=True` on one round's answer;
   asserted present in `suppressed_issue_ids` on every later round, not just the next one.
5. **`resume_at` correctness** — all 6 derived positions, including the `last.rewrite`
   branch that was wrong in the first version of the contract's prose.
6. **Document-level stage retried within the same run** — a `DocumentStageError` is
   logged, the stage is retried (not a new run started), a second failure bumps
   `retry_count` rather than appending a second error, and outcome climbs
   `DEGRADED` → `COMPLETED` once the retry succeeds.
7. **`DEGRADED` document** — consistency checker and dependency mapper each fail
   independently; the run continues without them per D1=b.
8. **Transport failure → error → resume → finish.** Not just "retries exhaust and
   `outcome=ERROR` is recorded" — that was never the hard part. Continue the scenario: a
   fresh resume pass finds the requirement in `pending_requirement_ids`, restarts it at
   the failed stage, and the run reaches a terminal outcome. This is the actual bug the
   harness exists to catch.
9. **Interruption mid-document, full round-trip.** Write partial files, abandon the
   in-memory orchestrator entirely, construct a new one from disk, continue to
   completion. Distinct from scenario 5 (tests `resume_at` as a pure function) and the
   on-disk checks above (test serialization) — this is the only scenario that proves
   resumability works end-to-end rather than that its pieces individually do.
10. **Validation failure** — a stage returns a `StageCallResult` whose `raw` fails
    `model_validate`; produces `StageError(kind=VALIDATION)`, records usage (tokens were
    spent), retries per the normal policy. New ground relative to the current
    `ORCHESTRATOR_CONTRACT.md`; added there when this is implemented, not presented as if
    it were always specified.
11. **Prompt provenance** — every stage in `RunMetadata.stages` carries a
    `prompt_hash` produced by `prompt_fingerprint`.
12a. **Token usage — validation failures.** Two calls to the same stage return
    validation-failing output, the third succeeds → **3** `TokenUsage` entries (every
    call returned, so every call is metered), `total_tokens` sums all three.
12b. **Token usage — transport failures.** Two calls raise `StageCallFailed`, the third
    succeeds → **1** `TokenUsage` entry (the two 429s never reached the model). Scenarios
    12a/12b are kept separate deliberately: a shared scenario would hide a wrong
    assumption about *where* usage gets appended, since the two failure kinds behave
    oppositely.

## Out of scope for this phase

- `orchestrator/stages.py` (real LLM-calling implementations) — next phase, once the
  harness is green.
- Concrete backoff delay schedule and `min_interval_seconds` values — pending a read of
  the actual account dashboards.
- Prompt text for the seven stages — separate deliverable per `CLAUDE.md`.
