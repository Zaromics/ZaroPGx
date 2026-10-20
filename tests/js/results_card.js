/*
 * Draws a finished job's results card from its report links, as the page does.
 *
 * Runs app/templates/index.html's real inline <script> under dom_shim.js. fetch
 * answers GET /reports/job/{job_id} with the scenario's body (/services-config,
 * which the FHIR push asks for, with 500); the driver then does what
 * monitorProgress's onComplete does for an end without links: fetchReportLinks,
 * then showResults.
 *
 * Usage:  node results_card.js <index.html> <scenario.json>
 *
 * scenario.json:  { job_id, reports_job: <GET /reports/job/{job_id} body> }
 *
 * Output (stdout): JSON { ok, errors, links, card }
 *   links  what fetchReportLinks made of the answer
 *   card   #analysisResults' HTML
 */
"use strict";

const fs = require("fs");
const vm = require("vm");
const { extractInlineScript, makeSandbox, runPageScript } = require("./dom_shim");

const [templatePath, scenarioPath] = process.argv.slice(2);
if (!templatePath || !scenarioPath) {
  console.error("usage: results_card.js <index.html> <scenario.json>");
  process.exit(2);
}

const template = fs.readFileSync(templatePath, "utf8");
const scenario = JSON.parse(fs.readFileSync(scenarioPath, "utf8"));

async function settle() {
  for (let i = 0; i < 20; i++) await new Promise((resolve) => setImmediate(resolve));
}

async function main() {
  const errors = [];
  process.on("unhandledRejection", (reason) => {
    errors.push("unhandled rejection: " + (reason && reason.stack ? reason.stack : reason));
  });
  const sandbox = makeSandbox(template);
  sandbox.fetch = (url) => {
    if (url === `/reports/job/${encodeURIComponent(scenario.job_id)}`) {
      return Promise.resolve({ ok: true, status: 200, json: async () => scenario.reports_job });
    }
    if (url === "/services-config") {
      return Promise.resolve({ ok: false, status: 500, json: async () => ({}) });
    }
    return Promise.reject(new Error(`unexpected fetch in test: ${url}`));
  };

  errors.push(...runPageScript(vm, sandbox, extractInlineScript(template, "window.showResults")));
  await settle();

  let links = null;
  try {
    links = await sandbox.window.fetchReportLinks(scenario.job_id);
    sandbox.window.showResults(links);
  } catch (e) {
    errors.push("fetchReportLinks/showResults: " + e.message);
  }
  await settle();

  const card = sandbox.document.getElementById("analysisResults");
  process.stdout.write(
    JSON.stringify({ ok: errors.length === 0, errors, links, card: card ? card.innerHTML : null })
  );
}

main().catch((e) => {
  process.stdout.write(JSON.stringify({ ok: false, errors: ["driver: " + e.stack] }));
});
