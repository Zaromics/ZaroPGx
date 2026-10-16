"""An alignment with no read groups gets PyPGx calls, not 64 failures out of 66.

Found 2026-10-14 on a whole-genome BAM whose header had no @RG lines, which many
aligners and services write. bcftools names such an alignment's sample after the path
it was given, so create-input-vcf's VCF named its sample
"/data/temp/pypgx/<job>/<dir>/<file>.bam". PyPGx draws each sample's allele-fraction
plot at <output>/allele-fraction-profile/<sample>.png, a path that does not exist, and
every gene with a variant failed (FileNotFoundError); only GSTT1 and UGT2B17, which
draw no such plot, were called. fuc, which builds the depth of coverage PyPGx's SV
calls read, names the same alignment by its file name's stem, so the two disagreed
as well.

The wrapper now names the VCF's sample (and the PharmCAT positions VCF's) as fuc
does: the read groups' one SM tag, or with none, the stem.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

pysam = pytest.importorskip("pysam")

WRAPPER = Path(__file__).resolve().parents[1] / "docker" / "pypgx" / "pypgx_wrapper.py"


def _load(names):
    tree = ast.parse(WRAPPER.read_text(encoding="utf-8"))
    wanted = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert {n.name for n in wanted} == set(names)
    ns = {"os": os, "Path": Path}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(WRAPPER), "exec"), ns)
    return ns


NS = _load(["alignment_sample_name", "name_vcf_sample"])


def _bam(path, read_groups):
    header = {
        "HD": {"VN": "1.6", "SO": "coordinate"},
        "SQ": [{"SN": "chr22", "LN": 1000}],
    }
    if read_groups:
        header["RG"] = read_groups
    with pysam.AlignmentFile(str(path), "wb", header=header):
        pass
    return str(path)


def test_the_sample_is_the_read_groups_sm_tag(tmp_path):
    bam = _bam(
        tmp_path / "x.bam",
        [{"ID": "L1", "SM": "NA12878"}, {"ID": "L2", "SM": "NA12878"}],
    )
    assert NS["alignment_sample_name"](bam) == "NA12878"


def test_without_read_groups_it_is_the_file_names_stem(tmp_path):
    """fuc's fallback; never the path bcftools uses, which holds slashes."""
    bam = _bam(tmp_path / "d0bacf754ecd4c429889d4bd8884510f.bam", None)
    assert NS["alignment_sample_name"](bam) == "d0bacf754ecd4c429889d4bd8884510f"
    no_sm = _bam(tmp_path / "y.bam", [{"ID": "L1"}])
    assert NS["alignment_sample_name"](no_sm) == "y"


def test_two_samples_fall_back_to_the_stem(tmp_path):
    bam = _bam(tmp_path / "z.bam", [{"ID": "A", "SM": "S1"}, {"ID": "B", "SM": "S2"}])
    assert NS["alignment_sample_name"](bam) == "z"


def _vcf(tmp_path, sample):
    text = (
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr22,length=1000>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        f"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t{sample}\n"
        "chr22\t100\t.\tG\tA\t50\tPASS\t.\tGT\t0/1\n"
    )
    plain = tmp_path / "in.vcf"
    plain.write_text(text)
    gz = tmp_path / "in.vcf.gz"
    pysam.tabix_compress(str(plain), str(gz))
    pysam.tabix_index(str(gz), preset="vcf")
    return str(gz)


def test_the_vcf_sample_is_renamed_and_its_index_dropped(tmp_path):
    vcf = _vcf(tmp_path, "/data/temp/pypgx/job/dir/d0bacf.bam")
    assert NS["name_vcf_sample"](vcf, "d0bacf") is True
    with pysam.VariantFile(vcf) as vf:
        assert list(vf.header.samples) == ["d0bacf"]
        (rec,) = list(vf)
        assert rec.samples["d0bacf"]["GT"] == (0, 1)
    assert not os.path.exists(vcf + ".tbi"), "the caller indexes the new file"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["in.vcf", "in.vcf.gz"]


def test_a_name_with_spaces_is_written_whole(tmp_path):
    """bcftools reads an unescaped space as an "old new" pair and renamed nothing,
    while the old index was dropped and the rename logged."""
    vcf = _vcf(tmp_path, "/data/temp/pypgx/job/dir/d0bacf.bam")
    assert NS["name_vcf_sample"](vcf, "NA 12878\\x") is True
    with pysam.VariantFile(vcf) as vf:
        assert list(vf.header.samples) == ["NA 12878\\x"]


def test_a_vcf_already_named_so_is_left_alone(tmp_path):
    vcf = _vcf(tmp_path, "NA12878")
    before = Path(vcf).read_bytes()
    assert NS["name_vcf_sample"](vcf, "NA12878") is False
    assert Path(vcf).read_bytes() == before
    assert os.path.exists(vcf + ".tbi")


def _body(name):
    src = WRAPPER.read_text(encoding="utf-8")
    body = src[src.index(f"def {name}(") :]
    return body[: body.index("\ndef ", 1)]


def test_create_input_vcf_names_the_sample_before_indexing():
    body = _body("run_pypgx_create_input_vcf")
    named = body.index("name_vcf_sample(output_vcf_gz, name)")
    assert body.index("alignment_sample_name(") < named < body.index("tabix_index(")
    # Before anything reads the VCF: the SV inputs, the uncovered genes.
    assert named < body.index("uncovered_genes_in_alignment(")


def test_the_pharmcat_vcf_is_named_by_the_same_rule():
    body = _body("genotype_pharmcat_positions")
    assert "alignment_sample_name(str(alignment_path))" in body
    assert "header.add_sample(name)" in body
    assert "header.add_sample(sample)" not in body
    assert "out.new_record" in body and 'rec.samples[sample]["GT"]' not in body
