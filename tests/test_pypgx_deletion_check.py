"""CYP2C19's, SLCO1B1's and DPYD's deletions, checked from the read depth PyPGx measures.

PyPGx has no copy-number model for either gene, so CYP2C19 *36/*37 and SLCO1B1
*48/*49 went uncalled, and with one copy deleted the other reads homozygous: a *1/*37
sample was CYP2C19 *1/*1, Normal Metabolizer. docker/pypgx/deletion_check.py bins
PyPGx's VDR-normalised copy number across each gene and calls a run of low bins;
the sidecar puts the result on the genes' calls, and a named deletion in the call
PharmCAT is given.

The profiles here are the check's own output on 1000 Genomes 30x genomes (public
data): HG00268 carries CYP2C19 *37 (exons 1-5), HG01771 a deletion of exons 2-5 that
keeps exon 1, and NA18861 neither. DPYD's are 1 kb bins: GeT-RM's DPYD study has one
copy of exon 11 deleted in NA19093 and of exon 4 in HG00325.
"""

from __future__ import annotations

import csv
import gzip
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "docker" / "pypgx" / "deletion_check.py"
WRAPPER = ROOT / "docker" / "pypgx" / "pypgx_wrapper.py"

# PyPGx's GRCh38 regions and exons (lexicon/gene-table.csv), + strand both.
CYP2C19_START = 94759680
CYP2C19_EXONS = [
    (94762680, 94762873),
    (94775057, 94775220),
    (94775389, 94775539),
    (94780498, 94780659),
    (94781820, 94781997),
    (94820495, 94820637),
    (94842836, 94843024),
    (94849916, 94850058),
    (94852732, 94855547),
]
SLCO1B1_START = 21128193
SLCO1B1_EXONS = [
    (21131193, 21131236),
    (21141513, 21141658),
    (21172649, 21172791),
    (21174576, 21174709),
    (21176775, 21176897),
    (21178575, 21178722),
    (21178921, 21179020),
    (21196945, 21197188),
    (21200507, 21200672),
    (21202490, 21202686),
    (21205867, 21206033),
    (21217118, 21217303),
    (21222299, 21222364),
    (21224721, 21224839),
    (21238978, 21239796),
]
# The check's 5 kb bins on 1000 Genomes 30x genomes.
HG00268_CYP2C19 = [
    0.91,
    0.91,
    0.86,
    0.86,
    0.91,
    0.91,
    0.8,
    0.8,
    1.77,
    1.83,
    1.66,
    1.83,
    1.77,
    1.83,
    1.89,
    1.89,
    1.89,
    1.83,
    1.83,
    1.71,
]
HG01771_CYP2C19 = [
    2.06,
    1.89,
    1.0,
    1.0,
    0.94,
    1.0,
    1.06,
    0.83,
    0.94,
    1.06,
    1.75,
    1.78,
    2.17,
    1.89,
    2.0,
    1.83,
    1.94,
    2.0,
    1.89,
    2.06,
]
NA18861_CYP2C19 = [
    2.0,
    1.89,
    1.77,
    1.89,
    1.71,
    1.83,
    1.94,
    1.77,
    1.94,
    2.0,
    1.77,
    1.71,
    1.77,
    1.77,
    2.06,
    1.77,
    1.94,
    1.71,
    1.83,
    1.94,
]
NA18861_SLCO1B1 = [
    1.66,
    1.83,
    1.71,
    1.66,
    1.6,
    1.66,
    1.71,
    1.66,
    1.77,
    1.66,
    1.83,
    1.6,
    1.77,
    1.66,
    1.77,
    1.71,
    1.54,
    2.06,
    1.71,
    1.77,
    1.66,
    1.83,
    1.66,
]
# DPYD's 1 kb bins over 30 kb (- strand: exon 11 at 97,573,759, exon 4 at 97,740,391).
DPYD_EXON_11_START = 97566742
NA19093_DPYD = [
    1.9,
    2.21,
    1.79,
    1.9,
    1.68,
    1.58,
    0.9,
    0.74,
    0.95,
    1.0,
    1.9,
    1.63,
    1.92,
    1.84,
    1.68,
    2.0,
    1.84,
    1.95,
    1.84,
    1.84,
    1.84,
    1.79,
    1.79,
    2.21,
    1.68,
    2.1,
    2.21,
    2.0,
    1.53,
    1.53,
]
NA18861_DPYD = [
    2.06,
    1.66,
    1.6,
    1.89,
    1.89,
    1.6,
    1.77,
    1.77,
    1.83,
    1.89,
    1.83,
    1.94,
    1.49,
    1.66,
    1.77,
    1.66,
    1.77,
    1.6,
    1.49,
    1.6,
    1.83,
    1.66,
    1.43,
    1.83,
    1.77,
    1.8,
    1.66,
    1.49,
    1.83,
    1.54,
]
DPYD_EXON_4_START = 97726742
HG00325_DPYD = [
    1.88,
    1.88,
    2.0,
    2.0,
    1.56,
    1.94,
    1.62,
    1.94,
    0.88,
    1.0,
    0.75,
    0.94,
    0.81,
    0.81,
    1.12,
    0.88,
    1.0,
    0.94,
    1.06,
    1.0,
    1.0,
    0.94,
    1.81,
    1.69,
    1.62,
    1.88,
    1.75,
    1.81,
    1.81,
    2.25,
]


