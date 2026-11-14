"""Reading a 23andMe or AncestryDNA export onto PharmCAT's own positions.

The design decision this module pins is WHY the mapping is keyed on the rs identifier
rather than on the coordinates the file carries. The obvious route -- `bcftools convert
--tsv2vcf` then the liftover the GRCh37 VCF lane already has -- silently drops
rs6025 (Factor V Leiden), because a chip's homozygous-reference row is `REF=T ALT=.` and
Picard cannot swap a reference allele with an ALT that is not there. Seven of PharmCAT's
1,226 positions are exposed to that. A chip reports observed BASES, which belong to the
DNA and not to a reference, so the rsID is the stable key and PharmCAT's own GRCh38
record supplies REF/ALT.

The other half is the vendor formats, each of which fails silently rather than loudly if
read naively -- AncestryDNA's two allele columns most of all.
"""

import pytest

from app.api.utils.consumer_array import (
    Position,
    array_genotypes,
    map_to_positions,
    positions_by_rsid,
    vcf_text,
)

# PharmCAT's real records for these rsIDs, verbatim from pharmcat_positions.vcf.
# rs6025 is the one that matters: GRCh38 carries C as reference and T as the variant,
# while GRCh37 carries T as reference -- which is why a lift-then-convert path rejects
# a chip's reference row here and loses Factor V Leiden entirely.
POSITIONS_VCF = "\n".join(
    [
        "##fileformat=VCFv4.2",
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPharmCAT",
        "chr1\t169549811\trs6025\tC\tT\t.\tPASS\tPX=F5\tGT\t0/0",
        "chr4\t88131171\trs2231142\tG\tT\t.\tPASS\tPX=ABCG2\tGT\t0/0",
        "chr10\t94761900\trs12248560\tC\tT\t.\tPASS\tPX=CYP2C19\tGT\t0/0",
        "chr10\t94781859\trs4244285\tG\tA\t.\tPASS\tPX=CYP2C19\tGT\t0/0",
        "chr11\t46739505\trs1799963\tG\tA\t.\tPASS\tPX=F2\tGT\t0/0",
        "chr16\t31096368\trs9923231\tC\tT\t.\tPASS\tPX=VKORC1\tGT\t0/0",
        "chr19\t39248147\trs12979860\tC\tT\t.\tPASS\tPX=IFNL3\tGT\t0/0",
        # A multi-allelic record and one with no usable ID, both real shapes.
        "chr10\t94942309\trs7900194\tG\tA,T\t.\tPASS\tPX=CYP2C9\tGT\t0/0",
        "chr22\t42126611\t.\tC\tT\t.\tPASS\tPX=CYP2D6\tGT\t0/0",
    ]
)


@pytest.fixture()
def by_rsid():
    return positions_by_rsid(POSITIONS_VCF.splitlines())


def _chip(rows, vendor="23andme"):
    """A vendor export holding these (rsid, genotype) pairs."""
    if vendor == "23andme":
        head = ["# rsid\tchromosome\tposition\tgenotype"]
        body = [f"{r}\t1\t100\t{g}" for r, g in rows]
    else:
        head = ["rsid\tchromosome\tposition\tallele1\tallele2"]
        body = [f"{r}\t1\t100\t{g[0]}\t{g[1] if len(g) > 1 else g[0]}" for r, g in rows]
    return head + body


# --------------------------------------------------------------------------
# The position index
# --------------------------------------------------------------------------


def test_only_positions_with_an_rs_identifier_are_reachable(by_rsid):
    """208 of PharmCAT's 1,226 positions carry no usable ID, so no chip can reach them.
    They are left out rather than given a placeholder key."""
    assert "rs6025" in by_rsid
    assert len(by_rsid) == 8, sorted(by_rsid)
    assert all(k.startswith("rs") for k in by_rsid)


def test_a_positions_record_keeps_pharmcats_own_alleles(by_rsid):
    """PharmCAT's record is the authority on which allele is the reference, and on
    GRCh38 that differs from GRCh37 at rs6025."""
    assert by_rsid["rs6025"] == Position(
        chrom="chr1", pos=169549811, ref="C", alts=("T",), gene="F5"
    )
    assert by_rsid["rs7900194"].alts == ("A", "T"), "multi-allelic records keep both"


