/*
 * A minimal DOM for running app/templates/index.html's inline <script> in Node.
 *
 * There is no template-rendering harness in this repo and asserting against
 * template *source text* is weak evidence, so the drivers next to this file
 * execute the page's real inline script inside Node's `vm` under this shim and
 * report the HTML it produces. Elements are created on first lookup by id and
 * keep what the script writes into them; nothing parses that HTML.
 *
 * Nothing here is shipped to the browser.
 */
"use strict";

// Pick the inline <script> block that contains `marker`. Ignore
// <script src=...> tags (CDN bootstrap, /static/js/*).
function extractInlineScript(html, marker) {
  const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g;
  let m;
  while ((m = re.exec(html)) !== null) {
    if (m[1].includes(marker)) return m[1];
  }
  throw new Error(`no inline <script> contains ${marker}`);
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
    // Kept so a driver can fire what the script attached (a button's click).
    this._listeners = {};
  }
  get className() {
    return this.classList.value;
  }
  set className(v) {
    this.classList = new ClassList(v);
  }
  addEventListener(type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }
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

// Evaluate the script in the sandbox and fire DOMContentLoaded, which is where
// the page defines most of its functions. Returns the errors met on the way.
function runPageScript(vm, sandbox, script) {
  const errors = [];
  vm.createContext(sandbox);
  try {
    new vm.Script(script, { filename: "index.html:<script>" }).runInContext(sandbox);
  } catch (e) {
    errors.push("script evaluation: " + e.message);
  }
  for (const fn of sandbox._listeners.DOMContentLoaded || []) {
    try {
      fn.call(sandbox.document, { type: "DOMContentLoaded" });
    } catch (e) {
      errors.push("DOMContentLoaded: " + e.message);
    }
  }
  return errors;
}

module.exports = {
  ClassList,
  El,
  extractInlineScript,
  makeSandbox,
  runPageScript,
  seedClasses,
};
