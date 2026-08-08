"""GRCh37/hg19 upload copy honesty (BACKLOG 1 / 49 / 120 / 221 -- honesty half).

`FileProcessor.determine_workflow` used to tell GRCh37/hg19 uploaders that
liftover to GRCh38/hg38 happens ("Step 0 ... (TO DO)", "it will proceed to
Step 1", "bcftools' liftover is used") even though app/api/utils/liftover.py
has zero callers anywhere in app/, docker/, or pipelines/. Nothing lifts the
file over.

Round 1 of this fix overcorrected into a second lie: it said the file
"cannot be analysed" / "will not be analysed". In fact nothing in the repo
gates on `unsupported` (every read of it -- app/api/models.py,
upload_router.py, index.html -- is display-only), so a GRCh37 upload still
runs header_analysis -> pypgx -> PharmCAT -> report_generation and the user
gets a report. `workflow["is_provisional"] = True`, six lines below the
reason string, is this codebase's own flag for "we *did* analyse it,
provisionally" -- the designed intent is "analyse provisionally", not
"refuse". The copy must be true of *that* behaviour: GRCh38/hg38 is the only
build ZaroPGx fully supports, so results for any other build are provisional
and should not be relied on, and the user should convert the file themselves
for reliable results.

These tests pin the emitted, user-visible strings (recommendations, warnings,
unsupported_reason) to that truth. They assert on the actual returned
strings, not on source text, so a future regression back to promise-shaped
copy -- or back to a false "not analysed" claim -- is caught even if the
exact wording changes.
"""

from app.api.models import FileType, SequencingProfile, VCFHeaderInfo
from app.api.utils.file_processor import FileAnalysis, FileProcessor

# Phrases that must never appear in user-visible workflow copy. Each pins down
# one specific lie that existed at f4e1bb6, or was introduced by round 1 of
# this very fix, in app/api/utils/file_processor.py.
_FORBIDDEN_SUBSTRINGS = [
    "(to do)",  # internal marker leaking into user-visible HTML
    "will be converted",  # promises ZaroPGx performs the liftover itself
    "will be re-aligned",
    "will be realigned",
    "it will proceed",  # implies an automatic pipeline step that doesn't exist
    "not be analys",  # catches "will/cannot/won't/should ... be analysed|analyzed":
    # the pipeline DOES run and DOES produce a (provisional) report
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


def test_grch37_upload_is_marked_unsupported_with_grch38_only_reason():
    workflow = FileProcessor().determine_workflow(_analysis_for("GRCh37"))

    assert workflow["unsupported"] is True
    reason = workflow["unsupported_reason"]
    assert reason, "unsupported_reason must be set for a non-GRCh38 VCF"
    assert "GRCh37" in reason  # tells the user which build their file actually is
    assert "GRCh38" in reason or "hg38" in reason
    assert "only" in reason.lower()  # names GRCh38-only support, not a mere preference


def test_grch37_upload_stays_provisional_not_refused():
    # Pins the property an independent reviewer caught round 1 contradicting:
    # is_provisional=True means the designed behaviour is "analyse it
    # anyway, but mark the results provisional" -- not "refuse to analyse".
    # unsupported_reason must say so, in those terms, not claim analysis
    # is skipped.
    workflow = FileProcessor().determine_workflow(_analysis_for("GRCh37"))
    assert workflow["unsupported"] is True
    assert workflow["is_provisional"] is True
    reason = workflow["unsupported_reason"]
    assert "provisional" in reason.lower()
    assert "not be relied on" in reason.lower() or "not be reliable" in reason.lower()


def test_grch37_upload_copy_contains_no_internal_markers_or_false_promises():
    workflow = FileProcessor().determine_workflow(_analysis_for("GRCh37"))
    all_strings = _all_user_visible_strings(workflow)
    assert all_strings, "expected warnings/recommendations/reason for a GRCh37 upload"

    for s in all_strings:
        lowered = s.lower()
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden not in lowered, f"forbidden phrase {forbidden!r} in: {s!r}"
        if "liftover" in lowered:
            assert "is used" not in lowered, f"claims a tool 'is used' in: {s!r}"


def test_grch37_upload_tells_user_to_convert_before_uploading():
    workflow = FileProcessor().determine_workflow(_analysis_for("GRCh37"))
    recs = [r.lower() for r in workflow["recommendations"]]

    # The user must be told, in plain language, to lift the file over
    # themselves before uploading -- not that ZaroPGx will do it for them.
    assert any(
        "convert" in r and "yourself" in r for r in recs
    ), f"no recommendation tells the user to convert the file themselves: {recs}"
    assert any("upload" in r for r in recs)


def test_hg19_lowercase_naming_is_also_flagged_unsupported():
    # Reference-genome detection is case-insensitive and substring-based
    # (file_processor.py normalizes with .lower()); confirm the honest-copy
    # path triggers for the "hg19" spelling too, not only "GRCh37".
    workflow = FileProcessor().determine_workflow(_analysis_for("hg19"))
    assert workflow["unsupported"] is True
    assert "hg19" in workflow["unsupported_reason"]


def test_grch38_upload_is_not_marked_unsupported():
    # Sanity/regression guard: the honest-copy rewrite must not start
    # flagging genuinely-supported GRCh38 files as unsupported.
    workflow = FileProcessor().determine_workflow(_analysis_for("GRCh38"))
    assert workflow["unsupported"] is False
    assert workflow["unsupported_reason"] is None
