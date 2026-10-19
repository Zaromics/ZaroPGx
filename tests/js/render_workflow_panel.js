/*
 * Headless renderer for the post-upload "Workflow Details" panel.
 *
 * There is no template-rendering harness in this repo and asserting against
 * template *source text* is weak evidence, so this driver executes the real
 * inline <script> block of app/templates/index.html inside Node's `vm` under a
 * minimal DOM shim (dom_shim.js), then calls window.updateFileAnalysis() with a
 * real POST /upload/genomic-data response body captured from the FastAPI app.
 *
 * Usage:  node render_workflow_panel.js <index.html> <payload.json>
 * Output (stdout): JSON { elements: { <id>: { html, classes } }, ... }
 *
 * Nothing here is shipped to the browser; it exists only so a pytest test can
 * assert on the HTML the panel actually produces.
 */
"use strict";

const fs = require("fs");
const vm = require("vm");
const { extractInlineScript, makeSandbox, runPageScript } = require("./dom_shim");

const [templatePath, payloadPath] = process.argv.slice(2);
if (!templatePath || !payloadPath) {
  console.error("usage: render_workflow_panel.js <index.html> <payload.json>");
  process.exit(2);
}

const template = fs.readFileSync(templatePath, "utf8");
const payload = JSON.parse(fs.readFileSync(payloadPath, "utf8"));

// ---------------------------------------------------------------- run
const script = extractInlineScript(template, "window.updateFileAnalysis");
const sandbox = makeSandbox(template);
const errors = runPageScript(vm, sandbox, script);

if (typeof sandbox.window.updateFileAnalysis !== "function") {
  process.stdout.write(
    JSON.stringify({
      ok: false,
      errors: errors.concat("window.updateFileAnalysis was never defined"),
      elements: {},
    })
  );
  process.exit(0);
}

try {
  sandbox.window.updateFileAnalysis(payload);
} catch (e) {
  errors.push("updateFileAnalysis: " + e.message);
}

const elements = {};
for (const [id, el] of sandbox._connectedElements()) {
  elements[id] = { html: el.innerHTML, classes: el.classList.value };
}

process.stdout.write(JSON.stringify({ ok: errors.length === 0, errors, elements }));
