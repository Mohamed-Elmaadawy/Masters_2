"""Extract PURE's annotated `<req>` elements into RequirementSet JSON files.

    python -m tools.extract_pure_xml datasets/requirements-xml/XMLZIPFile OUTPUT_DIR

Six of the eighteen documents in `datasets/requirements-xml/XMLZIPFile/` carry explicit
`<req id="...">` elements with `<text_body>` children -- PURE's own annotators decided
what counts as a requirement, so this extractor makes no such judgement itself. It is a
deterministic XML parse with zero inference: see
`docs/superpowers/plans/2026-08-14-evaluation-design.md`, "The annotated XML subset makes
extraction easy", for why that matters (an LLM extractor would mean an LLM pipeline
evaluated on LLM-produced input).

Per document it writes two files:

    <doc_id>.json           a RequirementSet, validated before writing
    <doc_id>.manifest.json  the provenance crosswalk (plain JSON, not a schema type)

The manifest is deliberately a sidecar rather than fields on `Requirement`: provenance is
extraction bookkeeping, not pipeline input, and `Requirement` has no such fields today.

Three things this does that are decisions, not mechanics:

1. **Ids are synthesised, ordinally.** `<req id>` is section-local in PURE, not
   document-unique -- cctns repeats 24 ids, gamma j 6, peering 4, ertms 8 -- so
   `RequirementSet._ids_are_unique` rejects the raw ids outright. Ids are therefore
   `PURE-CCTNS-0001..0115` in document order. The section path does *not* disambiguate
   as a fallback: cctns's duplicates all sit inside a single `<p id="">`. The original
   `<req id>`, the section path and the file position all survive in the manifest.

2. **`<itemize>`/`<enum>` bullets are preserved as newline + "- ".** Flattening them into
   the sentence (what a plain `itertext()` join does) manufactures run-on requirements --
   75 of eirene_fun's 583 contain lists -- which is exactly the shape the Quality Checker
   over-flags as `non_atomic` (DESIGN_NOTES.md Known Limitation 8). The flattening would
   be an artifact of extraction, not a property of the requirement.

3. **A leading token identical to the `<req id>` is stripped.** eirene_fun's text_bodies
   begin with their own section number ("2.2.1     This section describes..."). Stripped
   only on exact string equality with the id attribute, so no inference is involved; the
   manifest records per requirement whether it fired.

Excluded documents are enforced by `SPENT_DOCUMENTS` below, not remembered.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from design.schemas import Requirement, RequirementSet

# The XSD these documents declare as their default namespace.
NS = "{req_document.xsd}"

# Bumped when a change alters the bytes this extractor produces, so a manifest states
# which rules built it rather than only when it was run.
EXTRACTOR_VERSION = 1

# Enforced, not remembered. Both were pulled into `datasets/requirements_dataset.json`
# for schema-design work and are spent for evaluation purposes -- see
# `datasets/EVALUATION_DATASETS.md` and the evaluation-design plan, section 5 item 4.
# Keyed by slug (see `slug_for`), so the year prefix and separator style in the filename
# do not matter.
SPENT_DOCUMENTS: dict[str, str] = {
    "themas": "spent: pulled into requirements_dataset.json as pure-themas-1998-full "
              "(also the one document with flattened comparison operators)",
    "ertms": "spent: pulled into requirements_dataset.json as pure-ertms-2007",
}


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def slug_for(path: Path) -> str:
    """`0000 - cctns.xml` -> `cctns`; `2007-eirene_fun_7-2.xml` -> `eirene-fun-7-2`.

    The leading four-digit year is dropped because it is a PURE filing convention, not
    part of the document's identity, and it is `0000` (i.e. unknown) for two of the five.
    """
    stem = path.stem
    stem = re.sub(r"^\s*\d{4}\s*[-_ ]*", "", stem)
    stem = re.sub(r"[^0-9a-zA-Z]+", "-", stem).strip("-").lower()
    if not stem:
        raise ValueError(f"cannot derive a slug from filename {path.name!r}")
    return stem


def doc_id_for(slug: str) -> str:
    return f"pure-{slug}"


def requirement_id_for(doc_id: str, position: int) -> str:
    """Ordinal, 1-based, zero-padded to four digits (the largest document has 583)."""
    return f"{doc_id.upper()}-{position + 1:04d}"


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _collapse(text: Optional[str]) -> str:
    """Collapse all runs of whitespace to single spaces.

    NBSP and friends are normalised first: `str.split()` already treats them as
    whitespace in Python 3, but NFKC also folds the typographic ligatures and full-width
    forms that survive PDF-to-XML conversion, so an id-prefix comparison is not defeated
    by an invisible character.
    """
    if not text:
        return ""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def text_of(text_body: ET.Element) -> tuple[str, int]:
    """Render one `<text_body>` to a string, preserving list structure.

    Returns `(text, bullet_count)`. Children are walked in document order: an
    `<itemize>`/`<enum>` contributes one `- ` line per `<item>`, anything else is folded
    inline. `.tail` text (which carries the prose that resumes after a list) is kept.

    Only `itemize`, `enum` and `item` occur inside `<text_body>` in this corpus, and
    lists never nest -- verified across all five documents before this was written -- so
    the flat walk below is the whole story rather than a simplification of it.
    """
    lines: list[str] = [_collapse(text_body.text)]
    bullets = 0

    for child in text_body:
        tag = child.tag.replace(NS, "")
        if tag in ("itemize", "enum"):
            for item in child.findall(NS + "item"):
                rendered = _collapse("".join(item.itertext()))
                if rendered:
                    lines.append(f"- {rendered}")
                    bullets += 1
        else:
            # No such element exists in this corpus today; fold it in rather than drop
            # it silently if a future document has one.
            inline = _collapse("".join(child.itertext()))
            if inline:
                lines.append(inline)
        tail = _collapse(child.tail)
        if tail:
            lines.append(tail)

    # A bullet must start its own line; consecutive prose fragments join with a space.
    out = ""
    for part in (p for p in lines if p):
        if not out:
            out = part
        elif part.startswith("- ") or out.rsplit("\n", 1)[-1].startswith("- "):
            out += "\n" + part
        else:
            out += " " + part
    return out.strip(), bullets


def strip_id_prefix(text: str, req_id: str, other_ids: frozenset[str] = frozenset()) -> tuple[str, bool]:
    """Drop a leading occurrence of the `<req id>` attribute from the text.

    Exact string equality against the element's own id, never a pattern for "looks like
    a section number". `"2.2.1 This section..."` with `id="2.2.1"` loses the token;
    `"2.2.1.1 ..."` with `id="2.2.1"` does not, because the character after the match is
    a digit or a dot -- that guard is what stops `id="11.2.1.1"` from turning
    `"11.2.1.10It shall..."` into `"0It shall..."`.

    The delimiter is optional because two of eirene_fun's 583 have the number fused to
    the first word (`"11.2.1.10It shall be possible..."`). Requiring whitespace would
    leave those two mangled, so the digit/dot guard exists instead.

    That guard is not the whole story: eirene_fun's ids also carry roman-numeral suffixes
    (`5.2.2i`, `5.2.2ii`, `5.2.2iii`, ...), and a shorter one is a proper string-prefix of
    a longer sibling exactly the way `11.2.1.1` is a prefix of `11.2.1.10` -- except the
    continuation character is a letter, which the digit/dot guard does not see. `other_ids`
    is every `<req id>` in the document; if `req_id` is a proper prefix of one of them,
    stripping is refused even though nothing after it looks like a digit or dot, because a
    text that fused the *sibling's* longer id would otherwise be silently truncated to the
    wrong id's length. This does not fire on the digit-fused pair above: `11.2.1.1` is
    refused by the digit guard first, before the sibling check ever runs.
    """
    if not req_id or not text.startswith(req_id):
        return text, False
    rest = text[len(req_id):]
    if not rest or rest[0].isspace():
        return rest.lstrip(), True
    if rest[0].isdigit() or rest[0] == ".":
        return text, False
    if any(other_id != req_id and other_id.startswith(req_id) for other_id in other_ids):
        return text, False
    return rest.lstrip(), True


def flags_for(text: str) -> list[str]:
    """Mark requirements that are annotated but not actually requirements.

    A flag, never a filter: this extractor drops nothing PURE tagged. eirene_fun marks
    withdrawn clauses by replacing the body with the literal word "Deleted." (10 of its
    583, one of which carries a stray heading fragment after it). They validate fine and
    would silently enter any sample drawn from the corpus, so they are labelled here and
    counted in the manifest -- whoever draws the evaluation sample decides what to do
    with them, on a number rather than on discovery during hand-scoring.
    """
    flags = []
    if re.match(r"^Deleted\b", text):
        flags.append("deleted_marker")
    return flags


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _iter_reqs(element: ET.Element, path: list[str]):
    """Yield `(section_path, req_element)` in document order.

    `<p>` elements nest to form the section path; `<req>` may sit at any depth. Recursion
    depth is bounded by the document's section nesting (4 in the deepest document here),
    so this does not need the iterative treatment `find_cycles()` got.
    """
    for child in element:
        tag = child.tag.replace(NS, "")
        if tag == "req":
            yield path, child
        elif tag == "p":
            yield from _iter_reqs(child, path + [child.get("id") or ""])
        else:
            yield from _iter_reqs(child, path)


def extract_file(xml_path: Path) -> tuple[RequirementSet, dict]:
    """Parse one PURE XML document into a validated RequirementSet plus its manifest."""
    raw = xml_path.read_text(encoding="utf-8-sig")
    root = ET.fromstring(raw)

    slug = slug_for(xml_path)
    doc_id = doc_id_for(slug)

    requirements: list[Requirement] = []
    entries: list[dict] = []
    skipped_empty: list[dict] = []

    reqs = list(_iter_reqs(root, []))
    all_ids = frozenset(req.get("id") or "" for _, req in reqs)

    for position, (section_path, req) in enumerate(reqs):
        original_id = req.get("id") or ""
        bodies = req.findall(NS + "text_body")
        rendered_parts: list[str] = []
        bullets = 0
        for body in bodies:
            rendered, n = text_of(body)
            if rendered:
                rendered_parts.append(rendered)
            bullets += n
        text = "\n".join(rendered_parts)
        text, stripped = strip_id_prefix(text, original_id, all_ids) if original_id else (text, False)

        record = {
            "id": requirement_id_for(doc_id, position),
            "original_req_id": original_id,
            "section_path": section_path,
            "position": position,
            "text_body_count": len(bodies),
            "bullet_items": bullets,
            "id_prefix_stripped": stripped,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "text_chars": len(text),
            "flags": flags_for(text),
        }

        if not text:
            # `NonEmptyStr` would reject it anyway; recording it here means the manifest
            # accounts for every `<req>` in the file, so a count mismatch is visible
            # rather than silent. None occur in the five untouched documents.
            skipped_empty.append(record)
            continue

        requirements.append(Requirement(id=record["id"], text=text, source_doc_id=doc_id))
        entries.append(record)

    if not requirements:
        raise ValueError(f"{xml_path.name}: no <req> elements with text (found "
                         f"{len(skipped_empty)} empty)")

    requirement_set = RequirementSet(doc_id=doc_id, requirements=requirements)

    duplicate_texts = sorted(
        t for t, c in Counter(r.text for r in requirements).items() if c > 1
    )

    manifest = {
        "extractor_version": EXTRACTOR_VERSION,
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "doc_id": doc_id,
        "source_file": xml_path.name,
        "source_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "counts": {
            "requirements": len(requirements),
            "skipped_empty": len(skipped_empty),
            "with_bullets": sum(1 for e in entries if e["bullet_items"]),
            "id_prefix_stripped": sum(1 for e in entries if e["id_prefix_stripped"]),
            "duplicate_original_ids": len(
                [i for i, c in Counter(e["original_req_id"] for e in entries).items() if c > 1]
            ),
            # Not a defect and not deduplicated -- PURE's annotators tagged the same
            # sentence twice (peering has 3 such texts, cctns 1). Counted so the
            # evaluation can report it rather than discover it during hand-scoring.
            "duplicate_texts": len(duplicate_texts),
            "deleted_marker": sum(1 for e in entries if "deleted_marker" in e["flags"]),
        },
        "requirements": entries,
        "skipped_empty": skipped_empty,
    }
    return requirement_set, manifest


def extract_directory(input_dir: Path, output_dir: Path) -> dict:
    """Extract every `<req>`-bearing XML in `input_dir`, skipping spent documents."""
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "extractor_version": EXTRACTOR_VERSION,
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input_dir": str(input_dir),
        "documents": [],
        "skipped": [],
        "total_requirements": 0,
    }

    for xml_path in sorted(input_dir.glob("*.xml")):
        slug = slug_for(xml_path)
        if slug in SPENT_DOCUMENTS:
            summary["skipped"].append({"source_file": xml_path.name, "slug": slug,
                                       "reason": SPENT_DOCUMENTS[slug]})
            continue

        if not _has_req(xml_path.read_text(encoding="utf-8-sig")):
            summary["skipped"].append({"source_file": xml_path.name, "slug": slug,
                                       "reason": "no <req> elements (unannotated document)"})
            continue

        requirement_set, manifest = extract_file(xml_path)
        doc_id = manifest["doc_id"]
        (output_dir / f"{doc_id}.json").write_text(
            requirement_set.model_dump_json(indent=2), encoding="utf-8", newline="\n")
        (output_dir / f"{doc_id}.manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")

        summary["documents"].append({
            "doc_id": doc_id,
            "source_file": xml_path.name,
            "requirements": manifest["counts"]["requirements"],
            "counts": manifest["counts"],
        })
        summary["total_requirements"] += manifest["counts"]["requirements"]

    (output_dir / "extraction-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    return summary


def _has_req(raw: str) -> bool:
    """True if the parsed document contains at least one `<req>` element.

    Parsed, not regex-matched: `<req id=` also appears inside commented-out empty
    template blocks (5 in cctns, 9 in gamma j), which is why the evaluation plan's
    regex-derived counts of 120/60 overstate the real 115/51.
    """
    return any(True for _ in ET.fromstring(raw).iter(NS + "req"))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.extract_pure_xml")
    parser.add_argument("input_dir", type=Path,
                        help="Directory of PURE XML files (datasets/requirements-xml/XMLZIPFile)")
    parser.add_argument("output_dir", type=Path,
                        help="Directory to write <doc_id>.json and <doc_id>.manifest.json into")
    args = parser.parse_args(argv)

    try:
        summary = extract_directory(args.input_dir, args.output_dir)
    except (OSError, ValueError, ET.ParseError, ValidationError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    for entry in summary["skipped"]:
        print(f"skipped {entry['source_file']}: {entry['reason']}")
    for doc in summary["documents"]:
        c = doc["counts"]
        print(f"{doc['doc_id']:24s} {c['requirements']:4d} requirements  "
              f"(bullets {c['with_bullets']}, id-prefix stripped {c['id_prefix_stripped']}, "
              f"dup source ids {c['duplicate_original_ids']}, dup texts {c['duplicate_texts']}, "
              f"deleted markers {c['deleted_marker']})")
    print(f"{'TOTAL':24s} {summary['total_requirements']:4d} requirements "
          f"across {len(summary['documents'])} documents -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
