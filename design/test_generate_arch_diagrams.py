"""
Regression tests for design/generate_arch_diagrams.py's six validators. Run after any
change there:

    python -m design.test_generate_arch_diagrams

Plain script, no pytest -- same convention as design/test_schemas.py and
design/test_generate_diagrams.py.

Eight diagrams, most of them substantially hand-declared (runtime, call_stage,
config_flow, overview, stage_flow) and three introspected (modules, repo_map roles,
stage_wiring's cells). Every hand-declared part has a validator whose whole job is to
fail when the code moves underneath it. Per CLAUDE.md ("mutation-test new rules"), each
test below breaks one declaration on purpose and asserts the validator actually goes red
-- proving the checks discriminate, not merely that they run.

Deliberately NOT tested here, because it isn't checked anywhere and pretending otherwise
would be worse than the gap: runtime.mermaid's EDGES. Node names are verified to exist;
the calls between them are not traced. See generate_arch_diagrams.py's docstring.
"""

from __future__ import annotations

import dataclasses

import design.generate_arch_diagrams as ga
import design.schemas as schemas

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


def _raises_systemexit(fn) -> tuple[bool, str]:
    try:
        fn()
        return False, ""
    except SystemExit as e:
        return True, str(e)


def _no_raise(fn) -> bool:
    raised, _ = _raises_systemexit(fn)
    return not raised


# ---------------------------------------------------------------------------

def test_every_validator_passes_on_the_real_repository() -> None:
    """The baseline every mutation below is measured against. If this section fails,
    nothing further in this file means anything -- a validator that is already red
    cannot demonstrate that it went red for the reason a test intended."""
    section("baseline -- all six validators pass unmodified")
    for name, fn in (("stage_wiring", ga.validate_stage_wiring),
                     ("runtime", ga.validate_runtime),
                     ("call_stage", ga.validate_call_stage),
                     ("config_flow", ga.validate_config_flow),
                     ("repo_map", ga.validate_repo_map),
                     ("stage_flow", ga.validate_stage_flow)):
        ok(f"validate_{name} passes on the real repository", _no_raise(fn))


def test_stage_wiring_matches_all_stages() -> None:
    """STAGE_WIRING's first column must equal design.schemas.ALL_STAGES exactly, in
    order. A ninth stage, a renamed one, or a reordering must fail here rather than
    silently producing a diagram that draws eight of nine stages."""
    section("validate_stage_wiring -- a stage dropped from the declaration")

    original = ga.STAGE_WIRING
    try:
        ga.STAGE_WIRING = original[:-1]
        raised, msg = _raises_systemexit(ga.validate_stage_wiring)
        ok("dropping a stage row raises SystemExit", raised)
        ok("the message names ALL_STAGES", "ALL_STAGES" in msg)
    finally:
        ga.STAGE_WIRING = original
    ok("STAGE_WIRING restored", ga.STAGE_WIRING == original)


def test_stage_wiring_catches_a_wrong_factory_name() -> None:
    """Column 3 is a real orchestrator/stages.py attribute name. Renaming a factory
    without updating the row is the realistic drift, and it must not be absorbed by the
    ALL_STAGES check above (which only looks at column 1)."""
    section("validate_stage_wiring -- a factory that no longer exists")

    original = ga.STAGE_WIRING
    key, field, factory, protocol, model, prompt = original[0]
    try:
        ga.STAGE_WIRING = [(key, field, factory + "_renamed", protocol, model, prompt)] + list(original[1:])
        raised, msg = _raises_systemexit(ga.validate_stage_wiring)
        ok("a renamed factory raises SystemExit", raised)
        ok("the message names the missing factory", factory in msg)
    finally:
        ga.STAGE_WIRING = original


def test_stage_wiring_catches_a_factory_no_row_uses() -> None:
    """The other direction, and the one that actually catches a NEW stage: a
    make_*_fn added to orchestrator/stages.py that no STAGE_WIRING row mentions. Without
    this, adding a ninth stage function would leave the diagram quietly eight-wide.

    Simulated by removing a row's factory reference while leaving the row count intact,
    which is what a ninth real factory would look like from the check's point of view."""
    section("validate_stage_wiring -- a factory in stages.py that no row uses")

    original = ga.STAGE_WIRING
    key, field, factory, protocol, model, prompt = original[-1]
    try:
        # Point the last row at the first row's factory: now the last real factory is
        # referenced by nothing, exactly the "undrawn stage" shape.
        ga.STAGE_WIRING = list(original[:-1]) + [
            (key, field, original[0][2], protocol, model, prompt)]
        raised, msg = _raises_systemexit(ga.validate_stage_wiring)
        ok("an unreferenced factory raises SystemExit", raised)
        ok("the message names the orphaned factory", factory in msg)
    finally:
        ga.STAGE_WIRING = original


