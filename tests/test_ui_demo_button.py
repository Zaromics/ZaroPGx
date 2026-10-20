"""Run Demo comes back when the demo's job ends, however it ends.

runDemo turned Run Demo (and Upload) off and turned them back on only when the demo
could not start. A demo that completed, failed or was cancelled left both off, so a
second demo needed a page reload.

The page's own runDemo, monitorProgress, showJobEnded, showResults, fetchReportLinks
and cancelJobMonitoring (app/templates/index.html) and the real WorkflowMonitor
(app/static/js/workflow-monitor.js) run here in Node against a fake WebSocket, fetch,
timers and DOM.
"""

import json
import subprocess

import pytest

from tests.test_ui_job_end_without_socket import (
    JOB,
    MONITOR,
    STORED_REPORTS,
    TEMPLATE,
    _function_source,
    needs_node,
    status,
)

HARNESS = r"""
const input = JSON.parse(process.argv[1]);
const log = [];
const fetched = [];
const sockets = [];
let timeouts = new Map(), nextTimeout = 1;
let pollTick = null;

for (const name of ["log", "info", "warn", "debug", "error"]) console[name] = () => {};
globalThis.setTimeout = (cb) => { const id = nextTimeout++; timeouts.set(id, cb); return id; };
globalThis.clearTimeout = (id) => { timeouts.delete(id); };
globalThis.setInterval = (cb) => { pollTick = cb; return 7; };
globalThis.clearInterval = () => {};
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); };
// The timers due, then (rounds > 1) the ones those start: the cancel's animation
// nests two.
const fireTimeouts = async (rounds = 1) => {
  for (let round = 0; round < rounds && timeouts.size; round++) {
    const due = [...timeouts.values()]; timeouts.clear();
    due.forEach((cb) => cb());
    await settle();
  }
};

class FakeSocket {
  constructor(url) { this.url = url; this.readyState = 0; sockets.push(this); }
  addEventListener() {}
  send() {}
  close() { this.readyState = 3; }
  open() { this.readyState = 1; this.onopen(); }
  receive(type, data) { this.onmessage({data: JSON.stringify({type, data})}); }
  drop() { this.readyState = 3; this.onclose({code: 1006, reason: ""}); }
}
FakeSocket.OPEN = 1;
globalThis.WebSocket = FakeSocket;
const socket = () => sockets[sockets.length - 1];

const progressReplies = [...input.progressReplies];
const reply = (status, body) => ({ok: status === 200, status, json: async () => body, text: async () => ""});
globalThis.fetch = async (url, options) => {
  fetched.push(url);
  if (url === "/upload/demo") return reply(input.demoStatus, {job_id: input.job, detail: "no demo file"});
  if (url.endsWith("/progress")) return reply(200, progressReplies.shift());
  if (url.startsWith("/reports/job/")) return reply(200, {job_id: input.job, reports: input.reports});
  if (url.endsWith("/cancel")) return reply(200, {});
  throw new Error("unexpected fetch " + url);
};

class FakeClassList {
  constructor() { this.s = new Set(); }
  add(...c) { c.forEach((x) => this.s.add(x)); }
  remove(...c) { c.forEach((x) => this.s.delete(x)); }
  contains(c) { return this.s.has(c); }
}
function element() {
  return {
    classList: new FakeClassList(), textContent: "", className: "", children: [], html: "",
    disabled: false, style: {},
    get innerHTML() { return this.html; },
    set innerHTML(v) { this.html = v; this.children = []; },
    append(...kids) { this.children.push(...kids); },
    replaceChildren(...kids) { this.children = kids; this.html = ""; },
    querySelector() { return null; },
    setAttribute() {},
  };
}
const elements = {};
globalThis.document = {
  hidden: false,
  getElementById: (id) => (elements[id] = elements[id] || element()),
  createElement: () => element(),
  querySelector: () => null,
  addEventListener: () => {},
};
globalThis.window = {
  location: {protocol: "http:", host: "zaropgx", origin: "http://zaropgx"},
  addLogMessage: (message, level) => log.push([message, level]),
  GlyphManager: {updateStageIndicators() {}, setConnectionState() {}},
  initializeSmoothProgress: () => ({updateProgress() {}, stopAnimation() {}}),
  offerFhirPush() {},
};
const addLogMessage = window.addLogMessage;
const WorkflowMonitor = require(MONITOR_PATH);
let workflowMonitor = null;

DEMO_BUTTONS;
RUN_DEMO;
SHOW_RESULTS;
SHOW_JOB_ENDED;
FETCH_REPORT_LINKS;
MONITOR_PROGRESS;
REVERSE_BAR;
CANCEL_STAGES;
CANCEL_MONITORING;

const buttons = () => ({
  demo: elements.demoButton.disabled ? "off" : "on",
  upload: elements.uploadButton.disabled ? "off" : "on",
});
// What a click on Run Demo does: nothing while the button is off.
const click = async () => { if (!elements.demoButton.disabled) await runDemo(); await settle(); };
const message = (data) => socket().receive("job_update", data);
const marks = {};

(async () => {
  ["logMessages", "progressContainer", "demoButton", "uploadButton"].forEach((id) => document.getElementById(id));
  await click();
  marks.started = buttons();
  await fireTimeouts();  // runDemo starts monitoring the job a second later
  if (socket()) {
    socket().open();
    socket().receive("initial_status", input.running);
    await click();
    marks.running = buttons();
  }
  SCENARIO
  await settle();
  marks.ended = buttons();
  await click();
  process.stdout.write(JSON.stringify({
    marks,
    demos: fetched.filter((url) => url === "/upload/demo").length,
    log,
  }));
})().catch((e) => { process.stderr.write(String(e.stack || e)); process.exit(1); });
"""


