"""
Regression tests for tools/extract_pure_xml.py. Run after any change there:

    python -m tools.test_extract_pure_xml

Plain script, no pytest -- same convention as orchestrator/test_extract_document.py.

Two layers, deliberately:

* **Synthetic** documents exercise each rule in isolation, including the cases the real
  corpus does not contain (an empty `<req>`, a spent document, an unannotated one).
* **Corpus** checks assert the exact per-document counts the five untouched PURE files
  produce. They are pinned numbers, so a silent change in extraction behaviour -- a
  different bullet rule, a stripping rule that stops firing -- turns the suite red
  instead of quietly shifting the evaluation corpus underneath a run.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from design.schemas import RequirementSet
from tools.extract_pure_xml import (
    SPENT_DOCUMENTS,
    doc_id_for,
    extract_directory,
    extract_file,
    flags_for,
    main,
    requirement_id_for,
    slug_for,
    strip_id_prefix,
    text_of,
)

PASSED = 0
FAILED: list[str] = []

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "datasets" / "requirements-xml" / "XMLZIPFile"

# Pinned from the five untouched documents. The evaluation plan quotes 120/60/819 from a
# regex count of `<req id=`; those overcount, because both documents carry commented-out
# empty `<req>` template blocks (5 in cctns, 9 in gamma j) that no parser sees.
EXPECTED_COUNTS = {
    "pure-eirene-fun-7-2": 583,
    "pure-cctns": 115,
    "pure-gamma-j": 51,
    "pure-keepass": 32,
    "pure-peering": 24,
}
EXPECTED_TOTAL = 805


def ok(label: str, condition: bool = True) -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(label)
        print(f"    FAIL  {label}")


def section(name: str) -> None:
    print(f"\n{name}")


def write_xml(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<req_document xmlns="req_document.xsd">\n' + body + "\n</req_document>\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def test_slug_drops_the_year_and_normalises_separators() -> None:
    section("slugs drop PURE's year prefix and normalise separators")
    ok("'0000 - cctns.xml' -> cctns", slug_for(Path("0000 - cctns.xml")) == "cctns")
    ok("'2007-eirene_fun_7-2.xml' -> eirene-fun-7-2",
       slug_for(Path("2007-eirene_fun_7-2.xml")) == "eirene-fun-7-2")
    ok("'0000 - gamma j.xml' -> gamma-j", slug_for(Path("0000 - gamma j.xml")) == "gamma-j")
    ok("'1998 - themas.xml' -> themas", slug_for(Path("1998 - themas.xml")) == "themas")
    ok("'2007-ertms.xml' -> ertms", slug_for(Path("2007-ertms.xml")) == "ertms")
    ok("both spent slugs are the ones the exclusion list keys on",
       {"themas", "ertms"} == set(SPENT_DOCUMENTS))


def test_requirement_ids_are_ordinal_and_zero_padded() -> None:
    section("requirement ids are 1-based ordinals, zero-padded to four digits")
    ok("first is -0001", requirement_id_for(doc_id_for("cctns"), 0) == "PURE-CCTNS-0001")
    ok("115th is -0115", requirement_id_for(doc_id_for("cctns"), 114) == "PURE-CCTNS-0115")
    ok("padding holds past the largest document (583)",
       requirement_id_for(doc_id_for("eirene-fun-7-2"), 582) == "PURE-EIRENE-FUN-7-2-0583")


# ---------------------------------------------------------------------------
# Text rules
# ---------------------------------------------------------------------------

def test_id_prefix_is_stripped_only_on_an_exact_match() -> None:
    section("the id prefix is stripped on exact match, with a digit/dot guard")
    ok("space-delimited exact match is stripped",
       strip_id_prefix("2.2.1 This section describes", "2.2.1") == ("This section describes", True))
    ok("run of spaces is collapsed away by the lstrip",
       strip_id_prefix("2.2.1    Text here", "2.2.1") == ("Text here", True))
    ok("fused prefix is stripped too (eirene has two)",
       strip_id_prefix("11.2.1.10It shall be possible", "11.2.1.10")
       == ("It shall be possible", True))
    # The guard that makes the fused case safe: without it, id="11.2.1.1" would eat the
    # leading digit of a text that starts "11.2.1.10..." and leave "0It shall...".
    ok("a longer number is NOT stripped by a shorter id (digit guard)",
       strip_id_prefix("11.2.1.10It shall be possible", "11.2.1.1")
       == ("11.2.1.10It shall be possible", False))
    ok("a deeper section number is NOT stripped by its parent (dot guard)",
       strip_id_prefix("2.2.1.1 Text", "2.2.1") == ("2.2.1.1 Text", False))
    ok("an unrelated leading token is left alone",
       strip_id_prefix("The system shall", "3.1") == ("The system shall", False))
    ok("an empty id never strips", strip_id_prefix("Some text", "") == ("Some text", False))


def test_bullets_become_lines_and_surrounding_prose_survives() -> None:
    section("<itemize>/<enum> items become '- ' lines, prose before and after survives")
    with tempfile.TemporaryDirectory() as tmp:
        path = write_xml(Path(tmp), "0000 - synth.xml", """
