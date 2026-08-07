"""
Generates two Mermaid diagrams from `schemas.py`. Run after every schema change:

    python design/generate_diagrams.py

Outputs (both overwritten in place, both plain text so git diffs show what changed):

    design/pipeline.mermaid   -- how the pipeline runs: stages 0-4 as boxes, with the
                                 schema type carried along each arrow.
    design/models.mermaid     -- how the data nests: every model and enum with its
                                 fields, plus containment/reference arrows.

--------------------------------------------------------------------------------
WHY THIS IS PART-INTROSPECTED AND PART-DECLARED (a deliberate trade-off)
--------------------------------------------------------------------------------
`models.mermaid` is fully introspected -- it reads `schemas.py` at runtime via
Pydantic's `model_fields`, so it cannot drift. Add a field, rerun, it appears.

`pipeline.mermaid` cannot be. Execution *order* is not recorded anywhere in the
Pydantic models: nothing in `QualityReport` says it runs after `Classification`.
That ordering lives in the orchestrator, which does not exist yet. So the stage
graph below is declared by hand in PIPELINE_NODES / PIPELINE_EDGES.

To stop that hand-written part from silently going stale, every schema type named
in the declaration is checked against `schemas.py` at generation time, and the
script fails loudly if one is renamed or deleted. That catches renames and removals
-- it does NOT catch a stage whose real input type changes to another type that also
still exists. That residual gap is accepted rather than papered over; closing it
properly means generating the diagram from the orchestrator's actual call graph,
which is worth revisiting once the orchestrator is written.
"""

from __future__ import annotations

import types
import typing
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

import design.schemas as schemas

OUT_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Pipeline declaration -- the hand-written part, validated below.
# `id: (label, stage_kind)`; stage_kind drives colour only.
# ---------------------------------------------------------------------------

PIPELINE_NODES: dict[str, tuple[str, str]] = {
    "IN":   ("0 · Input<br/><i>full requirement document</i>", "io"),
    "CC":   ("1 · Consistency Checker<br/><i>runs once per document</i>", "doc"),
    "DM":   ("1b · Dependency Mapper<br/><i>runs once per document</i>", "doc"),
    "CL":   ("2a · Classifier<br/><i>whole doc as context</i>", "req"),
    "QC":   ("2b · Quality Checker", "req"),
    "PASS": ("passed?", "decision"),
    "RF":   ("2c · Refiner<br/><i>human in the loop</i>", "human"),
    "HUM":  ("Human answers", "human"),
    "CAP":  ("revision cap hit?", "decision"),
    "ASK":  ("Ask human:<br/>generate anyway, or stop?", "human"),
    "S3":   ("3 · Test Design Strategy Selector", "req"),
    "S4":   ("4 · Test Case Generator", "req"),
    "STOP": ("outcome = CAP_STOPPED<br/><i>no test plan produced</i>", "stop"),
    "REC":  ("RequirementRunRecord<br/><i>one file per requirement</i>", "io"),
    "DOC":  ("DocumentRunRecord<br/><i>document.json — doc-level stages + set</i>", "io"),
}

