from typing import Any, Dict
from docutils import nodes


def setup(app):
    """Sphinx extension to inject per-page curation status for AI disclaimer.

    Authors can set page-level metadata using the MyST/Docutils meta directive, e.g.:

    ```md
    ```{meta}
    :curation: fully
    ```
    ```

    Allowed values: "fully", "partial", "not" (case-insensitive). Defaults to "partial".
    """

    def _inject_curation_status(app, pagename: str, templatename: str, context: Dict[str, Any], doctree):
        env = getattr(app.builder, "env", None)
        metadata: Dict[str, Any] = {}
        if env is not None and hasattr(env, "metadata"):
            metadata = env.metadata.get(pagename, {}) or {}

        # Prefer explicit meta directive or front-matter fields in the doctree
        raw_status = None
        if doctree is not None:
            # 1) meta nodes (from {meta} directive)
            for node in doctree.traverse(nodes.meta):
                if node.get('name', '').lower() == 'curation':
                    raw_status = str(node.get('content', '')).strip().lower()
                    break
            # 2) YAML front-matter becomes field nodes in docinfo
            if not raw_status:
                for field in doctree.traverse(nodes.field):
                    if len(field) >= 2 and getattr(field[0], 'astext', None):
                        name = field[0].astext().strip().lower()
                        if name == 'curation':
                            value = field[1].astext().strip().lower()
                            raw_status = value
                            break

        if not raw_status:
            raw_status = str(metadata.get("curation", "partial")).strip().lower()

        # Normalize synonyms to three canonical states: fully, partial, not
        synonyms = {
            "full": "fully",
            "fully": "fully",
            "complete": "fully",
            "completed": "fully",
            "partial": "partial",
            "partially": "partial",
            "incomplete": "partial",
            "none": "not",
            "no": "not",
            "not": "not",
            "uncurated": "not",
        }
        canonical = synonyms.get(raw_status, "partial")

        # Map to grammatically correct display tokens
        display_map = {"fully": "fully", "partial": "partially", "not": "not"}
        context["curation_status"] = canonical
        context["curation_display"] = display_map.get(canonical, "partially")

    app.connect("html-page-context", _inject_curation_status)

    return {
        "version": "0.1.0",
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }


