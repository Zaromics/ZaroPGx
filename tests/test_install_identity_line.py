"""The About card says which ZaroPGx this is, and where it is running.

One muted line at the bottom of the documentation card, server-rendered:

    Platform: ZaroPGx 0.4.4 · docker · local · releases
    Platform: ZaroPGx 0.4.4 · St Mary's Genomics · docker · remote · releases
    Platform: ZaroPGx 0.4.4+a1b2c3d · … · releases        serving code the image lacks

Four facts, each its own segment, each only when it is true.

**`local` and `remote` describe the reading, not the deployment.** They come from the
request's own Host, which is the only thing the server actually knows: the same install
is local to the person at its console and remote to everyone else. A reverse proxy
passes its public name through, which is correctly `remote`.

**The version suffix is semver build metadata, not a phrase.** There is no accepted term
for a container serving code its image was not built from -- the mechanism is a bind
mount shadowing the image's files, and the field has no noun for it -- so the line
states the fact in the notation that already exists for it. A bare version is the
image; a version carrying a commit is that commit. The detail goes in the
element's title.

**The releases link is a link, not a version check.** Knowing whether a newer release
exists means asking an outside service, and this application makes no outbound request
on its own behalf: ``test_no_external_service_defaults.py`` exists because
``render_with_kroki`` nearly defaulted to the public kroki.io. The opt-in design is in
``docs/to-do.md``.
"""

from __future__ import annotations

import html as html_module
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
PAGE = REPO / "app" / "templates" / "index.html"


@pytest.fixture(scope="module")
def main_module():
    import app.main as main

    return main


@pytest.fixture(scope="module")
def client(main_module):
    return TestClient(main_module.app)


def _rendered(client, host="localhost") -> str:
    page = client.get("/", headers={"host": host}).text
    found = re.search(r'<p class="[^"]*install-identity"[^>]*>(.*?)</p>', page, re.S)
    assert found, "the Platform line is gone from the page"
    text = " ".join(re.sub(r"<[^>]+>", "", found.group(1)).split())
    return html_module.unescape(text)


def test_the_line_lives_in_the_documentation_card_not_the_footer():
    page = PAGE.read_text(encoding="utf-8")
    card = page.index("ZaroPGx Documentation")
    line = page.index("install-identity", card)
    footer = page.index('<div class="footer small">')
    assert (
        card < line < footer
    ), "the Platform line belongs at the bottom of the About card, before the footer"
    buttons = page.index('<a href="/api/docs"', card)
    assert buttons < line, "and below that card's buttons, as its last line"


def test_it_is_labelled_platform(client):
    assert _rendered(client).startswith("Platform: ZaroPGx ")


@pytest.mark.parametrize(
    "host,expected",
    [
        ("localhost", "local"),
        ("localhost:8765", "local"),
        ("127.0.0.1:8765", "local"),
        ("[::1]:8765", "local"),
        ("pgx.example.org", "remote"),
        ("10.0.0.7:8765", "remote"),
    ],
)
def test_local_and_remote_come_from_the_host_being_asked_for(client, host, expected):
    """The same install is local at its console and remote to everyone else."""
    rendered = _rendered(client, host)
    other = "remote" if expected == "local" else "local"
    assert f"· {expected}" in rendered, f"{host} should read {expected}: {rendered}"
    assert f"· {other}" not in rendered


def test_an_operator_appears_only_when_one_is_named(client, main_module, monkeypatch):
    monkeypatch.setattr(main_module, "OPERATOR", "")
    assert "St Mary" not in _rendered(client)
    monkeypatch.setattr(main_module, "OPERATOR", "St Mary's Genomics")
    assert "St Mary's Genomics" in _rendered(client), (
        "an unset operator must show nothing rather than a placeholder: a self-hosted "
        "deployment is not run by the people who wrote the software"
    )


def test_an_operator_name_cannot_inject_markup(client, main_module, monkeypatch):
    """OPERATOR is an env var, so it is the operator's text, not a visitor's --
    but it reaches the page as data and is escaped like any other."""
    monkeypatch.setattr(main_module, "OPERATOR", "<script>alert(1)</script>")
    page = client.get("/", headers={"host": "localhost"}).text
    element = re.search(r'<p class="[^"]*install-identity"[^>]*>(.*?)</p>', page, re.S)
    assert element, "the Platform line is gone"
    segment = element.group(1)
    assert "<script>alert(1)</script>" not in segment, "markup reached the page"
    assert "&lt;script&gt;" in segment, "it should be there, escaped"


def test_docker_is_named_only_when_this_is_a_container(
    client, main_module, monkeypatch
):
    monkeypatch.setattr(main_module, "_runs_in_a_container", lambda: True)
    assert "· docker" in _rendered(client)
    monkeypatch.setattr(main_module, "_runs_in_a_container", lambda: False)
    assert "docker" not in _rendered(client)


def test_the_version_carries_the_commit_only_when_it_is_not_the_image(main_module):
    served = main_module._version_served
    base = dict(main_module._BUILD_IDENTITY)
    try:
        for name, agrees in (("agreeing", True), ("one side unknown", None)):
            main_module._BUILD_IDENTITY = dict(
                base, version="9.9.9", agrees=agrees, code={"commit": "aaaaaaa1"}
            )
            assert served() == "9.9.9", f"{name} must print a bare version"

        main_module._BUILD_IDENTITY = dict(
            base, version="9.9.9", agrees=False, code={"commit": "bbbbbbb2222"}
        )
        assert served() == "9.9.9+bbbbbbb", "the running commit, short, after a +"

        main_module._BUILD_IDENTITY = dict(base, version="9.9.9", agrees=False, code={})
        assert served() == "9.9.9", "with no commit to name there is nothing to append"
    finally:
        main_module._BUILD_IDENTITY = base


def test_the_releases_link_is_a_link_and_not_a_check(client):
    page = PAGE.read_text(encoding="utf-8")
    assert "{{ source_url }}/releases" in page
    element = re.search(r'<p class="[^"]*install-identity"[^>]*>.*?</p>', page, re.S)
    assert element, "the Platform line is gone"
    assert "fetch(" not in element.group(
        0
    ), "an update check is opt-in; this line makes no request of its own"


def test_the_detail_is_in_the_title_and_not_in_the_line(client):
    page = PAGE.read_text(encoding="utf-8")
    opening = re.search(r'<p class="[^"]*install-identity"[^>]*>', page)  # the element
    assert opening and "build_detail" in opening.group(
        0
    ), "the explanation belongs in a title attribute, for whoever wants it"
    assert "build_detail" not in _rendered(client), "and not in the visible line"


def test_the_operator_is_empty_until_someone_sets_it():
    source = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    assert re.search(r'OPERATOR\s*=\s*os\.getenv\(\s*"ZAROPGX_OPERATOR",\s*""', source)