# --------------------------------------------------------------------------
# The mapping, and the build problem it exists to avoid
# --------------------------------------------------------------------------


def test_factor_v_leiden_survives_the_mapping(by_rsid):
    """The measured failure of the obvious design. GRCh37's reference at rs6025 is T and
    GRCh38's is C, so a chip reading TT is homozygous for the Leiden allele in GRCh38
    terms. Converting to a b37 VCF and lifting drops this row as MismatchedRefAllele;
    keying on the rsID gets it right."""
    reading = array_genotypes(_chip([("rs6025", "TT")]))

    mapped = map_to_positions(reading.genotypes, by_rsid)

    assert mapped.unmappable == []
    assert [(r, p.chrom, p.pos, c) for r, p, c in mapped.records] == [
        ("rs6025", "chr1", 169549811, "1/1")
    ]


@pytest.mark.parametrize(
    "genotype,expected",
    [
        ("CC", "0/0"),
        ("CT", "0/1"),
        ("TC", "0/1"),
        ("TT", "1/1"),
    ],
)
def test_the_genotype_is_translated_against_pharmcats_alleles(
    by_rsid, genotype, expected
):
    """Allele order in the chip's two letters carries no phase information, so TC and CT
    are the same unphased genotype."""
    reading = array_genotypes(_chip([("rs9923231", genotype)]))

    mapped = map_to_positions(reading.genotypes, by_rsid)

    assert mapped.records[0][2] == expected


def test_a_multi_allelic_record_indexes_the_right_alt(by_rsid):
    """rs7900194 is `G` with ALTs `A,T`: a chip reading TT is 2/2, not 1/1. Taking the
    first ALT would report CYP2C9*8 where the data says something else."""
    reading = array_genotypes(_chip([("rs7900194", "TT")]))

    mapped = map_to_positions(reading.genotypes, by_rsid)

    assert mapped.records[0][2] == "2/2"


def test_a_base_matching_neither_allele_is_unmappable_not_forced(by_rsid):
    """A strand problem or an allele PharmCAT's record does not list. Forcing it onto
    the nearest allele would invent a genotype; dropping it silently would hide a
    systematic strand error behind a thin result. So it is named."""
    reading = array_genotypes(_chip([("rs9923231", "AG"), ("rs6025", "CC")]))

    mapped = map_to_positions(reading.genotypes, by_rsid)

    assert mapped.unmappable == ["rs9923231"]
    assert [r for r, _, _ in mapped.records] == ["rs6025"], "the good row still maps"


def test_rows_that_are_not_pharmcat_positions_are_counted_not_dropped_silently(by_rsid):
    """Most of a 600,000-row export is off-panel and that is not a problem -- but the
    numbers have to add up, so it is counted."""
    reading = array_genotypes(_chip([("rs6025", "CC"), ("rs99999999", "AA")]))

    mapped = map_to_positions(reading.genotypes, by_rsid)

    assert mapped.off_panel == 1
    assert len(mapped.records) == 1


def test_the_records_are_sorted_and_name_their_genes(by_rsid):
    reading = array_genotypes(
        _chip([("rs12979860", "CT"), ("rs6025", "CC"), ("rs2231142", "GT")])
    )

    mapped = map_to_positions(reading.genotypes, by_rsid)

    assert [p.chrom for _, p, _ in mapped.records] == ["chr1", "chr19", "chr4"]
    assert mapped.genes == {"F5": 1, "IFNL3": 1, "ABCG2": 1}


# --------------------------------------------------------------------------
# The vendor formats, each of which fails silently if read naively
# --------------------------------------------------------------------------


def test_ancestrydnas_two_allele_columns_are_joined(by_rsid):
    """THE trap. Fed to `bcftools convert --tsv2vcf` as-is, AncestryDNA's
    `rs6025 1 169519049 T T` comes out `T N 0/1`: bcftools reads only the first allele
    column and invents an `N`, turning a reference call into a heterozygote against an
    allele that does not exist."""
    reading = array_genotypes(_chip([("rs6025", "CC")], vendor="ancestrydna"))

    assert reading.vendor_shape == "ancestrydna"
    assert reading.genotypes == {"rs6025": "CC"}
    assert map_to_positions(reading.genotypes, by_rsid).records[0][2] == "0/0"


