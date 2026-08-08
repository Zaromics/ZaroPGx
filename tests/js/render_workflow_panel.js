/*
 * Headless renderer for the post-upload "Workflow Details" panel.
 *
 * There is no template-rendering harness in this repo and asserting against
 * template *source text* is weak evidence, so this driver executes the real
 * inline <script> block of app/templates/index.html inside Node's `vm` under a
 * minimal DOM shim, then calls window.updateFileAnalysis() with a real
 * POST /upload/genomic-data response body captured from the FastAPI app.
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

const [templatePath, payloadPath] = process.argv.slice(2);
if (!templatePath || !payloadPath) {
  console.error("usage: render_workflow_panel.js <index.html> <payload.json>");
  process.exit(2);
}

const template = fs.readFileSync(templatePath, "utf8");
const payload = JSON.parse(fs.readFileSync(payloadPath, "utf8"));

// ---------------------------------------------------------------- extraction
// Pick the inline <script> block that defines the panel renderer. Ignore
// <script src=...> tags (CDN bootstrap, /static/js/*).
function extractPanelScript(html) {
  const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g;
  let m;
  while ((m = re.exec(html)) !== null) {
    if (m[1].includes("window.updateFileAnalysis")) return m[1];
  }
  throw new Error("no inline <script> defines window.updateFileAnalysis");
}

// Seed the shim with the classes each element really carries in the markup, so
// e.g. #warnings genuinely starts out `d-none` and "it became visible" means
// something.
function seedClasses(html) {
  const seeds = {};
  const re = /<[a-zA-Z][^>]*\bid="([^"]+)"[^>]*>/g;
  let m;
  while ((m = re.exec(html)) !== null) {
    const tag = m[0];
    const cls = /\bclass="([^"]*)"/.exec(tag);
    seeds[m[1]] = cls ? cls[1] : "";
  }
  return seeds;
}

// ---------------------------------------------------------------- DOM shim
class ClassList {
  constructor(initial) {
    this._s = new Set(String(initial || "").split(/\s+/).filter(Boolean));
  }
  add(...c) {
    c.forEach((x) => this._s.add(x));
  }
  remove(...c) {
    c.forEach((x) => this._s.delete(x));
  }
  contains(c) {
    return this._s.has(c);
  }
  toggle(c, force) {
    const on = force === undefined ? !this._s.has(c) : !!force;
    on ? this._s.add(c) : this._s.delete(c);
    return on;
  }
  get value() {
    return [...this._s].join(" ");
  }
  toString() {
    return this.value;
  }
}

class El {
  constructor(id, className, tagName) {
    this.id = id || "";
    this.tagName = (tagName || "DIV").toUpperCase();
    this.classList = new ClassList(className);
    this.innerHTML = "";
    this.textContent = "";
    this.innerText = "";
    this.value = "";
    this.title = "";
    this.disabled = false;
    this.checked = false;
    this.files = [];
    this.style = {};
    this.dataset = {};
    this.children = [];
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this._attrs = {};
  }
  get className() {
    return this.classList.value;
  }
  set className(v) {
    this.classList = new ClassList(v);
  }
  addEventListener() {}
  removeEventListener() {}
  appendChild(c) {
    this.children.push(c);
    return c;
  }
  removeChild() {}
  remove() {}
  insertAdjacentHTML(pos, html) {
    this.innerHTML += html;
  }
  setAttribute(k, v) {
    this._attrs[k] = v;
  }
  getAttribute(k) {
    return Object.prototype.hasOwnProperty.call(this._attrs, k)
      ? this._attrs[k]
      : null;
  }
  removeAttribute(k) {
    delete this._attrs[k];
  }
  hasAttribute(k) {
    return Object.prototype.hasOwnProperty.call(this._attrs, k);
  }
  querySelector() {
    return null;
  }
  querySelectorAll() {
    return [];
  }
  closest() {
    return null;
  }
  click() {}
  focus() {}
  scrollIntoView() {}
}

function makeSandbox(seeds) {
  const elements = new Map();
  const getById = (id) => {
    if (!elements.has(id)) elements.set(id, new El(id, seeds[id] || ""));
    return elements.get(id);
  };

  const listeners = {};
  const document = {
    documentElement: new El("", ""),
    body: new El("", ""),
    getElementById: getById,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: (tag) => new El("", "", tag),
    addEventListener: (evt, fn) => {
      (listeners[evt] = listeners[evt] || []).push(fn);
    },
    removeEventListener: () => {},
  };

  // The page logs freely; keep stdout clean for the result JSON.
  const quietConsole = {};
  for (const level of ["log", "info", "warn", "error", "debug", "trace"]) {
    quietConsole[level] = (...args) => {
      try {
        process.stderr.write(args.map(String).join(" ") + "\n");
      } catch (e) {
        /* ignore */
      }
    };
  }

  const store = {};
  const sandbox = {
    console: quietConsole,
    document,
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => {
        store[k] = String(v);
      },
      removeItem: (k) => {
        delete store[k];
      },
    },
    // The panel expands its collapse through bootstrap; stub the one API used.
    bootstrap: {
      Collapse: {
        getOrCreateInstance: () => ({ show() {}, hide() {}, toggle() {} }),
      },
    },
    setTimeout: () => 0,
    clearTimeout: () => {},
    setInterval: () => 0,
    clearInterval: () => {},
    requestAnimationFrame: () => 0,
    cancelAnimationFrame: () => {},
    fetch: () => Promise.reject(new Error("network disabled in render test")),
    WebSocket: function WebSocketStub() {
      this.addEventListener = () => {};
      this.close = () => {};
    },
    FormData: function FormDataStub() {
      this.append = () => {};
    },
    XMLHttpRequest: function XMLHttpRequestStub() {
      this.addEventListener = () => {};
      this.open = () => {};
      this.send = () => {};
      this.upload = { addEventListener: () => {} };
    },
    alert: () => {},
    location: { href: "http://localhost/", origin: "http://localhost", protocol: "http:", host: "localhost" },
    matchMedia: null,
    navigator: { userAgent: "node" },
    _listeners: listeners,
    _elements: elements,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.self = sandbox;
  return sandbox;
}

// ---------------------------------------------------------------- run
const script = extractPanelScript(template);
const sandbox = makeSandbox(seedClasses(template));
vm.createContext(sandbox);

const errors = [];
try {
  new vm.Script(script, { filename: "index.html:<script>" }).runInContext(sandbox);
} catch (e) {
  errors.push("script evaluation: " + e.message);
}

// The panel renderer is defined inside a DOMContentLoaded handler; fire it.
for (const fn of sandbox._listeners.DOMContentLoaded || []) {
  try {
    fn.call(sandbox.document, { type: "DOMContentLoaded" });
  } catch (e) {
    errors.push("DOMContentLoaded: " + e.message);
  }
}

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
for (const [id, el] of sandbox._elements.entries()) {
  elements[id] = { html: el.innerHTML, classes: el.classList.value };
}

process.stdout.write(JSON.stringify({ ok: errors.length === 0, errors, elements }));
