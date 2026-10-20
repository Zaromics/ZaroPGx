"""genome-downloader checks every fixed download against a pinned SHA-256.

The reference FASTAs (UCSC hg19 and hg38 over plain http, 1000 Genomes GRCh37,
PharmCAT's GRCh38 tarball), the 1000 Genomes analysis set behind the CRAM cache,
PharmCAT's positions VCF and regions BED, and the liftover chain were all
written to disk as served, with nothing to catch a corrupted or substituted file.
Each now carries the SHA-256 of the provider's file (taken from a download whose
MD5 matched the provider's published checksum), hashed while it streams and
checked before it moves into place. A mismatch is discarded with the reason in
/status; a pinned file left on disk that does not match is fetched again.

`docker/genome-downloader/downloader_api.py` runs in its own image and imports
tqdm, which the dev venv does not ship, so the fixture stubs it.
"""

import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parent.parent
    / "docker"
    / "genome-downloader"
    / "downloader_api.py"
)

PINNED = {
    "hg19": "ac73947d38df63ccb00724520a5c31d880c1ca423702ca7ccb7e6c2182a362d9",
    "hg38": "c1dd87068c254eb53d944f71e51d1311964fce8de24d6fc0effc9c61c01527d4",
    "grch37": "8b6c538abf0dd92d3f3020f36cc1dd67ce004ffa421c2781205f1eb690bdb442",
    "pharmcat_grch38": "7aeb9c9b7ddb262aab32fe97a48317a4a54d97680dea27b81af4561cf502f839",
    "pharmcat_positions": "63f49b9004b6a5ca921deebe8ec59f8e45f6990543e07b7890c0d6201f5e69fc",
    "pharmcat_regions": "9755c524125481c6140e9996e9569b8fb01544322ee704e854f2f7c6eaf4e14a",
    "hg19_to_hg38_chain": "5c0598e500ceb5a78c73086929e8ef993aec309bcafb595139b53d440b125a1d",
}
ANALYSIS_SET = "3b103f4742abfd54938fb0333e19ad067635c8eb86f1dbf0ce44b165c4292b50"


@pytest.fixture()
def downloader(tmp_path, monkeypatch):
    monkeypatch.delenv("PHARMCAT_VERSION", raising=False)
    fake_tqdm = types.ModuleType("tqdm")
    fake_tqdm.tqdm = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "tqdm", fake_tqdm)
    spec = importlib.util.spec_from_file_location("zaropgx_downloader_refs", SOURCE)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module.STATUS_FILE = str(tmp_path / "download_status.json")
    module.REF_CACHE_DIR = str(tmp_path / "ref_cache")
    return module


class _Response:
    def __init__(self, body: bytes):
        self.body = body
        self.headers = {"content-length": str(len(body))}

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        for i in range(0, len(self.body), chunk_size):
            yield self.body[i : i + chunk_size]


def _serve(downloader, monkeypatch, body: bytes):
    calls = []

    def get(*a, **k):
        calls.append(a)
        return _Response(body)

    monkeypatch.setattr(downloader.requests, "head", lambda *a, **k: _Response(body))
    monkeypatch.setattr(downloader.requests, "get", get)
    return calls


def test_every_fixed_download_is_pinned(downloader):
    pins = {g["name"]: g.get("sha256") for g in downloader.GENOMES}
    assert pins == PINNED
    (analysis_set,) = [s for s in downloader.REF_CACHE_SOURCES if s.get("url")]
    assert analysis_set["sha256"] == ANALYSIS_SET


def test_pharmcat_files_come_from_the_release_tag_not_development(downloader):
    urls = {g["name"]: g["url"] for g in downloader.GENOMES}
    assert urls["pharmcat_regions"] == (
        "https://github.com/PharmGKB/PharmCAT/raw/v3.4.0/pharmcat_regions.bed"
    )
    assert all("/development/" not in url for url in urls.values())


def test_a_pharmcat_release_without_pins_is_fetched_unpinned(tmp_path, monkeypatch):
    monkeypatch.setenv("PHARMCAT_VERSION", "9.9.9")
    fake_tqdm = types.ModuleType("tqdm")
    fake_tqdm.tqdm = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "tqdm", fake_tqdm)
    spec = importlib.util.spec_from_file_location("zaropgx_downloader_v999", SOURCE)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    pins = {g["name"]: g.get("sha256") for g in module.GENOMES}
    assert pins["pharmcat_positions"] is None
    assert pins["pharmcat_regions"] is None
    assert pins["hg38"] == PINNED["hg38"]


def test_the_download_is_hashed_as_it_streams_not_read_back(
    downloader, tmp_path, monkeypatch
):
    """A gigabyte FASTA should not be read a second time to check it."""
    body = b">chr1\nACGT\n" * 1000
    _serve(downloader, monkeypatch, body)

    def read_back(path):
        raise AssertionError(f"{path} was read back after download")

    monkeypatch.setattr(downloader, "sha256_of", read_back)
    dest = tmp_path / "hg38.fa.gz"

    ok = downloader.download_file(
        "http://x/hg38.fa.gz",
        str(dest),
        "hg38",
        sha256=hashlib.sha256(body).hexdigest(),
    )

    assert ok is True
    assert dest.read_bytes() == body


