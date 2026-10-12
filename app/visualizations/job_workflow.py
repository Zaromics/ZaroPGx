"""The workflow diagram of one job: the steps that ran for it, wired as they ran.

The reports used to draw a fixed, generic diagram (workflow.mmd) or one inferred from
flags, neither of which was this run: they showed ZIP extraction, routes that do not
exist and an export to a FHIR server, and left out mtDNA, BCF, gVCF and liftover. The
graph here is built from the job's own rows in job_steps -- which steps the run had
and how each ended -- and wired as pipelines/pgx/main.nf wires those steps:

- Alignments: the input is converted (CRAM, SAM) or aligned (FASTQ) to a BAM. HLA
  typing runs on the BAM first; main.nf only starts PyPGx's read genotyping when it
  has finished. That step writes the VCF PharmCAT reads and PyPGx's input. mtDNA runs
  alongside, on the BAM.
- Variant files: a BCF is converted and a gVCF genotyped, then a GRCh37 file is lifted
  over. PyPGx and PharmCAT read the result; mtDNA reads the file before liftover.
- PharmCAT takes PyPGx's, OptiType's and mtDNA-Server 2's calls as outside calls. The
  genes PharmCAT does not call (PyPGx's other genes, HLA-C, the haplogroup) go to the
  reports directly.

A step that was skipped or failed is drawn off to the side, with nothing flowing out
of it: it produced nothing the run used.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

INPUT = "input"

# step_name -> (what it does, the tool that does it). Step names are the ones in
# app/services/workflow_registry.py, which main.nf's processes report against.
STEP_LABELS = {
    "header_analysis": ("Read the file's header: format, build, samples", "ZaroPGx"),
    "bcf_to_vcf": ("BCF to VCF", "bcftools"),
    "gvcf_to_vcf": (
        "Genotype the gVCF, at PharmCAT's positions and genome-wide",
        "GATK GenotypeGVCFs",
    ),
    "liftover": ("Lift over GRCh37 to GRCh38", "GATK Picard LiftoverVcf"),
    "gatk_cram_sam_to_bam": ("Convert to BAM", "samtools"),
    "gatk_alignment": ("Align to GRCh38, mark duplicates", "GATK BwaSpark"),
    "hla_typing": ("HLA class I typing", "OptiType"),
    "pypgx_bam2vcf": (
        "Call variants from the reads: PyPGx's genes, PharmCAT's positions",
        "PyPGx, bcftools",
    ),
    "pypgx_analysis": ("Star alleles", "PyPGx"),
    "mtdna_analysis": (
        "Mitochondrial variants, haplogroup, MT-RNR1",
        "mtDNA-Server 2",
    ),
    "pharmcat_analysis": ("Phenotypes and drug guidance", "PharmCAT"),
    "report_generation": ("Reports", "ZaroPGx"),
}

INPUT_FORMATS = {
    "vcf": "VCF",
    "bcf": "BCF",
    "gvcf": "gVCF",
    "bam": "BAM",
    "cram": "CRAM",
    "sam": "SAM",
    "fastq": "FASTQ",
    # The steps say "converted to BAM" but the job does not say from what.
    "cram_or_sam": "CRAM or SAM",
}

ALIGNMENT_FORMATS = {"bam", "cram", "sam", "cram_or_sam", "fastq"}

# How each ended, as job_steps records it. Anything else (running, pending) is the
# report step itself, which is running while this is drawn.
OFF_PATH = {"skipped", "failed"}


@dataclass(frozen=True)
class Node:
    id: str
    title: str
    tool: str = ""
    status: str = ""

    @property
    def off_path(self) -> bool:
        return self.status in OFF_PATH


@dataclass
class JobGraph:
    nodes: List[Node] = field(default_factory=list)
    # (source, target, label); label "" for none. dotted edges lead to off-path steps.
    edges: List[Tuple[str, str, str]] = field(default_factory=list)
    dotted: List[Tuple[str, str]] = field(default_factory=list)

    def node(self, node_id: str) -> Optional[Node]:
        return next((n for n in self.nodes if n.id == node_id), None)


def _status_value(status) -> str:
    return str(getattr(status, "value", status) or "").lower()


def input_format(file_type: Optional[str], step_names: Iterable[str]) -> str:
    """The uploaded file's format: from the steps where they settle it, else the
    job's recorded file type when the steps agree with it."""
    steps = set(step_names)
    if "gvcf_to_vcf" in steps:
        return "gvcf"
    if "bcf_to_vcf" in steps:
        return "bcf"
    if "gatk_alignment" in steps:
        return "fastq"
    declared = str(file_type or "").lower()
    converted = "gatk_cram_sam_to_bam" in steps
    if converted:
        return declared if declared in {"cram", "sam"} else "cram_or_sam"
    if steps & {"hla_typing", "pypgx_bam2vcf"} or declared == "bam":
        return "bam"
    return "vcf"


