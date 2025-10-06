import os
import sys
from datetime import datetime, timezone

# -- Path setup --------------------------------------------------------------

# Add project root to sys.path to enable autodoc to find the codebase
sys.path.insert(0, os.path.abspath('..'))

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
html_theme_options = {
    'collapse_navigation': False,
    'navigation_depth': 4,
    'style_external_links': True,
    'display_version': True,
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
]
