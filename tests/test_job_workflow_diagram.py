"""Each report's workflow diagram is that job's steps, wired as main.nf runs them.

Every report used to show the same generic diagram (app/visualizations/workflow.mmd):
ZIP extraction, an /upload-vcf route that does not exist, an export to a FHIR server,
and no mtDNA, BCF, gVCF or liftover. When the diagram could not be drawn, the
templates fell back to a fixed "Upload -> Detect -> VCF -> PharmCAT -> Reports".
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

import app.reports.generator as generator_module
from app.api.db import Job, JobStep
from app.api.models import JobCreate, StepStatus
from app.services.job_service import JobService
from app.visualizations.job_workflow import (
    INPUT,
    STAGE_OF,
    RunFacts,
    build_job_graph,
    format_duration,
    input_format,
    load_job_file_type,
    load_job_step_records,
    load_job_steps,
    to_html,
    to_mermaid,
)

C = "completed"


def _edges(graph):
    return {(s, t) for s, t, _ in graph.edges}


def _labelled(graph):
    return {(s, t): label for s, t, label in graph.edges}


CRAM_STEPS = [
    ("header_analysis", C),
    ("gatk_cram_sam_to_bam", C),
    ("hla_typing", C),
    ("pypgx_bam2vcf", C),
    ("pypgx_analysis", C),
    ("mtdna_analysis", C),
    ("pharmcat_analysis", C),
    ("report_generation", "running"),
]


def test_an_alignment_runs_hla_before_read_genotyping_and_mtdna_alongside():
    graph = build_job_graph(CRAM_STEPS, "cram")

    assert graph.node(INPUT).title == "Uploaded CRAM file"
    assert graph.node("gatk_cram_sam_to_bam").title == "Convert CRAM to BAM"
    edges = _edges(graph)
    # main.nf starts PyPGxBam2Vcf only once OptiType has finished.
    assert ("hla_typing", "pypgx_bam2vcf") in edges
    assert ("gatk_cram_sam_to_bam", "hla_typing") in edges
    assert ("pypgx_bam2vcf", "pypgx_analysis") in edges
    # mtDNA reads the converted BAM, alongside the rest.
    assert ("gatk_cram_sam_to_bam", "mtdna_analysis") in edges
    labels = _labelled(graph)
    assert labels[("hla_typing", "pharmcat_analysis")] == "HLA-A, HLA-B"
    assert labels[("pypgx_bam2vcf", "pharmcat_analysis")] == "VCF"
    assert labels[("pypgx_analysis", "pharmcat_analysis")] == "CYP2D6"
    assert labels[("mtdna_analysis", "pharmcat_analysis")] == "MT-RNR1"
    # Genes PharmCAT does not call reach the reports directly.
    assert {
        ("pharmcat_analysis", "report_generation"),
        ("pypgx_analysis", "report_generation"),
        ("hla_typing", "report_generation"),
        ("mtdna_analysis", "report_generation"),
    } <= edges


def test_fastq_is_aligned_and_a_bam_goes_straight_to_typing():
    fastq = build_job_graph(
        [("gatk_alignment", C), ("pypgx_bam2vcf", C), ("pharmcat_analysis", C)],
        "fastq",
    )
    assert fastq.node(INPUT).title == "Uploaded FASTQ reads"
    assert ("gatk_alignment", "pypgx_bam2vcf") in _edges(fastq)

    bam = build_job_graph(
        [("hla_typing", C), ("pypgx_bam2vcf", C), ("pharmcat_analysis", C)], "bam"
    )
    assert (INPUT, "hla_typing") in _edges(bam)
    assert bam.node("gatk_cram_sam_to_bam") is None


def test_a_grch37_vcf_is_lifted_and_mtdna_reads_it_before_liftover():
    graph = build_job_graph(
        [
            ("header_analysis", C),
            ("liftover", C),
            ("pypgx_analysis", C),
            ("mtdna_analysis", C),
            ("pharmcat_analysis", C),
        ],
        "vcf",
    )
    edges = _edges(graph)
    assert ("header_analysis", "liftover") in edges
    assert ("liftover", "pypgx_analysis") in edges
    assert _labelled(graph)[("liftover", "pharmcat_analysis")] == "VCF"
    assert ("header_analysis", "mtdna_analysis") in edges
    assert graph.node("hla_typing") is None


@pytest.mark.parametrize(
    "step, fmt, label",
    [("bcf_to_vcf", "bcf", "BCF file"), ("gvcf_to_vcf", "gvcf", "gVCF file")],
)
def test_a_bcf_or_gvcf_is_converted_first(step, fmt, label):
    graph = build_job_graph(
        [(step, C), ("pypgx_analysis", C), ("pharmcat_analysis", C)], "vcf"
    )
    assert graph.node(INPUT).title == f"Uploaded {label}"
    assert (INPUT, step) in _edges(graph)
    assert (step, "pypgx_analysis") in _edges(graph)


def test_a_skipped_or_failed_step_sits_off_the_data_path():
    graph = build_job_graph(
        [
            ("pypgx_analysis", C),
            ("mtdna_analysis", StepStatus.SKIPPED),
            ("pharmcat_analysis", C),
            ("report_generation", "running"),
        ],
        "vcf",
    )
    assert graph.node("mtdna_analysis").off_path
    assert (INPUT, "mtdna_analysis") in graph.dotted
    assert not any("mtdna_analysis" in edge[:2] for edge in graph.edges)
    assert "(skipped)" in to_mermaid(graph)


def test_steps_settle_the_format_over_a_wrong_recorded_type():
    assert input_format("vcf", ["gatk_cram_sam_to_bam", "pypgx_bam2vcf"]) == (
        "cram_or_sam"
    )
    assert input_format("sam", ["gatk_cram_sam_to_bam"]) == "sam"
    assert input_format("vcf", ["hla_typing", "pypgx_bam2vcf"]) == "bam"
    assert input_format("bam", ["gvcf_to_vcf"]) == "gvcf"
    assert input_format(None, ["pharmcat_analysis"]) == "vcf"


def test_the_text_fallback_lists_the_same_steps():
    graph = build_job_graph(CRAM_STEPS, "cram")
    text = to_html(graph)
    assert text.startswith('<div class="workflow-text"><p>Uploaded CRAM file')
    assert text.count("<li>") == len(CRAM_STEPS)
    assert "HLA class I typing <em>(OptiType)</em>" in text
    assert "from " in text  # PharmCAT's several inputs are named


def test_no_recorded_steps_means_no_diagram_not_a_generic_one(monkeypatch):
    def kroki(*a, **k):
        raise AssertionError("nothing to draw")

    monkeypatch.setattr(generator_module, "render_with_kroki", kroki)
    diagram = generator_module.job_workflow_diagram([], "vcf")
    assert not diagram["recorded"] and not diagram["svg"] and not diagram["png"]
    assert "not recorded" in diagram["html"]


def test_kroki_draws_the_jobs_graph(monkeypatch):
    sources = []

    def kroki(source, fmt="svg"):
        sources.append((fmt, source))
        return b'<svg width="10" height="10"></svg>' if fmt == "svg" else b"PNG"

    monkeypatch.setattr(generator_module, "render_with_kroki", kroki)
    diagram = generator_module.job_workflow_diagram(CRAM_STEPS, "cram")

    assert [fmt for fmt, _ in sources] == ["svg", "png"]
    assert sources[0][1] == to_mermaid(build_job_graph(CRAM_STEPS, "cram"))
    assert diagram["png_data_uri"] == "data:image/png;base64,UE5H"
    assert diagram["svg"].startswith("<svg")


def test_kroki_down_leaves_the_text(monkeypatch):
    def kroki(*a, **k):
        raise ConnectionError("kroki is down")

    monkeypatch.setattr(generator_module, "render_with_kroki", kroki)
    diagram = generator_module.job_workflow_diagram(CRAM_STEPS, "cram")
    assert not diagram["svg"] and not diagram["png_data_uri"]
    assert "HLA class I typing" in diagram["html"]


@pytest.fixture
def cram_job(session_factory):
    session = session_factory()
    job = JobService(session).create_job(
        JobCreate(workflow_type="genomic_analysis", name="diagram")
    )
    db_job = session.get(Job, job.id)
    db_job.job_metadata = {"workflow": {"file_type": "cram"}}
    # create_job adds the recipe's default steps; this job has the CRAM lane's.
    session.query(JobStep).filter(JobStep.job_id == job.id).delete()
    started = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    for order, (name, status) in enumerate(CRAM_STEPS, start=1):
        step = JobStep(job_id=job.id, step_name=name, step_order=order, status=status)
        if name == "pypgx_analysis":
            step.started_at = started
            step.completed_at = started + timedelta(seconds=309.4)
            step.output_data = {"successful_genes": 66, "total_genes": 66}
        if name == "hla_typing":
            # Timestamps only: the seconds come from them.
            step.started_at = started
            step.completed_at = started + timedelta(seconds=410)
        session.add(step)
    session.commit()
    yield session, str(job.id)
    session.rollback()
    session.close()


def test_the_steps_and_file_type_come_from_the_job(cram_job):
    session, job_id = cram_job
    assert load_job_steps(session, job_id) == CRAM_STEPS
    assert load_job_file_type(session, job_id) == "cram"
    assert load_job_steps(None, job_id) == []
    assert load_job_steps(session, "not-a-uuid") == []
    records = {r["name"]: r for r in load_job_step_records(session, job_id)}
    assert list(records) == [name for name, _ in CRAM_STEPS]
    assert records["hla_typing"]["seconds"] == 410
    assert records["pypgx_analysis"]["output"] == {
        "successful_genes": 66,
        "total_genes": 66,
    }
    assert records["mtdna_analysis"]["seconds"] is None


def test_the_reports_draw_this_job(cram_job, tmp_path, monkeypatch):
    session, job_id = cram_job
    drawn = []

    def kroki(source, fmt="svg"):
        drawn.append(source)
        return b"<svg><text>this job</text></svg>" if fmt == "svg" else b"PNG"

    monkeypatch.setattr(generator_module, "render_with_kroki", kroki)
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG,
                key,
                key in ("write_html", "write_interactive_html", "write_workflow_png"),
            )
    monkeypatch.setattr(
        generator_module.PharmCATDataService,
        "get_pharmcat_data_for_workflow",
        lambda self, job: None,
    )
    result = generator_module.generate_report(
        {"data": {"genes": [], "drugRecommendations": []}},
        str(tmp_path),
        {"id": "p1"},
        job_id=job_id,
        db_session=session,
    )

    assert drawn and "Convert CRAM to BAM" in drawn[0]
    # What the run recorded: how long a step ran, what it produced.
    assert "6 min 50 s" in drawn[0]
    assert "66 of 66 genes genotyped" in drawn[0]
    assert (tmp_path / f"{job_id}_workflow.png").read_bytes() == b"PNG"
    # Every report shows the PNG (Kroki's SVG lost its node labels in WeasyPrint
    # and was restyled by the HTML reports' CSS).
    for key in ("html_path", "interactive_html_path"):
        page = (tmp_path / result[key].split("/")[-1]).read_text(encoding="utf-8")
        assert 'src="data:image/png;base64,UE5H"' in page
        assert "this job</text>" not in page


def test_the_templates_never_invent_a_pipeline():
    root = generator_module.os.path.dirname(generator_module.__file__)
    for name in ("report_template.html", "interactive_report.html"):
        with open(f"{root}/templates/{name}", encoding="utf-8") as fh:
            template = fh.read()
        assert ">Detect</span>" not in template
        assert "The steps of this run were not recorded." in template


def test_the_diagram_is_drawn_large_with_the_tool_beneath_the_step():
    """At Mermaid's 16px default, Kroki's PNG of a run was about 650px wide and
    soft printed across a page. It is laid out at 30px; each step is bold, its tool
    in italics beneath it."""
    source = to_mermaid(build_job_graph(CRAM_STEPS, "cram"))
    assert source.startswith("%%{init:")
    assert "'fontSize': '30px'" in source and "'htmlLabels': false" in source
    assert 'hla_typing["`**HLA class I typing**\n_OptiType_`"]:::calling' in source


VCF_STEPS = [
    ("header_analysis", C),
    ("pypgx_analysis", C),
    ("mtdna_analysis", C),
    ("pharmcat_analysis", C),
    ("report_generation", "running"),
]


def _node_line(source, node_id):
    """A node's whole definition: its label runs over several lines."""
    match = re.search(
        rf"^  {node_id}(?:\[|\(\[)\"`.*?`\"(?:\]|\]\)):::\w+$",
        source,
        flags=re.M | re.S,
    )
    assert match, node_id
    return match.group(0)