def test_the_vendor_shape_is_read_from_the_file_not_a_label():
    """A mislabelled AncestryDNA file read as 23andMe's shape does not error -- it
    produces wrong genotypes. So the field count decides."""
    assert array_genotypes(_chip([("rs6025", "CC")])).vendor_shape == "23andme"
    assert (
        array_genotypes(_chip([("rs6025", "CC")], vendor="ancestrydna")).vendor_shape
        == "ancestrydna"
    )


def test_a_row_disagreeing_with_the_files_own_shape_is_dropped():
    """A file that contradicts itself about its format is not one to guess about."""
    lines = _chip([("rs6025", "CC")]) + ["rs2231142\t4\t88131171\tG\tT"]

    reading = array_genotypes(lines)

    assert reading.malformed == 1
    assert list(reading.genotypes) == ["rs6025"]


@pytest.mark.parametrize("genotype", ["--", "NN", "..", "00", "DD", "II"])
def test_a_no_call_is_counted_and_left_out(genotype):
    """A position the chip tried and failed to read is not a position it does not carry,
    and emitting a `./.` would make PharmCAT treat it as covered-but-uncertain rather
    than absent."""
    reading = array_genotypes(_chip([("rs6025", genotype)]))

    assert reading.no_calls == 1
    assert reading.genotypes == {}


def test_the_indel_coding_is_unrepresentable_and_counted():
    """`I`/`D` name no sequence -- UGT1A1*28's TA repeat is one of these rows. Writing a
    base would be inventing one."""
    reading = array_genotypes(_chip([("rs3064744", "DI"), ("rs6025", "CC")]))

    assert reading.indels == 1
    assert list(reading.genotypes) == ["rs6025"]


def test_an_internal_probe_id_cannot_be_mapped():
    """23andMe's own `i`-prefixed probe ids name no dbSNP record, so nothing can be
    looked up from them."""
    reading = array_genotypes(_chip([("i3002742", "AA"), ("rs6025", "CC")]))

    assert list(reading.genotypes) == ["rs6025"]
    assert reading.malformed == 1


def test_a_repeated_rsid_keeps_the_first_and_counts_the_rest():
    """Two genotypes for one position cannot both be true, and silently taking the later
    one would be a choice disguised as parsing."""
    reading = array_genotypes(_chip([("rs6025", "CC"), ("rs6025", "TT")]))

    assert reading.genotypes == {"rs6025": "CC"}
    assert reading.duplicated == 1


def test_the_vendor_header_rows_are_skipped():
    """23andMe comments its header; AncestryDNA does not, so that one is recognised by
    its first field or it would be parsed as a variant at position "position"."""
    assert array_genotypes(_chip([("rs6025", "CC")])).total == 1
    assert array_genotypes(_chip([("rs6025", "CC")], vendor="ancestrydna")).total == 1


def test_an_empty_or_commented_file_reads_as_nothing():
    reading = array_genotypes(["# 23andMe", "", "   "])

    assert reading.genotypes == {}
    assert reading.vendor_shape is None
    assert reading.total == 0


# --------------------------------------------------------------------------
# The VCF
# --------------------------------------------------------------------------


def test_the_vcf_is_grch38_on_pharmcats_positions(by_rsid):
    reading = array_genotypes(_chip([("rs9923231", "TT"), ("rs6025", "CC")]))
    mapped = map_to_positions(reading.genotypes, by_rsid)

    text = vcf_text(mapped, {"chr1": 248956422, "chr16": 90338345}, sample="CHIP")

    assert text.startswith("##fileformat=VCFv4.2\n")
    assert "##contig=<ID=chr1,length=248956422,assembly=GRCh38>" in text
    assert "##contig=<ID=chr16,length=90338345,assembly=GRCh38>" in text
    body = [line for line in text.splitlines() if not line.startswith("#")]
    assert body == [
        "chr1\t169549811\trs6025\tC\tT\t.\tPASS\t.\tGT\t0/0",
        "chr16\t31096368\trs9923231\tC\tT\t.\tPASS\t.\tGT\t1/1",
    ]
    assert text.rstrip().endswith("1/1"), "and ends with a newline, not mid-record"


