/* 跑卡流程调整 · 第一批：右栏文字规则（Claude 写；施工方不许改）。由 test_flowfix_b_display.py 调用。
   第三行 = metaText；鼠标提示 = stepTitle；合计 = totalText + totalTitle。 */
"use strict";
var assert = require("assert");
var path = require("path");
var P = require(path.join(__dirname, "..", "desktop", "static", "panel.js"));

var dev = { ok: true, total: 3290955, input: 3245000, cached: 3083000, output: 45955 };

// ---- 第三行：交了活的环节，写累计 ----
assert.strictEqual(P.metaText({ state: "done", rounds: 4, spent: 646, tokens: dev }), "共 4 轮 · 10:46 · 329.1 万 token");
assert.strictEqual(P.metaText({ state: "done", rounds: 1, spent: 419, tokens: { ok: true, total: 1131271 } }),
  "6:59 · 113.1 万 token", "只一轮不写轮数");
assert.strictEqual(P.metaText({ state: "fail", rounds: 4, spent: 635, tokens: { ok: true, total: 2863616 } }),
  "共 4 轮 · 10:35 · 286.4 万 token", "没通过的环节也一样写");
assert.strictEqual(P.metaText({ state: "done", rounds: 1, spent: 419 }), "6:59", "没有用量就只写用时");
assert.strictEqual(P.metaText({ state: "done", rounds: 2, tokens: { ok: true, total: 8512 } }), "共 2 轮 · 8,512 token",
  "没有用时记录就不写用时");
assert.strictEqual(P.metaText({ state: "done", rounds: 1, spent: 419, tokens: { ok: false, reason: "这一步没有用量记录" } }),
  "6:59 · 用量 —（这一步没有用量记录）");
assert.strictEqual(P.metaText({ state: "done", rounds: 3, spent: 60, tokens: { ok: true, total: 3000, missing: 2 } }),
  "共 3 轮 · 1:00 · 3,000 token（有 2 轮没读到）");
assert.strictEqual(P.metaText({ state: "done", tokens: { ok: true, total: 100000000 } }), "1.0 亿 token");

// ---- 第三行：正在跑，写这一轮的计时和动静，用量是这一步累计 ----
assert.strictEqual(P.metaText({ state: "active", rounds: 2, elapsed: 83, quiet: 5, tokens: { ok: true, total: 450000 } }, 0),
  "第 2 轮 · 已干 1:23 · 5 秒前有动静 · 45.0 万 token");
assert.strictEqual(P.metaText({ state: "active", rounds: 1, elapsed: 83, quiet: 5, tokens: { ok: true, total: 450000 } }, 0),
  "已干 1:23 · 5 秒前有动静 · 45.0 万 token", "第 1 轮不写轮数");
assert.strictEqual(P.metaText({ state: "active", rounds: 1, elapsed: 83, quiet: 5 }, 10),
  "已干 1:33 · 15 秒前有动静", "两次取数之间计时照走");
assert.strictEqual(P.metaText({ state: "active", rounds: 1, elapsed: 30, quiet: 200 }, 0),
  "已干 0:30 · 3 分钟前有动静");
assert.strictEqual(P.metaText({ state: "active", elapsed: null, quiet: null, tokens: { ok: true, total: 8512 } }, 0),
  "8,512 token", "没有计时：前面不能多出“·”");

// ---- 第三行：没开始 / 等着 ----
assert.strictEqual(P.metaText({ state: "idle", tokens: { ok: true, total: 1 } }), "", "没开始什么都不写");
assert.strictEqual(P.metaText({ state: "wait", tokens: { ok: true, total: 1000 } }), "1,000 token");
assert.strictEqual(P.metaText({ state: "wait" }), "");
assert.strictEqual(P.metaText(null), "");

// ---- 鼠标提示：说明全文 + 每轮明细（两轮以上才列）+ 用量明细 ----
assert.strictEqual(P.stepTitle({
  detail: "汇报：改好了", rounds: 2,
  history: [{ seconds: 326, tokens: 2063310 }, { seconds: 57, tokens: null }],
  tokens: { ok: true, total: 2063310, input: 2034574, cached: 1939456, output: 28736 },
}), "改好了\n第 1 轮 5:26 · 206.3 万 token\n第 2 轮 0:57\n" +
  "输入 203.5 万（其中缓存读取 193.9 万）· 输出 2.9 万 · 新消耗 12.4 万（不含缓存读取）");
assert.strictEqual(P.stepTitle({ detail: "补了测试", rounds: 1, history: [{ seconds: 419, tokens: 1131271 }] }),
  "补了测试", "只一轮不列明细");
assert.strictEqual(P.stepTitle({ detail: "", rounds: 0 }), "");
assert.strictEqual(P.stepTitle(null), "");

// ---- 合计：一行，细项进提示 ----
assert.strictEqual(P.totalText({ total: 7364538, other: 78696 }), "本卡合计 736.5 万 token");
assert.strictEqual(P.totalText({ total: 5400000, other: 0 }), "本卡合计 540.0 万 token");
assert.strictEqual(P.totalText({ total: 0, other: 0 }), "");
assert.strictEqual(P.totalText(null), "");
assert.strictEqual(P.totalTitle({ total: 7364538, other: 78696 }), "其中交接资料等其他 7.9 万 token");
assert.strictEqual(P.totalTitle({ total: 5400000, other: 0 }), "");
assert.strictEqual(P.totalTitle(null), "");

// ---- 不出现钱 ----
[P.metaText({ state: "done", rounds: 4, spent: 646, tokens: dev }), P.totalText({ total: 1, other: 1 }),
 P.totalTitle({ total: 2, other: 1 })].forEach(function (s) {
  assert.ok(!/[元¥$]|价格|费用/.test(s), s);
});
console.log("OK");
