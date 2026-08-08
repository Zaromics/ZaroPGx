"""BACKLOG 378: one authoritative pipeline-flag set for all four diagram renderers.

The Mermaid and Graphviz renderers derived the seven ``used_*``/``exported_*``
flags with one set of defaults; the simple-HTML fallback defaulted every flag
(including ``used_pharmcat``) to ``False`` and the simple-PNG rasterizer used a
third, uppercase spelling of the same ``file_type`` rule. The same workflow dict
therefore rendered a different pipeline depending on which renderer ran -- most
visibly, a workflow dict without explicit flags produced a full pipeline in the
SVG and an empty one in the HTML fallback.

Mermaid/Graphviz is the behaviour users see on the main path, so it is the
reference: these tests pin its defaults and its rendered output, and require the
other two renderers to agree.
"""

import contextlib

import pytest

from app.visualizations import workflow_diagram as wd

# The seven pipeline flags plus the file_type they are derived from.
FLAG_FIELDS = (
    "used_gatk",
    "used_hla",
    "used_pypgx",
    "used_pypgx_bam2vcf",
    "used_pharmcat",
    "used_mtdna",
    "exported_to_fhir",
)

WORKFLOW_CASES = {
    # The empty dict is the case that used to diverge: mermaid/graphviz/png ran
    # the full PharmCAT pipeline, the HTML fallback rendered nothing.
    "empty": {},
    "vcf": {"file_type": "vcf"},
    # Deliberately upper-case: file_type normalisation must happen in one place.
    "bam": {"file_type": "BAM"},
}


def _drive_mermaid(workflow):
    wd.build_mermaid_from_workflow(workflow)


def _drive_graphviz(workflow):
    # Flags are derived before any rendering attempt; the `dot` binary may not
    # exist on the test host, so a render failure is irrelevant here.
    with contextlib.suppress(Exception):
        wd.render_with_graphviz(workflow, fmt="svg")


def _drive_simple_html(workflow):
    wd.build_simple_html_from_workflow(workflow)


def _drive_simple_png(workflow):
    with contextlib.suppress(Exception):
        wd.render_simple_png_from_workflow(workflow)


RENDERERS = {
    "mermaid": _drive_mermaid,
    "graphviz": _drive_graphviz,
    "simple_html": _drive_simple_html,
    "simple_png": _drive_simple_png,
}


@pytest.fixture
def recorded_flags(monkeypatch):
    """Record every WorkflowFlags derivation, the single seam all renderers use."""
    calls = []
    original = wd.WorkflowFlags.from_workflow

    def _spy(workflow):
        flags = original(workflow)
        calls.append(flags)
        return flags

    monkeypatch.setattr(wd.WorkflowFlags, "from_workflow", staticmethod(_spy))
    return calls


@pytest.mark.parametrize("case", sorted(WORKFLOW_CASES))
def test_all_four_renderers_derive_identical_flags(case, recorded_flags):
    workflow = WORKFLOW_CASES[case]
    derived = {}
    for name, drive in RENDERERS.items():
        recorded_flags.clear()
        drive(dict(workflow))
        assert recorded_flags, f"{name} did not derive flags via WorkflowFlags"
        derived[name] = recorded_flags[0]

    reference = derived["mermaid"]
    for name, flags in derived.items():
        assert flags == reference, f"{name} diverged from mermaid for {case!r}"


def test_default_flags_are_the_mermaid_graphviz_defaults():
    """No explicit flags: PharmCAT runs, alignment-only stages do not."""
    flags = wd.WorkflowFlags.from_workflow({})
    assert flags.file_type == "vcf"
    assert flags.used_pharmcat is True
    assert flags.used_gatk is False
    assert flags.used_hla is False
    assert flags.used_pypgx is False
    assert flags.used_pypgx_bam2vcf is False
    assert flags.used_mtdna is False
    assert flags.exported_to_fhir is False


@pytest.mark.parametrize("raw", ["bam", "BAM", "Cram", "sam", "FASTQ"])
def test_alignment_file_types_enable_alignment_stages_case_insensitively(raw):
    flags = wd.WorkflowFlags.from_workflow({"file_type": raw})
    assert flags.file_type == raw.lower()
    assert flags.used_gatk is True
    assert flags.used_hla is True
    assert flags.used_pypgx_bam2vcf is True
    assert flags.used_pharmcat is True


def test_explicit_flags_override_the_file_type_defaults():
    flags = wd.WorkflowFlags.from_workflow(
        {"file_type": "bam", "used_hla": False, "used_pharmcat": False}
    )
    assert flags.used_hla is False
    assert flags.used_pharmcat is False
    assert flags.used_gatk is True  # untouched default still applies


