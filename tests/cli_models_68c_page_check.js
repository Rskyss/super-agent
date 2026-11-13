// 68c 追加页面验收：执行完整页面脚本，仅替换 DOM、HTTP 和时钟边界。
// Node 标准库即可运行；主入口由 test_acceptance_68c_additional.py 调用。
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const scenario = process.argv[2];
const staticDir = path.join(__dirname, "..", "desktop", "static");

function page(file, respond) {
  const elements = new Map();
  const timers = new Map();
  const posts = [];
  const alerts = [];
  let timerId = 0;
  class Element {
    constructor(tag = "div") {
      this.tagName = tag.toUpperCase();
      this.children = [];
      this.listeners = {};
      this.attributes = {};
      this.style = {};
      this.className = "";
      this.hidden = false;
      this.disabled = false;
      this.textContent = "";
      this._value = "";
      this._selected = false;
      this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
    }
    set selected(value) {
      this._selected = Boolean(value);
      if (value && this.parentNode && this.parentNode.tagName === "SELECT") {
        this.parentNode.children.forEach(c => { if (c !== this) c._selected = false; });
      }
    }
    get selected() { return this._selected; }
    get value() {
      if (this.tagName === "SELECT") {
        const selected = this.children.find(c => c.selected);
        return selected ? selected.value : "";
      }
      return this._value;
    }
    set value(value) {
      this._value = String(value);
      if (this.tagName === "SELECT") {
        this.children.forEach(c => { c._selected = c.value === this._value; });
      }
    }
    set innerHTML(value) {
      this._html = value;
      this.children = [];
      // 页面用此 HTML 添加备用下拉框的“不用”选项。
      if (this.tagName === "SELECT" && value.includes("<option")) {
        const option = new Element("option");
        option.value = "";
        this.appendChild(option);
      }
    }
    get innerHTML() { return this._html || ""; }
    appendChild(child) {
      child.parentNode = this;
      if (this.tagName === "SELECT") {
        if (child.selected) this.children.forEach(c => { c._selected = false; });
        else if (!this.children.some(c => c.selected)) child._selected = true;
      }
      this.children.push(child);
      return child;
    }
    insertBefore(child) { return this.appendChild(child); }
    after(child) { return this.parentNode.appendChild(child); }
    addEventListener(type, handler) { this.listeners[type] = handler; }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    getAttribute(key) { return this.attributes[key] || null; }
    querySelectorAll(selector) {
      const found = [];
      for (const child of this.children) {
        if (selector === ".trial-btn" && child.className.split(" ").includes("trial-btn")) found.push(child);
        found.push(...child.querySelectorAll(selector));
      }
      return found;
    }
    querySelector(selector) {
      return this.querySelectorAll(selector)[0] || this.appendChild(new Element(selector));
    }
    focus() {}
    click() { if (!this.disabled && this.listeners.click) this.listeners.click({ target: this }); }
    change(value) { this.value = value; this.listeners.change({ target: this }); }
  }
  const root = new Element();
  function el(id) {
    if (!elements.has(id)) {
      const tag = ["abilityModel", "abilityBackup1", "abilityBackup2", "bindProvider", "bindModel"].includes(id)
        ? "select" : "div";
      const item = new Element(tag);
      item.id = id;
      root.appendChild(item);
      elements.set(id, item);
    }
    return elements.get(id);
  }
  const context = {
    console, URLSearchParams, encodeURIComponent,
    location: { search: "" }, // 不触发真实的绑定、环境体检等启动请求。
    document: {
      getElementById: el,
      createElement: tag => new Element(tag),
      querySelectorAll: selector => root.querySelectorAll(selector),
    },
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval() { return ++timerId; }, clearInterval() {},
    alert(message) { alerts.push(message); },
    addEventListener() {},
    fetch(url, options = {}) {
      const parsed = new URL(url, "http://test.invalid");
      const body = options.body ? JSON.parse(options.body) : null;
      if (body) posts.push({ url: parsed.pathname, body });
      return Promise.resolve(respond(parsed.pathname, parsed.searchParams, body)).then(data => ({
        ok: true, json: () => Promise.resolve(data),
      }));
    },
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  // 照 blocks.html：能力层页面先引入公共文件 abilities_core.js 再引入 abilities.js（69b 拆出来的）
  const scripts = file === "abilities.js" ? ["abilities_core.js", file] : [file];
  for (const name of scripts) vm.runInContext(fs.readFileSync(path.join(staticDir, name), "utf8"), context, { filename: name });
  return { el, context, posts, alerts };
}

const settle = () => new Promise(resolve => setImmediate(resolve));

async function abilityCase() {
  const defaultModel = "gpt-6.1-sol";
  const card = {
    id: "draw-image", title: "出图", block: "codex", backup_models: [],
    how: { kind: "cli", prompt: "画图" }, inputSchema: { type: "object", properties: {} },
    output: { type: "image", check: {} },
  };
  if (scenario === "explicit-model") card.model = "explicit-main";
  const detail = {
    ability: card, status: "ok", runs: [], example: null,
    block: { id: "codex", name: "Codex", exists: true, verified: true, command: "codex", model: defaultModel },
    // 第一项特意不是积木块默认模型。
    models: ["first-is-not-default", defaultModel, "backup-choice", "explicit-main"], model_status: {},
  };
  const ui = page("abilities.js", (url, query, body) => {
    if (url === "/api/abilities/list") return { abilities: [], broken: [] };
    if (url === "/api/abilities/detail") return detail;
    if (url === "/api/abilities/model") return { ability: { ...card, model: body.model, backup_models: body.backup_models } };
    throw new Error("Unexpected HTTP request: " + url);
  });
  ui.context.AbilitiesPage.open("draw-image");
  await settle();
  assert.strictEqual(ui.el("abilityTitle").textContent, "出图");
  assert.strictEqual(ui.el("abilityModel").children.length, 4, "真实页面已渲染模型名单");
  if (scenario === "save-backup") {
    ui.el("abilityBackup1").change("backup-choice");
    await settle();
    const saved = ui.posts.find(p => p.url === "/api/abilities/model");
    assert.ok(saved, "修改备用模型必须实际发出保存请求");
    assert.deepStrictEqual(saved.body, {
      id: "draw-image", model: defaultModel, backup_models: ["backup-choice"],
    }, "修改备用不能把名单第一项保存成主模型");
    assert.strictEqual(ui.el("abilityModel").value, defaultModel, "保存后继续显示默认主模型");
  } else {
    assert.strictEqual(ui.el("abilityModel").value,
      scenario === "explicit-model" ? "explicit-main" : defaultModel,
      "未写主模型时选积木块默认；写了主模型则优先用卡片上的值");
  }
  assert.deepStrictEqual(ui.alerts, []);
}

async function trialCase() {
  const jobs = new Map();
  const models = ["model-a", "model-b"];
  const ui = page("blocks.js", (url, query, body) => {
    if (url === "/api/blocks/list") return { blocks: [], broken: [], pending: [] };
    if (url === "/api/blocks/detail") {
      const id = query.get("id");
      return {
        card: { id, name: id, kind: "cli", role: "agent", command: id === "cursor" ? "agent" : id, model: models[0] },
        verified: true, category: "cli_agent", status: "listed", model_status: {},
        last_check: { ok: false, status: "listed", models, models_from: "tool", reason: "模型没通", how: "试其他模型",
          seconds: 1, time: "2026-11-12T10:00:00" },
      };
    }
    if (url === "/api/blocks/check") {
      jobs.set(body.id, {});
      return { job: body.id };
    }
    if (url === "/api/blocks/job") {
      const job = jobs.get(query.get("id"));
      return new Promise(resolve => { job.finish = () => resolve({ done: true, result: { ok: true } }); });
    }
    throw new Error("Unexpected HTTP request: " + url);
  });
  const buttons = () => ui.el("detailCliModels").querySelectorAll(".trial-btn");
  async function open(id) {
    const item = { closest: () => item, getAttribute: key => key === "data-block" ? id : null };
    ui.el("blocksList").listeners.click({ target: item });
    await settle();
    assert.strictEqual(ui.el("detailTitle").textContent, id, "目标块详情应完成渲染");
    assert.strictEqual(buttons().length, 2);
  }
  function busy(expected, message) {
    assert.strictEqual(ui.el("detailCheck").disabled, expected, message + "：检测按钮");
    assert.ok(buttons().every(b => b.disabled === expected), message + "：所有模型试用按钮");
    if (expected) assert.ok(buttons().some(b => /正在试/.test(b.textContent)), message + "：显示进行中");
  }
  await open("agy");
  buttons()[0].click();
  await settle();
  busy(true, "A 开始试用");
  await open("cursor");
  busy(false, "A 在试用不应禁用 B");
  await open("agy");
  busy(true, "A 试用期间切到 B 再回 A 仍禁用");

  await open("cursor");
  buttons()[1].click();
  await settle();
  busy(true, "B 也开始试用");
  await open("agy");
  busy(true, "B 的试用不能覆盖 A 的进行中状态");
  jobs.get("cursor").finish();
  await settle();
  await open("agy");
  busy(true, "B 先完成不能清除 A 的禁用状态");
  jobs.get("agy").finish();
  await settle();
  await open("agy");
  busy(false, "A 完成后重新启用");
  await open("cursor");
  busy(false, "B 完成后重新启用");
  assert.deepStrictEqual(ui.posts.filter(p => p.url === "/api/blocks/check").map(p => p.body),
    [{ id: "agy", model: "model-a" }, { id: "cursor", model: "model-b" }]);
  assert.deepStrictEqual(ui.alerts, []);
}

(scenario === "parallel-trials" ? trialCase() : abilityCase()).catch(error => {
  console.error(error.stack);
  process.exitCode = 1;
});
