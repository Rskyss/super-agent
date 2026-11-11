/* 58 号单：积木库体检卡、基础层详情的“规则层”检查。用 node 跑，失败就报错退出。由 test_acceptance_58_blocks_detail.py 调用。 */
"use strict";
var assert = require("assert");
var path = require("path");
var B = require(path.join(__dirname, "..", "desktop", "static", "blocks.js"));

["healthView", "detailView", "checkView", "removeConfirm"].forEach(function (name) {
  assert.strictEqual(typeof B[name], "function", "BlocksCore 要有 " + name);
});
// 56 号单的函数一个都不能少
["startView", "bindRequest", "modelChoices", "bindResultText", "statusText", "statusClass", "kindText", "chatUrl",
 "esc", "threadTitle", "deleteConfirm"].forEach(function (name) {
  assert.strictEqual(typeof B[name], "function", "56 号单的 " + name + " 不能丢");
});

function item(name, label, kind, status, extra) {
  return Object.assign({ name: name, label: label, kind: kind, status: status, version: "", login: null,
                         why: kind === "prereq" ? "用 npm 装 Codex 要用" : "接进基础层的工具", note: "", fix: "", path: "" }, extra || {});
}
function scan(items) { return { time: "2026-11-09T10:00:00", items: items }; }

var node = item("node", "Node", "prereq", "有", { version: "v22.1.0" });
var brewMissing = item("brew", "Homebrew", "prereq", "没有", { fix: "管家可以带你装 Homebrew" });
var codexOk = item("codex", "Codex", "tool", "有", { version: "codex-cli 0.167.0", login: "已登录" });
var codexOut = item("codex", "Codex", "tool", "有", { version: "codex-cli 0.167.0", login: "没登录",
                                                        fix: "Codex 还没登录：在终端里登录一次，管家会告诉你命令" });
var cursorMissing = item("cursor", "Cursor", "tool", "没有", { fix: "跟管家说“帮我装 Cursor”" });
var agyMissing = item("agy", "agy", "tool", "没有", { fix: "跟管家说“帮我装 agy”" });
var codexBlock = { id: "codex", name: "Codex", kind: "cli", status: "ok" };
var apiBlock = { id: "bailian-qwen-plus", name: "通义", kind: "llm_api", status: "ok" };

// 1. 还没有体检结果
var loading = B.healthView(null, [codexBlock]);
assert.strictEqual(loading.state, "loading");
assert.strictEqual(loading.expand, false);
assert.strictEqual(loading.line, "环境体检：正在检查…");
assert.deepStrictEqual(loading.rows, []);

// 2. 基础层里的都正常：收成一行，没接的工具和前提照样列出、打叉，但不逼着展开
var fine = B.healthView(scan([node, brewMissing, codexOk, cursorMissing, agyMissing]), [codexBlock, apiBlock]);
assert.strictEqual(fine.state, "ok");
assert.strictEqual(fine.expand, false, "只看基础层里的：没装 Cursor、Homebrew 不展开（用户 2026-11-09 定）");
assert.strictEqual(fine.line, "环境体检：基础层里的 1 个工具都正常 · 没装：Homebrew、Cursor、agy");
assert.strictEqual(fine.rows.length, 5, "每一项一行，按原顺序");
assert.deepStrictEqual(fine.rows.map(function (r) { return r.name; }), ["node", "brew", "codex", "cursor", "agy"]);
assert.strictEqual(fine.rows[0].ok, true);
assert.strictEqual(fine.rows[0].text, "v22.1.0");
assert.strictEqual(fine.rows[1].ok, false);
assert.strictEqual(fine.rows[1].text, "没有");
assert.strictEqual(fine.rows[1].fix, "管家可以带你装 Homebrew");
assert.strictEqual(fine.rows[1].why, "用 npm 装 Codex 要用");
assert.strictEqual(fine.rows[2].text, "codex-cli 0.167.0 · 已登录");
assert.strictEqual(fine.rows[2].label, "Codex");

// 3. 基础层里的工具没登录：展开，说清哪个要处理
var bad = B.healthView(scan([node, brewMissing, codexOut, cursorMissing]), [codexBlock]);
assert.strictEqual(bad.state, "problem");
assert.strictEqual(bad.expand, true);
assert.strictEqual(bad.line, "环境体检：基础层里有 1 个工具要处理：Codex");
assert.strictEqual(bad.rows[2].ok, false);
assert.strictEqual(bad.rows[2].text, "codex-cli 0.167.0 · 没登录");
assert.ok(bad.rows[2].fix.indexOf("登录") >= 0);

