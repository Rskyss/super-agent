/* token 用量 A 卡验收（右栏显示的规则层，Claude 写；施工方不许改）。由 test_token_usage_acceptance.py 调用。 */
"use strict";
var assert = require("assert");
var path = require("path");
var P = require(path.join(__dirname, "..", "desktop", "static", "panel.js"));

// 数字格式：不到 1 万写原数（带千分位），1 万到 1 亿写“N.N 万”，1 亿以上写“N.N 亿”
// （11-17 第一批调整：第三行改由 metaText 拼，去掉“已用”二字，见 flowfix_b_panel_check.js）
function T(t) { return P.metaText({ state: "done", tokens: t }); }
assert.strictEqual(T({ ok: true, total: 2207607 }), "220.8 万 token");
assert.strictEqual(T({ ok: true, total: 8512 }), "8,512 token");
assert.strictEqual(T({ ok: true, total: 123456789 }), "1.2 亿 token");
assert.strictEqual(T({ ok: true, total: 10000 }), "1.0 万 token");

// 读不到：写“—”和原因，不写 0
assert.strictEqual(T({ ok: false, reason: "找不到会话记录" }), "用量 —（找不到会话记录）");
// 这一步不支持（agy、Cursor）或还没开始：什么都不写
assert.strictEqual(T(null), "");
assert.strictEqual(T(undefined), "");
// 有几轮没读到：照实写
assert.strictEqual(T({ ok: true, total: 3000, missing: 2 }), "3,000 token（有 2 轮没读到）");

// 鼠标提示：分项 + 新消耗（不含缓存读取）
assert.strictEqual(
  P.tokenTitle({ ok: true, total: 2207607, input: 2178002, cached: 2082816, output: 29605 }),
  "输入 217.8 万（其中缓存读取 208.3 万）· 输出 3.0 万 · 新消耗 12.5 万（不含缓存读取）");
assert.strictEqual(P.tokenTitle({ ok: false, reason: "x" }), "");
assert.strictEqual(P.tokenTitle(null), "");

// 本卡合计
// （11-17 第一批调整：细项挪进鼠标提示 totalTitle）
assert.strictEqual(P.totalText({ total: 5400000, other: 20428 }), "本卡合计 540.0 万 token");
assert.strictEqual(P.totalTitle({ total: 5400000, other: 20428 }), "其中交接资料等其他 2.0 万 token");
assert.strictEqual(P.totalText({ total: 5400000, other: 0 }), "本卡合计 540.0 万 token");
assert.strictEqual(P.totalText(null), "");
assert.strictEqual(P.totalText({ total: 0, other: 0 }), "");

// 不出现钱
[T({ ok: true, total: 2207607 }), P.tokenTitle({ ok: true, total: 1, input: 1, cached: 0, output: 0 }),
 P.totalText({ total: 1, other: 0 })].forEach(function (s) {
  assert.ok(!/[元¥$]|价格|费用/.test(s), s);
});
console.log("OK");