def test_only_the_contigs_used_are_declared(by_rsid):
    """GRCh38's full analysis set has ~3,400 contigs and a chip touches a handful."""
    reading = array_genotypes(_chip([("rs6025", "CC")]))
    mapped = map_to_positions(reading.genotypes, by_rsid)

    text = vcf_text(mapped, {"chr1": 248956422, "chr16": 90338345})

    assert text.count("##contig=") == 1
    assert "chr16" not in text


def test_the_vcf_carries_no_coverage_header_lines(by_rsid):
    """``##ZaroPGx_`` lines vouch for absence having been MEASURED, and a chip measures
    nothing at a position it does not carry. Writing them here would be the fabrication
    this lane exists to avoid."""
    reading = array_genotypes(_chip([("rs6025", "CC")]))
    mapped = map_to_positions(reading.genotypes, by_rsid)

    assert "ZaroPGx_" not in vcf_text(mapped, {"chr1": 248956422})


def test_the_sample_column_is_named(by_rsid):
    reading = array_genotypes(_chip([("rs6025", "CC")]))
    mapped = map_to_positions(reading.genotypes, by_rsid)

    assert "\tCHIP\n" in vcf_text(mapped, {}, sample="CHIP")
    assert "\tSAMPLE\n" in vcf_text(mapped, {}, sample="")


# --------------------------------------------------------------------------
# The upload plan: accepted, and honest about what it cannot give
# --------------------------------------------------------------------------


def _array_workflow(file_type):
    from app.api.models import FileAnalysis
    from app.api.utils.file_processor import FileProcessor

    analysis = FileAnalysis(
        file_type=file_type, is_valid=True, is_compressed=False, has_index=False
    )
    return FileProcessor(temp_dir="/tmp").determine_workflow(analysis)


@pytest.mark.parametrize("vendor", ["TWENTYTHREE_AND_ME", "ANCESTRY_DNA"])
def test_a_consumer_array_is_accepted_and_plans_its_conversion(vendor):
    """Accepted as of 2026-11-12. What changed is not the chip: the conversion keys on
    the rs identifier (so Factor V Leiden is not lost to a liftover) and the AMP/CAP
    Tier 1 gate withholds any gene the chip cannot determine."""
    from app.api.models import FileType

    workflow = _array_workflow(getattr(FileType, vendor))

    assert workflow["unsupported"] is False
    assert workflow["unsupported_reason"] in (None, "")
    assert workflow["needs_conversion"] is True
    assert workflow["needs_array_conversion"] is True
    assert workflow["needs_gatk"] is True
    assert workflow["needs_report"] is True
    # Analyzed, and provisionally: under a fifth of PharmCAT's positions.
    assert workflow["is_provisional"] is True


@pytest.mark.parametrize("vendor", ["TWENTYTHREE_AND_ME", "ANCESTRY_DNA"])
def test_pypgx_does_not_run_on_an_array(vendor):
    """THE load-bearing flag, and the original refusal's whole argument answered. PyPGx
    reads an absent position as homozygous reference (pypgx#142) and its outside calls
    override PharmCAT's no-calls, so running it would reintroduce the confident wrong
    answer. Nothing is lost: PyPGx's value here is copy number from read depth, and a
    chip has no reads."""
    from app.api.models import FileType

    workflow = _array_workflow(getattr(FileType, vendor))

    assert workflow["needs_pypgx"] is False
    assert workflow["needs_pypgx_bam2vcf"] is False
    # Nor anything else that needs reads.
    assert workflow["needs_hla"] is False
    assert workflow["needs_mtdna"] is False
    assert workflow["needs_alignment"] is False


@pytest.mark.parametrize("vendor", ["TWENTYTHREE_AND_ME", "ANCESTRY_DNA"])
def test_an_array_is_never_scheduled_for_a_liftover(vendor):
    """The output is GRCh38 by construction -- its coordinates come from PharmCAT's own
    GRCh38 list, not from the chip -- so lifting it would move every position by the
    difference between the builds."""
    from app.api.models import FileType

    assert _array_workflow(getattr(FileType, vendor))["needs_liftover"] is False


