// 71 号单：执行真实页面脚本，只替换 DOM、HTTP、计时器边界，不打开浏览器。
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const staticDir = path.join(__dirname, "..", "desktop", "static");
const scenario = process.argv[2];
const FAILURE = "查不到试用结果（本机服务可能重启过），请再试一次";
const settle = () => new Promise(resolve => setImmediate(resolve));

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
    this.value = "";
    this.textContent = "";
    this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
  }
  set innerHTML(value) { this._html = value; this.children = []; }
  get innerHTML() { return this._html || ""; }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  insertBefore(child) { return this.appendChild(child); }
  addEventListener(type, callback) { this.listeners[type] = callback; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] || null; }
  querySelectorAll(selector) {
    const match = element => selector[0] === "."
      ? element.className.split(" ").includes(selector.slice(1))
      : element.tagName === selector.toUpperCase();
    return this.children.flatMap(child => [...(match(child) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  click() { if (!this.disabled && this.listeners.click) this.listeners.click({ target: this }); }
  focus() {}
}

function page(file, respond = () => ({ abilities: [], broken: [] })) {
  const root = new Element();
  const elements = new Map();
  const timers = new Map();
  const alerts = [];
  let timerId = 0;
  const el = id => {
    if (!elements.has(id)) {
      const item = root.appendChild(new Element());
      item.id = id;
      elements.set(id, item);
    }
    return elements.get(id);
  };
  // 真实 HTML 里原本有的节点和显隐状态。
  el("detailCard").appendChild(new Element("pre"));
  el("abilityDetail").hidden = true;
  const context = {
    console, URLSearchParams, encodeURIComponent,
    location: { search: "" },
    document: { getElementById: el, createElement: tag => new Element(tag),
      querySelectorAll: selector => root.querySelectorAll(selector) },
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval() { return ++timerId; }, clearInterval() {},
    addEventListener() {},
    alert(message) { alerts.push(message); },
    fetch(url, options = {}) {
      const parsed = new URL(url, "http://test.invalid");
      return Promise.resolve().then(() => respond(parsed.pathname, parsed.searchParams, options))
        .then(reply => ({ ok: !(reply && reply.httpStatus >= 400), status: (reply && reply.httpStatus) || 200,
          json: () => Promise.resolve(reply) }));
    },
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(staticDir, "abilities_core.js"), "utf8"), context);
  let source = fs.readFileSync(path.join(staticDir, file), "utf8");
  // 仅在测试 VM 内暴露闭包入口，完整执行原函数；不重写任何产品判断或回调。
  const hooks = file === "abilities.js" ? "window.test71 = { render: renderResultInBox };"
    : file === "chat.js" ? "window.test71 = { render: renderAbilityResult };"
      : "window.test71 = { poll: pollModelTrialJob };";
  const end = source.lastIndexOf("})();");
  assert.ok(end >= 0, "应能找到页面闭包末尾");
  source = source.slice(0, end) + hooks + source.slice(end);
  vm.runInContext(source, context, { filename: file });
  return { context, el, root, timers, alerts, async tick(delay) {
    assert.strictEqual(timers.size, 1, "同一个任务只能排一个后续查询");
    const [id, timer] = timers.entries().next().value;
    if (delay !== undefined) assert.strictEqual(timer.delay, delay, "查询间隔沿用原规则");
    timers.delete(id);
    timer.fn();
    await settle();
  } };
}

function safeLinkCase() {
  const core = require(path.join(staticDir, "abilities_core.js"));
  assert.strictEqual(typeof core.safeLink, "function", "公共层缺少 safeLink");
  for (const [url, expected] of [
    ["http://example.com/a", "http://example.com/a"],
    ["https://example.com/a?q=1", "https://example.com/a?q=1"],
    ["HTTPS://example.com/A", "HTTPS://example.com/A"],
    [" \thttps://example.com/a\n", "https://example.com/a"],
    ["#", ""], ["", ""], [null, ""], [undefined, ""], [0, ""],
    ["javascript:", ""], ["JAVASCRIPT:alert(1)", ""], ["  javascript:x", ""],
    ["data:text/html,test", ""], ["file:///tmp/test", ""], ["//example.com/a", ""],
  ]) assert.strictEqual(core.safeLink(url), expected, "地址处理不对：" + url);
}

async function linksCase(file, spy = false) {
  const ui = page(file);
  await settle();
  const seen = [];
  if (spy) {
    // 换公共函数的返回值，验证两个真实渲染器确实按共用判断建链接。
    ui.context.AbilitiesCore.safeLink = url => {
      seen.push(url);
      return url === "allow-me" ? "https://shared.example/result" : "";
    };
  }
  const items = spy ? [{ title: "共用函数放行", url: "allow-me" },
    { title: "共用函数拒绝", url: "https://otherwise-valid.example" }] : [
    { title: "网页一", url: "http://example.com/a", summary: "摘要一" },
    { title: "网页二", url: " \tHTTPS://example.com/A\n", snippet: "摘要二" },
    ...["#", "", "javascript:alert(1)", "JAVASCRIPT:alert(1)", "  javascript:x",
      "data:text/html,hello", "file:///tmp/test"].map(url => ({ title: "只显示标题 <b>", url, snippet: "危险地址摘要" })),
    { url: "file:///tmp/no-title", summary: "没有标题也留摘要" }, {},
  ];
  const box = new Element();
  ui.context.test71.render(box, { ok: true, status: "ok", output: items }, { id: "search-web", output: { type: "list" } });
  const rows = box.querySelectorAll("li");
  assert.strictEqual(rows.length, items.length);
  items.forEach((item, i) => {
    const child = rows[i].children[0];
    const allowed = spy ? i === 0 : i < 2;
    assert.strictEqual(child.tagName, allowed ? "A" : "SPAN", "列表第 " + i + " 项的链接类型");
    assert.strictEqual(child.textContent, item.title || item.url || "无标题");
    if (allowed) {
      assert.strictEqual(child.href, spy ? "https://shared.example/result" : item.url.trim());
      assert.strictEqual(child.target, "_blank");
      assert.strictEqual(child.rel || child.getAttribute("rel"), "noopener");
    } else {
      assert.ok(!child.href && !child.getAttribute("href"), "纯标题不能留下可点的地址");
    }
    if (item.summary || item.snippet) assert.strictEqual(rows[i].querySelector("p").textContent, item.summary || item.snippet);
  });
  if (spy) assert.deepStrictEqual(seen, items.map(item => item.url), "每项地址都交给 safeLink");
}

async function trialCase() {
  let calls = 0;
  const done = [], errors = [];
  const result = { ok: true, text: "试用完成" };
  const normal = [{ done: false }, { done: true, result }];
  const steps = scenario === "trial-normal" ? normal
    : scenario === "trial-reset" ? ["offline", "offline", { done: false }, "offline", "offline", { done: true, result }]
      : scenario === "trial-job-error" ? [{ done: true, error: "模型不可用" }]
        : Array(3).fill(scenario === "trial-404" ? 404 : scenario === "trial-503" ? 503 : "offline");
  const ui = page("blocks.js", url => {
    assert.strictEqual(url, "/api/blocks/job");
    const step = steps[calls++];
    assert.ok(step !== undefined, "停止后不应再发查询");
    if (step === "offline") throw new Error("连接断开");
    if (typeof step === "number") return { httpStatus: step, error: "任务找不到或服务出错" };
    return step;
  });
  ui.context.test71.poll("job-71", "agy", (...args) => done.push(args),
    scenario === "trial-fallback" ? undefined : error => errors.push(error));
  await settle();
  for (let i = 1; i < steps.length; i++) {
    assert.strictEqual(done.length + errors.length, 0, "不能提前结束，第 " + i + " 次查询之后");
    await ui.tick(steps[i - 1] && steps[i - 1].done === false ? 500 : 1000);
  }
  assert.strictEqual(calls, steps.length);
  assert.strictEqual(ui.timers.size, 0, "结束后不能继续排查询");
  if (["trial-normal", "trial-reset"].includes(scenario)) {
    assert.strictEqual(errors.length, 0);
    assert.strictEqual(done.length, 1);
    assert.strictEqual(done[0][0], null);
    assert.strictEqual(done[0][1], result);
  } else if (scenario === "trial-fallback") {
    assert.strictEqual(errors.length, 0);
    assert.strictEqual(done.length, 1);
    assert.strictEqual(done[0][0].name, "Error");
    assert.strictEqual(done[0][0].message, FAILURE);
  } else {
    assert.strictEqual(done.length, 0);
    assert.strictEqual(errors.length, 1);
    assert.strictEqual(errors[0].name, "Error");
    assert.strictEqual(errors[0].message, scenario === "trial-job-error" ? "模型不可用" : FAILURE);
  }
}

async function trialButtonsCase() {
  let starts = 0, polls = 0;
  const ui = page("blocks.js", (url, query, options) => {
    if (url === "/api/blocks/list") return { blocks: [], broken: [], pending: [] };
    if (url === "/api/blocks/detail") return {
      card: { id: "agy", name: "agy", kind: "cli", command: "agy", role: "agent", model: "model-a" },
      verified: true, status: "listed", model_status: {},
      last_check: { ok: false, status: "listed", models: ["model-a", "model-b"], time: "2026-11-14T10:00:00" },
    };
    if (url === "/api/blocks/check") {
      assert.deepStrictEqual(JSON.parse(options.body), { id: "agy", model: "model-a" });
      return { job: "job-" + (++starts) };
    }
    if (url === "/api/blocks/job") { polls++; throw new Error("断开"); }
    throw new Error("不应有其他请求：" + url);
  });
  const item = { closest: () => item, getAttribute: name => name === "data-block" ? "agy" : null };
  ui.el("blocksList").listeners.click({ target: item });
  await settle();
  assert.strictEqual(ui.el("detailTitle").textContent, "agy", "先确认详情成功渲染");
  const buttons = () => ui.el("detailCliModels").querySelectorAll(".trial-btn");
  assert.strictEqual(buttons().length, 2);
  buttons()[0].click();
  await settle();
  assert.strictEqual(starts, 1);
  assert.ok(buttons().every(button => button.disabled));
  buttons()[0].click();
  assert.strictEqual(starts, 1, "进行中不能重复开始");
  await ui.tick(1000);
  await ui.tick(1000);
  assert.strictEqual(polls, 3);
  assert.strictEqual(ui.timers.size, 0, "三次失败后停止");
  assert.deepStrictEqual(ui.alerts, ["试用失败：" + FAILURE]);
  assert.strictEqual(ui.el("detailCheck").disabled, false);
  assert.strictEqual(ui.el("detailRemove").disabled, false);
  assert.ok(buttons().every(button => !button.disabled), "刷新后的模型试用按钮恢复");
  buttons()[0].click();
  await settle();
  assert.strictEqual(starts, 2, "清掉任务状态后可以再次试用");
}

(async () => {
  if (scenario === "safe-link") safeLinkCase();
  else if (scenario === "ability-links") await linksCase("abilities.js");
  else if (scenario === "chat-links") await linksCase("chat.js");
  else if (scenario === "shared-link") { await linksCase("abilities.js", true); await linksCase("chat.js", true); }
  else if (scenario === "trial-buttons") await trialButtonsCase();
  else if (["trial-normal", "trial-errors", "trial-reset", "trial-404", "trial-503", "trial-fallback", "trial-job-error"].includes(scenario)) await trialCase();
  else throw new Error("未知验收场景：" + scenario);
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
