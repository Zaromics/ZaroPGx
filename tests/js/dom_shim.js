/*
 * A minimal DOM for running app/templates/index.html's inline <script> in Node.
 *
 * There is no template-rendering harness in this repo and asserting against
 * template *source text* is weak evidence, so the drivers next to this file
 * execute the page's real inline script inside Node's `vm` under this shim and
 * report the HTML it produces.
 *
 * getElementById finds what a browser would find: an element of the page's
 * markup (the template outside its <script> and <style> blocks), or one the
 * script put in the page through innerHTML, insertAdjacentHTML or appendChild,
 * and only while it is still there. Assigning innerHTML replaces what the
 * element held, so the ids in its old content are gone. Anything else is
 * null, as in the browser: a shim that made up an element for any id let a
 * test pass with #fhirPushSection never inserted into the results card.
 *
 * Only elements with an id are modelled, nested as the HTML nests them; an
 * element keeps its innerHTML as the string the script wrote.
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

// The page's markup: the template without the scripts and styles whose text
// would otherwise read as elements (the inline script builds HTML in strings).
function pageMarkup(html) {
  return html
    .replace(/<script\b[\s\S]*?<\/script>/gi, "")
    .replace(/<style\b[\s\S]*?<\/style>/gi, "")
    .replace(/<!--[\s\S]*?-->/g, "");
}

const VOID_TAGS = new Set([
  "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
  "param", "source", "track", "wbr",
]);
// A tag, its attributes read past quoted values that may hold ">".
const TAG_RE = /<(\/?)([a-zA-Z][\w:-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>/g;

function attribute(attrs, name) {
  const m = new RegExp(`(?:^|\\s)${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)')`).exec(attrs);
  return m ? (m[1] !== undefined ? m[1] : m[2]) : null;
}

// Every element with an id in `html`, made a descendant of `container` the way
// the tags nest; `add` registers each one. A closing tag closes up to the
// element it names, as a browser's parser does with an unclosed <p> or <li>.
function parseInto(container, html, add) {
  const open = [{ tag: null, el: container }];
  TAG_RE.lastIndex = 0;
  let m;
  while ((m = TAG_RE.exec(html)) !== null) {
    const [, closing, rawTag, attrs] = m;
    const tag = rawTag.toLowerCase();
    if (closing) {
      for (let i = open.length - 1; i > 0; i--) {
        if (open[i].tag === tag) {
          open.length = i;
          break;
        }
      }
      continue;
    }
    let el = null;
    const id = attribute(attrs, "id");
    if (id) {
      let parent = container;
      for (let i = open.length - 1; i >= 0; i--) {
        if (open[i].el) {
          parent = open[i].el;
          break;
        }
      }
      el = new El(id, attribute(attrs, "class") || "", tag);
      el._attachTo(parent);
      add(el);
    }
    if (!VOID_TAGS.has(tag) && !/\/\s*$/.test(attrs)) open.push({ tag, el });
  }
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
    // Where the element sits: the element (or the document) it is inside, null
    // once it is out of the page. _children: the modelled elements inside it.
    this._parent = null;
    this._children = new Set();
    // The sandbox's registry of ids, once the element is registered there.
    this._registry = null;
    this._html = "";
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
  get innerHTML() {
    return this._html;
  }
  // Replaces the content: what was inside leaves the page, what the HTML
  // holds enters it.
  set innerHTML(html) {
    for (const child of this._children) child._parent = null;
    this._children.clear();
    this._html = String(html);
    if (this._registry) this._registry.parse(this, this._html);
  }
  get isConnected() {
    let el = this;
    for (let hops = 0; el && hops < 10000; hops++) {
      if (el._parent === DOCUMENT) return true;
      el = el._parent;
    }
    return false;
  }
  _attachTo(parent) {
    if (this._parent && this._parent !== DOCUMENT) this._parent._children.delete(this);
    this._parent = parent;
    if (parent && parent !== DOCUMENT) parent._children.add(this);
  }
  addEventListener(type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }
  removeEventListener() {}
  appendChild(c) {
    this.children.push(c);
    c._attachTo(this);
    if (this._registry) this._registry.adopt(c);
    return c;
  }
  removeChild(c) {
    if (c && c._parent === this) c._attachTo(null);
    return c;
  }
  remove() {
    this._attachTo(null);
  }
  // Adds to the content; "beforebegin" and "afterend" put the new elements
  // beside this one, inside its parent.
  insertAdjacentHTML(pos, html) {
    const into = pos === "beforebegin" || pos === "afterend" ? this._parent : this;
    if (into === this) this._html += html;
    if (into && this._registry) this._registry.parse(into, String(html));
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

// What the page's top-level elements are inside.
const DOCUMENT = Object.freeze({ id: "#document" });

// The ids in the page: every element registered under each, latest last.
class Registry {
  constructor() {
    this.byId = new Map();
  }
  add(el) {
    el._registry = this;
    if (el.id) {
      if (!this.byId.has(el.id)) this.byId.set(el.id, []);
      this.byId.get(el.id).push(el);
    }
  }
  parse(container, html) {
    parseInto(container, html, (el) => this.add(el));
  }
  // An element made with createElement joins the registry when appended, with
  // whatever its innerHTML already holds.
  adopt(el) {
    if (el._registry === this) return;
    this.add(el);
    if (el._html) this.parse(el, el._html);
  }
  find(id) {
    const candidates = this.byId.get(id) || [];
    for (let i = candidates.length - 1; i >= 0; i--) {
      if (candidates[i].isConnected) return candidates[i];
    }
    return null;
  }
  // id -> element, for every element in the page now.
  connected() {
    const found = new Map();
    for (const id of this.byId.keys()) {
      const el = this.find(id);
      if (el) found.set(id, el);
    }
    return found;
  }
}

// A sandbox whose document holds the page's markup (`html`: the template).
function makeSandbox(html) {
  const registry = new Registry();
  const root = new El("", "", "html");
  root._attachTo(DOCUMENT);
  registry.add(root);
  registry.parse(root, pageMarkup(html));
  const body = new El("", "", "body");
  body._attachTo(DOCUMENT);
  registry.add(body);

  const listeners = {};
  const document = {
    documentElement: root,
    body,
    getElementById: (id) => registry.find(String(id)),
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
    // window's own listeners (a resize re-places an open stage popup); nothing
    // here resizes.
    addEventListener: () => {},
    removeEventListener: () => {},
    _listeners: listeners,
    // id -> element for everything in the page now (render_workflow_panel.js).
    _connectedElements: () => registry.connected(),
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

// The ids of the elements `el` sits inside, innermost first.
function ancestorIds(el) {
  const ids = [];
  for (let p = el && el._parent; p && p !== DOCUMENT; p = p._parent) {
    if (p.id) ids.push(p.id);
  }
  return ids;
}

module.exports = {
  ClassList,
  El,
  ancestorIds,
  extractInlineScript,
  makeSandbox,
  runPageScript,
};