def _source(marker: str) -> str:
    """The page's code at `marker`, or nothing where the page has none."""
    if marker not in TEMPLATE.read_text(encoding="utf-8"):
        return ""
    return _function_source(marker)


def run(scenario: str, progress=(), demo_status=200) -> dict:
    """Click Run Demo, let its job start, then play `scenario` (JavaScript)."""
    sources = {
        "DEMO_BUTTONS": _source("window.releaseDemoButtons = function("),
        "RUN_DEMO": _source("async function runDemo() {"),
        "SHOW_RESULTS": _source("window.showResults = function("),
        "SHOW_JOB_ENDED": _source("window.showJobEnded = function("),
        "FETCH_REPORT_LINKS": _source("window.fetchReportLinks = async function("),
        "MONITOR_PROGRESS": _source("window.monitorProgress = function("),
        "REVERSE_BAR": _source("window.animateProgressBarReverse = function("),
        "CANCEL_STAGES": _source(
            "window.updateStageIndicatorsForCancellation = function("
        ),
        "CANCEL_MONITORING": _source("window.cancelJobMonitoring = async function("),
    }
    script = HARNESS.replace("MONITOR_PATH", json.dumps(str(MONITOR)))
    for name, source in sources.items():
        script = script.replace(name, source)
    script = script.replace("SCENARIO", scenario)
    payload = {
        "job": JOB,
        "running": status("running", 40),
        "progressReplies": list(progress),
        "reports": STORED_REPORTS,
        "demoStatus": demo_status,
    }
    done = subprocess.run(
        ["node", "-e", script, json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


OFF = {"demo": "off", "upload": "off"}
ON = {"demo": "on", "upload": "on"}


def ended_and_back(shown: dict) -> None:
    """Off while the demo ran, on once its job ended, and a click starts another."""
    assert shown["marks"]["started"] == OFF, shown["marks"]
    assert shown["marks"]["running"] == OFF, shown["marks"]
    assert shown["marks"]["ended"] == ON, shown["marks"]
    assert shown["demos"] == 2, shown["demos"]


@needs_node
@pytest.mark.parametrize("state", ["completed", "failed", "cancelled"])
def test_run_demo_comes_back_when_its_job_ends(state):
    shown = run(
        f"message({json.dumps(status(state, 100 if state == 'completed' else 70))});"
    )

    ended_and_back(shown)


@needs_node
def test_run_demo_comes_back_when_the_poll_sees_the_end():
    """The socket is down when the job completes: the progress poll tells."""
    shown = run("socket().drop(); await pollTick();", progress=[status("completed")])

    ended_and_back(shown)


@needs_node
def test_run_demo_comes_back_when_its_monitoring_is_cancelled():
    shown = run("window.cancelJobMonitoring(); await settle(); await fireTimeouts(3);")

    assert any(m == "Job monitoring cancelled" for m, _ in shown["log"]), shown["log"]
    ended_and_back(shown)


@needs_node
def test_a_second_click_while_the_demo_runs_starts_nothing():
    shown = run("await click(); await runDemo();")

    assert shown["marks"]["running"] == OFF, shown["marks"]
    # The job never ended: still off, and only the first click reached the server.
    assert shown["marks"]["ended"] == OFF, shown["marks"]
    assert shown["demos"] == 1, shown["demos"]


@needs_node
def test_run_demo_comes_back_when_the_demo_cannot_start():
    shown = run("", demo_status=500)

    assert shown["marks"]["started"] == ON, shown["marks"]
    assert any(m.startswith("Error running demo") for m, _ in shown["log"]), shown[
        "log"
    ]
    assert shown["demos"] == 2, shown["demos"]
