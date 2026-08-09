"""Unified PharmCAT report.json gene walkers.

PharmCAT has emitted two live ``genes`` shapes:

* **nested** — ``genes → {CPIC|DPWG|FDA} → gene_symbol → gene_data`` (≤2.x)
* **flat** — ``genes → gene_symbol → gene_data`` (3.x / 3.4.0)

``normalize_pharmcat_results`` and ``PharmCATParser._parse_genes`` used to sniff
and walk these independently. All format detection, gene iteration, and
``sourceDiplotypes`` extraction belongs here so Wave 4 unification has a
single contract under test.

``validate_report`` (BACKLOG 265) is the standing gate over both: it records
which PharmCAT version produced a payload and checks the structures the walkers
actually depend on, so a future shape change is rejected instead of silently
yielding an empty report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import (
    Any,
    Dict,
    Iterator,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

FormatName = Literal["nested", "flat", "empty"]

GUIDELINE_SOURCES = frozenset({"CPIC", "DPWG", "FDA"})
_GENE_SHAPE_KEYS = frozenset(
    {
        "geneSymbol",
        "sourceDiplotypes",
        "recommendationDiplotypes",
        "alleleDefinitionVersion",
    }
)


@dataclass(frozen=True)
class GeneBlock:
    """One gene payload, the guideline source it was attributed to, and how it
    was called.

    ``source`` is the guideline bucket (nested shape) or ``phenotypeSource``
    (flat shape), and is ``None`` when the run recorded none -- never invented.
    ``call_source`` is PharmCAT's ``callSource``: ``MATCHER`` when its own Named
    Allele Matcher produced the diplotype, ``OUTSIDE`` for a supplied outside
    call, ``NONE`` when no call was made.
    """

    gene_symbol: str
    gene_data: Dict[str, Any]
    source: Optional[str]
    call_source: Optional[str] = None


def detect_format(genes_section: Optional[Mapping[str, Any]]) -> FormatName:
    """Return ``nested``, ``flat``, or ``empty`` for a PharmCAT ``genes`` object."""
    if not genes_section:
        return "empty"

    first_key = next(iter(genes_section.keys()))
    if first_key in GUIDELINE_SOURCES:
        return "nested"

    first_value = genes_section.get(first_key)
    if isinstance(first_value, dict) and _GENE_SHAPE_KEYS.intersection(first_value):
        return "flat"

    # Ambiguous non-guideline top keys without gene fields → treat as nested
    # source buckets (matches PharmCATParser's historical fallback).
    return "nested"


def iter_gene_blocks(
    genes_section: Optional[Mapping[str, Any]],
) -> Iterator[GeneBlock]:
    """Yield a ``GeneBlock`` for every gene in either shape."""
    fmt = detect_format(genes_section)
    if fmt == "empty" or genes_section is None:
        return

    if fmt == "flat":
        for gene_symbol, gene_data in genes_section.items():
            if not isinstance(gene_data, dict):
                continue
            # PharmCAT 3.x emits phenotypeSource=None. Report that as unknown --
            # defaulting to "CPIC" fabricated a guideline attribution and
            # destroyed callSource downstream (BACKLOG 28 + 216).
            yield GeneBlock(
                gene_symbol,
                gene_data,
                gene_data.get("phenotypeSource"),
                gene_data.get("callSource"),
            )
        return

    # nested
    for source, genes in genes_section.items():
        if not isinstance(genes, dict):
            continue
        for gene_symbol, gene_data in genes.items():
            if not isinstance(gene_data, dict):
                continue
            yield GeneBlock(gene_symbol, gene_data, source, gene_data.get("callSource"))


def extract_source_call(gene_data: Mapping[str, Any]) -> Dict[str, Any]:
    """Pull the primary display diplotype/phenotype/activity from a gene block.

    Uses ``sourceDiplotypes`` (PharmCAT's displayed / "real" call). Defaults match
    the historical empty-list behaviour of the old recommendation helper.
    """
    diplotype = "Unknown/Unknown"
    phenotype = "Unknown"
    activity_score = None

    src_list = gene_data.get("sourceDiplotypes")
    if not isinstance(src_list, list) or not src_list:
        return {
            "diplotype": diplotype,
            "phenotype": phenotype,
            "activity_score": activity_score,
        }

    src = src_list[0]
    if not isinstance(src, dict):
        return {
            "diplotype": diplotype,
            "phenotype": phenotype,
            "activity_score": activity_score,
        }

    if "label" in src:
        diplotype = src["label"]

    if "phenotypes" in src:
        phenotypes = src["phenotypes"]
        if isinstance(phenotypes, list):
            phenotype = ", ".join(str(p) for p in phenotypes)
        else:
            phenotype = str(phenotypes)

    if "activityScore" in src:
        activity_score = src["activityScore"]

    return {
        "diplotype": diplotype,
        "phenotype": phenotype,
        "activity_score": activity_score,
    }


def _clean(value: Any) -> Optional[str]:
    """Return a stripped string, or ``None`` for absent/blank values."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def extract_matcher_metadata(
    report: Optional[Mapping[str, Any]],
) -> Dict[str, Optional[str]]:
    """Pull run-derived provenance out of a PharmCAT ``report.json`` payload.

    Three independent facts, each ``None`` when the run did not emit it:

    * ``genome_build`` -- ``matcherMetadata.genomeBuild`` (e.g. ``GRCh38.p14``)
    * ``named_allele_matcher_version`` -- ``matcherMetadata.namedAlleleMatcherVersion``
    * ``data_version`` -- top-level ``dataVersion`` (the guideline data release)

    ``matcherMetadata`` first appears in PharmCAT 3.4.0; v2-shaped reports carry
    ``dataVersion`` only. Callers must render each fact conditionally rather
    than substituting a placeholder.
    """
    if not isinstance(report, Mapping):
        return {
            "genome_build": None,
            "named_allele_matcher_version": None,
            "data_version": None,
        }

    matcher = report.get("matcherMetadata")
    if not isinstance(matcher, Mapping):
        matcher = {}

    return {
        "genome_build": _clean(matcher.get("genomeBuild")),
        "named_allele_matcher_version": _clean(
            matcher.get("namedAlleleMatcherVersion")
        ),
        "data_version": _clean(report.get("dataVersion")),
    }