# (from, to, carried_schema_type_or_None, edge_label_suffix)
PIPELINE_EDGES: list[tuple[str, str, str | None, str]] = [
    ("IN",   "CC",   "RequirementSet",      ""),
    ("IN",   "DM",   "RequirementSet",      ""),
    ("IN",   "DOC",  "RequirementSet",      ""),
    ("CC",   "CL",   "ConsistencyReport",   "or None if it failed · DEGRADED"),
    ("DM",   "CL",   "DependencyReport",    "or None if it failed · DEGRADED"),
    ("CC",   "DOC",  "ConsistencyReport",   ""),
    ("DM",   "DOC",  "DependencyReport",    ""),
    ("IN",   "CL",   "Requirement",         "one at a time"),
    ("CL",   "QC",   "Classification",      ""),
    ("QC",   "PASS", "QualityReport",       ""),
    ("PASS", "RF",   None,                  "no · issues found"),
    ("RF",   "HUM",  "RefinerTurn",         "questions out"),
    ("HUM",  "RF",   "RefinerAnswer",       "answers in"),
    ("RF",   "CAP",  "RefinedRequirement",  ""),
    ("CAP",  "QC",   None,                  "no · re-check rewritten text"),
    ("CAP",  "ASK",  None,                  "yes · issues outstanding"),
    ("ASK",  "S3",   "Requirement",         "generate anyway · CAP_GENERATED"),
    ("ASK",  "STOP", None,                  "stop"),
    ("PASS", "S3",   "Requirement",         "yes · COMPLETED"),
    ("S3",   "S4",   "TestStrategy",        ""),
    ("S4",   "REC",  "TestPlan",            ""),
    ("STOP", "REC",  None,                  ""),
]

# Rendered as a floating note attached to a node.
PIPELINE_NOTES: list[tuple[str, str]] = [
    ("PASS",
     "Both branches converge on a plain <b>Requirement</b>.<br/>"
     "Clean requirements skip the Refiner entirely and pass<br/>"
     "the original object through; refined ones are rebuilt as<br/>"
     "Requirement(id, text=refined.refined_text). Stages 3/4<br/>"
     "never need to know which branch ran."),
    ("RF",
     "RefinedRequirement is an <b>audit record</b> of what changed<br/>"
     "and why -- not the pipeline's transport type."),
    ("ASK",
     "Second human interaction point. The answer is stored as the<br/>"
     "outcome (CAP_GENERATED / CAP_STOPPED) plus an optional<br/>"
     "cap_reason, so the decision lives in the data rather than<br/>"
     "only in whoever was at the keyboard."),
    ("REC",
     "Any stage above can fail (free-tier rate limits).<br/>"
     "The record is created at outcome=IN_PROGRESS before<br/>"
     "stage 2a and written incrementally, so a failure stores<br/>"
     "outcome=ERROR + StageError instead of losing the record,<br/>"
     "and an interrupted run can resume."),
    ("DOC",
     "If either document-level stage fails, processing<br/>"
     "<b>continues without it</b> (decision D1b) and the document<br/>"
     "is marked DEGRADED with a DocumentStageError. Both can<br/>"
     "fail independently, so errors is a list.<br/><br/>"
     "On disk (decision D2b): document.json holds an empty<br/>"
     "requirement_records list; each requirement is its own file.<br/>"
     "pending_requirement_ids is derived, and drives resume."),
]

# ---------------------------------------------------------------------------
# Path trees -- every route a document or a requirement can take, end to end.
#
# Split into three because one tree covering the loop, both cap branches and five
# failure points is unreadable. Each declares its own terminal outcomes; a check below
# confirms that between them they cover every RunOutcome and DocumentOutcome value, so
# adding an outcome to the schema without drawing its path fails the build.
# ---------------------------------------------------------------------------

# (from, to, edge label, kind)  -- kind drives colour: "" normal, "term" terminal
DOCUMENT_PATHS = [
    ("D0",  "DCC", "", ""),
    ("DCC", "DDM", "succeeded", ""),
    ("DCC", "DDMx", "failed after retries", ""),
    ("DDM", "DOK", "succeeded", ""),
    ("DDM", "DDEG1", "failed after retries", ""),
    ("DDMx", "DDEG2", "succeeded", ""),
    ("DDMx", "DDEG3", "failed after retries", ""),
]
DOCUMENT_NODES = {
    "D0":    ('0 · RequirementSet loaded<br/><i>outcome = IN_PROGRESS</i>', "io"),
    "DCC":   ('1 · Consistency Checker', "doc"),
    "DDM":   ('1b · Dependency Mapper', "doc"),
    "DDMx":  ('1b · Dependency Mapper<br/><i>consistency report absent</i>', "doc"),
    "DOK":   ('<b>COMPLETED</b><br/>both reports present<br/>per-requirement processing begins', "term"),
    "DDEG1": ('<b>DEGRADED</b><br/>dependency report missing<br/><i>stages 3/4 run without dependency context</i>', "term"),
    "DDEG2": ('<b>DEGRADED</b><br/>consistency report missing<br/><i>Quality Checker inherits no conflict flags</i>', "term"),
    "DDEG3": ('<b>DEGRADED</b><br/>both missing<br/><i>errors is a list for exactly this case</i>', "term"),
}
DOCUMENT_TERMINALS = {"DOK": "completed", "DDEG1": "degraded",
                      "DDEG2": "degraded", "DDEG3": "degraded"}

