"""A non-Latin filename must not switch off build detection.

``safe_upload_basename`` runs the client-supplied name through werkzeug's
``secure_filename``, which drops every non-ASCII character. For a name that is
*entirely* non-ASCII apart from its extension, that leaves the extension as the
whole basename: a Cyrillic-spelled ``obrazets.vcf`` is stored as ``upload_vcf``,
and a CJK-spelled one likewise -- no suffix left for anything to read.

``header_inspector.inspect_header`` dispatched on that suffix alone, so it
matched no inspector, returned no header, and ``FileProcessor.analyze_file``
recorded ``reference_genome="unknown"``.

Nothing errored. What happened instead is that every build-keyed decision
quietly changed its answer, because each of them reads exactly that value:

  * a GRCh37/hg19 VCF stopped setting ``needs_liftover`` and was analysed on
    GRCh38 coordinates -- the wrong-locus case the liftover exists to prevent;
  * a T2T-CHM13 VCF stopped being refused and was analysed as GRCh38;
  * the self-contradicting-header warning stopped firing.

All three on a file whose only peculiarity was being named in Cyrillic, Chinese,
Greek or anything else outside ASCII -- and all three silently, because an
undetectable build is a legitimate state this pipeline deliberately does not
guess at, so there was nothing to distinguish "we could not tell" from "we did
not look".

The fix is not to preserve the extension -- a genuinely extension-less upload
has the same problem -- but to stop deriving the format twice:
``_detect_file_type`` has already identified the file by sniffing its content,
and now passes that answer to ``inspect_header`` as ``format_hint``.
"""

import asyncio

import pytest

from app.api.models import FileType
from app.api.utils.file_processor import (
    _HEADER_FORMAT_HINTS,
    FileProcessor,
    safe_upload_basename,
)
from app.api.utils.header_inspector import DISPATCHABLE_FORMATS, inspect_header

# Header only -- no records are needed, because contig lengths are what the
# detector reads. These are CHM13v2.0's (chr22 51324926, chr10 134758134), which
# collide with no GRCh37 or GRCh38 length.
T2T_HEADER = "\n".join(
    [
        "##fileformat=VCFv4.2",
        "##reference=file:///ref/chm13v2.0.fa",
        "##contig=<ID=chr22,length=51324926>",
        "##contig=<ID=chr10,length=134758134>",
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1",
        "chr10\t94761900\trs12248560\tC\tT\t100\tPASS\t.\tGT\t0/1",
        "",
    ]
)

# GRCh37's lengths for the same two chromosomes.
GRCH37_HEADER = "\n".join(
    [
        "##fileformat=VCFv4.2",
        "##contig=<ID=chr10,length=135534747>",
        "##contig=<ID=chr22,length=51304566>",
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">',
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1",
        "chr10\t96521657\trs12248560\tC\tT\t100\tPASS\t.\tGT\t0/1",
        "",
    ]
)


def _analyse(tmp_path, stored_name, header):
    """Analyse a file stored under `stored_name`, as process_files would store it."""
    path = tmp_path / stored_name
    path.write_text(header, encoding="utf-8")
    processor = FileProcessor(temp_dir=str(tmp_path))
    return processor, asyncio.run(processor.analyze_file(str(path)))


@pytest.mark.parametrize(
    "original",
    ["образец.vcf", "标本.vcf"],
    ids=["cyrillic", "cjk"],
)
def test_a_non_ascii_name_is_stored_without_a_suffix(original):
    """The precondition. If werkzeug ever stops doing this, the rest is moot."""
    stored = safe_upload_basename(original)

    assert stored == "vcf"
    # The extension became the whole basename, so suffix-based dispatch has
    # nothing left to dispatch on.
    assert "." not in "upload_{}".format(stored)


def test_a_suffixless_t2t_vcf_is_still_detected_and_still_refused(tmp_path):
    """The bug at the layer that matters: a refusal must not depend on the name."""
    processor, analysis = _analyse(tmp_path, "upload_vcf", T2T_HEADER)

    assert analysis.vcf_info is not None, "no header was read at all"
    assert analysis.vcf_info.reference_genome == "T2T-CHM13v2"

    workflow = processor.determine_workflow(analysis)
    assert workflow["unsupported"] is True
    # Not provisional: is_provisional exempts a VCF from the upload gate, which
    # for CHM13 coordinates would mean analysing them as GRCh38.
    assert workflow["is_provisional"] is False


def test_a_suffixless_grch37_vcf_is_still_lifted(tmp_path):
    """The same hole on the lane where it silently produces wrong coordinates."""
    processor, analysis = _analyse(tmp_path, "upload_vcf", GRCH37_HEADER)

    assert analysis.vcf_info is not None
    assert analysis.vcf_info.reference_genome == "GRCh37"

    workflow = processor.determine_workflow(analysis)
    assert workflow["needs_liftover"] is True
    assert workflow["source_build"] == "GRCh37"


def test_the_suffixless_and_suffixed_paths_agree(tmp_path):
    """The control: the hint is a fallback, so a good name must be unaffected."""
    _, hinted = _analyse(tmp_path, "upload_vcf", T2T_HEADER)
    _, suffixed = _analyse(tmp_path, "upload_sample.vcf", T2T_HEADER)

    assert suffixed.vcf_info is not None and hinted.vcf_info is not None
    assert (
        suffixed.vcf_info.reference_genome == hinted.vcf_info.reference_genome
    ), "the fallback path and the suffix path disagree about the build"


def test_the_hint_never_overrides_a_suffix_the_inspector_can_use(tmp_path):
    """A wrong hint must lose to a readable suffix, or the fallback is an override."""
    path = tmp_path / "upload_sample.vcf"
    path.write_text(T2T_HEADER, encoding="utf-8")

    honest = inspect_header(str(path))
    misled = inspect_header(str(path), format_hint=".bam")

    assert honest.get("metadata", {}).get("reference_genome") == "T2T-CHM13v2"
    assert misled.get("metadata", {}).get("reference_genome") == "T2T-CHM13v2"


def test_every_hint_names_a_format_the_dispatch_branches_on():
    """A hint outside the dispatch is a silent no-op -- the same bug, wearing a fix."""
    unknown = {
        file_type: fmt
        for file_type, fmt in _HEADER_FORMAT_HINTS.items()
        if fmt not in DISPATCHABLE_FORMATS
    }

    assert not unknown, "hints name formats inspect_header cannot dispatch: {}".format(
        unknown
    )


def test_the_hint_table_covers_every_type_whose_header_decides_something():
    """VCF/BCF/gVCF carry the build in ##contig; the alignment types carry it in @SQ."""
    for file_type in (
        FileType.VCF,
        FileType.GVCF,
        FileType.BCF,
        FileType.BAM,
        FileType.CRAM,
        FileType.SAM,
    ):
        assert file_type in _HEADER_FORMAT_HINTS, file_type
