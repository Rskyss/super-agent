/* 68 号单：能力层页面的“规则层”检查（AbilitiesCore）。用 node 跑，失败就报错退出。
   由 test_acceptance_68_abilities_page.py 调用。三张预置卡片从 collab/abilities.py 现取，不在这里抄一份。 */
"use strict";
var assert = require("assert");
var path = require("path");
var execFileSync = require("child_process").execFileSync;
var ROOT = path.join(__dirname, "..");
var A = require(path.join(ROOT, "desktop", "static", "abilities.js"));

["esc", "statusView", "subText", "outputText", "checkText", "formFields", "collectInputs", "resultView",
 "runsView", "promptText", "removeConfirm", "canChangeModel"].forEach(function (name) {
  assert.strictEqual(typeof A[name], "function", "AbilitiesCore 要有 " + name);
});

var BUILTIN = JSON.parse(execFileSync("python3", ["-c",
  "import json; from collab import abilities; print(json.dumps(abilities.BUILTIN_ABILITIES, ensure_ascii=False))"],
  { cwd: ROOT, encoding: "utf-8" }));
var search = BUILTIN["search-web"], article = BUILTIN["write-article"], draw = BUILTIN["draw-image"];

// esc
assert.strictEqual(A.esc('<b a="1">&\''), "&lt;b a=&quot;1&quot;&gt;&amp;&#39;");

// 状态
assert.deepStrictEqual(A.statusView({ status: "ok" }), { cls: "ok", text: "能用" });
assert.deepStrictEqual(A.statusView({ status: "failed" }), { cls: "warn", text: "上次没成" });
assert.deepStrictEqual(A.statusView({ status: "unusable" }), { cls: "idle", text: "不能用" });

// 左栏小字
assert.strictEqual(A.subText({ block_name: "阿里百炼", model: "qwen3.7-plus" }), "阿里百炼 · qwen3.7-plus");
assert.strictEqual(A.subText({ block_name: "联网搜索 · Tavily", model: null }), "联网搜索 · Tavily");

// 交回什么
assert.deepStrictEqual(["text", "list", "image", "file"].map(A.outputText), ["文字", "列表", "图片", "文件"]);

// 怎么算干成了
assert.strictEqual(A.checkText(article.output, article.inputSchema, "llm_api"), "有内容，字数在你填的「大概字数」的一半到两倍之间");
assert.strictEqual(A.checkText(search.output, search.inputSchema, "search"), "至少 1 条，每条都有标题和链接");
assert.strictEqual(A.checkText(draw.output, draw.inputSchema, "cli"), "存出了图片，是 PNG 或 JPG，宽高都不小于 256");
assert.strictEqual(A.checkText({ type: "text", check: { min_chars: 300, max_chars: 3000 } }, {}, "llm_api"), "有内容，字数在 300 到 3000 之间");
assert.strictEqual(A.checkText({ type: "text", check: { min_chars: 300 } }, {}, "llm_api"), "有内容，不少于 300 字");
assert.strictEqual(A.checkText({ type: "text", check: { max_chars: 50 } }, {}, "cli"), "有内容，不超过 50 字");
assert.strictEqual(A.checkText({ type: "text", check: {} }, {}, "cli"), "有内容");
assert.strictEqual(A.checkText({ type: "text", check: { min_ratio: 1, max_ratio: 3, of: "words" } }, article.inputSchema, "llm_api"),
                   "有内容，字数在你填的「大概字数」的一样多到三倍之间");
assert.strictEqual(A.checkText({ type: "text", check: { min_ratio: 0.8, max_ratio: 1.5, of: "words" } }, article.inputSchema, "llm_api"),
                   "有内容，字数在你填的「大概字数」的0.8 倍到1.5 倍之间");
assert.strictEqual(A.checkText({ type: "list", check: { min_items: 3 } }, {}, "cli"), "至少 3 条，每条都有标题");
assert.strictEqual(A.checkText({ type: "list", check: {} }, {}, "cli"), "至少 1 条，每条都有标题");
assert.strictEqual(A.checkText({ type: "image", check: {} }, {}, "cli"), "存出了图片，是 PNG 或 JPG，宽高都不小于 256");
assert.strictEqual(A.checkText({ type: "file", check: { extensions: ["md", "txt"] } }, {}, "cli"), "存出了文件，不是空的，扩展名是 md、txt 之一");

