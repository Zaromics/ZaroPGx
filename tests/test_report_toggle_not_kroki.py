"""The page's Report toggle does not follow the Kroki service.

initializeServiceToggles (app/templates/index.html) sets each stage's toggle from
/services-config, and mapped the Kroki service onto the Report stage. Kroki renders
Mermaid diagrams, nothing more; without it the report draws them with Graphviz
(compose.e2e.yml turns it off for that reason). So a deployment with
KROKI_ENABLED=false sent report_enabled=false with every upload made through the
page: no PDF, no interactive report, and a results card announcing "Analysis
Complete!" over no links. The API e2e test sends report_enabled itself, so only the
browser test, tests/e2e/test_ui_smoke.py, saw it, on its first CI run (2026-10-18).

The function runs here in Node as the page runs it, against a stubbed fetch and DOM.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"
STAGES = ("hla", "gatk", "pypgx", "mtdna", "report")

HARNESS = r"""
const config = JSON.parse(process.argv[1]);
const indicators = {};
for (const stage of STAGES) {
  const attrs = {"data-stage": stage, "data-enabled": "true"};
  const classes = () => ({add() {}, remove() {}});
  const button = {classList: classes(), textContent: ""};
  indicators[stage] = {
    classList: classes(),
    getAttribute: (k) => attrs[k],
    setAttribute: (k, v) => { attrs[k] = String(v); },
    querySelector: (sel) => (sel === ".toggle-button" ? button : null),
  };
}
const window = {};
const document = {
  querySelector(sel) {
    const m = /data-stage="([^"]+)"/.exec(sel);
    return m ? indicators[m[1]] || null : null;
  },
  getElementById: () => null,
};
const fetch = async () => ({ok: true, json: async () => config});
// Where the page reads /services-config through a shared loader instead.
window.getServicesConfig = async () => config;
const console = {
  log() {}, warn() {}, error: (...a) => process.stderr.write(a.join(" ")),
};
FUNCTION_SOURCE;
window.initializeServiceToggles().then(() => {
  const out = {};
  for (const stage of STAGES) {
    out[stage] = indicators[stage].getAttribute("data-enabled");
  }
  process.stdout.write(JSON.stringify(out));
});
"""


def _function_source() -> str:
    """The page's `window.initializeServiceToggles = async function() {...};`."""
    html = TEMPLATE.read_text(encoding="utf-8")
    start = html.index("window.initializeServiceToggles = async function()")
    depth, i = 0, html.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(html[i], 0)
        if depth == 0:
            return html[start : i + 1] + ";"
        i += 1


def _toggles(services: dict) -> dict:
    script = HARNESS.replace("STAGES", json.dumps(STAGES)).replace(
        "FUNCTION_SOURCE", _function_source()
    )
    config = {"services": {k: {"enabled": v} for k, v in services.items()}}
    done = subprocess.run(
        ["node", "-e", script, json.dumps(config)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


ALL_ON = {s: True for s in ("gatk", "pypgx", "optitype", "mtdna", "kroki")}


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_kroki_off_leaves_the_report_on():
    toggles = _toggles({**ALL_ON, "kroki": False})
    assert toggles["report"] == "true", toggles


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_a_disabled_service_still_turns_its_own_stage_off():
    toggles = _toggles({**ALL_ON, "mtdna": False, "optitype": False})
    assert toggles == {
        "hla": "false",
        "gatk": "true",
        "pypgx": "true",
        "mtdna": "false",
        "report": "true",
    }


def test_no_service_maps_onto_the_report_stage():
    mapping = re.search(
        r"const serviceMapping = \{(.*?)\};", _function_source(), re.DOTALL
    )
    assert mapping, "initializeServiceToggles has no serviceMapping"
    assert "'report'" not in mapping.group(1), mapping.group(1)