def _dpyd_exons():
    """DPYD's GRCh38 exons, from PyPGx's gene table (lexicon/gene-table.csv)."""
    with open(ROOT / "lexicon" / "gene-table.csv", newline="", encoding="utf-8") as fh:
        row = next(r for r in csv.DictReader(fh) if r["Gene"] == "DPYD")
    starts = [int(x) for x in row["GRCh38ExonStarts"].strip(",").split(",")]
    ends = [int(x) for x in row["GRCh38ExonEnds"].strip(",").split(",")]
    return list(zip(starts, ends))


@pytest.fixture(scope="module")
def check():
    spec = importlib.util.spec_from_file_location("deletion_check", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bins(start, values, size=5000):
    return [(start + i * size, start + (i + 1) * size, v) for i, v in enumerate(values)]


def test_hg00268_is_cyp2c19_star37(check):
    record = check.call_gene(
        "CYP2C19", _bins(CYP2C19_START, HG00268_CYP2C19), CYP2C19_EXONS
    )
    (deletion,) = record["deletions"]
    assert record["status"] == "measured"
    assert deletion["exons"] == [1, 2, 3, 4, 5]
    assert deletion["allele"] == "*37" and deletion["copies_lost"] == 1
    assert deletion["description"] == "CYP2C19 *37: one copy of exons 1-5 deleted"


def test_a_deletion_that_keeps_exon_1_is_not_star37(check):
    record = check.call_gene(
        "CYP2C19", _bins(CYP2C19_START, HG01771_CYP2C19), CYP2C19_EXONS
    )
    (deletion,) = record["deletions"]
    assert deletion["exons"] == [2, 3, 4, 5] and deletion["allele"] is None
    assert "not *37" in deletion["description"]
    assert "*42" in deletion["description"]


@pytest.mark.parametrize(
    "gene, start, values, exons",
    [
        ("CYP2C19", CYP2C19_START, NA18861_CYP2C19, CYP2C19_EXONS),
        ("SLCO1B1", SLCO1B1_START, NA18861_SLCO1B1, SLCO1B1_EXONS),
    ],
)
def test_a_normal_profile_has_no_deletion(check, gene, start, values, exons):
    record = check.call_gene(gene, _bins(start, values), exons)
    assert record["status"] == "measured" and record["deletions"] == []
    assert check.genotype("*1/*2", record) == {
        "diplotype": "*1/*2",
        "copy_number": "Normal",
        "conflict": False,
    }


def test_one_low_bin_is_noise(check):
    values = list(NA18861_CYP2C19)
    values[3] = 0.9
    record = check.call_gene("CYP2C19", _bins(CYP2C19_START, values), CYP2C19_EXONS)
    assert record["deletions"] == []


def test_short_or_shallow_dips_at_15x_are_noise(check):
    # A GeT-RM reference genome downsampled to 15x: two SLCO1B1 bins at 1.33, which the
    # first thresholds (two bins under 1.35) called *49. Three shallow bins are noise too.
    for dip in ([1.33, 1.33], [1.3, 1.3, 1.3]):
        values = list(NA18861_SLCO1B1)
        values[4 : 4 + len(dip)] = dip
        record = check.call_gene("SLCO1B1", _bins(SLCO1B1_START, values), SLCO1B1_EXONS)
        assert record["deletions"] == [], dip


def test_a_deletion_at_15x_is_still_called(check):
    # HG01771's exons 2-5 deletion at 15x: bins up to 1.2, median 1.07.
    values = [v if v > 1.5 else min(1.2, v + 0.07) for v in HG01771_CYP2C19]
    record = check.call_gene("CYP2C19", _bins(CYP2C19_START, values), CYP2C19_EXONS)
    (deletion,) = record["deletions"]
    assert deletion["exons"] == [2, 3, 4, 5] and deletion["copies_lost"] == 1


def test_whole_genes_and_both_copies(check):
    whole = check.call_gene("CYP2C19", _bins(CYP2C19_START, [0.95] * 20), CYP2C19_EXONS)
    # A deletion of the whole region leaves no bins to compare: still called.
    assert whole["deletions"][0]["allele"] == "*36"
    both = check.call_gene("CYP2C19", _bins(CYP2C19_START, [0.05] * 20), CYP2C19_EXONS)
    assert both["deletions"][0]["copies_lost"] == 2
    assert check.genotype(None, both)["diplotype"] == "*36/*36"
    slco = check.call_gene("SLCO1B1", _bins(SLCO1B1_START, [1.0] * 23), SLCO1B1_EXONS)
    assert slco["deletions"][0]["allele"] == "*48"
    assert "SLCO1B3" in slco["deletions"][0]["description"]
    partial = list(NA18861_SLCO1B1)
    partial[:6] = [1.0] * 6  # exons 1-2
    slco = check.call_gene("SLCO1B1", _bins(SLCO1B1_START, partial), SLCO1B1_EXONS)
    assert slco["deletions"][0]["allele"] == "*49"
    assert slco["deletions"][0]["exons"] == [1, 2]


def test_a_profile_off_two_copies_calls_nothing(check):
    record = check.call_gene(
        "CYP2C19", _bins(CYP2C19_START, [1.4] * 18 + [0.5, 0.5]), CYP2C19_EXONS
    )
    assert record["status"] == "uncertain" and record["deletions"] == []
    # SLCO1B1 reads a little under two copies on normal genomes: still measured.
    low = check.call_gene("SLCO1B1", _bins(SLCO1B1_START, [1.55] * 23), SLCO1B1_EXONS)
    assert low["status"] == "measured" and low["deletions"] == []


def test_the_call_takes_the_deletion(check):
    record = check.call_gene(
        "CYP2C19", _bins(CYP2C19_START, HG00268_CYP2C19), CYP2C19_EXONS
    )
    # One copy left: its alleles read homozygous.
    assert check.genotype("*1/*1", record) == {
        "diplotype": "*1/*37",
        "copy_number": "PartialDel1",
        "conflict": False,
    }
    assert check.genotype("*17/*17", record)["diplotype"] == "*17/*37"
    # Heterozygous under a deletion: a contradiction, left as it was and marked.
    assert check.genotype("*1/*2", record) == {
        "diplotype": "*1/*2",
        "copy_number": "PartialDel1",
        "conflict": True,
    }


def test_an_unnamed_deletion_leaves_the_call(check):
    record = check.call_gene(
        "CYP2C19", _bins(CYP2C19_START, HG01771_CYP2C19), CYP2C19_EXONS
    )
    assert check.genotype("*1/*1", record) == {
        "diplotype": "*1/*1",
        "copy_number": "PartialDel1",
        "conflict": False,
    }


@pytest.mark.parametrize(
    "start, values, exon",
    [(DPYD_EXON_11_START, NA19093_DPYD, 11), (DPYD_EXON_4_START, HG00325_DPYD, 4)],
)
def test_dpyd_exon_deletions_are_called_and_leave_the_call(check, start, values, exon):
    record = check.call_gene("DPYD", _bins(start, values, 1000), _dpyd_exons(), "-")
    (deletion,) = record["deletions"]
    assert record["bin_size"] == 1000 and record["min_bins"] == 4
    assert deletion["exons"] == [exon] and deletion["copies_lost"] == 1
    assert deletion["allele"] is None
    assert deletion["description"] == f"a deletion of one copy of DPYD exon {exon}"
    # No allele names it, so the call from small variants stands.
    assert check.genotype("Reference/c.1627A>G (*5)", record) == {
        "diplotype": "Reference/c.1627A>G (*5)",
        "copy_number": "PartialDel1",
        "conflict": False,
    }


def test_dpyds_noise_at_15x_is_not_a_deletion(check):
    # At 15x, normal genomes had runs of 3 bins with a median of 1.1 and of 4 with 1.2;
    # the deletions read 0.92-0.94 over 4 or more.
    for dip in ([1.1, 1.1, 1.1], [1.2, 1.2, 1.2, 1.2]):
        values = list(NA18861_DPYD)
        values[6 : 6 + len(dip)] = dip
        record = check.call_gene(
            "DPYD", _bins(DPYD_EXON_11_START, values, 1000), _dpyd_exons(), "-"
        )
        assert record["deletions"] == [], dip
    values = list(NA18861_DPYD)
    values[6:10] = [0.92] * 4
    record = check.call_gene(
        "DPYD", _bins(DPYD_EXON_11_START, values, 1000), _dpyd_exons(), "-"
    )
    assert [d["exons"] for d in record["deletions"]] == [[11]]


def test_cyp2c19_and_slco1b1_keep_their_rule(check):
    assert check.RULES["CYP2C19"] == check.RULES["SLCO1B1"] == (5000, 3, 1.2)
    assert check.RULES["DPYD"] == (1000, 4, 1.1)
    assert check.GENES == ("CYP2C19", "SLCO1B1", "DPYD")


def test_no_depth_measures_nothing(check):
    record = check.call_gene("CYP2C19", [], CYP2C19_EXONS)
    assert record["status"] == "no_depth"
    assert check.genotype("*1/*1", record)["copy_number"] is None


def test_bins_take_the_median_of_their_positions(check):
    bins = check.bins_of([1, 2, 3, 5001, 5002], [2.0, 1.0, 2.0, 0.5, 0.7], 1, 10001)
    assert bins == [(1, 5001, 2.0), (5001, 10001, 0.6)]


# ---------------------------------------------------------------------------
# The sidecar: the result travels in the VCF's header, and lands on the calls
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pypgx(tmp_path_factory):
    root = tmp_path_factory.mktemp("pypgx_deletions_home")
    psutil = types.ModuleType("psutil")
    psutil.virtual_memory = lambda: types.SimpleNamespace(
        total=16 * 1024**3, available=8 * 1024**3, used=8 * 1024**3, percent=50.0
    )
    psutil.Process = lambda *a, **k: types.SimpleNamespace()
    psutil.pid_exists = lambda pid: False
    for name in ("NoSuchProcess", "AccessDenied", "TimeoutExpired"):
        setattr(psutil, name, type(name, (Exception,), {}))
    job_client = types.ModuleType("job_client")
    job_client.JobClient = object
    job_client.create_job_client = lambda *a, **k: None
    before = list(logging.root.handlers)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(root / "data"))
        mp.setenv("TMPDIR", str(root / "tmp"))
        mp.setenv("REFERENCE_DIR", str(root / "reference"))
        mp.setenv("PYPGX_PROGRESS_LOG", str(root / "pypgx_progress.log"))
        mp.setitem(sys.modules, "psutil", psutil)
        mp.setitem(sys.modules, "job_client", job_client)
        spec = importlib.util.spec_from_file_location(
            "zaropgx_pypgx_deletions_test", WRAPPER
        )
        module = importlib.util.module_from_spec(spec)
        mp.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        yield module
    for handler in list(logging.root.handlers):
        if handler not in before:
            logging.root.removeHandler(handler)
    for handler in getattr(module, "_log_handlers", []):
        handler.close()


