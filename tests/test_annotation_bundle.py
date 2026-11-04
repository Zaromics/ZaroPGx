"""The annotation the unlisted-variant check reads, and its build in the genome
downloader (docker/genome-downloader/annotation_bundle.py, downloader_api.py).

Hermetic: tiny GFF3, MANE and AlphaMissense files stand in for the sources; nothing is
fetched. The real build (2026-10-23, from the pinned sources) gave 46 MANE transcripts,
164,768 AlphaMissense rows, 70,843 gnomAD and 6,007 ClinVar records, 2.3 MB in all.
"""

import gzip
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent / "docker" / "genome-downloader"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def bundle():
    return _load("zaropgx_annotation_bundle", HERE / "annotation_bundle.py")


def _gz(path, text):
    with gzip.open(path, "wt") as f:
        f.write(text)
    return path


GFF3 = (
    "\n".join(
        [
            "##gff-version 3",
            "6\tensembl\tgene\t18128311\t18155077\t.\t-\t.\tID=gene:ENSG00000137364;Name=TPMT;biotype=protein_coding",
            "6\tensembl\tmRNA\t18128311\t18155077\t.\t-\t.\tID=transcript:ENST00000309983;Parent=gene:ENSG00000137364;"
            "Name=TPMT-201;biotype=protein_coding;tag=basic,Ensembl_canonical,MANE_Select;version=5",
            "6\tensembl\texon\t18128311\t18130780\t.\t-\t.\tParent=transcript:ENST00000309983",
            "6\tensembl\tCDS\t18130668\t18130780\t.\t-\t0\tParent=transcript:ENST00000309983",
            "6\tensembl\texon\t18132133\t18132177\t.\t-\t.\tParent=transcript:ENST00000309983",
            "6\tensembl\tCDS\t18132133\t18132177\t.\t-\t0\tParent=transcript:ENST00000309983",
            # Another transcript of the gene: not MANE, left out.
            "6\tensembl\tmRNA\t18128311\t18155077\t.\t-\t.\tID=transcript:ENST00000999999;Parent=gene:ENSG00000137364;"
            "Name=TPMT-202;biotype=protein_coding;tag=basic;version=1",
            "6\tensembl\texon\t18140000\t18140100\t.\t-\t.\tParent=transcript:ENST00000999999",
            # A gene the check leaves out (ATM: a damaging variant there is a disease finding).
            "11\tensembl\tgene\t108223067\t108369102\t.\t+\t.\tID=gene:ENSG00000149311;Name=ATM;biotype=protein_coding",
            "11\tensembl\tmRNA\t108223067\t108369102\t.\t+\t.\tID=transcript:ENST00000675843;Parent=gene:ENSG00000149311;"
            "Name=ATM-201;biotype=protein_coding;tag=MANE_Select;version=1",
        ]
    )
    + "\n"
)
MANE = (
    "#NCBI_GeneID\tEnsembl_Gene\tHGNC_ID\tsymbol\tname\tRefSeq_nuc\tRefSeq_prot\tEnsembl_nuc\t"
    "Ensembl_prot\tMANE_status\tGRCh38_chr\tchr_start\tchr_end\tchr_strand\n"
    "GeneID:7172\tENSG00000137364.5\tHGNC:12014\tTPMT\tx\tNM_000367.5\tNP_000358.1\t"
    "ENST00000309983.5\tENSP0\tMANE Select\tNC_000006.12\t18128311\t18155077\t-\n"
)


def test_each_genes_mane_transcript_is_kept_and_named_by_refseq(bundle, tmp_path):
    out = tmp_path / "transcripts.gff3.gz"
    models = bundle.mane_transcripts(
        _gz(tmp_path / "in.gff3.gz", GFF3), _gz(tmp_path / "mane.txt.gz", MANE), out
    )
    assert models == [
        {
            "gene": "TPMT",
            "transcript": "ENST00000309983.5",
            "refseq": "NM_000367.5",
            "chrom": "chr6",
            "strand": "-",
            "exons": [[18128311, 18130780], [18132133, 18132177]],
            "cds": [[18130668, 18130780], [18132133, 18132177]],
        }
    ]
    written = gzip.open(out, "rt").read()
    assert written.startswith("##gff-version 3\n")
    assert "ENST00000999999" not in written and "ATM" not in written
    # chr-named, as bcftools csq needs to match the run's VCF and FASTA.
    assert all(line.startswith("chr6\t") for line in written.splitlines()[1:])


