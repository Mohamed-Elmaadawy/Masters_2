"""S1 (design/DESIGN_NOTES.md, "System changes to make before the evaluation freeze"):
scans PURE documents for the three PDF/DOC/HTML-to-text corruption signatures Known
Limitation 5 found in `1998 - themas.xml` (`<=` flattened to `=`) -- run so far only
over the 18-file annotated XML subset, never over the 79-document full corpus
(`datasets/pure-full/`).

    python -m tools.scan_pure_corruption datasets/pure-full/req
    python -m tools.scan_pure_corruption datasets/requirements-xml/XMLZIPFile

This is a DIAGNOSTIC scan, not a requirement extractor -- it reports corruption
signatures per document; it does not decide requirement boundaries and it does not
feed `RequirementSet` construction. Text is pulled best-effort per format, deliberately
crude for formats with no requirement-extraction plan yet (see the handover doc's Task
3 and the 2026-08-15 discussion in DESIGN_NOTES.md: format-specific *extraction* is
deferred until the frozen evaluation subset actually needs it, but the corruption scan
covers all 79 regardless of that decision, since it does not commit to any subset or
extraction approach). A best-effort text pull can under-detect (miss a corruption that
real extraction would have surfaced) -- this is reported as a per-format caveat below,
not silently assumed clean.

Per-format text pull, all best-effort:
  .pdf        pdfplumber (already a project dependency for this scan; no page-layout
              reconstruction attempted -- .extract_text() only).
  .doc        legacy OLE binary, no parser used. Printable-ASCII-run extraction
              (`[\\x20-\\x7e]{15,}`) -- verified empirically (2026-08-16) against real
              files in this corpus to pull genuine prose, not just internal structure
              names. Cruder than a real parser: table/field-code text and anything
              stored outside plain runs is invisible to this scan.
  .html/.htm  regex tag-strip (no BeautifulSoup dependency for a diagnostic scan).
  .rtf        regex control-word/group strip.
  .xml        raw text via ElementTree itertext() (matches tools/extract_pure_xml.py's
              own text access, so this scan and that extractor see the same content).

Three signatures, matched independently (a document can trip more than one):
  chained_comparisons     `X = Y = Z`-shaped chains (>= 2 consecutive `=` between short
                          identifier-like tokens) -- the exact shape Known Limitation 5
                          found ("LT = T = UT").
  underscore_tokens       `T_LT`-shaped tokens: short identifier segments joined by `_`.
  surviving_math_symbols  literal Unicode math (<=, >=, !=, +/-, ≤, ≥, ≠, ±) -- proves
                          extraction CAN preserve comparison operators in this document,
                          which is why chained_comparisons here is more likely a genuine
                          flattening than an artifact of the source using prose "=".

Report per document; this script never rewrites or repairs anything.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pdfplumber

_CHAIN_RE = re.compile(
    r"\b[A-Za-z][A-Za-z0-9]{0,15}\s*=\s*[A-Za-z][A-Za-z0-9]{0,15}\s*=\s*[A-Za-z][A-Za-z0-9]{0,15}\b")
_UNDERSCORE_TOKEN_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*_[A-Z][A-Za-z0-9]*\b")
_MATH_SYMBOLS = ("<=", ">=", "!=", "+/-", "\u2264", "\u2265", "\u2260", "\u00b1")

_DOC_ASCII_RUN_RE = re.compile(rb"[\x20-\x7e]{15,}")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_RTF_CONTROL_RE = re.compile(r"\\[a-zA-Z]+-?\d* ?|[{}]|\\'\w{2}|\\")


def _text_from_pdf(path: Path) -> str:
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _text_from_doc(path: Path) -> str:
    runs = _DOC_ASCII_RUN_RE.findall(path.read_bytes())
    return "\n".join(run.decode("ascii") for run in runs)


def _text_from_html(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    return _HTML_TAG_RE.sub(" ", raw)


def _text_from_rtf(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    return _RTF_CONTROL_RE.sub(" ", raw)


def _text_from_xml(path: Path) -> str:
    tree = ET.parse(path)
    return "\n".join(tree.getroot().itertext())


_EXTRACTORS = {
    ".pdf": _text_from_pdf, ".doc": _text_from_doc, ".html": _text_from_html,
    ".htm": _text_from_html, ".rtf": _text_from_rtf, ".xml": _text_from_xml,
}


def scan_text(text: str) -> dict:
    chains = sorted(set(_CHAIN_RE.findall(text)))
    tokens = sorted(set(_UNDERSCORE_TOKEN_RE.findall(text)))
    symbols = {sym: text.count(sym) for sym in _MATH_SYMBOLS if text.count(sym) > 0}
    return {
        "chained_comparisons": chains,
        "underscore_tokens": tokens,
        "surviving_math_symbols": symbols,
        "any_signature": bool(chains or tokens or symbols),
    }


def scan_document(path: Path) -> dict:
    extractor = _EXTRACTORS.get(path.suffix.lower())
    if extractor is None:
        return {"file": path.name, "error": f"no text extractor for suffix {path.suffix!r}"}
    try:
        text = extractor(path)
    except Exception as e:  # best-effort scan: a file this can't read is reported, not fatal
        return {"file": path.name, "error": f"{type(e).__name__}: {e}"}
    result = scan_text(text)
    result["file"] = path.name
    result["chars_extracted"] = len(text)
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("directory", type=Path, help="Directory of documents to scan (non-recursive)")
    parser.add_argument("--output", type=Path, help="Optional path to write the JSON report to")
    args = parser.parse_args(argv)

    paths = sorted(p for p in args.directory.iterdir()
                   if p.is_file() and p.suffix.lower() in _EXTRACTORS)
    if not paths:
        print(f"No scannable files (suffixes {sorted(_EXTRACTORS)}) found in {args.directory}")
        return 1

    results = [scan_document(p) for p in paths]
    flagged = [r for r in results if r.get("any_signature")]
    errored = [r for r in results if "error" in r]

    print(f"Scanned {len(results)} document(s) in {args.directory}.")
    for r in results:
        if "error" in r:
            print(f"  {r['file']}: ERROR -- {r['error']}")
        elif r["any_signature"]:
            print(f"  {r['file']}: chained_comparisons={r['chained_comparisons']} "
                 f"underscore_tokens={r['underscore_tokens']} "
                 f"surviving_math_symbols={r['surviving_math_symbols']}")
    print(f"\n{len(flagged)}/{len(results)} document(s) show at least one signature. "
         f"{len(errored)} could not be read.")

    if args.output:
        args.output.write_text(json.dumps(results, indent=2))
        print(f"Full report written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