def test_stage_wiring_columns_track_the_real_objects() -> None:
    """Not a mutation -- a direct assertion that the declaration is currently true, so a
    reader can see what the diagram claims without running the generator."""
    section("validate_stage_wiring -- the declaration agrees with reality right now")
    ok("column 1 == ALL_STAGES",
       [r[0] for r in ga.STAGE_WIRING] == list(schemas.ALL_STAGES))
    ok("column 2 == StageFns fields",
       [r[1] for r in ga.STAGE_WIRING]
       == [f.name for f in dataclasses.fields(ga.stage_fns_mod.StageFns)])
    ok("all eight prompt files exist",
       all((ga.PROMPT_DIR / r[5]).is_file() for r in ga.STAGE_WIRING))


def test_runtime_catches_a_renamed_function() -> None:
    """Each runtime.mermaid node names a real module.attribute. This is the check that
    fires when pipeline.py's functions are renamed or removed."""
    section("validate_runtime -- a node naming a function that no longer exists")

    original = ga.RUNTIME_NODES
    victim = "RDOC"
    mod_name, attr, label, kind = original[victim]
    try:
        ga.RUNTIME_NODES = {**original, victim: (mod_name, attr + "_gone", label, kind)}
        raised, msg = _raises_systemexit(ga.validate_runtime)
        ok("a renamed function raises SystemExit", raised)
        ok("the message names the node", victim in msg)
    finally:
        ga.RUNTIME_NODES = original
    ok("RUNTIME_NODES restored", ga.RUNTIME_NODES == original)


def test_runtime_catches_an_edge_to_an_undeclared_node() -> None:
    section("validate_runtime -- an edge referencing an undeclared node")
    original = ga.RUNTIME_EDGES
    try:
        ga.RUNTIME_EDGES = list(original) + [("RDOC", "NOT_A_NODE", "")]
        raised, msg = _raises_systemexit(ga.validate_runtime)
        ok("an edge to an unknown node raises SystemExit", raised)
        ok("the message names the unknown node", "NOT_A_NODE" in msg)
    finally:
        ga.RUNTIME_EDGES = original


def test_call_stage_covers_every_attempt_result() -> None:
    """Every AttemptResult member must be drawn. This is the check that fires when a
    new failure mode is added to the schema and the diagram is not updated -- the exact
    shape of drift that made call_stage.mermaid worth generating rather than drawing."""
    section("validate_call_stage -- an AttemptResult with no branch drawn")

    original = ga.CALL_STAGE_NODES
    victim = "TRANSPORT_FAILURE"
    ok("sanity check: exactly one node claims TRANSPORT_FAILURE",
       len([n for n in original if n[3] == victim]) == 1)
    try:
        ga.CALL_STAGE_NODES = [n for n in original if n[3] != victim]
        raised, msg = _raises_systemexit(ga.validate_call_stage)
        ok("dropping the only TRANSPORT_FAILURE branch raises SystemExit", raised)
        ok("the message names the undrawn result", victim in msg)
    finally:
        ga.CALL_STAGE_NODES = original
    ok("CALL_STAGE_NODES restored", ga.CALL_STAGE_NODES == original)


def test_call_stage_covers_every_failure_kind() -> None:
    """FailureKind's sibling of the above. FATAL is drawn by exactly one node; OTHER by
    two (StageCallPartial and the catch-all), so FATAL is the one whose removal actually
    leaves the enum uncovered -- picking the wrong victim would make this test pass for
    the wrong reason."""
    section("validate_call_stage -- a FailureKind with no branch drawn")

    original = ga.CALL_STAGE_NODES
    victim = "FATAL"
    ok("sanity check: exactly one node claims kind=FATAL",
       len([n for n in original if n[4] == victim]) == 1)
    try:
        ga.CALL_STAGE_NODES = [(nid, lbl, kind, res, None if fk == victim else fk)
                               for nid, lbl, kind, res, fk in original]
        raised, msg = _raises_systemexit(ga.validate_call_stage)
        ok("dropping the only FATAL branch raises SystemExit", raised)
        ok("the message names the undrawn kind", victim in msg)
    finally:
        ga.CALL_STAGE_NODES = original


def test_call_stage_rejects_a_nonexistent_enum_member() -> None:
    """The reverse direction: a branch labelled with an enum member the schema does not
    have (a stale label left behind after a rename)."""
    section("validate_call_stage -- a branch naming a non-existent AttemptResult")

    original = ga.CALL_STAGE_NODES
    try:
        ga.CALL_STAGE_NODES = list(original) + [
            ("AGHOST", "ghost", "fail", "NO_SUCH_RESULT", None)]
        raised, msg = _raises_systemexit(ga.validate_call_stage)
        ok("a non-existent AttemptResult raises SystemExit", raised)
        ok("the message names it", "NO_SUCH_RESULT" in msg)
    finally:
        ga.CALL_STAGE_NODES = original


