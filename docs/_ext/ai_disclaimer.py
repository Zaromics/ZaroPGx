"""Notice on each page that it was composed with generative AI.

Every page gets the notice unless its front matter says ``curated: true``, which marks a
page the maintainer has curated personally. The flag is not shown anywhere.
"""

from typing import Any, Dict

from docutils import nodes


def _front_matter_flag(doctree, name: str):
    """A field from the page's YAML front matter (MyST puts it in the docinfo)."""
    if doctree is None:
        return None
    for field in doctree.traverse(nodes.field):
        if len(field) >= 2 and field[0].astext().strip().lower() == name:
            return field[1].astext().strip().lower()
    return None


def setup(app):
    def _inject(app, pagename: str, templatename: str, context: Dict[str, Any], doctree):
        metadata = {}
        env = getattr(app.builder, "env", None)
        if env is not None and hasattr(env, "metadata"):
            metadata = env.metadata.get(pagename, {}) or {}
        curated = _front_matter_flag(doctree, "curated")
        if curated is None:
            curated = str(metadata.get("curated", "")).strip().lower()
        context["ai_notice"] = curated not in {"true", "yes", "1"}

    app.connect("html-page-context", _inject)
    return {
        "version": "0.2.0",
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }
