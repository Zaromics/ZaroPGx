"""GRCh37/hg19 upload copy honesty (BACKLOG 1 / 49 / 120 / 221 -- honesty half).

The history of this module is three swings of a pendulum, and the tests exist to
pin it wherever the copy and the behaviour actually agree:

1. `FileProcessor.determine_workflow` used to *promise* a liftover that did not
   exist ("Step 0 ... (TO DO)", "bcftools' liftover is used") -- no liftover step
   existed anywhere in app/, docker/ or pipelines/.
2. The fix pinned the copy to the then-truth: GRCh37 was analysed on its original
   coordinates, provisionally, and the user was told to convert it themselves.
3. NOW THE LIFTOVER IS REAL. gatk-api's /liftover-vcf runs Picard LiftoverVcf
   (a genuine coordinate conversion against the GRCh38 reference, UCSC
   hg19ToHg38 chain -- not the contig rename of the deleted first attempt), and
   pipelines/pgx/main.nf routes a VCF through it whenever the app detects a
   GRCh37/hg19 build. So a GRCh37 VCF is *supported*: not unsupported, not
   provisional, `needs_liftover=True`, and the copy says the file will be lifted
   to GRCh38.

The honesty spirit is unchanged even though the verdict flipped. The copy must
be true of the behaviour, so it must also say what the liftover costs: variants
that cannot be mapped onto GRCh38 are dropped. No overclaiming ("lossless",
"identical"), no leftover convert-it-yourself demands, no internal markers.

What this module deliberately does NOT require: that the copy actively steer the
user toward a natively-GRCh38 file. A test did pin that, and it was retired on
2026-08-29 as stating the obvious to the reader. The distinction is worth keeping
straight -- the copy still may not claim a lifted file is *equivalent* to native
GRCh38 data (`_FORBIDDEN_SUBSTRINGS` below enforces that, and is unchanged); it
simply no longer has to say the preferable thing out loud.

These tests assert on the actual returned strings, not on source text, so a
regression in either direction -- back to "we don't lift" copy over a pipeline
that lifts, or ahead to loss-free promises the tool cannot keep -- is caught
even if the exact wording changes.
"""

from app.api.models import FileType, SequencingProfile, VCFHeaderInfo
from app.api.utils.file_processor import FileAnalysis, FileProcessor

# Phrases that must never appear in user-visible workflow copy for a GRCh37
# upload. Each pins one specific lie: the internal marker and the "not analysed"
# claim survive from the earlier rounds of this fix, the rest are the
# overclaiming this round could newly introduce -- liftover drops variants, so
# nothing may call it lossless or its results equivalent to native GRCh38 data.
_FORBIDDEN_SUBSTRINGS = [
    "(to do)",  # internal marker leaking into user-visible HTML
    "not be analys",  # the pipeline DOES run and DOES produce a report
    "lossless",  # liftover is not
    "without loss",
    "no variants are lost",
    "identical results",
    "provisional",  # the GRCh37 verdict is not provisional any more; saying so
    # over a real liftover would be the old copy over the new behaviour
]


def _grch37_vcf_info(reference_genome: str = "GRCh37") -> VCFHeaderInfo:
    return VCFHeaderInfo(
        reference_genome=reference_genome,
        sequencing_platform="Illumina",
        sequencing_profile=SequencingProfile.WGS,
        # has_index=True keeps the unrelated "index file ... (TO DO)"
        # recommendation (file_processor.py, index-exists branch) out of the
        # strings under test -- that TO DO belongs to a different defect.
        has_index=True,
        is_bgzipped=True,
        contigs=["chr1", "chr2"],
        sample_count=1,
        variant_count=1000,
    )


def _analysis_for(reference_genome: str) -> FileAnalysis:
    return FileAnalysis(
        file_type=FileType.VCF,
        is_compressed=True,
        has_index=True,
        vcf_info=_grch37_vcf_info(reference_genome),
    )


def _all_user_visible_strings(workflow: dict) -> list:
    strings = list(workflow.get("recommendations") or [])
    strings += list(workflow.get("warnings") or [])
    reason = workflow.get("unsupported_reason")
    if reason:
        strings.append(reason)
    return strings


