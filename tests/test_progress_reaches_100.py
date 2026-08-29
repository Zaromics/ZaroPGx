"""The bar has to land on 100% when the run finishes.

``onComplete`` does exactly two things to the bar, synchronously, on one tick:

    smoothProgress.updateProgress(100, 'completed', 'Analysis complete!');
    smoothProgress.stopAnimation();

``updateProgress`` starts a ``requestAnimationFrame`` walk from the current value
toward 100. ``stopAnimation`` then called ``cancelAnimationFrame`` -- killing the
very first frame before it ever ran. The bar could therefore *never* reach 100 by
this path; it froze wherever the previous stage had left it, and reporting it as
"did not reach 100 when it finished" is exactly right.

A second, independent cause sat in front of it. ``updateProgress`` enforces a
minimum stage duration by stashing the update in ``pendingStageUpdate`` and
returning early -- and that early return happens *before* ``this.targetProgress``
is assigned. So when completion arrived soon after a stage change, the manager
was still pointing at the previous stage's target while ``onComplete`` went on to
mark the run finished. Completion now bypasses that gate: minStageDuration exists
to stop intermediate stages flickering past, and the terminal update is not one.

Both fixes are in the same two methods, so this drives the real class out of the
template in Node rather than restating its logic. ``requestAnimationFrame`` is
stubbed to never fire, which is precisely the case that used to lose: it proves
the final value comes from settling on the target and not from an animation frame
that happened to sneak in.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


def _manager_source() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    start = html.index("class SmoothProgressManager {")
    # The class ends at the dedented closing brace of the class body.
    end = html.index("\n            }\n", start) + len("\n            }\n")
    source = html[start:end]
    assert "stopAnimation()" in source, "did not capture the whole class"
    return source


def _run(scenario: str) -> dict:
    """Drive the real SmoothProgressManager in Node and report the bar state."""
    harness = f"""
const fakeEl = () => ({{
  style: {{}}, textContent: '', _attrs: {{}},
  setAttribute(k, v) {{ this._attrs[k] = v; }},
  classList: {{ _s: new Set(),
    add(...c) {{ c.forEach(x => this._s.add(x)); }},
    remove(...c) {{ c.forEach(x => this._s.delete(x)); }},
    contains(c) {{ return this._s.has(c); }} }},
}});
// Never fires. The bug was that the only path to 100 was through a frame that
// stopAnimation() cancelled, so a no-op rAF is the scenario that must still work.
globalThis.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = () => {{}};
globalThis.document = {{ getElementById: () => null }};
// The class logs stage transitions to console.log; silence them so the result
// below is the only thing on stdout and stays parseable.
console.log = () => {{}};
console.warn = () => {{}};

{_manager_source()}

const bar = fakeEl(), status = fakeEl();
const m = new SmoothProgressManager(bar, status);
{scenario}
process.stdout.write(JSON.stringify({{
  width: bar.style.width,
  text: bar.textContent,
  aria: bar._attrs['aria-valuenow'],
  current: m.currentProgress,
  target: m.targetProgress,
}}));
"""
    proc = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_oncomplete_sequence_lands_on_100():
    """The reported bug, reproduced as the app actually calls it."""
    state = _run("""
        m.updateProgress(45, 'pharmcat', 'PharmCAT processing');
        m.updateProgress(100, 'completed', 'Analysis complete!');
        m.stopAnimation();
        """)

    assert state["width"] == "100%", state
    assert state["text"] == "100%", state
    assert str(state["aria"]) == "100", state


def test_completion_is_not_deferred_by_the_minimum_stage_duration():
    """A stage change immediately followed by completion.

    This is the ordering that loses: updateProgress's early return fires before
    targetProgress is assigned, so the manager was still aimed at the old stage.
    """
    state = _run("""
        m.updateProgress(52, 'pharmcat', 'PharmCAT processing');
        m.updateProgress(100, 'completed', 'Analysis complete!');
        m.stopAnimation();
        """)

    assert state["target"] == 100, state
    assert state["width"] == "100%", state


def test_stopping_mid_run_settles_rather_than_freezing():
    """stopAnimation is 'finish now', not 'abandon wherever we were'."""
    state = _run("""
        m.updateProgress(30, 'pypgx', 'PyPGx');
        m.stopAnimation();
        """)

    assert state["width"] == "30%", state
    assert state["current"] == 30, state


def test_the_bar_never_exceeds_100():
    state = _run("""
        m.updateProgress(140, 'completed', 'overshoot');
        m.stopAnimation();
        """)

    assert state["width"] == "100%", state


def test_oncomplete_still_calls_both_methods_in_that_order():
    """If the call sites move, the scenarios above stop mirroring production."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    start = html.index("onComplete: (data) => {")
    body = html[start : start + 1200]

    update = body.index("updateProgress(100")
    stop = body.index("stopAnimation()")
    assert update < stop, "onComplete no longer updates before stopping"