def test_coding_exons_are_read_with_their_splice_sites_and_merged(bundle):
    models = [{"chrom": "chr6", "cds": [[100, 200], [215, 300], [1000, 1100]]}]
    assert bundle.coding_regions(models) == {"chr6": [(80, 320), (980, 1120)]}
    inside = bundle.Regions(bundle.coding_regions(models))
    assert ("chr6", 80) in inside and ("chr6", 320) in inside
    assert (
        ("chr6", 79) not in inside
        and ("chr6", 500) not in inside
        and ("chr1", 100) not in inside
    )
    assert (
        inside.overlaps("chr6", 321, 979) is False
        and inside.overlaps("chr6", 300, 400) is True
    )


def test_alphamissense_is_cut_to_the_regions(bundle, tmp_path):
    source = _gz(
        tmp_path / "am.tsv.gz",
        (
            "# Copyright 2023 DeepMind Technologies Limited\n"
            "#CHROM\tPOS\tREF\tALT\tgenome\tuniprot_id\ttranscript_id\tprotein_variant\tam_pathogenicity\tam_class\n"
            "chr6\t18130700\tA\tG\thg38\tP51580\tENST00000309983.5\tY240C\t0.9876\tlikely_pathogenic\n"
            "chr6\t18140000\tA\tG\thg38\tP51580\tENST00000309983.5\tX1Y\t0.1\tlikely_benign\n"
        ),
    )
    inside = bundle.Regions({"chr6": [(18130648, 18130800)]})
    assert bundle.cut_alphamissense(source, inside, tmp_path / "out.tsv.gz") == 1
    rows = gzip.open(tmp_path / "out.tsv.gz", "rt").read().splitlines()
    assert rows[1] == "chr6\t18130700\tA\tG\tY240C\t0.9876\tlikely_pathogenic"


def test_every_source_is_pinned_and_free_for_commercial_use(bundle):
    for name, (url, sha256, version, licence) in bundle.SOURCES.items():
        assert url.startswith("https://"), name
        assert re.fullmatch(r"[0-9a-f]{64}", sha256), name
        assert version and licence, name
        assert not re.search(r"\bNC\b|-NC|non-?commercial", licence, re.I), name
    assert bundle.GNOMAD_SOURCE["licence"] == "CC0"
    # The joint release: frequencies only, none of the exome and genome releases'
    # embedded CADD, REVEL or SpliceAI scores.
    assert "/joint/" in bundle.GNOMAD
    assert not {"cadd_phred", "revel_max", "spliceai_ds_max"} & set(
        bundle.GNOMAD_FIELDS
    )


def test_every_gene_pharmcat_reports_is_checked_but_those_no_exon_scan_can_read(bundle):
    """PharmCAT's panel (config/genes.json), CFTR, RYR1, CACNA1S, F2 and F5 among them:
    the user chose every PharmCAT gene, though a variant there can also bear on a
    disease (2026-10-24). Not IFNL3 (one association variant outside its exons),
    MT-RNR1 (mitochondrial rRNA) or HLA (typed by OptiType)."""
    import json
    from pathlib import Path

    genes = json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "genes.json").read_text()
    )
    panel = set(genes["sets"]["pharmcat_all"]) - {"IFNL3", "MT-RNR1", "HLA-A", "HLA-B"}
    assert panel - set(bundle.GENES) == set()
    for gene in ("CFTR", "RYR1", "CACNA1S", "F2", "F5"):
        assert gene in bundle.GENES, gene
    # PyPGx's other genes where a damaging variant means a disease stay out.
    for gene in ("ATM", "APOE", "CYP1B1", "XPC", "POR", "IFNL3"):
        assert gene not in bundle.GENES, gene


