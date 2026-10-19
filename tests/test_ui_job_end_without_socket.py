"""The page shows a job's end however it learns of it, once, with its report links.

The page heard a job end only from a WebSocket job_update saying so, and only one of
those (upload_router's last) carries the report links. So:
  - a job that ended while the socket was down or reconnecting was never shown as
    ended: the status sent on reconnecting and the progress poll ignored it, and the
    bar just stopped;
  - the tab-visible refresh, and any "completed" message but that one, opened
    "Analysis Complete!" with no links;
  - a "completed" message without links after the one with them redrew the card and
    wiped its links.

The real WorkflowMonitor (app/static/js/workflow-monitor.js) and the page's own
monitorProgress, showJobEnded, fetchReportLinks and showResults
(app/templates/index.html) run here in Node against a fake WebSocket, fetch, timers
and DOM.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "app" / "templates" / "index.html"
MONITOR = ROOT / "app" / "static" / "js" / "workflow-monitor.js"

JOB = "0b9c5a51-0000-4000-8000-00000000abcd"
PREFIX = f"/reports/p1/{JOB}"
# The job's reports as GET /reports/job/{id} returns them: generate_report's keys.
STORED_REPORTS = {
    "pdf_path": f"{PREFIX}/{JOB}_pgx_report.pdf",
    "html_path": f"{PREFIX}/{JOB}_pgx_report.html",
    "interactive_html_path": f"{PREFIX}/{JOB}_pgx_report_interactive.html",
    "pharmcat_html_path": f"{PREFIX}/{JOB}_pgx_pharmcat.html",
    "pharmcat_json_path": f"{PREFIX}/{JOB}_pgx_pharmcat.json",
    "pharmcat_tsv_path": f"{PREFIX}/{JOB}_pgx_pharmcat.tsv",
    "job_directory": f"/data{PREFIX}",
    "is_provisional": False,
}
STORED_LINKS = sorted(
    STORED_REPORTS[k]
    for k in (
        "pdf_path",
        "interactive_html_path",
        "pharmcat_html_path",
        "pharmcat_json_path",
        "pharmcat_tsv_path",
    )
)
# upload_router's last job_update, the only one with the links.
LINKED_MESSAGE = {
    "job_id": JOB,
    "status": "completed",
    "progress_percentage": 100,
    "current_step": "completed",
    "pdf_report_url": f"{PREFIX}/{JOB}_pgx_report.pdf",
    "html_report_url": f"{PREFIX}/{JOB}_pgx_report.html",
    "interactive_html_report_url": f"{PREFIX}/{JOB}_pgx_report_interactive.html",
    "pharmcat_html_report_url": f"{PREFIX}/{JOB}_pgx_pharmcat.html",
    "pharmcat_json_report_url": f"{PREFIX}/{JOB}_pgx_pharmcat.json",
    "pharmcat_tsv_report_url": f"{PREFIX}/{JOB}_pgx_pharmcat.tsv",
    "mtdna_report_url": None,
    "mtdna_haplogroups_url": None,
    "mtdna_vcf_url": None,
}


def status(state: str, progress: int = 100) -> dict:
    """A job status without links: job_update, initial_status or /progress."""
    return {
        "job_id": JOB,
        "status": state,
        "total_steps": 8,
        "completed_steps": 8 if state == "completed" else 5,
        "progress_percentage": progress,
        "current_step": "completed" if state == "completed" else "pypgx_analysis",
        "message": f"Job {state}",
    }


HARNESS = r"""
const input = JSON.parse(process.argv[1]);
const log = [];
const bar = [];
const showResultsCalls = [];
const fetched = [];
const sockets = [];
let timeouts = new Map(), nextTimeout = 1;
let pollTick = null, pollCleared = false, visibilityListener = null;

