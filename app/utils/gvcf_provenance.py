# app/utils/gvcf_provenance.py
"""The report's paragraph about a gVCF that was genotyped before analysis.

A gVCF upload is the one input whose homozygous-reference calls at pharmacogene
positions are *real*. Every other lane either has a variant there or has nothing,
and "nothing" only becomes a reference call if the operator turns on one of
PharmCAT's assume-reference flags, which fabricates it. This lane does not NEED
either flag: ``gatk GenotypeGVCFs --include-non-variant-sites -L
pharmcat_positions.vcf`` emits those genotypes out of the gVCF's own
reference-confidence blocks.

Not needing them is not the same as not using them, which is why this module takes
the run's actual flags as arguments. ``--absent-to-ref`` / ``--unspecified-to-ref``
are GLOBAL checkboxes (index.html) resolved once per upload from form-or-env
(``upload_router.resolve_assume_ref_flags``) and forwarded to PharmCAT by
``main.nf`` with no input-type branch anywhere on the way. This paragraph used to
state "``--absent-to-ref`` was not used on this run" unconditionally, which was
simply false for any run where the uploader ticked the box.

WHICH flag matters here is the counter-intuitive part, and the paragraph has to say
it. The PGx pass runs ``--include-non-variant-sites``, which emits a row at EVERY
position in the interval list -- including the ones the gVCF had no block for, and
those come out ``./.`` (see ``count_positions_with_calls`` in
``docker/gatk-api/gatk_api.py``, which counts them as no coverage for exactly this
reason). PharmCAT's preprocessor documents ``--absent-to-ref`` as acting on
positions MISSING from the VCF and ``--unspecified-to-ref`` as acting on positions
PRESENT with ``./.``. So on this lane the flag that turns "your file did not cover
this" into ``0/0`` is ``--unspecified-to-ref``, and it is the one that can make the
coverage numbers above it mean the opposite of what they say.

That distinction is worth a paragraph, and it is worth it *next to* the
assume-reference paragraph (``app/utils/pharmcat_assume_ref.py``), because the two
describe the opposite ends of the same question and a reader who sees only one of
them cannot tell which they are looking at. They must also never contradict each
other, which ``tests/test_gvcf_report_provenance.py`` pins directly.

The paragraph also carries the two caveats the reader is owed:

* Coverage. A gVCF that omits a region has no reference block there, so those
  positions are no-calls rather than reference calls -- UNLESS
  ``--unspecified-to-ref`` was used, in which case PharmCAT rewrote them and the
  paragraph says so instead. The counts come from the step's own ``output_data`` --
  what happened, not what was planned.
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

# Why an uncovered PharmCAT position arrives as a present ./. row rather than as an
# absent one. Said once, used by both arms below, because the two arms are the same
# fact told to a reader who did and did not turn the flag on.
_WHY_UNSPECIFIED = (
    "the reference pass emits a row at every position in PharmCAT's list, so a "
    "position your file did not cover arrives as a present <code>./.</code> row "
    "rather than as a missing one"
)


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
    *,
    absent_to_ref: bool,
    unspecified_to_ref: bool,
) -> Optional[str]:
    """The run-provenance paragraph for a genotyped gVCF, or None if none ran.

    Args:
        output_data: the ``gvcf_to_vcf`` step's ``output_data``. None/empty means the
            step never ran, which is every input that was not a gVCF.
        absent_to_ref: whether PharmCAT ran with ``--absent-to-ref`` on THIS run.
        unspecified_to_ref: whether it ran with ``--unspecified-to-ref``.

    Both flags are required rather than defaulted: a default would let a caller that
    does not know the run's configuration print the old unconditional "not used on
    this run", which is the claim this signature exists to stop.

    Returns:
        An HTML paragraph for the methodology section, or None to render nothing.
    """
    if not output_data:
        return None

    called = _as_count(output_data.get("n_pgx_positions_called"))
    total = _as_count(output_data.get("n_pharmcat_positions"))

    if called is None or total is None or total == 0:
        # The conversion ran but did not report usable counts. Still say what it was
        # -- what the reference calls rest on is the part the reader cannot afford to
        # miss, and it does not depend on the numbers.
        coverage = (
            "Reference genotypes at the pharmacogene positions come from those "
            "blocks."
        )
    else:
        absent = _as_count(output_data.get("n_positions_absent"))
        if absent is None:
            absent = max(0, total - called)
        # The fate of the uncovered positions is NOT a property of this lane; it is a
        # property of the run's flags. Saying "remain no-calls" while
        # --unspecified-to-ref rewrote them to 0/0 is the wrong clinical answer, told
        # in the same sentence as the number that measured it.
        fate = (
            "and {absent:,} were not covered by the file"
            if unspecified_to_ref
            else "and {absent:,} were not covered by the file and remain no-calls"
        ).format(absent=absent)
        coverage = (
            f"Reference genotypes at the pharmacogene positions come from those "
            f"blocks: {called:,} of PharmCAT's {total:,} positions carried a call, "
            f"{fate}."
        )

    if unspecified_to_ref:
        # The dangerous combination, and the reason this function takes flags at all.
        used = (
            "<code>--missing-to-ref</code> (both <code>--absent-to-ref</code> and "
            "<code>--unspecified-to-ref</code>)"
            if absent_to_ref
            else "<code>--unspecified-to-ref</code>"
        )
        assume_ref = (
            f"Those uncovered positions did NOT stay no-calls: this run used PharmCAT's "
            f"{used}, and on this lane that is the flag that matters — "
            f"{_WHY_UNSPECIFIED}, which is exactly what "
            f"<code>--unspecified-to-ref</code> rewrites to homozygous reference. The "
            f"genotypes analysed at the uncovered positions are therefore assumed, not "
            f"called; only the covered ones came out of your file's reference-confidence "
            f"blocks. "
        )
    elif absent_to_ref:
        assume_ref = (
            "This run used PharmCAT's <code>--absent-to-ref</code>, which fabricates a "
            "homozygous-reference call at any pharmacogene position missing from the "
            "analysed VCF. It has little to act on here, and that is worth knowing "
            f"rather than reassuring: {_WHY_UNSPECIFIED}. The flag that would have "
            "turned those rows into reference calls is "
            "<code>--unspecified-to-ref</code>, which was not used on this run, so the "
            "uncovered positions above stayed no-calls. "
        )
    else:
        assume_ref = (
            "They are called data, not assumed — neither of PharmCAT's "
            "assume-reference flags was used on this run, and this lane does not need "
            f"them. Had one been used it would have been "
            f"<code>--unspecified-to-ref</code>, not <code>--absent-to-ref</code>: "
            f"{_WHY_UNSPECIFIED}. "
        )

    return (
        "<p><strong>gVCF genotyping:</strong> This run was uploaded as a gVCF and "
        "genotyped with GATK <code>GenotypeGVCFs</code> before analysis, using "
        "<code>--include-non-variant-sites</code> over PharmCAT's own position list. "
        f"{coverage} {assume_ref}Two caveats: "
        "<code>GenotypeGVCFs</code> re-derives each genotype from the recorded "
        "likelihoods rather than copying the original caller's, so the genotypes "
        "analysed here are not guaranteed identical to that caller's output (the "
        "calling-confidence threshold was set to zero, so nothing was dropped for "
        "failing a cutoff, but the re-derivation itself remains); and positions "
        "PharmCAT discards because their indel representation does not match its own "
        "definitions stay no-calls, the same as they would from a plain VCF.</p>"
    )
