"""The interactive report's drug-gene network shows what changes prescribing.

It used to draw every gene and every drug with guidance -- 217 nodes on 30x
NA12878 -- in a live force layout inside a 300 px box, drawn while its tab was
hidden, so 213 of them landed off-canvas. It now draws only the drugs whose
recommendation changes prescribing for this genotype, as PharmCAT's annotations
tag it (a different drug, a dose change, other prescribing guidance) where a gene
behind it is not a risk screen's all-clear, around the genes the change comes from.

The tags are measured here on PharmCAT's own example output; the network's data
step runs under Node against static/js/pgx-report.js itself.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.reports.evidence import change_genes, prescribing_changes

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "app" / "static" / "js" / "pgx-report.js"
EXAMPLE = REPO / "test_data" / "pharmcat.example.report.json"


def _annotations(report):
    for drugs in report["drugs"].values():
        for name, drug in drugs.items():
            for guideline in drug.get("guidelines", []):
                for annotation in guideline.get("annotations", []):
                    yield name, annotation


def test_prescribing_changes_reads_the_annotation_tags():
    assert prescribing_changes(
        {
            "dosingInformation": True,
            "alternateDrugAvailable": True,
            "otherPrescribingGuidance": False,
        }
    ) == ["alternative drug", "dose change"]
    untagged = {
        "dosingInformation": False,
        "alternateDrugAvailable": False,
        "otherPrescribingGuidance": False,
    }
    assert prescribing_changes(untagged) == []
    assert prescribing_changes(None) == []
    # Only a real true counts: a string is not a tag.
    assert prescribing_changes({"dosingInformation": "false"}) == []


def _annotation(text, tags, *diplotypes):
    """A PharmCAT 3 annotation: lookupKey names the genes, genotypes their calls."""
    return {
        "drugRecommendation": text,
        "lookupKey": [
            {gene: (phenotypes or [""])[0] for gene, phenotypes in diplotypes}
        ],
        "genotypes": [
            {
                "diplotypes": [
                    {"gene": gene, "phenotypes": list(phenotypes)}
                    for gene, phenotypes in diplotypes
                ]
            }
        ],
        "dosingInformation": "D" in tags,
        "alternateDrugAvailable": "A" in tags,
        "otherPrescribingGuidance": "O" in tags,
    }


# 30x NA12878, PharmCAT 3.4: CPIC's advice for MT-RNR1 normal risk, tagged other
# guidance for its monitoring sentence, on each of the 11 aminoglycosides.
GENTAMICIN = _annotation(
    "Use aminoglycoside antibiotics at standard doses for the shortest feasible "
    "course with therapeutic dose monitoring. Evaluate regularly for hearing loss "
    "in line with local guidance.",
    "O",
    ("MT-RNR1", ["normal risk of aminoglycoside-induced hearing loss"]),
)
# FDA phenytoin: HLA-B screened negative, CYP2C9 an intermediate metabolizer.
PHENYTOIN = _annotation(
    "Patients who are intermediate or poor metabolizers of CYP2C9 substrates ...",
    "D",
    ("HLA-B", ["*15:02 negative", "*57:01 negative", "*58:01 negative"]),
    ("CYP2C9", ["Intermediate Metabolizer"]),
)


def test_a_risk_screen_s_all_clear_changes_nothing():
    """PharmCAT's tags say what a recommendation's text contains, not that the
    genotype changes anything; on an all-clear screen they mark a monitoring line."""
    assert prescribing_changes(GENTAMICIN) == []
    assert change_genes(GENTAMICIN) == []
    assert prescribing_changes(PHENYTOIN) == ["dose change"]
    assert change_genes(PHENYTOIN) == ["CYP2C9"]  # not the negative HLA-B screen


def test_a_metabolizer_s_normal_can_still_change_the_dose():
    """CPIC in PharmCAT's example: a CYP3A5 normal metabolizer starts tacrolimus at
    1.5 to 2 times the dose, and a CYP2C19 one may double a PPI. Read off its own
    annotations, every such dose change stays a change."""
    report = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    normal = {}
    for name, a in _annotations(report):
        for genotype in a.get("genotypes") or []:
            for d in genotype.get("diplotypes") or []:
                if d.get("gene") in ("CYP3A5", "CYP2C19") and d.get("phenotypes") == [
                    "Normal Metabolizer"
                ]:
                    if a.get("dosingInformation") is True:
                        normal[name] = prescribing_changes(a)
    assert {"tacrolimus", "omeprazole", "lansoprazole"} <= set(normal)
    assert all("dose change" in changes for changes in normal.values()), normal


def test_a_gene_without_a_phenotype_is_never_all_clear():
    """VKORC1 has no phenotype, so warfarin's dosing stays a change for it."""
    warfarin = _annotation(
        "Use the warfarin dosing algorithm.",
        "D",
        ("CYP2C9", ["Normal Metabolizer"]),
        ("VKORC1", [""]),
    )
    assert prescribing_changes(warfarin) == ["dose change"]
    assert change_genes(warfarin) == ["CYP2C9", "VKORC1"]
    # Nor does an annotation that names no gene lose its tags.
    assert prescribing_changes({"dosingInformation": True}) == ["dose change"]


