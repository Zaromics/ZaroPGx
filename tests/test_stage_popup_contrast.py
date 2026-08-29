"""The stage popups are white in both themes, so they must set their own text colour.

``.stage-popup`` hardcodes ``background: white`` and has no dark-theme variant.
Its ``h6``, ``p`` and ``.toggle-button`` children each declare an explicit colour,
so they looked fine and the gap went unnoticed -- but anything else inside
inherited ``color`` from ``body``, which under ``data-bs-theme="dark"`` is nearly
white. White text on a white card.

The PharmCAT popup is the only one whose content is none of those three: it
carries ``<label>`` elements and a ``<p class="small text-muted">`` for the
assume-reference flags. Those were invisible in dark mode -- a control the user
is meant to read before enabling a flag PharmCAT itself documents as dangerous.

Two separate rules are needed and both are pinned here:

* ``.stage-popup`` must declare ``color`` itself, so *any* future child inherits
  a dark colour rather than the page's;
* the Bootstrap utilities used inside it resolve against theme variables, and
  ``.text-muted`` ships as ``color: var(--bs-secondary-color) !important``, so
  matching specificity is not enough -- the override must also be ``!important``.
  (Getting this wrong is not hypothetical: the first fix here omitted it and the
  muted paragraph stayed faint.)

Asserted against the stylesheet text because there is no DOM here; the check is
"the rule exists and is strong enough", which is exactly what regressed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"


def _rule_body(selector: str) -> str:
    """The declarations of the first CSS rule whose selector list matches."""
    source = INDEX_HTML.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(?:^|\n)\s*" + re.escape(selector) + r"\s*(?:,[^{]*)?\{([^}]*)\}"
    )
    match = pattern.search(source)
    assert match is not None, f"no CSS rule found for {selector!r}"
    return match.group(1)


def test_the_popup_sets_its_own_text_colour():
    """Without this every uncoloured child inherits the dark theme's body text."""
    body = _rule_body(".stage-popup")
    assert re.search(r"(?<!-)\bcolor\s*:", body), (
        ".stage-popup declares `background: white` but no `color`. Any child "
        "without its own colour will inherit the dark theme's near-white body "
        "text and vanish. Declare a colour on the popup itself."
    )


def test_the_popup_still_has_the_white_background_that_makes_this_necessary():
    """Guard the premise: if the card became theme-aware, revisit this module."""
    assert "background: white" in _rule_body(".stage-popup")


def test_muted_text_inside_the_popup_overrides_bootstrap_with_important():
    body = _rule_body(".stage-popup .text-muted")
    assert "!important" in body, (
        "Bootstrap ships `.text-muted { color: var(--bs-secondary-color) "
        "!important }`. A same-specificity override without !important loses, "
        "and the muted line stays unreadable on the white popup."
    )


@pytest.mark.parametrize("selector", [".stage-popup label", ".stage-popup .text-muted"])
def test_the_pharmcat_popups_own_element_types_are_covered(selector):
    """The two shapes the assume-ref block actually uses."""
    assert _rule_body(selector).strip(), f"{selector} has no declarations"


def test_the_assume_ref_controls_are_still_inside_a_stage_popup():
    """The premise again: these controls live on the white card, not the page.

    If they ever move out onto the page proper they inherit the *page* theme, and
    these rules both stop applying and stop being needed. Checked by walking back
    from the control to the nearest popup opening and confirming the PharmCAT
    heading sits between the two -- i.e. the control really is in that popup.
    """
    source = INDEX_HTML.read_text(encoding="utf-8")
    control = source.index('id="pharmcatAbsentToRef"')
    popup_open = source.rindex('<div class="stage-popup">', 0, control)
    between = source[popup_open:control]

    assert (
        "PharmCAT Analysis" in between
    ), "the assume-ref controls are no longer inside the PharmCAT stage popup"


# --------------------------------------------------------------------------
# The native checkboxes on that same white card
# --------------------------------------------------------------------------


def test_the_popup_forces_a_light_colour_scheme():
    """The off state fix, and the reason it is one line rather than many.

    ``[data-bs-theme='dark']`` sets ``color-scheme: dark`` on :root, so the
    browser painted the popup's native checkboxes for a dark surface -- a
    near-black box on a white card. Scoping the scheme to the popup fixes the
    unticked state at its source and covers any native control added here later,
    rather than hand-repainting each one.
    """
    assert "color-scheme: light" in _rule_body(".stage-popup")


def test_the_checkboxes_are_enlarged_and_green():
    body = _rule_body('.stage-popup input[type="checkbox"]')
    assert "accent-color" in body, "checked state falls back to the browser blue"
    # The same green as the "✓ Enabled" buttons in the sibling popups.
    assert "#198754" in body
    assert re.search(r"width:\s*1[3-9]px", body), body


def test_the_checkboxes_keep_a_hover_affordance():
    assert _rule_body(
        '.stage-popup label:hover input[type="checkbox"]'
    ).strip(), "hover feedback on the checkboxes was dropped"
