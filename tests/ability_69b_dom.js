// 69b 的 Node 页面边界：读取真实 HTML，执行真实脚本；不启动浏览器。
// 只替代 DOM、HTTP 和时钟，不替代任何产品函数。未匹配的选择器返回 null。
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");
const STATIC = path.join(__dirname, "..", "desktop", "static");
const decode = s => String(s).replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|#39);/gi, (_, x) =>
  ({ amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" }[x] ||
   String.fromCodePoint(x[1].toLowerCase() === "x" ? parseInt(x.slice(2), 16) : +x.slice(1))));

class Element {
  constructor(tag = "div", text = "") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.listeners = {};
    this.style = {};
    this._text = text;
    this._value = undefined;
    this.scrollTop = 0;
    this.scrollHeight = this.clientHeight = 100;
    this.classList = {
      contains: c => this.className.split(/\s+/).includes(c),
      add: c => { if (!this.classList.contains(c)) this.className += " " + c; },
      remove: c => { this.className = this.className.split(/\s+/).filter(x => x !== c).join(" "); },
      toggle: (c, force) => {
        const on = force === undefined ? !this.classList.contains(c) : force;
        this.classList[on ? "add" : "remove"](c);
        return on;
      },
    };
    this.dataset = new Proxy({}, {
      get: (_, k) => this.getAttribute("data-" + k.replace(/[A-Z]/g, c => "-" + c.toLowerCase())),
      set: (_, k, v) => { this.setAttribute("data-" + k.replace(/[A-Z]/g, c => "-" + c.toLowerCase()), v); return true; },
    });
  }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(""); }
  set textContent(v) { this.children.forEach(c => { c.parentNode = null; }); this.children = []; this._text = String(v ?? ""); }
  get innerText() { return this.textContent; }
  set innerText(v) { this.textContent = v; }
  get innerHTML() { return this._text + this.children.map(c => c.outerHTML).join(""); }
  set innerHTML(v) { this.textContent = ""; parse(String(v), this); }
  get outerHTML() {
    if (this.tagName === "#TEXT") return this._text;
    return "<" + this.tagName + JSON.stringify(this.attributes) + ">" + this.innerHTML + "</" + this.tagName + ">";
  }
  get value() {
    if (this._value !== undefined) return this._value;
    if (this.tagName === "TEXTAREA") return this.textContent;
    if (this.tagName === "SELECT") {
      const option = this.children.find(c => c.selected) || this.children[0];
      return option ? option.value : "";
    }
    return this.getAttribute("value") || "";
  }
  set value(v) { this._value = String(v); }
  get firstChild() { return this.children[0] || null; }
  get firstElementChild() { return this.children.find(c => c.tagName !== "#TEXT") || null; }
  get nextSibling() { return this.parentNode?.children[this.parentNode.children.indexOf(this) + 1] || null; }
  get nextElementSibling() { let n = this.nextSibling; while (n?.tagName === "#TEXT") n = n.nextSibling; return n; }
  get parentElement() { return this.parentNode; }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return Object.hasOwn(this.attributes, k) ? this.attributes[k] : null; }
  hasAttribute(k) { return Object.hasOwn(this.attributes, k); }
  removeAttribute(k) { delete this.attributes[k]; }
  appendChild(c) { if (c.parentNode) c.remove(); c.parentNode = this; this.children.push(c); return c; }
  append(...nodes) { nodes.forEach(n => this.appendChild(typeof n === "string" ? new Element("#text", n) : n)); }
  removeChild(c) { this.children.splice(this.children.indexOf(c), 1); c.parentNode = null; return c; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  insertBefore(c, before) {
    if (!before) return this.appendChild(c);
    if (c.parentNode) c.remove();
    this.children.splice(this.children.indexOf(before), 0, c); c.parentNode = this; return c;
  }
  replaceChild(c, old) { this.insertBefore(c, old); this.removeChild(old); return old; }
  after(c) { this.parentNode.insertBefore(c, this.nextSibling); }
  contains(c) { return c === this || this.children.some(n => n.contains(c)); }
  matches(selector) {
    return selector.split(",").some(part => {
      const tokens = part.trim().split(/\s+(?![^\[]*\])/);
      if (!simple(this, tokens.pop())) return false;
      let ancestor = this.parentNode;
      while (tokens.length) {
        const token = tokens.pop();
        while (ancestor && !simple(ancestor, token)) ancestor = ancestor.parentNode;
        if (!ancestor) return false;
        ancestor = ancestor.parentNode;
      }
      return true;
    });
  }
  closest(s) { for (let n = this; n; n = n.parentNode) if (n.matches(s)) return n; return null; }
  querySelectorAll(s) {
    const result = [];
    for (const c of this.children) {
      if (c.tagName !== "#TEXT" && c.matches(s)) result.push(c);
      result.push(...c.querySelectorAll(s));
    }
    return result;
  }
  querySelector(s) { return this.querySelectorAll(s)[0] || null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatchEvent(e) {
    e.target ||= this;
    e.preventDefault ||= () => {};
    e.stopPropagation ||= () => { e.stopped = true; };
    for (let n = this; n; n = e.bubbles === false || e.stopped ? null : n.parentNode) {
      e.currentTarget = n;
      (n.listeners[e.type] || []).forEach(fn => fn(e));
      if (typeof n["on" + e.type] === "function") n["on" + e.type](e);
    }
    return true;
  }
  click() { if (!this.disabled) this.dispatchEvent({ type: "click" }); }
  focus() {}
  scrollIntoView() {}
}
for (const [prop, attr] of Object.entries({ id: "id", className: "class", name: "name", type: "type", src: "src", href: "href" })) {
  Object.defineProperty(Element.prototype, prop, {
    get() { return this.getAttribute(attr) || ""; }, set(v) { this.setAttribute(attr, v); },
  });
}
for (const prop of ["hidden", "disabled", "required", "open", "selected", "checked"]) {
  Object.defineProperty(Element.prototype, prop, {
    get() { return this.hasAttribute(prop); },
    set(v) { if (v) this.setAttribute(prop, ""); else this.removeAttribute(prop); },
  });
}
function simple(el, selector) {
  if (el.tagName === "#TEXT") return false;
  let s = selector.replace(/:not\(([^)]+)\)/g, (_, inner) => el.matches(inner) ? "!" : "");
  if (s.includes("!")) return false;
  const attrs = [...s.matchAll(/\[([^\]=\s]+)(?:=["']?([^\]"']*)["']?)?\]/g)];
  if (attrs.some(m => !el.hasAttribute(m[1]) || (m[2] !== undefined && el.getAttribute(m[1]) !== m[2]))) return false;
  s = s.replace(/\[[^\]]*\]/g, "");
  const id = /#([\w-]+)/.exec(s);
  if (id && el.id !== id[1]) return false;
  if ([...s.matchAll(/\.([\w-]+)/g)].some(m => !el.classList.contains(m[1]))) return false;
  const tag = /^[\w-]+/.exec(s);
  return !tag || el.tagName === tag[0].toUpperCase();
}
function parse(html, root) {
  const stack = [root];
  for (const token of html.match(/<!--[\s\S]*?-->|<![^>]*>|<[^>]+>|[^<]+/g) || []) {
    if (/^<!/.test(token)) continue;
    if (/^<\//.test(token)) {
      const tag = token.slice(2, -1).trim().toUpperCase();
      const index = stack.map(n => n.tagName).lastIndexOf(tag);
      if (index > 0) stack.length = index;
    } else if (token.startsWith("<")) {
      const tag = /^<([\w-]+)/.exec(token)?.[1];
      if (!tag) continue;
      const el = new Element(tag);
      const attrText = token.slice(tag.length + 1).replace(/\/?\s*>$/, "");
      for (const m of attrText.matchAll(/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+)))?/g)) {
        el.setAttribute(m[1], decode(m[2] ?? m[3] ?? m[4] ?? ""));
      }
      stack.at(-1).appendChild(el);
      if (!/^(area|base|br|col|embed|hr|img|input|link|meta|source|wbr)$/i.test(tag) && !/\/>$/.test(token)) stack.push(el);
    } else stack.at(-1).appendChild(new Element("#text", decode(token)));
  }
}
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r)); };
function visible(el) {
  for (let n = el; n; n = n.parentNode) if (n.hidden || n.style.display === "none") return false;
  return true;
}
function text(el) {
  if (!visible(el)) return "";
  return el._text + el.children.map(text).join("");
}
function page(htmlName, { respond, search = "", before, parent, scriptFilter = () => true } = {}) {
  const document = new Element("document");
  parse(fs.readFileSync(path.join(STATIC, htmlName), "utf8"), document);
  document.getElementById = id => document.querySelector("#" + id);
  document.createElement = tag => new Element(tag);
  document.createTextNode = value => new Element("#text", String(value));
  document.body = document.querySelector("body");
  const requests = [], failures = [], timers = new Map();
  let now = 1800000000000, timerId = 0;
  const schedule = (fn, delay, repeat) => {
    const id = ++timerId;
    timers.set(id, { fn, delay: +delay || 0, due: now + Math.max(1, +delay || 0), repeat });
    return id;
  };
  class FakeDate extends Date { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } }
  const context = {
    console, URL, URLSearchParams, encodeURIComponent, decodeURIComponent,
    Date: FakeDate, performance: { now: () => now },
    location: { search }, navigator: {}, document,
    setTimeout: (fn, delay) => schedule(fn, delay, false), clearTimeout: id => timers.delete(id),
    setInterval: (fn, delay) => schedule(fn, delay, true), clearInterval: id => timers.delete(id),
    addEventListener() {}, alert: message => failures.push(new Error("意外弹窗：" + message)), confirm: () => true,
    fetch(url, options = {}) {
      const parsed = new URL(url, "http://fixture.invalid");
      const req = { url: parsed, options, body: options.body ? JSON.parse(options.body) : null, at: now };
      requests.push(req);
      return Promise.resolve().then(() => respond(req)).then(answer => {
        if (answer && typeof answer.json === "function") return answer;
        const status = answer?.status || 200;
        return { status, ok: status < 400, json: async () => answer?.data ?? answer };
      }).catch(error => { failures.push(error); throw error; });
    },
  };
  context.window = context;
  context.parent = parent || context;
  context.globalThis = context;
  if (before) before(document, context);
  vm.createContext(context);
  for (const script of document.querySelectorAll("script[src]")) {
    const filename = path.basename(script.src);
    if (!scriptFilter(filename)) continue;
    vm.runInContext(fs.readFileSync(path.join(STATIC, filename), "utf8"), context, { filename });
  }
  return {
    document, context, requests, timers, failures,
    el: id => document.getElementById(id),
    async advance(ms) {
      const end = now + ms;
      let count = 0;
      for (;;) {
        const next = [...timers].filter(([, t]) => t.due <= end).sort((a, b) => a[1].due - b[1].due)[0];
        if (!next) break;
        assert.ok(++count < 500, "页面重复创建了无法结束的定时器");
        const [id, timer] = next;
        now = timer.due;
        if (timer.repeat) timer.due = now + Math.max(timer.delay, 1); else timers.delete(id);
        timer.fn(); await settle();
      }
      now = end; await settle();
    },
    check() { assert.deepStrictEqual(failures.map(e => e.message), [], "页面边界错误"); },
  };
}
module.exports = { page, Element, settle, visible, text, STATIC };