def _check_file(check, directory, values=HG00268_CYP2C19):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "abc.deletions.json"
    path.write_text(
        json.dumps(
            {
                "version": check.VERSION,
                "genes": {
                    "CYP2C19": check.call_gene(
                        "CYP2C19", _bins(CYP2C19_START, values), CYP2C19_EXONS
                    ),
                    "SLCO1B1": check.call_gene(
                        "SLCO1B1", _bins(SLCO1B1_START, NA18861_SLCO1B1), SLCO1B1_EXONS
                    ),
                },
            }
        )
    )
    return path


def _vcf(directory, header=""):
    path = directory / "in.vcf.gz"
    with gzip.open(path, "wt") as fh:
        fh.write("##fileformat=VCFv4.2\n" + header)
        fh.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n")
    return path


def test_the_header_line_round_trips(pypgx, check, tmp_path):
    path = _check_file(check, tmp_path / "temp" / "pypgx" / "job" / "x")
    vcf = _vcf(tmp_path, pypgx.deletion_check_header_line(str(path)) + "\n")
    got = pypgx.deletion_check_from_vcf(str(vcf), tmp_path / "temp")
    assert got["genes"]["CYP2C19"]["deletions"][0]["allele"] == "*37"


def test_a_check_the_sidecar_did_not_write_is_ignored(pypgx, check, tmp_path):
    outside = _check_file(check, tmp_path / "elsewhere")
    vcf = _vcf(tmp_path, pypgx.deletion_check_header_line(str(outside)) + "\n")
    assert pypgx.deletion_check_from_vcf(str(vcf), tmp_path / "temp") is None
    assert pypgx.deletion_check_from_vcf(str(_vcf(tmp_path)), tmp_path / "temp") is None


