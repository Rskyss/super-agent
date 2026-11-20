"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..");
const NAV = fs.readFileSync(path.join(ROOT, "desktop", "static", "nav.js"), "utf8");
const CHAT = fs.readFileSync(path.join(ROOT, "desktop", "static", "chat.js"), "utf8");
const NAV_HTML = fs.readFileSync(path.join(ROOT, "desktop", "static", "nav.html"), "utf8");
const NavCore = require(path.join(ROOT, "desktop", "static", "nav.js"));
const ChatCore = require(path.join(ROOT, "desktop", "static", "chat.js"));

assert.deepStrictEqual(NavCore.projectItems("not-an-array", "/a/current", ""), [
  { name: "current", path: "/a/current", current: true },
], "非数组当空列表，当前项目要保留");
assert.deepStrictEqual(NavCore.projectItems({ length: 1 }, "/a/current", ""), [
  { name: "current", path: "/a/current", current: true },
], "伪数组也当空列表，当前项目要保留");
assert.deepStrictEqual(
  NavCore.projectItems(["/a/same", "/a/other", "/a/same"], "/a/same", "").map((item) => item.path),
  ["/a/same", "/a/other"],
  "重复路径只留第一个"
);
assert.deepStrictEqual(
  NavCore.projectItems(["/a/same", "/a/other"], "/a/same", "   ").map((item) => item.current),
  [true, false],
  "空白搜索等于没搜"
);
assert.deepStrictEqual(
  NavCore.projectItems(["/a/Alpha", "/a/Beta"], "/a/Alpha", "ALP").map((item) => item.name),
  ["Alpha"],
  "搜索不分大小写"
);
assert.strictEqual(NavCore.projectItems(["/a/Alpha"], "/a/Alpha", "a/").length, 0, "只按项目名搜");

assert.deepStrictEqual(
  ChatCore.restorePlace([{ id: "t1" }], { thread: "t1" }),
  { open: "t1", draft: "" },
  "有对话没草稿时草稿是空串"
);
assert.deepStrictEqual(
  ChatCore.restorePlace([{ id: "t1" }], { thread: "", draft: "想说的话  " }),
  { open: null, draft: "想说的话  " },
  "判断空白时才 trim，恢复时保留原草稿"
);
assert.deepStrictEqual(
  ChatCore.restorePlace([{ id: "t1" }], undefined),
  { open: "t1", draft: "" },
  "没有记录时接最近一段"
);

class ClassList {
  constructor() { this.names = new Set(); }
  add(...names) { names.forEach((name) => this.names.add(name)); }
  remove(...names) { names.forEach((name) => this.names.delete(name)); }
  contains(name) { return this.names.has(name); }
  toggle(name, force) {
    const wanted = force === undefined ? !this.names.has(name) : force;
    if (wanted) this.names.add(name); else this.names.delete(name);
    return wanted;
  }
}

class FakeNode {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.classList = new ClassList();
    this.value = "";
    this.hidden = false;
    this.focused = false;
    this.parentNode = null;
    this._innerHTML = "";
  }
  get textContent() {
    return this.children.map((child) => child.textContent).join("");
  }
  set textContent(text) {
    this.children = [{ textContent: String(text) }];
    this._innerHTML = "";
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(html) {
    this._innerHTML = String(html);
    this.children = [];
  }
  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    this._innerHTML = "";
    return child;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name); }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  dispatch(type, event = {}) {
    event.target = event.target || this;
    event.preventDefault = event.preventDefault || (() => {});
    event.stopPropagation = event.stopPropagation || (() => {});
    (this.listeners[type] || []).forEach((handler) => handler.call(this, event));
  }
  click() { this.dispatch("click", { target: this }); }
  focus() { this.focused = true; }
  closest(selector) {
    for (const part of selector.split(",").map((item) => item.trim())) {
      if (part.startsWith("#") && this.attributes.id === part.slice(1)) return this;
      if (part.startsWith(".") && this.classList.contains(part.slice(1))) return this;
      if (part === this.tagName.toLowerCase()) return this;
    }
    return null;
  }
}

function makePage(search) {
  const elements = {};
  const root = new FakeNode("body");
  const documentListeners = {};
  for (const match of NAV_HTML.matchAll(/<([a-z]+)\b[^>]*\bid="([^"]+)"[^>]*>/gi)) {
    const element = new FakeNode(match[1]);
    element.attributes.id = match[2];
    element.hidden = /\shidden(?:\s|>|=)/i.test(match[0]);
    element.parentNode = root;
    elements[match[2]] = element;
  }
  root.children = Object.values(elements);

  const sandbox = {
    console,
    URLSearchParams,
    setTimeout: () => 1,
    clearTimeout: () => {},
    setInterval: () => 1,
    clearInterval: () => {},
    fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
    location: { search, href: "" },
    document: {
      getElementById: (id) => elements[id] || null,
      createElement: (tag) => new FakeNode(tag),
      createTextNode: (text) => ({ textContent: String(text) }),
      querySelectorAll: () => [],
      querySelector: () => null,
      addEventListener: (type, handler) => { (documentListeners[type] ||= []).push(handler); },
      body: root,
    },
  };
  sandbox.window = sandbox;
  sandbox.window.alert = () => {};
  sandbox.window.confirm = () => true;
  sandbox.window.addEventListener = (type, handler) => { (documentListeners[type] ||= []).push(handler); };
  sandbox.window.dispatchEvent = (event) => {
    (documentListeners[event.type] || []).forEach((handler) => handler(event));
  };
  vm.createContext(sandbox);
  vm.runInContext(NAV, sandbox, { filename: "nav.js" });
  return { elements, location: sandbox.location, dispatchDocument: (type, event) => {
    event.type = type;
    (documentListeners[type] || []).forEach((handler) => handler(event));
  } };
}

