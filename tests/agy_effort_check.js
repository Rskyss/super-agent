/* 团队页 agy 拆出“思考强度”框的规则检查（11-14 用户要求）：用 node 跑，失败就报错退出。由 test_agy_effort_box.py 调用。
   第一个参数是内置 agy 名单的 JSON（从 collab/config.py 取），保证页面拆的就是真名单。 */
"use strict";
var assert = require("assert");
var path = require("path");
var T = require(path.join(__dirname, "..", "desktop", "static", "team.js"));

var agy = JSON.parse(process.argv[2]);

// 1. 拆开：同一模型的几档并成一项，编号去掉结尾强度，名字去掉括号
var groups = T.splitEfforts(agy);
var hydrogen = groups.find(function (g) { return g.base === "gemini-4-hydrogen"; });
assert.ok(hydrogen, "名单里要有 Gemini 4 Hydrogen");
assert.strictEqual(hydrogen.label, "Gemini 4 Hydrogen");
assert.deepStrictEqual(hydrogen.efforts.map(function (e) { return e.effort; }), ["low", "medium", "high", "xhigh"], "四档，从低到高");
assert.deepStrictEqual(hydrogen.efforts.map(function (e) { return e.value; }),
  ["gemini-4-hydrogen-low", "gemini-4-hydrogen-medium", "gemini-4-hydrogen-high", "gemini-4-hydrogen-xhigh"], "每档存回完整编号");
var argon = groups.find(function (g) { return g.base === "gemini-4-argon"; });
assert.strictEqual(argon.label, "Gemini 4 Argon", "“(High · 默认)”这类括号也去掉");
assert.strictEqual(groups.filter(function (g) { return g.base === "gemini-4-argon"; }).length, 1, "同一模型只出现一次");
// 11-20 Orchestrator 三个编号本来就不带强度，各占一项；其余模型仍至少一档
assert.ok(groups.every(function (g) { return g.efforts.length >= 1 || /^gemini-pro-orchestrator/.test(g.base); }), "agy 除 Orchestrator 外每个模型都至少一档");

// 2. 拆不开的编号单独一项，没有强度
var odd = T.splitEfforts([{ value: "my-model", label: "my-model（当前）" }, { value: "x-high", label: "X (High)" }]);
assert.strictEqual(odd[0].base, "my-model");
assert.deepStrictEqual(odd[0].efforts, [], "拆不开就不带强度");
assert.strictEqual(odd[0].label, "my-model（当前）", "拆不开时名字原样");
assert.strictEqual(odd[1].base, "x");

// 3. 认出当前选的是哪个模型哪一档
assert.deepStrictEqual(T.findEffort(groups, "gemini-4-hydrogen-xhigh"), { base: "gemini-4-hydrogen", effort: "xhigh" });
assert.strictEqual(T.findEffort(groups, "not-in-list"), null);

// 4. 换模型时尽量保持原档位；没有就选最接近的
assert.strictEqual(T.nearestEffort(hydrogen, "high").value, "gemini-4-hydrogen-high");
assert.strictEqual(T.nearestEffort(argon, "xhigh").value, "gemini-4-argon-high", "Argon 没有极高，选最接近的高");
var oss = groups.find(function (g) { return g.base === "gpt-oss-120b"; });
assert.strictEqual(T.nearestEffort(oss, "low").value, "gpt-oss-120b-medium", "只有一档就是那一档");
assert.strictEqual(T.nearestEffort(hydrogen, "").value, "gemini-4-hydrogen-high", "原来没档位时用高");

console.log("OK");
