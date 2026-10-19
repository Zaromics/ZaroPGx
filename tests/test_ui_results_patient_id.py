"""The results card finds the job's patient from any report link, not just the PDF's.

monitorProgress's onComplete took the patient id, which the "Download All Reports
as ZIP" button needs, from pdf_report_url alone. A job with the custom report off
has only PharmCAT's (and mtDNA's) links, so it lost the button. The page's own
monitorProgress runs here in Node against a stubbed monitor.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"

HARNESS = r"""
const data = JSON.parse(process.argv[1]);
const shown = [];
const element = () => ({classList: {add() {}, remove() {}}, textContent: ""});
const document = {getElementById: () => element()};
const window = {
  location: {protocol: "http:", host: "zaropgx", origin: "http://zaropgx"},
  addLogMessage() {},
  GlyphManager: {updateStageIndicators() {}, setConnectionState() {}},
  initializeSmoothProgress: () => ({updateProgress() {}, stopAnimation() {}}),
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
MONITOR_PROGRESS;
window.monitorProgress("job-1");
options.onComplete(data);
process.stdout.write(JSON.stringify(shown));
"""


def _function_source(marker: str) -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    start = html.index(marker)
    depth, i = 0, html.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(html[i], 0)
        if depth == 0:
            return html[start : i + 1] + ";"
        i += 1


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
@pytest.mark.parametrize(
    "links",
    [
        {"pdf_report_url": "/reports/patient-9/job-1/job-1_pgx_report.pdf"},
        {
            "pharmcat_html_report_url": "/reports/patient-9/job-1/job-1_pgx_pharmcat.html"
        },
        {"mtdna_vcf_url": "/reports/patient-9/job-1/mtdna_variants.vcf.gz"},
    ],
)
def test_the_patient_comes_from_whichever_report_link_there_is(links):
    script = HARNESS.replace(
        "MONITOR_PROGRESS", _function_source("window.monitorProgress = function(")
    )
    payload = {"job_id": "job-1", "status": "completed", **links}
    done = subprocess.run(
        ["node", "-e", script, json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    shown = json.loads(done.stdout)
    assert shown and shown[0]["patient_id"] == "patient-9", shown
