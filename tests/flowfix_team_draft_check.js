/* 流程修理复查第 1 轮（团队配置）：恢复没保存的改动时只恢复用户真改过、之后也没人动过的那几行。 */
"use strict";
var assert = require("assert");
var path = require("path");
var T = require(path.join(__dirname, "..", "desktop", "static", "team.js"));

var base = { team: { dispatcher: "codex", tester: "codex", developer: "cursor", reviewer: "codex" },
             picks: { dispatcher: { tool: "codex", model: "m", effort: "xhigh" }, developer: { tool: "cursor", model: "auto" } } };
var mine = JSON.parse(JSON.stringify(base));
mine.picks.dispatcher.effort = "medium";            // 用户只改了调度员强度，没保存
var savedLater = JSON.parse(JSON.stringify(base));
savedLater.team.developer = "agy";                   // 之后行政帮忙把开发员换成 agy 并保存
savedLater.picks.developer = { tool: "agy", model: "flash" };

var got = T.draftRows(savedLater, { base: base, now: mine });
assert.deepStrictEqual(Object.keys(got), ["dispatcher"], "只恢复调度员这一行");
assert.strictEqual(got.dispatcher.pick.effort, "medium");

// 同一行之后被别人改过：不恢复，免得盖掉
var savedOther = JSON.parse(JSON.stringify(base));
savedOther.picks.dispatcher.effort = "high";
assert.deepStrictEqual(Object.keys(T.draftRows(savedOther, { base: base, now: mine })), [], "那一行别人改过就不恢复");

// 什么都没改：不恢复
assert.deepStrictEqual(Object.keys(T.draftRows(base, { base: base, now: base })), []);
console.log("OK");
