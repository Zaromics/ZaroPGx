"""PYPGX_PHARMCAT_PREFERENCE: the genes PyPGx genotypes, and the calls PharmCAT gets.

Compose's default was `pharmcat`, so PyPGx never genotyped PharmCAT's genes, even from
a whole-genome BAM, and nothing measured CYP2B6's or CYP4F2's copy number: the report
said "Copy number not assessed" beside both. The default is now `auto`: from reads
(BAM, CRAM, SAM, FASTQ) PyPGx genotypes PharmCAT's genes too, for their copy number;
from a VCF, BCF or gVCF it does not. PharmCAT takes an outside call over its own call
of a gene, so under `auto` its genes' PyPGx calls do not go to it as outside calls:
the report keeps PharmCAT's call and gives PyPGx's copy number beside it. `pypgx`
still hands PharmCAT every call, and `pharmcat` still leaves its genes alone.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "docker" / "pypgx" / "pypgx_wrapper.py"
MAIN_NF = ROOT / "pipelines" / "pgx" / "main.nf"
SETS = json.loads((ROOT / "config" / "genes.json").read_text(encoding="utf-8"))["sets"]


def _set(name):
    value = SETS[name]
    return list(value["genes"] if isinstance(value, dict) else value)


class _GeneConfig:
    def get_gene_set(self, name):
        return _set(name)


def _load(names):
    """The named top-level definitions of the wrapper, without importing it (PyPGx,
    FastAPI and the job client are not in the test environment)."""
    tree = ast.parse(WRAPPER.read_text(encoding="utf-8"))
    wanted, found = [], set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            wanted.append(node)
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    wanted.append(node)
                    found.add(target.id)
    assert not set(names) - found, f"missing: {sorted(set(names) - found)}"
    ns = {
        "List": list,
        "logger": logging.getLogger("pypgx-wrapper-test"),
        "gene_config": _GeneConfig(),
    }
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(WRAPPER), "exec"), ns)
    return ns


NS = _load(
    [
        "ALIGNMENT_INPUT_TYPES",
        "determine_pypgx_gene_set",
        "pharmcat_outside_call_genes",
    ]
)
choose = NS["determine_pypgx_gene_set"]
outside = NS["pharmcat_outside_call_genes"]

READS = ["bam", "cram", "sam", "fastq"]
NOT_READS = ["vcf", "bcf", "gvcf", "unknown", "", None]


@pytest.mark.parametrize("input_type", READS + ["BAM", " cram "])
def test_auto_gives_reads_every_pypgx_gene(input_type):
    assert choose("auto", input_type) == "pypgx"


@pytest.mark.parametrize("input_type", NOT_READS)
def test_auto_leaves_pharmcats_genes_alone_without_reads(input_type):
    assert choose("auto", input_type) == "pypgx_minus_pharmcat"


@pytest.mark.parametrize("input_type", READS + NOT_READS)
def test_the_explicit_preferences_ignore_the_input(input_type):
    assert choose("pharmcat", input_type) == "pypgx_minus_pharmcat"
    assert choose("pypgx", input_type) == "pypgx"
    assert choose(" PyPGx ", input_type) == "pypgx"


@pytest.mark.parametrize("input_type", ["bam", "vcf"])
def test_an_unknown_preference_is_auto_and_says_so(input_type, caplog):
    with caplog.at_level(logging.WARNING, logger="pypgx-wrapper-test"):
        assert choose("bogus", input_type) == choose("auto", input_type)
    assert "Invalid PYPGX_PHARMCAT_PREFERENCE value: bogus" in caplog.text


def test_reads_are_the_types_main_nf_sends_for_an_alignment():
    """main.nf tells PyPGx `analysed_input_type`: the upload's type for reads, "vcf"
    for a VCF, BCF or gVCF. Its alignment types are the wrapper's."""
    text = MAIN_NF.read_text(encoding="utf-8")
    assert (
        "analysed_input_type = (params.input_type in ['bcf', 'gvcf']) ? 'vcf' : "
        "params.input_type" in text
    )
    found = re.search(r"pharmcat_vcf_ch = \(params\.input_type in \[([^\]]*)\]\)", text)
    assert found, "main.nf no longer lists the alignment input types"
    alignment = {t.strip().strip("'") for t in found.group(1).split(",")}
    assert alignment == set(NS["ALIGNMENT_INPUT_TYPES"]) == set(READS)


# --------------------------------------------------------------------------
# Which calls go to PharmCAT
# --------------------------------------------------------------------------


