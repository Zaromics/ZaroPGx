"""The page's bar shows what the server computes, as it computes it.

Two gaps between the server's number and the screen:

- The page only heard from the server when a step reported, over the WebSocket. A
  step that reports nothing (a CRAM's 33-minute conversion) left the bar frozen
  though the server paces such a step by its elapsed time. WorkflowMonitor now polls
  /progress every few seconds while the job runs -- progress only: completion stays
  with the WebSocket message, which carries the report links /progress lacks.
- SmoothProgressManager ignored a new target while an animation ran. The old
  animation ran on to its old target and then set the new value without drawing it,
  so the bar sat on the old number until the next update.

Both run the real scripts under Node with the browser stubbed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "app" / "templates" / "index.html"
MONITOR = ROOT / "app" / "static" / "js" / "workflow-monitor.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


def _node(script: str) -> dict:
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# SmoothProgressManager
# ---------------------------------------------------------------------------


def _manager_source() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    start = html.index("class SmoothProgressManager {")
    end = html.index("\n            }\n", start) + len("\n            }\n")
    return html[start:end]


def _manager(scenario: str) -> dict:
    """Frames run only when the scenario pumps them, on a clock it sets."""
    return _node(f"""
const fakeEl = () => ({{
  style: {{}}, textContent: '', _attrs: {{}},
  setAttribute(k, v) {{ this._attrs[k] = v; }},
  classList: {{ add() {{}}, remove() {{}} }},
}});
let clock = 0, frames = new Map(), nextId = 1;
Date.now = () => clock;
globalThis.requestAnimationFrame = cb => {{ const id = nextId++; frames.set(id, cb); return id; }};
globalThis.cancelAnimationFrame = id => frames.delete(id);
const pump = (ms) => {{ clock += ms; const due = [...frames.values()]; frames.clear(); due.forEach(cb => cb()); }};
globalThis.document = {{ getElementById: () => null }};
console.log = () => {{}}; console.warn = () => {{}};
{_manager_source()}
const bar = fakeEl(), status = fakeEl();
const m = new SmoothProgressManager(bar, status);
const shown = [];
{scenario}
process.stdout.write(JSON.stringify({{ width: bar.style.width, current: m.currentProgress,
  target: m.targetProgress, shown }}));
""")


def test_a_new_target_mid_animation_is_eased_to_and_drawn():
    state = _manager("""
        m.updateProgress(20, 'pypgx_analysis', 'PyPGx');
        pump(0); pump(500); shown.push(bar.style.width);
        m.updateProgress(40, 'pypgx_analysis', 'PyPGx');
        for (let i = 0; i < 30; i++) { pump(50); shown.push(bar.style.width); }
        """)
    assert state["width"] == "40%", state
    widths = [int(w.rstrip("%")) for w in state["shown"]]
    assert widths == sorted(widths), f"the bar went backwards: {widths}"


def test_the_bar_does_not_run_backwards():
    state = _manager("""
        m.updateProgress(50, 'pypgx_analysis', 'PyPGx');
        for (let i = 0; i < 30; i++) pump(50);
        m.updateProgress(30, 'pypgx_analysis', 'PyPGx');
        for (let i = 0; i < 30; i++) pump(50);
        """)
    assert state["width"] == "50%", state
    assert state["target"] == 50, state


# ---------------------------------------------------------------------------
# WorkflowMonitor's poll
# ---------------------------------------------------------------------------


def _monitor(responses: list, scenario: str) -> dict:
    """The real WorkflowMonitor with the poll's interval captured, run by hand."""
    return _node(f"""
let pollTick = null, cleared = 0, fetches = 0;
const replies = {json.dumps(responses)};
globalThis.setInterval = (cb) => {{ pollTick = cb; return 7; }};
globalThis.clearInterval = (id) => {{ if (id === 7) cleared++; }};
globalThis.setTimeout = () => 0; globalThis.clearTimeout = () => {{}};
globalThis.WebSocket = class {{ close() {{}} }};
globalThis.window = {{ location: {{ protocol: 'http:', host: 'h', origin: 'http://h' }} }};
globalThis.document = {{ hidden: false, addEventListener() {{}} }};
globalThis.fetch = async () => {{ fetches++; const r = replies.shift();
  return {{ ok: true, json: async () => r, text: async () => '' }}; }};
console.log = () => {{}}; console.info = () => {{}}; console.debug = () => {{}};
const WorkflowMonitor = require({json.dumps(str(MONITOR))});
const progress = [], completions = [];
const mon = new WorkflowMonitor('0b9c5a51-0000-0000-0000-000000000000', {{
  onProgress: (p) => progress.push(p), onComplete: (d) => completions.push(d) }});
(async () => {{
  {scenario}
  process.stdout.write(JSON.stringify({{ progress, completions: completions.length,
    fetches, cleared, polling: mon.pollTimer !== null }}));
}})();
""")


def test_the_poll_moves_the_bar_while_the_job_runs():
    state = _monitor(
        [
            {"status": "running", "progress_percentage": 42},
            {"status": "running", "progress_percentage": 47},
        ],
        "await pollTick(); await pollTick();",
    )
    assert state["progress"] == [42, 47], state


def test_the_poll_leaves_completion_to_the_websocket():
    """/progress has no report links; the results panel needs them."""
    state = _monitor(
        [{"status": "completed", "progress_percentage": 100}], "await pollTick();"
    )
    assert state["progress"] == [] and state["completions"] == 0, state


def test_the_poll_stops_with_the_monitor_and_skips_a_hidden_tab():
    state = _monitor(
        [{"status": "running", "progress_percentage": 10}],
        """document.hidden = true; await pollTick();
        document.hidden = false; mon.disconnect(); await pollTick();""",
    )
    assert state["fetches"] == 0, state
    assert state["cleared"] >= 1 and not state["polling"], state