def test_each_step_shows_its_tools_version_its_time_and_what_it_produced():
    facts = RunFacts(
        versions={"OptiType": "1.5.0", "PyPGx": "0.27.0"},
        seconds={"hla_typing": 410, "pypgx_analysis": 309.4},
        details={"pypgx_analysis": ["66 of 66 genes genotyped"]},
        input_detail="GRCh38",
    )
    source = to_mermaid(build_job_graph(CRAM_STEPS, "cram", facts))
    assert "_OptiType 1.5.0 · 6 min 50 s_" in _node_line(source, "hla_typing")
    assert "_PyPGx 0.27.0 · 5 min 9 s_\n66 of 66 genes genotyped`" in _node_line(
        source, "pypgx_analysis"
    )
    # A tool whose version the run did not record is named without one.
    assert "_PyPGx 0.27.0, bcftools_" in _node_line(source, "pypgx_bam2vcf")
    assert "_mtDNA-Server 2_" in _node_line(source, "mtdna_analysis")
    assert "**Uploaded CRAM file**\nGRCh38`" in _node_line(source, INPUT)


def test_a_step_that_produced_nothing_is_grey_and_its_calls_dashed():
    """mtDNA on a VCF without chrM calls ran and produced no MT-RNR1 call and no
    haplogroup. The diagram drew both flowing on regardless."""
    facts = RunFacts(
        produced={"mt_rnr1": False, "haplogroup": False},
        details={"mtdna_analysis": ["no calls on chrM: no haplogroup, no MT-RNR1"]},
    )
    graph = build_job_graph(VCF_STEPS, "vcf", facts)
    mtdna = graph.node("mtdna_analysis")
    assert mtdna.empty and not mtdna.off_path
    assert ("header_analysis", "mtdna_analysis") in _edges(graph)
    assert not any(s == "mtdna_analysis" for s, _ in _edges(graph))
    assert ("mtdna_analysis", "pharmcat_analysis", "MT-RNR1") in graph.empty_edges
    assert ("mtdna_analysis", "report_generation", "haplogroup") in graph.empty_edges
    source = to_mermaid(graph)
    assert _node_line(source, "mtdna_analysis").endswith(":::off")
    assert "mtdna_analysis -.->|MT-RNR1: none| pharmcat_analysis" in source
    assert "mtdna_analysis -.->|haplogroup: none| report_generation" in source
    assert 'key_off["Produced nothing"]:::off' in source