<p id="1">
<req id="1">
<text_body>The system shall support:
<itemize><item>voice calls;</item><item>data calls.</item></itemize>
This applies at all times.</text_body>
</req>
</p>""")
        rset, manifest = extract_file(path)
        text = rset.requirements[0].text
        ok("leading prose is first", text.startswith("The system shall support:"))
        ok("each item is its own '- ' line",
           "\n- voice calls;" in text and "\n- data calls." in text)
        ok("trailing prose (the tail) is not lost",
           text.rstrip().endswith("This applies at all times."))
        ok("trailing prose is on its own line, not glued to the last bullet",
           "\nThis applies at all times." in text)
        ok("bullet_items counts the items", manifest["requirements"][0]["bullet_items"] == 2)


def test_flat_text_body_is_a_single_line() -> None:
    section("a <text_body> with no list stays one whitespace-collapsed line")
    with tempfile.TemporaryDirectory() as tmp:
        path = write_xml(Path(tmp), "0000 - synth.xml", """
<p id="1"><req id="1"><text_body>
   The system   shall
   do a thing.
</text_body></req></p>""")
        rset, manifest = extract_file(path)
        ok("whitespace is collapsed to single spaces",
           rset.requirements[0].text == "The system shall do a thing.")
        ok("no bullets counted", manifest["requirements"][0]["bullet_items"] == 0)


def test_deleted_markers_are_flagged_and_never_dropped() -> None:
    section("'Deleted.' tombstones are flagged, not filtered")
    ok("'Deleted.' flags", flags_for("Deleted.") == ["deleted_marker"])
    ok("'Deleted. Manual network selection' flags too (a stray heading follows it)",
       flags_for("Deleted. Manual network selection") == ["deleted_marker"])
    ok("a real requirement does not flag",
       flags_for("The system shall delete the record.") == [])
    ok("'Deletion' does not flag -- \\b guards the word boundary",
       flags_for("Deletion of a record shall be logged.") == [])


# ---------------------------------------------------------------------------
# Ids and manifest
# ---------------------------------------------------------------------------

def test_section_local_duplicate_ids_do_not_collide() -> None:
    section("repeated <req id> values across sections produce distinct requirement ids")
    with tempfile.TemporaryDirectory() as tmp:
        # The shape that breaks RequirementSet in the real corpus, including cctns's
        # degenerate empty <p id="">, where the section path cannot disambiguate either.
        path = write_xml(Path(tmp), "0000 - synth.xml", """
<p id="4">
<p id=""><req id="1"><text_body>First requirement.</text_body></req>
<req id="2"><text_body>Second requirement.</text_body></req></p>
<p id=""><req id="1"><text_body>Third requirement.</text_body></req>
<req id="2"><text_body>Fourth requirement.</text_body></req></p>
</p>""")
        rset, manifest = extract_file(path)
        ok("all four survive", len(rset.requirements) == 4)
        ok("ids are unique and ordinal",
           [r.id for r in rset.requirements] ==
           ["PURE-SYNTH-0001", "PURE-SYNTH-0002", "PURE-SYNTH-0003", "PURE-SYNTH-0004"])
        ok("the set validates (this is the check raw ids fail)",
           RequirementSet.model_validate(rset.model_dump()).doc_id == "pure-synth")
        ok("the manifest keeps the original ids",
           [e["original_req_id"] for e in manifest["requirements"]] == ["1", "2", "1", "2"])
        ok("the manifest counts the collision",
           manifest["counts"]["duplicate_original_ids"] == 2)
        ok("section paths are recorded even when degenerate",
           manifest["requirements"][0]["section_path"] == ["4", ""])


def test_manifest_crosswalk_is_complete_and_positional() -> None:
    section("every emitted requirement has one manifest entry, keyed by position")
    with tempfile.TemporaryDirectory() as tmp:
        path = write_xml(Path(tmp), "0000 - synth.xml", """
