/* token 用量第二版 B 卡验收（行政对话顶部的显示规则，Claude 写；施工方不许改）。由 test_token_b_acceptance.py 调用。 */
"use strict";
var assert = require("assert");
var path = require("path");
var C = require(path.join(__dirname, "..", "desktop", "static", "chat.js"));

// 对话顶部：写清是 token，数字格式和右栏一致（不到 1 万写原数带千分位，1 万以上写“N.N 万”）
assert.strictEqual(C.headerUsage(12345), "本段对话 1.2 万 token");
assert.strictEqual(C.headerUsage(8512), "本段对话 8,512 token");
assert.strictEqual(C.headerUsage(null), "");
assert.strictEqual(C.headerUsage(undefined), "");
assert.strictEqual(C.headerUsage(0), "");
assert.ok(!/[元¥$]|价格|费用/.test(C.headerUsage(12345)));
console.log("OK");
