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

With the run's facts (RunFacts, gathered by the report generator) each step also
shows its tool's version, how long it took and what it produced -- genes called,
the haplogroup, positions genotyped, drugs with guidance -- and a step that ran but
produced nothing (mtDNA on a VCF without chrM calls) is drawn grey, its outside call
a dashed "none". Each step is coloured by its stage -- input, preparation,
calling, interpretation, reports -- with a key beneath.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

INPUT = "input"

# step_name -> (what it does, the tool that does it, the component whose version
# the tool shows). Step names are the ones in app/services/workflow_registry.py,
# which main.nf's processes report against. "{v}" in a tool takes the version the
# report's Platform table gives that component; it is dropped when unknown.
STEP_LABELS = {
    "header_analysis": ("Read the file's header", "ZaroPGx {v}", "ZaroPGx"),
    "bcf_to_vcf": ("BCF to VCF", "bcftools", ""),
    "gvcf_to_vcf": (
        "Genotype the gVCF, at PharmCAT's positions and genome-wide",
        "GATK {v} GenotypeGVCFs",
        "GATK",
    ),
    "liftover": ("Lift over GRCh37 to GRCh38", "GATK {v} Picard LiftoverVcf", "GATK"),
    "gatk_cram_sam_to_bam": ("Convert to BAM", "samtools", ""),
    "gatk_alignment": ("Align to GRCh38, mark duplicates", "GATK {v} BwaSpark", "GATK"),
    "hla_typing": ("HLA class I typing", "OptiType {v}", "OptiType"),
    "pypgx_bam2vcf": (
        "Call variants from the reads: PyPGx's genes, PharmCAT's positions",
        "PyPGx {v}, bcftools",
        "PyPGx",
    ),
    "pypgx_analysis": ("Star alleles", "PyPGx {v}", "PyPGx"),
    "mtdna_analysis": (
        "Mitochondrial variants, haplogroup, MT-RNR1",
        "mtDNA-Server 2 {v}",
        "mtDNA-Server 2",
    ),
    "pharmcat_analysis": ("Phenotypes and drug guidance", "PharmCAT {v}", "PharmCAT"),
    "report_generation": ("Reports", "ZaroPGx {v}", "ZaroPGx"),
}

# The stage each step belongs to, for its colour and the key.
STAGES = (
    ("input", "Input", (INPUT, "header_analysis")),
    (
        "preparation",
        "Preparation",
        (
            "bcf_to_vcf",
            "gvcf_to_vcf",
            "liftover",
            "gatk_cram_sam_to_bam",
            "gatk_alignment",
        ),
    ),
    (
        "calling",
        "Calling",
        ("hla_typing", "pypgx_bam2vcf", "pypgx_analysis", "mtdna_analysis"),
    ),
    ("interpretation", "Interpretation", ("pharmcat_analysis",)),
    ("reports", "Reports", ("report_generation",)),
)


@dataclass
class RunFacts:
    """What the run produced, for the diagram. Every field is optional: a missing
    fact is left out, never guessed.

    ``versions``: component (as the Platform table names it) -> version.
    ``seconds``: step -> how long it ran. ``details``: step -> lines saying what it
    produced. ``produced``: what each outside call or direct result carried -- keys
    ``hla`` (HLA-A/HLA-B to PharmCAT), ``hla_c``, ``cyp2d6``, ``mt_rnr1``,
    ``haplogroup``, ``other_genes`` (PyPGx's genes PharmCAT does not call); a key
    left out counts as produced. ``input_detail``: a line under the input.
    """

    versions: Dict[str, str] = field(default_factory=dict)
    seconds: Dict[str, float] = field(default_factory=dict)
    details: Dict[str, List[str]] = field(default_factory=dict)
    produced: Dict[str, bool] = field(default_factory=dict)
    input_detail: str = ""

    def made(self, key: str) -> bool:
        return not key or self.produced.get(key, True)


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return ""
    seconds = int(round(seconds))
    if seconds < 1:
        return "under 1 s"
    if seconds < 60:
        return f"{seconds} s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {secs} s" if secs else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


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
    details: Tuple[str, ...] = ()
    # Ran, but produced nothing the run could use.
    empty: bool = False

    @property
    def off_path(self) -> bool:
        return self.status in OFF_PATH


@dataclass
class JobGraph:
    nodes: List[Node] = field(default_factory=list)
    # (source, target, label); label "" for none. dotted edges lead to off-path steps.
    edges: List[Tuple[str, str, str]] = field(default_factory=list)
    dotted: List[Tuple[str, str]] = field(default_factory=list)
    # (source, target, label): an outside call or result the step did not produce,
    # drawn dashed as "label: none".
    empty_edges: List[Tuple[str, str, str]] = field(default_factory=list)

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


def _tool(name: str, facts: RunFacts) -> str:
    _, template, component = STEP_LABELS[name]
    version = facts.versions.get(component, "") if component else ""
    tool = template.replace("{v}", version).replace("  ", " ").replace(" ,", ",")
    duration = format_duration(facts.seconds.get(name))
    return f"{tool.strip()} · {duration}" if duration else tool.strip()


# The outside calls and direct results each step is drawn handing on, by their
# RunFacts.produced keys. A step that produced none of them ran empty.
_EMPTY_WHEN = {
    "hla_typing": ("hla", "hla_c"),
    "pypgx_analysis": ("cyp2d6", "other_genes"),
    "mtdna_analysis": ("mt_rnr1", "haplogroup"),
}


def build_job_graph(
    steps: Sequence[Tuple[str, object]],
    file_type: Optional[str] = None,
    facts: Optional[RunFacts] = None,
) -> JobGraph:
    """The graph of one job. ``steps`` is (step_name, status) per job_steps row;
    ``facts``, what the run produced, adds versions, durations and results."""
    facts = facts or RunFacts()
    status = {}
    for name, value in steps:
        if name in STEP_LABELS:
            status[name] = _status_value(value)
    fmt = input_format(file_type, status)

    graph = JobGraph()
    label = INPUT_FORMATS[fmt] + (" file" if fmt != "fastq" else " reads")
    graph.nodes.append(
        Node(
            INPUT,
            f"Uploaded {label}",
            details=(facts.input_detail,) if facts.input_detail else (),
        )
    )

    def ran(name: str) -> bool:
        return name in status and status[name] not in OFF_PATH

    def add(name: str, title: Optional[str] = None) -> None:
        step_title = STEP_LABELS[name][0]
        empty = (
            ran(name)
            and name in _EMPTY_WHEN
            and not any(facts.made(key) for key in _EMPTY_WHEN[name])
        )
        graph.nodes.append(
            Node(
                name,
                title or step_title,
                _tool(name, facts),
                status[name],
                tuple(facts.details.get(name, ())),
                empty,
            )
        )

    def flow(source: str, target: str, label: str, key: str) -> None:
        """An outside call or result: solid if the step produced it, else dashed."""
        if facts.made(key):
            graph.edges.append((source, target, label))
        else:
            graph.empty_edges.append((source, target, label))

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
    # (source, label, produced key; "" for always)
    pharmcat_inputs: List[Tuple[str, str, str]] = []
    report_inputs: List[Tuple[str, str, str]] = []

    if fmt in ALIGNMENT_FORMATS:
        if fmt == "fastq":
            bam = step("gatk_alignment", current)
        else:
            bam = step(
                "gatk_cram_sam_to_bam", current, f"Convert {INPUT_FORMATS[fmt]} to BAM"
            )
        reads = step("hla_typing", bam)
        if ran("hla_typing"):
            pharmcat_inputs.append(("hla_typing", "HLA-A, HLA-B", "hla"))
            report_inputs.append(("hla_typing", "HLA-C", "hla_c"))
        vcf = step("pypgx_bam2vcf", reads)
        if ran("pypgx_bam2vcf"):
            pharmcat_inputs.append(("pypgx_bam2vcf", "VCF", ""))
        mtdna_source = bam
    else:
        converted = step("bcf_to_vcf", current)
        converted = step("gvcf_to_vcf", converted)
        vcf = step("liftover", converted)
        pharmcat_inputs.append((vcf, "VCF", ""))
        mtdna_source = converted

    step("pypgx_analysis", vcf)
    if ran("pypgx_analysis"):
        pharmcat_inputs.append(("pypgx_analysis", "CYP2D6", "cyp2d6"))
        report_inputs.append(("pypgx_analysis", "other genes", "other_genes"))

    step("mtdna_analysis", mtdna_source)
    if ran("mtdna_analysis"):
        pharmcat_inputs.append(("mtdna_analysis", "MT-RNR1", "mt_rnr1"))
        report_inputs.append(("mtdna_analysis", "haplogroup", "haplogroup"))

    if "pharmcat_analysis" in status:
        add("pharmcat_analysis")
        for source, edge_label, key in pharmcat_inputs:
            flow(source, "pharmcat_analysis", edge_label, key)
        report_inputs.insert(0, ("pharmcat_analysis", "", ""))

    if "report_generation" in status:
        add("report_generation")
        for source, edge_label, key in report_inputs:
            flow(source, "report_generation", edge_label, key)
    return graph


def _mermaid_text(text: str) -> str:
    # Inside a quoted Mermaid label, a double quote ends the label; in a markdown
    # label, * _ and ` would format.
    for char, entity in (('"', "#quot;"), ("*", "#42;"), ("_", "#95;"), ("`", "#96;")):
        text = text.replace(char, entity)
    return text


# Drawn large (30px type) because Kroki's PNG is exactly the size Mermaid lays the
# diagram out at: at its 16px default, a run's diagram was about 650px wide and
# soft printed across an A4 page. Labels are SVG text, not HTML (WeasyPrint cannot
# draw HTML inside SVG), wrapping at 520px.
MERMAID_INIT = (
    "%%{init: {'themeVariables': {'fontFamily': 'Arial, sans-serif', "
    "'fontSize': '30px'}, "
    "'flowchart': {'htmlLabels': false, 'nodeSpacing': 60, 'rankSpacing': 80, "
    "'padding': 24, 'wrappingWidth': 520}}}%%"
)


# Each stage's colours (fill, stroke). A step that was skipped, failed or produced
# nothing is grey and dashed instead, whatever its stage.
STAGE_STYLES = {
    "input": ("#fff7e6", "#e08e0b"),
    "preparation": ("#fdecea", "#d9534f"),
    "calling": ("#e8f6ec", "#27ae60"),
    "interpretation": ("#f3ecfa", "#8e44ad"),
    "reports": ("#e7f0ff", "#5b8def"),
}
OFF_STYLE = (
    "fill:#f5f5f5,stroke:#999999,stroke-width:2px,stroke-dasharray:6 4,color:#555555"
)
STAGE_OF = {member: key for key, _, members in STAGES for member in members}


def _node_line(n: Node) -> str:
    # Markdown labels: the step in bold, the tool (version, time) in italics, then
    # what it produced.
    text = f"**{_mermaid_text(n.title)}**"
    if n.tool:
        text += f"\n_{_mermaid_text(n.tool)}_"
    for detail in n.details:
        text += f"\n{_mermaid_text(detail)}"
    if n.off_path:
        text += f"\n({n.status})"
    shape = ('(["`', '`"])') if n.id == INPUT else ('["`', '`"]')
    css = "off" if (n.off_path or n.empty) else STAGE_OF[n.id]
    return f"{n.id}{shape[0]}{text}{shape[1]}:::{css}"


def to_mermaid(graph: JobGraph) -> str:
    lines = [MERMAID_INIT, "flowchart TD"]
    for key, (fill, stroke) in STAGE_STYLES.items():
        lines.append(f"  classDef {key} fill:{fill},stroke:{stroke},stroke-width:2px;")
    lines.append(f"  classDef off {OFF_STYLE};")
    lines.extend(f"  {_node_line(n)}" for n in graph.nodes)
    for source, target, label in graph.edges:
        arrow = f"-->|{_mermaid_text(label)}|" if label else "-->"
        lines.append(f"  {source} {arrow} {target}")
    for source, target in graph.dotted:
        lines.append(f"  {source} -.-> {target}")
    for source, target, label in graph.empty_edges:
        lines.append(f"  {source} -.->|{_mermaid_text(label)}: none| {target}")
    lines.append("  linkStyle default stroke-width:2px;")

    # The key, a row under the diagram: the stages this run had, and grey when a
    # step is drawn grey. (Stage boxes around the steps were tried: Mermaid centres
    # a box's title where the edges come in, so the edges ran through the titles.)
    present = {STAGE_OF[n.id] for n in graph.nodes}
    keys = [(key, title) for key, title, _ in STAGES if key in present]
    if any(n.off_path or n.empty for n in graph.nodes):
        keys.append(("off", "Produced nothing"))
    last = graph.nodes[-1].id if graph.nodes else None
    if keys and last:
        # Unlinked, the entries share a rank: one row, node spacing apart. (A
        # subgraph left without a direction runs across its parent's: a column.)
        lines.append('  subgraph key["`_Key_`"]')
        lines.append("    direction TB")
        lines.extend(f'    key_{k}["{_mermaid_text(t)}"]:::{k}' for k, t in keys)
        lines.append("  end")
        lines.append(f"  {last} ~~~ key")
        lines.append("  style key fill:none,stroke:none;")
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
        if n.details:
            text += "<br/><small>" + "; ".join(html.escape(d) for d in n.details)
            text += "</small>"
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


def load_job_step_records(db_session, job_id) -> List[Dict[str, object]]:
    """Each of the job's steps, in order: name, status, seconds it ran (None when
    not recorded) and the output_data its service reported. The seconds are the
    duration_seconds the job's API reports, else the step's timestamps'."""
    job_uuid = _job_uuid(job_id) if db_session is not None and job_id else None
    if job_uuid is None:
        return []
    from app.api.db import JobStep

    rows = (
        db_session.query(
            JobStep.step_name,
            JobStep.status,
            JobStep.started_at,
            JobStep.completed_at,
            JobStep.duration_seconds,
            JobStep.output_data,
        )
        .filter(JobStep.job_id == job_uuid)
        .order_by(JobStep.step_order)
        .all()
    )
    records = []
    for name, value, started, completed, duration, output in rows:
        seconds = duration
        if seconds is None and started is not None and completed is not None:
            seconds = (completed - started).total_seconds()
        records.append(
            {
                "name": name,
                "status": _status_value(value),
                "seconds": seconds,
                "output": output or {},
            }
        )
    return records