def build_job_graph(
    steps: Sequence[Tuple[str, object]], file_type: Optional[str] = None
) -> JobGraph:
    """The graph of one job. ``steps`` is (step_name, status) per job_steps row."""
    status = {}
    for name, value in steps:
        if name in STEP_LABELS:
            status[name] = _status_value(value)
    fmt = input_format(file_type, status)

    graph = JobGraph()
    label = INPUT_FORMATS[fmt] + (" file" if fmt != "fastq" else " reads")
    graph.nodes.append(Node(INPUT, f"Uploaded {label}"))

    def ran(name: str) -> bool:
        return name in status and status[name] not in OFF_PATH

    def add(name: str, title: Optional[str] = None) -> None:
        step_title, tool = STEP_LABELS[name]
        graph.nodes.append(Node(name, title or step_title, tool, status[name]))

    def step(name: str, source: str, title: Optional[str] = None) -> str:
        """Draw `name` fed by `source`; return what feeds the next step."""
        if name not in status:
            return source
        add(name, title)
        if ran(name):
            graph.edges.append((source, name, ""))
            return name
        graph.dotted.append((source, name))
        return source

    current = step("header_analysis", INPUT)
    pharmcat_inputs: List[Tuple[str, str]] = []
    report_inputs: List[str] = []

    if fmt in ALIGNMENT_FORMATS:
        if fmt == "fastq":
            bam = step("gatk_alignment", current)
        else:
            bam = step(
                "gatk_cram_sam_to_bam", current, f"Convert {INPUT_FORMATS[fmt]} to BAM"
            )
        reads = step("hla_typing", bam)
        if ran("hla_typing"):
            pharmcat_inputs.append(("hla_typing", "HLA-A, HLA-B"))
            report_inputs.append("hla_typing")
        vcf = step("pypgx_bam2vcf", reads)
        if ran("pypgx_bam2vcf"):
            pharmcat_inputs.append(("pypgx_bam2vcf", "VCF"))
        mtdna_source = bam
    else:
        converted = step("bcf_to_vcf", current)
        converted = step("gvcf_to_vcf", converted)
        vcf = step("liftover", converted)
        pharmcat_inputs.append((vcf, "VCF"))
        mtdna_source = converted

    step("pypgx_analysis", vcf)
    if ran("pypgx_analysis"):
        pharmcat_inputs.append(("pypgx_analysis", "CYP2D6"))
        report_inputs.append("pypgx_analysis")

    step("mtdna_analysis", mtdna_source)
    if ran("mtdna_analysis"):
        pharmcat_inputs.append(("mtdna_analysis", "MT-RNR1"))
        report_inputs.append("mtdna_analysis")

    if "pharmcat_analysis" in status:
        add("pharmcat_analysis")
        for source, edge_label in pharmcat_inputs:
            graph.edges.append((source, "pharmcat_analysis", edge_label))
        report_inputs.insert(0, "pharmcat_analysis")

    if "report_generation" in status:
        add("report_generation")
        for source in report_inputs:
            graph.edges.append((source, "report_generation", ""))
    return graph


