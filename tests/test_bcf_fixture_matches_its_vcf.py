"""The BCF fixture is its VCF, in the other encoding -- and stays that way.

`test_data/pharmcat.example.bcf` exists so the BCF lane can be exercised end to end
with the same records the VCF lane uses, which is what makes the two comparable. A
binary fixture cannot be read in a diff, so nothing would notice if one were
regenerated and the other not: this reads both and compares them.
"""

from __future__ import annotations

from pathlib import Path

import pysam

TEST_DATA = Path(__file__).resolve().parents[1] / "test_data"
VCF = TEST_DATA / "pharmcat.example.vcf"
BCF = TEST_DATA / "pharmcat.example.bcf"


def _records(path: Path):
    with pysam.VariantFile(str(path)) as handle:
        return [
            (
                record.chrom,
                record.pos,
                record.ref,
                tuple(record.alts or ()),
                tuple(
                    tuple(sample.get("GT") or ()) for sample in record.samples.values()
                ),
            )
            for record in handle
        ]


def test_the_bcf_fixture_holds_the_same_records_as_its_vcf():
    vcf, bcf = _records(VCF), _records(BCF)

    assert len(bcf) == len(vcf), f"{len(bcf)} records in the BCF, {len(vcf)} in the VCF"
    assert bcf == vcf, "the two encodings of this fixture have drifted apart"


def test_the_fixture_is_not_empty():
    """A comparison of two empty files passes and proves nothing."""
    assert len(_records(BCF)) > 1000
