"""The web page, driven in a real browser: upload -> progress -> report, both themes.

Step (2) of the front-end harness plan in docs/to-do.md. Everything else in the suite
reads the page's source or runs its inline script under Node; nothing computed a
style or clicked anything, so three bugs of 2026-08-29 passed 1700 tests:
  - the PharmCAT popup's labels, white on its white card in the dark theme -- caught
    here by measuring every popup text's contrast in both themes (with the 08-29 CSS
    put back in the browser, the labels measure 1.22:1);
  - the progress bar frozen for a whole step -- here the bar must move while the job
    runs and finish at 100;
  - completed jobs deleting their own reports -- here every link the finished page
    offers must answer 200 with a body.

The job goes through the page the way a person sends it: the file input, the Upload
button, the stage toggles' defaults. Screenshots land in e2e-logs/ui/ on failure,
which CI uploads.

Needs Playwright's Chromium: `uv run playwright install chromium`.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
VCF = REPO / "test_data" / "pharmcat.example.vcf"
ARTIFACTS = REPO / "e2e-logs" / "ui"
JOB_TIMEOUT_S = 45 * 60

# WCAG 2.x AA: 4.5:1, or 3:1 for large text (24 px, or 18.66 px at weight 700+).
MIN_CONTRAST = 4.5
MIN_CONTRAST_LARGE = 3.0

# Every visible text under `root`, with its colour composited onto the background it
# actually sits on: the first opaque background up the tree, with any translucent
# ones in between laid over it. Bootstrap's dark theme writes translucent colours
# (rgba(222, 226, 230, 0.75) for muted text), so alpha is not optional.
TEXT_CONTRAST_JS = r"""
(root) => {
  const parse = (c) => {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const over = (top, under) =>
    [0, 1, 2].map(i => top[i] * top[3] + under[i] * (1 - top[3]));
  const luminance = (rgb) => {
    const f = (v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
  };
  const background = (el) => {
    const layers = [];
    for (let e = el; e; e = e.parentElement) {
      const c = parse(getComputedStyle(e).backgroundColor);
      if (c && c[3] > 0) { layers.push(c); if (c[3] >= 1) break; }
    }
    return layers.reverse().reduce((under, c) => over(c, under), [255, 255, 255]);
  };
  const out = [];
  const seen = new Set();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const text = walker.currentNode.textContent.trim();
    const el = walker.currentNode.parentElement;
    if (!text || seen.has(el)) continue;
    seen.add(el);
    const cs = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    if (cs.visibility === 'hidden' || box.width === 0 || box.height === 0) continue;
    const bg = background(el);
    const fg = over(parse(cs.color), bg);
    const [hi, lo] = [luminance(fg), luminance(bg)].sort((a, b) => b - a);
    out.push({
      text: text.slice(0, 60),
      ratio: (hi + 0.05) / (lo + 0.05),
      size: parseFloat(cs.fontSize),
      weight: parseInt(cs.fontWeight, 10) || 400,
      color: cs.color,
      background: 'rgb(' + bg.map(Math.round).join(', ') + ')',
    });
  }
  return out;
}
"""


def _sync_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # e2e was asked for, so a missing browser is a failure
        pytest.fail("playwright is not installed: uv sync --extra dev")
    return sync_playwright


def _low_contrast(texts: list[dict]) -> list[str]:
    low = []
    for t in texts:
        large = t["size"] >= 24 or (t["size"] >= 18.66 and t["weight"] >= 700)
        if t["ratio"] < (MIN_CONTRAST_LARGE if large else MIN_CONTRAST):
            low.append(
                f"{t['ratio']:.2f}:1 {t['text']!r} ({t['color']} on {t['background']})"
            )
    return low


def _theme(page) -> str:
    return page.evaluate("document.documentElement.getAttribute('data-bs-theme')")


def _popup_contrast(page) -> dict[str, list[str]]:
    """Open each stage's popup in turn; its texts that fall short, per stage."""
    failures = {}
    stages = page.locator(".stage-indicator")
    assert stages.count() >= 5, "the stage indicators are missing"
    for i in range(stages.count()):
        stage = stages.nth(i)
        stage.click()
        popup = stage.locator(".stage-popup")
        name = stage.get_attribute("id") or f"stage {i}"
        assert popup.is_visible(), f"{name}: clicking it did not open its popup"
        texts = popup.evaluate(TEXT_CONTRAST_JS)
        assert texts, f"{name}: its popup shows no text"
        low = _low_contrast(texts)
        if low:
            failures[name] = low
    page.evaluate("document.body.click()")  # a click outside closes the last one
    return failures


def _wait_for_result(page, deadline: float) -> list[int]:
    """Sample the progress bar until the page shows a result; the values seen."""
    seen = []
    results = page.locator("#analysisResults")
    while time.time() < deadline:
        value = page.locator("#progressBar").get_attribute("aria-valuenow")
        if value is not None and (not seen or seen[-1] != int(float(value))):
            seen.append(int(float(value)))
        if results.is_visible():
            text = results.inner_text()
            assert "Analysis Failed" not in text, text
            if "Analysis Complete" in text:
                return seen
        log = page.locator("#logMessages").inner_text()
        assert "Workflow error" not in log, log[-2000:]
        page.wait_for_timeout(1000)
    pytest.fail(f"no result within {JOB_TIMEOUT_S // 60} min; bar values {seen}")


@pytest.mark.e2e
def test_upload_progress_and_report_in_both_themes(e2e_client, e2e_base_url):
    assert VCF.is_file(), f"missing dataset {VCF}"
    errors: list[str] = []

    with _sync_playwright()() as pw:
        browser = pw.chromium.launch()
        try:
            # A browser that prefers dark, with nothing stored, opens dark.
            dark_ctx = browser.new_context(color_scheme="dark")
            page = dark_ctx.new_page()
            page.goto(e2e_base_url + "/", wait_until="networkidle")
            assert _theme(page) == "dark"
            dark_ctx.close()

            ctx = browser.new_context(
                color_scheme="light", viewport={"width": 1280, "height": 900}
            )
            page = ctx.new_page()
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            try:
                page.goto(e2e_base_url + "/", wait_until="networkidle")
                assert page.locator("#uploadForm").is_visible()
                assert _theme(page) == "light"
                low = {"light": _popup_contrast(page)}

                # The toggle flips the theme, and the choice outlives a reload.
                page.locator("#themeToggle").click()
                assert _theme(page) == "dark"
                page.reload(wait_until="networkidle")
                assert _theme(page) == "dark"
                low["dark"] = _popup_contrast(page)
                assert not any(low.values()), f"popup text below AA contrast: {low}"

                page.locator("#genomicFile").set_input_files(str(VCF))
                page.locator("#sampleIdentifier").fill("e2e-ui-smoke")
                page.locator("#uploadButton").click()
                page.locator("#progressContainer").wait_for(
                    state="visible", timeout=120_000
                )
                seen = _wait_for_result(page, time.time() + JOB_TIMEOUT_S)
                # A bar that sat still and jumped to 100 at the end is the 08-29 bug.
                moving = [v for v in seen if 0 < v < 100]
                assert len(set(moving)) >= 2, f"the bar did not move: {seen}"
                page.wait_for_function(
                    "document.getElementById('progressBar')"
                    ".getAttribute('aria-valuenow') === '100'",
                    timeout=30_000,
                )

                results = page.locator("#analysisResults")
                low = {"dark": _low_contrast(results.evaluate(TEXT_CONTRAST_JS))}
                page.locator("#themeToggle").click()
                assert _theme(page) == "light"
                low["light"] = _low_contrast(results.evaluate(TEXT_CONTRAST_JS))
                assert not any(low.values()), f"result text below AA contrast: {low}"

                links = results.locator("a[href]").evaluate_all(
                    "els => els.map(a => a.getAttribute('href'))"
                )
                assert links, results.inner_text()
                broken = []
                for link in links:
                    resp = e2e_client.get(link)
                    if resp.status_code != 200 or not resp.content:
                        broken.append(f"{resp.status_code} {link}")
                assert not broken, f"report links that do not resolve: {broken}"
                assert not errors, f"uncaught errors in the page: {errors}"
            except BaseException:
                ARTIFACTS.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(ARTIFACTS / "failure.png"), full_page=True)
                raise
            finally:
                ctx.close()
        finally:
            browser.close()
