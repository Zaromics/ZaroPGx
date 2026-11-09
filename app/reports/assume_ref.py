"""The Alerts and Warnings entry for a run that assumed the reference allele.

This lives on the reports side, not beside the flag logic in
``app/utils/pharmcat_assume_ref.py``, for a structural reason:
``docker/pharmcat/Dockerfile`` copies that file ALONE into ``/assume-ref-lib``
and the PharmCAT sidecar imports it as a bare module, with no ``app`` package
anywhere in that image. Anything shipped that way must import nothing from
``app`` -- a module-level ``from app...`` there makes PharmCAT fail with
``ModuleNotFoundError`` before it does any work, which it did.

Only the report builds this alert; the sidecar imports
``resolve_assume_ref_flags`` and ``pharmcat_cli_ref_flags`` and nothing else.
``tests/test_sidecar_standalone_modules.py`` holds the invariant.
"""

from typing import Optional

from app.reports.marks import CAUTION_SVG
from app.utils.pharmcat_assume_ref import pharmcat_cli_ref_flags


def assume_ref_alert(absent: bool, unspecified: bool) -> Optional[str]:
    """The Alerts and Warnings entry for a run that assumed the reference allele."""
    if not absent and not unspecified:
        return None
    if absent and unspecified:
        assumed = (
            "Positions absent from the file, and positions it lists without a "
            "genotype (<code>./.</code>),"
        )
    elif absent:
        assumed = "Positions absent from the file"
    else:
        assumed = "Positions the file lists without a genotype (<code>./.</code>)"
    (flag,) = pharmcat_cli_ref_flags(absent, unspecified)
    return (
        f"<p>{CAUTION_SVG} {assumed} were assumed to be the reference allele (PharmCAT "
        f"<code>{flag}</code>). Fabricating reference calls can over-call "
        "*1/Reference and normal phenotypes: a position the data never covered "
        "reads as reference. PharmCAT documents these flags as dangerous / "
        "research-oriented. Confirm assayed coverage before interpreting results.</p>"
    )