def _aggregated(cyp2c19="*1/*1"):
    return {
        "results": {
            "CYP2C19": {
                "success": True,
                "diplotype": cyp2c19,
                "details": {"phenotype": "Normal Metabolizer"},
            },
            "SLCO1B1": {"success": True, "diplotype": "*1/*15", "details": {}},
            "CYP2D6": {"success": True, "diplotype": "*1/*4", "details": {}},
        },
        "outside_call_genes": ["CYP2D6"],
    }


def test_a_named_deletion_goes_into_the_call_and_to_pharmcat(pypgx, check, tmp_path):
    record = json.loads(_check_file(check, tmp_path).read_text())
    aggregated = _aggregated()
    pypgx.apply_deletion_check(aggregated, record)
    cyp2c19 = aggregated["results"]["CYP2C19"]
    assert cyp2c19["diplotype"] == "*1/*37"
    details = cyp2c19["details"]
    assert details["copy_number"] == "PartialDel1"
    assert details["copy_number_by"] == "deletion_check"
    assert details["snv_diplotype"] == "*1/*1"
    # PyPGx's phenotype was for *1/*1; PharmCAT phenotypes the call it is given.
    assert (
        "phenotype" not in details and details["snv_phenotype"] == "Normal Metabolizer"
    )
    assert details["deletion_check"]["deletions"][0]["allele"] == "*37"
    assert aggregated["outside_call_genes"] == ["CYP2D6", "CYP2C19"]
    # Measured normal: the copy number, and nothing else changes.
    slco1b1 = aggregated["results"]["SLCO1B1"]
    assert slco1b1["diplotype"] == "*1/*15"
    assert slco1b1["details"]["copy_number"] == "Normal"
    assert "SLCO1B1" not in aggregated["outside_call_genes"]


