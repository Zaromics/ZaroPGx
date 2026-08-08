import os
import sys
from datetime import datetime, timezone

# -- Path setup --------------------------------------------------------------

# Add project root to sys.path to enable autodoc to find the codebase
sys.path.insert(0, os.path.abspath('..'))
sys.path.insert(0, os.path.abspath('_ext'))

# -- Project information -----------------------------------------------------

project = 'ZaroPGx'
author = 'Iliya Yaroshevskiy'
current_year = datetime.now(timezone.utc).year
copyright = f"{current_year}, {author}"

# -- General configuration ---------------------------------------------------

extensions = [
    'myst_parser',
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.autosectionlabel',
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',
    'sphinx.ext.todo',
    'sphinx.ext.ifconfig',
    'sphinx.ext.githubpages',
    'sphinx_copybutton',
    'sphinx_design',
    'ai_disclaimer',
]

myst_enable_extensions = [
    'colon_fence',
    'deflist',
    'html_admonition',
    'html_image',
    'linkify',
    'substitution',
    'attrs_inline',
    'attrs_block',
    'dollarmath',
    'fieldlist',
    'replacements',
    'smartquotes',
    'strikethrough',
    'tasklist',
]

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# Read the Docs builds with `sphinx.fail_on_warning: true` (see .readthedocs.yaml), so any
# warning class left un-suppressed here breaks the build. That is the point: orphan pages,
# broken {doc} targets and duplicate labels should be errors.

# Without this, every "## Next Steps"/"## Troubleshooting" heading in the docs set collides in
# one flat label namespace and autosectionlabel warns about ~40 duplicates. Prefixing with the
# document name makes labels unique. Nothing in docs/ uses {ref}/:ref:, so no target breaks.
autosectionlabel_prefix_document = True

# The two classes below are deliberately excluded because they are style signals, not
# correctness signals for this project:
#   misc.highlighting_failure — ```mermaid fences have no Pygments lexer, and a couple of
#       http/json samples are illustrative rather than parseable. Sphinx already degrades
#       gracefully; the block still renders.
#   myst.header — the sole remaining source is docs/user/usage.md, which skips heading levels:
#       H2->H4 at :79, :86 and :93, and H1->H3 at :195. Verified 2026-08-08 by rebuilding with
#       only misc.highlighting_failure suppressed: exactly those four fire and nothing else.
#       Fix usage.md's heading levels and delete this entry to restore the full gate. Re-run
#       that check before trusting this note — a page that opens at H2, or that jumps a level,
#       silently re-broadens what this line hides.
suppress_warnings = ['misc.highlighting_failure', 'myst.header']

autodoc_typehints = 'description'
autodoc_member_order = 'bysource'

intersphinx_mapping = {
    'python': ('https://docs.python.org/3', None),
    'fastapi': ('https://fastapi.tiangolo.com', None),
    'sqlalchemy': ('https://docs.sqlalchemy.org/en/20/', None),
    'pydantic': ('https://docs.pydantic.dev/latest/', None),
    'sphinx': ('https://www.sphinx-doc.org/en/master/', None),
}

# -- Options for HTML output -------------------------------------------------

html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']
html_favicon = '_static/favicon.png'
html_theme_options = {
    'collapse_navigation': False,
    'navigation_depth': 4,
    'style_external_links': True,
    'prev_next_buttons_location': 'bottom',
    'style_nav_header_background': '#2980B9',
    'logo_only': False,
    'sticky_navigation': True,
    'includehidden': True,
    'titles_only': False,
}

# -- MyST configuration ------------------------------------------------------

myst_heading_anchors = 3
myst_footnote_transition = True
myst_dmath_double_inline = True
myst_enable_checkboxes = True
myst_highlight_code_blocks = True

# -- MyST substitutions ------------------------------------------------------

myst_substitutions = {
    'project_name': project,
}

# -- Custom assets -----------------------------------------------------------

html_js_files = [
    'back-to-app.js',
]

html_css_files = [
    'back-to-app.css',
    'ai-disclaimer.css',
]
