/* 68b 号单：动作摘要改叫“出提议”，以前存下的“出卡”“没出卡”也要认、合并计数。由 test_acceptance_68b_naming.py 调用。 */
"use strict";
var assert = require("assert");
var path = require("path");
var C = require(path.join(__dirname, "..", "desktop", "static", "chat.js"));

function done(label) { return { label: label, status: "done" }; }

assert.strictEqual(C.actsSummary([done("出提议")]), "出了 1 个提议");
assert.strictEqual(C.actsSummary([done("出卡")]), "出了 1 个提议", "旧记录里的“出卡”");
assert.strictEqual(C.actsSummary([done("出卡"), done("出提议")]), "出了 2 个提议", "新旧合并计数");
assert.strictEqual(C.actsSummary([done("没出提议")]), "1 次没出成提议");
assert.strictEqual(C.actsSummary([done("没出卡"), done("没出提议")]), "2 次没出成提议", "新旧合并计数");
assert.strictEqual(C.actsSummary([done("出提议：已向用户出示确认框")]), "出了 1 个提议");
assert.strictEqual(C.actsSummary([done("没出提议：缺字段")]), "1 次没出成提议");
var s = C.actsSummary([done("查电脑"), done("出提议")]);
assert.ok(s.indexOf("张卡") < 0 && s.indexOf("出了 1 个提议") >= 0, s);

console.log("ok");
