"""The page checks the sample identifier before it uploads anything.

A genome's BAM uploaded for 13 minutes and was then refused with a 400 for a space in
its sample identifier ("Jane Doe"); the page said only "Upload failed!". Spaces now
become underscores, on the page and at the server (sample_identifier_from_form). What
is still refused, the page refuses before anything is sent, saying why under the
field; a refusal the server still sends is shown beside the red bar.

The page's GenomeDownloadProgress.js runs here in Node against a fake DOM and XHR.
"""

import json
import logging
import subprocess

import pytest
from fastapi import HTTPException

from app.api import db as db_module
from app.api.routes.upload_router import sample_identifier_from_form
from tests.test_ui_job_end_without_socket import ROOT, needs_node

SCRIPT = ROOT / "app" / "static" / "js" / "GenomeDownloadProgress.js"

HARNESS = r"""
const input = JSON.parse(process.argv[1]);
for (const name of ["log", "info", "warn", "debug", "error"]) console[name] = () => {};
const logged = [];
const xhrs = [];

class FakeClassList {
  constructor() { this.s = new Set(); }
  add(...c) { c.forEach((x) => this.s.add(x)); }
  remove(...c) { c.forEach((x) => this.s.delete(x)); }
  contains(c) { return this.s.has(c); }
}
const elements = new Map();
const element = (id) => {
  if (!elements.has(id)) {
    elements.set(id, {
      id, value: "", textContent: "", innerHTML: "", style: {}, disabled: false,
      classList: new FakeClassList(), listeners: {}, focused: false,
      parentNode: {appendChild() {}},
      addEventListener(type, cb) { this.listeners[type] = cb; },
      focus() { this.focused = true; },
      setAttribute(name, value) { this[name] = value; },
    });
  }
  return elements.get(id);
};
let ready = null;
globalThis.document = {
  getElementById: element,
  createElement: () => element("created-" + elements.size),
  addEventListener: (type, cb) => { if (type === "DOMContentLoaded") ready = cb; },
  dispatchEvent() {},
};
globalThis.Event = class { constructor(type) { this.type = type; } };
globalThis.window = {addLogMessage: (message, level) => logged.push([level, message])};
globalThis.FormData = class { constructor() { this.fields = []; } append(k, v) { this.fields.push([k, v]); } };
globalThis.XMLHttpRequest = class {
  constructor() { this.listeners = {}; this.upload = {addEventListener() {}}; this.sent = false; xhrs.push(this); }
  addEventListener(type, cb) { this.listeners[type] = cb; }
  open(method, url) { this.url = url; }
  send() { this.sent = true; }
};

eval(require("fs").readFileSync(input.script, "utf8") + "\nglobalThis.sampleIdentifierProblem = sampleIdentifierProblem;");

if (input.mode === "rule") {
  process.stdout.write(JSON.stringify(input.values.map((v) => sampleIdentifierProblem(v))) + "\n");
} else {
  ready();
  element("genomicFile").files = [{name: "sample.bam", size: 1024}];
  element("sampleIdentifier").value = input.sample;
  const stopped = element("uploadForm").onsubmit({preventDefault() {}, stopPropagation() {}}) === false;
  const xhr = xhrs[0];
  if (xhr && input.reply) {
    xhr.status = input.reply.status;
    xhr.responseText = JSON.stringify(input.reply.body);
    xhr.listeners.load();
  }
  if (input.edit) element("sampleIdentifier").listeners.input();
  process.stdout.write(JSON.stringify({
    stopped,
    sent: Boolean(xhr && xhr.sent),
    feedback: element("sampleIdentifierFeedback").textContent,
    invalid: element("sampleIdentifier").classList.contains("is-invalid"),
    focused: element("sampleIdentifier").focused,
    value: element("sampleIdentifier").value,
    status: element("upload-status").textContent,
    logged,
  }) + "\n");
}
"""


def _node(payload: dict):
    out = subprocess.run(
        ["node", "-e", HARNESS, json.dumps({"script": str(SCRIPT), **payload})],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


CASES = [
    "NA12878",
    "Sample_01",
    "HG002.GRCh38",
    "patient-123",
    "a" * 64,
    "a" * 65,
    "Jane Doe",
    "Jane   Doe",
    "Doe, Jane",
    "-leading-hyphen",
    "_leading",
    ".leading",
    "x;rm -rf",
    'quote"d',
    "tab\there",
    "ünïcode",
    "  padded  ",
]


@needs_node
def test_the_page_accepts_exactly_what_the_server_accepts():
    problems = _node({"mode": "rule", "values": CASES})
    for value, problem in zip(CASES, problems):
        try:
            sample_identifier_from_form(value)
            server_accepts = True
        except HTTPException:
            server_accepts = False
        assert (problem is None) == server_accepts, (value, problem)


@needs_node
def test_an_empty_identifier_is_left_to_the_server():
    # Optional: the server takes the VCF header's sample name, or none.
    assert _node({"mode": "rule", "values": ["", "   "]}) == [None, None]


@needs_node
def test_a_refused_identifier_stops_the_upload_before_anything_is_sent():
    seen = _node({"mode": "submit", "sample": "Doe, Jane"})
    assert seen["stopped"] and not seen["sent"]
    assert seen["invalid"] and seen["focused"]
    assert seen["feedback"].startswith('Sample identifier "Doe, Jane" is not accepted')
    assert "for example Doe_Jane" in seen["feedback"]
    assert seen["logged"] == [["danger", seen["feedback"]]]


@needs_node
def test_editing_the_identifier_clears_the_mark():
    seen = _node({"mode": "submit", "sample": "two;words", "edit": True})
    assert not seen["sent"] and not seen["invalid"]


@needs_node
@pytest.mark.parametrize("sample", ["NA12878", "", "  Sample_01 "])
def test_an_acceptable_or_empty_identifier_uploads(sample):
    seen = _node({"mode": "submit", "sample": sample})
    assert seen["sent"]
    assert not seen["invalid"] and seen["feedback"] == ""


@needs_node
def test_a_spaced_identifier_is_sent_with_underscores():
    seen = _node({"mode": "submit", "sample": " Jane  Doe "})
    assert seen["sent"] and not seen["invalid"]
    assert seen["value"] == "Jane_Doe"


@needs_node
def test_a_refusal_from_the_server_is_shown_beside_the_bar():
    detail = (
        "Invalid reference_genome: only letters, digits, dot, underscore and hyphen"
    )
    seen = _node(
        {
            "mode": "submit",
            "sample": "NA12878",
            "reply": {"status": 400, "body": {"detail": detail}},
        }
    )
    assert seen["status"] == f"Upload failed: {detail}"


class _Session:
    def __init__(self):
        self.rolled_back = self.closed = False

    def execute(self, _statement):
        return None

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_a_refusal_raised_through_get_db_is_not_logged_as_a_database_error(
    monkeypatch, caplog
):
    session = _Session()
    monkeypatch.setattr(db_module, "SessionLocal", lambda: session)
    dependency = db_module.get_db()
    next(dependency)
    with caplog.at_level(logging.ERROR), pytest.raises(HTTPException):
        dependency.throw(
            HTTPException(status_code=400, detail="Invalid sample_identifier")
        )
    assert "Database connection error" not in caplog.text
    assert session.rolled_back and session.closed


def test_a_real_failure_is_still_logged(monkeypatch, caplog):
    session = _Session()
    monkeypatch.setattr(db_module, "SessionLocal", lambda: session)
    dependency = db_module.get_db()
    next(dependency)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError):
        dependency.throw(RuntimeError("connection reset"))
    assert "Database connection error: connection reset" in caplog.text
