"""
Generates six Mermaid diagrams of the *implementation* -- the code that runs -- to sit
alongside design/generate_diagrams.py's five diagrams of the *schema and the conceptual
pipeline*. Run after any structural change to design/ or orchestrator/:

    python -m design.generate_arch_diagrams

Outputs (all overwritten in place, all plain text so git diffs show what changed, all
under design/diagrams/ alongside generate_diagrams.py's five):

    design/diagrams/overview.mermaid      -- the whole project, six boxes, no detail.
                                            Start here.
    design/diagrams/stage_flow.mermaid    -- the 8 LLM stages, happy-path order only.
    design/diagrams/modules.mermaid       -- which module imports which. Fully introspected.
    design/diagrams/stage_wiring.mermaid  -- the 8 stages end to end: config key -> prompt
                                            file -> factory -> StageFns field -> Protocol
                                            -> output model.
    design/diagrams/runtime.mermaid       -- the orchestrator's real call graph, CLI to disk.
    design/diagrams/call_stage.mermaid    -- the retry/attempt state machine inside call_stage.
    design/diagrams/config_flow.mermaid   -- YAML -> ResolvedRunConfig -> run args -> run_dir
                                            on disk.
    design/diagrams/repo_map.mermaid      -- every directory and file, with what each is for.

--------------------------------------------------------------------------------
WHY A SECOND GENERATOR AND NOT MORE OF THE FIRST
--------------------------------------------------------------------------------
design/generate_diagrams.py imports design.schemas and nothing else, on purpose: it is
the schema's diagram generator and its validation is "does this schema type still
exist". These six are about orchestrator/ -- they import the orchestrator, walk the
repository, and parse source files. Merging them would make one script that fails for
six unrelated reasons and force the schema diagrams to depend on the orchestrator
importing cleanly. Two scripts, two dependency footprints, two failure surfaces.

design/generate_diagrams.py's own docstring names the residual gap it accepted:
"closing it properly means generating the diagram from the orchestrator's actual call
graph, which is worth revisiting once the orchestrator is written." That is exactly
what runtime.mermaid below does -- but see the honesty note on it: the call graph is
declared and *validated by name*, not traced. What is checked, and what is not, is
stated per diagram rather than implied.

--------------------------------------------------------------------------------
WHAT IS INTROSPECTED AND WHAT IS DECLARED
--------------------------------------------------------------------------------
    overview.mermaid      declared -- six boxes, deliberately not derived from anything.
                          Its two stage counts ARE pulled live from schemas.py, so they
                          can't go stale, but the boxes themselves are curated by hand,
                          same as this docstring's own English. See its own comment.
    modules.mermaid       fully introspected (ast.parse of every .py file). Cannot drift.
    repo_map.mermaid      fully introspected (filesystem walk). Roles are declared, and
                          a .py/.md file with no declared role FAILS the build.
    stage_wiring.mermaid  declared rows, every cell of every row checked against the
                          real ALL_STAGES / StageFns fields / stages.py factories /
                          Protocol classes / schemas.py models / prompt files on disk.
                          Adding a ninth stage anywhere fails until the row is added.
    call_stage.mermaid    declared, but every AttemptResult and FailureKind member must
                          appear somewhere in it or the build fails.
    config_flow.mermaid   declared, with every model field name and function name it
                          references checked to still exist.
    runtime.mermaid       declared, with every "module.function" node checked to still
                          exist. This catches renames and deletions. It does NOT catch a
                          call that was removed while both functions still exist -- the
                          same residual gap generate_diagrams.py names for pipeline.mermaid,
                          accepted here for the same reason and recorded rather than hidden.
    stage_flow.mermaid    declared, but the one thing that made generate_diagrams.py's own
                          pipeline.mermaid quietly wrong -- which per-requirement stage
                          receives which document-level report -- is checked here against
                          the REAL StageFns Protocol signatures via inspect.signature, not
                          hand-copied. See validate_stage_flow's docstring.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
from pathlib import Path

import design.schemas as schemas
from orchestrator import stage_fns as stage_fns_mod

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = Path(__file__).resolve().parent / "diagrams"

HEADER = (
    "%% GENERATED by design/generate_arch_diagrams.py -- do not edit by hand.\n"
    "%% Rerun after any structural change to design/ or orchestrator/.\n"
)

STYLES = {
    "schema":   "fill:#e8eaf6,stroke:#3f51b5,color:#1a237e",
    "doc":      "fill:#e0f2f1,stroke:#00897b,color:#004d40",
    "req":      "fill:#fff8e1,stroke:#f9a825,color:#e65100",
    "human":    "fill:#fce4ec,stroke:#c2185b,color:#880e4f",
    "decision": "fill:#f5f5f5,stroke:#616161,color:#212121",
    "fail":     "fill:#ffebee,stroke:#c62828,color:#b71c1c",
    "term":     "fill:#ede7f6,stroke:#5e35b1,color:#311b92",
    "test":     "fill:#f1f8e9,stroke:#689f38,color:#33691e",
    "provider": "fill:#f3e5f5,stroke:#8e24aa,color:#4a148c",
    "ext":      "fill:#eceff1,stroke:#78909c,color:#37474f",
    "io":       "fill:#e1f5fe,stroke:#0277bd,color:#01579b",
    "note":     "fill:#ffffff,stroke:#9e9e9e,color:#424242,stroke-dasharray:3 3",
}


def _sid(text: str) -> str:
    """Mermaid-safe node/subgraph id: letters, digits and underscores only.

    Not cosmetic -- Mermaid's flowchart grammar rejects an id containing '(' or '/',
    and a subgraph id built from a human-readable title ("configuration
    (orchestrator/config.py)") hits exactly that. Whitelist rather than a list of
    replacements, so the next punctuation mark someone puts in a label is handled too.
    """
    return "".join(c if c.isalnum() or c == "_" else "_" for c in text)


def _classdefs(nodes: dict[str, tuple[str, str]]) -> list[str]:
    lines = []
    for kind, style in STYLES.items():
        ids = [n for n, (_, k) in nodes.items() if k == kind]
        if ids:
            lines.append(f"    classDef {kind} {style}")
            lines.append(f"    class {','.join(ids)} {kind}")
    return lines


def _render_flowchart(nodes: dict[str, tuple[str, str]], edges: list[tuple[str, str, str]],
                      title: str, direction: str = "TD",
                      subgraphs: list[tuple[str, list[str]]] | None = None) -> str:
    """nodes: id -> (label, kind). edges: (from, to, label). subgraphs: (title, [ids])."""
    lines = [HEADER, f"%% {title}", f"flowchart {direction}"]
    grouped = {n for _, ids in (subgraphs or []) for n in ids}
    for gtitle, ids in subgraphs or []:
        lines.append(f'    subgraph {_sid(gtitle)}["{gtitle}"]')
        for nid in ids:
            label, kind = nodes[nid]
            shape = f'{nid}{{"{label}"}}' if kind == "decision" else f'{nid}["{label}"]'
            lines.append(f"        {shape}")
        lines.append("    end")
    for nid, (label, kind) in nodes.items():
        if nid in grouped:
            continue
        shape = f'{nid}{{"{label}"}}' if kind == "decision" else f'{nid}["{label}"]'
        lines.append(f"    {shape}")
    lines.append("")
    for edge in edges:
        # A 4th element is an optional edge style ("dotted") -- every existing caller
        # passes plain 3-tuples and is unaffected. Added for stage_flow.mermaid, so a
        # secondary/contextual edge (document-level report -> a per-requirement stage
        # that merely reads it) can be drawn visually lighter than the main flow it
        # crosses, instead of all edges competing for the same attention.
        a, b, label = edge[0], edge[1], edge[2]
        style = edge[3] if len(edge) > 3 else "solid"
        base = "-.->" if style == "dotted" else "-->"
        arrow = f'{base}|"{label}"|' if label else base
        lines.append(f"    {a} {arrow} {b}")
    lines.append("")
    lines.extend(_classdefs(nodes))
    return "\n".join(lines) + "\n"


# ===========================================================================
# 1. modules.mermaid -- fully introspected import graph
# ===========================================================================

INTERNAL_PACKAGES = ("design", "orchestrator")
# Third-party imports worth drawing. Everything else in the stdlib is noise here.
TRACKED_EXTERNAL = ("pydantic", "requests", "yaml")


def python_files() -> list[Path]:
    out: list[Path] = []
    for pkg in INTERNAL_PACKAGES:
        out.extend(sorted((REPO_ROOT / pkg).rglob("*.py")))
    return [p for p in out if "__pycache__" not in p.parts]


def module_name_of(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    parts = [p for p in rel.parts if p != "__init__"]
    return ".".join(parts)


def imports_of(path: Path) -> tuple[set[str], set[str]]:
    """(internal modules imported, tracked third-party packages imported)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    internal: set[str] = set()
    external: set[str] = set()

    def record(dotted: str) -> None:
        root = dotted.split(".")[0]
        if root in INTERNAL_PACKAGES:
            internal.add(dotted)
        elif root in TRACKED_EXTERNAL:
            external.add(root)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                record(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            record(node.module)
    return internal, external


def build_modules() -> str:
    files = python_files()
    known = {module_name_of(p) for p in files}
    # An import of `orchestrator.providers` (the package) resolves to its __init__.
    nodes: dict[str, tuple[str, str]] = {}
    edges: list[tuple[str, str, str]] = []
    ext_seen: set[str] = set()

    def is_test(mod: str) -> bool:
        return mod.rsplit(".", 1)[-1].startswith("test_")

    def kind_of(mod: str) -> str:
        if is_test(mod):
            return "test"
        if mod.startswith("design"):
            return "schema"
        if "providers" in mod:
            return "provider"
        return "req"

    drawn: list[str] = []
    for path in files:
        mod = module_name_of(path)
        if not mod or mod in ("design", "orchestrator", "orchestrator.providers"):
            continue  # empty __init__.py files -- nothing to say about them
        drawn.append(mod)
        nodes[_sid(mod)] = (mod, kind_of(mod))

    for path in files:
        mod = module_name_of(path)
        if _sid(mod) not in nodes:
            continue
        internal, external = imports_of(path)
        for target in sorted(internal):
            if target not in known or target == mod:
                continue
            if _sid(target) not in nodes:
                continue
            edges.append((_sid(mod), _sid(target), ""))
        for pkg in sorted(external):
            ext_seen.add(pkg)
            edges.append((_sid(mod), _sid(f"ext_{pkg}"), ""))

    for pkg in sorted(ext_seen):
        nodes[_sid(f"ext_{pkg}")] = (f"{pkg}<br/><i>third party</i>", "ext")

    # Membership is decided from the real module name, never from the sanitized node id
    # -- an id-substring test put design.test_schemas in both the design/ group and the
    # test group, and Mermaid silently keeps the first, drawing a misleading picture.
    subgraphs = [
        ("design/ -- schema layer",
         [_sid(m) for m in drawn if m.startswith("design.") and not is_test(m)]),
        ("orchestrator/ -- runtime layer",
         [_sid(m) for m in drawn if m.startswith("orchestrator.") and not is_test(m)]),
        ("test suites", [_sid(m) for m in drawn if is_test(m)]),
    ]
    placed = [n for _, ids in subgraphs for n in ids]
    if len(placed) != len(set(placed)):
        raise SystemExit("modules.mermaid put a module in two subgraphs")
    # Everything not placed above (the external packages) is drawn loose.
    return _render_flowchart(
        nodes, sorted(set(edges)),
        "Import graph. Fully introspected with ast.parse -- cannot drift. "
        "Arrows point from importer to imported.",
        "LR", subgraphs)


# ===========================================================================
# 2. stage_wiring.mermaid -- the 8 stages, every layer, checked against reality
# ===========================================================================

# (stage key in ALL_STAGES, StageFns field, stages.py factory, Protocol class,
#  schemas.py output model, prompt file under orchestrator/example_prompts/)
STAGE_WIRING: list[tuple[str, str, str, str, str, str]] = [
    ("consistency_checker", "check_consistency", "make_check_consistency_fn",
     "CheckConsistencyFn", "ConsistencyReport", "consistency_checker.txt"),
    ("dependency_mapper", "map_dependencies", "make_map_dependencies_fn",
     "MapDependenciesFn", "DependencyReport", "dependency_mapper.txt"),
    ("classifier", "classify", "make_classify_fn",
     "ClassifyFn", "Classification", "classifier.txt"),
    ("quality_checker", "check_quality", "make_check_quality_fn",
     "CheckQualityFn", "QualityReport", "quality_checker.txt"),
    ("refiner_questioner", "refine_questioner", "make_refine_questioner_fn",
     "RefineQuestionerFn", "RefinerTurn", "refiner_questioner.txt"),
    ("refiner_rewriter", "refine_rewriter", "make_refine_rewriter_fn",
     "RefineRewriterFn", "RefinedRequirement", "refiner_rewriter.txt"),
    ("strategy_selector", "select_strategy", "make_select_strategy_fn",
     "SelectStrategyFn", "TestStrategy", "strategy_selector.txt"),
    ("test_generator", "generate_tests", "make_generate_tests_fn",
     "GenerateTestsFn", "TestPlan", "test_generator.txt"),
]

DOC_LEVEL_STAGES = {"consistency_checker", "dependency_mapper"}
PROMPT_DIR = REPO_ROOT / "orchestrator" / "example_prompts"


def validate_stage_wiring() -> None:
    stages_mod = importlib.import_module("orchestrator.stages")

    declared_keys = [row[0] for row in STAGE_WIRING]
    if declared_keys != list(schemas.ALL_STAGES):
        raise SystemExit(
            "STAGE_WIRING does not match design.schemas.ALL_STAGES.\n"
            f"  declared: {declared_keys}\n  ALL_STAGES: {list(schemas.ALL_STAGES)}\n"
            "Add/rename/reorder the row, do not edit the diagram.")

    real_fields = [f.name for f in dataclasses.fields(stage_fns_mod.StageFns)]
    if [row[1] for row in STAGE_WIRING] != real_fields:
        raise SystemExit(
            f"STAGE_WIRING's StageFns fields {[r[1] for r in STAGE_WIRING]} do not match "
            f"the real StageFns fields {real_fields}.")

    for key, field, factory, protocol, model, prompt in STAGE_WIRING:
        if not hasattr(stages_mod, factory):
            raise SystemExit(f"stage {key!r}: orchestrator.stages has no {factory!r}")
        if not hasattr(stage_fns_mod, protocol):
            raise SystemExit(f"stage {key!r}: orchestrator.stage_fns has no {protocol!r}")
        if not hasattr(schemas, model):
            raise SystemExit(f"stage {key!r}: design.schemas has no model {model!r}")
        if not (PROMPT_DIR / prompt).is_file():
            raise SystemExit(f"stage {key!r}: prompt file missing: {PROMPT_DIR / prompt}")

    # The other direction: a factory added to stages.py that no row mentions.
    real_factories = {n for n in vars(stages_mod) if n.startswith("make_") and n.endswith("_fn")}
    declared_factories = {row[2] for row in STAGE_WIRING}
    if extra := sorted(real_factories - declared_factories):
        raise SystemExit(f"orchestrator.stages defines factories no STAGE_WIRING row uses: {extra}")


def build_stage_wiring() -> str:
    nodes: dict[str, tuple[str, str]] = {}
    edges: list[tuple[str, str, str]] = []
    for key, field, factory, protocol, model, prompt in STAGE_WIRING:
        kind = "doc" if key in DOC_LEVEL_STAGES else "req"
        cfg, pro, fac, fn, out = (f"cfg_{_sid(key)}", f"pr_{_sid(key)}", f"fa_{_sid(key)}",
                                  f"fn_{_sid(key)}", f"out_{_sid(key)}")
        nodes[cfg] = (f"<b>{key}</b><br/><i>YAML stages: key</i><br/>provider · model ·"
                      f"<br/>temperature · output_mode", kind)
        nodes[pro] = (f"example_prompts/<br/>{prompt}<br/><i>hashed -> prompt_hash</i>", "io")
        nodes[fac] = (f"stages.{factory}", kind)
        nodes[fn] = (f"StageFns.{field}<br/><i>{protocol}</i>", kind)
        nodes[out] = (f"schemas.{model}", "schema")
        edges += [(cfg, fac, "ResolvedStageConfig"), (pro, fac, "prompt template"),
                  (fac, fn, "closure"), (fn, out, "raw dict -> model_validate")]

    nodes["ADAPT"] = ("ProviderAdapter.complete()<br/><i>GeminiAdapter · GroqAdapter</i>", "provider")
    for row in STAGE_WIRING:
        edges.append((f"fa_{_sid(row[0])}", "ADAPT", ""))
    nodes["CS"] = ("pipeline.call_stage / call_document_stage<br/>"
                   "<i>retry · validate · record one StageAttempt per try</i>", "req")
    for row in STAGE_WIRING:
        edges.append((f"fn_{_sid(row[0])}", "CS", ""))

    subgraphs = [
        ("configuration (orchestrator/config.py)", [f"cfg_{_sid(r[0])}" for r in STAGE_WIRING]),
        ("prompt files", [f"pr_{_sid(r[0])}" for r in STAGE_WIRING]),
        ("factories (orchestrator/stages.py)", [f"fa_{_sid(r[0])}" for r in STAGE_WIRING]),
        ("typed slots (orchestrator/stage_fns.py)", [f"fn_{_sid(r[0])}" for r in STAGE_WIRING]),
        ("output models (design/schemas.py)", [f"out_{_sid(r[0])}" for r in STAGE_WIRING]),
    ]
    return _render_flowchart(
        nodes, edges,
        "The eight stages, every layer. Each column is checked against the real "
        "ALL_STAGES / StageFns fields / stages.py factories / Protocols / schema models / "
        "prompt files -- a ninth stage anywhere fails this build until its row is added.",
        "LR", subgraphs)


# ===========================================================================
# 3. runtime.mermaid -- the orchestrator's call graph, validated by name
# ===========================================================================

# node id -> (module, attribute or None for a non-callable box, label, kind)
RUNTIME_NODES: dict[str, tuple[str | None, str | None, str, str]] = {
    "MAIN":   ("orchestrator.cli", "main", "python -m orchestrator.cli<br/>CONFIG.yaml INPUT.json", "io"),
    "RUN":    ("orchestrator.cli", "_run", "cli._run<br/><i>argparse · exit codes 0/1/2/130</i>", "req"),
    "LOAD":   ("orchestrator.config", "load_run_config", "config.load_run_config<br/><i>YAML -> RunConfig, shape only</i>", "req"),
    "RESOLVE": ("orchestrator.config", "resolve_run_config", "config.resolve_run_config<br/><i>defaults+overrides · capability check ·<br/>prompt hashing -> ResolvedRunConfig</i>", "req"),
    "RUNDIR": ("orchestrator.config", "run_dir_for", "config.run_dir_for<br/><i>refuses to reuse an existing run dir</i>", "req"),
    "ADAPT":  ("orchestrator.providers.gemini", "GeminiAdapter", "GeminiAdapter.from_env<br/>GroqAdapter.from_env<br/><i>API keys from env only</i>", "provider"),
    "BUILD":  ("orchestrator.cli", "_build_stage_fns", "cli._build_stage_fns<br/><i>8 factories -> StageFns</i>", "req"),
    "HUMAN":  ("orchestrator.human_cli", "answer_questions_cli", "human_cli.answer_questions_cli<br/>human_cli.decide_at_cap_cli<br/><i>-> HumanFns</i>", "human"),
    "WCFG":   ("orchestrator.config", "write_run_config", "config.write_run_config<br/><i>run_config.json</i>", "io"),
    "RDOC":   ("orchestrator.pipeline", "run_document", "pipeline.run_document<br/><i>the run</i>", "req"),
    "DSTAGES": ("orchestrator.pipeline", "run_document_stages", "pipeline.run_document_stages<br/><i>both doc stages, independently</i>", "doc"),
    "CDS":    ("orchestrator.pipeline", "call_document_stage", "pipeline.call_document_stage", "doc"),
    "WDOC":   ("orchestrator.pipeline", "write_document_run", "pipeline.write_document_run<br/><i>document.json, requirement_records=[]</i>", "io"),
    "RREQ":   ("orchestrator.pipeline", "run_requirement", "pipeline.run_requirement<br/><i>once per requirement, in order</i>", "req"),
    "RESUMEAT": ("orchestrator.pipeline", "resume_at", "pipeline.resume_at<br/><i>position derived from populated fields</i>", "decision"),
    "CS":     ("orchestrator.pipeline", "call_stage", "pipeline.call_stage<br/><i>see call_stage.mermaid</i>", "req"),
    "LOOP":   ("orchestrator.pipeline", "_run_refine_loop", "pipeline._run_refine_loop<br/><i>quality check -> questions -> answers -><br/>rewrite -> re-check, until pass or cap</i>", "human"),
    "HFNS":   ("orchestrator.stage_fns", "HumanFns", "HumanFns.answer_questions<br/>HumanFns.decide_at_cap", "human"),
    "SFNS":   ("orchestrator.stage_fns", "StageFns", "StageFns.<i>one of eight</i><br/><i>see stage_wiring.mermaid</i>", "req"),
    "THR":    ("orchestrator.pipeline", "Throttle", "Throttle.wait_for_slot<br/><i>per model, before every call</i>", "req"),
    "WREQ":   ("orchestrator.pipeline", "write_requirement_run", "pipeline.write_requirement_run<br/><i>requirements/&lt;sha256&gt;.json ·<br/>also the mid-requirement checkpoint</i>", "io"),
    "ATOM":   ("orchestrator.pipeline", "atomic_write_text", "pipeline.atomic_write_text<br/><i>tmp file + os.replace</i>", "io"),
    "SUM":    ("orchestrator.cli", "_print_summary", "cli._print_summary<br/><i>outcome · per-requirement · tokens</i>", "term"),
    "RESUME": ("orchestrator.pipeline", "resume_document", "pipeline.resume_document<br/><b>no CLI wiring in v1</b>", "fail"),
    "RETRYD": ("orchestrator.pipeline", "retry_document_stage", "pipeline.retry_document_stage<br/><b>no CLI wiring in v1</b><br/><i>refuses once any requirement has run</i>", "fail"),
    "RDOCR":  ("orchestrator.pipeline", "read_document_run", "pipeline.read_document_run<br/><i>document.json + requirements/*.json</i>", "io"),
}

RUNTIME_EDGES: list[tuple[str, str, str]] = [
    ("MAIN", "RUN", ""),
    ("RUN", "LOAD", "1"),
    ("LOAD", "RESOLVE", "RunConfig"),
    ("RESOLVE", "RUNDIR", "ResolvedRunConfig"),
    ("RUNDIR", "ADAPT", "2 · only now are keys read"),
    ("ADAPT", "BUILD", ""),
    ("RUN", "HUMAN", ""),
    ("RUN", "WCFG", "3"),
    ("BUILD", "RDOC", "StageFns"),
    ("HUMAN", "RDOC", "HumanFns"),
    ("RDOC", "DSTAGES", "4"),
    ("DSTAGES", "CDS", "x2, independently"),
    ("RDOC", "WDOC", "5"),
    ("RDOC", "RREQ", "6 · per requirement"),
    ("RREQ", "RESUMEAT", ""),
    ("RESUMEAT", "CS", "start here"),
    ("RREQ", "LOOP", "if quality check failed"),
    ("LOOP", "CS", ""),
    ("LOOP", "HFNS", "RefinerTurn / at the cap"),
    ("CS", "THR", ""),
    ("THR", "SFNS", ""),
    ("CDS", "THR", ""),
    ("LOOP", "WREQ", "checkpoint before each human call"),
    ("RREQ", "WREQ", "after each requirement"),
    ("WREQ", "ATOM", ""),
    ("WDOC", "ATOM", ""),
    ("WCFG", "ATOM", ""),
    ("RDOC", "SUM", "7 · DocumentRunRecord"),
    ("ATOM", "RDOCR", "later run"),
    ("RDOCR", "RESUME", ""),
    ("RDOCR", "RETRYD", ""),
    ("RESUME", "RREQ", "pending_requirement_ids"),
    ("RETRYD", "CDS", "one stage, new invocation_id"),
]


def validate_runtime() -> None:
    for nid, (mod_name, attr, _, _) in RUNTIME_NODES.items():
        if mod_name is None or attr is None:
            continue
        mod = importlib.import_module(mod_name)
        if not hasattr(mod, attr):
            raise SystemExit(
                f"runtime.mermaid node {nid!r} names {mod_name}.{attr}, which no longer "
                "exists. Update RUNTIME_NODES.")
    for a, b, _ in RUNTIME_EDGES:
        for n in (a, b):
            if n not in RUNTIME_NODES:
                raise SystemExit(f"RUNTIME_EDGES references undeclared node id {n!r}")


def build_runtime() -> str:
    nodes = {nid: (label, kind) for nid, (_, _, label, kind) in RUNTIME_NODES.items()}
    return _render_flowchart(
        nodes, RUNTIME_EDGES,
        "The orchestrator's call graph, CLI to disk. Every node names a real "
        "module.attribute, checked at generation time -- but the EDGES are declared, not "
        "traced: a call deleted while both functions survive is not caught here. Numbered "
        "edges are cli._run's order.",
        "TD")


# ===========================================================================
# 4. call_stage.mermaid -- the retry/attempt state machine
# ===========================================================================

# (node id, label, kind, AttemptResult member or None, FailureKind member or None)
CALL_STAGE_NODES: list[tuple[str, str, str, str | None, str | None]] = [
    ("A0", "call_stage(...)<br/><i>attempt = 1 .. max_attempts</i>", "req", None, None),
    ("AT", "Throttle.wait_for_slot(model)", "req", None, None),
    ("AC", "stage_fn(*args)<br/><i>the provider call</i>", "req", None, None),
    ("AFATAL", "StageCallFatal<br/><i>bad key · unsupported output mode</i>", "fail", "FATAL_FAILURE", "FATAL"),
    ("APART", "StageCallPartial<br/><i>inference happened, output unusable</i><br/><b>carries tokens</b>", "fail", "OTHER_FAILURE", "OTHER"),
    ("AFAIL", "StageCallFailed<br/><i>network · rate limit · timeout</i>", "fail", "TRANSPORT_FAILURE", "TRANSPORT"),
    ("AOTHER", "any other Exception<br/><i>from the stage_fn only</i>", "fail", "OTHER_FAILURE", "OTHER"),
    ("AVAL", "model_cls.model_validate(raw)", "decision", None, None),
    ("AVERR", "ValidationError", "fail", "VALIDATION_FAILURE", "VALIDATION"),
    ("AID", "requirement_id / doc_id<br/>matches the one asked about?", "decision", None, None),
    ("AIDERR", "answered about a different requirement", "fail", "VALIDATION_FAILURE", "VALIDATION"),
    ("AEX", "extra_check(parsed)<br/><i>cross-stage agreement</i>", "decision", None, None),
    ("AEXERR", "extra_check returned a message", "fail", "VALIDATION_FAILURE", "VALIDATION"),
    ("AOK", "<b>return parsed</b>", "term", "SUCCESS", None),
    ("ABACK", "sleep(backoff_seconds(attempt))<br/><i>next attempt</i>", "req", None, None),
    ("AEXH", "<b>raise StageFailed</b><br/>kind · message · retry_count", "term", None, None),
    ("AREC", "StageError on the record<br/><i>outcome = ERROR, partial record kept</i>", "term", None, None),
]

CALL_STAGE_EDGES = [
    ("A0", "AT", ""), ("AT", "AC", ""),
    ("AC", "AFATAL", "raises"), ("AC", "APART", "raises"), ("AC", "AFAIL", "raises"),
    ("AC", "AOTHER", "raises"), ("AC", "AVAL", "returns StageCallResult"),
    ("AVAL", "AVERR", "invalid"), ("AVAL", "AID", "valid"),
    ("AID", "AIDERR", "no"), ("AID", "AEX", "yes"),
    ("AEX", "AEXERR", "disagrees"), ("AEX", "AOK", "None"),
    ("AFATAL", "AEXH", "no retry · retry_count=0"),
    ("APART", "ABACK", ""), ("AFAIL", "ABACK", ""), ("AOTHER", "ABACK", ""),
    ("AVERR", "ABACK", ""), ("AIDERR", "ABACK", ""), ("AEXERR", "ABACK", ""),
    ("ABACK", "AT", "attempts left"),
    ("ABACK", "AEXH", "attempts exhausted"),
    ("AEXH", "AREC", ""),
]

CALL_STAGE_NOTE = (
    "Every branch above -- success and failure alike -- appends exactly one StageAttempt "
    "to the attempt sink, so a call that succeeds on attempt 3 still records what "
    "attempts 1 and 2 did and what they cost. Only StageCallPartial and the branches "
    "after a returned StageCallResult carry token counts: the rest were rejected before "
    "inference, so there is nothing to charge."
)


def validate_call_stage() -> None:
    used_results = {r for _, _, _, r, _ in CALL_STAGE_NODES if r}
    all_results = {m.name for m in schemas.AttemptResult}
    if missing := sorted(all_results - used_results):
        raise SystemExit(f"call_stage.mermaid draws no branch for AttemptResult: {missing}")
    if unknown := sorted(used_results - all_results):
        raise SystemExit(f"call_stage.mermaid names non-existent AttemptResult: {unknown}")

    used_kinds = {k for _, _, _, _, k in CALL_STAGE_NODES if k}
    all_kinds = {m.name for m in schemas.FailureKind}
    if missing := sorted(all_kinds - used_kinds):
        raise SystemExit(f"call_stage.mermaid draws no branch for FailureKind: {missing}")
    if unknown := sorted(used_kinds - all_kinds):
        raise SystemExit(f"call_stage.mermaid names non-existent FailureKind: {unknown}")

    ids = {n for n, _, _, _, _ in CALL_STAGE_NODES}
    for a, b, _ in CALL_STAGE_EDGES:
        for n in (a, b):
            if n not in ids:
                raise SystemExit(f"CALL_STAGE_EDGES references undeclared node id {n!r}")


def build_call_stage() -> str:
    nodes: dict[str, tuple[str, str]] = {}
    for nid, label, kind, result, fkind in CALL_STAGE_NODES:
        tags = " · ".join(t for t in (result, fkind and f"kind={fkind}") if t)
        nodes[nid] = (f"{label}<br/><i>{tags}</i>" if tags else label, kind)
    nodes["ANOTE"] = (CALL_STAGE_NOTE.replace(". ", ".<br/>"), "note")
    edges = list(CALL_STAGE_EDGES)
    out = _render_flowchart(
        nodes, edges,
        "One stage call: retry, validation and per-attempt accounting inside "
        "pipeline.call_stage / call_document_stage. Every AttemptResult and FailureKind "
        "member must appear here or this build fails.",
        "TD")
    return out.replace("\n    class ANOTE note", "\n    class ANOTE note") + "    A0 -.- ANOTE\n"


# ===========================================================================
# 5. config_flow.mermaid -- YAML to run args to disk
# ===========================================================================

# (node id, label, kind, module to check, attribute to check)
CONFIG_NODES: list[tuple[str, str, str, str | None, str | None]] = [
    ("YAML", "run_config.yaml<br/><i>run_id · output_dir · max_revisions ·<br/>retry · rate_limits · defaults ·<br/>stages overrides · prompts</i>", "io", None, None),
    ("PROMPTS", "8 prompt files<br/><i>one per stage, by path</i>", "io", None, None),
    ("RC", "config.RunConfig<br/><i>shape only: keys known,<br/>prompts cover every stage</i>", "schema", "orchestrator.config", "RunConfig"),
    ("RES", "config.resolve_run_config<br/><i>defaults + overrides per stage ·<br/>output_mode capability check ·<br/>prompt file read and hashed</i>", "req", "orchestrator.config", "resolve_run_config"),
    ("RRC", "config.ResolvedRunConfig<br/><i>8 x ResolvedStageConfig ·<br/>rate_limits must match resolved models exactly</i>", "schema", "orchestrator.config", "ResolvedRunConfig"),
    ("CAP", "providers.capabilities.supports_output_mode<br/><i>no network, no key -- a table</i>", "provider", "orchestrator.providers.capabilities", "supports_output_mode"),
    ("META", "config.to_run_metadata<br/><i>-> schemas.RunMetadata,<br/>persisted with the record</i>", "req", "orchestrator.config", "to_run_metadata"),
    ("THR", "config.throttle_from<br/><i>rpm -> min seconds between calls,<br/>keyed by provider/model</i>", "req", "orchestrator.config", "throttle_from"),
    ("RETRY", "config.retry_args<br/><i>-> max_attempts, backoff_seconds(n)</i>", "req", "orchestrator.config", "retry_args"),
    ("RD", "config.run_dir_for<br/><i>output_dir / run_id, re-checked<br/>to be inside output_dir</i>", "req", "orchestrator.config", "run_dir_for"),
    ("RUNDOC", "pipeline.run_document(<br/>requirement_set, metadata, stage_fns,<br/>human_fns, throttle, max_revisions,<br/>run_dir, max_attempts, backoff_seconds)", "req", "orchestrator.pipeline", "run_document"),
    ("DIR", "&lt;output_dir&gt;/&lt;run_id&gt;/", "io", None, None),
    ("F1", "run_config.json<br/><i>the exact resolved config;<br/>a resume reloads this, not the YAML</i>", "io", None, None),
    ("F2", "document.json<br/><i>DocumentRunRecord with<br/>requirement_records = []</i>", "io", None, None),
    ("F3", "requirements/&lt;sha256 of id&gt;.json<br/><i>one RequirementRunRecord each;<br/>id is hashed so a hostile id<br/>cannot escape the directory</i>", "io", None, None),
    ("READ", "pipeline.read_document_run<br/><i>reassembles both halves,<br/>sorted by requirement.id</i>", "req", "orchestrator.pipeline", "read_document_run"),
]

CONFIG_EDGES = [
    ("YAML", "RC", "load_run_config"),
    ("RC", "RES", ""), ("PROMPTS", "RES", "read + sha256 -> prompt_hash"),
    ("RES", "CAP", "per stage, before any key is read"),
    ("RES", "RRC", ""),
    ("RRC", "META", ""), ("RRC", "THR", ""), ("RRC", "RETRY", ""), ("RRC", "RD", ""),
    ("META", "RUNDOC", ""), ("THR", "RUNDOC", ""), ("RETRY", "RUNDOC", ""), ("RD", "RUNDOC", ""),
    ("RD", "DIR", "must not already exist"),
    ("RRC", "F1", "write_run_config"),
    ("DIR", "F1", ""), ("DIR", "F2", ""), ("DIR", "F3", ""),
    ("RUNDOC", "F2", "write_document_run"),
    ("RUNDOC", "F3", "write_requirement_run"),
    ("F2", "READ", ""), ("F3", "READ", ""),
]


def validate_config_flow() -> None:
    for nid, _, _, mod_name, attr in CONFIG_NODES:
        if mod_name is None or attr is None:
            continue
        mod = importlib.import_module(mod_name)
        if not hasattr(mod, attr):
            raise SystemExit(
                f"config_flow.mermaid node {nid!r} names {mod_name}.{attr}, which no "
                "longer exists.")
    ids = {n for n, _, _, _, _ in CONFIG_NODES}
    for a, b, _ in CONFIG_EDGES:
        for n in (a, b):
            if n not in ids:
                raise SystemExit(f"CONFIG_EDGES references undeclared node id {n!r}")


def build_config_flow() -> str:
    nodes = {nid: (label, kind) for nid, label, kind, _, _ in CONFIG_NODES}
    return _render_flowchart(
        nodes, CONFIG_EDGES,
        "Configuration, from the YAML file to the arguments run_document is actually "
        "called with, to what a finished run leaves on disk. Everything left of "
        "run_document happens before any API key is read.",
        "TD")


# ===========================================================================
# 6. repo_map.mermaid -- filesystem walk + declared roles
# ===========================================================================

FILE_ROLES: dict[str, str] = {
    "CLAUDE.md": "project conventions and rules learned the hard way",
    "requirements.txt": "pinned deps: pydantic>=2.1 · requests · pyyaml",

    "design/schemas.py": "<b>the models.</b> every stage's input/output,<br/>the run records, RunOutcome/DocumentOutcome",
    "design/test_schemas.py": "the schema suite. also the best worked<br/>example of how the models fit together",
    "design/generate_diagrams.py": "generates the 5 schema/pipeline diagrams",
    "design/test_generate_diagrams.py": "suite for the diagram generator",
    "design/generate_arch_diagrams.py": "<b>this script.</b> generates the 6 architecture diagrams",
    "design/test_generate_arch_diagrams.py": "suite for this script's five validators<br/>(each mutation-tested)",
    "design/DESIGN_NOTES.md": "every decision, including the rejected ones",
    "design/ORCHESTRATOR_CONTRACT.md": "the 18 things the orchestrator must do<br/>that the schema deliberately does not enforce",
    "design/SCHEMA_AUDIT_CHECKLIST.md": "the eight lenses used to find schema gaps",
    "design/DIAGRAMS.md": "index of all 13 diagrams",

    "orchestrator/pipeline.py": "<b>control flow.</b> stage sequencing, retry,<br/>revision cap, resume, persistence",
    "orchestrator/stage_fns.py": "the typed slots: StageFns · HumanFns ·<br/>10 Protocols · the 3 stage_fn exceptions",
    "orchestrator/stages.py": "the 8 real stage functions: prompt rendering,<br/>JSON extraction, provider call",
    "orchestrator/config.py": "YAML RunConfig -> ResolvedRunConfig -> run args",
    "orchestrator/human_cli.py": "terminal HumanFns: answer questions, decide at the cap",
    "orchestrator/cli.py": "the entrypoint: run/resume subcommands",
    "orchestrator/extract_document.py": "pulls one doc_id out of a list-of-documents<br/>JSON file into a standalone RequirementSet",
    "orchestrator/test_extract_document.py": "extract_document suite",
    "orchestrator/example_run_config.yaml": "a real, loadable, resolvable config",
    "orchestrator/providers/base.py": "ProviderAdapter Protocol · CompletionResult",
    "orchestrator/providers/capabilities.py": "which model supports which output_mode.<br/>dated, cited, best-effort",
    "orchestrator/providers/gemini.py": "Gemini REST + error classification",
    "orchestrator/providers/groq.py": "Groq REST + error classification",
    "orchestrator/test_harness.py": "the big one: control flow, resume,<br/>cap, degraded runs, per-attempt records",
    "orchestrator/test_config.py": "config resolution suite",
    "orchestrator/test_stages.py": "stage-function suite",
    "orchestrator/test_stage_fns.py": "Protocol/signature suite",
    "orchestrator/test_providers.py": "adapter suite",
    "orchestrator/test_cli.py": "CLI suite: exit codes, wiring, failure paths",
    "orchestrator/test_human_cli.py": "terminal HumanFns suite",
}

DIR_ROLES: dict[str, str] = {
    "design": "schema layer + all diagram generation",
    "design/diagrams": "the 11 generated .mermaid files -- never hand-edited",
    "orchestrator": "runtime layer: the code that actually runs",
    "orchestrator/providers": "one adapter per LLM provider",
    "orchestrator/example_prompts": "8 prompt templates, one per stage",
    "datasets": "evaluation corpora, reserved for the evaluation phase",
    "datasets/requirements-xml": "18 requirement documents (XML)",
    "docs": "plans and design specs",
    "docs/superpowers/plans": "work plans, incl. the first-real-run checklist",
    "docs/superpowers/specs": "design specs for the orchestrator changes",
    "papers": "reference literature (PDF), optional",
}

# Files that exist but need no individual box.
ROLE_EXEMPT_SUFFIXES = (".mermaid", ".pyc")
ROLE_EXEMPT_NAMES = ("__init__.py",)


def validate_repo_map() -> None:
    for rel in FILE_ROLES:
        if not (REPO_ROOT / rel).exists():
            raise SystemExit(f"FILE_ROLES describes a file that does not exist: {rel}")
    for rel in DIR_ROLES:
        if not (REPO_ROOT / rel).is_dir():
            raise SystemExit(f"DIR_ROLES describes a directory that does not exist: {rel}")

    # The direction that matters: a new module with no described role.
    undescribed = []
    for pkg in INTERNAL_PACKAGES:
        for path in sorted((REPO_ROOT / pkg).rglob("*.py")):
            if "__pycache__" in path.parts or path.name in ROLE_EXEMPT_NAMES:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel not in FILE_ROLES:
                undescribed.append(rel)
    if undescribed:
        raise SystemExit(
            "repo_map.mermaid has no role for these module(s): "
            f"{undescribed}\nAdd them to FILE_ROLES.")


def _count(pattern: str, root: Path) -> int:
    return len([p for p in root.glob(pattern) if p.is_file()])


def _nearest_declared_parent(directory: str) -> str:
    """The closest ancestor that has a DIR_ROLES entry, else the repo root.

    docs/superpowers/plans' literal parent (docs/superpowers) is not described -- it
    holds nothing but the two subdirectories. Walking up, rather than testing only the
    immediate parent, keeps it hanging off docs/ instead of silently reparenting to the
    repo root and drawing a tree that isn't the real one.
    """
    parts = Path(directory).parts
    for cut in range(len(parts) - 1, 0, -1):
        candidate = "/".join(parts[:cut])
        if candidate in DIR_ROLES:
            return _sid(f"dir_{candidate}")
    return _sid("dir_root")


def build_repo_map() -> str:
    nodes: dict[str, tuple[str, str]] = {}
    edges: list[tuple[str, str, str]] = []
    subgraphs: list[tuple[str, list[str]]] = []

    def kind_for(rel: str) -> str:
        name = Path(rel).name
        if name.startswith("test_"):
            return "test"
        if rel.startswith("design/") and rel.endswith(".md"):
            return "note"
        if rel.startswith("design/"):
            return "schema"
        if "providers" in rel:
            return "provider"
        if rel.endswith((".yaml", ".md", ".txt")):
            return "io"
        return "req"

    def file_count(directory: str) -> int:
        return len([p for p in (REPO_ROOT / directory).rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts])

    members: dict[str, list[str]] = {d: [] for d in DIR_ROLES}
    for rel, blurb in FILE_ROLES.items():
        fid = _sid(f"f_{rel}")
        nodes[fid] = (f"<b>{Path(rel).name}</b><br/><i>{blurb}</i>", kind_for(rel))
        members.setdefault(str(Path(rel).parent.as_posix()), []).append(fid)

    nodes[_sid("dir_root")] = (
        f"<b>repo root</b><br/><i>Masters_2</i><br/>"
        f"{len(list(REPO_ROOT.glob('*')))} top-level entries", "decision")
    for fid in members.get(".", []):
        edges.append((_sid("dir_root"), fid, ""))

    # A directory with described files becomes a subgraph (its parent points at the
    # subgraph itself); one without becomes a plain box. Doing both -- a box AND a
    # subgraph of the same name -- was the first draft, and just said everything twice.
    box_id: dict[str, str] = {}
    for directory, blurb in DIR_ROLES.items():
        title = f"{directory}/ -- {blurb} -- {file_count(directory)} files"
        if members.get(directory):
            subgraphs.append((title, members[directory]))
            box_id[directory] = _sid(title)
        else:
            box_id[directory] = _sid(f"dir_{directory}")
            nodes[box_id[directory]] = (
                f"<b>{directory}/</b><br/><i>{blurb}</i><br/>{file_count(directory)} files",
                "decision")
    for directory in DIR_ROLES:
        edges.append((_nearest_declared_parent(directory), box_id[directory], ""))

    # Two groups counted rather than listed file by file.
    nodes["PROMPTS"] = (
        f"<b>example_prompts/*.txt</b><br/><i>one per stage</i><br/>"
        f"{_count('*.txt', REPO_ROOT / 'orchestrator' / 'example_prompts')} files", "io")
    edges.append((box_id["orchestrator/example_prompts"], "PROMPTS", ""))
    nodes["MERM"] = (
        f"<b>design/diagrams/*.mermaid</b><br/><i>generated, never hand-edited</i><br/>"
        f"{_count('*.mermaid', OUT_DIR)} files", "term")
    edges.append((box_id["design/diagrams"], "MERM", ""))

    return _render_flowchart(
        nodes, edges,
        "Every directory and every module, with what each is for. File counts and the "
        "tree are read from disk; the one-line roles are declared, and a .py module with "
        "no declared role fails this build.",
        "LR", subgraphs)


# ===========================================================================
# 7. overview.mermaid -- the whole project in one picture, six boxes
# ===========================================================================
# Everything else in design/diagrams/ picks one layer and shows it in full. This one
# picks none of them -- it's the "what even is this project" diagram, for a first look
# or a thesis defense slide, not for finding a bug. If a box here needs a sub-box to
# make sense, that sub-box belongs in one of the other eleven diagrams, not here.
#
# The two stage counts are the only introspected part -- pulled from
# len(schemas.DocumentStage)/len(schemas.PipelineStage) so adding a stage changes the
# number without anyone remembering to edit a caption.

OVERVIEW_NODES: dict[str, tuple[str, str]] = {
    "IN":  ("Requirement document (JSON)<br/>+ run config (YAML)", "io"),
    "DOC": ("Document-level checks<br/><i>consistency · dependencies<br/>"
           "{doc_n} stages, once per document</i>", "doc"),
    "REQ": ("Per-requirement pipeline<br/><i>classify → check quality →<br/>"
           "refine (loop) → select strategy → generate tests<br/>"
           "{req_n} stages, repeated for every requirement</i>", "req"),
    "HUM": ("Human<br/><i>answers refiner questions ·<br/>decides at the revision cap</i>", "human"),
    "LLM": ("LLM providers<br/><i>Gemini · Groq</i>", "provider"),
    "OUT": ("Run record<br/><i>JSON on disk · resumable</i>", "term"),
}

OVERVIEW_EDGES: list[tuple[str, str, str]] = [
    ("IN", "DOC", ""),
    ("IN", "REQ", ""),
    ("DOC", "REQ", "conflicts · dependencies"),
    ("DOC", "LLM", ""),
    ("REQ", "LLM", ""),
    ("REQ", "HUM", "questions · cap decision"),
    ("HUM", "REQ", "answers"),
    ("DOC", "OUT", ""),
    ("REQ", "OUT", ""),
]


def build_overview() -> str:
    nodes = {
        nid: (label.format(doc_n=len(schemas.DocumentStage), req_n=len(schemas.PipelineStage)), kind)
        for nid, (label, kind) in OVERVIEW_NODES.items()
    }
    return _render_flowchart(
        nodes, OVERVIEW_EDGES,
        "The whole project in one picture. For any real detail, see the other eleven "
        "diagrams in design/diagrams/ -- this one is deliberately too small to debug from.",
        "TD")


# ===========================================================================
# 8. stage_flow.mermaid -- the 8 LLM stages, happy path only
# ===========================================================================
# stage_wiring.mermaid shows all 8 stages' configuration in full; paths_requirement.mermaid
# shows every branch, including the revision cap. This is neither -- it is what actually
# happens on a normal run: each stage in order, plus the one branch you cannot omit and
# still call it "the flow" (did the quality check pass?). No config, no retries, no cap,
# no notes.
#
# The one part of this that is worth getting wrong is exactly the part
# generate_diagrams.py's pipeline.mermaid DID get wrong: which per-requirement stage
# receives ConsistencyReport/DependencyReport. That file hand-declares CC/DM -> Classifier
# and was never updated when the 2026-08-08 document-context-wiring change moved that
# context onto CheckQualityFn/SelectStrategyFn/GenerateTestsFn instead (see
# docs/superpowers/specs/2026-08-08-document-context-wiring-design.md). Declaring the same
# thing by hand a second time would just be a second place to go stale. Instead,
# validate_stage_flow reads the REAL parameter names off the StageFns Protocols in
# orchestrator/stage_fns.py with inspect.signature -- if a document-context parameter is
# ever added, removed, or moved to a different stage, this fails instead of drawing a
# diagram that quietly disagrees with the code, the way pipeline.mermaid now does.

STAGE_FLOW_NODES: dict[str, tuple[str, str]] = {
    "IN":  ("Requirement Document", "io"),
    "CC":  ("Consistency Checker", "doc"),
    "DM":  ("Dependency Mapper", "doc"),
    "CL":  ("Classifier", "req"),
    "QC":  ("Quality Checker", "req"),
    "PASS": ("Quality passed?", "decision"),
    "RQ":  ("Refiner: Ask Questions", "req"),
    "HUM": ("Human Answers", "human"),
    "RW":  ("Refiner: Rewrite", "req"),
    "S3":  ("Strategy Selector", "req"),
    "S4":  ("Test Case Generator", "req"),
    "OUT": ("Test Cases", "term"),
}

# A 4th element is an edge style: "dotted" for the document-level reports quietly
# feeding a per-requirement stage, versus the solid main flow every reader follows
# top to bottom. Distinguishing them was the actual fix for "confusing" -- the four
# solid crossing lines (CC/DM into three different downstream boxes) fought the main
# flow for attention; dotted, they read as background context instead.
STAGE_FLOW_EDGES: list[tuple] = [
    ("IN", "CC", ""),
    ("IN", "DM", ""),
    ("IN", "CL", ""),
    ("CL", "QC", ""),
    ("CC", "QC", "conflicts", "dotted"),
    ("DM", "QC", "dependencies", "dotted"),
    ("QC", "PASS", ""),
    ("PASS", "S3", "yes"),
    ("PASS", "RQ", "no"),
    ("RQ", "HUM", ""),
    ("HUM", "RW", ""),
    ("RW", "QC", "try again"),
    ("DM", "S3", "dependencies", "dotted"),
    ("DM", "S4", "dependencies", "dotted"),
    ("S3", "S4", ""),
    ("S4", "OUT", ""),
]

# (StageFns Protocol name, parameter names it must have, parameter names it must NOT have)
_DOC_CONTEXT_PROTOCOLS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("ClassifyFn", (), ("relevant_conflicts", "relevant_dependencies")),
    ("CheckQualityFn", ("relevant_conflicts", "relevant_dependencies"), ()),
    ("SelectStrategyFn", ("relevant_dependencies",), ("relevant_conflicts",)),
    ("GenerateTestsFn", ("relevant_dependencies",), ("relevant_conflicts",)),
]


def validate_stage_flow() -> None:
    for name, must_have, must_not_have in _DOC_CONTEXT_PROTOCOLS:
        protocol = getattr(stage_fns_mod, name, None)
        if protocol is None:
            raise SystemExit(f"stage_flow.mermaid: orchestrator.stage_fns has no {name!r}")
        params = set(inspect.signature(protocol.__call__).parameters)
        if missing := [p for p in must_have if p not in params]:
            raise SystemExit(
                f"stage_flow.mermaid: {name}.__call__ no longer has parameter(s) "
                f"{missing} -- the diagram's CC/DM edges assume it does. Update "
                "STAGE_FLOW_EDGES to match the real signature.")
        if extra := [p for p in must_not_have if p in params]:
            raise SystemExit(
                f"stage_flow.mermaid: {name}.__call__ now has parameter(s) {extra}, which "
                "the diagram draws as NOT receiving document context. Update "
                "STAGE_FLOW_EDGES.")

    ids = set(STAGE_FLOW_NODES)
    for edge in STAGE_FLOW_EDGES:
        for n in (edge[0], edge[1]):
            if n not in ids:
                raise SystemExit(f"STAGE_FLOW_EDGES references undeclared node id {n!r}")


def build_stage_flow() -> str:
    nodes = dict(STAGE_FLOW_NODES)
    # Grouped into two subgraphs, captioned in plain English, so a first-time reader
    # gets "these two run once, these six run per requirement" from the picture itself
    # instead of having to infer it from colour alone.
    subgraphs = [
        ("Runs once per document", ["CC", "DM"]),
        ("Runs once per requirement -- repeated for every requirement in the document",
         ["CL", "QC", "PASS", "RQ", "HUM", "RW", "S3", "S4"]),
    ]
    return _render_flowchart(
        nodes, STAGE_FLOW_EDGES,
        "The 8 LLM stages, normal flow only: happy-path order plus the one branch you "
        "can't drop and still call it a flow (did the quality check pass?). Dotted "
        "arrows are a document-level report being read, not the main sequence. No "
        "config, no retries, no revision cap -- see stage_wiring.mermaid and "
        "paths_requirement.mermaid for those.",
        "TD", subgraphs)


# ===========================================================================

def main() -> None:
    validate_stage_wiring()
    validate_runtime()
    validate_call_stage()
    validate_config_flow()
    validate_repo_map()
    validate_stage_flow()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for fname, content in (
            ("overview.mermaid", build_overview()),
            ("stage_flow.mermaid", build_stage_flow()),
            ("modules.mermaid", build_modules()),
            ("stage_wiring.mermaid", build_stage_wiring()),
            ("runtime.mermaid", build_runtime()),
            ("call_stage.mermaid", build_call_stage()),
            ("config_flow.mermaid", build_config_flow()),
            ("repo_map.mermaid", build_repo_map()),
    ):
        (OUT_DIR / fname).write_text(content, encoding="utf-8")
        print(f"wrote {fname} ({len(content.splitlines())} lines)")


if __name__ == "__main__":
    main()