// 你要填什么
var f = A.formFields(article.inputSchema);
assert.deepStrictEqual(f.map(function (x) { return x.name; }), ["topic", "words", "notes"]);
assert.deepStrictEqual([f[0].kind, f[1].kind, f[2].kind], ["text", "number", "textarea"]);
assert.deepStrictEqual([f[0].required, f[1].required, f[2].required], [true, false, false]);
assert.strictEqual(f[0].title, "主题");
assert.strictEqual(f[0].placeholder, "例如：周末带孩子去哪玩");
assert.strictEqual(f[0].value, "");
assert.strictEqual(f[0].maxLength, 200);
assert.strictEqual(f[1].value, 800);
assert.deepStrictEqual([f[1].min, f[1].max], [100, 20000]);
var sf = A.formFields(search.inputSchema);
assert.strictEqual(sf[2].kind, "select");
assert.deepStrictEqual(sf[2].options, ["不限", "最近一天", "最近一周", "最近一个月"]);
assert.strictEqual(sf[2].value, "不限");
assert.strictEqual(A.formFields(draw.inputSchema)[0].kind, "textarea", "maxLength 1000 用多行框");

// 收集输入
assert.deepStrictEqual(A.collectInputs(f, { topic: "  春天 ", words: "800", notes: "" }),
                       { ok: true, inputs: { topic: "春天", words: 800 } });
assert.deepStrictEqual(A.collectInputs(f, { topic: "春天", words: "", notes: "资料" }),
                       { ok: true, inputs: { topic: "春天", notes: "资料" } });
assert.deepStrictEqual(A.collectInputs(f, { topic: "  ", words: "800" }), { ok: false, error: "请填写「主题」" });
["abc", "50", "30000", "8.5"].forEach(function (bad) {
  assert.deepStrictEqual(A.collectInputs(f, { topic: "春天", words: bad }),
                         { ok: false, error: "「大概字数」要填 100 到 20000 的整数" }, bad);
});
assert.deepStrictEqual(A.collectInputs(f, { topic: "字".repeat(201) }), { ok: false, error: "「主题」最多 200 个字" });

// 结果怎么显示
var okText = A.resultView({ ok: true, status: "ok", model: "qwen3.7-plus", seconds: 12.4, output: "正文", files: ["结果.txt"],
                            reason: "成功完成", how: "", platform_said: "", next_model: null, run_id: "20261111-120000-abcd" }, article);
assert.strictEqual(okText.tone, "ok");
assert.strictEqual(okText.head, "用了 qwen3.7-plus · 12 秒");
assert.deepStrictEqual(okText.body, { type: "text", text: "正文" });
assert.strictEqual(okText.ask, null);

var okList = A.resultView({ ok: true, status: "ok", model: null, seconds: 0.4, files: ["结果.json"], reason: "", how: "",
                            platform_said: "", next_model: null, output: [{ title: "t", url: "https://t.cn", summary: "s" }] }, search);
assert.strictEqual(okList.head, "用时不到 1 秒");
assert.deepStrictEqual(okList.body, { type: "list", items: [{ title: "t", url: "https://t.cn", summary: "s" }] });

var okImage = A.resultView({ ok: true, status: "ok", model: "gpt-6.1-sol", seconds: 60, output: "输出.png", files: ["输出.png"],
                             reason: "", how: "", platform_said: "", next_model: null }, draw);
assert.deepStrictEqual(okImage.body, { type: "image", name: "输出.png" });

var fmt = A.resultView({ ok: false, status: "format", model: "qwen3.7-plus", seconds: 3, output: "太短了", files: ["结果.txt"],
                         reason: "字数不足：实际 3 字", how: "调整要求", platform_said: "", next_model: null }, article);
assert.strictEqual(fmt.tone, "bad");
assert.strictEqual(fmt.head, "没成：字数不足：实际 3 字");
assert.deepStrictEqual(fmt.body, { type: "text", text: "太短了" }, "拿到了照样给用户看");

var none = A.resultView({ ok: false, status: "format", model: null, seconds: 1, output: null, files: [],
                          reason: "一条都没有", how: "", platform_said: "", next_model: null }, search);
assert.strictEqual(none.body, null);

var model1 = A.resultView({ ok: false, status: "model", model: "qwen3.7-plus", seconds: 2, output: null, files: [],
                            reason: "模型未开通或无权访问", how: "", platform_said: "model not enabled", next_model: "qwen3.6-plus" }, article);