for (const name of ["log", "info", "warn", "debug", "error"]) console[name] = () => {};
globalThis.setTimeout = (cb) => { const id = nextTimeout++; timeouts.set(id, cb); return id; };
globalThis.clearTimeout = (id) => { timeouts.delete(id); };
globalThis.setInterval = (cb) => { pollTick = cb; return 7; };
globalThis.clearInterval = (id) => { if (id === 7) pollCleared = true; };
const fireTimeouts = () => { const due = [...timeouts.values()]; timeouts.clear(); due.forEach((cb) => cb()); };
const settle = async () => { for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r)); };

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
let reportsReply = input.reportsReply;
let releaseReports = null;
globalThis.fetch = async (url) => {
  fetched.push(url);
  if (url.endsWith("/progress")) {
    const body = progressReplies.shift();
    return {ok: true, status: 200, json: async () => body, text: async () => ""};
  }
  if (url.startsWith("/reports/job/")) {
    if (reportsReply === "hold") await new Promise((r) => { releaseReports = r; });
    const reply = reportsReply === "hold" ? input.heldReply : reportsReply;
    if (reply.network) throw new TypeError("Failed to fetch");
    return {ok: reply.status === 200, status: reply.status, json: async () => reply.body};
  }
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
    get innerHTML() { return this.html; },
    set innerHTML(v) { this.html = v; this.children = []; },
    append(...kids) { this.children.push(...kids); },
    replaceChildren(...kids) { this.children = kids; this.html = ""; },
    querySelector() { return null; },
  };
}
const elements = {};
globalThis.document = {
  hidden: false,
  getElementById: (id) => (elements[id] = elements[id] || element()),
  createElement: () => element(),
  addEventListener: (type, cb) => { if (type === "visibilitychange") visibilityListener = cb; },
};
globalThis.window = {
  location: {protocol: "http:", host: "zaropgx", origin: "http://zaropgx"},
  addLogMessage: (message, level) => log.push([message, level]),
  GlyphManager: {updateStageIndicators() {}, setConnectionState() {}},
  initializeSmoothProgress: () => ({
    updateProgress: (value, stage, message) => bar.push(["update", value]),
    stopAnimation: () => bar.push(["stop"]),
  }),
  // The FHIR push button (its own branch) makes showResults offer a push; its
  // card section is tested in tests/test_ui_fhir_push_button.py, not here.
  offerFhirPush() {},
};
const addLogMessage = window.addLogMessage;
const WorkflowMonitor = require(MONITOR_PATH);
let workflowMonitor = null;

SHOW_RESULTS;
const realShowResults = window.showResults;
window.showResults = (data) => { showResultsCalls.push(data); realShowResults(data); };
SHOW_JOB_ENDED;
FETCH_REPORT_LINKS;
MONITOR_PROGRESS;

const message = (data) => socket().receive("job_update", data);
const cardLinks = () => {
  const card = elements.analysisResults || element();
  return [...card.html.matchAll(/href="([^"]*)"/g)].map((m) => m[1]).sort();
};
const marks = {};
const mark = (name) => { marks[name] = cardLinks(); };

(async () => {
  window.monitorProgress(input.job);
  socket().open();
  socket().receive("initial_status", input.running);
  SCENARIO
  await settle();
  const card = elements.analysisResults || element();
  const text = (el) => [el.textContent, el.html, ...el.children.map(text)].join(" ");
  process.stdout.write(JSON.stringify({
    card: text(card),
    links: cardLinks(),
    marks,
    cardHidden: card.classList.contains("d-none"),
    showResults: showResultsCalls.length,
    status: (elements.progressStatus || element()).textContent,
    barClasses: [...(elements.progressBar || element()).classList.s],
    bar,
    log,
    fetched,
    sockets: sockets.length,
    pollStopped: pollCleared,
  }));
})().catch((e) => { process.stderr.write(String(e.stack || e)); process.exit(1); });
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


