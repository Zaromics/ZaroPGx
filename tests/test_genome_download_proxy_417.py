"""417 — unused genome-downloader app proxies must be removed."""

from pathlib import Path

MAIN = Path("app/main.py")


def test_genome_download_proxy_routes_removed():
    text = MAIN.read_text(encoding="utf-8")
    assert "/api/genome-download-status" not in text
    assert "/api/start-genome-download" not in text
    assert "async def genome_download_status" not in text
    assert "async def start_genome_download" not in text
