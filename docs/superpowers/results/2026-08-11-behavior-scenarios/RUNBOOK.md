# RUNBOOK — 2026-08-11 behavior scenarios

Nothing in this file has been run. Fixtures and configs are built and validated
offline only (see "Offline validation" below). Spec:
`docs/superpowers/plans/2026-08-11-behavior-scenarios.md`.

All commands run from the repo root (`Masters_2/`), with `GEMINI_API_KEY` (or
`GEMINI_API_KEYS` for rotation, see `orchestrator/providers/rotating.py`) set to a
**paid**-tier key — the spec doc's cost section measured an absolute 20-request/day
cap on the free tier, and a full pass is 172–300 requests. Command form:

```
python -m orchestrator.cli run <config> <input>
```

## Order (spec doc, "Suggested order" — cheap and diagnostic first)

Controls first: if S2/S8 fail, nothing downstream is interpretable.

### 1. S2 — negative control

```
python -m orchestrator.cli run docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-02-null-control.yaml docs/superpowers/results/2026-08-11-behavior-scenarios/fixtures/scn-02-null-control.json
```
Expected cost: $0.03–0.06 (2 reqs, 10–18 calls).

### 2. S8 — clean requirement, control

```
python -m orchestrator.cli run docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-08-clean.yaml docs/superpowers/results/2026-08-11-behavior-scenarios/fixtures/scn-08-clean.json
```
Expected cost: $0.02–0.03 (1 req, 6–10 calls).

### 3. S4 — inconsistency, unambiguous numeric (floor case)

```
python -m orchestrator.cli run docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-04-conflict-numeric.yaml docs/superpowers/results/2026-08-11-behavior-scenarios/fixtures/scn-04-conflict-numeric.json
```
Expected cost: $0.03–0.06 (2 reqs, 10–18 calls).

### 4. S1 — dependency, minimal pair (floor case)

```
python -m orchestrator.cli run docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-01-dep-pair.yaml docs/superpowers/results/2026-08-11-behavior-scenarios/fixtures/scn-01-dep-pair.json
```
Expected cost: $0.03–0.06 (2 reqs, 10–18 calls).

### 5. S9 — ambiguity that must be caught, plus one known not to be

Needs the scripted human answer policy — reuse
`docs/superpowers/results/2026-08-10-first-real-run/answer_policy_driver.py`
unchanged (do not write a second policy; it would make the two runs incomparable).

```
python -m orchestrator.cli run docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-09-vague.yaml docs/superpowers/results/2026-08-11-behavior-scenarios/fixtures/scn-09-vague.json
```
Expected cost: $0.03–0.06 (2 reqs, 10–18 calls).

### 6. S10 — non-atomic and unmeasurable

```
python -m orchestrator.cli run docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-10-atomicity.yaml docs/superpowers/results/2026-08-11-behavior-scenarios/fixtures/scn-10-atomicity.json
```
Expected cost: $0.03–0.06 (2 reqs, 10–18 calls). **Read `refined_text` for LUITEL-R7
by hand** — the schema cannot check whether the rewrite stayed atomic.

### 7. S12 — classification and technique routing

```
python -m orchestrator.cli run docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-12-routing.yaml docs/superpowers/results/2026-08-11-behavior-scenarios/fixtures/scn-12-routing.json
```
Expected cost: $0.05–0.09 (3 reqs, 14–26 calls).

### 8. S3 — inconsistency, minimal pair (native, hard detection case)

```
python -m orchestrator.cli run docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-03-conflict-native.yaml docs/superpowers/results/2026-08-11-behavior-scenarios/fixtures/scn-03-conflict-native.json
```
Expected cost: $0.03–0.06 (2 reqs, 10–18 calls). A miss here is a genuine result — do
not reword the fixture to force a hit.

### 9. S5 — three-way inconsistency (hard detection case)

```
python -m orchestrator.cli run docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-05-conflict-threeway.yaml docs/superpowers/results/2026-08-11-behavior-scenarios/fixtures/scn-05-conflict-threeway.json
```
Expected cost: $0.05–0.09 (3 reqs, 14–26 calls).

### 10. S6 — circular dependency (hard detection case)

```
python -m orchestrator.cli run docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-06-cycle.yaml docs/superpowers/results/2026-08-11-behavior-scenarios/fixtures/scn-06-cycle.json
```
Expected cost: $0.05–0.09 (3 reqs, 14–26 calls).

### 11. S11 — the revision cap, both branches

Two separate runs, same fixture, **distinct run configs** (distinct human decisions
scripted at the cap):

