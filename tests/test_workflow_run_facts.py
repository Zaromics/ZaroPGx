"""The workflow diagram says what the run produced, from what the run recorded.

The diagram showed which steps ran and nothing of what they yielded: an mtDNA step
on a VCF without chrM calls was drawn sending MT-RNR1 to PharmCAT and a haplogroup
to the reports when it had produced neither. generator.workflow_run_facts gathers
what the run recorded -- versions, step times, job_steps output, mtdna_result.json,
the summary's rows -- and leaves out whatever it did not record.
"""

from __future__ import annotations

from app.reports.generator import workflow_run_facts

PLATFORM = [
    {"name": "ZaroPGx", "version": "0.4.0"},
    {"name": "PyPGx", "version": "0.27.0"},
    {"name": "OptiType", "version": "not recorded"},
]


def _record(name, seconds=None, output=None, status="completed"):
    return {"name": name, "status": status, "seconds": seconds, "output": output or {}}


def _gene(name, called=True, by="C"):
    return {
        "gene": name,
        "diplotype": "*1/*2" if called else "Unknown/Unknown",
        "called_by": by,
    }


def _facts(records=(), genes=(), mtdna=None, **kwargs):
    arguments = {
        "absent_to_ref": None,
        "drug_count": None,
        "outputs": [],
        "summary": None,
        "input_build": None,
    }
    arguments.update(kwargs)
    return workflow_run_facts(list(records), PLATFORM, list(genes), mtdna, **arguments)


def test_versions_and_times_as_recorded():
    facts = _facts([_record("pypgx_analysis", 308), _record("mtdna_analysis")])
    # "not recorded" is no version to print beside a tool.
    assert facts.versions == {"ZaroPGx": "0.4.0", "PyPGx": "0.27.0"}
    assert facts.seconds == {"pypgx_analysis": 308}


def test_pharmcat_repeats_the_summarys_count_and_the_drugs_section():
    summary = [{"called": True}] * 22 + [{"called": False}] * 3
    facts = _facts(
        [_record("pharmcat_analysis")],
        summary=summary,
        drug_count=99,
        absent_to_ref=True,
    )
    assert facts.details["pharmcat_analysis"] == [
        "22 of 25 genes called",
        "99 drugs with guidance",
        "positions absent from the file read as reference",
    ]
    plain = _facts([_record("pharmcat_analysis")], summary=summary, absent_to_ref=False)
    assert plain.details["pharmcat_analysis"] == ["22 of 25 genes called"]


def test_mtdna_on_a_vcf_without_chrm_produced_nothing():
    facts = _facts(
        [_record("mtdna_analysis")],
        mtdna={
            "haplogroup": None,
            "mt_rnr1": None,
            "mt_rnr1_no_call_reason": "no_chrm_data",
        },
    )
    assert facts.details["mtdna_analysis"] == [
        "no calls on chrM: no haplogroup, no MT-RNR1"
    ]
    assert not facts.made("mt_rnr1") and not facts.made("haplogroup")


def test_mtdna_that_called_says_what():
    facts = _facts(
        [_record("mtdna_analysis")],
        mtdna={"haplogroup": "H7c2", "mt_rnr1": "Reference"},
    )
    assert facts.details["mtdna_analysis"] == ["haplogroup H7c2 · MT-RNR1 Reference"]
    assert facts.made("mt_rnr1") and facts.made("haplogroup")


def test_no_mtdna_result_claims_nothing_either_way():
    facts = _facts([_record("mtdna_analysis")])
    assert "mtdna_analysis" not in facts.details
    assert facts.made("mt_rnr1") and facts.made("haplogroup")


def test_read_genotyping_says_how_many_of_pharmcats_positions_it_called():
    positions = {
        "n_pharmcat_positions": 1226,
        "n_reference": 1160,
        "n_variant": 41,
        "n_no_reads": 19,
        "n_uncertain": 6,
    }
    facts = _facts([_record("pypgx_bam2vcf", output={"pharmcat_positions": positions})])
    assert facts.details["pypgx_bam2vcf"] == [
        "PharmCAT's 1,226 positions: 1,201 called, 19 no reads, 6 uncertain"
    ]


def test_star_alleles_and_the_genes_pharmcat_does_not_call():
    genes = [_gene("CYP2D6", by="P"), _gene("CYP2C19"), _gene("CYP2A6", by="P")]
    facts = _facts(
        [_record("pypgx_analysis", output={"successful_genes": 66, "total_genes": 66})],
        genes,
    )
    assert facts.details["pypgx_analysis"] == ["66 of 66 genes genotyped"]
    assert facts.made("cyp2d6") and facts.made("other_genes")
    uncalled = _facts(
        [_record("pypgx_analysis")], [_gene("CYP2D6", called=False, by="P")]
    )
    assert not uncalled.made("cyp2d6") and not uncalled.made("other_genes")


def test_hla_typing_names_the_genes_typed():
    typed = _facts(
        [_record("hla_typing")],
        [_gene("HLA-A", by="O"), _gene("HLA-B", by="O"), _gene("HLA-C", by="O")],
    )
    assert typed.details["hla_typing"] == ["HLA-A, HLA-B, HLA-C typed"]
    assert typed.made("hla") and typed.made("hla_c")
    untyped = _facts([_record("hla_typing")], [_gene("HLA-A", called=False)])
    assert untyped.details["hla_typing"] == ["no HLA allele called"]
    assert not untyped.made("hla") and not untyped.made("hla_c")
    # A failed step is drawn as failed, not as having called nothing.
    failed = _facts([_record("hla_typing", status="failed")])
    assert "hla_typing" not in failed.details


def test_the_input_build_is_said_only_when_recorded():
    lifted = _facts(
        [
            _record(
                "liftover",
                output={
                    "n_lifted": 4812345,
                    "n_rejected": 2210,
                    "source_build": "GRCh37",
                    "target_build": "GRCh38",
                },
            )
        ],
        input_build="GRCh37",
    )
    assert lifted.input_detail == "GRCh37, lifted to GRCh38"
    assert lifted.details["liftover"] == [
        "4,812,345 variants lifted, 2,210 could not be"
    ]
    assert _facts(input_build="GRCh38").input_detail == "GRCh38"
    # A BAM's build is not recorded: nothing is said, rather than a guess.
    assert _facts().input_detail == ""
    # A liftover that did not complete leaves the build unsaid.
    failed = _facts([_record("liftover", status="failed")], input_build="GRCh37")
    assert failed.input_detail == ""


def test_the_reports_written():
    facts = _facts(
        [_record("report_generation", status="running")],
        outputs=["PDF, HTML, interactive HTML", "FHIR R4 bundle"],
    )
    assert facts.details["report_generation"] == [
        "PDF, HTML, interactive HTML",
        "FHIR R4 bundle",
    ]


def test_nothing_recorded_adds_nothing():
    facts = _facts()
    assert facts.details == {} and facts.seconds == {} and facts.input_detail == ""