def test_grch37_upload_is_supported_via_liftover():
    workflow = FileProcessor().determine_workflow(_analysis_for("GRCh37"))

    assert workflow["unsupported"] is False
    assert workflow["unsupported_reason"] is None
    assert workflow["is_provisional"] is False
    assert workflow["needs_liftover"] is True


def test_grch37_upload_carries_the_detected_source_build():
    # main.nf's LiftoverVCF process is triggered by --source_build, which the
    # router reads from this key. It must be the DETECTED build -- the
    # reference_genome form field defaults to hg38 regardless of the file.
    workflow = FileProcessor().determine_workflow(_analysis_for("GRCh37"))
    assert workflow["source_build"] == "GRCh37"


def test_grch37_copy_says_the_file_will_be_lifted_to_grch38():
    workflow = FileProcessor().determine_workflow(_analysis_for("GRCh37"))
    all_strings = [s.lower() for s in _all_user_visible_strings(workflow)]
    assert all_strings, "expected warnings/recommendations for a GRCh37 upload"

    # The one promise the copy now makes must be the one the pipeline keeps:
    # a lift to GRCh38, named as such.
    assert any(
        "lift" in s and ("grch38" in s or "hg38" in s) for s in all_strings
    ), f"no string says the file will be lifted to GRCh38: {all_strings}"


def test_grch37_copy_admits_that_liftover_drops_variants():
    # The honesty half of the flip: liftover is lossy, and saying so is the
    # difference between a supported feature and a new overclaim.
    workflow = FileProcessor().determine_workflow(_analysis_for("GRCh37"))
    all_strings = [s.lower() for s in _all_user_visible_strings(workflow)]

    assert any(
        "drop" in s for s in all_strings
    ), f"no string admits that unliftable variants are dropped: {all_strings}"


def test_grch37_copy_contains_no_internal_markers_or_overclaims():
    workflow = FileProcessor().determine_workflow(_analysis_for("GRCh37"))
    all_strings = _all_user_visible_strings(workflow)
    assert all_strings, "expected warnings/recommendations for a GRCh37 upload"

    for s in all_strings:
        lowered = s.lower()
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden not in lowered, f"forbidden phrase {forbidden!r} in: {s!r}"


def test_grch37_copy_no_longer_tells_the_user_to_convert_it_themselves():
    # The round-2 copy demanded a DIY conversion because ZaroPGx performed none.
    # Now that it performs one, that demand would be the stale copy over the new
    # behaviour -- exactly the class of mismatch this module exists to catch.
    workflow = FileProcessor().determine_workflow(_analysis_for("GRCh37"))
    recs = [r.lower() for r in workflow["recommendations"]]

    assert not any(
        "convert" in r and "yourself" in r for r in recs
    ), f"copy still tells the user to convert the file themselves: {recs}"


def test_hg19_lowercase_naming_also_triggers_the_liftover():
    # Reference-genome detection is case-insensitive and substring-based
    # (file_processor.py normalizes with .lower()); the liftover path must
    # trigger for the "hg19" spelling too, not only "GRCh37".
    workflow = FileProcessor().determine_workflow(_analysis_for("hg19"))
    assert workflow["unsupported"] is False
    assert workflow["needs_liftover"] is True
    assert workflow["source_build"] == "hg19"


def test_grch38_upload_is_not_marked_for_liftover():
    # Sanity/regression guard: the flip must not start lifting files that are
    # already on GRCh38 coordinates.
    workflow = FileProcessor().determine_workflow(_analysis_for("GRCh38"))
    assert workflow["unsupported"] is False
    assert workflow["unsupported_reason"] is None
    assert workflow["needs_liftover"] is False


def test_other_named_builds_keep_the_honest_provisional_copy():
    # No chain is staged for anything but GRCh37->GRCh38, so a third build
    # (T2T, say) keeps the old behaviour AND the old copy: unsupported,
    # provisional, convert-it-yourself. The flip is scoped to GRCh37/hg19.
    workflow = FileProcessor().determine_workflow(_analysis_for("T2T-CHM13"))
    assert workflow["unsupported"] is True
    assert workflow["is_provisional"] is True
    assert workflow["needs_liftover"] is False
    reason = workflow["unsupported_reason"]
    assert "provisional" in reason.lower()
    recs = [r.lower() for r in workflow["recommendations"]]
    assert any("convert" in r and "yourself" in r for r in recs)
