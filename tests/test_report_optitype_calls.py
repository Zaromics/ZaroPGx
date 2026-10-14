"""OptiType's HLA calls reach the report: HLA-C included, and credited to OptiType.
MT-RNR1 is credited to mtDNA-Server 2 the same way.

Found on a sliced NA12878 BAM (public, GIAB) on 2026-10-14. OptiType typed HLA-A,
HLA-B and HLA-C and zarohla returned all three, but the report took HLA calls only
from PharmCAT, which received them as outside calls. PharmCAT has no HLA-C, so the
gene table listed HLA-C among the genes examined without a call ("HLA-C (–)") and
the workflow diagram drew "HLA-C: none". PharmCAT's HLA-A and HLA-B rows record only
that the call came from outside, so their Call column read "Outside call - producing
tool not recorded by this run" though this run's OptiType had made them.

generator.merge_optitype_calls reads the run's hla_result.json (zarohla's /call-hla
response, which Nextflow publishes into the job's report folder).

The same report's MT-RNR1 row read "X", producing tool not recorded: mtDNA-Server 2
made the call (mtdna_result.json's mt_rnr1) and handed it to PharmCAT the same way.
generator.credit_mtdna_call credits it.
"""

from __future__ import annotations

import json
import re
from html import unescape

import app.reports.generator as generator_module
from app.reports.generator import (
    _build_canonical_diplotypes,
    credit_mtdna_call,
    merge_optitype_calls,
    optitype_diplotype,
)

# NA12878's class I types, as zarohla returned them for the sliced BAM.
HLA_RESULT = {
    "status": "success",
    "results": {
        "HLA-A": "A*01:01,A*11:01",
        "HLA-B": "B*08:01,B*56:01",
        "HLA-C": "C*01:02,C*07:01",
    },
}


def _outside(gene, diplotype):
    """PharmCAT's row for an HLA outside call."""
    return {
        "gene": gene,
        "diplotype": diplotype,
        "phenotype": "*57:01 negative",
        "call_source": "OUTSIDE",
    }


def _folder(tmp_path, result=HLA_RESULT):
    (tmp_path / "hla_result.json").write_text(json.dumps(result), encoding="utf-8")
    return str(tmp_path)


def test_the_pair_is_written_as_pharmcat_writes_hla_calls():
    assert optitype_diplotype("C*01:02,C*07:01") == "*01:02/*07:01"
    assert optitype_diplotype("A*02:01, A*02:01") == "*02:01/*02:01"
    assert optitype_diplotype("") == ""
    assert optitype_diplotype(None) == ""


def test_hla_c_is_added_and_matching_outside_calls_are_optitypes(tmp_path):
    genes = [
        _outside("HLA-A", "*01:01/*11:01"),
        # A manual outside call that is not what OptiType typed stays unattributed.
        _outside("HLA-B", "*57:01/*08:01"),
    ]
    assert merge_optitype_calls(genes, _folder(tmp_path)) == 1
    rows = {g["gene"]: g for g in genes}
    assert rows["HLA-A"]["tool_source"] == "OptiType"
    assert "tool_source" not in rows["HLA-B"]
    assert rows["HLA-C"]["diplotype"] == "*01:02/*07:01"
    assert rows["HLA-C"]["tool_source"] == "OptiType"


def test_a_run_without_hla_typing_changes_nothing(tmp_path):
    genes = [_outside("HLA-A", "*01:01/*11:01")]
    assert merge_optitype_calls(genes, str(tmp_path)) == 0
    (tmp_path / "hla_result.json").write_text("{not json", encoding="utf-8")
    assert merge_optitype_calls(genes, str(tmp_path)) == 0
    # zarohla's "not typed" result (HLA reads not located): no results at all.
    _folder(tmp_path, {"results": {}, "reason": "hla_reads_not_located"})
    assert merge_optitype_calls(genes, str(tmp_path)) == 0
    assert genes == [_outside("HLA-A", "*01:01/*11:01")]


def test_the_gene_table_rows_say_optitype_called_them(tmp_path):
    genes = [_outside("HLA-A", "*01:01/*11:01"), _outside("HLA-B", "*08:01/*56:01")]
    merge_optitype_calls(genes, _folder(tmp_path))
    rows = {
        r["gene"]: r
        for r in _build_canonical_diplotypes(genes, "bam", workflow_config={})
    }
    for gene in ("HLA-A", "HLA-B", "HLA-C"):
        assert rows[gene]["called_by"] == "O", gene
        assert rows[gene]["called_by_label"] == "Called by OptiType"
    assert rows["HLA-C"]["diplotype"] == "*01:02/*07:01"
    # OptiType types alleles and gives no phenotype: N/A, as PharmCAT writes it.
    assert rows["HLA-C"]["phenotype"] == "N/A"


def _clean(fragment):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def test_the_report_lists_hla_c_as_called(monkeypatch, tmp_path):
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    _folder(tmp_path)
    data = {
        "genes": [
            _outside("HLA-A", "*01:01/*11:01"),
            _outside("HLA-B", "*08:01/*56:01"),
        ],
        "drugRecommendations": [],
    }
    result = generator_module.generate_report(
        {"data": data}, str(tmp_path), {"id": "p1"}, job_id=None
    )
    page = (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")
    genes = page[page.index("<h2>Genetic Results</h2>") :]
    uncalled = _clean(genes[genes.index("Genes examined without a call") :])
    assert "HLA-C" not in uncalled.split("Drugs")[0]
    hla_c = genes[genes.index(">HLA-C<") :][:2000]
    assert "*01:02/*07:01" in _clean(hla_c)
    assert 'title="Called by OptiType"' in hla_c
    assert "Outside call - producing tool not recorded" not in genes


def test_mt_rnr1_is_credited_to_mtdna_server_when_it_is_the_runs_call(tmp_path):
    (tmp_path / "mtdna_result.json").write_text(
        json.dumps({"haplogroup": "H13a1a1a", "mt_rnr1": "Reference"}),
        encoding="utf-8",
    )
    row = _outside("MT-RNR1", "Reference")
    assert credit_mtdna_call([row], str(tmp_path))
    rows = _build_canonical_diplotypes([row], "bam", workflow_config={})
    (out,) = [r for r in rows if r["gene"] == "MT-RNR1"]
    assert (out["called_by"], out["called_by_label"]) == (
        "M",
        "Called by mtDNA-server-2",
    )
    # A manual outside call that differs from the run's stays unattributed.
    manual = _outside("MT-RNR1", "m.1555A>G")
    assert not credit_mtdna_call([manual], str(tmp_path))
    assert "tool_source" not in manual
    # No mtDNA step: nothing to credit.
    assert not credit_mtdna_call(
        [_outside("MT-RNR1", "Reference")], str(tmp_path / "x")
    )