def _mermaid_text(text: str) -> str:
    # Inside a quoted Mermaid label, a double quote ends the label.
    return text.replace('"', "#quot;")


def to_mermaid(graph: JobGraph) -> str:
    lines = [
        "flowchart TD",
        "  classDef io fill:#fff7e6,stroke:#f39c12,stroke-width:1px;",
        "  classDef step fill:#e7f0ff,stroke:#5b8def,stroke-width:1px;",
        "  classDef off fill:#f5f5f5,stroke:#999999,stroke-width:1px,"
        "stroke-dasharray:4 3,color:#666666;",
    ]
    for n in graph.nodes:
        text = _mermaid_text(n.title)
        if n.tool:
            text += "<br/>" + _mermaid_text(n.tool)
        if n.off_path:
            text += f"<br/>({n.status})"
        shape = ('(["', '"])') if n.id == INPUT else ('["', '"]')
        css = "io" if n.id == INPUT else ("off" if n.off_path else "step")
        lines.append(f"  {n.id}{shape[0]}{text}{shape[1]}:::{css}")
    for source, target, label in graph.edges:
        arrow = f"-->|{_mermaid_text(label)}|" if label else "-->"
        lines.append(f"  {source} {arrow} {target}")
    for source, target in graph.dotted:
        lines.append(f"  {source} -.-> {target}")
    return "\n".join(lines) + "\n"


def to_html(graph: JobGraph) -> str:
    """The same graph as text, for when the diagram cannot be drawn."""
    incoming = {}
    for source, target, label in graph.edges:
        incoming.setdefault(target, []).append((source, label))
    titles = {n.id: n.title for n in graph.nodes}
    items = []
    for n in graph.nodes:
        if n.id == INPUT:
            continue
        text = html.escape(n.title)
        if n.tool:
            text += f" <em>({html.escape(n.tool)})</em>"
        if n.off_path:
            text += f" &mdash; {html.escape(n.status)}"
        sources = [
            html.escape(titles[s]) + (f": {html.escape(lbl)}" if lbl else "")
            for s, lbl in incoming.get(n.id, [])
            if s != INPUT
        ]
        if len(sources) > 1:
            text += "<br/><small>from " + "; ".join(sources) + "</small>"
        items.append(f"<li>{text}</li>")
    first = html.escape(titles[INPUT])
    return (
        f'<div class="workflow-text"><p>{first}, then:</p>'
        f"<ol>{''.join(items)}</ol></div>"
    )


def _job_uuid(job_id):
    import uuid

    try:
        return job_id if isinstance(job_id, uuid.UUID) else uuid.UUID(str(job_id))
    except (TypeError, ValueError):
        return None


def load_job_file_type(db_session, job_id) -> Optional[str]:
    """The uploaded file's type as the job recorded it at upload."""
    job_uuid = _job_uuid(job_id) if db_session is not None and job_id else None
    if job_uuid is None:
        return None
    from app.api.db import Job

    row = db_session.query(Job.job_metadata).filter(Job.id == job_uuid).first()
    metadata = (row[0] if row else None) or {}
    for section in ("workflow", "file_analysis"):
        value = (metadata.get(section) or {}).get("file_type")
        if value:
            return str(value)
    return None


def load_job_steps(db_session, job_id) -> List[Tuple[str, str]]:
    """(step_name, status) for each of the job's steps, in their recorded order."""
    job_uuid = _job_uuid(job_id) if db_session is not None and job_id else None
    if job_uuid is None:
        return []
    from app.api.db import JobStep

    rows = (
        db_session.query(JobStep.step_name, JobStep.status)
        .filter(JobStep.job_id == job_uuid)
        .order_by(JobStep.step_order)
        .all()
    )
    return [(name, _status_value(value)) for name, value in rows]
