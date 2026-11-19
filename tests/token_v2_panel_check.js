/* token 用量第二版 A2 卡验收（右栏显示的规则层，Claude 写；施工方不许改）。由 test_token_v2_acceptance.py 调用。 */
"use strict";
var assert = require("assert");
var path = require("path");
var P = require(path.join(__dirname, "..", "desktop", "static", "panel.js"));

// Cursor 在跑：写“这一轮结束后出数”，不写“用量 —”、不写 0
assert.strictEqual(P.metaText({ state: "done", tokens: { ok: false, pending: true, reason: "这一轮结束后出数" } }),
  "这一轮结束后出数");

// 鼠标提示：多轮时每轮写清是谁、用时、用量；没读到的轮写原因
var title = P.stepTitle({
  rounds: 3,
  history: [
    { seconds: 10, tokens: 2000, who: "千问 · DeepSeek V4.1 Flash" },
    { seconds: 70, tokens: 85500, who: "agy · Gemini 4 Hydrogen（高）" },
    { seconds: 5, tokens: null, who: "agy · Gemini 4 Hydrogen（高）", missing: true, reason: "实时输出已不在" }
  ],
  tokens: { ok: true, total: 87500, missing: 1 }
});
assert.ok(title.indexOf("第 1 轮 千问 · DeepSeek V4.1 Flash · 0:10 · 2,000 token") >= 0, title);
assert.ok(title.indexOf("第 2 轮 agy · Gemini 4 Hydrogen（高）· 1:10 · 8.6 万 token") >= 0
  || title.indexOf("第 2 轮 agy · Gemini 4 Hydrogen（高） · 1:10 · 8.6 万 token") >= 0, title);
assert.ok(/第 3 轮 agy · Gemini 4 Hydrogen（高）\s?· 0:05 · 用量 —（实时输出已不在）/.test(title), title);

// 没有 who 的老记录照旧（第一版格式不变）
var old = P.stepTitle({ rounds: 2, history: [{ seconds: 10, tokens: 2000 }, { seconds: 20, tokens: 3000 }] });
assert.ok(old.indexOf("第 1 轮 0:10 · 2,000 token") >= 0, old);

// 本卡合计：有没读到的轮，照实注明
assert.strictEqual(P.totalText({ total: 5400000, other: 0, missing: 2 }), "本卡合计 540.0 万 token（不含没读到的 2 轮）");
assert.strictEqual(P.totalText({ total: 5400000, other: 0, missing: 0 }), "本卡合计 540.0 万 token");
console.log("OK");
