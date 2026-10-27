"""A gene set the configuration does not define is a fault, not every gene.

``GeneConfig.get_gene_set`` used to answer a name it did not know by logging
"Gene set '...' not found, returning all genes" and doing exactly that. It needed
no typo to reach: ``config/genes.json`` is a bind mount (compose.yml,
``./config:/app/config``), so an install without it falls back to
``_get_fallback_config()``, which defines only "all" and "core". Asking that
fallback for "pharmcat_can_call" -- which ``pharmcat_outside_call_genes`` does on
every alignment run -- returned all 91 genes, so every gene looked like one
PharmCAT calls itself and PharmCAT was handed no outside call at all, CYP2D6
included, in a run that otherwise looked normal.

Sets and categories were confused too. They are different things in genes.json and
only partly overlap, and /genotype tested a requested name against the *categories*
before asking ``get_gene_set`` for it: a category ("phase2", "transporters",
"other_enzymes", "receptors", "blood_clotting", "other") genotyped all 91 genes
instead of its own, and a set that is not also a category
("neuropsychopharmacogenes") was refused as an unsupported gene.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "docker" / "pypgx" / "pypgx_wrapper.py"
CONFIG = ROOT / "config" / "genes.json"


def _gene_config_class():
    """The wrapper's GeneConfig, without importing it (PyPGx and FastAPI are not in
    the test environment)."""
    tree = ast.parse(WRAPPER.read_text(encoding="utf-8"))
    (node,) = [
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "GeneConfig"
    ]
    namespace: Dict[str, Any] = {
        "Path": Path,
        "json": json,
        "logger": logging.getLogger("pypgx-wrapper-test"),
        "Any": Any,
        "Dict": Dict,
        "List": List,
        "Optional": Optional,
    }
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), str(WRAPPER), "exec"),
        namespace,
    )
    return namespace["GeneConfig"]


GeneConfig = _gene_config_class()
SETS = json.loads(CONFIG.read_text(encoding="utf-8"))["sets"]
CATEGORIES = json.loads(CONFIG.read_text(encoding="utf-8"))["categories"]


def _config(**overrides):
    """A GeneConfig reading the repository's own genes.json, or a given config."""
    config = GeneConfig(str(CONFIG))
    if overrides:
        config._config = overrides.get("config")
    return config


# --------------------------------------------------------------------------
# The sets the wrapper asks for by name
# --------------------------------------------------------------------------


def _named_in_source():
    """Every literal name the wrapper passes to get_gene_set."""
    return set(
        re.findall(
            r"get_gene_set\(\s*['\"]([a-z_0-9]+)['\"]",
            WRAPPER.read_text(encoding="utf-8"),
        )
    )


def test_every_set_the_wrapper_names_is_in_the_configuration():
    """Each is now a hard failure if it goes missing, so each must be there."""
    named = _named_in_source()
    assert named, "no literal gene set in the wrapper: has the call shape changed?"
    assert named <= set(SETS) | {"all"}, sorted(named - set(SETS) - {"all"})


def test_the_sets_the_report_depends_on_are_all_defined():
    for name in ("all", "pypgx", "pypgx_minus_pharmcat", "pharmcat_can_call"):
        assert name in SETS


# --------------------------------------------------------------------------
# An unknown name
# --------------------------------------------------------------------------


def test_an_unknown_set_raises_and_says_what_there_is():
    with pytest.raises(KeyError) as raised:
        _config().get_gene_set("pharmcat_can_cal")
    message = str(raised.value)
    assert "pharmcat_can_cal" in message
    assert str(CONFIG) in message
    assert "pharmcat_can_call" in message  # the sets it does define


def test_an_unknown_set_does_not_come_back_as_every_gene():
    config = _config()
    every = config.get_gene_set("all")
    assert len(every) > 80
    for name in ("", "not_a_set", "PHARMCAT_CAN_CALL", "phase2"):
        with pytest.raises(KeyError):
            config.get_gene_set(name)


def test_the_fallback_configuration_refuses_the_sets_it_does_not_have():
    """The regression: a deployment with no config/genes.json mounted. Asking for
    pharmcat_can_call there returned all 91 genes, so pharmcat_outside_call_genes
    found every gene "PharmCAT's own" and sent PharmCAT nothing."""
    config = GeneConfig("/nonexistent/genes.json")
    fallback = config.load_config()
    assert set(fallback["sets"]) == {"all", "core"}
    assert len(config.get_gene_set("all")) > 80
    assert config.get_gene_set("core")
    for name in ("pharmcat_can_call", "pypgx", "pypgx_minus_pharmcat"):
        with pytest.raises(KeyError):
            config.get_gene_set(name)


def test_a_set_may_be_a_bare_list_or_carry_its_genes_under_a_key():
    config = _config(
        config={"sets": {"a": ["CYP2D6"], "b": {"count": 1, "genes": ["TPMT"]}}}
    )
    assert config.get_gene_set("a") == ["CYP2D6"]
    assert config.get_gene_set("b") == ["TPMT"]


def test_a_set_is_copied_so_a_caller_cannot_edit_the_configuration():
    config = _config()
    first = config.get_gene_set("pharmcat_can_call")
    first.append("NOT_A_GENE")
    assert "NOT_A_GENE" not in config.get_gene_set("pharmcat_can_call")


# --------------------------------------------------------------------------
# Sets and categories
# --------------------------------------------------------------------------


def test_sets_and_categories_are_not_the_same_names():
    """What made the request path wrong; if they ever coincide, this test says so
    and resolve_gene_selection's set-first order is what decides."""
    assert set(CATEGORIES) - set(SETS), "categories that are not sets"
    assert set(SETS) - set(CATEGORIES), "sets that are not categories"


@pytest.mark.parametrize("name", sorted(set(CATEGORIES) - set(SETS)))
def test_a_category_resolves_to_its_own_genes_not_to_every_gene(name):
    genes = _config().resolve_gene_selection(name)
    assert genes == list(CATEGORIES[name]["genes"])
    assert len(genes) < len(SETS["all"])


@pytest.mark.parametrize("name", sorted(set(SETS) - set(CATEGORIES)))
def test_a_set_that_is_not_a_category_still_resolves(name):
    assert _config().resolve_gene_selection(name) == list(SETS[name])


def test_a_name_that_is_neither_is_left_to_be_read_as_genes():
    assert _config().resolve_gene_selection("CYP2D6") is None
    assert _config().resolve_gene_selection("not_a_set_or_category") is None


def test_an_empty_set_is_still_that_set():
    config = _config(
        config={"sets": {"empty": []}, "categories": {"empty": {"genes": ["TPMT"]}}}
    )
    assert config.resolve_gene_selection("empty") == []


def test_a_set_wins_over_a_category_of_the_same_name():
    config = _config(
        config={
            "sets": {"shared": ["CYP2D6"]},
            "categories": {"shared": {"genes": ["TPMT"]}},
        }
    )
    assert config.resolve_gene_selection("shared") == ["CYP2D6"]


# --------------------------------------------------------------------------
# The request path uses it
# --------------------------------------------------------------------------


def test_genotype_resolves_a_requested_name_rather_than_testing_the_categories():
    source = WRAPPER.read_text(encoding="utf-8")
    assert "available_sets = gene_config.get_categories().keys()" not in source
    assert source.count("gene_config.resolve_gene_selection(") == 2
    # An explicit gene_set= that names nothing is a 400, not every gene.
    genotype = source[source.index("elif gene_set:") :][:600]
    assert "resolve_gene_selection" in genotype
    assert "status_code=400" in genotype