const current = "/tmp/当前<b>项目";
const other = "/tmp/别的<b>项目";
const projects = [current, other, "/tmp/Zeta"];
const search = new URLSearchParams({ project: current, projects: JSON.stringify(projects) }).toString();
const page = makePage(search);

assert.strictEqual(page.elements.projMenu.hidden, true, "浮层平时要藏着");
page.elements.projBtn.click();
assert.strictEqual(page.elements.projMenu.hidden, false, "点左下角按钮要打开浮层");
assert.strictEqual(page.elements.projQ.value, "", "打开时要清空搜索");
assert.strictEqual(page.elements.projQ.focused, true, "打开时光标要进搜索框");
assert.strictEqual(page.elements.projList.children.length, 3, "最近项目要按传入顺序画");
assert.ok(page.elements.projList.children[0].classList.contains("on"), "当前项目要有选中样式");
assert.ok(page.elements.projList.children[0].textContent.includes("✓"), "当前项目要打勾");
assert.ok(page.elements.projList.children[1].textContent.includes("别的<b>项目"), "项目名要按文字显示");
assert.ok(!page.elements.projList.children[1].innerHTML.includes("<b>"), "项目名不能用 HTML 拼");

page.location.href = "";
page.elements.projList.children[0].click();
assert.strictEqual(page.elements.projMenu.hidden, true, "点当前项目只关浮层");
assert.strictEqual(page.location.href, "", "点当前项目不能触发切换");

page.elements.projBtn.click();
page.elements.projQ.value = "zeta";
page.elements.projQ.dispatch("input");
assert.strictEqual(page.elements.projList.children.length, 1, "搜索要按项目名过滤");
assert.ok(page.elements.projList.textContent.toLowerCase().includes("zeta"), "搜索结果要显示项目名");

page.elements.projQ.value = "找不到";
page.elements.projQ.dispatch("input");
assert.ok(page.elements.projList.textContent.includes("没有匹配的项目"), "没有结果时要提示");

page.elements.projBtn.click();
assert.strictEqual(page.elements.projMenu.hidden, true, "菜单开着时再点按钮要先关");
page.elements.projBtn.click();
assert.strictEqual(page.elements.projMenu.hidden, false, "再点按钮要重新打开");
assert.strictEqual(page.elements.projQ.value, "", "重新打开时搜索要重置");
page.location.href = "";
page.elements.projList.children[1].click();
assert.strictEqual(
  page.location.href,
  "superagent://project?path=" + encodeURIComponent(other),
  "点别的项目要告诉外壳完整路径"
);
assert.strictEqual(page.elements.projMenu.hidden, true, "选择项目后要关浮层");

page.elements.projBtn.click();
page.location.href = "";
page.elements.projAdd.click();
assert.strictEqual(page.location.href, "superagent://project-add", "添加项目要走现有选文件夹入口");
assert.strictEqual(page.elements.projMenu.hidden, true, "点添加后要关浮层");

page.elements.projBtn.click();
page.dispatchDocument("keydown", { key: "Escape", target: page.elements.projQ });
assert.strictEqual(page.elements.projMenu.hidden, true, "按 Escape 要关浮层");

page.elements.projBtn.click();
page.dispatchDocument("click", { target: page.elements.gear });
assert.strictEqual(page.elements.projMenu.hidden, true, "点浮层和按钮外面要关浮层");

const brokenSearch = new URLSearchParams({ project: current, projects: "{bad json" }).toString();
const brokenPage = makePage(brokenSearch);
brokenPage.elements.projBtn.click();
assert.strictEqual(brokenPage.elements.projMenu.hidden, false, "项目列表解析失败时按钮还要能用");
assert.strictEqual(brokenPage.elements.projList.children.length, 1, "解析失败时不显示坏列表，只补当前项目");
assert.ok(brokenPage.elements.projList.textContent.includes("当前<b>项目"), "解析失败时当前项目还要显示");

assert.ok(CHAT.includes("clerkMode && !butlerMode"), "只有行政对话报位置，积木库管家不报");
assert.ok(CHAT.includes("setTimeout") && CHAT.includes("800"), "输入停下 800 毫秒再报位置");
assert.ok(/(?:slice|substring)\(0, 2000\)/.test(CHAT), "页面报草稿也要截到 2000 字");

console.log("OK");