REQUIREMENT_PATHS = [
    ("R0",  "RCL", "", ""),
    ("RCL", "RQC", "", ""),
    ("RQC", "RQ",  "", ""),
    ("RQ",  "R34", "passed", ""),
    ("RQ",  "RRF", "failed · issues raised", ""),
    ("RRF", "RHU", "RefinerTurn · questions", ""),
    ("RHU", "RRW", "RefinerAnswer · answers", ""),
    ("RHU", "RSUP", "answer marked<br/>user_confirms_resolved", ""),
    ("RSUP", "RRW", "", ""),
    ("RRW", "RCAP", "", ""),
    ("RCAP", "RQC", "no · next round checks the rewrite", ""),
    ("RCAP", "RASK", "yes · issues outstanding", ""),
    ("RASK", "R34b", "generate anyway", ""),
    ("RASK", "RSTOP", "stop", ""),
    ("R34", "RDONE", "", ""),
    ("R34b", "RGEN", "", ""),
]
REQUIREMENT_NODES = {
    "R0":    ('Requirement picked up<br/><i>record created, outcome = IN_PROGRESS</i>', "io"),
    "RCL":   ('2a · Classifier', "req"),
    "RQC":   ('2b · Quality Checker<br/><i>new RefinementRound</i>', "req"),
    "RQ":    ('passed?', "decision"),
    "RRF":   ('2c · Refiner', "human"),
    "RHU":   ('Human answers', "human"),
    "RSUP":  ('id added to suppressed_issue_ids<br/><i>carried forward every later round</i>', "human"),
    "RRW":   ('Rewrite<br/><i>becomes the next round\'s text_checked</i>', "req"),
    "RCAP":  ('revision cap reached?', "decision"),
    "RASK":  ('Ask human:<br/>generate anyway, or stop?', "human"),
    "R34":   ('3 · Strategy Selector<br/>4 · Test Case Generator', "req"),
    "R34b":  ('3 · Strategy Selector<br/>4 · Test Case Generator<br/><i>on the best-effort text</i>', "req"),
    "RDONE": ('<b>COMPLETED</b><br/><i>last round passed</i>', "term"),
    "RGEN":  ('<b>CAP_GENERATED</b><br/>test plan produced<br/><i>from a requirement still failing checks</i>', "term"),
    "RSTOP": ('<b>CAP_STOPPED</b><br/><i>no strategy, no test plan</i>', "term"),
}
REQUIREMENT_TERMINALS = {"RDONE": "completed", "RGEN": "cap_generated", "RSTOP": "cap_stopped"}