def test_a_contradiction_keeps_pharmcats_call(pypgx, check, tmp_path):
    record = json.loads(_check_file(check, tmp_path).read_text())
    aggregated = _aggregated("*1/*2")
    pypgx.apply_deletion_check(aggregated, record)
    cyp2c19 = aggregated["results"]["CYP2C19"]
    assert cyp2c19["diplotype"] == "*1/*2"
    assert cyp2c19["details"]["deletion_conflict"] is True
    assert "CYP2C19" not in aggregated["outside_call_genes"]


def test_a_dpyd_deletion_marks_the_call_and_leaves_it_pharmcats(pypgx, check):
    record = {
        "genes": {
            "DPYD": check.call_gene(
                "DPYD", _bins(DPYD_EXON_4_START, HG00325_DPYD, 1000), _dpyd_exons(), "-"
            )
        }
    }
    aggregated = {
        "results": {
            "DPYD": {
                "success": True,
                "diplotype": "Reference/Reference",
                "details": {"phenotype": "Normal Metabolizer"},
            }
        },
        "outside_call_genes": [],
    }
    pypgx.apply_deletion_check(aggregated, record)
    dpyd = aggregated["results"]["DPYD"]
    assert dpyd["diplotype"] == "Reference/Reference"
    assert dpyd["details"]["copy_number"] == "PartialDel1"
    assert dpyd["details"]["copy_number_by"] == "deletion_check"
    assert dpyd["details"]["phenotype"] == "Normal Metabolizer"
    assert dpyd["details"]["deletion_check"]["deletions"][0]["exons"] == [4]
    assert aggregated["outside_call_genes"] == []


def test_genes_pypgx_did_not_genotype_are_left_alone(pypgx, check, tmp_path):
    record = json.loads(_check_file(check, tmp_path).read_text())
    aggregated = {"results": {}, "outside_call_genes": []}
    pypgx.apply_deletion_check(aggregated, record)
    assert aggregated == {"results": {}, "outside_call_genes": []}
    pypgx.apply_deletion_check(_aggregated(), None)


def test_the_image_carries_the_check():
    dockerfile = (ROOT / "docker" / "pypgx" / "Dockerfile.pypgx").read_text()
    assert "docker/pypgx/deletion_check.py" in dockerfile
