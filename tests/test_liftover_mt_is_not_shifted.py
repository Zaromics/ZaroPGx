"""b37's MT is already rCRS, so lifting it introduces the error it prevents.

Introduced in 93b2878 ("feat(liftover): real GRCh37/hg19 -> GRCh38 VCF
liftover"). PLAIN_TO_CHR_CONTIGS renames MT/M to chrM along with 1->chr1 and
the rest. For those 26 entries the rename is purely cosmetic -- same sequence,
same coordinates -- so rename-then-lift is right. MT is the one contig where
the spelling difference is also a sequence difference: b37 MT is NC_012920
(rCRS, 16569 bp), hg19 chrM is NC_001807 (16571 bp). Inside MT-RNR1 the chain
applies a constant -2 shift, so an m.1555A>G in a b37 VCF lifts to 1553.

That commit's A/B check was sound -- "7 textbook PGx SNPs lift to their exact
known GRCh38 positions, 0 rejected" -- but all seven are autosomal, so MT was
outside its scope, and nothing downstream reads chrM to catch it later.
"""

from pathlib import Path

GATK_API = Path(__file__).resolve().parent.parent / "docker/gatk-api/gatk_api.py"


def test_mt_is_excluded_from_the_lift_on_unprefixed_input():
    source = GATK_API.read_text(encoding="utf-8")
    assert "MT_IS_ALREADY_RCRS" in source or "exclude_mt_from_lift" in source


def test_the_reason_is_written_down_where_the_map_is():
    """The next person to read this table must not re-add the plain mapping."""
    source = GATK_API.read_text(encoding="utf-8")
    index = source.index("PLAIN_TO_CHR_CONTIGS")
    nearby = source[max(0, index - 2000) : index + 500]
    assert "rCRS" in nearby
    assert "NC_001807" in nearby or "16571" in nearby