def test_auto_keeps_pharmcats_calls_of_its_own_genes():
    genes = _set(choose("auto", "cram"))
    sent = outside("auto", genes)
    assert set(sent) == set(_set("pypgx_minus_pharmcat"))
    assert "CYP2D6" in sent
    assert not set(sent) & set(_set("pharmcat_can_call"))
    for gene in ("CYP2B6", "CYP4F2", "CYP2C19", "SLCO1B1", "DPYD", "F2", "F5"):
        assert gene in genes and gene not in sent


def test_pharmcat_sends_what_it_always_sent():
    genes = _set(choose("pharmcat", "bam"))
    assert outside("pharmcat", genes) == genes


def test_pypgx_still_hands_pharmcat_every_call():
    genes = _set(choose("pypgx", "bam"))
    assert outside("pypgx", genes) == genes
    assert outside("PYPGX", genes) == genes


# --------------------------------------------------------------------------
# The default
# --------------------------------------------------------------------------


def test_auto_is_the_default_everywhere():
    compose = yaml.safe_load((ROOT / "compose.yml").read_text(encoding="utf-8"))
    env = compose["services"]["pypgx"]["environment"]
    assert "PYPGX_PHARMCAT_PREFERENCE=${PYPGX_PHARMCAT_PREFERENCE:-auto}" in env
    for name in (".env.example", ".env.local", ".env.production"):
        lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
        assert "PYPGX_PHARMCAT_PREFERENCE=auto" in lines, name
    assert "os.getenv('PYPGX_PHARMCAT_PREFERENCE', 'auto')" in WRAPPER.read_text(
        encoding="utf-8"
    )


# --------------------------------------------------------------------------
# main.nf writes the outside calls for those genes alone
# --------------------------------------------------------------------------


def _outside_call_writer(project_dir=MAIN_NF.parent):
    """The Python main.nf's PyPGx process runs, as bash receives it: Groovy turns
    each \\\\ into \\ in a ''' shell block, and !{projectDir} into main.nf's
    directory."""
    source = MAIN_NF.read_text(encoding="utf-8")
    start = source.index("python3 - <<PY\n") + len("python3 - <<PY\n")
    end = source.index("\nPY\n", start)
    code = source[start:end].replace("\\\\", "\\")
    return code.replace("!{projectDir}", str(project_dir))


def _write_outside_calls(tmp_path, response, project_dir=MAIN_NF.parent):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "pypgx_result.json").write_text(json.dumps(response))
    proc = subprocess.run(
        [sys.executable, "-c", _outside_call_writer(project_dir)],
        cwd=tmp_path,
        env={"PYPGX_SUCCESS": "true", "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return (tmp_path / "pharmcat.outside.tsv").read_text().splitlines()


def _call(gene, diplotype):
    return {"success": True, "gene": gene, "diplotype": diplotype, "details": {}}


RESULTS = {
    "CYP2D6": _call("CYP2D6", "*3/*68+*4"),
    "CYP2B6": _call("CYP2B6", "*1/*6"),
    "CYP4F2": _call("CYP4F2", "*1/*3"),
    "GSTM1": _call("GSTM1", "*0/*B"),
}


def test_main_nf_hands_pharmcat_only_the_listed_calls(tmp_path):
    rows = _write_outside_calls(
        tmp_path,
        {
            "success": True,
            "results": RESULTS,
            "outside_call_genes": outside("auto", list(RESULTS)),
        },
    )
    assert rows == ["CYP2D6\t*3/*68+*4", "GSTM1\t*0/*B"]


def test_main_nf_hands_pharmcat_every_listed_call_under_pypgx(tmp_path):
    rows = _write_outside_calls(
        tmp_path,
        {
            "success": True,
            "results": RESULTS,
            "outside_call_genes": outside("pypgx", list(RESULTS)),
        },
    )
    assert sorted(rows) == sorted(f"{g}\t{r['diplotype']}" for g, r in RESULTS.items())


def test_an_older_pypgx_does_not_replace_pharmcats_calls(tmp_path):
    """pypgx 0.4.0 sends no list, and under auto genotypes PharmCAT's genes from
    reads: main.nf holds those back itself, from genes.json."""
    rows = _write_outside_calls(tmp_path, {"success": True, "results": RESULTS})
    assert rows == ["CYP2D6\t*3/*68+*4", "GSTM1\t*0/*B"]


def test_without_genes_json_every_call_goes_as_before(tmp_path):
    rows = _write_outside_calls(
        tmp_path / "run",
        {"success": True, "results": RESULTS},
        project_dir=tmp_path / "nowhere",
    )
    assert sorted(rows) == sorted(f"{g}\t{r['diplotype']}" for g, r in RESULTS.items())
