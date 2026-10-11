"""The app's pages and reports load nothing from third-party servers.

The upload page took Bootstrap and Bootstrap Icons from jsDelivr, and the interactive
report Chart.js (unpinned: whatever jsDelivr called latest that day) and D3 from
jsDelivr and d3js.org. Every page view and every opened report therefore told those
hosts that someone was using a pharmacogenomics instance, and a report opened offline
or from the results page's ZIP drew no charts. Its own pgx-report.js was linked as
/static/js/pgx-report.js, which a downloaded copy cannot reach either.

The libraries are now in app/static/vendor, byte for byte what the npm registry
publishes for the pinned versions (each tarball checked against the registry's
sha512 integrity before extraction), and the interactive report carries its three
scripts inside the file.
"""

import hashlib
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SECRET_KEY", "pytest-secret-key-not-for-production")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://pytest:pytest@localhost:5432/pytest",
)

REPO = Path(__file__).resolve().parent.parent
VENDOR = REPO / "app" / "static" / "vendor"

# sha256 of each vendored file as published on npm (chart.js 4.5.1, d3 7.9.0,
# bootstrap 5.3.2, bootstrap-icons 1.11.1). A changed file means a changed library:
# update the version in the templates' comments and here together.
EXPECTED_SHA256 = {
    "chartjs/chart.umd.min.js": "48444a82d4edcb5bec0f1965faacdde18d9c17db3063d042abada2f705c9f54a",
    "d3/d3.min.js": "f2094bbf6141b359722c4fe454eb6c4b0f0e42cc10cc7af921fc158fceb86539",
    "bootstrap/bootstrap.min.css": "3017df4a76db5f01c2b99b603d88b03106df13bcfe18e67b7c13c2341d3a67df",
    "bootstrap/bootstrap.bundle.min.js": "82f64f62bb03c1bc1824b0f9c9e05f70dba33e146818e63cdf5c306c8cf3dedd",
    "bootstrap-icons/bootstrap-icons.css": "bb6fd8cd85394cb367e8ac58e47292f2d68eb288fa12fab68e65430a5ddfce48",
    "bootstrap-icons/fonts/bootstrap-icons.woff2": "bacd70afda7da1deac2bbd49b5717a4dd133bcd59c379525d705b8492f678e95",
    "bootstrap-icons/fonts/bootstrap-icons.woff": "4d4572ef314e1b734cdd6485f913b0396d81bedf4d216a47cfde0cdf32a9316e",
}
LICENCES = [
    "chartjs/LICENSE.md",
    "d3/LICENSE",
    "bootstrap/LICENSE",
    "bootstrap-icons/LICENSE",
]

