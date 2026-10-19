"""A failed or cancelled job is not shown as "Analysis Complete!".

WorkflowMonitor (app/static/js/workflow-monitor.js) calls its onComplete callback
however a job ends: completed, failed or cancelled. monitorProgress's onComplete
(app/templates/index.html) never looked at the status, so a failed job drove the
bar to 100%, turned it green, logged "Workflow completed successfully" and opened
the results card on "Analysis Complete! The pharmacogenomic analysis has been
completed successfully." Spotted while adding the FHIR push button (2026-10-19).

The page's own monitorProgress and showJobEnded run here in Node, against a stubbed
WorkflowMonitor and DOM; the test calls the onComplete monitorProgress hands it.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"

HARNESS = r"""
const data = JSON.parse(process.argv[1]);
const log = [];
const glyphs = [];
const shown = [];
const progress = {updates: [], stopped: false};

class FakeClassList {
  constructor() { this.s = new Set(); }
  add(...c) { c.forEach((x) => this.s.add(x)); }
  remove(...c) { c.forEach((x) => this.s.delete(x)); }
  contains(c) { return this.s.has(c); }
}
function element() {
  return {
    classList: new FakeClassList(), textContent: "", className: "", children: [],
    append(...kids) { this.children.push(...kids); },
    replaceChildren(...kids) { this.children = kids; },
  };
}
const elements = {};
const document = {
  getElementById: (id) => (elements[id] = elements[id] || element()),
  createElement: () => element(),
};
const window = {
  location: {protocol: "http:", host: "zaropgx", origin: "http://zaropgx"},
  addLogMessage: (message, level) => log.push([message, level]),
  GlyphManager: {
    updateStageIndicators: (step, status) => glyphs.push([step, status]),
    setConnectionState() {},
  },
  initializeSmoothProgress: () => ({
    updateProgress: (value) => progress.updates.push(value),
    stopAnimation: () => { progress.stopped = true; },
  }),
  showResults: (results) => shown.push(results),
};
let options = null;
class WorkflowMonitor {
  constructor(jobId, opts) { options = opts; this.websocket = {addEventListener() {}}; }
  connect() { return Promise.resolve(); }
  disconnect() {}
}
let workflowMonitor = null;
const console = {log() {}, info() {}, warn() {}, debug() {}, error() {}};

SHOW_JOB_ENDED;
MONITOR_PROGRESS;

window.monitorProgress("job-1");
options.onComplete(data);
const card = elements.analysisResults || element();
const text = (el) => [el.textContent, ...el.children.map(text)].join(" ");
process.stdout.write(JSON.stringify({
  shown,
  card: text(card),
  cardHidden: card.classList.contains("d-none"),
  barClasses: [...(elements.progressBar || element()).classList.s],
  status: (elements.progressStatus || element()).textContent,
  progress,
  glyphs,
  log,
}));
"""


def _function_source(marker: str) -> str:
    """`window.<name> = function(...) {...};` as the page has it."""
    html = TEMPLATE.read_text(encoding="utf-8")
    start = html.index(marker)
    depth, i = 0, html.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(html[i], 0)
        if depth == 0:
            return html[start : i + 1] + ";"
        i += 1


def _end_job(status: str) -> dict:
    script = HARNESS.replace(
        "SHOW_JOB_ENDED", _function_source("window.showJobEnded = function(")
    ).replace(
        "MONITOR_PROGRESS", _function_source("window.monitorProgress = function(")
    )
    payload = {
        "job_id": "job-1",
        "status": status,
        "pdf_report_url": "/reports/p/job-1/job-1_pgx_report.pdf",
    }
    done = subprocess.run(
        ["node", "-e", script, json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")


@needs_node
def test_a_failed_job_is_shown_failed():
    ended = _end_job("failed")

    assert not ended["shown"], "a failed job opened the results card"
    assert "Analysis Failed" in ended["card"], ended["card"]
    assert "Complete" not in ended["card"]
    assert not ended["cardHidden"]
    assert "bg-danger" in ended["barClasses"], ended["barClasses"]
    assert "progress-bar-complete" not in ended["barClasses"]
    assert 100 not in ended["progress"]["updates"]
    assert ended["progress"]["stopped"]
    assert ended["status"] == "Analysis failed"
    # The stage row is onProgress's to mark, from the same message: a call here
    # resets the whole row first, and with no step to match it erased the mark.
    assert not ended["glyphs"], ended["glyphs"]
    assert not any("successfully" in message for message, _ in ended["log"])


@needs_node
def test_a_cancelled_job_is_shown_cancelled():
    ended = _end_job("cancelled")

    assert not ended["shown"]
    assert "Analysis Cancelled" in ended["card"], ended["card"]
    assert "bg-secondary" in ended["barClasses"]
    assert ended["status"] == "Analysis cancelled"
    assert not ended["glyphs"], ended["glyphs"]


@needs_node
def test_a_completed_job_still_shows_its_results():
    ended = _end_job("completed")

    assert len(ended["shown"]) == 1
    assert ended["shown"][0]["pdf_report_url"].endswith("_pgx_report.pdf")
    assert "progress-bar-complete" in ended["barClasses"]
    assert 100 in ended["progress"]["updates"]


PROGRESS_HARNESS = r"""
const classes = new Set(JSON.parse(process.argv[1]));
const progressBar = {
  style: {}, textContent: "", setAttribute() {},
  classList: {
    add: (...c) => c.forEach((x) => classes.add(x)),
    remove: (...c) => c.forEach((x) => classes.delete(x)),
    contains: (c) => classes.has(c),
  },
};
const requestAnimationFrame = () => 1;
const cancelAnimationFrame = () => {};
const console = {log() {}, info() {}, warn() {}, debug() {}, error() {}};
SMOOTH_PROGRESS_MANAGER
const manager = new SmoothProgressManager(progressBar, {textContent: ""});
manager.pendingStageUpdate = {targetProgress: 40, stage: "pypgx", message: "queued"};
manager.stopAnimation();
const dropped = manager.pendingStageUpdate === null;
manager.reset();
process.stdout.write(JSON.stringify({classes: [...classes], dropped}));
"""


def _progress_after(classes: list) -> dict:
    """The bar's classes after stopAnimation() then reset(), and whether the
    stop dropped a queued stage update."""
    source = _function_source("class SmoothProgressManager {")[:-1]
    script = PROGRESS_HARNESS.replace("SMOOTH_PROGRESS_MANAGER", source)
    done = subprocess.run(
        ["node", "-e", script, json.dumps(classes)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_node
@pytest.mark.parametrize("ended_colour", ["bg-danger", "bg-secondary"])
def test_the_next_run_starts_blue_and_striped_again(ended_colour):
    """Bootstrap's bg-danger outranks bg-info: left on, the next job's whole run
    was red."""
    after = _progress_after(["progress-bar", ended_colour])

    assert ended_colour not in after["classes"], after
    assert {"bg-info", "progress-bar-striped", "progress-bar-animated"} <= set(
        after["classes"]
    ), after


@needs_node
def test_stopping_the_bar_drops_a_queued_stage_update():
    """A stage update waiting out minStageDuration fired after the job ended,
    moved the bar and replaced "Analysis failed" with the server's message."""
    assert _progress_after(["progress-bar", "bg-info"])["dropped"]
