"""Every step the workflow registry can mint must light a stage glyph.

``GlyphManager.updateStageIndicators`` (app/templates/index.html) begins by
calling ``resetAllStages()`` and then looks the incoming ``step_name`` up in
``GlyphManager.stepMapping``. A step name that is absent from that map matches
nothing -- not on the exact pass, not on the ``includes()`` partial pass -- so the
function returns having only *cleared* the row. The browser logs
``No matching step found for: <name>`` to a console nobody is reading, and every
glyph sits dark for the whole step. To a user that is indistinguishable from a
stalled job, and it lasts exactly as long as the step does.

That is what happened when ``liftover`` was added: the step was registered in
``app/services/workflow_registry.py`` (without which its status updates 404) and
wired into ``main.nf``, and the glyph row was simply never told. A GRCh37 upload
therefore went dark through the one stage that was new.

The mapping is a plain JS object literal, so this test parses it out of the
template rather than asserting on a hand-copied list -- a copy would rot the
moment either side changed. The registry is the authority for what names exist.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.services.workflow_registry import list_recipes

INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"


def _step_mapping() -> dict[str, str]:
    """The ``stepMapping`` object literal from index.html, as a dict."""
    source = INDEX_HTML.read_text(encoding="utf-8")
    match = re.search(r"stepMapping:\s*\{(.*?)\n\s*\}", source, re.S)
    assert match is not None, "stepMapping literal not found in index.html"
    body = match.group(1)
    # Drop // comments and trailing commas, then read it as a Python dict --
    # the literal is quoted 'key': 'value' pairs, which ast can parse directly.
    body = re.sub(r"//[^\n]*", "", body)
    body = re.sub(r",(\s*)$", r"\1", body.strip())
    mapping = ast.literal_eval("{" + body + "}")
    assert isinstance(mapping, dict) and mapping
    return mapping


def _registry_step_names() -> set[str]:
    return {
        template.step_name
        for recipe in list_recipes()
        for template in recipe.step_templates
    }


def test_the_mapping_parses_and_is_not_empty():
    """Guard the parser itself: a silent parse failure would pass everything."""
    mapping = _step_mapping()
    assert "header_analysis" in mapping
    assert all(v.startswith("stage") for v in mapping.values()), mapping


@pytest.mark.parametrize("step_name", sorted(_registry_step_names()))
def test_every_registry_step_has_a_glyph(step_name):
    mapping = _step_mapping()
    assert step_name in mapping, (
        f"workflow_registry mints step {step_name!r} but GlyphManager.stepMapping "
        f"has no entry for it: the stage row will go dark for the whole step. "
        f"Add it to app/templates/index.html."
    )


def test_liftover_lights_the_gatk_glyph():
    """The specific case this module was written for.

    Picard LiftoverVcf runs inside the gatk-api container, and the backend already
    groups it that way (``app/services/workflow_stages.py`` maps ``liftover`` to
    ``WorkflowStage.GATK``). The two must not drift apart.
    """
    from app.services.workflow_stages import STEP_TO_STAGE, WorkflowStage

    assert _step_mapping()["liftover"] == "stageGATK"
    assert STEP_TO_STAGE["liftover"] is WorkflowStage.GATK


def test_every_mapped_glyph_id_exists_in_the_markup():
    """A mapping pointing at a missing element is the same dark row, one step later."""
    source = INDEX_HTML.read_text(encoding="utf-8")
    ids = set(re.findall(r'id="(stage[A-Za-z0-9]+)"', source))
    missing = {
        step: stage for step, stage in _step_mapping().items() if stage not in ids
    }
    assert not missing, f"stepMapping points at elements that do not exist: {missing}"
