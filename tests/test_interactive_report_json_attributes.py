"""The interactive report's JSON data attributes must survive an HTML parser.

static/js/pgx-report.js reads data-organized-recommendations and every
data-literature-references with JSON.parse. Both were written as
"{{ ...|tojson|safe }}": tojson leaves double quotes alone, so the attribute ended
at the JSON's first quote and the browser handed the script "[" (seen on a live
report as "Unexpected end of JSON input" at pgx-report.js:39). The throw comes
before the script draws the gene/phenotype chart, the recommendation chart, the
network view and the gene info section, so every report with literature
references lost all four.

html.parser follows the same attribute rules a browser does, so the values it
returns are what the script would see.
"""

import json
from html.parser import HTMLParser

REFS = [
    'Relling MV, et al. "CPIC guideline" for thiopurines. PMID 30447069.',
    "O'Donnell's <review> & notes",
]
ORGANIZED = [{"drug": "codeine", "note": 'say "no"'}]


def _render():
    from app.reports.generator import env

    return env.get_template("interactive_report.html").render(
        diplotypes=[],
        recommendations=[
            {
                "drug": "mercaptopurine",
                "gene": "TPMT",
                "recommendation": "Start with normal starting dose.",
                "classification": "Strong",
                "literature_references": REFS,
            },
            {
                "drug": "codeine",
                "gene": "CYP2D6",
                "recommendation": "Use label-recommended dosing.",
                "classification": "Strong",
                "literature_references": [],
            },
        ],
        gene_drug_recommendations=[],
        organized_recommendations=ORGANIZED,
        patient_id="test-patient",
        report_id="test-report",
        report_date="2026-10-05",
        organization="ZaroPGx",
        disclaimer="",
    )


class _DataAttributes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.organized = None
        self.literature = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == "pgxData":
            self.organized = attrs.get("data-organized-recommendations")
        if attrs.get("class") == "recommendation-data":
            self.literature.append(attrs.get("data-literature-references"))


def _parsed():
    parser = _DataAttributes()
    parser.feed(_render())
    return parser


def test_organized_recommendations_round_trip():
    assert json.loads(_parsed().organized) == ORGANIZED


def test_literature_references_round_trip():
    assert [json.loads(v) for v in _parsed().literature] == [REFS, []]
