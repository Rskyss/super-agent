/* 积木库管家：聊天页的规则层检查（动作摘要、网上安装卡的非官方两行）。由 test_blocks_butler.py 调用。 */
"use strict";
var assert = require("assert");
var path = require("path");
var C = require(path.join(__dirname, "..", "desktop", "static", "chat.js"));

// 1. 积木库的动作标签有自己的摘要，不再是“另有 N 个动作”
var s = C.actsSummary([
  { label: "查电脑", status: "done" }, { label: "查安装", status: "done" }, { label: "查安装", status: "done" },
  { label: "搜网上", status: "done" }, { label: "读网页", status: "done" }, { label: "翻手册", status: "done" },
  { label: "读帮助", status: "done" }, { label: "看基础层", status: "done" }, { label: "出卡", status: "done" }
]);
assert.ok(s.indexOf("另有") < 0, s);
["查了电脑", "查了 2 次安装", "搜了网上 1 次", "读了 1 个网页", "翻了 1 次手册", "读了帮助说明", "看了基础层", "出了 1 张卡"].forEach(function (w) {
  assert.ok(s.indexOf(w) >= 0, w + " 不在：" + s);
});
// 项目窗口的照旧
assert.strictEqual(C.actsSummary([{ label: "读 卡片", status: "done" }]), "读了 1 个文件");

// 2. 动作带 text：展开时显示大白话
var m = new C.Model();
m.apply({ type: "activity", item: "a1", label: "搜网上", status: "done", text: "搜网上：小红书 → 找到 5 条" });
var acts = m.blocks[m.blocks.length - 1].parts[0];
assert.strictEqual(acts.list[0].text, "搜网上：小红书 → 找到 5 条");
assert.strictEqual(C.actLine(acts.list[0]), "搜网上：小红书 → 找到 5 条");
assert.strictEqual(C.actLine({ label: "读 卡片" }), "读 卡片");

// 3. 网上安装卡：非官方、作者、风险
m.apply({ type: "proposal", id: "w1", kind: "web_install", state: "open", label: "从网上装 xhs", tool: "xhs",
  command: "npm install -g xhs-cli", source: "https://xhs-cli.com", domain: "xhs-cli.com", danger: false,
  new: false, facts: [], found_by: "page", official: false, maker: "joowhwang", risk: "通过你的浏览器登录来用，可能封号" });
var b = m.blocks[m.blocks.length - 1];
var view = C.blockCard(b);
var rows = view.rows.map(function (r) { return r.label + "：" + r.text; }).join("\n");
assert.ok(rows.indexOf("非官方") >= 0 && rows.indexOf("joowhwang") >= 0, rows);
var risk = view.rows.filter(function (r) { return r.label === "风险"; })[0];
assert.ok(risk && risk.danger && risk.text.indexOf("封号") >= 0, rows);
assert.ok(view.warn.indexOf("别人做的") >= 0, view.warn);

m.apply({ type: "proposal", id: "w2", kind: "web_install", state: "open", label: "从网上装 demo", tool: "demo",
  command: "npm install -g demo", source: "https://demo.com", domain: "demo.com", danger: false,
  new: false, facts: [], found_by: "page", official: true, maker: "Demo 公司", risk: "" });
var v2 = C.blockCard(m.blocks[m.blocks.length - 1]);
var r2 = v2.rows.map(function (r) { return r.label + "：" + r.text; }).join("\n");
assert.ok(r2.indexOf("官方") >= 0 && r2.indexOf("非官方") < 0, r2);
assert.ok(!v2.rows.some(function (r) { return r.label === "风险"; }), "官方且没写风险：不显示风险行");

// 4. 积木库里的提示语叫管家
assert.ok(C.blockCard({ proposalKind: "connect_search", info: { name: "联网搜索 · Tavily" } }).rows[0].text.indexOf("管家") >= 0);
assert.ok(C.jobView(null).text.indexOf("管家") >= 0);

console.log("ok");

// 5. Codex 复查补：没出成的卡不算“出了卡”；非官方的脚本卡不再叫你确认“官网”
var s2 = C.actsSummary([{ label: "没出卡", status: "done" }]);
assert.ok(s2.indexOf("出了") < 0 && s2.indexOf("没出成") >= 0, s2);
m.apply({ type: "proposal", id: "w3", kind: "web_install", state: "open", label: "从网上装 x", tool: "x",
  command: "curl -fsSL https://x.dev/i.sh | sh", source: "https://x.dev", domain: "x.dev", danger: true,
  new: false, facts: [], found_by: "page", official: false, maker: "某人", risk: "个人维护" });
var v3 = C.blockCard(m.blocks[m.blocks.length - 1]);
assert.strictEqual(v3.warnLevel, "danger");
assert.ok(v3.warn.indexOf("官网") < 0 && v3.warn.indexOf("别人做的") >= 0, v3.warn);
console.log("ok2");

// 6. 检测没通过的卡，标题不能写“已确认接入”（用户真实接腾讯云混元时看到的，2026-11-10）
var cb = { state: "confirmed", proposalKind: "connect_api", info: { name: "腾讯云混元" } };
assert.strictEqual(C.blockDoneTitle(cb, { state: "fail" }), "没接上：腾讯云混元");
assert.strictEqual(C.blockDoneTitle(cb, { state: "running" }), "正在检测：腾讯云混元");
assert.strictEqual(C.blockDoneTitle(cb, { state: "ok" }), "已接入：腾讯云混元");
assert.strictEqual(C.blockDoneTitle(cb, { state: "warn" }), "已接入：腾讯云混元");
assert.strictEqual(C.blockDoneTitle(cb), "已确认接入：腾讯云混元", "没有结果时照旧");
assert.strictEqual(C.blockDoneTitle(cb, { state: "ok", canSave: true }), "检测通过，还没放进基础层：腾讯云混元");
var ib = { state: "confirmed", proposalKind: "web_install", info: { label: "从网上装 x" } };
assert.strictEqual(C.blockDoneTitle(ib, { state: "fail" }), "没装成：从网上装 x");
assert.strictEqual(C.blockDoneTitle(ib, { state: "running" }), "正在安装：从网上装 x");
assert.strictEqual(C.blockDoneTitle(ib, { state: "ok" }), "已装好：从网上装 x");
assert.ok(C.actsSummary([{ label: "查模型", status: "done" }]).indexOf("查了模型名单") >= 0);
console.log("ok3");

// 7. 复查补：结果没留下来、额度不足时的标题
assert.strictEqual(C.blockDoneTitle(cb, { state: "lost" }), "已点确认，结果没留下来：腾讯云混元");
assert.strictEqual(C.blockDoneTitle(cb, { state: "warn", canSave: true }), "能连上但额度不足，还没放进基础层：腾讯云混元");
console.log("ok4");
