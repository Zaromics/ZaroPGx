/*
 * Drives the "Push to FHIR server" button of a finished job's results card.
 *
 * Runs app/templates/index.html's real inline <script> under dom_shim.js, with
 * fetch answering /services-config and POST /fhir/push/workflow/{job_id} from a
 * scenario file, then calls window.showResults() the way monitorProgress's
 * onComplete does and, if the scenario has a push, clicks the button.
 *
 * Usage:  node fhir_push_button.js <index.html> <scenario.json>
 *
 * scenario.json:
 *   services_config  the /services-config body (null: it answers HTTP 500)
 *   results          what showResults is given (job_id, patient_id, report URLs)
 *   push             optional; how the push endpoint answers:
 *                    { status, body, headers } or { network_error: "..." }
 *
 * Output (stdout): JSON { ok, errors, fetches, section, click }
 */
"use strict";

const fs = require("fs");
const vm = require("vm");
const {
  extractInlineScript,
  makeSandbox,
  runPageScript,
  seedClasses,
} = require("./dom_shim");

const [templatePath, scenarioPath] = process.argv.slice(2);
if (!templatePath || !scenarioPath) {
  console.error("usage: fhir_push_button.js <index.html> <scenario.json>");
  process.exit(2);
}

const template = fs.readFileSync(templatePath, "utf8");
const scenario = JSON.parse(fs.readFileSync(scenarioPath, "utf8"));

function fakeResponse(status, body, headers) {
  const lower = {};
  for (const [k, v] of Object.entries(headers || {})) lower[k.toLowerCase()] = v;
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    headers: {
      get: (name) => (lower[name.toLowerCase()] === undefined ? null : lower[name.toLowerCase()]),
    },
    json: async () => {
      if (body === undefined || typeof body === "string") throw new SyntaxError("not JSON");
      return body;
    },
  };
}

// Let the page's awaits run: its promises share Node's microtask queue.
async function settle() {
  for (let i = 0; i < 20; i++) await new Promise((resolve) => setImmediate(resolve));
}

async function main() {
  const fetches = [];
  let releasePush = null;
  const sandbox = makeSandbox(seedClasses(template));
  sandbox.fetch = (url, options) => {
    const method = ((options && options.method) || "GET").toUpperCase();
    fetches.push({ url: String(url), method });
    if (url === "/services-config") {
      return Promise.resolve(
        scenario.services_config === null
          ? fakeResponse(500, "Internal Server Error")
          : fakeResponse(200, scenario.services_config)
      );
    }
    if (String(url).startsWith("/fhir/push/workflow/") && scenario.push) {
      // Held until the driver has looked at the button mid-push.
      return new Promise((resolve, reject) => {
        releasePush = () => {
          if (scenario.push.network_error) reject(new TypeError(scenario.push.network_error));
          else resolve(fakeResponse(scenario.push.status, scenario.push.body, scenario.push.headers));
        };
      });
    }
    return Promise.reject(new Error(`unexpected fetch in test: ${method} ${url}`));
  };

  const script = extractInlineScript(template, "window.showResults");
  const errors = runPageScript(vm, sandbox, script);
  await settle();

  try {
    sandbox.window.showResults(scenario.results);
  } catch (e) {
    errors.push("showResults: " + e.message);
  }
  await settle();

  const el = (id) => sandbox.document.getElementById(id);
  const section = { html: el("fhirPushSection").innerHTML, classes: el("fhirPushSection").classList.value };

  let click = null;
  if (scenario.push) {
    const handlers = el("fhirPushBtn")._listeners.click || [];
    if (handlers.length !== 1) {
      errors.push(`expected one click handler on #fhirPushBtn, found ${handlers.length}`);
    } else {
      const button = el("fhirPushBtn");
      handlers[0].call(button, { type: "click", preventDefault() {} });
      const during = { disabled: button.disabled, label: button.innerHTML };
      await settle();
      if (!releasePush) {
        errors.push("the click did not call the push endpoint");
      } else {
        releasePush();
        await settle();
      }
      click = {
        during,
        after: { disabled: button.disabled, label: button.innerHTML },
        result_html: el("fhirPushResult").innerHTML,
      };
    }
  }

  process.stdout.write(JSON.stringify({ ok: errors.length === 0, errors, fetches, section, click }));
}

main().catch((e) => {
  process.stdout.write(JSON.stringify({ ok: false, errors: ["driver: " + e.stack] }));
});