<p id="1"><req id="a"><text_body>One.</text_body></req>
<p id="1.1"><req id="b"><text_body>Two.</text_body></req></p></p>""")
        rset, manifest = extract_file(path)
        ok("one entry per requirement",
           [e["id"] for e in manifest["requirements"]] == [r.id for r in rset.requirements])
        ok("positions are 0-based and in document order",
           [e["position"] for e in manifest["requirements"]] == [0, 1])
        ok("nested <p> deepens the section path",
           manifest["requirements"][1]["section_path"] == ["1", "1.1"])
        ok("source provenance is recorded", manifest["source_file"] == "0000 - synth.xml")
        ok("source bytes are hashed", len(manifest["source_sha256"]) == 64)
        ok("text hashes let a later run detect drift",
           all(len(e["text_sha256"]) == 64 for e in manifest["requirements"]))


def test_empty_req_is_skipped_and_accounted_for() -> None:
    section("a <req> with no text is skipped, and the manifest says so")
    with tempfile.TemporaryDirectory() as tmp:
        path = write_xml(Path(tmp), "0000 - synth.xml", """
<p id="1"><req id="1"><text_body>   </text_body></req>
<req id="2"><text_body>A real one.</text_body></req></p>""")
        rset, manifest = extract_file(path)
        ok("only the non-empty one is emitted", len(rset.requirements) == 1)
        ok("the empty one is recorded, not silently lost",
           len(manifest["skipped_empty"]) == 1)
        ok("skipped_empty keeps its original id",
           manifest["skipped_empty"][0]["original_req_id"] == "1")
        # Position is assigned before the skip, so the surviving requirement keeps the
        # ordinal of its place in the file. Ids stay a stable function of file position.
        ok("the survivor keeps its file position", rset.requirements[0].id == "PURE-SYNTH-0002")


# ---------------------------------------------------------------------------
# Directory-level behaviour
# ---------------------------------------------------------------------------

def test_spent_documents_are_excluded_by_the_list() -> None:
    section("spent documents are skipped by SPENT_DOCUMENTS, with a reason")
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "in", Path(tmp) / "out"
        src.mkdir()
        body = '<p id="1"><req id="1"><text_body>A requirement.</text_body></req></p>'
        write_xml(src, "1998 - themas.xml", body)
        write_xml(src, "2007-ertms.xml", body)
        write_xml(src, "0000 - keeper.xml", body)

        summary = extract_directory(src, out)

        ok("only the untouched document is extracted",
           [d["doc_id"] for d in summary["documents"]] == ["pure-keeper"])
        ok("both spent documents are reported as skipped",
           {s["slug"] for s in summary["skipped"]} == {"themas", "ertms"})
        ok("each skip carries its reason",
           all(s["reason"] for s in summary["skipped"]))
        ok("no output file is written for a spent document",
           not (out / "pure-themas.json").exists() and not (out / "pure-ertms.json").exists())


def test_unannotated_documents_are_skipped_not_failed() -> None:
    section("a document with no <req> elements is skipped, not an error")
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "in", Path(tmp) / "out"
        src.mkdir()
        write_xml(src, "0000 - prose.xml", '<p id="1"><text_body>Just prose.</text_body></p>')
        write_xml(src, "0000 - keeper.xml",
                  '<p id="1"><req id="1"><text_body>A requirement.</text_body></req></p>')

        summary = extract_directory(src, out)

        ok("the prose document is skipped",
           [s["slug"] for s in summary["skipped"]] == ["prose"])
        ok("the annotated one still extracts", summary["total_requirements"] == 1)


def test_commented_out_req_blocks_are_not_counted() -> None:
    section("<req> inside an XML comment is invisible -- the source of the 120/60 overcount")
    with tempfile.TemporaryDirectory() as tmp:
        path = write_xml(Path(tmp), "0000 - synth.xml", """
