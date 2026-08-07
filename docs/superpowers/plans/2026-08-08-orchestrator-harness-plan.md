# Orchestrator + Simulation Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the real orchestrator control-flow code (stage sequencing, resume, retry, revision cap, suppression) with every LLM call and human-interaction point passed in as a parameter, and prove it correct with a scripted-fixture harness that exercises all of `design/ORCHESTRATOR_CONTRACT.md` before a single real API call is made.

**Architecture:** Two new packages. `design/` gains two schema amendments (`FailureKind`, token usage) and loses `resume_at`/`test_resume_positions` (moved out). `orchestrator/pipeline.py` holds all control flow — `StageFns`/`HumanFns`/`Throttle` as injectable dependencies, `run_document`/`resume_document`/`retry_document_stage` as entry points — with real LLM calls (`orchestrator/stages.py`) deferred to the next phase. `orchestrator/test_harness.py` is a plain script (no pytest, matching `design/test_schemas.py`) driving `pipeline.py` with scripted fixtures through 14 scenarios, each mapped to a contract item.

**Tech Stack:** Python, Pydantic >= 2.1. No new dependencies.

## Global Constraints

- Run everything from the repo root. `design/` and `orchestrator/` are both packages (`__init__.py` added to each); imports are `from design.schemas import ...`, never relative `sys.path` hacks.
- After *any* change to `design/schemas.py`: run `python -m design.test_schemas` (must end "N checks passed, 0 failed") and `python -m design.generate_diagrams` (must exit 0), in that order, every time — per `CLAUDE.md`. These replace the old `python design/test_schemas.py` / `python design/generate_diagrams.py` invocations once `design/` becomes a package (Task 1 updates `CLAUDE.md` to match — a deliberate, called-out change, not silent drift).
- Schema amendments (Tasks 2–3) are isolated, each followed immediately by both commands above, and land *before* any orchestrator code is touched. Do not batch schema work behind orchestrator work.
- Plain-script tests only (`ok`/`accepts`/`rejects`/`section`/`PASSED`/`FAILED`/`main()`, matching `design/test_schemas.py`'s existing style) — no pytest, no mocking library.
- `NamedTuple`/frozen `dataclass` for typed interfaces the orchestrator calls through (`StageFns`, `HumanFns`, `StageCallResult`) — a typo'd field name must be an immediate `TypeError`, never a silently-skipped dict key. `Throttle` is the one non-frozen dataclass (it owns mutable per-model call-time bookkeeping).
- No `datetime.now()`/`time.sleep` calls anywhere in `orchestrator/pipeline.py`'s logic — always through the injected `Throttle.now_fn` / `Throttle.sleep_fn`, so tests are deterministic and never actually wait.
- If the harness surfaces a further schema/contract gap during implementation (it already found two before any code existed), the fix is to amend `docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md` and `design/ORCHESTRATOR_CONTRACT.md`, not to work around the gap in `pipeline.py`. Task 12 already does this once, for scenario 10 (validation-failure `StageError`) — treat any later one the same way, as its own small addendum, not a special case.
- Reference: `docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md` (the approved design this plan implements) and `design/ORCHESTRATOR_CONTRACT.md` (the 12 obligations the schema doesn't enforce). Don't restate their reasoning in commit messages or code comments — cite them.

---

## File Structure

```
design/
  __init__.py                 # NEW, empty — makes design importable as a package
  schemas.py                  # MODIFIED — FailureKind, TokenUsage, DocumentTokenUsage (Tasks 2-3)
  test_schemas.py             # MODIFIED — package import, kind= added, resume_at/test_resume_positions removed (Tasks 1,2,4)
  generate_diagrams.py        # MODIFIED — package import, FERR label updated (Tasks 1,2)
  ORCHESTRATOR_CONTRACT.md    # MODIFIED — cites the two schema amendments and scenario 10 (Tasks 2,3,12)
CLAUDE.md                     # MODIFIED — the two mandatory commands become `python -m design.*` (Task 1)
orchestrator/
  __init__.py                 # NEW, empty
  pipeline.py                 # NEW — everything described below
  test_harness.py             # NEW — fixtures + 14 scenarios, plain-script style
  stages.py                   # NEW, stub only — real LLM-calling fns, next phase
```

`orchestrator/pipeline.py` responsibilities, in the order they're built:
1. Injectable interfaces: `StageCallResult`, `StageCallFailed`, `StageFns`, `HumanFns`, `Throttle` (Task 5-6).
2. `resume_at` (Task 4, moved from `design/test_schemas.py`).
3. `call_stage` / `call_document_stage`: the narrow-`except` wrapper that turns a raw stage call into a validated model or a classified failure, recording token usage exactly when a call returns (Task 7).
4. `run_requirement`: the per-requirement stage sequence, including the quality-check/refine loop with issue-id reuse, suppression carry-forward, and the revision cap (Task 9).
5. `run_document` / document-level stage runner with `DEGRADED` continuation (Task 8, wired together in Task 10).
6. On-disk layout: `write_document_run`, `write_requirement_run`, `read_document_run` (Task 9... actually Task 10 — see task list).
7. `retry_document_stage`, `resume_document` (Task 11).

---

## Task 1: Package scaffolding and import cleanup

**Files:**
- Create: `design/__init__.py` (empty)
- Create: `orchestrator/__init__.py` (empty)
- Create: `orchestrator/stages.py` (stub, real LLM calls come next phase)
- Modify: `design/test_schemas.py:25-43` (imports)
- Modify: `design/generate_diagrams.py:35-44` (imports)
- Modify: `CLAUDE.md` (the two mandatory commands)

**Interfaces:**
- Produces: `design` and `orchestrator` are importable packages from the repo root. All later tasks `from design.schemas import ...` and `from orchestrator.pipeline import ...`.

- [ ] **Step 1: Create the two `__init__.py` files**

```bash
: > design/__init__.py
: > orchestrator/__init__.py
```

(Or use the Write tool with empty content — either way, zero bytes, just package markers.)

- [ ] **Step 2: Create the `orchestrator/stages.py` stub**

```python
"""Real LLM-calling stage functions -- NOT built this phase.

Every function here must eventually return an orchestrator.pipeline.StageCallResult,
matching the signature its StageFns/HumanFns field expects. Wiring these up is the
next phase, once orchestrator/test_harness.py is green against fakes.
"""
```

- [ ] **Step 3: Fix `design/test_schemas.py`'s import**

Replace:
```python
sys.path.insert(0, str(Path(__file__).parent))

from pydantic import ValidationError  # noqa: E402

from schemas import (  # noqa: E402
    ALL_STAGES, Classification, ELIGIBLE_TECHNIQUES, ClarifyingQuestion, ConsistencyConflict,
    ConsistencyReport, DependencyLink, DependencyReport, DocumentOutcome,
    DocumentRunRecord, DocumentStage, DocumentStageError, Issue, IssueCategory,
    PipelineStage, QualityReport, RefinedRequirement, RefinementRound, RefinerAnswer,
    RefinerTurn, Requirement, RequirementRunRecord, RequirementSet, RunMetadata,
    RunOutcome, StageConfig, StageError, SystemType, TestCase, TestPlan, TestStrategy,
    TestTechnique, fields_carrying_requirement_id, prompt_fingerprint,
)
from schemas import _DOCUMENT_OUTCOME_RULES, _OUTCOME_RULES  # noqa: E402
```
with:
```python
from pydantic import ValidationError

from design.schemas import (
    ALL_STAGES, Classification, ELIGIBLE_TECHNIQUES, ClarifyingQuestion, ConsistencyConflict,
    ConsistencyReport, DependencyLink, DependencyReport, DocumentOutcome,
    DocumentRunRecord, DocumentStage, DocumentStageError, Issue, IssueCategory,
    PipelineStage, QualityReport, RefinedRequirement, RefinementRound, RefinerAnswer,
    RefinerTurn, Requirement, RequirementRunRecord, RequirementSet, RunMetadata,
    RunOutcome, StageConfig, StageError, SystemType, TestCase, TestPlan, TestStrategy,
    TestTechnique, fields_carrying_requirement_id, prompt_fingerprint,
)
from design.schemas import _DOCUMENT_OUTCOME_RULES, _OUTCOME_RULES
```
Also remove the now-unused `import sys` and `from pathlib import Path` lines if nothing else in the file uses them (check with a search for `sys.` and `Path(` elsewhere in the file first — `Path` may still be used by test fixtures; only drop what's actually unused).

- [ ] **Step 4: Fix `design/generate_diagrams.py`'s import**

Replace:
```python
sys.path.insert(0, str(Path(__file__).parent))
import schemas  # noqa: E402
```
with:
```python
import design.schemas as schemas
```
Keep `from pathlib import Path` — it's still used for `OUT_DIR`.

- [ ] **Step 5: Update `CLAUDE.md`'s mandatory commands**

Find the section:
```
## After any change to `design/schemas.py`

Both, every time:

​```bash
python design/test_schemas.py        # must end "N checks passed, 0 failed"
python design/generate_diagrams.py   # rewrites the five .mermaid files
​```
```
Replace the code block with:
```bash
python -m design.test_schemas        # must end "N checks passed, 0 failed"
python -m design.generate_diagrams   # rewrites the five .mermaid files
```
Add one line above it explaining why: `design/` became a package (`design/__init__.py`) as part of the orchestrator work in `docs/superpowers/plans/2026-08-08-orchestrator-harness-plan.md`, so both scripts are now run as modules from the repo root rather than as bare file paths.

- [ ] **Step 6: Verify both commands still work from the repo root**

Run: `python -m design.test_schemas`
Expected: ends with `265 checks passed, 0 failed` (or whatever the current count is — same count as before this task, since no test changed yet)

Run: `python -m design.generate_diagrams`
Expected: exits 0, rewrites the five `.mermaid` files with no validation errors

- [ ] **Step 7: Commit**

```bash
git add design/__init__.py orchestrator/__init__.py orchestrator/stages.py \
        design/test_schemas.py design/generate_diagrams.py CLAUDE.md
git commit -m "$(cat <<'EOF'
Make design/ and orchestrator/ proper packages

Sets up for orchestrator/pipeline.py to import design.schemas without a
third sys.path hack. CLAUDE.md's two mandatory commands become
`python -m design.*` accordingly.
EOF
)"
```

---

## Task 2: Schema amendment — `FailureKind`

**Files:**
- Modify: `design/schemas.py` (add `FailureKind`, add `kind` field to `StageError` and `DocumentStageError`)
- Modify: `design/test_schemas.py` (every `StageError(...)`/`DocumentStageError(...)` construction gets a `kind=`, plus new anchor coverage)
- Modify: `design/generate_diagrams.py:213` (the `FERR` label)
- Modify: `design/ORCHESTRATOR_CONTRACT.md` (cite this amendment)

**Interfaces:**
- Produces: `design.schemas.FailureKind` (enum: `TRANSPORT`, `VALIDATION`, `OTHER`), `StageError.kind: FailureKind`, `DocumentStageError.kind: FailureKind` — both required, no default.

- [ ] **Step 1: Write the failing test first**

In `design/test_schemas.py`, add to `test_gap2_requirement_outcomes` (or wherever `StageError` is first exercised) — actually, add a new small test function since this is new ground:

```python
def test_failure_kind() -> None:
    """FailureKind distinguishes why a stage call failed -- see the design doc."""
    section("FailureKind")
    rejects("StageError without kind",
            lambda: StageError(stage=PipelineStage.CLASSIFIER, message="x"))
    accepts("StageError with kind=TRANSPORT",
            lambda: StageError(stage=PipelineStage.CLASSIFIER, kind=FailureKind.TRANSPORT,
                               message="429"))
    accepts("StageError with kind=VALIDATION",
            lambda: StageError(stage=PipelineStage.CLASSIFIER, kind=FailureKind.VALIDATION,
                               message="schema rejected"))
    accepts("StageError with kind=OTHER",
            lambda: StageError(stage=PipelineStage.CLASSIFIER, kind=FailureKind.OTHER,
                               message="KeyError: 'foo'"))
    accepts("DocumentStageError with kind",
            lambda: DocumentStageError(stage=DocumentStage.CONSISTENCY_CHECKER,
                                       kind=FailureKind.TRANSPORT, message="429"))
```
Also add the import: `from design.schemas import (... FailureKind, ...)`.
Add `test_failure_kind` to the `for fn in (...)` tuple in `main()`, right after `test_gap2_requirement_outcomes`.

- [ ] **Step 2: Run it, confirm it fails**

Run: `python -m design.test_schemas`
Expected: `NameError: name 'FailureKind' is not defined` (or `ImportError` on the new import) — `FailureKind` doesn't exist yet.

- [ ] **Step 3: Add `FailureKind` and the `kind` field in `design/schemas.py`**

Immediately above `class StageError(BaseModel):` (currently around line 712), add:

```python
class FailureKind(str, Enum):
    """Why a stage call ultimately failed. Distinguishes three cases that mean
    different things for retry policy and for the thesis's LLM-reliability numbers:
    a rejected request (retry usually helps), a schema-rejected model output (retrying
    may help, but "how often does this model produce invalid output" is itself a
    finding), and anything else caught but not anticipated. See
    docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md.

    Not exhaustive of every possible mistake by construction, which is exactly why
    OTHER exists -- a two-value enum would force a 500 or an SDK bug into one of the
    other two buckets, corrupting both counts silently. OTHER is for a caught stage-call
    failure of unanticipated type; it is NOT for bugs in the orchestrator's own control
    flow, which must still crash rather than be filed here (see
    orchestrator/pipeline.py's call_stage).
    """
    TRANSPORT = "transport"
    VALIDATION = "validation"
    OTHER = "other"
```

Then modify `StageError` and `DocumentStageError`:

```python
class StageError(BaseModel):
    stage: PipelineStage
    kind: FailureKind
    message: NonEmptyStr
    retry_count: int = Field(0, ge=0)


class DocumentStageError(BaseModel):
    stage: DocumentStage
    kind: FailureKind
    message: NonEmptyStr
    retry_count: int = Field(0, ge=0)
```
(Keep every existing comment on both classes — only inserting the `kind` line between `stage` and `message`.)

- [ ] **Step 4: Update every existing `StageError`/`DocumentStageError` construction in `design/test_schemas.py`**

Search for `StageError(` and `DocumentStageError(` (11 remaining call sites besides the ones just added in Step 1) and add `kind=FailureKind.TRANSPORT` to each — every existing fixture uses `message="429"` or similar rate-limit text, so `TRANSPORT` is the correct kind for all of them:

- Line ~165 (`VALID_RECORDS[RunOutcome.ERROR]`): `StageError(stage=PipelineStage.CLASSIFIER, kind=FailureKind.TRANSPORT, message="429 rate limit", retry_count=3)`
- Line ~181 (`CE`): `DocumentStageError(stage=DocumentStage.CONSISTENCY_CHECKER, kind=FailureKind.TRANSPORT, message="429", retry_count=2)`
- Line ~182 (`DE`): `DocumentStageError(stage=DocumentStage.DEPENDENCY_MAPPER, kind=FailureKind.TRANSPORT, message="malformed JSON")` — note: this one's message describes a validation-style failure; change its `kind` to `FailureKind.VALIDATION` instead, since fixture text should match the kind it claims.
- Line ~283: add `kind=FailureKind.TRANSPORT`
- Line ~286 (the `retry_count=-1` rejection test): add `kind=FailureKind.TRANSPORT` — the test is about `retry_count`, not `kind`, so give `kind` a valid value so the rejection is unambiguously about `retry_count`.
- Line ~458: add `kind=FailureKind.TRANSPORT`
- Line ~473, ~475 (the two "wrong stage type" rejection tests): add `kind=FailureKind.TRANSPORT` — these tests are about `stage`, not `kind`, same reasoning as above.
- Line ~484: add `kind=FailureKind.TRANSPORT`
- Line ~1000, ~1008, ~1017 (`gen_failed`, `qc` lambda), ~1024, ~1029: add `kind=FailureKind.TRANSPORT`
- Line ~1069 (`err = lambda stage: [StageError(stage=stage, ...)]` inside what will become `test_resume_positions`): add `kind=FailureKind.TRANSPORT` — this one moves to `orchestrator/test_harness.py` in Task 4, but fix it here first so `test_schemas.py` stays green in the meantime.

Add `FailureKind` to the import list at the top of the file.

- [ ] **Step 5: Add the two rule-table anchors for `_DOCUMENT_OUTCOME_RULES`/`_OUTCOME_RULES` — skip, none needed**

(`FailureKind` isn't part of `_OutcomeRule` — no anchor test needed here. This step is a no-op; don't add a check that can't fail differently from Step 1's.)

- [ ] **Step 6: Run the test suite, confirm it passes**

Run: `python -m design.test_schemas`
Expected: ends with `N checks passed, 0 failed` where `N` is the old count + 5 (the new `test_failure_kind` checks)

- [ ] **Step 7: Regenerate diagrams**

Run: `python -m design.generate_diagrams`
Expected: exits 0. `design/models.mermaid` now shows `FailureKind` and the `kind` field automatically (it's introspected at runtime) — open it briefly to confirm, no manual edit needed there.

- [ ] **Step 8: Update the hand-written `FERR` label in `design/generate_diagrams.py`**

Find (around line 213):
```python
    "FERR":   ('<b>ERROR</b><br/>StageError: stage · message · retry_count<br/><i>partial record still persisted</i>', "term"),
```
Replace with:
```python
    "FERR":   ('<b>ERROR</b><br/>StageError: stage · kind · message · retry_count<br/><i>partial record still persisted</i>', "term"),
```

- [ ] **Step 9: Regenerate diagrams again, confirm the label change took**

Run: `python -m design.generate_diagrams`
Expected: exits 0; `design/paths_failure.mermaid` (or wherever `FERR` renders) now shows `kind` in the field list.

- [ ] **Step 10: Add a line to `design/ORCHESTRATOR_CONTRACT.md`**

At the end of item 7 ("Retries and failures"), add a paragraph:

```markdown
**`FailureKind`** (added 2026-08-08, see
`docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md`): every `StageError`
and `DocumentStageError` now carries `kind: TRANSPORT | VALIDATION | OTHER`. `TRANSPORT`
is a rejected request (retry usually helps); `VALIDATION` is a model output that failed
schema validation (the call succeeded, tokens were spent, retrying may help since LLM
output is nondeterministic); `OTHER` is a caught-but-unanticipated failure and must never
be used for a bug in the orchestrator's own control flow, which should crash instead.
```

- [ ] **Step 11: Commit**

```bash
git add design/schemas.py design/test_schemas.py design/generate_diagrams.py \
        design/ORCHESTRATOR_CONTRACT.md
git commit -m "$(cat <<'EOF'
Add FailureKind to StageError and DocumentStageError

Distinguishes a rejected request from a schema-rejected model output
from anything else caught but unanticipated -- surfaced while
designing the orchestrator harness, before either mattered by
accident. See docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md.
EOF
)"
```

---

## Task 3: Schema amendment — token usage

**Files:**
- Modify: `design/schemas.py` (`TokenUsage`, `DocumentTokenUsage`, `usage` fields, `total_tokens`/`document_stage_tokens` computed fields)
- Modify: `design/test_schemas.py` (new test coverage)
- Modify: `design/ORCHESTRATOR_CONTRACT.md` (cite this amendment)

**Interfaces:**
- Consumes: `PipelineStage`, `DocumentStage` (existing)
- Produces: `TokenUsage(stage, prompt_tokens, completion_tokens)`, `DocumentTokenUsage(stage, prompt_tokens, completion_tokens)`, `RequirementRunRecord.usage: list[TokenUsage]`, `RequirementRunRecord.total_tokens` (computed), `DocumentRunRecord.usage: list[DocumentTokenUsage]`, `DocumentRunRecord.document_stage_tokens` (computed). Task 7 (`call_stage`) constructs `TokenUsage` entries; Task 8 (`call_document_stage`) constructs `DocumentTokenUsage` entries.

- [ ] **Step 1: Write the failing test**

Add to `design/test_schemas.py`:

```python
def test_token_usage() -> None:
    """TokenUsage is a log mirroring StageError/DocumentStageError -- see the design doc."""
    section("Token usage")
    u1 = TokenUsage(stage=PipelineStage.CLASSIFIER, prompt_tokens=100, completion_tokens=20)
    u2 = TokenUsage(stage=PipelineStage.QUALITY_CHECKER, prompt_tokens=50, completion_tokens=10)
    r = rec(usage=[u1, u2])
    ok("total_tokens sums all entries", r.total_tokens == 180)
    ok("empty usage sums to zero", rec().total_tokens == 0)
    rejects("TokenUsage rejects negative prompt_tokens",
            lambda: TokenUsage(stage=PipelineStage.CLASSIFIER, prompt_tokens=-1, completion_tokens=0))
    rejects("TokenUsage rejects negative completion_tokens",
            lambda: TokenUsage(stage=PipelineStage.CLASSIFIER, prompt_tokens=0, completion_tokens=-1))

    du1 = DocumentTokenUsage(stage=DocumentStage.CONSISTENCY_CHECKER, prompt_tokens=200, completion_tokens=40)
    du2 = DocumentTokenUsage(stage=DocumentStage.DEPENDENCY_MAPPER, prompt_tokens=300, completion_tokens=60)
    d = doc(usage=[du1, du2])
    ok("document_stage_tokens sums only document-level usage", d.document_stage_tokens == 600)
    ok("document_stage_tokens ignores requirement_records",
       doc(usage=[du1], requirement_records=[rec(usage=[u1])]).document_stage_tokens == 220)
    ok("total_tokens is read-only",
       _raises_attribute_error_total_tokens(r))
    ok("usage round trips through JSON",
       RequirementRunRecord.model_validate_json(r.model_dump_json()).total_tokens == 180)


def _raises_attribute_error_total_tokens(record) -> bool:
    try:
        record.total_tokens = 999
        return False
    except AttributeError:
        return True
```

Add `TokenUsage`, `DocumentTokenUsage` to the import list, and add `test_token_usage` to `main()`'s tuple, after `test_failure_kind`.

- [ ] **Step 2: Run it, confirm it fails**

Run: `python -m design.test_schemas`
Expected: `NameError: name 'TokenUsage' is not defined`

- [ ] **Step 3: Add the models and fields in `design/schemas.py`**

Immediately above `class StageError(BaseModel):`, add (this can go right after the new `FailureKind` from Task 2):

```python
class TokenUsage(BaseModel):
    """One entry per API call that returned, whether it validated or not.

    Tokens, not cost: prices change, a stored cost freezes today's price into a record
    read months later. Tokens x a price table kept separately gives cost at whatever
    price is current. No model field: RunMetadata.stages[stage].model already says
    which model served this stage for the whole run -- repeating it here would be a
    denormalised copy requiring agreement, the exact pattern behind most bugs in this
    project. Never recorded for a StageCallFailed (transport failure): the request was
    rejected before inference, so no tokens were spent. See
    docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md.
    """
    stage: PipelineStage
    prompt_tokens: int = Field(..., ge=0)
    completion_tokens: int = Field(..., ge=0)


class DocumentTokenUsage(BaseModel):
    """Structurally identical to TokenUsage apart from the stage type -- same reasoning
    as the StageError/DocumentStageError split: a shared PipelineStage | DocumentStage
    union would let a RequirementRunRecord's usage list name a document stage again."""
    stage: DocumentStage
    prompt_tokens: int = Field(..., ge=0)
    completion_tokens: int = Field(..., ge=0)
```

In `RequirementRunRecord`, add the field right after `test_plan`:
```python
    test_plan: Optional[TestPlan] = None
    usage: list[TokenUsage] = Field(default_factory=list)
```
And add the computed field at the end of the class, after `issue_history`:
```python
    # Cost at any price table, at any point: tokens x price, computed by the caller.
    # A stage retried twice then succeeded shows 3 entries here -- once per call that
    # returned, including the two that failed validation and got thrown away.
    @computed_field
    @property
    def total_tokens(self) -> int:
        return sum(u.prompt_tokens + u.completion_tokens for u in self.usage)
```

In `DocumentRunRecord`, add the field right after `requirement_records`:
```python
    requirement_records: list[RequirementRunRecord] = Field(default_factory=list)
    usage: list[DocumentTokenUsage] = Field(default_factory=list)
```
And add the computed field at the end of the class, after `pending_requirement_ids`:
```python
    # Deliberately NOT named total_tokens. Under D2b, requirement_records arrives
    # empty in the on-disk document.json and is only populated after assembly from
    # requirements/*.json -- a field named total_tokens would silently return near-zero
    # on disk and a large number post-assembly, same name, two different answers
    # depending on when it's read. This sums only the two document-level stages.
    # Whole-document cost is document_stage_tokens + sum(r.total_tokens for r in
    # requirement_records), computed by the caller -- not implied by a field name.
    @computed_field
    @property
    def document_stage_tokens(self) -> int:
        return sum(u.prompt_tokens + u.completion_tokens for u in self.usage)
```

- [ ] **Step 4: Run the test suite, confirm it passes**

Run: `python -m design.test_schemas`
Expected: ends with `N checks passed, 0 failed` where `N` is the Task 2 count + 9 (roughly — count the `ok`/`rejects`/`accepts` calls in Step 1)

- [ ] **Step 5: Regenerate diagrams**

Run: `python -m design.generate_diagrams`
Expected: exits 0. `TokenUsage`/`DocumentTokenUsage` appear automatically in `design/models.mermaid` (introspected). No hand-written label mentions token fields, so no manual diagram edit needed this time (unlike Task 2's `FERR`).

- [ ] **Step 6: Add a line to `design/ORCHESTRATOR_CONTRACT.md`**

Add a new numbered item, 13, after item 12 ("Prompt provenance"):

```markdown
## 13. Token usage

Every stage call that *returns* (success or a validation failure — both mean inference
happened) gets one `TokenUsage`/`DocumentTokenUsage` entry appended, via
`orchestrator.pipeline.call_stage`/`call_document_stage`. A `StageCallFailed` (transport
failure) never gets one — the request was rejected before inference, so no tokens were
spent, and the stage fn raises in that case with no result to carry counts.

`RequirementRunRecord.total_tokens` and `DocumentRunRecord.document_stage_tokens` are
computed, never stored directly — the latter is deliberately not named `total_tokens`,
since it can only ever sum the two document-level stages, never the requirement records
(which arrive empty in `document.json` under D2b). Whole-document cost is
`doc.document_stage_tokens + sum(r.total_tokens for r in doc.requirement_records)`,
computed by the caller.

*(Added 2026-08-08, see docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md.)*
```

- [ ] **Step 7: Commit**

```bash
git add design/schemas.py design/test_schemas.py design/ORCHESTRATOR_CONTRACT.md
git commit -m "$(cat <<'EOF'
Add token usage tracking to RequirementRunRecord and DocumentRunRecord

Tokens, not cost, so a price table applied later gives current cost
rather than freezing today's price. Mirrors the StageError/
DocumentStageError stage-type split. document_stage_tokens is named
to avoid the total_tokens naming trap under D2b assembly.
EOF
)"
```

---

## Task 4: Move `resume_at` out of `design/test_schemas.py`

**Files:**
- Create: `orchestrator/test_harness.py` (fixtures + `resume_at` tests, first content in this file)
- Modify: `orchestrator/pipeline.py` (create, with just `resume_at` for now)
- Modify: `design/test_schemas.py` (remove `resume_at`, `test_resume_positions`, its entry in `main()`, and its now-unused `err` lambda / fixtures if nothing else uses them)
- Modify: `CLAUDE.md` ("Rules learned the hard way" section — the `test_resume_positions` citation moves with the test)

**Interfaces:**
- Consumes: `design.schemas.{PipelineStage, RequirementRunRecord}`
- Produces: `orchestrator.pipeline.resume_at(record: RequirementRunRecord) -> Optional[PipelineStage]` — every later task that needs to know where a requirement left off calls this.

- [ ] **Step 1: Create `orchestrator/pipeline.py` with `resume_at`**

```python
"""Orchestrator control flow: stage sequencing, resume, retry, revision cap.

Every LLM call and human-interaction point is a parameter (StageFns, HumanFns), not a
hardcoded call -- see docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md.
orchestrator/test_harness.py wires in fixtures; orchestrator/stages.py (next phase)
wires in real ones. No control-flow logic is built twice.
"""

from __future__ import annotations

from typing import Optional

from design.schemas import PipelineStage, RequirementRunRecord


def resume_at(rec: RequirementRunRecord) -> Optional[PipelineStage]:
    """Where an interrupted or errored requirement record should resume.

    Moved here from design/test_schemas.py, where it lived "so it stays honest"
    (design/ORCHESTRATOR_CONTRACT.md item 6) because there was nowhere else for it --
    the schema deliberately does not encode pipeline ordering. Now that the orchestrator
    exists, this is the real implementation the orchestrator calls, not a copy kept in
    sync with one. Its test moved with it, to orchestrator/test_harness.py.
    """
    if rec.classification is None:
        return PipelineStage.CLASSIFIER
    if not rec.rounds:
        return PipelineStage.QUALITY_CHECKER
    last = rec.rounds[-1]
    if not last.quality_report.passed:
        # A round whose check failed but which already rewrote has finished refining;
        # the next step is checking that rewrite, i.e. the next round.
        return PipelineStage.REFINER if last.rewrite is None else PipelineStage.QUALITY_CHECKER
    if rec.test_strategy is None:
        return PipelineStage.STRATEGY_SELECTOR
    if rec.test_plan is None:
        return PipelineStage.TEST_GENERATOR
    return None
```

- [ ] **Step 2: Create `orchestrator/test_harness.py` with fixtures and the `resume_at` test**

```python
"""
Simulation harness for the orchestrator. Run after any change to orchestrator/pipeline.py:

    python -m orchestrator.test_harness

Plain script, no pytest -- same reasoning as design/test_schemas.py: one less
dependency, readable pass/fail report, wireable into CI later. design/ and
orchestrator/ import in one direction only (orchestrator -> design); this file never
imports from design/test_schemas.py, so design/ stays self-contained.

Each scenario is numbered to match docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md's
"Harness scenarios" section.
"""

from __future__ import annotations

from datetime import datetime, timezone

from design.schemas import (
    ClarifyingQuestion, PipelineStage, QualityReport, RefinedRequirement,
    RefinementRound, RefinerAnswer, RefinerTurn, Requirement, RequirementRunRecord,
    RequirementSet, RunOutcome, StageConfig, StageError, RunMetadata, ALL_STAGES,
    FailureKind, prompt_fingerprint,
)
from orchestrator.pipeline import resume_at

PASSED = 0
FAILED: list[str] = []


def ok(label: str, condition: bool = True) -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(label)
        print(f"    FAIL  {label}")


def section(name: str) -> None:
    print(f"\n{name}")


# ---------------------------------------------------------------------------
# Shared fixtures -- deliberately independent of design/test_schemas.py's fixtures.
# ---------------------------------------------------------------------------

FAKE_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

T0 = "Temperatures that do not exceed these limits shall be output for subsequent processing."
T1 = "Temperatures within the valid range defined in DOC-REQ-B shall be output for subsequent processing."

REQ_A = Requirement(id="DOC-REQ-A", text=T0, source_doc_id="harness-doc")
REQ_B = Requirement(
    id="DOC-REQ-B", source_doc_id="harness-doc",
    text="If the current temperature value is outside the valid range, the system shall output an invalid status.",
)
DOC = RequirementSet(doc_id="harness-doc", requirements=[REQ_A, REQ_B])

STAGE_CONFIGS = {s: StageConfig(model="fake-model", prompt_hash=prompt_fingerprint(f"prompt for {s}"))
                 for s in ALL_STAGES}


def make_metadata(run_id: str = "run-harness-001") -> RunMetadata:
    return RunMetadata(run_id=run_id, started_at=FAKE_NOW, stages=STAGE_CONFIGS,
                       prompt_version="test-v1")


def rec(**kw) -> RequirementRunRecord:
    kw.setdefault("run_id", "run-harness-001")
    kw.setdefault("requirement", REQ_A)
    return RequirementRunRecord(**kw)


def mk_round(n, text, passed=True, rewrite_to=None, requirement_id=REQ_A.id) -> RefinementRound:
    quality_report = QualityReport(requirement_id=requirement_id, passed=passed,
                                    issues=[] if passed else [_dummy_issue(n)])
    turn = None
    answers = []
    rewrite = None
    if not passed:
        issue = quality_report.issues[0]
        turn = RefinerTurn(requirement_id=requirement_id, revision_number=n, questions=[
            ClarifyingQuestion(id=f"Q{n}", issue_id=issue.id, issue_category=issue.category,
                               question_text="?")])
        answers = [RefinerAnswer(question_id=f"Q{n}", answer_text="answer")]
        if rewrite_to:
            rewrite = RefinedRequirement(requirement_id=requirement_id, original_text=text,
                                         refined_text=rewrite_to, revision_number=n,
                                         answers_used=answers)
    return RefinementRound(revision_number=n, text_checked=text, quality_report=quality_report,
                           turn=turn, answers=answers, rewrite=rewrite)


def _dummy_issue(n):
    from design.schemas import Issue, IssueCategory
    return Issue(id=f"I{n}", category=IssueCategory.VAGUE_PRONOUN, span="these limits",
                explanation="Unresolved referent.")


# ---------------------------------------------------------------------------

def test_resume_positions() -> None:
    """Scenario 5: a failure at any stage must resume at that stage -- nothing earlier redone."""
    section("Scenario 5 -- resume_at correctness")
    err = lambda stage: [StageError(stage=stage, kind=FailureKind.TRANSPORT, message="429",
                                    retry_count=3)]
    mid_round = mk_round(1, T0, passed=False)                        # no rewrite yet
    rewritten = mk_round(1, T0, passed=False, rewrite_to=T1)
    from design.schemas import Classification, SystemType, TestStrategy, TestTechnique
    cls = Classification(requirement_id=REQ_A.id, system_type=SystemType.OTHER, rationale="r")
    strategy = TestStrategy(requirement_id=REQ_A.id, system_type=SystemType.OTHER,
                            techniques=[TestTechnique.BOUNDARY_VALUE_ANALYSIS], rationale="r")
    rounds_refined = [rewritten, mk_round(2, T1, passed=True)]

    cases = [
        ("classifier failed", dict(errors=err(PipelineStage.CLASSIFIER)), PipelineStage.CLASSIFIER),
        ("quality checker failed on round 1",
         dict(errors=err(PipelineStage.QUALITY_CHECKER), classification=cls),
         PipelineStage.QUALITY_CHECKER),
        ("refiner failed mid-round, nothing rewritten yet",
         dict(errors=err(PipelineStage.REFINER), classification=cls, rounds=[mid_round]),
         PipelineStage.REFINER),
        ("quality checker failed on round 2, round 1 already rewrote",
         dict(errors=err(PipelineStage.QUALITY_CHECKER), classification=cls, rounds=[rewritten]),
         PipelineStage.QUALITY_CHECKER),
        ("strategy selector failed",
         dict(errors=err(PipelineStage.STRATEGY_SELECTOR), classification=cls, rounds=rounds_refined),
         PipelineStage.STRATEGY_SELECTOR),
        ("test generator failed",
         dict(errors=err(PipelineStage.TEST_GENERATOR), classification=cls, rounds=rounds_refined,
              test_strategy=strategy), PipelineStage.TEST_GENERATOR),
    ]
    for label, kw, expected in cases:
        got = resume_at(rec(outcome=RunOutcome.ERROR, **kw))
        ok(f"{label} -> resume at {expected.value}", got is expected)

    ok("an interrupted record resumes at the classifier", resume_at(rec()) is PipelineStage.CLASSIFIER)


def main() -> int:
    print("=" * 72)
    print("orchestrator simulation harness")
    print("=" * 72)
    for fn in (test_resume_positions,):
        fn()
    print("\n" + "=" * 72)
    if FAILED:
        print(f"{PASSED} passed, {len(FAILED)} FAILED:")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print(f"{PASSED} checks passed, 0 failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run the new harness, confirm it passes**

Run: `python -m orchestrator.test_harness`
Expected: ends with `7 checks passed, 0 failed`

- [ ] **Step 4: Remove `resume_at` and `test_resume_positions` from `design/test_schemas.py`**

Delete the `resume_at` function (the copy, not the one just created in `orchestrator/pipeline.py`) and the `test_resume_positions` function entirely from `design/test_schemas.py`. Remove `test_resume_positions` from the `for fn in (...)` tuple in `main()`.

- [ ] **Step 5: Run `design/test_schemas.py`, confirm it still passes with a lower count**

Run: `python -m design.test_schemas`
Expected: ends with `M checks passed, 0 failed`, where `M` = (Task 3's count) − 8 (the `test_resume_positions` checks: 6 case rows + 1 interrupted + 1 finished-record check that also gets deleted along with it — count the actual `ok(...)` calls removed to get the exact number)

- [ ] **Step 6: Update the stale citation in `CLAUDE.md`**

`CLAUDE.md`'s "Rules learned the hard way" section says:

> **A spec nobody executes drifts.** The resume rule lived only in prose and was wrong for
> one case. It is now executed by `test_schemas.py::test_resume_positions`. If a document
> describes logic, test the logic.

The test just moved. Update the citation:

```markdown
**A spec nobody executes drifts.** The resume rule lived only in prose and was wrong for
one case. It is now executed by `orchestrator/test_harness.py::test_resume_positions`. If
a document describes logic, test the logic.
```

- [ ] **Step 7: Commit**

```bash
git add orchestrator/pipeline.py orchestrator/test_harness.py design/test_schemas.py CLAUDE.md
git commit -m "$(cat <<'EOF'
Move resume_at from design/test_schemas.py to orchestrator/pipeline.py

It lived in the schema test suite only because there was nowhere else
for it. Now it has a real owner (the orchestrator that actually calls
it) instead of being a copy kept honest by a test in a different
package. design/ goes back to schema-only.
EOF
)"
```

---

## Task 5: `StageCallResult`, `StageCallFailed`, `StageFns`, `HumanFns`

**Files:**
- Modify: `orchestrator/pipeline.py` (add the four types)
- Modify: `orchestrator/test_harness.py` (add tests + the `Scripted` fixture helper used by every later scenario)

**Interfaces:**
- Consumes: `design.schemas.{RefinerTurn, RefinerAnswer, RequirementRunRecord, RunOutcome}`
- Produces: `StageCallResult(raw, prompt_tokens, completion_tokens)` (NamedTuple), `StageCallFailed(Exception)`, `StageFns` (frozen dataclass, 7 callables), `HumanFns` (frozen dataclass, 2 callables). Every later task's `stage_fns`/`human_fns` parameters are these types.

- [ ] **Step 1: Write the failing tests**

Add to `orchestrator/test_harness.py`:

```python
def test_stage_fns_typo_is_a_typeerror() -> None:
    """A typo'd field name must be an immediate TypeError, not a silently-skipped key --
    the reason StageFns/HumanFns are dataclasses, not dicts."""
    section("StageFns / HumanFns construction")
    from orchestrator.pipeline import StageFns, HumanFns

    def not_provided(*a, **k):
        raise AssertionError("should never be called")

    try:
        StageFns(check_consistency=not_provided, map_dependencies=not_provided,
                 classify=not_provided, check_quality=not_provided, refine=not_provided,
                 select_strategy=not_provided, generate_tests=not_provided,
                 clasify=not_provided)  # typo
        ok("StageFns rejects an unknown field", False)
    except TypeError:
        ok("StageFns rejects an unknown field", True)

    try:
        HumanFns(answer_questions=not_provided, decide_at_cap=not_provided,
                 decide_at_capp=not_provided)  # typo
        ok("HumanFns rejects an unknown field", False)
    except TypeError:
        ok("HumanFns rejects an unknown field", True)

    real = StageFns(check_consistency=not_provided, map_dependencies=not_provided,
                    classify=not_provided, check_quality=not_provided, refine=not_provided,
                    select_strategy=not_provided, generate_tests=not_provided)
    ok("StageFns constructs with exactly the right fields", real.classify is not_provided)
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `python -m orchestrator.test_harness`
Expected: `ImportError: cannot import name 'StageFns' from 'orchestrator.pipeline'`

- [ ] **Step 3: Add the four types to `orchestrator/pipeline.py`**

```python
from dataclasses import dataclass
from typing import Callable, NamedTuple


class StageCallResult(NamedTuple):
    """One stage call's raw output, not yet validated against a schema model."""
    raw: dict
    prompt_tokens: int
    completion_tokens: int


class StageCallFailed(Exception):
    """Transport-level failure: network error, rate limit, timeout. Raised by a stage
    fn; never carries token counts, because the request was rejected before inference."""


@dataclass(frozen=True)
class StageFns:
    """Every LLM call in the pipeline, as a parameter. A frozen dataclass, not a dict --
    a typo'd dict key silently returns nothing and the stage gets skipped; a typo'd
    field name here is an immediate TypeError. Each callable returns a StageCallResult
    (or raises StageCallFailed). orchestrator/test_harness.py wires in scripted
    fixtures; orchestrator/stages.py (next phase) wires in real LLM calls."""
    check_consistency: Callable[..., StageCallResult]
    map_dependencies: Callable[..., StageCallResult]
    classify: Callable[..., StageCallResult]
    check_quality: Callable[..., StageCallResult]
    refine: Callable[..., StageCallResult]
    select_strategy: Callable[..., StageCallResult]
    generate_tests: Callable[..., StageCallResult]


@dataclass(frozen=True)
class HumanFns:
    """The pipeline's two human-interaction points, as parameters. Separate from
    StageFns because the source is categorically different (a person or a web request,
    not an LLM call) -- RefinerTurn/RefinerAnswer were split in the schema specifically
    so this contract works whether the caller is a CLI loop, a notebook cell, or a
    FastAPI backend (see DESIGN_NOTES.md); a blocking input() inside the orchestrator
    would discard that."""
    answer_questions: Callable[["RefinerTurn"], list["RefinerAnswer"]]
    decide_at_cap: Callable[["RequirementRunRecord"], tuple["RunOutcome", str]]
```

Add `RefinerTurn`, `RefinerAnswer`, `RequirementRunRecord`, `RunOutcome` to the `from design.schemas import (...)` at the top (as forward-reference strings above since they're only used in type hints — or just import them directly and drop the quotes; either works since `from __future__ import annotations` is already at the top of the file, which makes all annotations lazy strings automatically. Prefer importing directly and dropping the quotes, since the `from __future__ import annotations` line makes the quoting redundant.).

- [ ] **Step 4: Run the tests, confirm they pass**

Run: `python -m orchestrator.test_harness`
Expected: ends with `10 checks passed, 0 failed`

- [ ] **Step 5: Add the `Scripted` fixture helper to `orchestrator/test_harness.py`**

Every later scenario needs to script a sequence of stage-call behaviors. Add this near the top, after the shared fixtures:

```python
class Scripted:
    """A stage fn returning one scripted behavior per call, in order.

    Each behavior is either a dict (wrapped into a successful StageCallResult) or an
    Exception instance (raised as-is, e.g. StageCallFailed("429") or KeyError("oops")).
    Records every call's positional args for scenarios that need to assert on them.
    """
    def __init__(self, behaviors: list, tokens: tuple[int, int] = (10, 5)):
        self._behaviors = list(behaviors)
        self._tokens = tokens
        self.calls: list[tuple] = []

    def __call__(self, *args, **kwargs) -> StageCallResult:
        self.calls.append(args)
        behavior = self._behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        return StageCallResult(raw=behavior, prompt_tokens=self._tokens[0],
                               completion_tokens=self._tokens[1])
```
Add `from orchestrator.pipeline import StageCallResult, StageCallFailed, StageFns, HumanFns` to the imports (extending the existing `from orchestrator.pipeline import resume_at` line).

- [ ] **Step 6: Commit**

```bash
git add orchestrator/pipeline.py orchestrator/test_harness.py
git commit -m "$(cat <<'EOF'
Add StageFns, HumanFns, StageCallResult, StageCallFailed

Every LLM call and human-interaction point becomes an injectable
parameter, frozen dataclasses rather than dicts so a typo is a
TypeError, not a silently-skipped stage.
EOF
)"
```

---

## Task 6: `Throttle`

**Files:**
- Modify: `orchestrator/pipeline.py`
- Modify: `orchestrator/test_harness.py`

**Interfaces:**
- Produces: `Throttle(sleep_fn, now_fn, min_interval_seconds)` with `.wait_for_slot(model: str) -> None`. Task 7's `call_stage`/`call_document_stage` and every scenario from here on take a `Throttle` instance.

- [ ] **Step 1: Write the failing test**

```python
def test_throttle() -> None:
    """Scenario: the throttle enforces a minimum interval PER MODEL, and asserts the
    actual recorded delay -- not just that a delay happened -- since a no-op sleep_fn
    makes it easy to test that retries occur without testing that backoff is correct."""
    section("Throttle")
    from orchestrator.pipeline import Throttle

    slept: list[float] = []
    clock = [0.0]

    def fake_now():
        return datetime(2026, 1, 1, tzinfo=timezone.utc).fromtimestamp(
            clock[0], tz=timezone.utc)

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += seconds

    throttle = Throttle(sleep_fn=fake_sleep, now_fn=fake_now,
                        min_interval_seconds={"cheap-model": 10.0, "strong-model": 0.0})

    throttle.wait_for_slot("cheap-model")
    ok("first call on a model never waits", slept == [])

    clock[0] += 3.0  # only 3s elapsed, need 10s
    throttle.wait_for_slot("cheap-model")
    ok("second call within the interval sleeps exactly the remainder", slept == [7.0])

    throttle.wait_for_slot("strong-model")
    ok("a model with 0.0 min_interval never waits", slept == [7.0])

    slept.clear()
    clock[0] += 100.0  # plenty of time passed
    throttle.wait_for_slot("cheap-model")
    ok("a call after the interval has elapsed does not wait", slept == [])
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `python -m orchestrator.test_harness`
Expected: `ImportError: cannot import name 'Throttle' from 'orchestrator.pipeline'`

- [ ] **Step 3: Add `Throttle` to `orchestrator/pipeline.py`**

```python
import time
from dataclasses import field
from datetime import datetime, timezone


@dataclass
class Throttle:
    """Paces stage calls, per model, so a tight per-minute quota mostly never gets hit
    in the first place -- backoff (see call_stage) then handles the rare exception
    rather than the normal case. Not frozen, unlike StageFns/HumanFns: it owns
    last_call_at as mutable state, since something has to hold it and threading a
    separate dict through every call site is worse. sleep_fn/now_fn are injected so
    production uses time.sleep/datetime.now(timezone.utc) and tests use a no-op
    recorder and a fake clock -- deterministic, and never actually wait.

    Keyed by model, not global: RunMetadata.stages[stage].model already allows
    different stages to use different models (e.g. a cheap classifier, a stronger
    generator), and those are separate quotas -- a single global interval is either
    too slow for one or too fast for the other. No default interval is hardcoded:
    neither Gemini's nor Groq's official docs expose a static free-tier RPM number,
    both defer to a live per-account dashboard. min_interval_seconds must be filled in
    from that dashboard for a real run.
    """
    sleep_fn: Callable[[float], None] = time.sleep
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    min_interval_seconds: dict[str, float] = field(default_factory=dict)
    last_call_at: dict[str, datetime] = field(default_factory=dict, init=False)

    def wait_for_slot(self, model: str) -> None:
        interval = self.min_interval_seconds.get(model, 0.0)
        last = self.last_call_at.get(model)
        now = self.now_fn()
        if last is not None:
            elapsed = (now - last).total_seconds()
            if elapsed < interval:
                self.sleep_fn(interval - elapsed)
        self.last_call_at[model] = self.now_fn()
```

- [ ] **Step 4: Run the tests, confirm they pass**

Run: `python -m orchestrator.test_harness`
Expected: ends with `14 checks passed, 0 failed`

- [ ] **Step 5: Commit**

```bash
git add orchestrator/pipeline.py orchestrator/test_harness.py
git commit -m "feat: add Throttle for per-model call pacing"
```

---

## Task 7: `call_stage` — narrow-`except` failure handling and usage recording

**Files:**
- Modify: `orchestrator/pipeline.py`
- Modify: `orchestrator/test_harness.py`

**Interfaces:**
- Consumes: `StageFns` entries, `Throttle`, `design.schemas.{TokenUsage, FailureKind}`, any Pydantic `BaseModel` subclass as the validator.
- Produces: `StageFailed(kind, message, retry_count)` (exception), `call_stage(stage_fn, args, model_cls, stage, model_name, throttle, usage_sink, max_attempts=3, backoff_seconds=...) -> BaseModel` (raises `StageFailed` on exhaustion). This is scenario 14 (`OTHER`) and the foundation scenarios 8, 10, 12, 13 build on.

- [ ] **Step 1: Write the failing tests — covers scenario 14 (`OTHER`) directly**

```python
def test_call_stage() -> None:
    """The narrow-except wrapper: success, TRANSPORT, VALIDATION, and scenario 14 --
    OTHER, the one branch that must actually fire, not just be documented as possible."""
    section("call_stage")
    from orchestrator.pipeline import call_stage, StageFailed, Throttle
    from design.schemas import Classification, SystemType, FailureKind, TokenUsage

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)

    # -- success --
    usage: list[TokenUsage] = []
    fn = Scripted([{"requirement_id": "R1", "system_type": "web", "rationale": "r"}])
    result = call_stage(fn, ("R1",), Classification, PipelineStage.CLASSIFIER,
                        "fake-model", throttle, usage)
    ok("call_stage returns a validated model", isinstance(result, Classification))
    ok("a successful call records one usage entry", len(usage) == 1)
    ok("usage entry carries the right stage", usage[0].stage is PipelineStage.CLASSIFIER)

    # -- TRANSPORT: exhausts retries, no usage recorded --
    usage2: list[TokenUsage] = []
    fn2 = Scripted([StageCallFailed("429"), StageCallFailed("429"), StageCallFailed("429")])
    try:
        call_stage(fn2, ("R1",), Classification, PipelineStage.CLASSIFIER, "fake-model",
                  throttle, usage2, max_attempts=3, backoff_seconds=lambda a: 0.0)
        ok("TRANSPORT exhaustion raises StageFailed", False)
    except StageFailed as f:
        ok("TRANSPORT exhaustion raises StageFailed", True)
        ok("StageFailed.kind is TRANSPORT", f.kind is FailureKind.TRANSPORT)
        ok("retry_count is attempts-1", f.retry_count == 2)
    ok("no usage recorded for pure transport failures", usage2 == [])

    # -- VALIDATION: the call succeeded, tokens were spent on rejected output --
    usage3: list[TokenUsage] = []
    fn3 = Scripted([{"requirement_id": "R1"}])  # missing system_type, rationale
    try:
        call_stage(fn3, ("R1",), Classification, PipelineStage.CLASSIFIER, "fake-model",
                  throttle, usage3, max_attempts=1, backoff_seconds=lambda a: 0.0)
        ok("VALIDATION failure raises StageFailed", False)
    except StageFailed as f:
        ok("VALIDATION failure raises StageFailed", True)
        ok("StageFailed.kind is VALIDATION", f.kind is FailureKind.VALIDATION)
    ok("a validation failure still records usage (tokens were spent)", len(usage3) == 1)

    # -- Scenario 14: OTHER, and the negative it's paired with --
    usage4: list[TokenUsage] = []
    fn4 = Scripted([KeyError("unexpected")])
    try:
        call_stage(fn4, ("R1",), Classification, PipelineStage.CLASSIFIER, "fake-model",
                  throttle, usage4, max_attempts=1, backoff_seconds=lambda a: 0.0)
        ok("an unexpected exception type raises StageFailed(OTHER)", False)
    except StageFailed as f:
        ok("an unexpected exception type raises StageFailed(OTHER)", True)
        ok("StageFailed.kind is OTHER", f.kind is FailureKind.OTHER)
        ok("message names the exception class", "KeyError" in f.message)
    ok("no usage recorded for OTHER (never reached the model)", usage4 == [])

    # Negative: a bug in code CALLING call_stage (outside the guarded line) still
    # crashes, rather than being caught and filed as OTHER. call_stage itself has no
    # surrounding try/except beyond the one line that calls stage_fn -- demonstrated by
    # confirming a bug in the *test's own* orchestration crashes as a real exception:
    def broken_caller():
        result = call_stage(fn, ("R1",), Classification, PipelineStage.CLASSIFIER,
                            "fake-model", throttle, usage)
        return result.nonexistent_attribute  # AttributeError, not from inside call_stage
    try:
        broken_caller()
        ok("a caller bug (outside call_stage's guarded line) still crashes", False)
    except AttributeError:
        ok("a caller bug (outside call_stage's guarded line) still crashes", True)
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `python -m orchestrator.test_harness`
Expected: `ImportError: cannot import name 'call_stage' from 'orchestrator.pipeline'`

- [ ] **Step 3: Add `StageFailed` and `call_stage` to `orchestrator/pipeline.py`**

```python
from pydantic import BaseModel, ValidationError

from design.schemas import FailureKind, PipelineStage, TokenUsage


class StageFailed(Exception):
    """Raised by call_stage once retries are exhausted. Carries what a StageError
    needs: kind, message, and retry_count (attempts before giving up, 0 meaning it
    failed on the first try with no retry -- see StageError's own docstring)."""
    def __init__(self, kind: FailureKind, message: str, retry_count: int):
        self.kind = kind
        self.message = message
        self.retry_count = retry_count
        super().__init__(message)


def call_stage(
    stage_fn: Callable[..., StageCallResult],
    args: tuple,
    model_cls: type[BaseModel],
    stage: PipelineStage,
    model_name: str,
    throttle: Throttle,
    usage_sink: list[TokenUsage],
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
) -> BaseModel:
    """Call one stage, validate its output, retry on failure, record usage.

    Only the stage_fn call itself is wrapped in `except Exception` -- not the
    validation that follows, and not any surrounding orchestrator code. A bug in the
    orchestrator's own loop must still crash instead of being filed as kind=OTHER,
    which would otherwise be an enum member with no real producer (CLAUDE.md: "don't
    write a check that can't fire"). See design/ORCHESTRATOR_CONTRACT.md item 7 and
    the FailureKind docstring in design/schemas.py.
    """
    last_kind: FailureKind = FailureKind.OTHER
    last_message = "call_stage was invoked with max_attempts < 1"

    for attempt in range(max_attempts):
        throttle.wait_for_slot(model_name)
        try:
            result = stage_fn(*args)
        except StageCallFailed as e:
            last_kind, last_message = FailureKind.TRANSPORT, str(e)
        except Exception as e:
            last_kind, last_message = FailureKind.OTHER, f"{type(e).__name__}: {e}"
        else:
            usage_sink.append(TokenUsage(stage=stage, prompt_tokens=result.prompt_tokens,
                                         completion_tokens=result.completion_tokens))
            try:
                return model_cls.model_validate(result.raw)
            except ValidationError as e:
                last_kind, last_message = FailureKind.VALIDATION, str(e)

        if attempt < max_attempts - 1:
            throttle.sleep_fn(backoff_seconds(attempt))

    raise StageFailed(last_kind, last_message, retry_count=max_attempts - 1)
```

- [ ] **Step 4: Run the tests, confirm they pass**

Run: `python -m orchestrator.test_harness`
Expected: ends with `29 checks passed, 0 failed` (count the `ok(...)` calls added in Step 1 against the running total)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/pipeline.py orchestrator/test_harness.py
git commit -m "$(cat <<'EOF'
Add call_stage: narrow-except failure classification and usage recording

Only the stage_fn call is guarded, so an orchestrator bug still
crashes instead of being filed as kind=OTHER. Usage is recorded
whenever a call returns (success or validation failure), never on a
transport failure, which raises before inference happens.
EOF
)"
```

---

## Task 8: `call_document_stage` and document-level retry/`DEGRADED`

**Files:**
- Modify: `orchestrator/pipeline.py`
- Modify: `orchestrator/test_harness.py`

**Interfaces:**
- Consumes: `call_stage`'s pattern, `design.schemas.{DocumentStage, DocumentStageError, DocumentTokenUsage, ConsistencyReport, DependencyReport, DocumentOutcome}`.
- Produces: `call_document_stage(...) -> BaseModel` (mirrors `call_stage`, `DocumentStage`/`DocumentTokenUsage` instead), `run_document_stages(requirement_set, stage_fns, throttle, ...) -> tuple[Optional[ConsistencyReport], Optional[DependencyReport], list[DocumentStageError], list[DocumentTokenUsage]]` — covers scenario 7 (`DEGRADED`). Task 10 wires this into `run_document`; Task 11's `retry_document_stage` covers scenario 6 (retry within the same run).

- [ ] **Step 1: Write the failing tests — scenario 7**

```python
def test_document_stages_degraded() -> None:
    """Scenario 7: consistency checker and dependency mapper fail independently; the
    run continues without whichever one failed, per contract D1=b."""
    section("Scenario 7 -- DEGRADED document")
    from orchestrator.pipeline import run_document_stages, Throttle
    from design.schemas import DocumentOutcome, FailureKind

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    stage_fns = StageFns(
        check_consistency=Scripted([StageCallFailed("429"), StageCallFailed("429"),
                                    StageCallFailed("429")]),
        map_dependencies=Scripted([{"doc_id": DOC.doc_id, "dependencies": []}]),
        classify=None, check_quality=None, refine=None, select_strategy=None,
        generate_tests=None,
    )
    cons, deps, errors, usage = run_document_stages(
        DOC, STAGE_CONFIGS, stage_fns, throttle, max_attempts=3, backoff_seconds=lambda a: 0.0)
    ok("consistency checker failure leaves consistency_report None", cons is None)
    ok("dependency mapper still succeeds independently", deps is not None)
    ok("exactly one DocumentStageError recorded", len(errors) == 1)
    ok("the error names the failed stage", errors[0].stage.value == "consistency_checker")
    ok("the error's kind is TRANSPORT", errors[0].kind is FailureKind.TRANSPORT)
    ok("dependency mapper's success recorded usage", len(usage) == 1)

    outcome = DocumentOutcome.COMPLETED if cons is not None and deps is not None else DocumentOutcome.DEGRADED
    ok("both-independent-failures-considered outcome is DEGRADED", outcome is DocumentOutcome.DEGRADED)
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `python -m orchestrator.test_harness`
Expected: `ImportError: cannot import name 'run_document_stages' from 'orchestrator.pipeline'`

- [ ] **Step 3: Add `call_document_stage` and `run_document_stages` to `orchestrator/pipeline.py`**

```python
from design.schemas import (
    ConsistencyReport, DependencyReport, DocumentStage, DocumentStageError,
    DocumentTokenUsage, RequirementSet, StageConfig,
)


def call_document_stage(
    stage_fn: Callable[..., StageCallResult],
    args: tuple,
    model_cls: type[BaseModel],
    stage: DocumentStage,
    model_name: str,
    throttle: Throttle,
    usage_sink: list[DocumentTokenUsage],
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
) -> BaseModel:
    """Structurally identical to call_stage apart from the stage/usage types -- same
    reasoning as the StageError/DocumentStageError split: a shared implementation
    parameterised by a PipelineStage | DocumentStage union would let a call meant for
    one level accidentally target the other."""
    last_kind: FailureKind = FailureKind.OTHER
    last_message = "call_document_stage was invoked with max_attempts < 1"

    for attempt in range(max_attempts):
        throttle.wait_for_slot(model_name)
        try:
            result = stage_fn(*args)
        except StageCallFailed as e:
            last_kind, last_message = FailureKind.TRANSPORT, str(e)
        except Exception as e:
            last_kind, last_message = FailureKind.OTHER, f"{type(e).__name__}: {e}"
        else:
            usage_sink.append(DocumentTokenUsage(stage=stage, prompt_tokens=result.prompt_tokens,
                                                 completion_tokens=result.completion_tokens))
            try:
                return model_cls.model_validate(result.raw)
            except ValidationError as e:
                last_kind, last_message = FailureKind.VALIDATION, str(e)

        if attempt < max_attempts - 1:
            throttle.sleep_fn(backoff_seconds(attempt))

    raise StageFailed(last_kind, last_message, retry_count=max_attempts - 1)


def run_document_stages(
    requirement_set: RequirementSet,
    stage_configs: dict[str, StageConfig],
    stage_fns: StageFns,
    throttle: Throttle,
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
) -> tuple[Optional[ConsistencyReport], Optional[DependencyReport],
           list[DocumentStageError], list[DocumentTokenUsage]]:
    """Runs the two document-level stages independently -- one failing must not stop
    the other from running (contract item 8, D1=b)."""
    errors: list[DocumentStageError] = []
    usage: list[DocumentTokenUsage] = []

    consistency_report: Optional[ConsistencyReport] = None
    try:
        consistency_report = call_document_stage(
            stage_fns.check_consistency, (requirement_set,), ConsistencyReport,
            DocumentStage.CONSISTENCY_CHECKER,
            stage_configs[DocumentStage.CONSISTENCY_CHECKER.value].model, throttle, usage,
            max_attempts, backoff_seconds)
    except StageFailed as f:
        errors.append(DocumentStageError(stage=DocumentStage.CONSISTENCY_CHECKER, kind=f.kind,
                                         message=f.message, retry_count=f.retry_count))

    dependency_report: Optional[DependencyReport] = None
    try:
        dependency_report = call_document_stage(
            stage_fns.map_dependencies, (requirement_set,), DependencyReport,
            DocumentStage.DEPENDENCY_MAPPER,
            stage_configs[DocumentStage.DEPENDENCY_MAPPER.value].model, throttle, usage,
            max_attempts, backoff_seconds)
    except StageFailed as f:
        errors.append(DocumentStageError(stage=DocumentStage.DEPENDENCY_MAPPER, kind=f.kind,
                                         message=f.message, retry_count=f.retry_count))

    return consistency_report, dependency_report, errors, usage
```

- [ ] **Step 4: Run the tests, confirm they pass**

Run: `python -m orchestrator.test_harness`
Expected: ends with `35 checks passed, 0 failed`

- [ ] **Step 5: Commit**

```bash
git add orchestrator/pipeline.py orchestrator/test_harness.py
git commit -m "feat: add call_document_stage and run_document_stages (DEGRADED policy)"
```

---

## Task 9: On-disk layout (D2b)

**Files:**
- Modify: `orchestrator/pipeline.py`
- Modify: `orchestrator/test_harness.py`

**Interfaces:**
- Consumes: `design.schemas.{DocumentRunRecord, RequirementRunRecord}`, `pathlib.Path`.
- Produces: `write_document_run(run_dir, record)`, `write_requirement_run(run_dir, record)`, `read_document_run(run_dir) -> DocumentRunRecord`. Task 10 uses these inside `run_document`; Task 11's `resume_document`/`retry_document_stage` reload through `read_document_run`.

- [ ] **Step 1: Write the failing test**

```python
def test_on_disk_round_trip(tmp_path) -> None:
    """Contract items 9 and 10: document.json is written with requirement_records=[],
    each requirement gets its own file, and everything re-validates before persisting."""
    section("On-disk layout round trip")
    from orchestrator.pipeline import write_document_run, write_requirement_run, read_document_run
    from design.schemas import DocumentOutcome, RunOutcome

    metadata = make_metadata()
    record = DocumentRunRecord(requirement_set=DOC, metadata=metadata,
                               outcome=DocumentOutcome.IN_PROGRESS)
    write_document_run(tmp_path, record)
    ok("document.json was written", (tmp_path / "document.json").exists())

    on_disk_raw = (tmp_path / "document.json").read_text()
    ok("requirement_records is empty on disk",
       '"requirement_records": []' in on_disk_raw)

    req_record = rec(requirement=REQ_A, outcome=RunOutcome.IN_PROGRESS)
    write_requirement_run(tmp_path, req_record)
    ok("the requirement file was written",
       (tmp_path / "requirements" / f"{REQ_A.id}.json").exists())

    reloaded = read_document_run(tmp_path)
    ok("reloaded document keeps its metadata", reloaded.metadata.run_id == metadata.run_id)
    ok("reloaded document reassembles requirement_records",
       [r.requirement.id for r in reloaded.requirement_records] == [REQ_A.id])
```

`orchestrator/test_harness.py`'s plain-script runner has no `tmp_path` fixture (that's a pytest-ism) — use the standard library instead. Change the test's signature and add a `tempfile` import:

```python
import tempfile
from pathlib import Path

def test_on_disk_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # ... body exactly as above, using tmp_path ...
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `python -m orchestrator.test_harness`
Expected: `ImportError: cannot import name 'write_document_run' from 'orchestrator.pipeline'`

- [ ] **Step 3: Add the three functions to `orchestrator/pipeline.py`**

```python
from pathlib import Path


def write_document_run(run_dir: Path, record: DocumentRunRecord) -> None:
    """Writes document.json with an EMPTY requirement_records list (decision D2b) --
    each requirement is its own file, written separately by write_requirement_run.
    Re-validates before persisting (contract item 10): mutation after construction
    bypasses Pydantic's checks, so this re-runs them right before the bytes hit disk."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "requirements").mkdir(exist_ok=True)
    on_disk = DocumentRunRecord.model_validate(
        {**record.model_dump(mode="json"), "requirement_records": []})
    (run_dir / "document.json").write_text(on_disk.model_dump_json(indent=2))


def write_requirement_run(run_dir: Path, record: RequirementRunRecord) -> None:
    (run_dir / "requirements").mkdir(parents=True, exist_ok=True)
    validated = RequirementRunRecord.model_validate(record.model_dump(mode="json"))
    (run_dir / "requirements" / f"{record.requirement.id}.json").write_text(
        validated.model_dump_json(indent=2))


def read_document_run(run_dir: Path) -> DocumentRunRecord:
    """Reassembles the document from document.json (empty requirement_records) plus
    every requirements/*.json file -- the inverse of write_document_run/
    write_requirement_run under D2b."""
    doc_data = json.loads((run_dir / "document.json").read_text())
    req_dir = run_dir / "requirements"
    records = [RequirementRunRecord.model_validate_json(path.read_text())
               for path in sorted(req_dir.glob("*.json"))]
    return DocumentRunRecord.model_validate(
        {**doc_data, "requirement_records": [r.model_dump(mode="json") for r in records]})
```
Add `import json` at the top of the file, and `RequirementRunRecord`, `DocumentRunRecord` to the `from design.schemas import (...)` block.

- [ ] **Step 4: Run the tests, confirm they pass**

Run: `python -m orchestrator.test_harness`
Expected: ends with `40 checks passed, 0 failed`

- [ ] **Step 5: Commit**

```bash
git add orchestrator/pipeline.py orchestrator/test_harness.py
git commit -m "feat: add on-disk layout (D2b) — write_document_run, write_requirement_run, read_document_run"
```

---

## Task 10: `run_requirement` — the per-requirement pipeline

This is the largest task: the quality-check/refine loop with issue-id reuse, suppression carry-forward, and the revision cap. Covers scenarios 1 (happy path, including the contract item 2 assertion), 2 (revision cap, both branches), 3 (issue identity reuse), and 4 (suppression persists).

**Files:**
- Modify: `orchestrator/pipeline.py`
- Modify: `orchestrator/test_harness.py`

**Interfaces:**
- Consumes: `call_stage`, `StageFns`, `HumanFns`, `Throttle`, `resume_at`, everything from `design.schemas` used in the refine loop.
- Produces: `run_requirement(record, requirement_set, consistency_report, dependency_report, stage_fns, human_fns, throttle, max_revisions, stage_configs, max_attempts=3, backoff_seconds=...) -> RequirementRunRecord`. Task 11's `run_document`/`resume_document` call this once per pending requirement.

- [ ] **Step 1: Write the failing test for scenario 1 (happy path)**

```python
def test_happy_path() -> None:
    """Scenario 1: one clean requirement, one refined-once requirement, straight
    through all 7 stages. Explicitly asserts contract item 2: the Requirement handed
    to stage 3/4 has .text equal to the original (clean) or refined_text (refined) --
    never read from record.final_text."""
    section("Scenario 1 -- happy path, both paths converge (contract item 2)")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import RunOutcome

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    strategy_calls = []
    generate_calls = []

    def select_strategy(req, *a):
        strategy_calls.append(req.text)
        return StageCallResult(raw={"requirement_id": req.id, "system_type": "other",
                                    "techniques": ["boundary_value_analysis"], "rationale": "r"},
                               prompt_tokens=10, completion_tokens=5)

    def generate_tests(req, *a):
        generate_calls.append(req.text)
        return StageCallResult(raw={"requirement_id": req.id, "test_cases": [{
            "id": f"TC-{req.id}-1", "requirement_ids": [req.id],
            "technique_used": "boundary_value_analysis", "title": "t", "steps": ["s"],
            "expected_result": "e"}]}, prompt_tokens=10, completion_tokens=5)

    # -- Clean path: passes on the first quality check --
    clean_fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([{"requirement_id": REQ_A.id, "passed": True, "issues": []}]),
        refine=None, select_strategy=select_strategy, generate_tests=generate_tests)
    human_fns = HumanFns(answer_questions=lambda turn: [], decide_at_cap=lambda rec: (None, None))
    clean_record = run_requirement(
        rec(requirement=REQ_A), DOC, None, None, clean_fns, human_fns, throttle,
        max_revisions=3, stage_configs=STAGE_CONFIGS)
    ok("clean path completes", clean_record.outcome is RunOutcome.COMPLETED)
    ok("clean path never refined", clean_record.final_requirement is None)
    ok("stage 3/4 saw the original text (contract item 2)",
       strategy_calls == [REQ_A.text] and generate_calls == [REQ_A.text])

    # -- Refined path: fails once, one round of Q&A, then passes --
    refined_text = "Temperatures within the valid range defined in DOC-REQ-B shall be output."
    strategy_calls.clear(); generate_calls.clear()
    refined_fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([
            {"requirement_id": REQ_A.id, "passed": False, "issues": [{
                "id": "I1", "category": "vague_pronoun", "span": "these limits",
                "explanation": "Unresolved referent."}]},
            {"requirement_id": REQ_A.id, "passed": True, "issues": []},
        ]),
        refine=Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
                "id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun",
                "question_text": "What limits?"}]},
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text,
             "refined_text": refined_text, "revision_number": 1,
             "answers_used": [{"question_id": "Q1", "answer_text": "DOC-REQ-B's range."}]},
        ]),
        select_strategy=select_strategy, generate_tests=generate_tests)
    human_fns2 = HumanFns(
        answer_questions=lambda turn: [RefinerAnswer(question_id="Q1", answer_text="DOC-REQ-B's range.")],
        decide_at_cap=lambda rec: (None, None))
    refined_record = run_requirement(
        rec(requirement=REQ_A), DOC, None, None, refined_fns, human_fns2, throttle,
        max_revisions=3, stage_configs=STAGE_CONFIGS)
    ok("refined path completes", refined_record.outcome is RunOutcome.COMPLETED)
    ok("refined path recorded exactly one rewrite",
       refined_record.final_requirement is not None
       and refined_record.final_requirement.refined_text == refined_text)
    ok("stage 3/4 saw the refined text, not the original (contract item 2)",
       strategy_calls == [refined_text] and generate_calls == [refined_text])
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `python -m orchestrator.test_harness`
Expected: `ImportError: cannot import name 'run_requirement' from 'orchestrator.pipeline'`

- [ ] **Step 3: Implement the issue-id reconciliation and confirmation helpers**

Add to `orchestrator/pipeline.py`:

```python
from design.schemas import (
    Classification, Issue, QualityReport, RefinedRequirement, RefinementRound,
    RefinerAnswer, RefinerTurn, Requirement, RequirementRunRecord, TestPlan, TestStrategy,
)


def _reconcile_issue_ids(new_issues: list[Issue], previous_round: Optional[RefinementRound]) -> list[Issue]:
    """Matches this round's issues against the previous round's on (category, span) and
    reuses the id when it's the same defect -- the orchestrator's job per contract item
    4, not the LLM's: each round's QualityReport is a fresh call minting its own ids."""
    if previous_round is None:
        return new_issues
    available = {(i.category, i.span): i.id for i in previous_round.quality_report.issues}
    used_ids: set[str] = set()
    reconciled = []
    for issue in new_issues:
        reused_id = available.get((issue.category, issue.span))
        if reused_id is not None and reused_id not in used_ids:
            used_ids.add(reused_id)
            reconciled.append(issue.model_copy(update={"id": reused_id}))
        else:
            reconciled.append(issue)
    return reconciled


def _confirmed_issue_ids(rounds: list[RefinementRound]) -> set[str]:
    """Every issue id the human has ever confirmed resolved (user_confirms_resolved),
    recomputed from the rounds already on the record -- same shape as schemas.py's own
    _issue_identity_is_stable validator, so resuming mid-refinement doesn't need to
    track this separately from what's already persisted."""
    confirmed: set[str] = set()
    for rnd in rounds:
        if rnd.turn is None:
            continue
        issue_of = {q.id: q.issue_id for q in rnd.turn.questions}
        for ans in rnd.answers:
            if ans.user_confirms_resolved:
                confirmed.add(issue_of[ans.question_id])
    return confirmed
```

- [ ] **Step 4: Implement the refine loop**

```python
def _run_refine_loop(
    record: RequirementRunRecord,
    stage_fns: StageFns,
    throttle: Throttle,
    max_revisions: int,
    stage_configs: dict,
    max_attempts: int,
    backoff_seconds: Callable[[int], float],
) -> tuple[RequirementRunRecord, Optional["StageError"]]:
    """Runs quality-check/refine rounds until one passes, the cap is hit, or a stage
    call fails outright. Returns the updated record and, on failure, the StageError to
    append -- the caller sets outcome=ERROR, since only it knows the full error list.
    """
    from design.schemas import StageError
    req = record.requirement
    rounds = list(record.rounds)

    # Resuming mid-round: the last round already has a quality_report but no rewrite
    # yet (REFINER position) -- pick up from there instead of starting a new round.
    pending_round = None
    if rounds and not rounds[-1].quality_report.passed and rounds[-1].rewrite is None:
        pending_round = rounds[-1]
        rounds = rounds[:-1]

    while True:
        if pending_round is not None:
            n = pending_round.revision_number
            text_checked = pending_round.text_checked
            quality_report = pending_round.quality_report
            suppressed_ids = pending_round.suppressed_issue_ids
            turn = pending_round.turn
            answers = pending_round.answers
        else:
            n = len(rounds) + 1
            text_checked = rounds[-1].rewrite.refined_text if rounds else req.text
            suppressed_ids = sorted(_confirmed_issue_ids(rounds))
            current = Requirement(id=req.id, text=text_checked, source_doc_id=req.source_doc_id)
            usage = list(record.usage)
            try:
                raw_report = call_stage(
                    stage_fns.check_quality, (current, record.classification, suppressed_ids),
                    QualityReport, PipelineStage.QUALITY_CHECKER,
                    stage_configs[PipelineStage.QUALITY_CHECKER.value].model, throttle, usage,
                    max_attempts, backoff_seconds)
            except StageFailed as f:
                record = record.model_copy(update={"rounds": rounds, "usage": usage})
                return record, StageError(stage=PipelineStage.QUALITY_CHECKER, kind=f.kind,
                                          message=f.message, retry_count=f.retry_count)
            record = record.model_copy(update={"usage": usage})
            reconciled = _reconcile_issue_ids(raw_report.issues, rounds[-1] if rounds else None)
            quality_report = QualityReport(requirement_id=req.id, passed=raw_report.passed,
                                           issues=reconciled)
            turn, answers = None, []

        if quality_report.passed:
            rounds.append(RefinementRound(revision_number=n, text_checked=text_checked,
                                          quality_report=quality_report,
                                          suppressed_issue_ids=suppressed_ids))
            return record.model_copy(update={"rounds": rounds}), None

        if n >= max_revisions:
            # Cap reached this round: record what we have (turn/answers if we got that
            # far while resuming, otherwise none yet) and stop -- the caller decides.
            rounds.append(RefinementRound(revision_number=n, text_checked=text_checked,
                                          quality_report=quality_report, turn=turn,
                                          answers=answers, suppressed_issue_ids=suppressed_ids))
            return record.model_copy(update={"rounds": rounds}), None

        current = Requirement(id=req.id, text=text_checked, source_doc_id=req.source_doc_id)
        usage = list(record.usage)
        if turn is None:
            try:
                turn = call_stage(
                    stage_fns.refine, (current, quality_report), RefinerTurn,
                    PipelineStage.REFINER, stage_configs[PipelineStage.REFINER.value].model,
                    throttle, usage, max_attempts, backoff_seconds)
            except StageFailed as f:
                record = record.model_copy(update={"usage": usage})
                rounds.append(RefinementRound(revision_number=n, text_checked=text_checked,
                                              quality_report=quality_report,
                                              suppressed_issue_ids=suppressed_ids))
                return record.model_copy(update={"rounds": rounds}), StageError(
                    stage=PipelineStage.REFINER, kind=f.kind, message=f.message,
                    retry_count=f.retry_count)
            record = record.model_copy(update={"usage": usage})
            answers = human_fns_answer(stage_fns, turn)  # see Step 5 note below

        usage = list(record.usage)
        try:
            rewrite = call_stage(
                stage_fns.refine, (current, answers), RefinedRequirement,
                PipelineStage.REFINER, stage_configs[PipelineStage.REFINER.value].model,
                throttle, usage, max_attempts, backoff_seconds)
        except StageFailed as f:
            record = record.model_copy(update={"usage": usage})
            rounds.append(RefinementRound(revision_number=n, text_checked=text_checked,
                                          quality_report=quality_report, turn=turn,
                                          answers=answers, suppressed_issue_ids=suppressed_ids))
            return record.model_copy(update={"rounds": rounds}), StageError(
                stage=PipelineStage.REFINER, kind=f.kind, message=f.message,
                retry_count=f.retry_count)
        record = record.model_copy(update={"usage": usage})

        rounds.append(RefinementRound(revision_number=n, text_checked=text_checked,
                                      quality_report=quality_report, turn=turn,
                                      answers=answers, rewrite=rewrite,
                                      suppressed_issue_ids=suppressed_ids))
        pending_round = None
```

`human_fns_answer` above is a naming slip to fix immediately: `_run_refine_loop` needs `human_fns` passed in, not read off `stage_fns`. Correct the signature and the one call site:

```python
def _run_refine_loop(
    record: RequirementRunRecord,
    stage_fns: StageFns,
    human_fns: HumanFns,
    throttle: Throttle,
    max_revisions: int,
    stage_configs: dict,
    max_attempts: int,
    backoff_seconds: Callable[[int], float],
) -> tuple[RequirementRunRecord, Optional["StageError"]]:
```
and replace `answers = human_fns_answer(stage_fns, turn)` with `answers = human_fns.answer_questions(turn)`.

- [ ] **Step 5: Implement `run_requirement`, wiring in classifier, refine loop, cap decision, strategy, and generation**

```python
def run_requirement(
    record: RequirementRunRecord,
    requirement_set: RequirementSet,
    consistency_report,
    dependency_report,
    stage_fns: StageFns,
    human_fns: HumanFns,
    throttle: Throttle,
    max_revisions: int,
    stage_configs: dict,
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
) -> RequirementRunRecord:
    from design.schemas import StageError

    stage = resume_at(record)
    if stage is None:
        return record

    req = record.requirement

    if stage is PipelineStage.CLASSIFIER:
        usage = list(record.usage)
        try:
            classification = call_stage(
                stage_fns.classify, (req, requirement_set), Classification,
                PipelineStage.CLASSIFIER, stage_configs[PipelineStage.CLASSIFIER.value].model,
                throttle, usage, max_attempts, backoff_seconds)
        except StageFailed as f:
            errors = list(record.errors) + [StageError(
                stage=PipelineStage.CLASSIFIER, kind=f.kind, message=f.message,
                retry_count=f.retry_count)]
            return RequirementRunRecord.model_validate({
                **record.model_dump(mode="json"), "outcome": "error",
                "errors": [e.model_dump(mode="json") for e in errors],
                "usage": [u.model_dump(mode="json") for u in usage]})
        record = record.model_copy(update={"classification": classification, "usage": usage})

    record, refine_error = _run_refine_loop(
        record, stage_fns, human_fns, throttle, max_revisions, stage_configs,
        max_attempts, backoff_seconds)
    if refine_error is not None:
        errors = list(record.errors) + [refine_error]
        return RequirementRunRecord.model_validate(
            {**record.model_dump(mode="json"), "outcome": "error",
             "errors": [e.model_dump(mode="json") for e in errors]})

    last_round = record.rounds[-1]
    if not last_round.quality_report.passed:
        # The cap fired: ask the human whether to generate anyway or stop.
        outcome, cap_reason = human_fns.decide_at_cap(record)
        if outcome not in (RunOutcome.CAP_GENERATED, RunOutcome.CAP_STOPPED):
            raise ValueError(
                f"decide_at_cap returned {outcome!r}, must be CAP_GENERATED or CAP_STOPPED")
        if outcome is RunOutcome.CAP_STOPPED:
            return RequirementRunRecord.model_validate(
                {**record.model_dump(mode="json"), "outcome": outcome.value,
                 "cap_reason": cap_reason})
        record = record.model_copy(update={"cap_reason": cap_reason})
        final_outcome = RunOutcome.CAP_GENERATED
    else:
        final_outcome = RunOutcome.COMPLETED

    current = Requirement(id=req.id, text=record.final_text, source_doc_id=req.source_doc_id)

    usage = list(record.usage)
    try:
        strategy = call_stage(
            stage_fns.select_strategy, (current, record.classification), TestStrategy,
            PipelineStage.STRATEGY_SELECTOR,
            stage_configs[PipelineStage.STRATEGY_SELECTOR.value].model, throttle, usage,
            max_attempts, backoff_seconds)
    except StageFailed as f:
        errors = list(record.errors) + [StageError(
            stage=PipelineStage.STRATEGY_SELECTOR, kind=f.kind, message=f.message,
            retry_count=f.retry_count)]
        return RequirementRunRecord.model_validate(
            {**record.model_dump(mode="json"), "outcome": "error",
             "errors": [e.model_dump(mode="json") for e in errors],
             "usage": [u.model_dump(mode="json") for u in usage]})
    record = record.model_copy(update={"test_strategy": strategy, "usage": usage})

    usage = list(record.usage)
    try:
        plan = call_stage(
            stage_fns.generate_tests, (current, strategy), TestPlan,
            PipelineStage.TEST_GENERATOR, stage_configs[PipelineStage.TEST_GENERATOR.value].model,
            throttle, usage, max_attempts, backoff_seconds)
    except StageFailed as f:
        errors = list(record.errors) + [StageError(
            stage=PipelineStage.TEST_GENERATOR, kind=f.kind, message=f.message,
            retry_count=f.retry_count)]
        return RequirementRunRecord.model_validate(
            {**record.model_dump(mode="json"), "outcome": "error",
             "errors": [e.model_dump(mode="json") for e in errors],
             "usage": [u.model_dump(mode="json") for u in usage]})
    record = record.model_copy(update={"test_plan": plan, "usage": usage})

    return RequirementRunRecord.model_validate(
        {**record.model_dump(mode="json"), "outcome": final_outcome.value})
```

Add `RunOutcome` to the imports if not already present.

- [ ] **Step 6: Run the happy-path test, confirm it passes**

Run: `python -m orchestrator.test_harness`
Expected: ends with `47 checks passed, 0 failed`

- [ ] **Step 7: Write and pass the remaining scenario 2, 3, 4 tests**

Add scenario 2 (revision cap, both branches):

```python
def test_revision_cap() -> None:
    """Scenario 2: quality check fails every round up to the cap; decide_at_cap is
    invoked; both CAP_GENERATED and CAP_STOPPED produce the matching outcome."""
    section("Scenario 2 -- revision cap")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import RunOutcome

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)

    def always_fails_quality():
        return Scripted([{"requirement_id": REQ_A.id, "passed": False, "issues": [{
            "id": "I1", "category": "vague_pronoun", "span": "these limits",
            "explanation": "Unresolved."}]}] * 5)

    def refine_forever():
        return Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
                "id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun",
                "question_text": "?"}]},
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text, "refined_text": T1,
             "revision_number": 1,
             "answers_used": [{"question_id": "Q1", "answer_text": "a"}]},
        ] * 5)

    for decision, expected in ((RunOutcome.CAP_GENERATED, RunOutcome.CAP_GENERATED),
                               (RunOutcome.CAP_STOPPED, RunOutcome.CAP_STOPPED)):
        fns = StageFns(
            check_consistency=None, map_dependencies=None,
            classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
            check_quality=always_fails_quality(), refine=refine_forever(),
            select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                       "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
            generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
                "id": "TC-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
                "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
        human_fns = HumanFns(
            answer_questions=lambda turn: [RefinerAnswer(question_id="Q1", answer_text="a")],
            decide_at_cap=lambda rec, d=decision: (d, f"cap reached, chose {d.value}"))
        result = run_requirement(rec(requirement=REQ_A), DOC, None, None, fns, human_fns,
                                 throttle, max_revisions=2, stage_configs=STAGE_CONFIGS)
        ok(f"cap decision {decision.value} produces outcome {expected.value}",
           result.outcome is expected)
        ok(f"{decision.value} record has a cap_reason", result.cap_reason is not None)

    ok("decide_at_cap returning a nonsense outcome raises ValueError",
       _cap_returns_nonsense_raises())


def _cap_returns_nonsense_raises() -> bool:
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import RunOutcome
    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([{"requirement_id": REQ_A.id, "passed": False, "issues": [{
            "id": "I1", "category": "vague_pronoun", "span": "x", "explanation": "e"}]}] * 3),
        refine=Scripted([{"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
            "id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun", "question_text": "?"}]}] * 3),
        select_strategy=None, generate_tests=None)
    human_fns = HumanFns(answer_questions=lambda turn: [],
                         decide_at_cap=lambda rec: (RunOutcome.COMPLETED, "nonsense"))
    try:
        run_requirement(rec(requirement=REQ_A), DOC, None, None, fns, human_fns, throttle,
                        max_revisions=1, stage_configs=STAGE_CONFIGS)
        return False
    except ValueError:
        return True
```

Add scenario 3 (issue identity reuse):

```python
def test_issue_identity_reuse() -> None:
    """Scenario 3: two rounds produce an issue at the same (category, span); the
    orchestrator reuses the same Issue.id, per contract item 4."""
    section("Scenario 3 -- issue identity across rounds")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import RunOutcome

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([
            {"requirement_id": REQ_A.id, "passed": False, "issues": [
                {"id": "FRESH-1", "category": "vague_pronoun", "span": "these limits",
                 "explanation": "e1"}]},
            {"requirement_id": REQ_A.id, "passed": False, "issues": [
                {"id": "FRESH-2", "category": "vague_pronoun", "span": "these limits",
                 "explanation": "e1, still there"}]},
            {"requirement_id": REQ_A.id, "passed": True, "issues": []},
        ]),
        refine=Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [{
                "id": "Q1", "issue_id": "FRESH-1", "issue_category": "vague_pronoun",
                "question_text": "?"}]},
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text, "refined_text": T1,
             "revision_number": 1, "answers_used": [{"question_id": "Q1", "answer_text": "a"}]},
            {"requirement_id": REQ_A.id, "revision_number": 2, "questions": [{
                "id": "Q2", "issue_id": "FRESH-2", "issue_category": "vague_pronoun",
                "question_text": "?"}]},
            {"requirement_id": REQ_A.id, "original_text": T1, "refined_text": T1 + " ",
             "revision_number": 2, "answers_used": [{"question_id": "Q2", "answer_text": "a"}]},
        ]),
        select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                   "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
        generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
            "id": "TC-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
            "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
    human_fns = HumanFns(
        answer_questions=lambda turn: [RefinerAnswer(question_id=turn.questions[0].id, answer_text="a")],
        decide_at_cap=lambda rec: (RunOutcome.CAP_STOPPED, "n/a"))
    result = run_requirement(rec(requirement=REQ_A), DOC, None, None, fns, human_fns, throttle,
                             max_revisions=3, stage_configs=STAGE_CONFIGS)
    ids_seen = [r.quality_report.issues[0].id for r in result.rounds if r.quality_report.issues]
    ok("the LLM's fresh ids were replaced with the reused id",
       ids_seen == ["FRESH-1", "FRESH-1"])
```

Add scenario 4 (suppression persists):

```python
def test_suppression_persists() -> None:
    """Scenario 4: user_confirms_resolved=True on one round's answer is present in
    suppressed_issue_ids on every LATER round, not just the next one."""
    section("Scenario 4 -- suppression persists across rounds")
    from orchestrator.pipeline import run_requirement, Throttle
    from design.schemas import RunOutcome

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    fns = StageFns(
        check_consistency=None, map_dependencies=None,
        classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
        check_quality=Scripted([
            {"requirement_id": REQ_A.id, "passed": False, "issues": [
                {"id": "I1", "category": "vague_pronoun", "span": "these limits", "explanation": "e"},
                {"id": "I2", "category": "non_verifiable", "span": "subsequent processing",
                 "explanation": "e2"}]},
            # I1 suppressed by the human after round 1; only I2 should still show up.
            {"requirement_id": REQ_A.id, "passed": False, "issues": [
                {"id": "I2b", "category": "non_verifiable", "span": "subsequent processing",
                 "explanation": "e2 still there"}]},
            {"requirement_id": REQ_A.id, "passed": True, "issues": []},
        ]),
        refine=Scripted([
            {"requirement_id": REQ_A.id, "revision_number": 1, "questions": [
                {"id": "Q1", "issue_id": "I1", "issue_category": "vague_pronoun", "question_text": "?"},
                {"id": "Q2", "issue_id": "I2", "issue_category": "non_verifiable", "question_text": "?"}]},
            {"requirement_id": REQ_A.id, "original_text": REQ_A.text, "refined_text": T1,
             "revision_number": 1, "answers_used": [
                 {"question_id": "Q1", "answer_text": "confirmed fine", "user_confirms_resolved": True},
                 {"question_id": "Q2", "answer_text": "still working on it"}]},
            {"requirement_id": REQ_A.id, "revision_number": 2, "questions": [
                {"id": "Q3", "issue_id": "I2", "issue_category": "non_verifiable", "question_text": "?"}]},
            {"requirement_id": REQ_A.id, "original_text": T1, "refined_text": T1 + " ",
             "revision_number": 2, "answers_used": [{"question_id": "Q3", "answer_text": "a"}]},
        ]),
        select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                   "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
        generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
            "id": "TC-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
            "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
    human_fns = HumanFns(
        answer_questions=lambda turn: [
            RefinerAnswer(question_id=q.id, answer_text="confirmed fine" if q.issue_id == "I1" else "a",
                         user_confirms_resolved=(q.issue_id == "I1"))
            for q in turn.questions],
        decide_at_cap=lambda rec: (RunOutcome.CAP_STOPPED, "n/a"))
    result = run_requirement(rec(requirement=REQ_A), DOC, None, None, fns, human_fns, throttle,
                             max_revisions=3, stage_configs=STAGE_CONFIGS)
    ok("round 2 suppresses I1", "I1" in result.rounds[1].suppressed_issue_ids)
    ok("round 3 (final passing round) still suppresses I1",
       "I1" in result.rounds[2].suppressed_issue_ids)
```

Add all four new test functions (`test_revision_cap`, `test_issue_identity_reuse`, `test_suppression_persists`, plus `test_happy_path` from Step 1) to `main()`'s tuple.

- [ ] **Step 8: Run the full harness, confirm everything passes**

Run: `python -m orchestrator.test_harness`
Expected: ends with `N checks passed, 0 failed`, `N` = Step 6's count + the new checks from Step 7 (count the `ok(...)` calls added)

- [ ] **Step 9: Commit**

```bash
git add orchestrator/pipeline.py orchestrator/test_harness.py
git commit -m "$(cat <<'EOF'
Add run_requirement: the per-requirement pipeline

Classifier through test generation, with the quality-check/refine
loop doing issue-id reuse (contract item 4) and suppression
carry-forward (item 5), the revision cap with a runtime-checked human
decision (item 3), and stages 3/4 always fed a plain Requirement
whose text equals final_text on either path (item 2, gap 1).

Covers harness scenarios 1-4.
EOF
)"
```

---

## Task 11: `run_document` — wiring document-level and per-requirement stages together

**Files:**
- Modify: `orchestrator/pipeline.py`
- Modify: `orchestrator/test_harness.py`

**Interfaces:**
- Consumes: `run_document_stages` (Task 8), `run_requirement` (Task 10), `write_document_run`/`write_requirement_run` (Task 9).
- Produces: `run_document(requirement_set, metadata, stage_fns, human_fns, throttle, max_revisions, run_dir=None, max_attempts=3, backoff_seconds=...) -> DocumentRunRecord`. Covers scenario 11 (prompt provenance, essentially a fixture-completeness check) and is the entry point Task 12's resume/retry functions build on.

- [ ] **Step 1: Write the failing test**

```python
def test_run_document_happy_path() -> None:
    """run_document wires the document-level stages and both requirements together.
    Also scenario 11: every stage in RunMetadata.stages carries a prompt_hash from
    prompt_fingerprint -- a property of the fixture, verified here rather than assumed."""
    section("run_document -- full document, scenario 11 (prompt provenance)")
    from orchestrator.pipeline import run_document, Throttle
    from design.schemas import DocumentOutcome, ALL_STAGES

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)

    def classification_for(req_id):
        return {"requirement_id": req_id, "system_type": "other", "rationale": "r"}

    def passing_quality(req_id):
        return {"requirement_id": req_id, "passed": True, "issues": []}

    def strategy_for(req_id):
        return {"requirement_id": req_id, "system_type": "other",
                "techniques": ["boundary_value_analysis"], "rationale": "r"}

    def plan_for(req_id):
        return {"requirement_id": req_id, "test_cases": [{
            "id": f"TC-{req_id}-1", "requirement_ids": [req_id],
            "technique_used": "boundary_value_analysis", "title": "t", "steps": ["s"],
            "expected_result": "e"}]}

    fns = StageFns(
        check_consistency=Scripted([{"doc_id": DOC.doc_id, "conflicts": []}]),
        map_dependencies=Scripted([{"doc_id": DOC.doc_id, "dependencies": []}]),
        classify=Scripted([classification_for(REQ_A.id), classification_for(REQ_B.id)]),
        check_quality=Scripted([passing_quality(REQ_A.id), passing_quality(REQ_B.id)]),
        refine=None,
        select_strategy=Scripted([strategy_for(REQ_A.id), strategy_for(REQ_B.id)]),
        generate_tests=Scripted([plan_for(REQ_A.id), plan_for(REQ_B.id)]))
    human_fns = HumanFns(answer_questions=lambda turn: [], decide_at_cap=lambda rec: (None, None))
    metadata = make_metadata()

    result = run_document(DOC, metadata, fns, human_fns, throttle, max_revisions=3)
    ok("document outcome is COMPLETED", result.outcome.value == "completed")
    ok("both requirements completed",
       all(r.outcome.value == "completed" for r in result.requirement_records))
    ok("every stage in metadata.stages carries a prompt_hash (scenario 11)",
       all(metadata.stages[s].prompt_hash for s in ALL_STAGES))
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `python -m orchestrator.test_harness`
Expected: `ImportError: cannot import name 'run_document' from 'orchestrator.pipeline'`

- [ ] **Step 3: Add `run_document` to `orchestrator/pipeline.py`**

```python
def run_document(
    requirement_set: RequirementSet,
    metadata: RunMetadata,
    stage_fns: StageFns,
    human_fns: HumanFns,
    throttle: Throttle,
    max_revisions: int,
    run_dir: Optional[Path] = None,
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
) -> DocumentRunRecord:
    """Runs the whole pipeline for one document: consistency/dependency checks (D1=b:
    continue DEGRADED if either fails independently), then every requirement in order.
    Writes to run_dir incrementally if given (D2b) -- document.json first, then one
    requirement file at a time, so an interruption leaves a resumable partial run."""
    consistency_report, dependency_report, doc_errors, doc_usage = run_document_stages(
        requirement_set, metadata.stages, stage_fns, throttle, max_attempts, backoff_seconds)

    doc_outcome = (DocumentOutcome.COMPLETED
                  if consistency_report is not None and dependency_report is not None
                  else DocumentOutcome.DEGRADED)
    record = DocumentRunRecord(
        requirement_set=requirement_set, metadata=metadata, outcome=doc_outcome,
        errors=doc_errors, consistency_report=consistency_report,
        dependency_report=dependency_report, usage=doc_usage)
    if run_dir is not None:
        write_document_run(run_dir, record)

    requirement_records = []
    for req in requirement_set.requirements:
        req_record = run_requirement(
            RequirementRunRecord(requirement=req, run_id=metadata.run_id), requirement_set,
            consistency_report, dependency_report, stage_fns, human_fns, throttle,
            max_revisions, metadata.stages, max_attempts, backoff_seconds)
        requirement_records.append(req_record)
        if run_dir is not None:
            write_requirement_run(run_dir, req_record)

    return record.model_copy(update={"requirement_records": requirement_records})
```
Add `DocumentOutcome`, `RunMetadata` to the `from design.schemas import (...)` block.

- [ ] **Step 4: Run the test, confirm it passes**

Run: `python -m orchestrator.test_harness`
Expected: ends with `N checks passed, 0 failed`

- [ ] **Step 5: Commit**

```bash
git add orchestrator/pipeline.py orchestrator/test_harness.py
git commit -m "feat: add run_document, wiring document-level and per-requirement stages together"
```

---

## Task 12: `resume_document`, `retry_document_stage`, and the remaining scenarios

Covers scenario 6 (document-level stage retried within the same run), 8 (transport failure → error → resume → finish), 9 (interruption mid-document, full round-trip), 10 (validation failure — new ground, added to `ORCHESTRATOR_CONTRACT.md` here), 12 and 13 (token usage, validation vs. transport failures).

**Files:**
- Modify: `orchestrator/pipeline.py`
- Modify: `orchestrator/test_harness.py`
- Modify: `design/ORCHESTRATOR_CONTRACT.md` (scenario 10 is new ground — add it here, not as a special case)

**Interfaces:**
- Consumes: everything from Tasks 8–11.
- Produces: `retry_document_stage(run_dir, stage, stage_fns, throttle, max_attempts=3, backoff_seconds=...) -> DocumentRunRecord`, `resume_document(run_dir, stage_fns, human_fns, throttle, max_revisions, max_attempts=3, backoff_seconds=...) -> DocumentRunRecord`.

- [ ] **Step 1: Write the failing test for scenario 6 (document-level retry within the same run)**

```python
def test_document_stage_retry_within_run() -> None:
    """Scenario 6: a failed document-level stage is retried within the SAME run (not a
    new one), the original failure stays in errors, a second failure bumps retry_count
    rather than appending a new entry, and outcome climbs DEGRADED -> COMPLETED once
    the retry succeeds."""
    section("Scenario 6 -- document-level stage retried within the same run")
    from orchestrator.pipeline import run_document, retry_document_stage, Throttle
    from design.schemas import DocumentStage, DocumentOutcome, FailureKind

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
        # RequirementSet requires min_length=1, so there is no empty-document stand-in
        # to isolate the document-level outcome from requirement processing -- instead
        # classify is scripted to fail fast too, and the test only asserts on the
        # document-level fields (outcome, errors), ignoring requirement_records.
        first_fns = StageFns(
            check_consistency=Scripted([StageCallFailed("429")] * 2),
            map_dependencies=Scripted([{"doc_id": DOC.doc_id, "dependencies": []}]),
            classify=Scripted([StageCallFailed("429")] * 2),
            check_quality=None, refine=None, select_strategy=None, generate_tests=None)
        metadata = make_metadata()
        result = run_document(DOC, metadata, first_fns, HumanFns(
            answer_questions=lambda t: [], decide_at_cap=lambda r: (None, None)),
            throttle, max_revisions=3, run_dir=tmp_path, max_attempts=2,
            backoff_seconds=lambda a: 0.0)
        ok("first run is DEGRADED", result.outcome is DocumentOutcome.DEGRADED)
        ok("first run recorded one consistency_checker error", len(result.errors) == 1)
        first_retry_count = result.errors[0].retry_count

        retry_fns = StageFns(
            check_consistency=Scripted([{"doc_id": DOC.doc_id, "conflicts": []}]),
            map_dependencies=None, classify=None, check_quality=None, refine=None,
            select_strategy=None, generate_tests=None)
        retried = retry_document_stage(tmp_path, DocumentStage.CONSISTENCY_CHECKER, retry_fns,
                                       throttle, max_attempts=1, backoff_seconds=lambda a: 0.0)
        ok("retry succeeds: outcome climbs to COMPLETED", retried.outcome is DocumentOutcome.COMPLETED)
        ok("the original failure is still on record", len(retried.errors) == 1)
        ok("no second entry was appended for the same stage",
           sum(1 for e in retried.errors if e.stage is DocumentStage.CONSISTENCY_CHECKER) == 1)

        # -- second failure bumps retry_count instead of appending --
        fail_again_fns = StageFns(
            check_consistency=Scripted([StageCallFailed("429")]), map_dependencies=None,
            classify=None, check_quality=None, refine=None, select_strategy=None,
            generate_tests=None)
        # Reset the fixture run_dir to the post-first-run DEGRADED state to test this
        # branch in isolation: write it back down before retrying again.
        degraded_again = retried.model_copy(update={
            "outcome": DocumentOutcome.DEGRADED, "consistency_report": None})
        from orchestrator.pipeline import write_document_run
        write_document_run(tmp_path, degraded_again)
        retried_again = retry_document_stage(tmp_path, DocumentStage.CONSISTENCY_CHECKER,
                                             fail_again_fns, throttle, max_attempts=1,
                                             backoff_seconds=lambda a: 0.0)
        ok("still only one error entry for the stage after a second failure",
           sum(1 for e in retried_again.errors if e.stage is DocumentStage.CONSISTENCY_CHECKER) == 1)
        ok("retry_count bumped rather than reset",
           retried_again.errors[0].retry_count > first_retry_count)
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `python -m orchestrator.test_harness`
Expected: `ImportError: cannot import name 'retry_document_stage' from 'orchestrator.pipeline'`

- [ ] **Step 3: Add `retry_document_stage` to `orchestrator/pipeline.py`**

```python
def retry_document_stage(
    run_dir: Path,
    stage: DocumentStage,
    stage_fns: StageFns,
    throttle: Throttle,
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
) -> DocumentRunRecord:
    """Retries ONE failed document-level stage within the same run (contract item 6,
    'Retrying a failed document-level stage') rather than starting a new run, which
    would orphan every already-completed requirement record. errors is a LOG of failed
    attempts, not current state, so the original failure stays even after a successful
    retry; a second failure of the same stage bumps retry_count on the existing entry
    instead of appending a duplicate (document-level stages run once per run)."""
    record = read_document_run(run_dir)
    stage_fn = {DocumentStage.CONSISTENCY_CHECKER: stage_fns.check_consistency,
               DocumentStage.DEPENDENCY_MAPPER: stage_fns.map_dependencies}[stage]
    model_cls = {DocumentStage.CONSISTENCY_CHECKER: ConsistencyReport,
                DocumentStage.DEPENDENCY_MAPPER: DependencyReport}[stage]
    field_name = {DocumentStage.CONSISTENCY_CHECKER: "consistency_report",
                 DocumentStage.DEPENDENCY_MAPPER: "dependency_report"}[stage]

    usage: list[DocumentTokenUsage] = []
    try:
        report = call_document_stage(
            stage_fn, (record.requirement_set,), model_cls, stage,
            record.metadata.stages[stage.value].model, throttle, usage,
            max_attempts, backoff_seconds)
    except StageFailed as f:
        existing = next((e for e in record.errors if e.stage is stage), None)
        errors = [e for e in record.errors if e.stage is not stage]
        if existing is not None:
            errors.append(existing.model_copy(
                update={"retry_count": existing.retry_count + f.retry_count + 1}))
        else:
            errors.append(DocumentStageError(stage=stage, kind=f.kind, message=f.message,
                                             retry_count=f.retry_count))
        record = record.model_copy(update={"errors": errors, "usage": record.usage + usage})
    else:
        record = record.model_copy(update={field_name: report, "usage": record.usage + usage})

    both_present = record.consistency_report is not None and record.dependency_report is not None
    record = record.model_copy(update={
        "outcome": DocumentOutcome.COMPLETED if both_present else DocumentOutcome.DEGRADED})
    record = DocumentRunRecord.model_validate(record.model_dump(mode="json"))  # re-validate before persisting
    write_document_run(run_dir, record)
    return record
```

- [ ] **Step 4: Run the test, confirm it passes**

Run: `python -m orchestrator.test_harness`
Expected: passes (fix the test's awkward re-setup step if the DEGRADED-again fixture doesn't validate cleanly — `DocumentRunRecord`'s `_outcome_matches_contents` validator requires a `DocumentStageError` explaining any missing report on a `DEGRADED` record, so `degraded_again` in Step 1 must keep its existing error entry, which it does via `.model_copy`)

- [ ] **Step 5: Write the failing test for scenarios 8 and 9**

```python
def test_error_resume_finish() -> None:
    """Scenario 8: not just 'retries exhaust and outcome=ERROR is recorded' -- continue
    past that. A fresh resume pass must find the requirement in pending_requirement_ids,
    restart it at the failed stage, and reach a terminal outcome."""
    section("Scenario 8 -- transport failure -> error -> resume -> finish")
    from orchestrator.pipeline import run_document, resume_document, Throttle
    from design.schemas import RunOutcome

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
        one_req_doc = RequirementSet(doc_id="one-req", requirements=[REQ_A])
        failing_fns = StageFns(
            check_consistency=Scripted([{"doc_id": "one-req", "conflicts": []}]),
            map_dependencies=Scripted([{"doc_id": "one-req", "dependencies": []}]),
            classify=Scripted([StageCallFailed("429")] * 2),  # exhausts at max_attempts=2
            check_quality=None, refine=None, select_strategy=None, generate_tests=None)
        human_fns = HumanFns(answer_questions=lambda t: [], decide_at_cap=lambda r: (None, None))
        metadata = make_metadata(run_id="run-scenario-8")
        result = run_document(one_req_doc, metadata, failing_fns, human_fns, throttle,
                              max_revisions=3, run_dir=tmp_path, max_attempts=2,
                              backoff_seconds=lambda a: 0.0)
        ok("the requirement errored", result.requirement_records[0].outcome is RunOutcome.ERROR)
        ok("it shows up as pending", REQ_A.id in result.pending_requirement_ids)

        recovering_fns = StageFns(
            check_consistency=None, map_dependencies=None,
            classify=Scripted([{"requirement_id": REQ_A.id, "system_type": "other", "rationale": "r"}]),
            check_quality=Scripted([{"requirement_id": REQ_A.id, "passed": True, "issues": []}]),
            refine=None,
            select_strategy=Scripted([{"requirement_id": REQ_A.id, "system_type": "other",
                                       "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
            generate_tests=Scripted([{"requirement_id": REQ_A.id, "test_cases": [{
                "id": "TC-1", "requirement_ids": [REQ_A.id], "technique_used": "boundary_value_analysis",
                "title": "t", "steps": ["s"], "expected_result": "e"}]}]))
        resumed = resume_document(tmp_path, recovering_fns, human_fns, throttle, max_revisions=3)
        ok("resume finds and finishes the errored requirement",
           resumed.requirement_records[0].outcome is RunOutcome.COMPLETED)
        ok("nothing is pending after resume", resumed.pending_requirement_ids == [])


def test_interruption_mid_document_round_trip() -> None:
    """Scenario 9: write partial files, abandon the in-memory orchestrator entirely,
    construct a NEW one from disk, continue to completion. Distinct from resume_at as a
    pure function and from serialization round-trip checks -- this is the only scenario
    proving resumability works end to end."""
    section("Scenario 9 -- interruption mid-document, full round trip")
    from orchestrator.pipeline import (
        run_document_stages, write_document_run, write_requirement_run, resume_document,
        read_document_run, Throttle)
    from design.schemas import DocumentOutcome, RunOutcome

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
        metadata = make_metadata(run_id="run-scenario-9")

        # Simulate an interruption: document-level stages ran and were written, REQ_A
        # is untouched (no file at all -- "no record file" also counts as pending),
        # and then the process is abandoned. No run_document call spans this at all.
        cons, deps, errors, usage = run_document_stages(
            DOC, metadata.stages,
            StageFns(check_consistency=Scripted([{"doc_id": DOC.doc_id, "conflicts": []}]),
                    map_dependencies=Scripted([{"doc_id": DOC.doc_id, "dependencies": []}]),
                    classify=None, check_quality=None, refine=None, select_strategy=None,
                    generate_tests=None),
            throttle)
        partial = DocumentRunRecord(requirement_set=DOC, metadata=metadata,
                                    outcome=DocumentOutcome.COMPLETED, consistency_report=cons,
                                    dependency_report=deps, usage=usage)
        write_document_run(tmp_path, partial)
        # (No requirement files written at all -- both requirements are pending.)

        reloaded_before = read_document_run(tmp_path)
        ok("both requirements are pending on the interrupted, freshly-read record",
           set(reloaded_before.pending_requirement_ids) == {REQ_A.id, REQ_B.id})

        # A brand new orchestrator "process" picks up from disk -- nothing here reuses
        # any in-memory state from the run_document_stages call above.
        def classification_for(req_id):
            return {"requirement_id": req_id, "system_type": "other", "rationale": "r"}
        finishing_fns = StageFns(
            check_consistency=None, map_dependencies=None,
            classify=Scripted([classification_for(REQ_A.id), classification_for(REQ_B.id)]),
            check_quality=Scripted([
                {"requirement_id": REQ_A.id, "passed": True, "issues": []},
                {"requirement_id": REQ_B.id, "passed": True, "issues": []}]),
            refine=None,
            select_strategy=Scripted([
                {"requirement_id": REQ_A.id, "system_type": "other",
                 "techniques": ["boundary_value_analysis"], "rationale": "r"},
                {"requirement_id": REQ_B.id, "system_type": "other",
                 "techniques": ["boundary_value_analysis"], "rationale": "r"}]),
            generate_tests=Scripted([
                {"requirement_id": REQ_A.id, "test_cases": [{
                    "id": "TC-A-1", "requirement_ids": [REQ_A.id],
                    "technique_used": "boundary_value_analysis", "title": "t", "steps": ["s"],
                    "expected_result": "e"}]},
                {"requirement_id": REQ_B.id, "test_cases": [{
                    "id": "TC-B-1", "requirement_ids": [REQ_B.id],
                    "technique_used": "boundary_value_analysis", "title": "t", "steps": ["s"],
                    "expected_result": "e"}]}]))
        human_fns = HumanFns(answer_questions=lambda t: [], decide_at_cap=lambda r: (None, None))
        finished = resume_document(tmp_path, finishing_fns, human_fns, throttle, max_revisions=3)
        ok("both requirements finished after the full round trip",
           all(r.outcome is RunOutcome.COMPLETED for r in finished.requirement_records))
        ok("re-reading from disk after resume shows nothing pending",
           read_document_run(tmp_path).pending_requirement_ids == [])
```

- [ ] **Step 6: Add `resume_document` to `orchestrator/pipeline.py`**

```python
def resume_document(
    run_dir: Path,
    stage_fns: StageFns,
    human_fns: HumanFns,
    throttle: Throttle,
    max_revisions: int,
    max_attempts: int = 3,
    backoff_seconds: Callable[[int], float] = lambda attempt: 2.0 ** attempt,
) -> DocumentRunRecord:
    """A resume pass: read the document from disk, process everything in
    pending_requirement_ids (no record at all, or IN_PROGRESS/ERROR), starting each at
    its derived resume_at position. A requirement that never had a file gets a fresh
    IN_PROGRESS record; one that errored gets its existing record continued in place."""
    record = read_document_run(run_dir)
    pending = set(record.pending_requirement_ids)
    by_id = {r.requirement.id: r for r in record.requirement_records}

    updated_records = []
    for req in record.requirement_set.requirements:
        if req.id not in pending:
            updated_records.append(by_id[req.id])
            continue
        base = by_id.get(req.id) or RequirementRunRecord(requirement=req, run_id=record.metadata.run_id)
        updated = run_requirement(
            base, record.requirement_set, record.consistency_report, record.dependency_report,
            stage_fns, human_fns, throttle, max_revisions, record.metadata.stages,
            max_attempts, backoff_seconds)
        updated_records.append(updated)
        write_requirement_run(run_dir, updated)

    return record.model_copy(update={"requirement_records": updated_records})
```

- [ ] **Step 7: Run both new tests, confirm they pass**

Run: `python -m orchestrator.test_harness`
Expected: passes

- [ ] **Step 8: Write and pass scenarios 10, 12, 13 (validation failure and token usage)**

```python
def test_validation_failure() -> None:
    """Scenario 10: a stage returns output that fails model_validate. New ground
    relative to design/ORCHESTRATOR_CONTRACT.md as written -- added there in this same
    task, not treated as a special case."""
    section("Scenario 10 -- validation failure")
    from orchestrator.pipeline import call_stage, StageFailed, Throttle
    from design.schemas import Classification, FailureKind

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    usage = []
    fn = Scripted([{"requirement_id": "R1", "system_type": "not-a-real-type", "rationale": "r"}])
    try:
        call_stage(fn, ("R1",), Classification, PipelineStage.CLASSIFIER, "fake-model",
                  throttle, usage, max_attempts=1, backoff_seconds=lambda a: 0.0)
        ok("an invalid enum value fails validation", False)
    except StageFailed as f:
        ok("an invalid enum value fails validation", f.kind is FailureKind.VALIDATION)
    ok("the call still returned, so usage was recorded", len(usage) == 1)


def test_token_usage_validation_failures() -> None:
    """Scenario 12: two validation-failing calls then a success -> 3 usage entries,
    every call returned so every call is metered."""
    section("Scenario 12 -- token usage, validation failures")
    from orchestrator.pipeline import call_stage, Throttle
    from design.schemas import Classification

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    usage = []
    fn = Scripted([
        {"requirement_id": "R1"},                          # missing fields -> VALIDATION
        {"requirement_id": "R1", "system_type": "bogus"},  # bad enum -> VALIDATION
        {"requirement_id": "R1", "system_type": "web", "rationale": "r"},  # succeeds
    ])
    result = call_stage(fn, ("R1",), Classification, PipelineStage.CLASSIFIER, "fake-model",
                        throttle, usage, max_attempts=3, backoff_seconds=lambda a: 0.0)
    ok("the call eventually succeeds", isinstance(result, Classification))
    ok("all three calls recorded usage (every call returned)", len(usage) == 3)
    ok("total tokens sum all three calls", sum(u.prompt_tokens + u.completion_tokens for u in usage) == 45)


def test_token_usage_transport_failures() -> None:
    """Scenario 13: two transport failures then a success -> 1 usage entry, the two
    429s never reached the model. Kept separate from scenario 12 deliberately -- a
    shared scenario would hide a wrong assumption about WHERE usage gets appended."""
    section("Scenario 13 -- token usage, transport failures")
    from orchestrator.pipeline import call_stage, Throttle
    from design.schemas import Classification

    throttle = Throttle(sleep_fn=lambda s: None, now_fn=lambda: FAKE_NOW)
    usage = []
    fn = Scripted([
        StageCallFailed("429"), StageCallFailed("429"),
        {"requirement_id": "R1", "system_type": "web", "rationale": "r"},
    ])
    result = call_stage(fn, ("R1",), Classification, PipelineStage.CLASSIFIER, "fake-model",
                        throttle, usage, max_attempts=3, backoff_seconds=lambda a: 0.0)
    ok("the call eventually succeeds", isinstance(result, Classification))
    ok("only the one call that returned recorded usage", len(usage) == 1)
```

Add all six new test functions from Steps 1, 5, and 8 to `main()`'s tuple.

- [ ] **Step 9: Run the full harness, confirm everything passes**

Run: `python -m orchestrator.test_harness`
Expected: ends with `N checks passed, 0 failed` — every scenario from the design doc (1–14) now has a passing test

- [ ] **Step 10: Add scenario 10 to `design/ORCHESTRATOR_CONTRACT.md`**

Add a new item, 14, after item 13 (added in Task 3):

```markdown
## 14. Validation failures are a real, recorded outcome

A stage's raw output can fail `model_cls.model_validate(...)` even though the call
itself succeeded (see `FailureKind.VALIDATION`, item 7). This was not originally in this
contract — it surfaced from building `orchestrator/test_harness.py`'s scenario 10, not
from a bug found in production. `call_stage`/`call_document_stage` treat it exactly like
a transport failure for retry purposes (same backoff, same `StageError`/
`DocumentStageError` shape), with two differences: `kind=VALIDATION` instead of
`TRANSPORT`, and usage IS recorded, because inference happened and tokens were spent on
output that got thrown away. That cost is itself a thesis-relevant number: how often,
and at what cost, a given model produces schema-invalid output.

*(Added 2026-08-08, see docs/superpowers/specs/2026-08-08-orchestrator-harness-design.md,
harness scenario 10.)*
```

- [ ] **Step 11: Commit**

```bash
git add orchestrator/pipeline.py orchestrator/test_harness.py design/ORCHESTRATOR_CONTRACT.md
git commit -m "$(cat <<'EOF'
Add resume_document, retry_document_stage; close out remaining scenarios

Covers scenario 6 (document-level stage retried within the same run,
not a new one), 8 (transport failure -> error -> resume -> finish,
the actual bug this harness exists to catch), 9 (interruption
mid-document, full round trip through disk with a fresh orchestrator
instance), 10 (validation failure -- new ground, added to
ORCHESTRATOR_CONTRACT.md here), and 12/13 (token usage under each
failure kind).

All 14 harness scenarios from the design doc now have a passing test.
EOF
)"
```

---

## Final check

- [ ] Run `python -m design.test_schemas` — ends "N passed, 0 failed"
- [ ] Run `python -m design.generate_diagrams` — exits 0
- [ ] Run `python -m orchestrator.test_harness` — ends "N passed, 0 failed", all 14 scenarios represented
- [ ] Re-read `design/ORCHESTRATOR_CONTRACT.md` — items 13 and 14 read coherently alongside the original 12
- [ ] Confirm `orchestrator/stages.py` is still just the stub from Task 1 — real LLM calls are explicitly the next phase, not this one