def test_both_report_lanes_carry_the_tags():
    from app.pharmcat.pharmcat_client import normalize_pharmcat_results

    report = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data = normalize_pharmcat_results(report)["data"]
    # The annotation-derived groups (the genes' "Related drug" placeholders,
    # listed first, carry no recommendation of their own).
    entries = [
        r for d in data["drugRecommendations"] for r in d.get("recommendations", [])
    ]
    assert entries and all("prescribing_changes" in r for r in entries)
    assert any(r["prescribing_changes"] for r in entries)
    assert any(not r["prescribing_changes"] for r in entries)
    assert all(
        r["change_genes"] == ([] if not r["prescribing_changes"] else r["change_genes"])
        for r in entries
    )
    assert any(r["change_genes"] for r in entries)

    from app.services.pharmcat_data_service import PharmCATDataService

    service = PharmCATDataService.__new__(PharmCATDataService)
    grouped = service._transform_drug_recommendations_for_reports(
        [
            {
                "drug_name": name,
                "gene_symbol": gene,
                "drug_recommendation": a["drugRecommendation"],
                "classification": "Strong",
                "recommendation_data": a,
            }
            for name, gene, a in (
                ("phenytoin", "HLA-B", PHENYTOIN),
                ("gentamicin", "MT-RNR1", GENTAMICIN),
            )
        ]
    )
    by_drug = {g["drug"]: g["recommendations"][0] for g in grouped}
    assert by_drug["phenytoin"]["prescribing_changes"] == ["dose change"]
    assert by_drug["phenytoin"]["change_genes"] == ["CYP2C9"]
    assert by_drug["gentamicin"]["prescribing_changes"] == []
    assert by_drug["gentamicin"]["change_genes"] == []

    from app.reports.generator import map_recommendations_for_template

    mapped = {m["drug"]: m for m in map_recommendations_for_template(grouped)}
    assert mapped["phenytoin"]["prescribing_changes"] == ["dose change"]
    assert mapped["phenytoin"]["change_genes"] == ["CYP2C9"]


NETWORK = r"""
const fs = require('fs');
const vm = require('vm');
const context = { document: { addEventListener() {} }, console };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const [recs, dips] = JSON.parse(fs.readFileSync(0, 'utf8'));
context.recs = recs; context.dips = dips;
const net = vm.runInContext('actionableNetwork(recs, dips)', context);
process.stdout.write(JSON.stringify({
    genes: net.genes.map(g => [g.label, g.phenotype, g.drugs]),
    drugs: net.drugs.map(d => [d.label, d.genes]),
    links: net.links.map(l => [l.gene, l.drug, l.change, l.changes]),
}));
"""


def _network(recommendations, diplotypes):
    proc = subprocess.run(
        [shutil.which("node"), "-e", NETWORK, str(SCRIPT)],
        input=json.dumps([recommendations, diplotypes]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_network_holds_only_what_changes_prescribing():
    recs = [
        # Two guidelines for one pair: one line, coloured by the decisive change.
        {"drug": "codeine", "gene": "CYP2D6", "prescribingChanges": ["other guidance"]},
        {
            "drug": "codeine",
            "gene": "CYP2D6",
            "prescribingChanges": ["alternative drug"],
        },
        # One recommendation resting on two genes: a line to each.
        {
            "drug": "amitriptyline",
            "gene": "CYP2C19",
            "genes": ["CYP2C19", "CYP2D6"],
            "prescribingChanges": ["dose change"],
        },
        # Resting on two genes, changed by one (HLA-B screened negative).
        {
            "drug": "phenytoin",
            "gene": "HLA-B",
            "genes": ["HLA-B", "CYP2C9"],
            "changeGenes": ["CYP2C9"],
            "prescribingChanges": ["dose change"],
        },
        # Standard dose (CYP3A5 poor metabolizer, tacrolimus): not drawn.
        {"drug": "tacrolimus", "gene": "CYP3A5", "prescribingChanges": []},
        # No gene to hang it on.
        {"drug": "mystery", "gene": "Unknown", "prescribingChanges": ["dose change"]},
    ]
    dips = [
        {"gene": "CYP2D6", "phenotype": "Poor Metabolizer"},
        {"gene": "CYP2C19", "phenotype": "Intermediate Metabolizer"},
        {"gene": "CYP3A5", "phenotype": "Poor Metabolizer"},
        {"gene": "CYP2C9", "phenotype": "Intermediate Metabolizer"},
        {"gene": "HLA-B", "phenotype": "*15:02 negative"},
    ]
    net = _network(recs, dips)

    assert sorted(g[0] for g in net["genes"]) == ["CYP2C19", "CYP2C9", "CYP2D6"]
    assert sorted(d[0] for d in net["drugs"]) == [
        "amitriptyline",
        "codeine",
        "phenytoin",
    ]
    links = {(l[0], l[1]): (l[2], l[3]) for l in net["links"]}
    assert links[("CYP2D6", "codeine")] == (
        "alternative drug",
        ["alternative drug", "other guidance"],
    )
    assert ("CYP2C19", "amitriptyline") in links and len(links) == 4
    assert ("CYP2C9", "phenytoin") in links and ("HLA-B", "phenytoin") not in links
    assert dict((d[0], sorted(d[1])) for d in net["drugs"])["amitriptyline"] == [
        "CYP2C19",
        "CYP2D6",
    ]
    assert dict((g[0], g[1]) for g in net["genes"])["CYP2D6"] == "Poor Metabolizer"


def _with_label(annotation, gene, label):
    for genotype in annotation["genotypes"]:
        for diplotype in genotype["diplotypes"]:
            if diplotype["gene"] == gene:
                diplotype["label"] = label
    return annotation


def test_a_gene_without_a_call_is_not_a_hub_beside_one_with():
    """PharmCAT looks an uncalled gene up as "Unknown/Unknown". On an unphased
    variant-only VCF, phenytoin's guidance rests on HLA-B (not called) and CYP2C9:
    HLA-B was drawn as a hub labelled "None" beside the gene that set the dose."""
    phenytoin = _with_label(
        _annotation(
            "Reduce the starting dose ...",
            "D",
            ("HLA-B", []),
            ("CYP2C9", ["Intermediate Metabolizer"]),
        ),
        "HLA-B",
        "Unknown/Unknown",
    )
    assert change_genes(phenytoin) == ["CYP2C9"]
    assert prescribing_changes(phenytoin) == ["dose change"]  # still a change
    # With no called gene left, the guidance still has to hang somewhere.
    alone = _with_label(
        _annotation("Consider testing ...", "O", ("HLA-B", [])),
        "HLA-B",
        "Unknown/Unknown",
    )
    assert change_genes(alone) == ["HLA-B"]


GENE_COLOR = r"""
const fs = require('fs');
const vm = require('vm');
const context = { document: { addEventListener() {} }, console };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const phenotypes = JSON.parse(fs.readFileSync(0, 'utf8'));
context.phenotypes = phenotypes;
process.stdout.write(JSON.stringify(vm.runInContext('phenotypes.map(geneColor)', context)));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_hub_with_several_possible_phenotypes_takes_no_colour():
    """An unphased call's "Normal Function or ... Poor Function" matched "Poor"."""
    proc = subprocess.run(
        [shutil.which("node"), "-e", GENE_COLOR, str(SCRIPT)],
        input=json.dumps(
            [
                "Poor Metabolizer",
                "Normal Function or Decreased Function or Poor Function",
                "Intermediate Metabolizer or Indeterminate",
                "Normal Metabolizer",
            ]
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    poor, unresolved, unresolved2, normal = json.loads(proc.stdout)
    assert unresolved == unresolved2 == "#95a5a6"
    assert poor != unresolved and normal != unresolved


def test_a_missing_phenotype_is_never_printed_as_none():
    """The data attribute the network reads printed Python's None as text."""
    template = (
        REPO / "app" / "reports" / "templates" / "interactive_report.html"
    ).read_text(encoding="utf-8")
    assert 'data-phenotype="{{ d.phenotype }}"' not in template
    assert "data-phenotype=\"{{ d.phenotype or '' }}\"" in template