# ===========================================================================
# 265 -- version tracking + structure validation
# ===========================================================================

#: PharmCAT ``major.minor`` series this parser has been shown to handle, each
#: mapped to the payload that proves it.  Membership is evidence, not
#: aspiration: every entry below was checked by walking a real report.json of
#: that version and confirming the walkers extract every gene the file
#: contains.  Patch releases inside a listed series are assumed compatible --
#: PharmCAT has never changed report.json shape in a patch bump, and treating
#: 3.4.1 as unknown would cry wolf on every upstream point release.
#:
#: When you bump ``PHARMCAT_VERSION`` in ``compose.yml``, regenerate a fixture
#: and add the series here.  Until you do, runs are annotated as unverified.
SUPPORTED_VERSION_SERIES: Dict[Tuple[int, int], str] = {
    (2, 15): "test_data/pharmcat.example.report.json (v2.15.4-20-g7f763d7c, nested)",
    (3, 0): (
        "dev-notes/pharmcat-json-postgres/example_pgx_pharmcat.json "
        "(3.0.1, nested -- untracked, verified by hand)"
    ),
    (3, 1): "data/reports -- 18 real runs (3.1.1, flat)",
    (3, 2): "data/reports -- 1 real run (3.2.0, flat)",
    (3, 4): "test_data/pharmcat.example.v340.report.json + 5 real runs (3.4.0, flat)",
}

# ``v2.15.4-20-g7f763d7c``, ``3.4.0``, ``v3.5.0-SNAPSHOT`` -- take the leading
# numeric triple and ignore any git-describe / qualifier tail.
_VERSION_RE = re.compile(r"^\s*v?(\d+)\.(\d+)(?:\.(\d+))?")

Severity = Literal["error", "warning"]

#: Cap on repeated drug-spine complaints; a shape change breaks hundreds of
#: entries identically and the log line has to stay readable.
_MAX_REPORTED_DRUG_ISSUES = 5