def test_the_plan_says_what_the_chip_cannot_give():
    from app.api.models import FileType

    workflow = _array_workflow(FileType.TWENTYTHREE_AND_ME)
    text = " ".join(workflow["warnings"] + workflow["recommendations"]).lower()

    # the measured ceiling, before the job runs
    assert "229 of the 1,226" in text or "229" in text
    assert "18.7%" in text
    # what no chip can do, and why PyPGx is not the answer to it
    assert "duplications or deletions" in text
    assert "a chip has no reads" in text
    # the gate, named as the thing that stops a normal-looking wrong answer
    assert "amp/cap minimum set" in text
    assert "not called" in text
    # and the better input
    assert "bam, cram or sam" in text


def test_the_plan_does_not_promise_a_step_this_lane_does_not_run():
    """Copy that promised a step this lane never runs would be the aspirational planning
    this repo forbids.

    "liftover" may appear only where the plan DENIES one, which is worth saying: a
    reader who knows a chip's coordinates are GRCh37 will expect a liftover and should
    be told why there is none. Same shape as the gVCF plan's rule about the command that
    deletes variants -- allowed as a prohibition, never as an offer.
    """
    from app.api.models import FileType

    workflow = _array_workflow(FileType.TWENTYTHREE_AND_ME)
    entries = workflow["warnings"] + workflow["recommendations"]

    assert "lifted over" not in " ".join(entries).lower()
    for entry in entries:
        lowered = entry.lower()
        if "liftover" in lowered:
            assert (
                "depends on a liftover" in lowered
            ), f"liftover may only appear as a denial: {entry}"
    assert "run pypgx" not in " ".join(entries).lower()


def test_the_pipeline_will_accept_the_type_it_is_handed():
    """NEXTFLOW_INPUT_TYPES exists to keep an input off the queue when the pipeline
    cannot finish it. Both vendor types are now on it, and main.nf has the branch --
    tests/test_input_type_honesty.py pins the other half of that agreement."""
    from app.api.routes import upload_router

    assert "23andme" in upload_router.NEXTFLOW_INPUT_TYPES
    assert "ancestry" in upload_router.NEXTFLOW_INPUT_TYPES


def test_the_conversion_step_is_registered_with_a_stage_and_a_duration():
    """A step name main.nf posts with no template is never minted onto the Job, and one
    with no stage is silently reported as ANALYSIS."""
    from app.services.workflow_progress_calculator import WorkflowProgressCalculator
    from app.services.workflow_stages import STEP_TO_STAGE, WorkflowStage

    assert STEP_TO_STAGE["array_to_vcf"] is WorkflowStage.GATK
    assert "array_to_vcf" in WorkflowProgressCalculator.STEP_EXPECTED_SECONDS
    assert "array_to_vcf" in WorkflowProgressCalculator.CANONICAL_STEP_ORDER


def test_main_nf_refuses_to_run_pypgx_on_an_array():
    """A correctness guard, not a convenience one.

    PyPGx reads a position the chip does not carry as homozygous reference and its
    outside calls override PharmCAT's no-calls -- the confident wrong answer these
    inputs were refused for. The app always sets the flag (needs_pypgx=False becomes
    --skip_pypgx), so this fires only for a pipeline run made by hand; FOUND by previewing
    the DAG, where the combination died at `pypgx_result.outside.ifEmpty(...)` with
    "Missing process or function", which says nothing about what is wrong.
    """
    from pathlib import Path

    main_nf = (
        Path(__file__).resolve().parent.parent / "pipelines" / "pgx" / "main.nf"
    ).read_text(encoding="utf-8")

    assert (
        "!params.skip_pypgx && ['23andme', 'ancestry'].contains(params.input_type)"
        in main_nf
    )
    assert "requires --skip_pypgx" in main_nf
    # The reason, not just the rule: a guard whose message does not say why invites
    # being switched off.
    assert "homozygous reference" in main_nf