FAILURE_PATHS = [
    ("F0", "FRETRY", "any stage raises", ""),
    ("FRETRY", "FOK", "a retry succeeds", ""),
    ("FRETRY", "FERR", "retries exhausted", ""),
    ("FOK", "FGONE", "", ""),
    ("F0", "FKILL", "process dies / interrupted", ""),
    ("FKILL", "FIP", "", ""),
    ("FIP", "FRES", "", ""),
]
FAILURE_NODES = {
    "F0":     ('Any per-requirement stage<br/><i>classifier · quality_checker · refiner<br/>strategy_selector · test_generator</i>', "req"),
    "FRETRY": ('Retry with backoff<br/><i>free-tier rate limits are the normal case</i>', "req"),
    "FOK":    ('Continue normally', "req"),
    "FGONE":  ('<i>no trace kept</i><br/>a successful retry is not recorded --<br/>retry_count only ever describes a call that FAILED', "note"),
    "FERR":   ('<b>ERROR</b><br/>StageError: stage · message · retry_count<br/><i>partial record still persisted</i>', "term"),
    "FKILL":  ('Record already on disk<br/><i>written incrementally</i>', "io"),
    "FIP":    ('<b>IN_PROGRESS</b><br/><i>not a failure -- the resume marker</i>', "term"),
    "FRES":   ('Resume position derived from<br/>which fields are populated<br/><i>no stored pointer</i>', "io"),
}
FAILURE_TERMINALS = {"FERR": "error", "FIP": "in_progress"}

STAGE_STYLES = {
    "io":       "fill:#e8eaf6,stroke:#3f51b5,color:#1a237e",
    "doc":      "fill:#e0f2f1,stroke:#00897b,color:#004d40",
    "req":      "fill:#fff8e1,stroke:#f9a825,color:#e65100",
    "human":    "fill:#fce4ec,stroke:#c2185b,color:#880e4f",
    "decision": "fill:#f5f5f5,stroke:#616161,color:#212121",
    "stop":     "fill:#ffebee,stroke:#c62828,color:#b71c1c",
    "term":     "fill:#ede7f6,stroke:#5e35b1,color:#311b92",
}


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------

def schema_models() -> dict[str, type[BaseModel]]:
    return {
        n: o for n, o in vars(schemas).items()
        if isinstance(o, type) and issubclass(o, BaseModel) and o is not BaseModel
        and o.__module__ == schemas.__name__
    }


def schema_enums() -> dict[str, type[Enum]]:
    return {
        n: o for n, o in vars(schemas).items()
        if isinstance(o, type) and issubclass(o, Enum) and o.__module__ == schemas.__name__
    }


def render_type(ann: typing.Any) -> str:
    """Human-readable name for a type annotation, Mermaid-safe (no [] or |)."""
    if ann is type(None):
        return "None"
    origin = typing.get_origin(ann)
    args = typing.get_args(ann)
    if origin in (typing.Union, types.UnionType):
        inner = [a for a in args if a is not type(None)]
        rendered = "/".join(render_type(a) for a in inner)
        return f"Optional~{rendered}~" if len(inner) < len(args) else rendered
    if origin in (list, set, tuple):
        return f"{origin.__name__}~{','.join(render_type(a) for a in args)}~"
    if origin is dict:
        return f"dict~{','.join(render_type(a) for a in args)}~"
    return getattr(ann, "__name__", str(ann))


def referenced_names(ann: typing.Any) -> set[str]:
    """Every named type appearing anywhere inside an annotation."""
    found: set[str] = set()
    stack = [ann]
    while stack:
        cur = stack.pop()
        args = typing.get_args(cur)
        if args:
            stack.extend(args)
        else:
            name = getattr(cur, "__name__", None)
            if name:
                found.add(name)
    return found


def is_collection(ann: typing.Any) -> bool:
    stack = [ann]
    while stack:
        cur = stack.pop()
        if typing.get_origin(cur) in (list, set, tuple):
            return True
        stack.extend(typing.get_args(cur))
    return False


def public_methods(model: type[BaseModel]) -> list[str]:
    out = []
    for name, obj in vars(model).items():
        if name.startswith("_") or not callable(obj):
            continue
        if getattr(obj, "__pydantic_validator_config__", None) is not None:
            continue
        out.append(name)
    return sorted(out)


# ---------------------------------------------------------------------------
# Validation of the declared pipeline against the real schema
# ---------------------------------------------------------------------------