@dataclass(frozen=True)
class SchemaIssue:
    """One finding about a ``report.json`` payload."""

    severity: Severity
    code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.severity}] {self.code}: {self.message}"


@dataclass(frozen=True)
class ValidationResult:
    """What ``validate_report`` learned about one payload.

    ``ok`` is false only when an *error* was raised -- warnings (an unverified
    PharmCAT version, a run that called no genes) are advisory and must not
    stop an analysis.
    """

    version: Optional[str]
    version_series: Optional[Tuple[int, int]]
    version_supported: bool
    detected_format: FormatName
    gene_entry_count: int
    gene_block_count: int
    issues: Tuple[SchemaIssue, ...] = field(default=())

    @property
    def errors(self) -> Tuple[SchemaIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "error")

    @property
    def warnings(self) -> Tuple[SchemaIssue, ...]:
        return tuple(i for i in self.issues if i.severity == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        head = (
            f"PharmCAT report.json: version={self.version or 'unknown'} "
            f"format={self.detected_format} "
            f"genes={self.gene_block_count}/{self.gene_entry_count}"
        )
        if not self.issues:
            return head + " -- ok"
        return head + " -- " + "; ".join(str(i) for i in self.issues)


class PharmCATSchemaError(ValueError):
    """A ``report.json`` whose structure the parser cannot honestly read."""

    def __init__(self, result: ValidationResult) -> None:
        super().__init__(result.summary())
        self.result = result


def parse_pharmcat_version(raw: Any) -> Optional[Tuple[int, int, int]]:
    """Return ``(major, minor, patch)`` from a ``pharmcatVersion`` string.

    ``None`` when the value is absent or carries no leading numeric version --
    a missing version is a fact to report, never a guess to fill in.
    """
    if not isinstance(raw, str):
        return None
    match = _VERSION_RE.match(raw)
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def _is_gene_like(value: Any) -> bool:
    """True when a mapping carries at least one field only a gene block has."""
    return isinstance(value, Mapping) and bool(_GENE_SHAPE_KEYS.intersection(value))


def _scan_genes(genes_section: Mapping[str, Any]) -> Tuple[int, List[str]]:
    """Count the gene entries a payload *contains*, independent of the walker.

    Deliberately does not call :func:`iter_gene_blocks`: the whole point is a
    second opinion.  If this scan can see genes the walker cannot reach, the
    walker has fallen behind the format -- which is exactly the silent
    degradation 265 exists to catch.

    Returns ``(expected_gene_count, unrecognised_top_level_keys)``.
    """
    expected = 0
    unrecognised: List[str] = []

    for key, value in genes_section.items():
        if _is_gene_like(value):  # flat: genes -> symbol -> gene_data
            expected += 1
            continue
        if isinstance(value, Mapping) and value:
            nested = sum(1 for child in value.values() if _is_gene_like(child))
            if nested:  # nested: genes -> SOURCE -> symbol -> gene_data
                expected += nested
                continue
        unrecognised.append(str(key))

    return expected, unrecognised


def _check_genes(
    report: Mapping[str, Any], issues: List[SchemaIssue]
) -> Tuple[FormatName, int, int]:
    """Validate the ``genes`` section. Returns (format, entries seen, blocks)."""
    if "genes" not in report:
        issues.append(
            SchemaIssue(
                "error",
                "genes.missing",
                "report has no 'genes' section -- this is not a PharmCAT report.json",
            )
        )
        return "empty", 0, 0

    genes_section = report.get("genes")
    if genes_section is None:
        issues.append(
            SchemaIssue("warning", "genes.empty", "'genes' is null; no genes called")
        )
        return "empty", 0, 0

    if not isinstance(genes_section, Mapping):
        issues.append(
            SchemaIssue(
                "error",
                "genes.not_a_mapping",
                f"'genes' is {type(genes_section).__name__}, expected an object "
                f"keyed by gene symbol (flat) or guideline source (nested)",
            )
        )
        return "empty", 0, 0

    if not genes_section:
        issues.append(
            SchemaIssue("warning", "genes.empty", "'genes' is empty; no genes called")
        )
        return "empty", 0, 0

    expected, unrecognised = _scan_genes(genes_section)
    detected = detect_format(genes_section)
    blocks = len(list(iter_gene_blocks(genes_section)))

    if expected == 0:
        issues.append(
            SchemaIssue(
                "error",
                "genes.unrecognised_shape",
                f"'genes' has {len(genes_section)} entries but none match either "
                f"known PharmCAT shape (flat genes->symbol, or nested "
                f"genes->source->symbol); keys: {sorted(genes_section)[:8]}",
            )
        )
    elif unrecognised:
        issues.append(
            SchemaIssue(
                "warning",
                "genes.partial_shape",
                f"'genes' entries not recognised as gene data or guideline "
                f"buckets, and therefore not parsed: {sorted(unrecognised)[:8]}",
            )
        )

    if expected > 0 and blocks == 0:
        issues.append(
            SchemaIssue(
                "error",
                "genes.not_walkable",
                f"'genes' contains {expected} gene entries but the walker "
                f"extracted 0 -- the report format has moved ahead of the "
                f"parser and every gene would be silently dropped",
            )
        )
    elif blocks < expected:
        issues.append(
            SchemaIssue(
                "error",
                "genes.undercounted",
                f"'genes' contains {expected} gene entries but the walker "
                f"extracted only {blocks} (detected format: {detected})",
            )
        )
    elif blocks > expected:
        issues.append(
            SchemaIssue(
                "warning",
                "genes.overcounted",
                f"the walker extracted {blocks} gene blocks from {expected} "
                f"recognised gene entries (detected format: {detected})",
            )
        )

    return detected, expected, blocks


def _check_drugs(report: Mapping[str, Any], issues: List[SchemaIssue]) -> None:
    """Validate ``drugs[source][drug].guidelines[].annotations[]``.

    That chain is the whole of ``PharmCATParser._parse_drugs``; every field it
    reads off an annotation is optional (3.4.0 dropped ``source``, ``citations``
    and ``urls`` from drug entries without breaking anything), so only the
    container *types* are enforced.
    """
    if "drugs" not in report:
        return  # a genes-only payload is legitimate

    drugs = report.get("drugs")
    if drugs is None:
        return
    if not isinstance(drugs, Mapping):
        issues.append(
            SchemaIssue(
                "error",
                "drugs.not_a_mapping",
                f"'drugs' is {type(drugs).__name__}, expected an object keyed by "
                f"guideline source",
            )
        )
        return

    broken: Dict[str, List[str]] = {}

    def flag(code: str, where: str) -> None:
        broken.setdefault(code, []).append(where)

    for source, drugs_in_source in drugs.items():
        if not isinstance(drugs_in_source, Mapping):
            flag(
                "drugs.source_not_a_mapping",
                f"{source} ({type(drugs_in_source).__name__})",
            )
            continue

        for drug_name, drug_data in drugs_in_source.items():
            if not isinstance(drug_data, Mapping):
                flag("drugs.entry_not_a_mapping", f"{source}/{drug_name}")
                continue

            if "guidelines" not in drug_data:
                flag("drugs.guidelines_missing", f"{source}/{drug_name}")
                continue

            guidelines = drug_data.get("guidelines")
            if guidelines is None:
                continue
            if not isinstance(guidelines, list):
                flag(
                    "drugs.guidelines_not_a_list",
                    f"{source}/{drug_name} ({type(guidelines).__name__})",
                )
                continue

            for guideline in guidelines:
                if not isinstance(guideline, Mapping):
                    flag("drugs.guideline_not_a_mapping", f"{source}/{drug_name}")
                    continue
                annotations = guideline.get("annotations")
                if annotations is None:
                    continue
                if not isinstance(annotations, list):
                    flag(
                        "drugs.annotations_not_a_list",
                        f"{source}/{drug_name} ({type(annotations).__name__})",
                    )

    for code, where in broken.items():
        severity: Severity = (
            "warning" if code == "drugs.guidelines_missing" else "error"
        )
        issues.append(
            SchemaIssue(
                severity,
                code,
                _describe_drug_breakage(code, where),
            )
        )


def _describe_drug_breakage(code: str, where: Sequence[str]) -> str:
    shown = ", ".join(where[:_MAX_REPORTED_DRUG_ISSUES])
    more = len(where) - _MAX_REPORTED_DRUG_ISSUES
    tail = f" (+{more} more)" if more > 0 else ""
    return (
        f"{len(where)} drug entr{'y' if len(where) == 1 else 'ies'} break the "
        f"drugs->source->drug->guidelines[]->annotations[] walk: {shown}{tail}"
    )


def _check_version(
    report: Mapping[str, Any], issues: List[SchemaIssue]
) -> Tuple[Optional[str], Optional[Tuple[int, int]], bool]:
    raw = report.get("pharmcatVersion")
    version = _clean(raw)

    if version is None:
        issues.append(
            SchemaIssue(
                "warning",
                "version.missing",
                "report.json carries no 'pharmcatVersion'; results cannot be "
                "attributed to a verified PharmCAT release",
            )
        )
        return None, None, False

    parsed = parse_pharmcat_version(version)
    if parsed is None:
        issues.append(
            SchemaIssue(
                "warning",
                "version.unparseable",
                f"'pharmcatVersion' {version!r} is not a recognisable version "
                f"number; results cannot be attributed to a verified release",
            )
        )
        return version, None, False

    series = (parsed[0], parsed[1])
    if series in SUPPORTED_VERSION_SERIES:
        return version, series, True

    known = ", ".join(f"{a}.{b}" for a, b in sorted(SUPPORTED_VERSION_SERIES))
    issues.append(
        SchemaIssue(
            "warning",
            "version.unsupported",
            f"PharmCAT {version} has not been verified against this parser "
            f"(known-good series: {known}); the structure checks passed, so the "
            f"results are usable, but treat them as unverified until a fixture "
            f"for {series[0]}.{series[1]} is added",
        )
    )
    return version, series, False


def validate_report(report: Any) -> ValidationResult:
    """Check a PharmCAT ``report.json`` payload before anything parses it.

    Two independent gates:

    * **version** -- is ``pharmcatVersion`` a release this parser has been shown
      to handle?  A miss is always a *warning*.  Hard-failing would turn an
      upstream point release into a clinical outage, after the expensive VCF
      preprocessing and PharmCAT run already succeeded, over a bump that is
      usually harmless.  The structural gate below is what has teeth.
    * **structure** -- do the containers the walkers traverse still have the
      types they need?  Violations are *errors*: the parser's ``isinstance``
      guards mean a shape change never raises, it just yields nothing, and an
      empty report is indistinguishable from a clean one.
    """
    issues: List[SchemaIssue] = []

    if not isinstance(report, Mapping):
        issues.append(
            SchemaIssue(
                "error",
                "report.not_an_object",
                f"report.json is {type(report).__name__}, expected a JSON object",
            )
        )
        return ValidationResult(
            version=None,
            version_series=None,
            version_supported=False,
            detected_format="empty",
            gene_entry_count=0,
            gene_block_count=0,
            issues=tuple(issues),
        )

    version, series, supported = _check_version(report, issues)
    detected, entries, blocks = _check_genes(report, issues)
    _check_drugs(report, issues)

    unannotated = report.get("unannotatedGeneCalls")
    if unannotated is not None and not isinstance(unannotated, list):
        issues.append(
            SchemaIssue(
                "error",
                "unannotatedGeneCalls.not_a_list",
                f"'unannotatedGeneCalls' is {type(unannotated).__name__}, "
                f"expected a list of gene call objects",
            )
        )

    messages = report.get("messages")
    if messages is not None and not isinstance(messages, list):
        issues.append(
            SchemaIssue(
                "warning",
                "messages.not_a_list",
                f"'messages' is {type(messages).__name__}, expected a list",
            )
        )

    return ValidationResult(
        version=version,
        version_series=series,
        version_supported=supported,
        detected_format=detected,
        gene_entry_count=entries,
        gene_block_count=blocks,
        issues=tuple(issues),
    )
