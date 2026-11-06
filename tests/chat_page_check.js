/* 聊天页面“记账层”的检查：用 node 跑，失败就报错退出。由 test_desktop_chat_page.py 调用。 */
"use strict";
var assert = require("assert");
var path = require("path");
var C = require(path.join(__dirname, "..", "desktop", "static", "chat.js"));

// 1. 调度员回复里的网页代码只会当文字显示
var evil = C.md("<script>alert(1)</script> **粗** `代码` <img src=x onerror=alert(1)>");
assert.ok(evil.indexOf("<script>") === -1 && evil.indexOf("<img") === -1, "网页代码被执行了：" + evil);
assert.ok(evil.indexOf("&lt;script&gt;") >= 0);
assert.ok(evil.indexOf("<strong>粗</strong>") >= 0 && evil.indexOf("<code>代码</code>") >= 0);

// 2. 排版：列表、代码块、标题、段落
var fmt = C.md("## 方案\n- 第一步\n- 第二步\n\n```\nnpm test\n<b>\n```\n结尾");
assert.ok(/<p class="h">方案<\/p>/.test(fmt));
assert.ok(fmt.indexOf("<ul><li>第一步</li><li>第二步</li></ul>") >= 0);
assert.ok(fmt.indexOf("<pre><code>npm test\n&lt;b&gt;</code></pre>") >= 0);
assert.ok(fmt.indexOf("<p>结尾</p>") >= 0);

// 3. 一段完整对话
var m = new C.Model();
var n = 0;
function ev(o) { n += 1; o.seq = n; m.apply(o); return o; }
ev({ type: "opening", text: "上次的交代", history: true });
ev({ type: "user", text: "你好", history: true });
ev({ type: "message", item: "h1", text: "你好呀", history: true });
ev({ type: "status", text: "已连上 Codex" });
ev({ type: "usage", total: 1200 });
assert.deepStrictEqual(m.blocks.map(function (b) { return b.kind; }), ["sys", "user", "ai"], "还没说新话时不画分割线");
ev({ type: "opening", text: "先读交接" });
assert.deepStrictEqual(m.blocks.map(function (b) { return b.kind; }), ["sys", "user", "ai", "divider", "sys"]);
assert.strictEqual(m.status, "已连上 Codex");

ev({ type: "turn_started" });
assert.strictEqual(m.busy, true);
ev({ type: "activity", item: "c1", label: "读 README.md", status: "started" });
assert.strictEqual(C.actsSummary(m.blocks[m.blocks.length - 1].parts[0].list), "读 README.md…");
ev({ type: "activity", item: "c1", label: "读 README.md", status: "done" });
ev({ type: "activity", item: "c2", label: "运行 npm test", status: "done" });
var ai = m.blocks[m.blocks.length - 1];
assert.strictEqual(ai.parts.length, 1, "连续的动作应归成一组");
assert.strictEqual(C.actsSummary(ai.parts[0].list), "读了 1 个文件 · 运行了 1 条命令");

ev({ type: "delta", item: "m1", text: "项目" });
ev({ type: "delta", item: "m1", text: "进行中" });
assert.strictEqual(ai.parts[1].text, "项目进行中");
ev({ type: "message", item: "m1", text: "项目进行中。" });
assert.strictEqual(ai.parts[1].text, "项目进行中。");
assert.strictEqual(ai.parts[1].done, true);

ev({ type: "activity", item: "img1", label: "画图中…", status: "started" });
ev({ type: "image", item: "img1", path: "/tmp/猫.png", prompt: "橘猫" });
assert.strictEqual(ai.parts[2].list[0].label, "画了 1 张图");
assert.strictEqual(ai.parts[3].type, "image");

ev({ type: "approval", id: "900", kind: "command", command: "touch a.txt", reason: "建文件" });
var ask = m.blocks[m.blocks.length - 1];
assert.strictEqual(ask.state, "open");
ev({ type: "approval_done", id: "900", decision: "decline" });
assert.strictEqual(ask.state, "decline");

ev({ type: "usage", total: 178720 });
assert.strictEqual(C.usageText(m.usage), "这次对话用量 17.9 万");
ev({ type: "turn_done", status: "interrupted" });
assert.strictEqual(m.busy, false);
assert.strictEqual(m.blocks[m.blocks.length - 1].text, "已停止");

// 3·附 摘要里每种动作都说清楚，不出现笼统的“其他”；被拒绝的单独说
assert.strictEqual(C.actsSummary([
  { label: "看文件夹 .", status: "done" }, { label: "搜索 greet", status: "done" },
  { label: "搜索网页 天气", status: "done" }, { label: "用工具 query", status: "done" },
  { label: "运行 python3 collab run", status: "declined" }, { label: "看图片 a.png", status: "done" }
]), "看了 1 个文件夹 · 在项目里搜索 1 次 · 搜索网页 1 次 · 运行了 1 条命令 · 用了 1 次工具 · 看了 1 张图片 · 其中 1 个被你拒绝");

// 4. 断线重连时重复收到的事件不会再显示一次
var before = m.blocks.length;
m.apply({ seq: 3, type: "user", text: "重复" });
assert.strictEqual(m.blocks.length, before);

// 5. 出错用红字提示，状态文字也会跟着更新
ev({ type: "status", text: "正在等对话让出来…" });
assert.strictEqual(m.status, "正在等对话让出来…");
ev({ type: "status", text: "未连上 Codex" });
ev({ type: "error", text: "和 Codex 的连接断了" });
assert.strictEqual(m.status, "未连上 Codex");
assert.strictEqual(m.blocks[m.blocks.length - 1].tone, "bad");

// 切换对话时用：读好的历史直接变成画面
var cached = C.modelFrom([{ seq: 1, type: "user", text: "旧问题", history: true }, { seq: 2, type: "message", item: "a", text: "旧回答", history: true }]);
assert.deepStrictEqual(cached.blocks.map(function (b) { return b.kind; }), ["user", "ai"]);
assert.strictEqual(cached.busy, false);

// 连不上之后：再点同一段对话要能重连，旧的报错不留在画面上
assert.strictEqual(C.needsOpen("a", "a", false), false, "已经连着的对话，再点不用重连");
assert.strictEqual(C.needsOpen("a", "a", true), true, "连失败的对话，再点要重连");
assert.strictEqual(C.needsOpen("b", "a", false), true, "点别的对话要切换");
var failed = C.modelFrom([{ seq: 1, type: "user", text: "旧问题", history: true }]);
failed.apply({ seq: 2, type: "error", text: "连不上 Codex" });
failed.dropErrors();
assert.ok(failed.blocks.every(function (b) { return b.tone !== "bad"; }), "旧的报错要清掉");
assert.strictEqual(failed.blocks[0].kind, "user", "历史内容要留下");

console.log("OK");