# Mermaid node markers for the pipeline stages, paired with the HTML fallback's
# label for the same stage. The markers include the node id because the Services
# legend mentions every service unconditionally ("HLA Typing Service", ...).
STAGE_MARKERS = (
    ("PCAT[PharmCAT Analysis", "PharmCAT"),
    ("HLA[HLA Typing", "HLA Typing"),
    ("PyPGx_BAM2VCF[PyPGx BAM2VCF", "BAM→VCF"),
)


@pytest.mark.parametrize("case", sorted(WORKFLOW_CASES))
def test_simple_html_fallback_renders_the_same_pipeline_as_mermaid(case):
    """The HTML fallback used to drop every stage when flags were implicit."""
    workflow = WORKFLOW_CASES[case]
    mermaid = wd.build_mermaid_from_workflow(dict(workflow))
    html = wd.build_simple_html_from_workflow(dict(workflow))

    for mermaid_marker, html_label in STAGE_MARKERS:
        assert (mermaid_marker in mermaid) == (
            html_label in html
        ), f"{html_label} stage disagrees between mermaid and the HTML fallback"


def test_simple_html_fallback_defaults_file_type_to_vcf():
    """An empty workflow dict is a VCF run, not an "unknown" one."""
    assert "Detect (VCF)" in wd.build_simple_html_from_workflow({})


def test_simple_png_renders_with_default_flags():
    png = wd.render_simple_png_from_workflow({})
    assert png.startswith(b"\x89PNG")


# Golden for the default (no explicit flags) path -- the diagram users actually
# see today. Any change here is a change to production output, not a refactor.
MERMAID_DEFAULT_GOLDEN = """flowchart TD
classDef active fill:#cfe8ff,stroke:#5b8def,stroke-width:2px;
classDef norm fill:#f5f7fa,stroke:#b5bdc9,stroke-width:1px;
classDef svc fill:#f8f1ff,stroke:#9b59b6,stroke-width:1px;
classDef io fill:#fff7e6,stroke:#f39c12,stroke-width:1px;
classDef conversion fill:#ffe6e6,stroke:#e74c3c,stroke-width:1px;
classDef analysis fill:#e6ffe6,stroke:#27ae60,stroke-width:1px;

subgraph Client["Client/UI"]
  U[User]
  U --> Upload["Upload file"]
end

subgraph FastAPI["FastAPI App"]
  Upload --> SaveTmp[/Save to /tmp and /data/uploads/]
  SaveTmp --> Detect[Detect file type]
  Detect:::active
  Detect --> VCF[VCF]:::active
  VCF --> VCF_Processed[VCF]
  VCF_Processed --> PCAT[PharmCAT Analysis<br/>Drug recommendations]:::analysis
  PCAT --> Outputs["report.json<br/>report.html<br/>report.tsv<br/>match.json<br/>phenotype.json"]:::io
  Outputs --> Normalize["Normalize results<br/>(pharmcat_client.normalize_...)"]
  Normalize --> WorkflowDiagram[Generate Workflow Diagram<br/>Visual representation]:::analysis
  WorkflowDiagram --> Generate["Generate Reports<br/>(app/reports/generator.py)"]:::active
  Generate --> ReportsDir[/Write to /data/reports/:patient_id/:job_id/]:::io
  ReportsDir --> Serve["Serve at /reports/*"]
end

subgraph Services["External Services"]
  GATK_SVC["GATK API<br/>(docker/gatk-api)"]:::svc
  HLA_SVC["HLA Typing Service<br/>(docker/zarohla)"]:::svc
  PYP_SVC["PyPGx Service<br/>(docker/pypgx)"]:::svc
  PCAT_SVC["PharmCAT API/JAR<br/>(docker/pharmcat)"]:::svc
  MTDNA_SVC["mtDNA Server<br/>(docker/mtdna-server-2)"]:::svc
end

subgraph Optional["FHIR Export (optional)"]
  Generate -.-> FhirRoute["POST /reports/:report_id/export-to-fhir"]
end"""


@pytest.mark.parametrize("workflow", [{}, {"file_type": "vcf"}, {"file_type": "VCF"}])
def test_mermaid_default_path_matches_golden(workflow):
    assert wd.build_mermaid_from_workflow(workflow) == MERMAID_DEFAULT_GOLDEN


def test_mermaid_bam_path_is_unchanged_by_case():
    assert wd.build_mermaid_from_workflow(
        {"file_type": "BAM"}
    ) == wd.build_mermaid_from_workflow({"file_type": "bam"})