assert.strictEqual(model1.tone, "warn");
assert.strictEqual(model1.head, "模型不通：模型未开通或无权访问");
assert.strictEqual(model1.said, "model not enabled");
assert.deepStrictEqual(model1.ask, { model: "qwen3.6-plus", text: "要换成 qwen3.6-plus 再试一次吗？" });

var model2 = A.resultView({ ok: false, status: "model", model: "qwen3.6-plus", seconds: 2, output: null, files: [],
                            reason: "额度用完或欠费", how: "", platform_said: "", next_model: null }, article);
assert.strictEqual(model2.ask, null);
assert.strictEqual(model2.tip, "可以在上面的模型下拉框换一个再试。");
var model3 = A.resultView({ ok: false, status: "model", model: "gpt-6.1-sol", seconds: 2, output: null, files: [],
                            reason: "额度用完", how: "", platform_said: "", next_model: null }, draw);
assert.strictEqual(model3.tip, "这张卡没有备用模型。");

var blk = A.resultView({ ok: false, status: "block", model: "qwen3.7-plus", seconds: 0, output: null, files: [],
                         reason: "大模型 API 密钥不对或已失效", how: "去基础层检测积木块「阿里百炼」或换密钥",
                         platform_said: "", next_model: null }, article);
assert.strictEqual(blk.head, "积木块的问题：大模型 API 密钥不对或已失效");
assert.strictEqual(blk.tip, "去基础层检测积木块「阿里百炼」或换密钥");
assert.strictEqual(blk.ask, null);

var err = A.resultView({ ok: false, status: "error", model: null, seconds: 30, output: null, files: [],
                         reason: "超时", how: "稍后再试", platform_said: "", next_model: null }, search);
assert.deepStrictEqual([err.tone, err.head, err.tip], ["bad", "没成：超时", "稍后再试"]);
[okText, okList, okImage, fmt, none, model1, model2, model3, blk, err].forEach(function (v) {
  ["tone", "head", "said", "tip", "ask", "body"].forEach(function (k) {
    assert.ok(k in v && v[k] !== undefined, "resultView 的 " + k + " 不能是 undefined");
  });
});

// 最近几次
assert.deepStrictEqual(A.runsView([
  { time: "2026-11-11T17:40:05", ok: true, status: "ok", model: "qwen3.7-plus", reason: "成功完成" },
  { time: "2026-11-10T09:03:00", ok: false, status: "model", model: "qwen3.7-plus", reason: "额度用完" },
  { time: "2026-11-10T09:00:00", ok: false, status: "format", reason: "一条都没有" },
]), [
  { time: "11-11 17:40", ok: true, text: "成了", model: "qwen3.7-plus", reason: "" },
  { time: "11-10 09:03", ok: false, text: "没成", model: "qwen3.7-plus", reason: "额度用完" },
  { time: "11-10 09:00", ok: false, text: "没成", model: "", reason: "一条都没有" },
]);
assert.deepStrictEqual(A.runsView([]), []);

// 交代原文
assert.strictEqual(A.promptText(article), "系统：" + article.how.system + "\n\n要求：" + article.how.prompt);
assert.strictEqual(A.promptText(draw), draw.how.prompt);
assert.strictEqual(A.promptText(search), "");

// 移出确认、换模型
assert.strictEqual(A.removeConfirm(article), "把「写文章」移出能力层？记录和成果都留着。");
assert.deepStrictEqual([A.canChangeModel(article), A.canChangeModel(draw), A.canChangeModel(search)], [true, false, false]);

// 第 2 轮：试做示例显示那次填的内容（按“你要填什么”的顺序，只列填了的项）
assert.strictEqual(typeof A.inputsView, "function", "AbilitiesCore 要有 inputsView");
assert.deepStrictEqual(A.inputsView(article.inputSchema, { words: 800, topic: "春天" }),
  [{ title: "主题", value: "春天" }, { title: "大概字数", value: "800" }]);
assert.deepStrictEqual(A.inputsView(search.inputSchema, { keywords: "<b>猫</b>" }),
  [{ title: "关键词", value: "<b>猫</b>" }], "只给值，转义交给页面用 textContent");
assert.deepStrictEqual(A.inputsView(article.inputSchema, { topic: "春天", notes: "  " }), [{ title: "主题", value: "春天" }]);
assert.deepStrictEqual(A.inputsView(article.inputSchema, null), []);
assert.deepStrictEqual(A.inputsView({ properties: { x: {} } }, { x: 1, y: 2 }), [{ title: "x", value: "1" }],
  "没有 title 用项名；卡上没写的项不列");

console.log("OK");
