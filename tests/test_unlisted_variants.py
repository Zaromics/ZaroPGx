"""The check for variants no allele definition lists (docker/pypgx/unlisted_variants.py).

What runs here needs no bcftools: HGVS numbering on the MANE transcripts, bcftools
csq's consequence and protein strings, the QC gate and the tiers. The pipeline's own
run of the check (bcftools norm and csq over a VCF) was exercised on NA12878's demo
reads and on that VCF with rare variants of each kind added (2026-10-23).
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parent.parent / "docker" / "pypgx" / "unlisted_variants.py"


@pytest.fixture(scope="module")
def uv():
    spec = importlib.util.spec_from_file_location("zaropgx_unlisted_variants", SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# MANE Select models from Ensembl 116 (the bundle's transcripts.json).
CYP2C19 = {
    "gene": "CYP2C19", "transcript": "ENST00000371321.9", "refseq": "NM_000769.4",
    "chrom": "chr10", "strand": "+",
    "exons": [[94762681, 94762873], [94775058, 94775220], [94775390, 94775539],
              [94780499, 94780659], [94781821, 94781997], [94820496, 94820637],
              [94842837, 94843024], [94849917, 94850058], [94852733, 94855547]],
    "cds": [[94762706, 94762873], [94775058, 94775220], [94775390, 94775539],
            [94780499, 94780659], [94781821, 94781997], [94820496, 94820637],
            [94842837, 94843024], [94849917, 94850058], [94852733, 94852914]],
}
TPMT = {
    "gene": "TPMT", "transcript": "ENST00000309983.5", "refseq": "NM_000367.5",
    "chrom": "chr6", "strand": "-",
    "exons": [[18128311, 18130780], [18132133, 18132177], [18133804, 18133889],
              [18138963, 18139037], [18139665, 18139717], [18143596, 18143728],
              [18147823, 18147915], [18148988, 18149171], [18155033, 18155077]],
    "cds": [[18130668, 18130780], [18132133, 18132177], [18133804, 18133889],
            [18138963, 18139037], [18139665, 18139717], [18143596, 18143728],
            [18147823, 18147915], [18148988, 18149127]],
}


@pytest.mark.parametrize(
    "model, pos, ref, alt, expected",
    [
        # CYP2C19*2, *17 (781 bases before the transcript's first, c.-25) and *3.
        (CYP2C19, 94781859, "G", "A", "c.681G>A"),
        (CYP2C19, 94761900, "C", "T", "c.-806C>T"),
        (CYP2C19, 94780653, "G", "A", "c.636G>A"),
        # The first intron's donor: exon 1's coding part ends at c.168.
        (CYP2C19, 94762874, "G", "A", "c.168+1G>A"),
        # TPMT*3C and *3B, on the minus strand: bases complemented.
        (TPMT, 18130687, "T", "C", "c.719A>G"),
        (TPMT, 18138997, "C", "T", "c.460G>A"),
    ],
)
def test_hgvs_c_numbers_as_the_alleles_are_known(uv, model, pos, ref, alt, expected):
    assert uv.Transcript(model).hgvs_c(model["chrom"], pos, ref, alt) == expected


def test_no_c_name_is_made_up_for_an_indel(uv):
    assert uv.Transcript(CYP2C19).hgvs_c("chr10", 94781859, "GA", "G") is None


@pytest.mark.parametrize(
    "text, frameshift, expected",
    [
        ("949E>949V", False, ("p.Glu949Val", 949)),
        ("390F", False, ("p.Phe390=", 390)),
        ("930Q>930*", False, ("p.Gln930Ter", 930)),
        # DPYD c.2755delT (ClinVar 370956): csq writes the old and new peptides.
        ("918IPTIKDVIGKALQYLG*>918ILPSRM*", True, ("p.Pro919LeufsTer6", 919)),
        ("", False, (None, None)),
    ],
)
def test_protein_change_in_hgvs_form(uv, text, frameshift, expected):
    assert uv.protein_change(text, frameshift) == expected


def test_a_splice_site_consequence_is_read_though_it_names_no_amino_acid(uv):
    """csq gives a splice-site change four fields, not seven; read as too short, the
    CYP2C19 c.168+1G>A planted in NA12878's VCF vanished from the result."""
    rec = {"info": {"BCSQ": "splice_donor|CYP2C19|ENST00000371321|protein_coding"}}
    assert uv.consequences(rec) == [
        {"consequence": "splice_donor", "gene": "CYP2C19", "transcript": "ENST00000371321", "protein": ""}
    ]


def _rec(gt="0/1", ad="15,15", pl="200,0,200", filt="PASS", dp=None):
    fmt = {"GT": gt, "AD": ad, "PL": pl}
    if dp is not None:
        fmt["DP"] = str(dp)
    return {"filter": filt, "format": fmt, "info": {}}