def run(
    scenario: str, progress=(), reports=STORED_REPORTS, reply=None, held=None
) -> dict:
    """Start monitoring a running job, then play `scenario` (JavaScript).

    `progress`: what /progress answers, in turn. GET /reports/job answers `reply`
    if given ("hold": `held`, once the scenario calls releaseReports()), else 200
    with `reports`.
    """
    script = (
        HARNESS.replace("MONITOR_PATH", json.dumps(str(MONITOR)))
        .replace("SHOW_RESULTS", _function_source("window.showResults = function("))
        .replace("SHOW_JOB_ENDED", _function_source("window.showJobEnded = function("))
        .replace(
            "FETCH_REPORT_LINKS",
            _function_source("window.fetchReportLinks = async function("),
        )
        .replace(
            "MONITOR_PROGRESS", _function_source("window.monitorProgress = function(")
        )
        .replace("SCENARIO", scenario)
    )
    ok = {"status": 200, "body": {"job_id": JOB, "status": "completed"}}
    ok["body"]["reports"] = reports
    payload = {
        "job": JOB,
        "running": status("running", 40),
        "progressReplies": list(progress),
        "reportsReply": reply or ok,
        "heldReply": held,
    }
    done = subprocess.run(
        ["node", "-e", script, json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def completed_once(shown: dict) -> None:
    """One completion: logged once, the bar finished and then left alone."""
    successes = [m for m, _ in shown["log"] if m == "Workflow completed successfully"]
    assert len(successes) == 1, shown["log"]
    assert "progress-bar-complete" in shown["barClasses"], shown["barClasses"]
    assert shown["bar"].index(["stop"]) == len(shown["bar"]) - 1, shown["bar"]
    assert "Analysis Complete!" in shown["card"]
    assert not shown["cardHidden"]


def failed_once(shown: dict) -> None:
    ended = [m for m, _ in shown["log"] if m.startswith("Workflow ended")]
    assert ended == ["Workflow ended: failed"], shown["log"]
    assert "Analysis Failed" in shown["card"], shown["card"]
    assert shown["showResults"] == 0
    assert shown["status"] == "Analysis failed"
    assert "bg-danger" in shown["barClasses"]
    assert shown["bar"][-1] == ["stop"], shown["bar"]
    assert not any(url.startswith("/reports/job/") for url in shown["fetched"])


DROP_AND_RECONNECT = """
  socket().drop();
  fireTimeouts();
  socket().open();
"""

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")


@needs_node
def test_a_job_that_completed_while_the_socket_was_down_is_shown_on_reconnect():
    shown = run(
        DROP_AND_RECONNECT
        + f"socket().receive('initial_status', {json.dumps(status('completed'))});"
    )

    assert shown["sockets"] == 2, "the monitor did not reconnect"
    completed_once(shown)
    assert shown["links"] == STORED_LINKS, shown["card"]
    assert shown["fetched"].count(f"/reports/job/{JOB}") == 1, shown["fetched"]
    assert shown["pollStopped"]


@needs_node
def test_a_job_that_failed_while_the_socket_was_down_is_shown_on_reconnect():
    shown = run(
        DROP_AND_RECONNECT
        + f"socket().receive('initial_status', {json.dumps(status('failed', 70))});"
    )

    failed_once(shown)
    assert shown["pollStopped"]


@needs_node
def test_the_poll_shows_a_job_that_completed_with_the_socket_down():
    shown = run(
        "socket().drop(); await pollTick();",
        progress=[status("completed")],
    )

    completed_once(shown)
    assert shown["links"] == STORED_LINKS, shown["card"]
    assert shown["pollStopped"]


@needs_node
def test_the_poll_shows_a_job_that_failed_with_the_socket_down():
    shown = run(
        "socket().drop(); await pollTick();",
        progress=[status("failed", 70)],
    )

    failed_once(shown)
    assert shown["pollStopped"]


@needs_node
def test_the_tab_visible_refresh_shows_a_completed_job_with_its_links():
    """/progress has no links; the card used to open on none."""
    shown = run(
        "visibilityListener();",
        progress=[status("completed")],
    )

    completed_once(shown)
    assert shown["links"] == STORED_LINKS, shown["card"]


@needs_node
def test_a_completed_message_without_links_then_the_one_with_them():
    shown = run(f"""
        message({json.dumps(status('completed'))});
        await settle();
        mark("before");
        message({json.dumps(LINKED_MESSAGE)});
        """)

    assert shown["marks"]["before"] == STORED_LINKS, "the card opened without links"
    completed_once(shown)
    assert shown["links"] == STORED_LINKS, shown["card"]
    assert shown["fetched"].count(f"/reports/job/{JOB}") == 1


@needs_node
def test_links_that_come_while_they_are_fetched_are_not_drawn_over():
    """The message with the links beats the fetch: the fetch's answer is dropped."""
    held = {
        "status": 200,
        "body": {"reports": {"pdf_path": f"{PREFIX}/stale.pdf"}},
    }
    shown = run(
        f"""
        message({json.dumps(status('completed'))});
        await settle();
        message({json.dumps(LINKED_MESSAGE)});
        if (releaseReports) releaseReports();
        """,
        reply="hold",
        held=held,
    )

    assert shown["fetched"].count(f"/reports/job/{JOB}") == 1, shown["fetched"]
    completed_once(shown)
    assert shown["showResults"] == 1, shown["showResults"]
    assert shown["links"] == STORED_LINKS, shown["card"]


@needs_node
def test_a_completed_message_without_links_after_the_one_with_them_keeps_them():
    shown = run(f"""
        message({json.dumps(LINKED_MESSAGE)});
        message({json.dumps(status('completed'))});
        """)

    completed_once(shown)
    assert shown["links"] == STORED_LINKS, shown["card"]
    assert shown["showResults"] == 1
    assert not any(url.startswith("/reports/job/") for url in shown["fetched"])


@needs_node
@pytest.mark.parametrize(
    "reply", [{"network": True}, {"status": 500, "body": {"detail": "boom"}}]
)
def test_links_that_cannot_be_loaded_are_said_so(reply):
    shown = run(f"message({json.dumps(status('completed'))});", reply=reply)

    completed_once(shown)
    assert shown["links"] == []
    assert "The report links could not be loaded" in shown["card"], shown["card"]
    assert f"data/reports/<patient_id>/{JOB}/" in shown["card"], shown["card"]
    assert any(
        m.startswith("Could not load the report links") and level == "warning"
        for m, level in shown["log"]
    ), shown["log"]


@needs_node
def test_report_links_in_the_endpoints_fallback_shape():
    """Without stored reports the endpoint builds *_url keys from the patient id;
    its html_report_url is the interactive report. A value that is not a report
    path is not put in a link."""
    fallback = {
        "pdf_report_url": f"{PREFIX}/{JOB}_pgx_report.pdf",
        "html_report_url": f"{PREFIX}/{JOB}_pgx_report_interactive.html",
        "pharmcat_tsv_report_url": f"{PREFIX}/{JOB}_pgx_pharmcat.tsv",
        "mtdna_vcf_url": 'javascript:alert(1)"',
    }
    shown = run(f"message({json.dumps(status('completed'))});", reports=fallback)

    completed_once(shown)
    assert shown["links"] == sorted(
        fallback[k]
        for k in ("pdf_report_url", "html_report_url", "pharmcat_tsv_report_url")
    ), shown["card"]
    assert "View Interactive HTML Report" in shown["card"]
    assert "javascript" not in shown["card"]


@needs_node
def test_a_second_failed_message_does_not_end_the_job_again():
    """Nor move the stopped bar, or replace "Analysis failed"."""
    shown = run(f"""
        message({json.dumps(status('failed', 70))});
        message({json.dumps(status('failed', 70))});
        """)

    failed_once(shown)
