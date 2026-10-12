"""Write the credits that every report draws from into the docs and the README.

app/reports/credits.py is the one list of what ZaroPGx uses: each report credits
the part its run used; docs/user/credits.md and the README's Acknowledgements list
all of it. Run this after changing the list; tests/test_credits_docs.py fails
while either file is out of date.

    python scripts/render_credits.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.reports.credits import (  # noqa: E402
    CREDITS,
    PLATFORM,
    TERMINOLOGY_NOTICES,
    Paper,
    ref_numbers,
)

DOCS_PAGE = ROOT / "docs" / "user" / "credits.md"
README = ROOT / "README.md"
README_START = "<!-- credits:start (scripts/render_credits.py writes this part) -->"
README_END = "<!-- credits:end -->"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _markdown(paper: Paper) -> str:
    """A reference in Markdown: the journal in italics, the DOI linked."""
    text = paper.text()
    if paper.source and not paper.publisher:
        text = text.replace(f" {paper.source}.", f" *{paper.source}*.", 1)
    if paper.doi:
        text = text.replace(
            f"doi:{paper.doi}", f"doi:[{paper.doi}](https://doi.org/{paper.doi})"
        )
    elif paper.url:
        text = text.replace(paper.url, f"<{paper.url}>")
    return text


def _numbered():
    """Every reference once, numbered in the order the credits cite them."""
    numbers, papers = {}, []
    for credit in CREDITS:
        for paper in credit.papers + tuple(p for _, p in credit.papers_when):
            if paper.key not in numbers:
                papers.append(paper)
                numbers[paper.key] = len(papers)
    return numbers, papers


def docs_page() -> str:
    numbers, papers = _numbered()

    def refs(credit):
        keys = [p.key for p in credit.papers + tuple(p for _, p in credit.papers_when)]
        return ref_numbers([numbers[k] for k in keys])

    lines = [
        "---",
        "title: Credits",
        "---",
        "",
        "# Credits and licences",
        "",
        "ZaroPGx is built on the software and data below, and we thank their authors "
        "and maintainers. Every report lists, under **Platform and Citations**, the "
        "parts its own run used, at the versions that ran, with these references. "
        "This page lists all of them. It is written by `scripts/render_credits.py` "
        "from `app/reports/credits.py`, the list the reports use.",
        "",
        "## Software",
        "",
        "| Software | Used for | Used when | Licence | Ref. |",
        "|---|---|---|---|---|",
    ]
    for credit in CREDITS:
        if credit.kind == "software":
            lines.append(
                f"| [{_cell(credit.name)}]({credit.url}) | {_cell(credit.used_for)} | "
                f"{_cell(credit.used_when)} | {_cell(credit.licence)} | {refs(credit)} |"
            )
    lines += [
        "",
        "## Data and knowledge",
        "",
        "| Source | Used for | Used when | Licence or terms | Ref. |",
        "|---|---|---|---|---|",
    ]
    for credit in CREDITS:
        if credit.kind == "data":
            lines.append(
                f"| [{_cell(credit.name)}]({credit.url}) | {_cell(credit.used_for)} | "
                f"{_cell(credit.used_when)} | {_cell(credit.licence)} | {refs(credit)} |"
            )
    lines += [
        "",
        "## The platform",
        "",
        "What runs the application, its database and its pages. None of it touches a "
        "call, so reports do not list it.",
        "",
        "| Component | Used for | Licence |",
        "|---|---|---|",
    ]
    for item in PLATFORM:
        lines.append(
            f"| [{_cell(item.name)}]({item.url}) | {_cell(item.used_for)} | "
            f"{_cell(item.licence)} |"
        )
    lines += ["", "## References", ""]
    for n, paper in enumerate(papers, 1):
        lines.append(f"{n}. {_markdown(paper)}")
    lines += [
        "",
        "## Terms of use",
        "",
        "Reports print the notices of the sources their run used. All of them:",
        "",
    ]
    for credit in CREDITS:
        if credit.notice:
            lines += [f"- **{credit.name}.** {credit.notice}", ""]
    lines += [
        "The FHIR export carries codes from these terminologies:",
        "",
    ]
    for name, notice in TERMINOLOGY_NOTICES:
        lines += [f"- **{name}.** {notice}", ""]
    lines += [
        "## Citing ZaroPGx",
        "",
        "`CITATION.cff` in the repository says how to cite ZaroPGx itself. Please cite "
        "the tools and data above as well: ZaroPGx's results are theirs.",
        "",
    ]
    return "\n".join(lines)


def readme_block() -> str:
    numbers, _ = _numbered()
    lines = [README_START, ""]
    for kind, title in (("software", "Software"), ("data", "Data and knowledge")):
        lines += [f"**{title}**", ""]
        for credit in CREDITS:
            if credit.kind != kind:
                continue
            lines.append(f"- [{credit.name}]({credit.url}): {credit.used_for}.")
            for paper in credit.papers + tuple(p for _, p in credit.papers_when):
                if paper.kind == "Software" and not paper.authors:
                    continue
                lines.append(f"  - {_markdown(paper)}")
        lines.append("")
    lines += [
        "Licences, terms of use and the platform's own components: "
        "[Credits](docs/user/credits.md).",
        "",
        README_END,
    ]
    return "\n".join(lines)


def readme(text: str) -> str:
    pattern = re.compile(re.escape(README_START) + r".*?" + re.escape(README_END), re.S)
    if not pattern.search(text):
        raise SystemExit(f"README.md has no {README_START!r} ... {README_END!r} block")
    return pattern.sub(lambda _: readme_block(), text)


def main() -> None:
    DOCS_PAGE.write_text(docs_page(), encoding="utf-8")
    README.write_text(readme(README.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"wrote {DOCS_PAGE.relative_to(ROOT)} and README.md")


if __name__ == "__main__":
    main()
