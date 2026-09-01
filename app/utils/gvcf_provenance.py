# app/utils/gvcf_provenance.py
"""The report's paragraph about a gVCF that was genotyped before analysis.

A gVCF upload is the one input whose homozygous-reference calls at pharmacogene
positions are *real*. Every other lane either has a variant there or has nothing,
and "nothing" only becomes a reference call if the operator turns on PharmCAT's
``--absent-to-ref``, which fabricates it. This lane does not use that flag and does
not need to: ``gatk GenotypeGVCFs --include-non-variant-sites -L
pharmcat_positions.vcf`` emits those genotypes out of the gVCF's own
reference-confidence blocks.

That distinction is worth a paragraph, and it is worth it *next to* the
``--absent-to-ref`` paragraph (``app/utils/pharmcat_assume_ref.py``), because the two
describe the opposite ends of the same question and a reader who sees only one of
them cannot tell which they are looking at.

The paragraph also carries the two caveats the reader is owed, because both of them
make a position a no-call rather than a reference call:

* Coverage. A gVCF that omits a region has no reference block there, so those
  positions are absent, not reference. The counts come from the step's own
  ``output_data`` -- what happened, not what was planned.
* Re-genotyping. GenotypeGVCFs derives each genotype afresh from the recorded
  likelihoods rather than copying the original caller's GT. ZaroPGx runs it with
  ``--standard-min-confidence-threshold-for-calling 0`` so that nothing is dropped
  for failing a cutoff the uploader never chose, but that removes a *filter*, not the
  re-derivation, so the emitted genotypes are still not guaranteed identical to the
  original caller's.

Not carried, and deliberately: the count of positions PharmCAT discarded for indel
representation mismatch. It is in PharmCAT's ``*.match_warnings.txt``, which nothing
in this repo reads; the fact is stated qualitatively below instead of with a number
this module would have to invent.

Shaped after ``app/utils/liftover_provenance.py``: build the text in one testable
place, hand the template a string, let the template render it only if it resolved.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


def _as_count(value: Any) -> Optional[int]:
    """An int count, or None for anything that is not one.

    Deliberately strict, for the reason liftover_provenance's copy of this gives: a
    missing or malformed count must drop the numbers from the sentence rather than
    render as "None" or invent a zero. Zero itself is a real answer -- a gVCF that
    covered none of PharmCAT's positions is exactly what the reader needs told -- so
    it must survive.
    """
    if isinstance(value, bool):  # bool is an int subclass; not a count
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    return None


def gvcf_provenance_paragraph(
    output_data: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """The run-provenance paragraph for a genotyped gVCF, or None if none ran.

    Args:
        output_data: the ``gvcf_to_vcf`` step's ``output_data``. None/empty means the
            step never ran, which is every input that was not a gVCF.

    Returns:
        An HTML paragraph for the methodology section, or None to render nothing.
    """
    if not output_data:
        return None

    called = _as_count(output_data.get("n_pgx_positions_called"))
    total = _as_count(output_data.get("n_pharmcat_positions"))

    if called is None or total is None or total == 0:
        # The conversion ran but did not report usable counts. Still say what it was
        # -- that the reference calls are called data is the part the reader cannot
        # afford to miss, and it does not depend on the numbers.
        coverage = (
            "Reference genotypes at the pharmacogene positions come from those "
            "blocks."
        )
    else:
        absent = _as_count(output_data.get("n_positions_absent"))
        if absent is None:
            absent = max(0, total - called)
        coverage = (
            f"Reference genotypes at the pharmacogene positions come from those "
            f"blocks: {called:,} of PharmCAT's {total:,} positions carried a call, "
            f"and {absent:,} were not covered by the file and remain no-calls."
        )

    return (
        "<p><strong>gVCF genotyping:</strong> This run was uploaded as a gVCF and "
        "genotyped with GATK <code>GenotypeGVCFs</code> before analysis, using "
        "<code>--include-non-variant-sites</code> over PharmCAT's own position list. "
        f"{coverage} They are called data, not assumed — PharmCAT's "
        "<code>--absent-to-ref</code> was not used on this run. Two caveats: "
        "<code>GenotypeGVCFs</code> re-derives each genotype from the recorded "
        "likelihoods rather than copying the original caller's, so the genotypes "
        "analysed here are not guaranteed identical to that caller's output (the "
        "calling-confidence threshold was set to zero, so nothing was dropped for "
        "failing a cutoff, but the re-derivation itself remains); and positions "
        "PharmCAT discards because their indel representation does not match its own "
        "definitions stay no-calls, the same as they would from a plain VCF.</p>"
    )