# --- the positions the callers can name, for the frequency table ----------
#
# The exons above answer "what else is in this gene"; these answer "how common is
# the variant behind this call", and the answer is often not in an exon. The two
# region sets are written to different files for that reason.

PHARMCAT_VCF = """##fileformat=VCFv4.2
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
chr16\t31096368\trs9923231\tC\tT\t.\tPASS\tPX=VKORC1
chr10\t94775367\trs12769205\tA\tG\t.\tPASS\tPX=CYP2C19
"""

# A quoted cell holding several variants, a cell with none, and a mitochondrial
# position gnomAD's joint release has no file for.
PYPGX_CSV = (
    "Gene,StarAllele,ActivityScore,Function,GRCh37Core,GRCh37Tag,GRCh38Core,GRCh38Tag,SV\n"
    'CYP3A5,*3,0.0,No Function,N/A,N/A,"7-99672916-T-C,7-99652770-T-C",N/A,FALSE\n'
    "CYP2D6,*1,1.0,Normal Function,N/A,N/A,N/A,N/A,FALSE\n"
    "MT-RNR1,*1,N/A,Normal Function,N/A,N/A,M-1555-A-G,N/A,FALSE\n"
)


def test_pharmcat_positions_come_from_its_own_panel(bundle, tmp_path):
    path = tmp_path / "pharmcat_positions.vcf"
    path.write_text(PHARMCAT_VCF)
    assert bundle.pharmcat_positions(path) == {
        "chr16": [31096368],
        "chr10": [94775367],
    }


def test_pypgx_positions_come_from_its_allele_table(bundle, tmp_path):
    """Core and tag columns, several to a cell, and N/A for none."""
    path = tmp_path / "allele-table.csv"
    path.write_text(PYPGX_CSV)
    assert bundle.pypgx_positions(path) == {
        "chr7": [99672916, 99652770],
        "chrM": [1555],
    }


def test_a_called_position_is_kept_with_a_few_bases_either_side(bundle):
    """Enough for an indel whose anchor sits a base off between two normalisations,
    and no more: the position is where the caller's own definition puts it."""
    called = bundle.called_regions({"chr16": [31096368]})
    assert called == {
        "chr16": [(31096368 - bundle.CALLED_FLANK, 31096368 + bundle.CALLED_FLANK)]
    }
    assert bundle.CALLED_FLANK < bundle.FLANK


def test_positions_on_a_chromosome_gnomad_has_no_file_for_are_dropped(bundle):
    """PyPGx types MT-RNR1; the joint release has no chrM, and asking for one would
    fail the build rather than leave a gap."""
    assert "chrM" not in bundle.called_regions({"chrM": [1555], "chr7": [99672916]})
    assert "chrM" not in bundle.GNOMAD_CHROMS
    assert {"chr1", "chr22", "chrX", "chrY"} <= bundle.GNOMAD_CHROMS


def test_positions_a_few_bases_apart_become_one_interval(bundle):
    assert bundle.called_regions({"chr1": [1000, 1003, 1200]}) == {
        "chr1": [(995, 1008), (1195, 1205)]
    }


def test_the_frequency_table_is_cut_wider_than_the_unlisted_check_reads(bundle):
    """The union keeps every exon interval and adds the called positions outside them.

    Both sets are written: regions.bed is the unlisted check's scope and must not
    move (docker/pypgx/unlisted_variants.py passes it to bcftools as its target
    list), while gnomad_regions.bed describes the frequency table.
    """
    exons = bundle.coding_regions([{"chrom": "chr7", "cds": [[99660000, 99660100]]}])
    called = bundle.called_regions({"chr7": [99672916], "chr16": [31096368]})
    priced = bundle.merge_regions(exons, called)
    inside = bundle.Regions(priced)
    assert ("chr7", 99660050) in inside  # the exon
    assert ("chr7", 99672916) in inside  # CYP3A5 *3, in an intron
    assert ("chr16", 31096368) in inside  # VKORC1 -1639, in the promoter
    assert ("chr7", 99665000) not in inside  # between them, still out
    # Nothing is lost from the exons.
    assert bundle.Regions(exons).regions["chr7"][0] in priced["chr7"]