def test_a_corrupted_reference_download_is_discarded_and_says_why(
    downloader, tmp_path, monkeypatch
):
    _serve(downloader, monkeypatch, b"truncated")
    dest = tmp_path / "hg38.fa.gz"

    ok = downloader.download_file(
        "http://x/hg38.fa.gz", str(dest), "hg38", sha256=PINNED["hg38"]
    )

    assert ok is False
    assert not dest.exists() and not Path(f"{dest}.part").exists()
    error = downloader.download_status["genomes"]["hg38"]["error"]
    assert "SHA-256 mismatch" in error and PINNED["hg38"] in error


def _analysis_set(downloader, tmp_path, monkeypatch, sha256):
    src = {
        "name": "ref_cache_1kg_analysis_set",
        "url": "https://x/GRCh38_full_analysis_set_plus_decoy_hla.fa",
        "sha256": sha256,
        "fasta_path": str(tmp_path / "sources" / "analysis_set.fa"),
        "remove_after": True,
    }
    monkeypatch.setattr(downloader, "REF_CACHE_SOURCES", [src])
    return src


def test_the_cram_cache_source_is_verified_before_it_is_cached(
    downloader, tmp_path, monkeypatch
):
    fasta = b">chr1\nACGT\n>chr2\nGGCC\n"
    src = _analysis_set(downloader, tmp_path, monkeypatch, ANALYSIS_SET)
    _serve(downloader, monkeypatch, fasta)

    assert downloader.populate_reference_cache() is False

    entry = downloader.download_status["genomes"]["ref_cache_1kg_analysis_set"]
    assert "SHA-256 mismatch" in entry["error"] and ANALYSIS_SET in entry["error"]
    assert not Path(src["fasta_path"]).exists()
    assert not Path(downloader.REF_CACHE_DIR).exists(), "nothing cached from it"


def test_the_cram_cache_source_is_cached_when_it_matches(
    downloader, tmp_path, monkeypatch
):
    fasta = b">chr1\nACGT\n>chr2\nGGCC\n"
    src = _analysis_set(
        downloader, tmp_path, monkeypatch, hashlib.sha256(fasta).hexdigest()
    )
    _serve(downloader, monkeypatch, fasta)

    assert downloader.populate_reference_cache() is True

    assert not Path(src["fasta_path"]).exists(), "removed once cached"
    md5 = hashlib.md5(b"ACGT").hexdigest()
    assert Path(downloader.ref_cache_path(downloader.REF_CACHE_DIR, md5)).exists()


def test_a_wrong_cram_cache_source_left_on_disk_is_fetched_again(
    downloader, tmp_path, monkeypatch
):
    fasta = b">chr1\nACGT\n"
    src = _analysis_set(
        downloader, tmp_path, monkeypatch, hashlib.sha256(fasta).hexdigest()
    )
    Path(src["fasta_path"]).parent.mkdir(parents=True)
    Path(src["fasta_path"]).write_bytes(b">chr1\nTTTT\n")  # a stopped earlier run's
    calls = _serve(downloader, monkeypatch, fasta)

    assert downloader.populate_reference_cache() is True

    assert calls, "the wrong file was trusted instead of fetched again"
    md5 = hashlib.md5(b"ACGT").hexdigest()
    assert Path(downloader.ref_cache_path(downloader.REF_CACHE_DIR, md5)).exists()


def test_status_records_the_verified_hash(downloader):
    source = SOURCE.read_text(encoding="utf-8")
    loop = source[source.index("def download_genomes") :]
    assert '["sha256_verified"] = genome["sha256"]' in loop
    json.dumps(downloader.download_status)  # still serialisable


START_SH = SOURCE.parent.parent / "pharmcat" / "start.sh"


def _verify_function() -> str:
    text = START_SH.read_text(encoding="utf-8")
    start = text.index("PHARMCAT_REFERENCE_TAR_SHA256=")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


@pytest.mark.parametrize("matches", [True, False])
def test_pharmcats_start_script_checks_the_zenodo_tarball(tmp_path, matches):
    """The pharmcat image fetches the same Zenodo tarball when its reference cache
    is empty; it checks the same pin before unpacking it."""
    import shutil
    import subprocess

    if shutil.which("sha256sum") is None:
        pytest.skip("sha256sum not available")
    function = _verify_function()
    assert PINNED["pharmcat_grch38"] in function
    body = b"a tarball"
    (tmp_path / "GRCh38_reference_fasta.tar").write_bytes(body)
    if matches:
        function = function.replace(
            PINNED["pharmcat_grch38"], hashlib.sha256(body).hexdigest()
        )
    result = subprocess.run(
        ["sh", "-c", function + "\n_verify_reference_tar"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) is matches, result.stdout
    assert (tmp_path / "GRCh38_reference_fasta.tar").exists() is matches
    if not matches:
        assert "does not match its pinned SHA-256" in result.stdout


def test_ci_checks_the_zenodo_tarball_too():
    ci = (SOURCE.parent.parent.parent / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert (
        f'echo "{PINNED["pharmcat_grch38"]}  $RUNNER_TEMP/GRCh38_reference_fasta.tar"'
        in ci
    )
    assert ci.index("sha256sum -c -") < ci.index(
        'tar -xf "$RUNNER_TEMP/GRCh38_reference_fasta.tar"'
    )
