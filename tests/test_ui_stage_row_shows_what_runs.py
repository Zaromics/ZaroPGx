"""The stage row shows which stages will not run, and keeps showing it.

GlyphManager (app/templates/index.html) resets the whole row on every progress
update, through setStageState, which strips every state class. That took the fade
off a stage the user had switched off: it looked on for the rest of the job and
after it, while data-enabled="false" still left it out of the next upload. Found
2026-10-19 by switching mtDNA off and sending the page one update.

Stages a job has no step for (OptiType for a VCF, GATK for a GRCh38 VCF) were not
faded at all: they looked like stages still to come (docs/to-do.md, "grey-out
non-applicable steps"). loadJobSteps reads the job's steps and fades the rest. With a
job's steps shown, the job decides: a switch does not remove every step of its stage
(GATK off still lifts a GRCh37 VCF over), and a stage that runs must not look off.

The page's own GlyphManager runs here in Node, against fake stage elements.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"

STAGES = [
    "stageUpload",
    "stageAnalysis",
    "stageHLA",
    "stageMtDNA",
    "stageGATK",
    "stagePyPGx",
    "stagePharmcat",
    "stageReport",
]

# A GRCh38 VCF job's steps, as GET /upload/status/{job_id} lists them.
VCF_JOB_STEPS = [
    "header_analysis",
    "pypgx_analysis",
    "mtdna_analysis",
    "pharmcat_analysis",
    "report_generation",
]

HARNESS = r"""
const scenario = JSON.parse(process.argv[1]);
class FakeClassList {
  constructor() { this.s = new Set(["stage-indicator", "text-muted"]); }
  add(...c) { c.forEach((x) => this.s.add(x)); }
  remove(...c) { c.forEach((x) => this.s.delete(x)); }
  contains(c) { return this.s.has(c); }
}
const elements = {};
for (const id of scenario.stages) {
  const attrs = {"data-enabled": "true"};
  elements[id] = {
    id, classList: new FakeClassList(),
    getAttribute: (name) => (name in attrs ? attrs[name] : null),
    setAttribute: (name, value) => { attrs[name] = String(value); },
  };
}
const document = {getElementById: (id) => elements[id] || null};
const fetched = [];
const fetch = (url) => {
  fetched.push(url);
  if (scenario.fetchFails) return Promise.reject(new Error("offline"));
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve({data: {steps: scenario.steps.map((name) => ({name}))}}),
  });
};
const window = {};
const console = {log() {}, info() {}, warn() {}, debug() {}, error() {}};

GLYPH_MANAGER;

