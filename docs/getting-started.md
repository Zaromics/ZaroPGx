---
title: Getting Started
---

## Building docs locally

You can build the docs in three ways:

1) With Docker (recommended)

```bash
docker compose up -d --build docs
```

Visit `http://localhost:5070` for live-reloading docs.

2) With Python directly (no Docker)

```bash
python -m pip install -r docs/requirements.txt
sphinx-build -b html docs docs/_build/html
python -m http.server --directory docs/_build/html 5070
```

3) Using the Makefile helpers

```bash
python -m pip install -r docs/requirements.txt
make -C docs html
```

## Read the Docs

This repository includes a `.readthedocs.yaml` configuration. When connected to Read the Docs, each push will build and host these docs automatically.

## Conventions

- Prefer Markdown pages using MyST (`.md`) for simplicity. reStructuredText is also supported.
- Keep internal diagrams and images in `docs/_static/`.
- Cross-reference Python APIs with autodoc when helpful.
