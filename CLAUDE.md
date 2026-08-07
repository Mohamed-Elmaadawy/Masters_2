# Project conventions

Master's thesis: an LLM pipeline that refines natural-language requirements and
generates test cases from them. Python, Pydantic, free-tier LLM APIs (Gemini/Groq), no
agent framework. Academic deliverable — correctness and defensibility matter more than
polish.

The schema design phase is **finished**. The next phase is the orchestrator, the seven
stages, and their prompts.

---

## After any change to `design/schemas.py`

`design/` became a package (`design/__init__.py`) as part of the orchestrator work in `docs/superpowers/plans/2026-08-08-orchestrator-harness-plan.md`, so both scripts are now run as modules from the repo root rather than as bare file paths.

Both, every time:

```bash
python -m design.test_schemas        # must end "N checks passed, 0 failed"
python -m design.generate_diagrams   # rewrites the five .mermaid files
```

The diagram generator also **validates**: it fails if the pipeline declaration names a
schema type that no longer exists, or if a `RunOutcome`/`DocumentOutcome` has no drawn
path. A generation failure is a real signal, not a nuisance.

---

## Read these before writing orchestrator code

| File | What it is |
|---|---|
| `design/ORCHESTRATOR_CONTRACT.md` | **Start here.** The 12 things the orchestrator must do that the schema deliberately does not enforce. |
| `design/schemas.py` | The models. Comments explain *why*, not just what. |
| `design/DESIGN_NOTES.md` | 1,678 lines of decisions, including rejected ones. Search it before re-litigating anything. |
| `design/SCHEMA_AUDIT_CHECKLIST.md` | The eight lenses used to find schema gaps. |
| `design/test_schemas.py` | 265 checks. Also the best worked example of how the models fit together. |
| `datasets/EVALUATION_DATASETS.md` | Corpora reserved for the evaluation phase, plus one planned experiment. |

Do not restate design reasoning in new files — link to the `DESIGN_NOTES.md` section.

---

## How to work on this codebase

**Challenge weak reasoning, including mine and the user's.** If a design or
justification is wrong, say so before implementing it.

**Verify before asserting.** The one dismissal in this project that turned out wrong
rested on a claim about the project's own data that took one command to check and was
never run. If a decision depends on a fact, check the fact.

**Prefer the simplest thing that works.** No premature abstraction, no dependencies
nobody asked for. Justify complexity before adding it.

**Never invent results, metrics or citations.** Mark anything unverified as unverified.

**Explain simply.** Short, plain-language replies; the user has asked several times for
less density. Put the depth in `DESIGN_NOTES.md`, not in chat. Explain any Python or
Pydantic construct the first time it appears — the user has asked what `@property`,
`@computed_field` and `NamedTuple` do, but their design reasoning is sharp and has
caught real bugs. The gap is vocabulary, not understanding.

**Log rejected ideas too.** A rejected option with its reasoning prevents the same
question being re-litigated later without remembering why.

---

## Rules learned the hard way

These came from bugs, not taste.

**Don't write a check that can't fire.** Three separate checks turned out to be
unreachable because another rule always caught the case first. Unreachable means
untestable, which means untested. Delete it and say why in a comment.

**Two fields that must agree need something forcing it.** This single pattern produced
most of the bugs found in this project — `passed`/`issues`, outcome/contents,
`requirement_id` copies, `system_type` copies, `issue_category` copies. Any new field
restating something held elsewhere needs a validator in the same commit.

**A list identified by something needs a uniqueness check.** Nine instances existed
before `_require_unique` was applied across all of them.

**Mutation-test new rules.** Break the check on purpose, confirm the suite goes red. The
enumerated tests prove rules are *enforced* and the anchor tests prove they *exist*, but
only mutation runs prove the tests discriminate — and that layer has caught weaknesses
the other two could not, three times.

**A spec nobody executes drifts.** The resume rule lived only in prose and was wrong for
one case. It is now executed by `test_schemas.py::test_resume_positions`. If a document
describes logic, test the logic.

---

## Known-open, deliberately

Do not "fix" these without reading the reasoning first — each was decided, not
overlooked. All are in `DESIGN_NOTES.md`:

- Duplicate test cases across dependent requirements (Known Limitation 1)
- The schema verifies testability structure, not domain truth (2)
- `PERFORMANCE` doesn't separate hard real-time from soft targets (3)
- `VAGUE_PRONOUN` is expected to be noisy to detect (4)
- Undefined domain notation (e.g. `LO = T_LT`) isn't caught (5)
- `TestPlan` requires *every* case to cover the plan's requirement — loosen only if real
  generator output gets rejected (6)
- Mutation after construction bypasses validation — re-validate before persisting
- Pairwise testing deferred; needs a real combinatorial algorithm, not an LLM

---

## Environment

`pip install -r requirements.txt` — pydantic >= 2.1 (2.0 fails; v1 will not work).
Tested against 2.1 through 2.13.4.