<p id="1"><req id="1"><text_body>A real requirement.</text_body></req></p>
<!-- <p id=""><req id="1"><text_body></text_body></req>
<req id="2"><text_body></text_body></req></p> -->""")
        rset, manifest = extract_file(path)
        ok("only the uncommented requirement is extracted", len(rset.requirements) == 1)
        ok("the commented ones are not even counted as skipped",
           manifest["counts"]["skipped_empty"] == 0)


def test_cli_writes_both_files_per_document_and_a_summary() -> None:
    section("the CLI writes <doc_id>.json, <doc_id>.manifest.json and a summary")
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "in", Path(tmp) / "out"
        src.mkdir()
        write_xml(src, "0000 - keeper.xml",
                  '<p id="1"><req id="1"><text_body>A requirement.</text_body></req></p>')

        code = main([str(src), str(out)])

        ok("exit code is 0", code == 0)
        ok("the RequirementSet is written", (out / "pure-keeper.json").exists())
        ok("the manifest is written", (out / "pure-keeper.manifest.json").exists())
        ok("the summary is written", (out / "extraction-summary.json").exists())
        ok("the written RequirementSet validates",
           RequirementSet.model_validate_json(
               (out / "pure-keeper.json").read_text(encoding="utf-8")).doc_id == "pure-keeper")


def test_bad_input_directory_exits_1() -> None:
    section("a nonexistent input directory exits 0 with nothing extracted, not a crash")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out"
        code = main([str(Path(tmp) / "nope"), str(out)])
        # glob on a missing directory yields nothing rather than raising; the summary
        # showing zero documents is the honest report, so this is deliberately not an
        # error path. Asserted so the behaviour is a decision rather than an accident.
        ok("exit code is 0", code == 0)
        ok("the summary records zero documents",
           json.loads((out / "extraction-summary.json").read_text())["total_requirements"] == 0)


# ---------------------------------------------------------------------------
# The real corpus
# ---------------------------------------------------------------------------

def test_real_corpus_counts_are_pinned() -> None:
    section("the five untouched PURE documents extract to the pinned counts")
    if not CORPUS.is_dir():
        ok(f"corpus directory {CORPUS} exists", False)
        return
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        summary = extract_directory(CORPUS, out)

        got = {d["doc_id"]: d["requirements"] for d in summary["documents"]}
        ok("exactly the five untouched documents are extracted",
           set(got) == set(EXPECTED_COUNTS))
        for doc_id, expected in EXPECTED_COUNTS.items():
            ok(f"{doc_id} has {expected} requirements", got.get(doc_id) == expected)
        ok(f"total is {EXPECTED_TOTAL}", summary["total_requirements"] == EXPECTED_TOTAL)
        ok("themas and ertms are skipped as spent",
           {s["slug"] for s in summary["skipped"] if s["slug"] in SPENT_DOCUMENTS}
           == {"themas", "ertms"})

        for doc_id in EXPECTED_COUNTS:
            rset = RequirementSet.model_validate_json(
                (out / f"{doc_id}.json").read_text(encoding="utf-8"))
            manifest = json.loads((out / f"{doc_id}.manifest.json").read_text(encoding="utf-8"))
            ok(f"{doc_id} validates as a RequirementSet", rset.doc_id == doc_id)
            ok(f"{doc_id} has no empty requirements dropped",
               manifest["counts"]["skipped_empty"] == 0)
            ok(f"{doc_id}'s manifest covers every requirement",
               len(manifest["requirements"]) == len(rset.requirements))
            ok(f"{doc_id}'s every requirement names this document",
               all(r.source_doc_id == doc_id for r in rset.requirements))


def test_real_corpus_known_quirks_are_pinned() -> None:
    section("the corpus quirks that affect sampling are pinned as numbers")
    if not CORPUS.is_dir():
        ok(f"corpus directory {CORPUS} exists", False)
        return
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        extract_directory(CORPUS, out)
        counts = {}
        for doc_id in EXPECTED_COUNTS:
            counts[doc_id] = json.loads(
                (out / f"{doc_id}.manifest.json").read_text(encoding="utf-8"))["counts"]

        # Every eirene text_body repeats its own section number; if this stops being
        # 583/583 the stripping rule has silently changed.
        ok("eirene strips its id prefix on all 583",
           counts["pure-eirene-fun-7-2"]["id_prefix_stripped"] == 583)
        ok("no other document has an id prefix to strip",
           all(counts[d]["id_prefix_stripped"] == 0
               for d in EXPECTED_COUNTS if d != "pure-eirene-fun-7-2"))
        ok("eirene has 75 requirements containing lists",
           counts["pure-eirene-fun-7-2"]["with_bullets"] == 75)
        ok("eirene carries 10 'Deleted.' tombstones",
           counts["pure-eirene-fun-7-2"]["deleted_marker"] == 10)
        ok("no other document has tombstones",
           all(counts[d]["deleted_marker"] == 0
               for d in EXPECTED_COUNTS if d != "pure-eirene-fun-7-2"))
        # Section-local ids: the reason ids are synthesised at all.
        ok("cctns repeats 24 source ids", counts["pure-cctns"]["duplicate_original_ids"] == 24)
        ok("gamma j repeats 6", counts["pure-gamma-j"]["duplicate_original_ids"] == 6)
        ok("peering repeats 4", counts["pure-peering"]["duplicate_original_ids"] == 4)
        ok("eirene and keepass do not repeat any",
           counts["pure-eirene-fun-7-2"]["duplicate_original_ids"] == 0
           and counts["pure-keepass"]["duplicate_original_ids"] == 0)
        # Verbatim-repeated requirement text. Not deduplicated -- PURE tagged it twice --
        # but it bears on Known Limitation 1 (duplicate test cases) and on sampling.
        ok("eirene has 31 repeated texts", counts["pure-eirene-fun-7-2"]["duplicate_texts"] == 31)
        ok("peering has 3", counts["pure-peering"]["duplicate_texts"] == 3)
        ok("cctns has 1", counts["pure-cctns"]["duplicate_texts"] == 1)
        ok("gamma j and keepass have none",
           counts["pure-gamma-j"]["duplicate_texts"] == 0
           and counts["pure-keepass"]["duplicate_texts"] == 0)


def test_real_corpus_extraction_is_deterministic() -> None:
    section("two runs over the corpus produce byte-identical RequirementSet files")
    if not CORPUS.is_dir():
        ok(f"corpus directory {CORPUS} exists", False)
        return
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        extract_directory(CORPUS, Path(a))
        extract_directory(CORPUS, Path(b))
        for doc_id in EXPECTED_COUNTS:
            # Only the RequirementSet files: manifests carry a timestamp by design.
            ok(f"{doc_id}.json is byte-identical across runs",
               (Path(a) / f"{doc_id}.json").read_bytes()
               == (Path(b) / f"{doc_id}.json").read_bytes())


ALL_TESTS = [
    test_slug_drops_the_year_and_normalises_separators,
    test_requirement_ids_are_ordinal_and_zero_padded,
    test_id_prefix_is_stripped_only_on_an_exact_match,
    test_bullets_become_lines_and_surrounding_prose_survives,
    test_flat_text_body_is_a_single_line,
    test_deleted_markers_are_flagged_and_never_dropped,
    test_section_local_duplicate_ids_do_not_collide,
    test_manifest_crosswalk_is_complete_and_positional,
    test_empty_req_is_skipped_and_accounted_for,
    test_spent_documents_are_excluded_by_the_list,
    test_unannotated_documents_are_skipped_not_failed,
    test_commented_out_req_blocks_are_not_counted,
    test_cli_writes_both_files_per_document_and_a_summary,
    test_bad_input_directory_exits_1,
    test_real_corpus_counts_are_pinned,
    test_real_corpus_known_quirks_are_pinned,
    test_real_corpus_extraction_is_deterministic,
]


if __name__ == "__main__":
    for test in ALL_TESTS:
        test()
    print(f"\n{PASSED} checks passed, {len(FAILED)} failed")
    if FAILED:
        raise SystemExit(1)