// 4. 基础层里的工具没了、找不到、查版本出错，都算要处理；几个一起列
var mine = { id: "mytool", name: "我的工具", kind: "cli", status: "ok" };
var gone = item("codex", "Codex", "tool", "没有", { fix: "跟管家说“帮我装 Codex”" });
var lost = item("mytool", "我的工具", "tool", "找不到", { fix: "终端里已经装好，但超级助手还没认到：关掉超级助手再重新打开" });
var both = B.healthView(scan([gone, lost]), [codexBlock, mine]);
assert.strictEqual(both.line, "环境体检：基础层里有 2 个工具要处理：Codex、我的工具");
assert.strictEqual(both.rows[1].text, "装了，但超级助手找不到");
var slow = item("codex", "Codex", "tool", "有", { note: "查版本超时" });
var slowView = B.healthView(scan([slow]), [codexBlock]);
assert.strictEqual(slowView.state, "problem");
assert.strictEqual(slowView.rows[0].text, "有 · 查版本超时", "没有版本写“有”，有 note 加在后面");
assert.strictEqual(slowView.rows[0].fix, "跟管家说一声，它会帮你查", "fix 为空但有 note 时要说怎么办");

// 5. 基础层空着；同名的大模型 API 不算 CLI
var empty = B.healthView(scan([node, codexOk]), []);
assert.strictEqual(empty.state, "ok");
assert.strictEqual(empty.line, "环境体检：基础层里还没有工具");
var apiSameName = B.healthView(scan([gone]), [{ id: "codex", name: "叫 codex 的 API", kind: "llm_api" }]);
assert.strictEqual(apiSameName.state, "ok", "只有 CLI 才拿体检比");
assert.strictEqual(apiSameName.line, "环境体检：基础层里还没有工具 · 没装：Codex");

// 6. 详情：CLI，核实过
var cliDetail = {
  card: { id: "codex", kind: "cli", name: "Codex", command: "codex", run: ["exec", "{prompt}"], source: "builtin" },
  verified: true, preview: "/usr/local/bin/codex exec '...'", key_account: "",
  last_check: { type: "check", ok: true, reason: "通了 · 4.2 秒", how: "", seconds: 4.2, time: "2026-11-09T10:01:02" },
  last_install: null,
};
var cv = B.detailView(cliDetail, codexOk);
assert.strictEqual(cv.title, "Codex");
assert.deepStrictEqual(cv.rows.map(function (r) { return r.label; }), ["类型", "命令", "版本", "登录", "来源", "上次检测"]);
assert.strictEqual(cv.rows[0].text, "CLI");
assert.strictEqual(cv.rows[1].text, "codex");
assert.strictEqual(cv.rows[1].code, true);
assert.strictEqual(cv.rows[2].text, "codex-cli 0.167.0");
assert.strictEqual(cv.rows[3].text, "已登录");
assert.strictEqual(cv.rows[4].text, "预置接法，核实过");
assert.strictEqual(cv.rows[5].text, "2026-11-09 10:01:02 · 用了 4.2 秒");
assert.deepStrictEqual(cv.status, { cls: "ok", text: "能用" });
assert.strictEqual(cv.needsKey, false);
assert.strictEqual(cv.needsApprove, false);
assert.strictEqual(cv.preview, "", "核实过的不亮命令");
assert.strictEqual(cv.warn, "");
assert.strictEqual(cv.checkText, "检测");
assert.strictEqual(cv.cardText, JSON.stringify(cliDetail.card, null, 2));

// 7. 详情：没核实的 CLI，还没体检过、没检测过
var drafted = {
  card: { id: "mytool", kind: "cli", name: "我的工具<b>x</b>", command: "mytool", run: ["{prompt}"], source: "clerk" },
  verified: false, preview: "/opt/bin/mytool '新建 连通检测.txt'", key_account: "", last_check: null, last_install: null,
};
var dv = B.detailView(drafted, null);
assert.strictEqual(dv.title, "我的工具<b>x</b>", "名字原样，页面用 textContent 放");
assert.strictEqual(dv.rows[2].text, "还没查到");
assert.strictEqual(dv.rows[3].text, "还没查到");
assert.strictEqual(dv.rows[4].text, "管家起草，没核实");
assert.strictEqual(dv.rows[5].text, "没检测过");
assert.deepStrictEqual(dv.status, { cls: "idle", text: "没检测过" });
assert.strictEqual(dv.needsApprove, true);
assert.strictEqual(dv.preview, drafted.preview, "检测时带上的 approved 就是亮给用户看的这条");
assert.strictEqual(dv.warn, "未核实，有风险：检测前看清要运行的完整命令");
assert.strictEqual(dv.checkText, "看过了，确认检测");
var noLogin = B.detailView(drafted, item("mytool", "我的工具", "tool", "有", { version: "1.0", login: null }));
assert.strictEqual(noLogin.rows[3].text, "查不了", "体检查过但这块没有查登录的办法");

