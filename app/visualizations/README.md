### Visualizations

- `job_workflow.py`: the workflow diagram of one job, built from its `job_steps` rows
  and wired as `pipelines/pgx/main.nf` runs those steps. The reports draw it as SVG
  (HTML) and PNG (PDF), and as a list of steps when it cannot be drawn.
- `workflow_diagram.py`: the client for Kroki, the stack's diagram renderer (the
  `kroki` and `mermaid` services).