```
python -m orchestrator.cli run docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-11a-cap-generate.yaml docs/superpowers/results/2026-08-11-behavior-scenarios/fixtures/scn-11-cap.json
python -m orchestrator.cli run docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-11b-cap-stop.yaml docs/superpowers/results/2026-08-11-behavior-scenarios/fixtures/scn-11-cap.json
```
Expected cost: ~$0.07 combined (1 req × 2 runs, ~20 calls total). If AUTOGEN-US3
passes cleanly instead of hitting the cap, re-run against AUTOGEN-US4 instead of
forcing it (see `fixtures/scn-11-cap.GROUND-TRUTH.md`).

### 12. S13 — forced DEGRADED document, real adapter (config-only, cheap, run any time)

Reuses the S1 fixture unchanged; only the config differs (`consistency_checker`
pointed at a nonexistent model):

```
python -m orchestrator.cli run docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-13-degraded.yaml docs/superpowers/results/2026-08-11-behavior-scenarios/fixtures/scn-01-dep-pair.json
```
Expected cost: $0.03–0.06 (2 reqs, 10–18 calls — dependency_mapper still runs
normally; only consistency_checker fails fatally).

### 13. S7 — signal in a larger document (most expensive, last)

```
python -m orchestrator.cli run docs/superpowers/results/2026-08-11-behavior-scenarios/configs/scn-07-dilution.yaml docs/superpowers/results/2026-08-11-behavior-scenarios/fixtures/scn-07-dilution.json
```
Expected cost: $0.11–0.23 (8 reqs, 34–66 calls).

---

**Full-pass total (Gemini paid tier, n=1 per scenario):** $0.55–$1.05. At the n=3
repetition the spec doc's threats-to-validity section argues for: $1.70–$3.10 per
provider. Run each scenario on both providers where quota allows (spec doc,
"Cross-cutting"); Groq's free tier has a measured 59.2% transport-failure rate
(contract item 19) even with the TPM throttle configured, so budget for repeats there
specifically.

---

## Offline validation performed (2026-08-11, before any run)

All done without spending anything, per the task's hard constraint against API calls:

1. **Every fixture parses as `design.schemas.RequirementSet`** — verified by loading
   all 12 `fixtures/*.json` through `RequirementSet.model_validate`. All 12 pass.
2. **Every config loads through `orchestrator/config.py`'s resolver, and every
   referenced prompt file exists and fingerprints cleanly** — verified by running
   `load_run_config` + `resolve_run_config` on all 14 `configs/*.yaml`. All 14
   resolve; every stage's `prompt_path` exists and its `prompt_hash` is computed from
   `orchestrator/example_prompts/*.txt`, unmodified.
3. **`run_id` and `output_dir` are unique across all 14 configs** — verified
   programmatically; both sets have 14 distinct values.
4. **Requirement ids are unique within each fixture** — enforced by
   `RequirementSet._ids_are_unique` itself (a `RequirementSet` with a duplicate id
   fails to construct); confirmed no fixture raised on load.
5. **Every `-P` id is text the ground-truth file names as planted** — cross-checked
   by hand against each `fixtures/*.GROUND-TRUTH.md`: `PURE-THEMAS-R6-P` (S4),
   `PURE-THEMAS-R4-P1`/`PURE-THEMAS-R4-P2` (S5), `SCN6-A-P`/`SCN6-B-P`/`SCN6-C-P`
   (S6) — no other id in any fixture carries a `-P` suffix.
6. **S13 resolves offline despite naming a nonexistent model** — confirmed
   `resolve_run_config` succeeds on `scn-13-degraded.yaml` because
   `orchestrator/providers/capabilities.py`'s `supports_output_mode` returns `True`
   unconditionally for `OutputMode.TEXT` (the config's output mode, inherited
   unchanged from `runs_gemini.yaml`) regardless of model name — the model-name check
   only happens at the real adapter call. `rate_limits` was confirmed to carry both
   `gemini/gemini-3.6-flash` and `gemini/gemini-nonexistent-model-x9` (`
   ResolvedRunConfig._rate_limits_match_resolved_models_exactly` would otherwise
   reject the config).

## One deliberate deviation from "change ONLY run_id and output_dir"

Every config's `prompts` section points at `orchestrator/example_prompts/*.txt` via a
relative path adjusted for this file's location (5 directories deeper than
`orchestrator/runs_gemini.yaml`, e.g. `../../../../../orchestrator/example_prompts/
consistency_checker.txt`) — the path *string* had to change so that the *referenced
file* stays exactly the same one `runs_gemini.yaml` uses. No prompt file under
`orchestrator/example_prompts/` was touched, and the fingerprint each config computes
at resolve time was confirmed to match those files' actual content (offline
validation, item 2 above). This is required for the task's own validation criterion
("every referenced prompt file exists and fingerprints cleanly") — leaving the path
string unchanged as a literal string would have pointed at a nonexistent file, since
this directory is not `orchestrator/`.