@pytest.mark.parametrize(
    "rec, problem",
    [
        (_rec(), None),
        (_rec(filt="IndelGap"), "filtered (IndelGap)"),
        (_rec(pl="20,0,200"), "genotype quality 20"),  # 20 passes: the second-lowest PL
        (_rec(pl="10,0,200"), "genotype quality 10"),
        (_rec(ad="4,4"), "8 reads"),
        (_rec(ad="28,2"), "7% of reads carry it"),
        (_rec(gt="1/1", ad="6,24", pl="200,60,0"), "80% of reads carry it"),
    ],
)
def test_a_call_too_weak_to_flag_is_held_back(uv, rec, problem):
    found = uv.quality_problems(rec, median_depth=None)
    if problem is None or problem == "genotype quality 20":
        assert found == []
    else:
        assert problem in found


def test_depth_far_over_the_median_reads_as_collapsed_copies(uv):
    assert uv.quality_problems(_rec(ad="40,40"), median_depth=30) == ["80 reads, over 2.5 times the median"]


def test_a_field_the_vcf_does_not_carry_is_not_held_against_it(uv):
    assert uv.quality_problems({"filter": ".", "format": {"GT": "0/1"}, "info": {}}, None) == []


@pytest.mark.parametrize(
    "gene, consequence, protein_pos, am, clinvar, low, tier",
    [
        ("CYP2C19", "stop_gained", 100, None, None, None, "A"),
        ("CYP2C19", "splice_donor", None, None, None, None, "A"),
        # In the last 5% of CYP2C19's 490 codons: the protein may escape decay.
        ("CYP2C19", "stop_gained", 480, None, None, None, "A-low"),
        ("CYP2C19", "frameshift", 100, None, None, "segmental duplication", "A-low"),
        ("CYP2C19", "missense", 50, 0.9613, None, None, "B"),
        ("CYP2C19", "missense", 50, 0.9613, None, "segmental duplication", "B-low"),
        ("CYP2C19", "missense", 50, 0.30, None, None, "B-low"),
        ("CYP2C19", "missense", 50, 0.10, None, None, "none"),
        ("CYP2C19", "missense", 50, None, None, None, "C"),
        ("CYP2C19", "inframe_deletion", 50, None, None, None, "C"),
        ("CYP2C19", "missense&splice_region", 50, None, None, None, "C"),
        ("CYP2C19", "synonymous", 50, None, None, None, "none"),
    ],
)
def test_tiers(uv, gene, consequence, protein_pos, am, clinvar, low, tier):
    transcript = uv.Transcript(CYP2C19)
    assert uv.tier_for(gene, consequence, protein_pos, transcript, am, clinvar, low) == tier


@pytest.mark.parametrize(
    "gene, sig, review, condition, loss",
    [
        # DPYD c.2788C>T (ClinVar 3241364): one star, DPD deficiency.
        ("DPYD", "Likely_pathogenic", "criteria_provided,_single_submitter",
         "Dihydropyrimidine_dehydrogenase_deficiency", True),
        # No assertion criteria: no star, no override.
        ("DPYD", "Likely_pathogenic", "no_assertion_criteria_provided",
         "Dihydropyrimidine_dehydrogenase_deficiency", False),
        ("DPYD", "Conflicting_classifications_of_pathogenicity", "criteria_provided,_conflicting_classifications",
         "Dihydropyrimidine_dehydrogenase_deficiency", False),
        # A condition that is not the gene's drug response: not a loss of function here.
        ("DPYD", "Pathogenic", "criteria_provided,_single_submitter", "Some_other_disease", False),
        # A gene with no drug-response condition listed.
        ("CYP2C9", "Pathogenic", "reviewed_by_expert_panel", "Warfarin_response", False),
    ],
)
def test_clinvar_makes_a_loss_of_function_only_for_the_genes_drug_response(uv, gene, sig, review, condition, loss):
    entry = uv.clinvar_entry({"clnsig": sig, "clnrevstat": review, "clndn": condition, "variation_id": "1"})
    assert uv.clinvar_says_loss(gene, entry) is loss


def test_pypgx_names_include_synonyms_and_skip_structural_codes(uv, tmp_path):
    (tmp_path / "variant-table.csv").write_text(
        "Gene,GRCh37Name,GRCh38Name,rsID,Chromosome,GRCh37Position,GRCh38Position,"
        "GRCh37Allele,GRCh38Allele,Variant,Impact,GRCh37Synonym,GRCh38Synonym\n"
        "CYP2B6,19-41499291-G-GGC,19-40991387-G-GGC,.,19,41499291,40991387,G,G,GGC,N/A,N/A,"
        '"19-40991388-A-G,19-40991390-C-A"\n'
    )
    (tmp_path / "allele-table.csv").write_text(
        "Gene,StarAllele,ActivityScore,Function,GRCh37Core,GRCh37Tag,GRCh38Core,GRCh38Tag,SV\n"
        "CYP2D6,*5,0,No Function,DeletionHet,N/A,DeletionHet,N/A,TRUE\n"
    )
    assert sorted(uv.pypgx_variant_names(tmp_path)) == [
        "19-40991387-G-GGC", "19-40991388-A-G", "19-40991390-C-A",
    ]


def test_without_the_bundle_the_check_says_it_did_not_run(uv, tmp_path):
    result = uv.check(tmp_path / "in.vcf.gz", tmp_path / "ref.fa", tmp_path / "work", bundle=tmp_path / "none")
    assert result["status"] == "not_run"
    assert "genome downloader" in result["reason"]