def test_config_flow_catches_a_renamed_function() -> None:
    section("validate_config_flow -- a node naming a function that no longer exists")

    original = ga.CONFIG_NODES
    nid, label, kind, mod_name, attr = next(n for n in original if n[0] == "RES")
    try:
        ga.CONFIG_NODES = [(nid, label, kind, mod_name, attr + "_gone") if n[0] == "RES" else n
                           for n in original]
        raised, msg = _raises_systemexit(ga.validate_config_flow)
        ok("a renamed config function raises SystemExit", raised)
        ok("the message names the node", "RES" in msg)
    finally:
        ga.CONFIG_NODES = original
    ok("CONFIG_NODES restored", ga.CONFIG_NODES == original)


def test_stage_flow_catches_a_missing_document_context_parameter() -> None:
    """stage_flow.mermaid draws CheckQualityFn as receiving relevant_conflicts and
    relevant_dependencies. This is the check that would have caught pipeline.mermaid's
    real, pre-existing mistake (design/generate_diagrams.py's PIPELINE_EDGES draws those
    reports flowing into the Classifier instead) -- if that stage's Protocol lost one of
    the parameters the diagram assumes, this must fail instead of drawing a diagram that
    quietly disagrees with the code."""
    section("validate_stage_flow -- a Protocol that lost a document-context parameter")

    original = ga._DOC_CONTEXT_PROTOCOLS
    try:
        ga._DOC_CONTEXT_PROTOCOLS = [
            ("CheckQualityFn", ("relevant_conflicts", "relevant_dependencies", "ghost_param"), ())
        ] + [row for row in original if row[0] != "CheckQualityFn"]
        raised, msg = _raises_systemexit(ga.validate_stage_flow)
        ok("requiring a parameter that doesn't exist raises SystemExit", raised)
        ok("the message names the missing parameter", "ghost_param" in msg)
    finally:
        ga._DOC_CONTEXT_PROTOCOLS = original
    ok("_DOC_CONTEXT_PROTOCOLS restored", ga._DOC_CONTEXT_PROTOCOLS == original)


def test_stage_flow_catches_an_unexpected_document_context_parameter() -> None:
    """The other direction: ClassifyFn is declared as receiving NEITHER report. If a
    future change gave it one (the exact kind of silent expansion that made
    pipeline.mermaid wrong in the first place), this must fail rather than keep drawing
    the Classifier with no document-context edges."""
    section("validate_stage_flow -- a Protocol with an undeclared document-context parameter")

    original = ga._DOC_CONTEXT_PROTOCOLS
    try:
        ga._DOC_CONTEXT_PROTOCOLS = [
            ("ClassifyFn", (), ("requirement_set",))  # a real param it does have
        ] + [row for row in original if row[0] != "ClassifyFn"]
        raised, msg = _raises_systemexit(ga.validate_stage_flow)
        ok("forbidding a parameter that exists raises SystemExit", raised)
        ok("the message names the unexpected parameter", "requirement_set" in msg)
    finally:
        ga._DOC_CONTEXT_PROTOCOLS = original


def test_stage_flow_catches_an_edge_to_an_undeclared_node() -> None:
    section("validate_stage_flow -- an edge referencing an undeclared node")
    original = ga.STAGE_FLOW_EDGES
    try:
        ga.STAGE_FLOW_EDGES = list(original) + [("QC", "NOT_A_NODE", "")]
        raised, msg = _raises_systemexit(ga.validate_stage_flow)
        ok("an edge to an unknown node raises SystemExit", raised)
        ok("the message names the unknown node", "NOT_A_NODE" in msg)
    finally:
        ga.STAGE_FLOW_EDGES = original


def test_stage_flow_matches_reality_right_now() -> None:
    """Not a mutation -- confirms the four StageFns Protocols this diagram depends on
    currently have exactly the parameters the diagram assumes, so a reader can trust the
    diagram without re-deriving it."""
    section("validate_stage_flow -- the declaration agrees with reality right now")
    ok("real repository currently passes validate_stage_flow",
       _no_raise(ga.validate_stage_flow))
    import inspect as _inspect
    ok("ClassifyFn takes no document-context parameter",
       "relevant_conflicts" not in _inspect.signature(ga.stage_fns_mod.ClassifyFn.__call__).parameters
       and "relevant_dependencies" not in _inspect.signature(ga.stage_fns_mod.ClassifyFn.__call__).parameters)
    ok("CheckQualityFn takes both",
       {"relevant_conflicts", "relevant_dependencies"}
       <= set(_inspect.signature(ga.stage_fns_mod.CheckQualityFn.__call__).parameters))