def test_only_what_is_missing_is_dashed():
    graph = build_job_graph(
        VCF_STEPS, "vcf", RunFacts(produced={"mt_rnr1": True, "haplogroup": False})
    )
    assert not graph.node("mtdna_analysis").empty
    assert _labelled(graph)[("mtdna_analysis", "pharmcat_analysis")] == "MT-RNR1"
    assert graph.empty_edges == [("mtdna_analysis", "report_generation", "haplogroup")]


def test_hla_c_goes_to_the_reports_and_no_hla_call_is_dashed():
    graph = build_job_graph(CRAM_STEPS, "cram")
    assert _labelled(graph)[("hla_typing", "report_generation")] == "HLA-C"
    assert _labelled(graph)[("hla_typing", "pharmcat_analysis")] == "HLA-A, HLA-B"
    untyped = build_job_graph(
        CRAM_STEPS, "cram", RunFacts(produced={"hla": False, "hla_c": False})
    )
    assert untyped.node("hla_typing").empty
    assert ("hla_typing", "pharmcat_analysis", "HLA-A, HLA-B") in untyped.empty_edges
    # HLA typing still hands the reads on: read genotyping waits for it.
    assert ("hla_typing", "pypgx_bam2vcf") in _edges(untyped)


def test_each_step_is_coloured_by_its_stage_and_the_key_names_the_stages_run():
    source = to_mermaid(build_job_graph(CRAM_STEPS, "cram"))
    for node_id, stage in STAGE_OF.items():
        if node_id in dict(CRAM_STEPS) or node_id == INPUT:
            assert _node_line(source, node_id).endswith(f":::{stage}"), node_id
    key = re.findall(r'key_(\w+)\["([^"]+)"\]', source)
    assert key == [
        ("input", "Input"),
        ("preparation", "Preparation"),
        ("calling", "Calling"),
        ("interpretation", "Interpretation"),
        ("reports", "Reports"),
    ]
    # A VCF has no preparation; with nothing grey, the key has no grey entry.
    vcf = to_mermaid(build_job_graph(VCF_STEPS, "vcf"))
    assert "key_preparation" not in vcf and "key_off" not in vcf
    # Mermaid reads a subgraph sharing a node's id ("input") as a cycle.
    assert not re.search(r"subgraph (input|header_analysis)\b", source)


@pytest.mark.parametrize(
    "seconds, text",
    [
        (None, ""),
        (0, "under 1 s"),
        (0.4, "under 1 s"),
        (27, "27 s"),
        (309.4, "5 min 9 s"),
        (600, "10 min"),
        (3600, "1 h"),
        (5430, "1 h 30 min"),
    ],
)
def test_durations_read_as_time(seconds, text):
    assert format_duration(seconds) == text
