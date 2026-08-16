"""S1 (design/DESIGN_NOTES.md, "System changes to make before the evaluation freeze"):
extract Dalpiaz's user-story dataset (`datasets/EVALUATION_DATASETS.md`, item 3) into
`RequirementSet` JSON, one per source file.

    python -m tools.extract_dalpiaz datasets/dalpiaz-user-stories datasets/dalpiaz-extracted

Each file is (per EVALUATION_DATASETS.md) one story per line -- no requirement-boundary
judgement call to make here, unlike PURE's unstructured prose: the dataset's own authors
already decided where one story ends and the next begins, same reasoning as
`tools/extract_pure_xml.py`'s `<req>` elements. This extractor's only real decision is
recognising two exceptions to "one line, one story", found by surveying all 22 files
before writing this (2026-08-16):

1. **Line-wrapped stories.** A small number of stories span two physical lines with no
   sentence-ending punctuation before the wrap (e.g. `06`/`11`/`15.txt`). A line starting
   with `As ` (case-insensitive) begins a new story; anything else is a continuation of
   the previous one, joined with a single space. This is NOT always safe -- see point 3.
2. **Missing article.** Several real stories read "As lab administrator, ..." / "As
   User, ..." rather than "As a lab administrator, ...". The rule above deliberately
   does not require "a"/"an"/"the" after "As", specifically so these are NOT
   mis-detected as continuations of the previous line.
3. **One stray non-story line found (`15.txt`, "Auditing & Reporting.").** It is neither
   a new story (doesn't start with "As ") nor a real continuation -- a section heading
   that happens to sit mid-file. The continuation rule cannot distinguish this from a
   genuine wrap, so it gets merged into the previous story like any other continuation,
   and the merge is recorded in the manifest as `line_merges` for manual review rather
   than silently trusted. Every merge this extractor performs is listed there, not just
   this one.

Two files per document, same shape as tools/extract_pure_xml.py:

    <doc_id>.json           a RequirementSet, validated before writing
    <doc_id>.manifest.json  provenance: source file, encoding used, every line merge
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from design.schemas import Requirement, RequirementSet

EXTRACTOR_VERSION = 1

_NEW_STORY_PREFIX = "as "


def doc_id_for(path: Path) -> str:
    return f"dalpiaz-{path.stem}"


def requirement_id_for(doc_id: str, position: int) -> str:
    return f"{doc_id.upper()}-{position + 1:04d}"


def read_text_best_effort(path: Path) -> tuple[str, str]:
    """Several files are UTF-8 (with or without a BOM), several are Windows-1252
    (curly quotes, `&` entities) -- verified per-file during the 2026-08-16 survey.
    Returns (text, encoding_used) so the manifest can record which was used."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        return raw.decode("cp1252"), "cp1252"


def split_stories(text: str) -> tuple[list[str], list[dict]]:
    """One story per line, continuation lines (not starting with "As ") joined onto the
    previous story. Returns (stories, merges) -- merges records every continuation join
    performed, in order, so a reviewer can check each one (see module docstring, point 3)."""
    stories: list[str] = []
    merges: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith(_NEW_STORY_PREFIX):
            stories.append(line)
        elif stories:
            merged = f"{stories[-1]} {line}"
            merges.append({"story_index": len(stories) - 1,
                          "before": stories[-1], "appended": line, "after": merged})
            stories[-1] = merged
        else:
            # A continuation-shaped line with nothing before it to attach to -- kept as
            # its own story rather than silently dropped.
            stories.append(line)
    return stories, merges


def extract_file(path: Path) -> tuple[RequirementSet, dict]:
    text, encoding = read_text_best_effort(path)
    stories, merges = split_stories(text)
    doc_id = doc_id_for(path)

    requirements = [
        Requirement(id=requirement_id_for(doc_id, i), text=story, source_doc_id=doc_id)
        for i, story in enumerate(stories)
    ]
    requirement_set = RequirementSet(doc_id=doc_id, requirements=requirements)

    manifest = {
        "doc_id": doc_id, "source_file": path.name, "encoding_used": encoding,
        "counts": {"requirements": len(requirements), "line_merges": len(merges)},
        "line_merges": merges,
    }
    return requirement_set, manifest


def extract_directory(input_dir: Path, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "extractor_version": EXTRACTOR_VERSION,
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input_dir": str(input_dir), "documents": [], "total_requirements": 0,
    }

    for txt_path in sorted(input_dir.glob("*.txt")):
        requirement_set, manifest = extract_file(txt_path)
        doc_id = manifest["doc_id"]
        (output_dir / f"{doc_id}.json").write_text(
            requirement_set.model_dump_json(indent=2), encoding="utf-8", newline="\n")
        (output_dir / f"{doc_id}.manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")

        summary["documents"].append({
            "doc_id": doc_id, "source_file": txt_path.name,
            "requirements": manifest["counts"]["requirements"],
            "line_merges": manifest["counts"]["line_merges"],
        })
        summary["total_requirements"] += manifest["counts"]["requirements"]

    (output_dir / "extraction-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    return summary


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.extract_dalpiaz")
    parser.add_argument("input_dir", type=Path, help="datasets/dalpiaz-user-stories")
    parser.add_argument("output_dir", type=Path,
                        help="Directory to write <doc_id>.json and <doc_id>.manifest.json into")
    args = parser.parse_args(argv)

    try:
        summary = extract_directory(args.input_dir, args.output_dir)
    except (OSError, ValueError, ValidationError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    for doc in summary["documents"]:
        merge_note = f", {doc['line_merges']} line merge(s) -- REVIEW" if doc["line_merges"] else ""
        print(f"{doc['doc_id']:16s} {doc['requirements']:4d} requirements{merge_note}")
    print(f"{'TOTAL':16s} {summary['total_requirements']:4d} requirements "
         f"across {len(summary['documents'])} documents -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
