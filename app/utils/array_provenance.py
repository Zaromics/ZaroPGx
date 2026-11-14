# app/utils/array_provenance.py
"""The report's paragraph about a consumer genotyping array that was read onto
PharmCAT's positions before analysis.

Shaped after ``app/utils/gvcf_provenance.py`` and for the same reason: build the text in
one testable place, hand the template a string, let the template render it only if it
resolved. The counts come from the ``array_to_vcf`` step's own ``output_data`` -- what
happened, not what was planned.

WHAT THE READER CANNOT INFER FROM THE RESULTS, and therefore has to be told:

* **How little of PharmCAT's list the file carried.** A chip's report is short, and a
  short report is indistinguishable from a healthy one unless the denominator is on the
  page. This is the one number that frames everything else.
* **That the match was by rs identifier**, not by the coordinates in their file. It is
  the uncommon choice, it is why no liftover appears anywhere in their report, and it is
  what keeps rs6025 (Factor V Leiden) from being dropped -- a reference-only chip row has
  no ALT for Picard to swap when the two builds disagree about which allele is reference.
* **What was dropped, and why.** A no-call is a position the chip tried and failed to
  read; an `I`/`D` row names no sequence and cannot be represented at all (the
  UGT1A1*28 TA repeat is one); a base matching neither allele in PharmCAT's record is a
  strand problem. Each is counted separately because they mean different things, and a
  single "rows skipped" figure would hide the one that indicates a systematic error.

NOT carried here, deliberately: which genes came back callable. That is the gene table's
job, and the AMP/CAP Tier 1 gate (``app/reports/tier1_gate.py``) raises its own alert for
the genes it withheld. Two places saying what was not called is two places to drift.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

#: Vendor spellings for the shape the conversion reported, so the paragraph names the
#: file the way the reader does. The conversion decides the shape from the row's own
#: field count rather than from a label, so this maps what it observed.
_VENDOR = {"23andme": "23andMe", "ancestrydna": "AncestryDNA"}


def _as_count(value: Any) -> Optional[int]:
    """A non-negative int, or None. A count that did not survive the step row must not
    be rendered as 0: "0 positions unmappable" and "we do not know" are different."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if count >= 0 else None


def array_provenance_paragraph(
    output_data: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """The run-provenance paragraph for a converted consumer array, or None.

    None means no array conversion ran, which is every other input. Never raises: a step
    row that does not parse as expected costs this paragraph, not the report.
    """
    if not output_data:
        return None

    carried = _as_count(output_data.get("n_positions_carried"))
    available = _as_count(output_data.get("n_pharmcat_positions_rsid"))
    vendor = _VENDOR.get(
        str(output_data.get("vendor_shape") or "").strip().lower(), "genotyping array"
    )

    if carried is None or not available:
        # The conversion ran but did not report usable counts. The method still has to
        # be stated: how the file was matched is the part the reader cannot infer, and
        # it does not depend on the numbers.
        coverage = ""
    else:
        coverage = (
            f"{carried:,} of the {available:,} positions in PharmCAT's list that carry "
            f"an rs identifier were present in your file. "
        )

    dropped = []
    no_calls = _as_count(output_data.get("n_no_calls"))
    if no_calls:
        dropped.append(
            f"{no_calls:,} position(s) the chip reported as a no-call, which it tried "
            "to read and could not"
        )
    indels = _as_count(output_data.get("n_indel_coded"))
    if indels:
        dropped.append(
            f"{indels:,} row(s) written with the insertion/deletion coding, which names "
            "no sequence and cannot be represented (the UGT1A1*28 TA repeat is one of "
            "these)"
        )
    unmappable = _as_count(output_data.get("n_unmappable"))
    if unmappable:
        named = [str(r) for r in (output_data.get("unmappable_rsids") or [])][:5]
        listed = f" ({', '.join(named)})" if named else ""
        dropped.append(
            f"{unmappable:,} position(s) whose reported bases match neither allele in "
            f"PharmCAT's record{listed}, which indicates a strand or annotation "
            "mismatch rather than a genotype"
        )
    losses = f"Not carried: {'; '.join(dropped)}. " if dropped else ""

    return (
        f"<p><strong>Genotyping array:</strong> This {vendor} export was read onto "
        "PharmCAT's own GRCh38 positions by rs identifier before analysis. "
        f"{coverage}{losses}"
        "The match is on the rs identifier rather than on the coordinates in your file, "
        "so no liftover is involved: a chip's coordinates are build 37, and converting "
        "them instead would drop any position where the two builds disagree about which "
        "allele is the reference — rs6025, Factor V Leiden, among them. "
        "PyPGx was not run: it calls copy number from read depth, and an array has no "
        "reads. A gene is reported only where this file could assess the alleles a "
        "clinical test for it must assess.</p>"
    )
