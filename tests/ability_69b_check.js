// 施工单 69b：从可见页面和真实请求验证验收行为，不要求新增私有测试 API。
"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const { page, settle, visible, text, STATIC } = require("./ability_69b_dom.js");
const scenario = process.argv[2];
const clone = x => JSON.parse(JSON.stringify(x));
const example = "今天天气很好，我们去公园散步吧。";
const translated = "The weather is lovely today.";
function card(type = "text", kind = "llm_api") {
  return {
    id: "translate-en", title: "翻译成英文", description: "把中文翻译成英文",
    block: "bailian", model: "qwen3.7-plus", backup_models: ["qwen-flash"],
    inputSchema: { type: "object", properties: {
      text: { type: "string", title: "要翻译的中文", maxLength: 4000, examples: [example] },
      count: { type: "integer", title: "份数", minimum: 1, maximum: 5, examples: [2] },
      style: { type: "string", title: "风格", enum: ["简洁", "正式"], examples: ["正式"] },
    }, required: ["text", "count"] },
    how: kind === "search" ? { kind, query: "找 {text}" } :
      { kind, system: "只输出译文", prompt: "翻译下面的话：{text}" },
    output: { type, check: {} },
  };
}
function proposal(changes = {}) {
  return Object.assign({ type: "proposal", kind: "ability", id: "proposal69b", state: "open",
    ability: card(), name: "翻译成英文", block_name: "阿里百炼", warns: ["提醒甲", "提醒乙"],
    replaces: "旧翻译", saved: false }, changes);
}
function result(type = "text", ok = true) {
  return { ok, status: ok ? "ok" : "format", reason: ok ? "" : "字数不够",
    run_id: "run69b", seconds: 2, model: "qwen3.7-plus",
    output: { text: translated, list: [{ title: "公园 A", url: "https://a.invalid/", snippet: "适合散步" },
      { title: "公园 B", url: "https://b.invalid/", snippet: "适合休息" }],
      image: "输出 图片.png", file: "说明 文件.txt" }[type],
    files: ["image", "file"].includes(type) ? [type === "image" ? "输出 图片.png" : "说明 文件.txt"] : [],
  };
}
function trial(r = result()) {
  return Object.fromEntries(["ok", "status", "reason", "run_id", "seconds", "model"].map(k => [k, r[k]]));
}
function requests(ui, pathname) { return ui.requests.filter(r => r.url.pathname === pathname); }
function box(ui) {
  const el = ui.document.querySelector('[data-proposal="proposal69b"]');
  assert.ok(el, "聊天应渲染能力提议框");
  return el;
}
function button(root, label) {
  return root.querySelectorAll("button").find(b => visible(b) && text(b).trim() === label);
}
function click(root, label) {
  const b = button(root, label);
  assert.ok(b, "应显示按钮：" + label);
  assert.strictEqual(b.disabled, false, label + "应可点击");
  b.click();
}
function fields(ui) { return box(ui).querySelectorAll("input,textarea,select"); }
function input(ui, name) {
  const el = fields(ui).find(e => e.name === name || e.getAttribute("data-name") === name ||
    e.getAttribute("data-field") === name || e.getAttribute("data-input") === name ||
    e.id.endsWith("-" + name) || e.id.endsWith("_" + name)) || fields(ui)[["text", "count", "style"].indexOf(name)];
  assert.ok(el, "inputSchema 应生成输入项：" + name);
  return el;
}
async function chat(events = [proposal()], options = {}) {
  const eventMap = options.eventMap || { "conversation-1": events, "conversation-new": events };
  let ui;
  ui = page("chat.html", {
    search: options.search ?? "?project=fixture-home&token=fixture-token&clerk=1&butler=1",
    parent: options.parent,
    respond(req) {
      const custom = options.respond?.(req);
      if (custom !== undefined) return custom;
      switch (req.url.pathname) {
        case "/api/clerk/list": return { threads: options.blank ? [] : [{ id: "conversation-1", title: "已有对话" }] };
        case "/api/clerk/focus": return { ok: true };
        case "/api/clerk/send": return { conversation: "conversation-new" };
        case "/api/clerk/events": {
          if (+req.url.searchParams.get("since") > 0) return new Promise(() => {});
          const stream = eventMap[req.url.searchParams.get("conversation")] || [];
          return { events: clone(stream).map((e, i) => ({ ...e, seq: i + 1 })), open: true, busy: false };
        }
        case "/api/clerk/proposal": return { ok: true, state: "confirmed", job: "job69b" };
        case "/api/blocks/job": return { job: "job69b", kind: "trial", done: true, saved: false, error: "", result: result() };
        case "/api/blocks/state": return { saved_accounts: [] };
        case "/api/abilities/save": return { ok: true, id: "translate-en" };
        case "/api/abilities/reveal": return { ok: true };
        case "/api/abilities/detail": return { ability: events[0]?.ability || card(), example: result(), runs: [result()], block: {} };
        default: throw new Error("不应请求：" + req.url.pathname);
      }
    },
  });
  await settle();
  return ui;
}
async function startFromBlock() {
  const bad = scenario === "start-400";
  const ui = await chat([], { eventMap: {
    "conversation-1": [{ type: "user", text: "旧对话独有消息" }], "conversation-new": [],
  }, respond: r =>
    bad && r.url.pathname === "/api/clerk/send" ? { status: 400, data: { error: "基础层里没有这个积木块" } } : undefined });
  const oldPolls = requests(ui, "/api/clerk/events").length;
  assert.strictEqual(typeof ui.context.SuperAgentChat.startFromBlock, "function", "必须暴露 startFromBlock");
  ui.context.SuperAgentChat.startFromBlock("bailian", "阿里百炼");
  await settle();
  const send = requests(ui, "/api/clerk/send");
  assert.strictEqual(send.length, 1);
  assert.strictEqual(send[0].body.block, "bailian");
  assert.strictEqual(send[0].body.conversation, "", "从积木块开全新对话");
  assert.strictEqual(send[0].body.text, "我想从「阿里百炼」提取能力卡片");
  assert.strictEqual(requests(ui, "/api/chat/open").length, 0);
  if (bad) {
    assert.match(text(ui.el("msgs")), /基础层里没有这个积木块/);
    assert.strictEqual(requests(ui, "/api/clerk/events").length, oldPolls, "400 不轮询空对话");
    assert.ok(!requests(ui, "/api/clerk/focus").some(r => r.body.conversation === "conversation-new"), "400 不选中虚构对话");
    ui.el("input").value = "普通消息";
    ui.el("composer").dispatchEvent({ type: "submit" }); await settle();
    assert.strictEqual(requests(ui, "/api/clerk/send")[1].body.conversation, "", "报错后仍是未保存的新对话");
  } else {
    assert.ok(requests(ui, "/api/clerk/events").some(r => r.url.searchParams.get("conversation") === "conversation-new"));
    assert.ok(requests(ui, "/api/clerk/focus").some(r => r.body.conversation === "conversation-new"));
  }
  assert.ok(!text(ui.el("msgs")).includes("旧对话独有消息"), "提取入口先清空当前对话");
  ui.check();
}
async function modeGuard() {
  for (const search of ["?clerk=1", "", "?butler=1"]) {
    const ui = await chat([], { search });
    assert.strictEqual(typeof ui.context.SuperAgentChat.startFromBlock, "function");
    ui.context.SuperAgentChat.startFromBlock("bailian", "阿里百炼"); await settle();
    assert.strictEqual(requests(ui, "/api/clerk/send").length, 0, "项目窗口及行政模式禁止提取能力");
    assert.strictEqual(requests(ui, "/api/chat/open").length, 0);
    ui.check();
  }
}
async function draftView() {
  const kind = scenario === "draft-search" ? "search" : scenario === "draft-cli" ? "cli" : "llm_api";
  const ability = card(kind === "search" ? "list" : "text", kind);
  if (kind === "search") { delete ability.model; delete ability.backup_models; }
  const ui = await chat([proposal({ ability })]);
  const b = box(ui), content = text(b);
  for (const part of ["能力卡片（草稿）「翻译成英文」", "把中文翻译成英文", "用哪个积木块", "阿里百炼",
    "用哪个模型", "交回什么", kind === "search" ? "列表" : "文字", "放进能力层后会替换原来的「旧翻译」", "提醒甲", "提醒乙"]) {
    assert.ok(content.includes(part), "草稿应显示：" + part);
  }
  if (kind === "search") { assert.match(content, /不涉及/); assert.match(content, /搜什么/); assert.match(content, /找 \{text\}/); }
  else {
    assert.match(content, /qwen3\.7-plus/); assert.match(content, /备用/); assert.match(content, /qwen-flash/);
    const details = b.querySelectorAll("details").find(d => d.textContent.includes("交代原文"));
    assert.ok(details, "应有可展开的交代原文");
    assert.strictEqual(details.open, true, "交代原文默认展开");
    assert.match(text(details), /翻译下面的话：\{text\}/);
    if (kind === "llm_api") assert.match(text(details), /只输出译文/);
  }
  assert.strictEqual(input(ui, "text").tagName, "TEXTAREA");
  assert.strictEqual(input(ui, "text").value, example, "examples 是预填值，不能仅作为 placeholder");
  assert.strictEqual(input(ui, "count").type, "number");
  assert.strictEqual(input(ui, "count").value, "2");
  assert.strictEqual(input(ui, "style").tagName, "SELECT");
  assert.strictEqual(input(ui, "style").value, "正式");
  assert.deepStrictEqual(input(ui, "style").querySelectorAll("option").map(o => o.value).filter(Boolean), ["简洁", "正式"]);
  assert.ok(button(b, "试做")); assert.ok(button(b, "先不做"));
  assert.strictEqual(b.querySelectorAll("[contenteditable],input[type=password]").length, 0);
  assert.strictEqual(b.querySelectorAll("select").length, 1, "草稿不提供模型修改框");
  ui.check();
}
async function localValidation() {
  const ui = await chat();
  const name = scenario === "input-number" ? "count" : "text";
  input(ui, name).value = name === "count" ? "99" : "";
  click(box(ui), "试做"); await settle();
  assert.strictEqual(requests(ui, "/api/clerk/proposal").length, 0, "本地缺项/越界时不能发确认");
  assert.match(text(box(ui)), name === "count" ? /份数/ : /请填写.*要翻译的中文/);
  assert.strictEqual(input(ui, name).disabled, false, "提示后输入框保持可改");
  assert.ok(button(box(ui), "试做"));
  ui.check();
}
async function trialError() {
  const status = scenario === "trial-409" ? 409 : 400;
  const reason = status === 400 ? "例子格式不对，请重新填写" : "这个积木块正在用";
  let calls = 0;
  const ui = await chat([proposal()], { respond: r => {
    if (r.url.pathname === "/api/clerk/proposal" && ++calls === 1) return { status, data: { error: reason } };
  } });
  click(box(ui), "试做"); await settle();
  assert.ok(text(box(ui)).includes(reason), "接口错误要在草稿框内显示");
  assert.strictEqual(input(ui, "text").disabled, false);
  assert.strictEqual(requests(ui, "/api/blocks/job").length, 0, "拒绝确认不启动任务轮询");
  if (status === 400) {
    input(ui, "text").value = "修正后的例子";
    click(box(ui), "试做"); await settle();
    const posts = requests(ui, "/api/clerk/proposal");
    assert.strictEqual(posts.length, 2, "400 后可修改并再次试做");
    assert.strictEqual(posts[1].body.inputs.text, "修正后的例子");
  }
  ui.check();
}
async function trialAndPoll() {
  let done = false;
  const p = proposal();
  const ui = await chat([p], { eventMap: { "conversation-1": [p], other: [] }, respond: r => {
    if (r.url.pathname === "/api/clerk/proposal") {
      p.state = "confirmed";
      return { ok: true, state: "confirmed", job: "job69b" };
    }
    if (r.url.pathname === "/api/blocks/job") return {
      job: "job69b", kind: "trial", done, error: "", seconds: done ? 2 : 0,
      result: done ? result() : null, saved: false,
    };
  } });
  const first = button(box(ui), "试做");
  assert.ok(first, "开放草稿应有试做按钮");
  first.click(); first.click(); await settle();
  const confirms = requests(ui, "/api/clerk/proposal");
  assert.strictEqual(confirms.length, 1, "连点不重复确认");
  assert.strictEqual(confirms[0].body.id, "proposal69b");
  assert.strictEqual(confirms[0].body.conversation, "conversation-1");
  assert.strictEqual(confirms[0].body.decision, "confirm");
  assert.deepStrictEqual(confirms[0].body.inputs, { text: example, count: 2, style: "正式" });
  assert.match(text(box(ui)), /正在试做/);
  const before = requests(ui, "/api/blocks/job").length;
  await ui.advance(1499);
  assert.strictEqual(requests(ui, "/api/blocks/job").length, before, "未到 1.5 秒不重查");
  assert.match(text(box(ui)), /秒|用时|计时/, "试做中显示计时");
  await ui.advance(1);
  assert.strictEqual(requests(ui, "/api/blocks/job").length, before + 1);
  assert.ok(requests(ui, "/api/blocks/job").every(r => r.url.searchParams.get("proposal") === "proposal69b"), "按提议编号查询");
  ui.context.SuperAgentChat.openThread("other"); await settle();
  ui.context.SuperAgentChat.openThread("conversation-1"); await settle();
  const restored = requests(ui, "/api/blocks/job").length;
  done = true;
  await ui.advance(1500);
  assert.strictEqual(requests(ui, "/api/blocks/job").length, restored + 1, "同一任务切回来不能开两套轮询");
  assert.match(text(box(ui)), /The weather is lovely today\./);
  assert.ok(button(box(ui), "放进能力层"));
  const completed = requests(ui, "/api/blocks/job").length;
  await ui.advance(4500);
  assert.strictEqual(requests(ui, "/api/blocks/job").length, completed, "任务结束停止轮询");
  ui.check();
}
async function failedTrial() {
  const r = result("text", false);
  const ui = await chat([proposal()], { respond: req => req.url.pathname === "/api/blocks/job" ?
    { job: "job69b", kind: "trial", done: true, error: "", result: r, saved: false } : undefined });
  click(box(ui), "试做"); await settle();
  assert.match(text(box(ui)), /字数不够/);
  assert.match(text(box(ui)), /跟管家说哪里不对，它会改了重新出草稿/);
  assert.strictEqual(button(box(ui), "放进能力层"), undefined);
  assert.strictEqual(requests(ui, "/api/abilities/save").length, 0);
  ui.check();
}
async function outputView() {
  const type = scenario.slice("output-".length);
  const r = result(type), ability = card(type, type === "list" ? "search" : type === "text" ? "llm_api" : "cli");
  const ui = await chat([proposal({ ability })], { respond: req => req.url.pathname === "/api/blocks/job" ?
    { job: "job69b", kind: "trial", done: true, error: "", result: r, saved: false } : undefined });
  click(box(ui), "试做"); await settle();
  const b = box(ui);
  assert.ok(button(b, "放进能力层"), "试做成功允许放进能力层");
  assert.strictEqual(button(b, "试做"), undefined, "成功后不支持更换例子再试");
  if (type === "text") assert.ok(text(b).includes(translated));
  if (type === "list") {
    assert.deepStrictEqual(b.querySelectorAll("li a").map(a => a.textContent), ["公园 A", "公园 B"]);
    assert.deepStrictEqual(b.querySelectorAll("li a").map(a => a.href), ["https://a.invalid/", "https://b.invalid/"]);
    assert.match(text(b), /适合散步/);
  }
  if (type === "image" || type === "file") {
    const target = type === "image" ? b.querySelector("img") : b.querySelectorAll("a").find(a => a.href.includes("/api/abilities/file"));
    assert.ok(target, "成果应有图片或文件链接");
    const url = new URL(type === "image" ? target.src : target.href, "http://fixture.invalid");
    assert.strictEqual(url.pathname, "/api/abilities/file");
    assert.strictEqual(url.searchParams.get("id"), "translate-en");
    assert.strictEqual(url.searchParams.get("run"), "run69b");
    assert.strictEqual(url.searchParams.get("name"), r.output);
    assert.strictEqual(url.searchParams.get("token"), "fixture-token");
    click(b, "在访达里打开"); await settle();
    const reveal = requests(ui, "/api/abilities/reveal");
    assert.strictEqual(reveal.length, 1);
    assert.strictEqual(reveal[0].body.id, "translate-en");
    assert.strictEqual(reveal[0].body.run, "run69b");
  }
  ui.check();
}
async function saveCard() {
  const conflict = scenario === "save-409", repeat = scenario === "save-repeat";
  const ui = await chat([proposal({ state: "confirmed", trial: trial() })], { respond: r => {
    if (r.url.pathname === "/api/abilities/save") return conflict ?
      { status: 409, data: { error: "能力层里已经有同编号的能力卡片：跟管家说重新起草" } } :
      { ok: true, id: "translate-en", repeat };
  } });
  click(box(ui), "放进能力层"); await settle();
  assert.deepStrictEqual(requests(ui, "/api/abilities/save").map(r => r.body.proposal), ["proposal69b"]);
  if (conflict) {
    assert.match(text(box(ui)), /同编号.*跟管家说重新起草/);
    assert.ok(!text(box(ui)).includes("已放进能力层"));
  } else {
    assert.match(text(box(ui)), /已放进能力层/);
    assert.strictEqual(button(box(ui), "放进能力层"), undefined);
  }
  ui.check();
}
async function recovery() {
  const state = scenario.slice("recover-".length);
  let p = proposal(), job;
  if (["success", "saved", "failure"].includes(state)) {
    p.state = "confirmed"; p.trial = trial(result("text", state !== "failure")); p.saved = state === "saved";
    job = { job: "job69b", kind: "trial", done: true, result: result("text", state !== "failure"), saved: p.saved, error: "" };
  } else if (["running", "lost"].includes(state)) {
    p.state = "confirmed";
    job = state === "lost" ? { status: 404, data: { error: "没有这个任务" } } :
      { job: "job69b", kind: "trial", done: false, result: null, saved: false, error: "" };
  } else p.state = state; // cancelled / voided
  for (let opening = 0; opening < 2; opening++) { // 全新的 JS 上下文，不依赖上一页的内存
    const ui = await chat([p], { respond: req => req.url.pathname === "/api/blocks/job" ? job : undefined });
    const content = text(box(ui));
    if (state === "success") { assert.ok(button(box(ui), "放进能力层")); assert.ok(content.includes(translated)); }
    if (state === "saved") { assert.match(content, /已放进能力层/); assert.strictEqual(button(box(ui), "放进能力层"), undefined); }
    if (state === "failure") {
      assert.match(content, /字数不够/); assert.match(content, /跟管家说哪里不对，它会改了重新出草稿/);
      assert.strictEqual(button(box(ui), "放进能力层"), undefined);
    }
    if (state === "running") {
      assert.match(content, /正在试做/);
      const count = requests(ui, "/api/blocks/job").length;
      await ui.advance(1500);
      assert.strictEqual(requests(ui, "/api/blocks/job").length, count + 1, "重开后继续查进度");
    }
    if (state === "lost") assert.match(content, /这次试做的结果没留下来（超级助手重开过）。跟管家说再出一次草稿。/);
    if (state === "cancelled") assert.match(content, /先不做了/);
    if (state === "voided") assert.match(content, /已失效/);
    if (["cancelled", "voided"].includes(state)) {
      assert.strictEqual(requests(ui, "/api/blocks/job").length, 0);
      assert.strictEqual(button(box(ui), "试做"), undefined);
    }
    ui.check();
  }
}
// 追加验收：只从页面入口重开草稿，HTTP/时钟用现有边界替代。
// 捕捉：重画触发重复请求、非 404 误报丢失、重试过早/停止、成果恢复失败。
async function recoveryQueryContract() {
  const [, phase, failure] = scenario.split("-");
  const file = phase === "file";
  const endpoint = file ? "/api/abilities/file" : "/api/blocks/job";
  const p = proposal({ state: "confirmed", ...(file ? { trial: trial() } : {}) });
  const missing = { status: 404, data: { error: "没有这个成果文件" } };
  const done = { job: "job69b", kind: "trial", done: true, result: result(), saved: false, error: "" };
  const running = { ...done, done: false, result: null };
  const restored = { status: 200, ok: true, json: async () => ({}), text: async () => translated };
  const offline = new TypeError("fixture: local service disconnected");
  let attempt = 0, release;
  const expectedErrors = [];
  const ui = await chat([p], { eventMap: { "conversation-1": [p], other: [] }, respond: req => {
    if (file && req.url.pathname === "/api/blocks/job") return missing;
    if (req.url.pathname !== endpoint) return;
    attempt++;
    if (phase === "poll" && attempt === 1) return running;
    if (failure === "404") return missing;
    if (failure !== "pending" && attempt === (phase === "poll" ? 2 : 1)) {
      if (failure === "offline") { expectedErrors.push(offline); return Promise.reject(offline); }
      if (failure === "503") return { status: 503, data: { error: "暂时不可用" } };
      throw new Error("未知故障类型：" + failure);
    }
    // 第二次请求保持在途，使错误实现的立即重入也能明确失败，而不陷入无限微任务。
    return new Promise(resolve => { release = resolve; });
  } });
  const lookups = () => ui.requests.filter(r => ["/api/blocks/job", "/api/abilities/file"].includes(r.url.pathname));
  const targetRequests = () => requests(ui, endpoint);
  async function redraw() {
    ui.context.SuperAgentChat.openThread("other"); await settle();
    ui.context.SuperAgentChat.openThread("conversation-1"); await settle();
    box(ui);
  }
  function checkBoundary() {
    assert.deepStrictEqual(ui.failures, expectedErrors, "只允许本场景主动注入的网络错误");
    assert.strictEqual(requests(ui, "/api/clerk/proposal").length, 0, "恢复查询不得重新试做");
    assert.strictEqual(requests(ui, "/api/abilities/save").length, 0, "恢复查询不得自动保存");
    for (const req of lookups()) {
      assert.strictEqual(req.url.searchParams.get("token"), "fixture-token");
      if (req.url.pathname === "/api/blocks/job") assert.strictEqual(req.url.searchParams.get("proposal"), "proposal69b");
      else {
        assert.strictEqual(req.url.searchParams.get("id"), "translate-en");
        assert.strictEqual(req.url.searchParams.get("run"), "run69b");
        assert.strictEqual(req.url.searchParams.get("name"), "结果.txt");
      }
    }
  }
  if (phase === "poll") await ui.advance(1500);
  const initial = phase === "poll" ? 2 : 1;
  assert.strictEqual(targetRequests().length, initial, "一次重开/轮询只发一个查询，出错重画不能立刻重入");

  if (failure === "404") {
    assert.match(text(box(ui)), /结果没留下来/, "文件明确 404 才显示结果没留下来");
    assert.ok(!text(box(ui)).includes("没连上本机服务"));
    const count = lookups().length;
    await redraw(); await ui.advance(9000);
    assert.strictEqual(lookups().length, count, "明确丢失后重画不再重复查询");
    checkBoundary();
    return;
  }
  if (failure === "pending") {
    const count = lookups().length;
    await redraw(); await ui.advance(9000); await redraw();
    assert.strictEqual(lookups().length, count, "请求尚未返回时，重画和定时器不能并发查同一草稿");
  } else {
    assert.ok(text(box(ui)).includes("没连上本机服务，过一会儿再试…"), "暂时故障必须在草稿框内提示可重试");
    assert.ok(!text(box(ui)).includes("结果没留下来"), "断网/503 不能误报成果丢失");
    const count = lookups().length;
    await ui.advance(1000); await redraw();
    assert.strictEqual(lookups().length, count, "等待重试时重新显示页面不得立即查询");
    await ui.advance(1999);
    assert.strictEqual(lookups().length, count, "故障后 2999 毫秒内不发第二次查询");
    await ui.advance(1);
    assert.strictEqual(targetRequests().length, initial + 1, "故障后 3 秒必须继续查询");
    assert.strictEqual(targetRequests().at(-1).at - targetRequests().at(-2).at, 3000, "失败重试间隔为 3 秒");
    const inFlight = lookups().length;
    await redraw(); await ui.advance(3000);
    assert.strictEqual(lookups().length, inFlight, "重试已在途时不能因重画或等待再发查询");
  }
  assert.strictEqual(typeof release, "function", "页面应发出等待网络恢复的查询");
  release(file ? restored : done); await settle();
  assert.ok(text(box(ui)).includes(translated), "网络恢复/在途请求返回后补回真实结果正文");
  assert.ok(button(box(ui), "放进能力层"), "补回成功结果后允许保存");
  assert.ok(!text(box(ui)).includes("没连上本机服务"), "恢复成功后清掉临时断网提示");
  assert.ok(!text(box(ui)).includes("结果没留下来"));
  const finished = lookups().length;
  await redraw(); await ui.advance(9000);
  assert.strictEqual(lookups().length, finished, "结果补全后停止查询，重画也不另发请求");
  assert.ok(text(box(ui)).includes(translated));
  checkBoundary();
}
async function cancel() {
  const ui = await chat([proposal()], { respond: r => r.url.pathname === "/api/clerk/proposal" ?
    { ok: true, state: "cancelled" } : undefined });
  click(box(ui), "先不做"); await settle();
  const sent = requests(ui, "/api/clerk/proposal");
  assert.strictEqual(sent.length, 1); assert.strictEqual(sent[0].body.decision, "cancel");
  assert.strictEqual(sent[0].body.id, "proposal69b"); assert.strictEqual(sent[0].body.conversation, "conversation-1");
  assert.strictEqual(requests(ui, "/api/blocks/job").length, 0);
  assert.strictEqual(requests(ui, "/api/abilities/save").length, 0);
  ui.check();
}
async function blockDetail() {
  const role = scenario.slice("block-".length);
  let working = role === "busy";
  const calls = [];
  const type = ["search", "llm_api"].includes(role) ? role : "cli";
  const block = { id: "block69b", name: "测试积木块", kind: type, role: role === "tool" ? "tool" : "agent", command: "fixture" };
  if (type !== "cli") delete block.role;
  const ui = page("blocks.html", {
    search: "?token=fixture-token",
    before(doc) {
      doc.getElementById("chatFrame").contentWindow = { SuperAgentChat: {
        isWorking: () => working, startFromBlock: (...args) => calls.push(args),
      } };
    },
    respond: r => {
      if (r.url.pathname === "/api/abilities/list") return { abilities: [], broken: [] };
      if (r.url.pathname === "/api/blocks/state") return {
        connected: true, setting: { provider: "fixture", model: "fixture" }, home: "fixture-home", providers: [], saved_accounts: [],
      };
      if (r.url.pathname === "/api/clerk/list") return { threads: [] };
      if (r.url.pathname === "/api/blocks/scan") return { job: "scan69b" };
      if (r.url.pathname === "/api/blocks/job") return { job: "scan69b", done: true, error: "", result: { items: [] } };
      if (r.url.pathname === "/api/blocks/list") return { blocks: [block], pending: [], broken: [] };
      if (r.url.pathname === "/api/blocks/detail") return {
        card: block, category: role === "tool" ? "tool" : type === "cli" ? "agent" : type,
        verified: false, key_account: "", secrets: [], preview: "", last_check: role === "failed" ?
          { ok: false, status: "fail", reason: "没登录", time: "2026-11-13T10:00:00", seconds: 1 } : null,
      };
      throw new Error("不应请求：" + r.url.pathname);
    },
  });
  // 从真正的左栏点击事件进入详情，不调用 openDetail 这类私有函数。
  await settle(); ui.check();
  const row = ui.el("blocksList").querySelector('[data-block="block69b"]');
  assert.ok(row, "基础层列表应由真实页面渲染");
  row.click(); await settle(); ui.check();
  assert.strictEqual(ui.el("detailTitle").textContent, "测试积木块", "详情已加载");
  const extract = button(ui.el("blockDetail"), "提取能力卡片");
  if (role === "tool") assert.strictEqual(extract, undefined, "普通工具不能提取能力");
  else {
    assert.ok(extract, "未检测、检测失败和 AI/搜索积木块均显示提取入口");
    assert.strictEqual(extract.closest(".detail-actions"), ui.el("detailCheck").closest(".detail-actions"), "和检测、移出基础层同一排");
    if (working) {
      assert.strictEqual(extract.disabled, true); assert.match(text(ui.el("blockDetail")), /管家还在回话，等它说完再点/);
      extract.click(); assert.deepStrictEqual(calls, []);
    } else {
      extract.click(); await settle(); assert.deepStrictEqual(calls, [["block69b", "测试积木块"]]);
      assert.strictEqual(ui.el("blockDetail").hidden, true); assert.strictEqual(ui.el("chatFrame").hidden, false);
    }
  }
  ui.check();
}
async function coreCompatibility() {
  assert.ok(fs.existsSync(path.join(STATIC, "abilities_core.js")), "需提取公共 AbilitiesCore 文件");
  const core = require(path.join(STATIC, "abilities_core.js"));
  const old = require(path.join(STATIC, "abilities.js"));
  assert.strictEqual(old, core, "旧 require 路径继续导出同一个规则层");
  assert.strictEqual(core.outputText("image"), "图片");
  assert.strictEqual(core.promptText(card()), "系统：只输出译文\n\n要求：翻译下面的话：{text}");
  assert.strictEqual(core.collectInputs(core.formFields(card().inputSchema), { text: "" }).ok, false);
  for (const html of ["chat.html", "blocks.html"]) {
    const source = fs.readFileSync(path.join(STATIC, html), "utf8");
    const scripts = [...source.matchAll(/<script[^>]+src=["']([^"']+)/g)].map(m => path.basename(m[1]));
    assert.ok(scripts.includes("abilities_core.js"));
    assert.ok(scripts.indexOf("abilities_core.js") < scripts.indexOf(html === "chat.html" ? "chat.js" : "abilities.js"));
    if (html === "chat.html") assert.ok(!scripts.includes("abilities.js"), "聊天页只加载公共规则层");
  }
}
async function httpFlow() {
  const base = process.argv[3], home = process.argv[4];
  assert.match(base, /^http:\/\/127\.0\.0\.1:\d+$/);
  const forward = async req => {
    const url = new URL(req.url.pathname + req.url.search, base);
    if (url.pathname.endsWith("/events")) url.searchParams.set("wait", "0");
    return fetch(url, req.options);
  };
  // 同一份 blocks.html 里的真实能力层列表；省去基础层启动体检，避免无关工具执行。
  const parent = page("blocks.html", {
    search: "?" + new URLSearchParams({ token: "口令" }),
    before: doc => { doc.getElementById("mainLayout").hidden = false; },
    scriptFilter: filename => filename !== "blocks.js", respond: forward,
  });
  const ui = page("chat.html", {
    search: "?" + new URLSearchParams({ project: home, token: "口令", clerk: "1", butler: "1" }),
    parent: parent.context, respond: forward,
  });
  async function until(predicate, message) {
    for (let i = 0; i < 150; i++) {
      await new Promise(r => setTimeout(r, 10)); await ui.advance(100);
      if (predicate()) return;
    }
    ui.check(); assert.fail(message);
  }
  await until(() => requests(ui, "/api/clerk/focus").length > 0, "聊天初始化没完成");
  assert.strictEqual(typeof ui.context.SuperAgentChat.startFromBlock, "function", "完整流程需要提取能力入口");
  ui.context.SuperAgentChat.startFromBlock("bailian", "阿里百炼");
  await until(() => ui.document.querySelector("[data-proposal]"), "假管家没有显示能力草稿");
  const actualBox = () => ui.document.querySelector("[data-proposal]");
  assert.match(text(actualBox()), /能力卡片（草稿）「翻译成英文」/);
  assert.strictEqual(actualBox().querySelector("textarea").value, example);
  click(actualBox(), "试做");
  await until(() => button(actualBox(), "放进能力层"), "真实本机接口试做后没有保存按钮");
  assert.match(text(actualBox()), /The weather is lovely today\./);
  click(actualBox(), "放进能力层");
  await until(() => text(actualBox()).includes("已放进能力层"), "保存后未显示已放进能力层");
  const get = async pathname => (await fetch(base + pathname + "?" + new URLSearchParams({ token: "口令" }))).json();
  const list = await get("/api/abilities/list");
  assert.ok(list.abilities.some(a => a.id === "translate-en"), "真实能力层列表应出现已保存卡片");
  await parent.advance(5000);
  await until(() => text(parent.el("abilitiesList")).includes("翻译成英文"), "保存后真实左栏能力层列表未出现新卡");
  const conversation = requests(ui, "/api/clerk/send")[0].body;
  assert.strictEqual(conversation.block, "bailian"); assert.strictEqual(conversation.conversation, "");
  const focus = requests(ui, "/api/clerk/focus").findLast(r => r.body.conversation);
  ui.context.SuperAgentChat.openThread("other"); await settle();
  ui.context.SuperAgentChat.openThread(focus.body.conversation);
  await until(() => actualBox() && text(actualBox()).includes("已放进能力层"), "切换回来未从事件恢复保存状态");
  ui.check(); parent.check();
}
async function main() {
  if (scenario === "core") await coreCompatibility();
  else if (scenario.startsWith("start-")) await startFromBlock();
  else if (scenario === "mode-guard") await modeGuard();
  else if (scenario.startsWith("draft-")) await draftView();
  else if (scenario.startsWith("input-")) await localValidation();
  else if (["trial-400", "trial-409"].includes(scenario)) await trialError();
  else if (scenario === "trial-failure") await failedTrial();
  else if (scenario === "poll") await trialAndPoll();
  else if (scenario.startsWith("output-")) await outputView();
  else if (scenario.startsWith("save-")) await saveCard();
  else if (scenario.startsWith("recover-")) await recovery();
  else if (scenario.startsWith("retry-")) await recoveryQueryContract();
  else if (scenario === "cancel") await cancel();
  else if (scenario.startsWith("block-")) await blockDetail();
  else if (scenario === "http-flow") await httpFlow();
  else throw new Error("未知验收场景：" + scenario);
  console.log("OK " + scenario);
}
main().catch(error => { console.error(error.stack); process.exitCode = 1; });