def validate_pipeline(models: dict[str, type[BaseModel]]) -> None:
    declared = {t for _, _, t, _ in PIPELINE_EDGES if t}
    missing = sorted(declared - set(models))
    if missing:
        raise SystemExit(
            f"pipeline.mermaid declares schema type(s) that no longer exist in "
            f"schemas.py: {missing}\nRename or remove them in PIPELINE_EDGES."
        )
    node_ids = set(PIPELINE_NODES)
    for a, b, _, _ in PIPELINE_EDGES:
        for n in (a, b):
            if n not in node_ids:
                raise SystemExit(f"PIPELINE_EDGES references undeclared node id: {n!r}")
    for n, _ in PIPELINE_NOTES:
        if n not in node_ids:
            raise SystemExit(f"PIPELINE_NOTES references undeclared node id: {n!r}")


# ---------------------------------------------------------------------------
# Diagram builders
# ---------------------------------------------------------------------------

HEADER = "%% GENERATED by design/generate_diagrams.py -- do not edit by hand.\n%% Rerun after every change to schemas.py.\n"


def build_pipeline() -> str:
    lines = [HEADER, "flowchart TD"]
    for nid, (label, kind) in PIPELINE_NODES.items():
        shape = f'{nid}{{"{label}"}}' if kind == "decision" else f'{nid}["{label}"]'
        lines.append(f"    {shape}")
    lines.append("")
    for a, b, carried, suffix in PIPELINE_EDGES:
        parts = [p for p in (carried, suffix) if p]
        label = f'|"{" · ".join(parts)}"|' if parts else ""
        arrow = "-.->" if (a, b) == ("HUM", "RF") else "-->"
        lines.append(f"    {a} {arrow}{label} {b}")
    lines.append("")
    for i, (anchor, text) in enumerate(PIPELINE_NOTES):
        nid = f"NOTE{i}"
        lines.append(f'    {nid}["{text}"]')
        lines.append(f"    {anchor} -.- {nid}")
        lines.append(f"    class {nid} note")
    lines.append("")
    for kind, style in STAGE_STYLES.items():
        ids = [n for n, (_, k) in PIPELINE_NODES.items() if k == kind]
        if ids:
            lines.append(f"    classDef {kind} {style}")
            lines.append(f"    class {','.join(ids)} {kind}")
    lines.append("    classDef note fill:#ffffff,stroke:#9e9e9e,color:#424242,stroke-dasharray:3 3")
    return "\n".join(lines) + "\n"


def build_models() -> str:
    models, enums = schema_models(), schema_enums()
    known = set(models) | set(enums)
    lines = [HEADER, "classDiagram", "    direction TB"]

    for name, enum_cls in enums.items():
        lines.append(f"    class {name} {{")
        lines.append("        <<enumeration>>")
        for member in enum_cls:
            lines.append(f"        {member.value}")
        lines.append("    }")

    edges: list[str] = []
    for name, model in models.items():
        lines.append(f"    class {name} {{")
        for fname, field in model.model_fields.items():
            required = "*" if field.is_required() else ""
            lines.append(f"        +{render_type(field.annotation)} {fname}{required}")
        for fname, cfield in getattr(model, "model_computed_fields", {}).items():
            lines.append(f"        +{render_type(cfield.return_type)} {fname} $computed$")
        for meth in public_methods(model):
            lines.append(f"        +{meth}()")
        lines.append("    }")

        for fname, field in model.model_fields.items():
            for ref in referenced_names(field.annotation) & known:
                card = '"1..*"' if is_collection(field.annotation) else '"1"'
                link = "*--" if ref in models else "-->"
                edges.append(f'    {name} {card} {link} {ref} : {fname}')

    lines.append("")
    lines.extend(sorted(set(edges)))
    lines.append("")
    lines.append("    note \"* = required field · $computed$ = derived, not stored (Pydantic @computed_field)\"")
    return "\n".join(lines) + "\n"


