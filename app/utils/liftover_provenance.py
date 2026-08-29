# app/utils/liftover_provenance.py
"""The report's one sentence about a GRCh37->GRCh38 liftover.

A lifted run's results are reported on coordinates the uploaded file never had.
That is a fact about the analysis, so the report has to state it: without this
sentence a GRCh37 upload and a native GRCh38 upload produce reports that are
indistinguishable, and the reader has no way to know variants were dropped.

The counts come from the liftover ``JobStep``'s ``output_data``, written by
gatk-api's ``/liftover-vcf`` when the step completes. That row exists only if the
lift actually ran, which is why it is the source rather than the upload-time
``needs_liftover`` flag -- the flag records an intention, the row records what
happened.

Shaped after ``app/utils/pharmcat_assume_ref.py``: build the sentence in one
testable place, hand the template a string, let the template render it only if it
resolved.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


def _as_count(value: Any) -> Optional[int]:
    """An int count, or None for anything that is not one.

    Deliberately strict: a missing or malformed count must drop the numbers from
    the sentence, never render as "None dropped" or invent a zero. Zero itself is
    a real and reassuring answer, so it must survive.
    """
    if isinstance(value, bool):  # bool is an int subclass; not a count
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    return None


def liftover_provenance_sentence(
    output_data: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """One sentence naming the lift and its cost, or None if no lift ran.

    Args:
        output_data: the liftover step's ``output_data``. None/empty means the
            step never ran (the ordinary case: a native GRCh38 upload).

    Returns:
        A sentence for the run-provenance paragraph, or None to render nothing.
    """
    if not output_data:
        return None

    source = str(output_data.get("source_build") or "").strip() or "GRCh37"
    target = str(output_data.get("target_build") or "").strip() or "GRCh38"

    lifted = _as_count(output_data.get("n_lifted"))
    rejected = _as_count(output_data.get("n_rejected"))

    if lifted is None or rejected is None:
        # The lift ran but did not report usable counts. Still say it happened --
        # the build change is the part the reader cannot afford to miss.
        return (
            f"This file was uploaded on {source} and lifted over to {target} "
            "before analysis."
        )

    return (
        f"This file was uploaded on {source} and lifted over to {target} before "
        f"analysis: {lifted:,} variants lifted, {rejected:,} dropped as unliftable."
    )
