"""Reaching 100% has to look like something.

The bar climbed to 100%, ``stopAnimation()`` stripped the stripe classes, and it
froze in the same ``bg-info`` cyan it had been the whole run. Nothing marked the
moment the analysis finished, so a user who looked away had no way to tell a
finished run from a stalled one -- which, given the bar genuinely *did* stall for
the whole liftover step until recently, is not a hypothetical confusion.

Two details worth keeping straight:

* the pulse animates ``background-color``, not ``box-shadow``. Bootstrap's
  ``.progress`` wrapper is ``overflow: hidden``, so a glow would be clipped away
  and the cue would silently do nothing;
* ``prefers-reduced-motion`` gets one slow cycle rather than none. That setting
  is about movement and repeated flashing, not a single colour settle, and
  suppressing it outright left the only completion cue as a static colour swap.
  It is enabled on the maintainer's own machine, so "none" would have meant he
  never saw the feature he asked for.
"""

from __future__ import annotations

import re
from pathlib import Path

INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"


def _source() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _rule_body(selector: str) -> str:
    match = re.search(
        r"(?:^|\n)\s*" + re.escape(selector) + r"\s*(?:,[^{]*)?\{([^}]*)\}", _source()
    )
    assert match is not None, f"no CSS rule found for {selector!r}"
    return match.group(1)


def test_the_completed_bar_turns_green_and_drops_the_stripes():
    body = _rule_body("#progressBar.progress-bar-complete")
    assert "#198754" in body, "completion colour is not the platform green"
    assert "background-image: none" in body, (
        "the stripe gradient on #progressBar would still show through; it is set "
        "on the id, so the completion rule has to clear it explicitly"
    )


def test_the_pulse_animates_colour_not_a_glow():
    """A box-shadow would be clipped by .progress's overflow:hidden."""
    keyframes = re.search(
        r"@keyframes\s+progress-complete-pulse\s*\{(.*?)\n        \}",
        _source(),
        re.S,
    )
    assert keyframes is not None, "the completion keyframes are gone"
    assert "background-color" in keyframes.group(1)
    assert "box-shadow" not in keyframes.group(1)


def test_reduced_motion_still_gets_a_single_ping():
    """Not `animation: none` -- that is how this shipped broken the first time."""
    match = re.search(
        r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n        \}",
        _source(),
        re.S,
    )
    assert match is not None
    block = match.group(1)
    assert "progress-complete-pulse" in block, (
        "reduced motion suppresses the completion cue entirely; give it one slow "
        "cycle instead of none"
    )
    assert not re.search(r"animation:\s*none", block), block


def test_completion_applies_the_class_and_clears_bg_info():
    source = _source()
    start = source.index("onComplete: (data) => {")
    body = source[start : start + 2000]
    assert "classList.add('progress-bar-complete')" in body
    assert "classList.remove('bg-info')" in body


def test_a_new_run_resets_the_bar_out_of_the_completed_state():
    """Otherwise the next upload starts already green."""
    source = _source()
    start = source.index("this.progressBar.style.width = '0%';")
    body = source[start : start + 600]
    assert "classList.remove('progress-bar-complete')" in body
    assert "classList.add('bg-info')" in body