def build_path_tree(nodes: dict, edges: list, title: str, direction: str = "TD") -> str:
    lines = [HEADER, f"%% {title}", f"flowchart {direction}"]
    for nid, (label, kind) in nodes.items():
        shape = f'{nid}{{"{label}"}}' if kind == "decision" else f'{nid}["{label}"]'
        lines.append(f"    {shape}")
    lines.append("")
    for a, b, label, _ in edges:
        arrow = f'-->|"{label}"|' if label else "-->"
        lines.append(f"    {a} {arrow} {b}")
    lines.append("")
    for kind, style in STAGE_STYLES.items():
        ids = [n for n, (_, k) in nodes.items() if k == kind]
        if ids:
            lines.append(f"    classDef {kind} {style}")
            lines.append(f"    class {','.join(ids)} {kind}")
    lines.append("    classDef note fill:#ffffff,stroke:#9e9e9e,color:#424242,stroke-dasharray:3 3")
    return "\n".join(lines) + "\n"


def validate_path_trees() -> None:
    """Every outcome the schema can express must have a drawn path.

    The trees themselves are declared by hand (execution order is not in the models --
    same reason pipeline.mermaid is). What *is* checkable is the leaves: if a RunOutcome
    or DocumentOutcome is added and no path ends in it, this fails rather than quietly
    producing an incomplete picture.
    """
    drawn_req = set(REQUIREMENT_TERMINALS.values()) | set(FAILURE_TERMINALS.values())
    expected_req = {o.value for o in schemas.RunOutcome}
    if missing := sorted(expected_req - drawn_req):
        raise SystemExit(f"RunOutcome value(s) with no path drawn: {missing}")
    if unknown := sorted(drawn_req - expected_req):
        raise SystemExit(f"path tree ends in non-existent RunOutcome(s): {unknown}")

    drawn_doc = set(DOCUMENT_TERMINALS.values())
    expected_doc = {o.value for o in schemas.DocumentOutcome}
    # in_progress is the document's starting state, drawn as the root rather than a leaf
    expected_doc.discard("in_progress")
    if missing := sorted(expected_doc - drawn_doc):
        raise SystemExit(f"DocumentOutcome value(s) with no path drawn: {missing}")
    if unknown := sorted(drawn_doc - expected_doc):
        raise SystemExit(f"path tree ends in non-existent DocumentOutcome(s): {unknown}")

    for nodes, edges, name in ((DOCUMENT_NODES, DOCUMENT_PATHS, "document"),
                               (REQUIREMENT_NODES, REQUIREMENT_PATHS, "requirement"),
                               (FAILURE_NODES, FAILURE_PATHS, "failure")):
        for a, b, _, _ in edges:
            for n in (a, b):
                if n not in nodes:
                    raise SystemExit(f"{name} path tree references undeclared node {n!r}")


def main() -> None:
    validate_pipeline(schema_models())
    validate_path_trees()
    for fname, content in (
            ("pipeline.mermaid", build_pipeline()),
            ("models.mermaid", build_models()),
            ("paths_document.mermaid", build_path_tree(
                DOCUMENT_NODES, DOCUMENT_PATHS,
                "Every route a DOCUMENT can take through the two document-level stages.")),
            ("paths_requirement.mermaid", build_path_tree(
                REQUIREMENT_NODES, REQUIREMENT_PATHS,
                "Every route a REQUIREMENT can take to a successful terminal outcome. "
                "Failure routes are in paths_failure.mermaid.")),
            ("paths_failure.mermaid", build_path_tree(
                FAILURE_NODES, FAILURE_PATHS,
                "What happens when a stage fails or the process is interrupted.", "LR")),
    ):
        (OUT_DIR / fname).write_text(content, encoding="utf-8")
        print(f"wrote {fname} ({len(content.splitlines())} lines)")


if __name__ == "__main__":
    main()