// 8. 详情：检测没通过
var failed = Object.assign({}, cliDetail, { last_check: { ok: false, reason: "没登录", how: "在终端里登录一次", seconds: 1, time: "2026-11-09T09:00:00" } });
assert.deepStrictEqual(B.detailView(failed, codexOut).status, { cls: "bad", text: "不能用：没登录。在终端里登录一次" });
var failedNoHow = Object.assign({}, cliDetail, { last_check: { ok: false, reason: "超时", how: "", seconds: 30, time: "2026-11-09T09:00:00" } });
assert.deepStrictEqual(B.detailView(failedNoHow, null).status, { cls: "bad", text: "不能用：超时" });

// 9. 详情：大模型 API
var apiDetail = {
  card: { id: "bailian-qwen-plus", kind: "llm_api", name: "通义", provider: "bailian", model: "qwen-plus",
          base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", key_account: "bailian", source: "clerk" },
  verified: false, preview: "", key_account: "bailian", last_check: null, last_install: null,
};
var av = B.detailView(apiDetail, null);
assert.deepStrictEqual(av.rows.map(function (r) { return r.label; }), ["类型", "服务商", "地址", "密钥账号", "这家的模型", "试通用的模型", "上次检测"]);
assert.strictEqual(av.rows[0].text, "大模型 API");
assert.strictEqual(av.rows[1].text, "阿里百炼");
assert.strictEqual(av.rows[2].text, "https://dashscope.aliyuncs.com/compatible-mode/v1");
assert.strictEqual(av.rows[3].text, "bailian");
assert.strictEqual(av.rows[5].text, "qwen-plus");  // 按整家接（2026-11-10）：卡上写的模型只是试通用的
assert.strictEqual(av.needsKey, true);
assert.strictEqual(av.needsApprove, false, "API 没有“核实”一说，不要求看命令");
assert.strictEqual(av.warn, "");
assert.strictEqual(av.checkText, "检测");
var names = { tencent: "腾讯云", other: "其他", someone: "someone" };
Object.keys(names).forEach(function (p) {
  var d = JSON.parse(JSON.stringify(apiDetail));
  d.card.provider = p;
  assert.strictEqual(B.detailView(d, null).rows[1].text, names[p]);
});

// 10. 检测结果
assert.deepStrictEqual(B.checkView(null), { state: "lost", text: "这次检测的结果没留下来（超级助手重开过），再点一次检测" });
assert.deepStrictEqual(B.checkView({ kind: "check", done: false, result: null, error: "" }), { state: "running", text: "正在检测…" });
assert.deepStrictEqual(B.checkView({ kind: "check", done: true, result: null, error: "后台任务出错了，请再试一次" }),
                       { state: "fail", text: "后台任务出错了，请再试一次" });
assert.deepStrictEqual(B.checkView({ kind: "check", done: true, error: "", result: { ok: true, reason: "通了", seconds: 4.2 } }),
                       { state: "ok", text: "检测通过 · 4.2 秒" });
assert.deepStrictEqual(B.checkView({ kind: "check", done: true, error: "", result: { ok: false, reason: "没登录", how: "在终端里登录一次" } }),
                       { state: "fail", text: "没登录。在终端里登录一次" });
assert.deepStrictEqual(B.checkView({ kind: "check", done: true, error: "", result: { ok: false, reason: "超时", how: "" } }),
                       { state: "fail", text: "超时" });
assert.deepStrictEqual(B.checkView({ kind: "check", done: true, error: "", result: { ok: false, reason: "", how: "检查密钥" } }),
                       { state: "fail", text: "检查密钥" });
assert.deepStrictEqual(B.checkView({ kind: "check", done: true, error: "", result: { ok: false } }),
                       { state: "fail", text: "没成功，原因没查出来，跟管家说一声" });

// 11. 移出前的确认
assert.strictEqual(B.removeConfirm("Codex"), "把「Codex」移出基础层？不会卸载工具，也不删密钥。要用时跟管家说一声就能再接回来。");

console.log("OK");
