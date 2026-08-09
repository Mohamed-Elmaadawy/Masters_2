"""
Regression tests for design/generate_diagrams.py's two validators. Run after any
change there:

    python -m design.test_generate_diagrams

Plain script, no pytest -- same convention as design/test_schemas.py.

generate_diagrams.py's own docstring explains why PIPELINE_EDGES/PIPELINE_NODES and
REQUIREMENT_TERMINALS/DOCUMENT_TERMINALS/FAILURE_TERMINALS are hand-declared, not
introspected: execution order and "which outcome is a terminal" aren't recorded in the
Pydantic models. validate_pipeline()/validate_path_trees() are the guardrails that stop
that hand-written part from silently going stale -- this file proves the guardrails
themselves actually fire, not just that they exist in prose.
"""

from __future__ import annotations

import design.generate_diagrams as gd

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


def test_validate_pipeline_catches_renamed_or_removed_schema_type() -> None:
    """A schema type PIPELINE_EDGES declares (e.g. "RequirementSet") must actually
    exist in schemas.py. validate_pipeline(models) takes `models` as a plain parameter
    (not a global), so this drives the real, unmodified PIPELINE_EDGES against a
    models dict that's missing one of the types it names -- exactly the "renamed or
    deleted" scenario the module's own docstring describes."""
    section("validate_pipeline -- a declared schema type missing from schemas.py")

    real_models = gd.schema_models()
    ok("sanity check: the real models pass validation with no error",
       _no_raise(lambda: gd.validate_pipeline(real_models)))

    declared_types = {t for _, _, t, _ in gd.PIPELINE_EDGES if t}
    ok("sanity check: PIPELINE_EDGES declares at least one schema type",
       len(declared_types) > 0)
    victim = sorted(declared_types)[0]
    missing_models = {k: v for k, v in real_models.items() if k != victim}

    try:
        gd.validate_pipeline(missing_models)
        ok(f"a models dict missing {victim!r} raises SystemExit", False)
    except SystemExit as e:
        ok(f"a models dict missing {victim!r} raises SystemExit", True)
        ok("the message names the missing type", victim in str(e))


def test_validate_pipeline_accepts_extra_unrelated_models() -> None:
    """The check is one-directional -- PIPELINE_EDGES's declared types must all exist
    in `models`, but `models` may freely contain types PIPELINE_EDGES never mentions
    (most of schemas.py's models aren't pipeline-carried payloads at all, e.g.
    StageConfig). A models dict that's a strict superset of what's needed must not
    trip the check."""
    section("validate_pipeline -- extra, undeclared models are not an error")
    real_models = gd.schema_models()
    ok("the real (unmodified) models dict validates cleanly",
       _no_raise(lambda: gd.validate_pipeline(real_models)))


def test_validate_path_trees_catches_outcome_with_no_drawn_path() -> None:
    """Every RunOutcome/DocumentOutcome member must have a path ending in it, drawn
    across REQUIREMENT_TERMINALS/FAILURE_TERMINALS/DOCUMENT_TERMINALS.
    validate_path_trees() reads these as module globals (no parameters to inject a
    fake `models` through, unlike validate_pipeline), so this monkeypatches one
    terminals dict for the duration of one call and restores it in `finally` -- the
    smallest change that removes exactly one real outcome's only drawn path without
    touching schemas.py itself."""
    section("validate_path_trees -- a RunOutcome with no drawn path")

    ok("sanity check: the real path trees validate with no error",
       _no_raise(lambda: gd.validate_path_trees()))

    original = gd.REQUIREMENT_TERMINALS
    # cap_generated's only drawn path is REQUIREMENT_TERMINALS's "RGEN" entry (see
    # generate_diagrams.py) -- dropping it must not be silently absorbed by
    # FAILURE_TERMINALS, which draws paths for error/in_progress, not cap_generated.
    victim_node, victim_value = next(
        (n, v) for n, v in original.items() if v == "cap_generated")
    try:
        gd.REQUIREMENT_TERMINALS = {n: v for n, v in original.items() if n != victim_node}
        try:
            gd.validate_path_trees()
            ok(f"dropping the only path to {victim_value!r} raises SystemExit", False)
        except SystemExit as e:
            ok(f"dropping the only path to {victim_value!r} raises SystemExit", True)
            ok("the message names the missing outcome value", victim_value in str(e))
    finally:
        gd.REQUIREMENT_TERMINALS = original

    ok("REQUIREMENT_TERMINALS was restored, no cross-test leakage",
       gd.REQUIREMENT_TERMINALS == original)
    ok("the real path trees validate again after restoration",
       _no_raise(lambda: gd.validate_path_trees()))


def test_validate_path_trees_catches_document_outcome_with_no_drawn_path() -> None:
    """DocumentOutcome's sibling of the above -- a separate dict (DOCUMENT_TERMINALS),
    a separate code path in validate_path_trees (contract item: check the twin)."""
    section("validate_path_trees -- a DocumentOutcome with no drawn path")

    original = gd.DOCUMENT_TERMINALS
    victim_value = "degraded"
    # DOCUMENT_TERMINALS maps three distinct nodes (DDEG1/DDEG2/DDEG3 -- consistency
    # failed, dependency failed, both failed) to this one outcome value -- dropping
    # only one still leaves the other two drawing a path to it. Every node naming this
    # value must go for the "no path at all" case to actually fire.
    try:
        gd.DOCUMENT_TERMINALS = {n: v for n, v in original.items() if v != victim_value}
        try:
            gd.validate_path_trees()
            ok(f"dropping the only path to {victim_value!r} raises SystemExit", False)
        except SystemExit as e:
            ok(f"dropping the only path to {victim_value!r} raises SystemExit", True)
            ok("the message names the missing outcome value", victim_value in str(e))
    finally:
        gd.DOCUMENT_TERMINALS = original

    ok("DOCUMENT_TERMINALS was restored, no cross-test leakage",
       gd.DOCUMENT_TERMINALS == original)


def _no_raise(fn) -> bool:
    try:
        fn()
        return True
    except SystemExit:
        return False


def main() -> int:
    print("=" * 72)
    print("generate_diagrams.py validator regression")
    print("=" * 72)
    for fn in (
        test_validate_pipeline_catches_renamed_or_removed_schema_type,
        test_validate_pipeline_accepts_extra_unrelated_models,
        test_validate_path_trees_catches_outcome_with_no_drawn_path,
        test_validate_path_trees_catches_document_outcome_with_no_drawn_path,
    ):
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