def test_the_pharmcat_pin_matches_the_one_the_downloader_fetches(bundle, downloader):
    """Two pins of the same asset: a bundle cut at one release's positions while the
    matcher genotypes another's would disagree silently, gene by gene."""
    assert bundle.PHARMCAT_VERSION == downloader.PHARMCAT_VERSION
    url, sha256 = bundle.SOURCES["pharmcat_positions"][:2]
    assert (
        sha256
        == downloader.PHARMCAT_SHA256[bundle.PHARMCAT_VERSION]["pharmcat_positions"]
    )
    assert bundle.PHARMCAT_VERSION in url


def test_the_pypgx_pin_matches_the_version_the_image_installs(bundle):
    """The table is read at the tag of the release PyPGx runs from, not whatever is
    newest: its positions are the ones the calls in this report were made at."""
    dockerfile = (
        Path(__file__).resolve().parents[1] / "docker" / "pypgx" / "Dockerfile.pypgx"
    ).read_text()
    assert f"pypgx=={bundle.PYPGX_VERSION}" in dockerfile
    assert f"/pypgx/{bundle.PYPGX_VERSION}/" in bundle.SOURCES["pypgx_alleles"][0]


def test_a_bundle_of_another_version_is_not_current(bundle, tmp_path):
    (tmp_path / "manifest.json").write_text('{"bundle_version": "0"}')
    assert bundle.current(tmp_path) is None
    (tmp_path / "manifest.json").write_text(
        f'{{"bundle_version": "{bundle.BUNDLE_VERSION}"}}'
    )
    assert bundle.current(tmp_path)["bundle_version"] == bundle.BUNDLE_VERSION


@pytest.fixture()
def downloader(tmp_path, monkeypatch):
    fake_tqdm = types.ModuleType("tqdm")
    fake_tqdm.tqdm = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "tqdm", fake_tqdm)
    module = _load("zaropgx_downloader_annotation", HERE / "downloader_api.py")
    module.STATUS_FILE = str(tmp_path / "download_status.json")
    module.DOWNLOAD_COMPLETE_FLAG = str(tmp_path / ".download_complete")
    module.ANNOTATION_DIR = str(tmp_path / "annotation")
    module.ANNOTATION_CACHE = str(tmp_path / "annotation-cache")
    module.REF_CACHE_ENABLED = False
    return module


def test_a_failed_build_is_recorded_and_does_not_fail_the_downloads(
    downloader, monkeypatch
):
    def boom(out_dir, cache):
        raise RuntimeError("gnomAD chr1 could not be read")

    monkeypatch.setattr(downloader.annotation_bundle, "build", boom)
    downloader.finish_download(True)
    assert downloader.download_status["completed"] is True
    assert downloader.download_status["annotation"] == {
        "status": "error",
        "error": "gnomAD chr1 could not be read",
    }


def test_a_built_bundle_is_not_built_again(downloader, monkeypatch, tmp_path):
    built = []
    monkeypatch.setattr(
        downloader.annotation_bundle,
        "build",
        lambda o, c: built.append(o) or {"bundle_version": "1", "counts": {}},
    )
    monkeypatch.setattr(
        downloader.annotation_bundle, "current", lambda path: {"bundle_version": "1"}
    )
    downloader.build_annotation()
    assert built == [] and downloader.download_status["annotation"]["status"] == "ready"


def test_an_install_staged_before_the_annotation_existed_builds_just_it(
    downloader, monkeypatch, tmp_path
):
    Path(downloader.DOWNLOAD_COMPLETE_FLAG).write_text("Completed")
    started = []
    monkeypatch.setattr(
        downloader.threading,
        "Thread",
        lambda target: types.SimpleNamespace(start=lambda: started.append(target)),
    )
    downloader.schedule_download(0)
    assert len(started) == 1
    built = []
    monkeypatch.setattr(
        downloader.annotation_bundle,
        "build",
        lambda o, c: built.append(o) or {"bundle_version": "1", "counts": {}},
    )
    monkeypatch.setattr(
        downloader,
        "populate_reference_cache",
        lambda: pytest.fail("no cache was asked for"),
    )
    started[0]()
    assert built == [Path(downloader.ANNOTATION_DIR)]
    assert downloader.download_status["annotation"]["status"] == "ready"


def test_switched_off_nothing_is_built(downloader, monkeypatch):
    downloader.ANNOTATION_ENABLED = False
    monkeypatch.setattr(
        downloader.annotation_bundle, "build", lambda o, c: pytest.fail("built")
    )
    downloader.finish_download(True)
    assert downloader.download_status["annotation"]["status"] == "disabled"


# A synthetic VCF, bgzipped and indexed by htslib 1.24 (test_data/README.md): 6,000
# records on chr10, one every 997 bases, over several BGZF blocks and index bins.
FIXTURE = (
    Path(__file__).resolve().parent.parent / "test_data" / "tabix_range_example.vcf.gz"
)


def _served(monkeypatch, bundle):
    """_get answering from the fixture: the index whole, the data by its Range."""
    data, index = FIXTURE.read_bytes(), Path(str(FIXTURE) + ".tbi").read_bytes()
    asked = []

    def get(url, headers=None):
        if url.endswith(".tbi"):
            return index
        first, last = (
            int(x) for x in headers["Range"].removeprefix("bytes=").split("-")
        )
        asked.append((first, last))
        return data[first : last + 1]

    monkeypatch.setattr(bundle, "_get", get)
    return bundle.tabix_index(index)["chr10"], asked


def _positions(bundle, entry, spans):
    inside = bundle.Regions({"chr10": spans})
    return [
        int(c[1])
        for c in bundle.remote_records("u", entry, spans)
        if ("chr10", int(c[1])) in inside
    ]


def test_a_remote_range_returns_what_tabix_does(bundle, monkeypatch):
    entry, asked = _served(monkeypatch, bundle)
    # `tabix test_data/tabix_range_example.vcf.gz chr10:1000000-1010000`
    assert _positions(bundle, entry, [(1000000, 1010000)]) == [
        1000988,
        1001985,
        1002982,
        1003979,
        1004976,
        1005973,
        1006970,
        1007967,
        1008964,
        1009961,
    ]
    # One range request, from the block the index points to, not the file's start.
    assert len(asked) == 1 and asked[0][0] > 0


def test_spans_across_blocks_and_bins_miss_nothing(bundle, monkeypatch):
    entry, _ = _served(monkeypatch, bundle)
    assert _positions(bundle, entry, [(1, 6_000_000)]) == [
        i * 997 for i in range(1, 6001)
    ]
    spans = [(997, 997), (2_991_000, 2_995_000), (5_982_000, 5_982_000)]
    assert _positions(bundle, entry, spans) == [
        997,
        2_991_000,
        2_991_997,
        2_992_994,
        2_993_991,
        2_994_988,
        5_982_000,
    ]


def test_bins_follow_the_sam_spec(bundle):
    # SAM spec 5.3: a 1-base region at 0 is in bins 0, 1, 9, 73, 585 and 4681.
    assert bundle._reg2bins(0, 1) == [0, 1, 9, 73, 585, 4681]
    assert 4681 + (100_000 >> 14) in bundle._reg2bins(100_000, 100_001)


def test_a_deletion_running_into_a_flank_is_kept(bundle):
    """As the check reads the run's VCF (bcftools view --targets-overlap 1): by its
    reference bases, not its position alone. gnomAD's chr1:109688652 ACTT>A starts
    outside a flank and was the kind left out."""
    inside = bundle.Regions({"chr1": [(100, 200)]})
    assert bundle._overlaps(inside, "chr1", "97", "ACTT")  # 97..100
    assert not bundle._overlaps(inside, "chr1", "96", "ACTT")  # 96..99
    assert bundle._overlaps(inside, "chr1", "150", "A")