# A script, stylesheet or font fetched from another host. Plain links (<a href>) to
# guidelines and documentation are fine: following one is the reader's choice.
EXTERNAL_LOAD = re.compile(
    r"<script\b[^>]*\bsrc\s*=\s*[\"']?(?:https?:)?//"
    r"|<link\b[^>]*\bhref\s*=\s*[\"']?(?:https?:)?//"
    r"|@import\s+(?:url\()?\s*[\"']?(?:https?:)?//"
    r"|url\(\s*[\"']?(?:https?:)?//",
    re.IGNORECASE,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pages():
    yield from (REPO / "app" / "templates").glob("*.html")
    yield from (REPO / "app" / "reports" / "templates").glob("*.html")
    yield from (REPO / "app" / "reports" / "templates").glob("*.css")
    yield from (REPO / "app" / "static").rglob("*.css")
    # main.py builds two pages as strings (the login and manual-access pages).
    yield REPO / "app" / "main.py"


@pytest.mark.parametrize(
    "path", sorted(_pages()), ids=lambda p: str(p.relative_to(REPO))
)
def test_no_page_loads_from_another_host(path):
    text = path.read_text(encoding="utf-8")
    hits = [m.group(0) for m in EXTERNAL_LOAD.finditer(text)]
    assert hits == [], f"{path.relative_to(REPO)} loads from another host: {hits}"


@pytest.mark.parametrize("name", sorted(EXPECTED_SHA256))
def test_vendored_library_is_present_and_unmodified(name):
    path = VENDOR / name
    assert path.is_file(), f"missing {path.relative_to(REPO)}"
    assert _sha256(path) == EXPECTED_SHA256[name]


@pytest.mark.parametrize("name", LICENCES)
def test_vendored_library_ships_its_licence(name):
    text = (VENDOR / name).read_text(encoding="utf-8")
    assert "Permission" in text and "copyright" in text.lower()


def test_icon_fonts_resolve_next_to_the_stylesheet():
    css = (VENDOR / "bootstrap-icons" / "bootstrap-icons.css").read_text("utf-8")
    fonts = re.findall(r'url\("\./(fonts/[^"?]+)', css)
    assert fonts, "bootstrap-icons.css names no font files"
    for font in fonts:
        assert (VENDOR / "bootstrap-icons" / font).is_file(), font


def _render_interactive():
    from app.reports.generator import env

    return env.get_template("interactive_report.html").render(
        diplotypes=[],
        recommendations=[],
        gene_drug_recommendations=[],
        organized_recommendations=[],
        patient_id="test-patient",
        report_id="test-report",
        report_date="2026-10-11",
        organization="ZaroPGx",
        disclaimer="",
    )


def test_interactive_report_carries_its_scripts():
    html = _render_interactive()

    assert re.search(r"<script\b[^>]*\bsrc\s*=", html, re.IGNORECASE) is None
    assert "Chart.js v4.5.1" in html
    assert "https://d3js.org v7.9.0" in html
    pgx_report = (REPO / "app" / "static" / "js" / "pgx-report.js").read_text(
        encoding="utf-8"
    )
    assert pgx_report.strip()[:200] in html
    # Every element the libraries open is closed where the template meant it to be:
    # an unescaped "</script" inside one would close it early.
    assert html.lower().count("<script") == html.lower().count("</script>")


def test_inline_script_cannot_close_its_element(tmp_path, monkeypatch):
    import app.reports.generator as generator

    (tmp_path / "lib.js").write_text(
        'var a = "</script><b>"; var b = "</SCRIPT>";', encoding="utf-8"
    )
    monkeypatch.setattr(generator, "_STATIC_DIR", str(tmp_path))
    monkeypatch.setitem(generator._INLINE_SCRIPTS, "lib", "lib.js")
    monkeypatch.setattr(generator, "_inline_script_cache", {})

    source = generator.inline_script("lib")

    assert "</script" not in source.lower()
    assert source == 'var a = "<\\/script><b>"; var b = "<\\/SCRIPT>";'


@pytest.fixture()
def client():
    import app.main as main

    main.app.router.on_startup.clear()
    main.app.router.on_shutdown.clear()
    return TestClient(main.app)


@pytest.mark.parametrize(
    "path",
    [
        "vendor/bootstrap/bootstrap.min.css",
        "vendor/bootstrap/bootstrap.bundle.min.js",
        "vendor/bootstrap-icons/bootstrap-icons.css",
    ],
)
def test_upload_page_loads_vendored_assets(client, path):
    html = (REPO / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    assert f"static_url('{path}')" in html
    resp = client.get(f"/static/{path}")
    assert resp.status_code == 200
    assert resp.content == (REPO / "app" / "static" / path).read_bytes()


def test_icon_font_is_served_where_its_stylesheet_points(client):
    # bootstrap-icons.css asks for ./fonts/... relative to its own URL.
    path = "vendor/bootstrap-icons/fonts/bootstrap-icons.woff2"
    resp = client.get(f"/static/{path}")
    assert resp.status_code == 200
    assert resp.content == (REPO / "app" / "static" / path).read_bytes()
