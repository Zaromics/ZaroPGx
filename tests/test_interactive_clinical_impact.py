"""The interactive report says only what a phenotype means.

Its gene cards and gene panels chose their sentence by matching words in the
phenotype. So SLCO1B1 (a transporter), G6PD, RYR1 and F5 were said to
"metabolize" drugs; every gene outside three written-up ones was "a gene involved
in drug metabolism"; and an unresolved call's "Normal Function or ... Poor
Function" matched "Normal": a call that may be Poor Function read "Standard drug
metabolism expected" and "should respond normally". Metabolism wording is now for
metabolizer phenotypes only, an unresolved call is called one, and a gene with no
description gets none.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html import unescape
from pathlib import Path

import pytest

import app.reports.generator as generator_module

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "app" / "static" / "js" / "pgx-report.js"
DATA = REPO / "test_data"

CALLS = r"""
const fs = require('fs');
const vm = require('vm');
const context = { document: { addEventListener() {} }, console };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
context.cases = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(vm.runInContext(`({
    implications: cases.implications.map(([g, p]) => clinicalImplication(g, p)),
    descriptions: cases.descriptions.map(geneDescription),
    shown: cases.shown.map(diplotypeShown),
})`, context)));
"""


def _run(cases):
    proc = subprocess.run(
        [shutil.which("node"), "-e", CALLS, str(SCRIPT)],
        input=json.dumps(cases),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_gene_panels_say_only_what_the_phenotype_means():
    out = _run(
        {
            "implications": [
                ["SLCO1B1", "Normal Function or Decreased Function or Poor Function"],
                ["SLCO1B1", "Normal Function"],
                ["G6PD", "Normal"],
                ["RYR1", "Uncertain Susceptibility"],
                ["CYP2C19", "Normal Metabolizer"],
                ["CYP2D6", "Poor Metabolizer"],
                ["CYP2C19", "Likely Intermediate Metabolizer"],
            ],
            "descriptions": ["CYP2D6", "RYR1", "F5"],
            "shown": [
                "*1/*14 or *1/*15 or *1/*20 or *1/*24",
                "*1/*3 or *1/*18 or *1/*68",
                "*1/*2",
            ],
        }
    )
    unresolved, transporter, g6pd, ryr1, normal, poor, intermediate = out[
        "implications"
    ]
    assert "could not narrow" in unresolved and "normal" not in unresolved.split(";")[0]
    for text in (unresolved, transporter, g6pd, ryr1):
        assert "metaboli" not in text, text
    assert "metabolized as usual" in normal
    assert "reduced" in poor and "moderately reduced" in intermediate
    assert out["descriptions"][0].startswith("Cytochrome P450 Family 2 Subfamily D")
    assert out["descriptions"][1:] == ["", ""]
    assert out["shown"] == [
        "*1/*14 or 3 other possible diplotypes",
        "*1/*3 or *1/*18 or *1/*68",
        "*1/*2",
    ]


def _cards(monkeypatch, tmp_path, name):
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_interactive_html"
            )
    result = generator_module.generate_report(
        {"data": json.loads((DATA / name).read_text(encoding="utf-8"))},
        str(tmp_path),
        {"id": "p1"},
    )
    page = (tmp_path / result["interactive_html_path"].split("/")[-1]).read_text(
        encoding="utf-8"
    )
    summary = page[page.index('<div id="summary"') : page.index('<div id="genes"')]
    return {
        gene: re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", body)))
        for gene, body in re.findall(
            r"<h3>\s*([\w-]+)\s*</h3>(.*?Clinical Impact:.*?)</p>", summary, re.S
        )
    }


def test_the_summary_cards_say_only_what_the_phenotype_means(monkeypatch, tmp_path):
    cards = _cards(monkeypatch, tmp_path, "pharmcat.example.v340.report.json")
    assert "Standard drug metabolism expected" in cards["CYP2C19"]
    assert "metaboli" not in cards["ABCG2"], cards["ABCG2"]  # Normal Function
    assert "medications affected by ABCG2" in cards["ABCG2"]
    assert "metaboli" not in cards["G6PD"], cards["G6PD"]


def test_an_unresolved_call_is_called_one(monkeypatch, tmp_path):
    cards = _cards(monkeypatch, tmp_path, "pharmcat.unphased.example.report.json")
    assert "could not narrow this call" in cards["SLCO1B1"]
    # All fifteen CYP2B6 candidates are Intermediate; still not one call.
    assert "could not narrow this call" in cards["CYP2B6"]
