// 65 号单：沙盒接入的页面规则层（chat.js 的 ChatCore、blocks.js 的 BlocksCore）
var assert = require("assert");
var C = require("../desktop/static/chat.js");
var B = require("../desktop/static/blocks.js");

// 1. 新的两种提议算积木库提议
["web_install", "connect_search"].forEach(function (k) { assert.ok(C.isBlockKind(k), k); });

// 2. 网上安装卡：黄色提醒、来源、网站加粗、原文核对、包底细、刚发布提醒、原样命令
var web = { proposalKind: "web_install", info: { label: "从网上装 demo-tool", source: "https://tool.example.com/docs",
  domain: "tool.example.com", command: "npm install -g @demo/tool", danger: false, new: true, found_by: "page",
  facts: ["npm 上第一次发布：2026-11-01", "发布者：demo-team"] } };
var wc = C.blockCard(web);
assert.strictEqual(wc.title, "从网上装 demo-tool");
assert.strictEqual(wc.confirmText, "确认安装");
assert.strictEqual(wc.warnLevel, "caution");
assert.ok(/不在核实名单里/.test(wc.warn));
var labels = wc.rows.map(function (r) { return r.label; });
assert.deepStrictEqual(labels, ["来源", "网站", "原文核对", "安装包底细", "安装包底细", "提醒", "会执行的命令"]);
assert.strictEqual(wc.rows[1].strong, true, "网站域名加粗");
assert.ok(/找到了/.test(wc.rows[2].text));
assert.strictEqual(wc.rows[6].code, true);
assert.strictEqual(wc.needsKey, false);

// 3. 脚本安装：红色提醒
var danger = C.blockCard({ proposalKind: "web_install", info: Object.assign({}, web.info, { danger: true, new: false }) });
assert.strictEqual(danger.warnLevel, "danger");
assert.ok(/脚本做什么程序管不住/.test(danger.warn));

// 4. 接联网搜索：一个密钥框，账号 tavily
var sc = C.blockCard({ proposalKind: "connect_search", info: { name: "联网搜索 · Tavily", key_account: "tavily" } });
assert.strictEqual(sc.needsKey, true);
assert.strictEqual(sc.keyAccount, "tavily");
assert.ok(/tvly-/.test(sc.keyHint));

// 5. 接普通工具：类别一行、多把密钥
var tc = C.blockCard({ proposalKind: "connect", info: { name: "Demo 工具", verified: false, preview: "/bin/demo-tool status",
  category: "普通工具", secrets: [{ account: "cli:demotool:DEMO_KEY", label: "Key" }] } });
assert.strictEqual(tc.rows[0].label, "类别");
assert.strictEqual(tc.rows[0].text, "普通工具");
assert.deepStrictEqual(tc.secrets, [{ account: "cli:demotool:DEMO_KEY", label: "Key" }]);
assert.strictEqual(tc.needsKey, false, "普通工具的密钥走多把密钥框，不走单个 API 密钥框");

// 6. 完成后的标题
assert.strictEqual(C.blockDoneTitle({ state: "confirmed", proposalKind: "web_install", info: web.info }), "已确认安装：从网上装 demo-tool");
assert.strictEqual(C.blockDoneTitle({ state: "confirmed", proposalKind: "connect_search", info: { name: "联网搜索 · Tavily" } }), "已确认接入：联网搜索 · Tavily");

// 7. 基础层类别
assert.strictEqual(B.categoryText("agent", "cli"), "AI 助手");
assert.strictEqual(B.categoryText("tool", "cli"), "普通工具");
assert.strictEqual(B.categoryText("llm_api", "llm_api"), "大模型 API");
assert.strictEqual(B.categoryText("search", "search"), "联网搜索");
assert.strictEqual(B.categoryText(undefined, "cli"), "CLI", "老数据没有类别时退回老说法");

// 8. 待接入
assert.deepStrictEqual(B.pendingView({ id: "codex" }, null), { cls: "idle", text: "待接入 · 点一下检测并接入", busy: false });
assert.strictEqual(B.pendingView({ id: "codex" }, { done: false }).busy, true);
assert.strictEqual(B.pendingView({ id: "codex" }, { done: true, saved: true, result: { ok: true } }).text, "已接入");
var bad = B.pendingView({ id: "codex" }, { done: true, saved: false, result: { ok: false, reason: "没登录", how: "在终端里登录一次" } });
assert.strictEqual(bad.cls, "bad");
assert.ok(/没登录/.test(bad.text));

// 9. 详情：联网搜索要密钥框；普通工具列出几把密钥的状态
var sd = B.detailView({ card: { id: "web-search", kind: "search", name: "联网搜索 · Tavily", provider: "tavily", key_account: "tavily" },
  category: "search", verified: false, preview: "", key_account: "tavily", last_check: null, secrets: [] }, null);
assert.strictEqual(sd.rows[0].text, "联网搜索");
assert.strictEqual(sd.needsKey, true);
assert.strictEqual(sd.needsApprove, false);
var td = B.detailView({ card: { id: "demotool", kind: "cli", role: "tool", name: "Demo", command: "demo-tool", probe: ["status"] },
  category: "tool", verified: false, preview: "/bin/demo-tool status", key_account: "", last_check: null,
  secrets: [{ account: "cli:demotool:DEMO_KEY", label: "Key", saved: true }] }, null);
assert.strictEqual(td.rows[0].text, "普通工具");
assert.ok(td.rows.some(function (r) { return r.label === "密钥" && /已保存/.test(r.text); }));
assert.strictEqual(td.needsApprove, true);
assert.strictEqual(td.secrets.length, 1);

console.log("OK");