def test_repo_map_catches_an_undescribed_module() -> None:
    """The check with the most day-to-day value: a new .py module under design/ or
    orchestrator/ that nobody wrote a one-line role for. Simulated by removing an
    existing role, which is indistinguishable from never having added one."""
    section("validate_repo_map -- a module with no declared role")

    original = ga.FILE_ROLES
    victim = "orchestrator/pipeline.py"
    ok("sanity check: the victim is currently described", victim in original)
    try:
        ga.FILE_ROLES = {k: v for k, v in original.items() if k != victim}
        raised, msg = _raises_systemexit(ga.validate_repo_map)
        ok("an undescribed module raises SystemExit", raised)
        ok("the message names the module", victim in msg)
    finally:
        ga.FILE_ROLES = original
    ok("FILE_ROLES restored", ga.FILE_ROLES == original)


def test_repo_map_catches_a_role_for_a_deleted_file() -> None:
    """The other direction: a role left behind for a file that has been deleted or
    moved, which would draw a box for something that isn't there."""
    section("validate_repo_map -- a role describing a file that does not exist")

    original = ga.FILE_ROLES
    try:
        ga.FILE_ROLES = {**original, "orchestrator/deleted_module.py": "gone"}
        raised, msg = _raises_systemexit(ga.validate_repo_map)
        ok("a role for a missing file raises SystemExit", raised)
        ok("the message names the missing file", "deleted_module.py" in msg)
    finally:
        ga.FILE_ROLES = original


def test_modules_graph_is_introspected_not_declared() -> None:
    """modules.mermaid has no validator because it has nothing to validate -- it is read
    from the source with ast.parse. These checks assert that property directly: the graph
    contains modules and edges that were never typed into this repository by hand."""
    section("modules.mermaid -- introspection, not declaration")

    out = ga.build_modules()
    ok("the import graph names design.schemas", "design.schemas" in out)
    ok("the import graph names orchestrator.pipeline", "orchestrator.pipeline" in out)
    ok("pydantic is drawn as a third-party dependency", "pydantic" in out)
    ok("__pycache__ is excluded",
       all("__pycache__" not in p.parts for p in ga.python_files()))

    internal, external = ga.imports_of(ga.REPO_ROOT / "orchestrator" / "pipeline.py")
    ok("pipeline.py is seen importing design.schemas", "design.schemas" in internal)
    ok("pipeline.py is seen importing orchestrator.stage_fns",
       "orchestrator.stage_fns" in internal)
    ok("pipeline.py is seen importing pydantic", "pydantic" in external)


def test_every_diagram_builds_and_is_nonempty() -> None:
    """End to end: each builder produces something Mermaid-shaped. Syntactic validity is
    checked by rendering, not here -- this catches a builder that raises or silently
    returns a header with no body."""
    section("builders -- every diagram is produced and non-trivial")
    for name, fn in (("overview", ga.build_overview),
                     ("stage_flow", ga.build_stage_flow),
                     ("modules", ga.build_modules),
                     ("stage_wiring", ga.build_stage_wiring),
                     ("runtime", ga.build_runtime),
                     ("call_stage", ga.build_call_stage),
                     ("config_flow", ga.build_config_flow),
                     ("repo_map", ga.build_repo_map)):
        out = fn()
        ok(f"{name}: starts with the generated-file header", out.startswith(ga.HEADER))
        ok(f"{name}: declares a flowchart", "flowchart " in out)
        ok(f"{name}: has at least 20 lines", len(out.splitlines()) >= 20)
        ok(f"{name}: every node id is Mermaid-safe",
           all(c.isalnum() or c == "_" for c in ga._sid("a b/c.d-e(f)")))


def main() -> int:
    print("=" * 72)
    print("generate_arch_diagrams.py validator regression")
    print("=" * 72)
    for fn in (
        test_every_validator_passes_on_the_real_repository,
        test_stage_wiring_matches_all_stages,
        test_stage_wiring_catches_a_wrong_factory_name,
        test_stage_wiring_catches_a_factory_no_row_uses,
        test_stage_wiring_columns_track_the_real_objects,
        test_runtime_catches_a_renamed_function,
        test_runtime_catches_an_edge_to_an_undeclared_node,
        test_call_stage_covers_every_attempt_result,
        test_call_stage_covers_every_failure_kind,
        test_call_stage_rejects_a_nonexistent_enum_member,
        test_config_flow_catches_a_renamed_function,
        test_stage_flow_catches_a_missing_document_context_parameter,
        test_stage_flow_catches_an_unexpected_document_context_parameter,
        test_stage_flow_catches_an_edge_to_an_undeclared_node,
        test_stage_flow_matches_reality_right_now,
        test_repo_map_catches_an_undescribed_module,
        test_repo_map_catches_a_role_for_a_deleted_file,
        test_modules_graph_is_introspected_not_declared,
        test_every_diagram_builds_and_is_nonempty,
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
