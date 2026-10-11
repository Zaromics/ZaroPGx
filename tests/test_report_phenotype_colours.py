"""Every phenotype badge's colour comes from one place, and function phenotypes
get one.

"Decreased Function" (SLCO1B1, ABCG2) matched none of the badge rules, which knew
only metabolizer words, and printed in the neutral grey of "Indeterminate". CPIC's
data ranks it with Intermediate Metabolizer: both are "Abnormal/Priority" EHR
results. So decreased function takes the intermediate amber, poor function the
poor red, increased function the rapid blue. The three copies of the rule in the
two templates are now one filter.
"""

from pathlib import Path

import pytest

from app.reports.generator import phenotype_badge_class

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "reports" / "templates"


@pytest.mark.parametrize(
    "phenotype, css_class",
    [
        ("Normal Metabolizer", "normal-metabolizer"),
        ("Normal Function", "normal-metabolizer"),
        ("Intermediate Metabolizer", "intermediate-metabolizer"),
        ("Likely Intermediate Metabolizer", "intermediate-metabolizer"),
        ("Decreased Function", "decreased-function"),
        ("Possible Decreased Function", "decreased-function"),
        ("Poor Metabolizer", "poor-metabolizer"),
        ("Poor Function", "poor-metabolizer"),
        ("Rapid Metabolizer", "rapid-metabolizer"),
        ("Ultrarapid Metabolizer", "rapid-metabolizer"),
        ("Increased Function", "increased-function"),
        ("Possibly Wild Type", "possibly-wild-type"),
        ("Likely Wild Type", "likely-wild-type"),
        # Risk, not function: MT-RNR1's labels stay neutral.
        ("increased risk of aminoglycoside-induced hearing loss", ""),
        ("Indeterminate", ""),
        ("Uncertain Susceptibility", ""),
        (None, ""),
    ],
)
def test_each_phenotype_gets_its_colour(phenotype, css_class):
    assert phenotype_badge_class(phenotype) == css_class


@pytest.mark.parametrize("name", ["report_template.html", "interactive_report.html"])
def test_the_templates_use_the_one_rule(name):
    template = (TEMPLATES / name).read_text(encoding="utf-8")
    assert "phenotype_badge_class" in template
    assert "intermediate-metabolizer{%" not in template


@pytest.mark.parametrize(
    "name", ["report_template.html", "interactive_report.html", "style.css"]
)
def test_every_stylesheet_colours_the_function_classes(name):
    css = (TEMPLATES / name).read_text(encoding="utf-8")
    for css_class in (".decreased-function {", ".increased-function {"):
        assert css_class in css, (name, css_class)
