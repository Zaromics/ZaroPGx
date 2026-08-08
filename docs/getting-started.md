---
title: Getting Started
curation: partial
---

# Getting Started

## Reading the docs without building them

The running app already serves a built copy of this documentation at
`http://localhost:8765/documentation`. `app/main.py` mounts `docs/_build/html` there and
builds it on startup when the directory is missing, so a normal `docker compose up -d`
gives you these pages with no extra step.

## Building docs locally

If you are editing the docs and want a live-reloading preview, build them in one of
three ways:

1) With Docker (recommended)

The `docs` service sits behind the `optional` Compose profile, so it is skipped by a plain
`docker compose up`. Name the profile to start it:

```bash
docker compose --profile optional up -d --build docs
```

Visit `http://localhost:5070` for live-reloading docs. The port is bound to
`${INTERNAL_BIND_ADDRESS:-127.0.0.1}`, i.e. loopback only by default.

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
