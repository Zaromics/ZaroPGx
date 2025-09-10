import os
import sys
from datetime import datetime

# -- Path setup --------------------------------------------------------------

# Add project root to sys.path to enable autodoc to find the codebase
sys.path.insert(0, os.path.abspath('..'))

# -- Project information -----------------------------------------------------

project = 'ZaroPGx'
author = 'ZaroPGx Contributors'
current_year = datetime.utcnow().year
copyright = f"{current_year}, {author}"

# -- General configuration ---------------------------------------------------

extensions = [
    'myst_parser',
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.autosectionlabel',
    'sphinx.ext.viewcode',
    'sphinx.ext.intersphinx',
    'sphinx_copybutton',
]

myst_enable_extensions = [
    'colon_fence',
    'deflist',
    'html_admonition',
    'html_image',
    'linkify',
    'substitution',
]

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

autodoc_typehints = 'description'
autodoc_member_order = 'bysource'

intersphinx_mapping = {
    'python': ('https://docs.python.org/3', {}),
    'fastapi': ('https://fastapi.tiangolo.com', {}),
}

# -- Options for HTML output -------------------------------------------------

html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']
html_theme_options = {
    'collapse_navigation': False,
    'navigation_depth': 4,
    'style_external_links': True,
}

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