const faded = () => scenario.stages.filter((id) => elements[id].classList.contains("disabled"));
const snapshots = {};
(async () => {
  const gm = window.GlyphManager;
  for (const id of scenario.switchedOff || []) {
    elements[id].setAttribute("data-enabled", "false");
    elements[id].classList.add("disabled");
  }
  if (scenario.load) {
    const pending = gm.loadJobSteps("job-1");
    // A new file chosen before the job's steps came back.
    if (scenario.interrupt) gm.clearJobSteps();
    await pending;
  }
  snapshots.start = faded();
  const update = scenario.update || "pharmcat_analysis";
  gm.updateStageIndicators(update, "running");
  snapshots.afterUpdate = faded();
  const updated = elements[gm.stepMapping[update]];
  snapshots.running = updated.classList.contains("text-success");
  if (scenario.toggleOn) {
    elements[scenario.toggleOn].setAttribute("data-enabled", "true");
    gm.refreshFade(elements[scenario.toggleOn]);
    snapshots.afterToggle = faded();
  }
  gm.clearJobSteps();
  snapshots.afterClear = faded();
  process.stdout.write(JSON.stringify({snapshots, fetched}));
})();
"""


def _glyph_manager_source() -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    start = html.index("window.GlyphManager = {")
    depth, i = 0, html.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(html[i], 0)
        if depth == 0:
            return html[start : i + 1] + ";"
        i += 1


def _run(**scenario) -> dict:
    scenario.setdefault("stages", STAGES)
    scenario.setdefault("steps", VCF_JOB_STEPS)
    script = HARNESS.replace("GLYPH_MANAGER", _glyph_manager_source())
    done = subprocess.run(
        ["node", "-e", script, json.dumps(scenario)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")


@needs_node
def test_a_switched_off_stage_stays_faded_through_an_update():
    snapshots = _run(switchedOff=["stageMtDNA"], load=False)["snapshots"]

    assert snapshots["start"] == ["stageMtDNA"]
    assert snapshots["afterUpdate"] == ["stageMtDNA"], snapshots
    assert snapshots["running"]
    assert snapshots["afterClear"] == ["stageMtDNA"]


@needs_node
def test_the_stages_a_job_has_no_step_for_are_faded_until_the_next_file():
    result = _run(load=True)
    snapshots = result["snapshots"]

    assert result["fetched"] == ["/upload/status/job-1"]
    assert snapshots["start"] == ["stageHLA", "stageGATK"]
    assert snapshots["afterUpdate"] == ["stageHLA", "stageGATK"], snapshots
    assert snapshots["running"], "the running stage is still marked"
    assert snapshots["afterClear"] == []


@needs_node
def test_a_switched_off_stage_outlasts_the_jobs_fades():
    """mtDNA switched off: the job has no mtdna step, and clearing the job's fades
    must not switch it back on."""
    steps = [s for s in VCF_JOB_STEPS if s != "mtdna_analysis"]
    snapshots = _run(switchedOff=["stageMtDNA"], steps=steps, load=True)["snapshots"]

    assert snapshots["afterUpdate"] == ["stageHLA", "stageMtDNA", "stageGATK"]
    assert snapshots["afterClear"] == ["stageMtDNA"]


@needs_node
def test_steps_that_cannot_be_read_fade_nothing():
    snapshots = _run(load=True, fetchFails=True)["snapshots"]

    assert snapshots["start"] == []
    assert snapshots["afterUpdate"] == []


@needs_node
def test_an_alignment_job_fades_nothing_it_runs():
    """A BAM job runs every stage: none is faded."""
    steps = [
        "header_analysis",
        "hla_typing",
        "pypgx_bam2vcf",
        "pypgx_analysis",
        "mtdna_analysis",
        "pharmcat_analysis",
        "report_generation",
    ]
    snapshots = _run(load=True, steps=steps)["snapshots"]

    # GATK: a BAM needs no conversion, so its stage is left out.
    assert snapshots["start"] == ["stageGATK"]


@needs_node
def test_a_switched_off_stage_that_still_runs_is_not_faded():
    """GATK off on a GRCh37 VCF: the liftover still runs, under the GATK stage."""
    steps = ["header_analysis", "liftover"] + VCF_JOB_STEPS[1:]
    snapshots = _run(
        switchedOff=["stageGATK"], steps=steps, load=True, update="liftover"
    )["snapshots"]

    assert snapshots["start"] == ["stageHLA"]
    assert snapshots["afterUpdate"] == ["stageHLA"], snapshots
    assert snapshots["running"]
    # A new file: the switch says again.
    assert snapshots["afterClear"] == ["stageGATK"]


@needs_node
def test_a_reply_for_a_job_no_longer_shown_is_dropped():
    snapshots = _run(load=True, interrupt=True)["snapshots"]

    assert snapshots["start"] == []
    assert snapshots["afterUpdate"] == []


@needs_node
def test_switching_a_stage_on_does_not_unfade_what_the_job_leaves_out():
    snapshots = _run(switchedOff=["stageHLA"], load=True, toggleOn="stageHLA")[
        "snapshots"
    ]

    assert snapshots["afterToggle"] == ["stageHLA", "stageGATK"]
    # No job shown, HLA switched back on: nothing is faded.
    assert snapshots["afterClear"] == []
