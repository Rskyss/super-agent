/* 左下角切换项目的“规则层”检查（验收测试，施工方不许改）。由 test_projswitch_acceptance.py 调用。
   依据 docs/切换项目/施工单-切换项目.md */
"use strict";
var assert = require("assert");
var path = require("path");
var N = require(path.join(__dirname, "..", "desktop", "static", "nav.js"));
var C = require(path.join(__dirname, "..", "desktop", "static", "chat.js"));

// 1. 项目名：取文件夹名，末尾斜杠不影响
assert.strictEqual(typeof N.projectName, "function", "NavCore.projectName 还没有");
assert.strictEqual(N.projectName("/Users/rs/Desktop/project/开发助手"), "开发助手");
assert.strictEqual(N.projectName("/Users/rs/sp2/"), "sp2");

// 2. 浮层列表：顺序照给的（最近用的在前），当前项目打勾
assert.strictEqual(typeof N.projectItems, "function", "NavCore.projectItems 还没有");
var list = ["/a/sp2", "/a/w2", "/a/deepseek-harness", "/a/开发助手"];
var items = N.projectItems(list, "/a/w2", "");
assert.deepStrictEqual(items.map(function (i) { return i.name; }), ["sp2", "w2", "deepseek-harness", "开发助手"]);
assert.deepStrictEqual(items.map(function (i) { return i.path; }), list);
assert.deepStrictEqual(items.map(function (i) { return i.current; }), [false, true, false, false]);

// 3. 搜索：按项目名，不分大小写，含这几个字就算；空白等于没搜
assert.deepStrictEqual(N.projectItems(list, "/a/w2", "DEEP").map(function (i) { return i.name; }), ["deepseek-harness"]);
assert.deepStrictEqual(N.projectItems(list, "/a/w2", "助手").map(function (i) { return i.name; }), ["开发助手"]);
assert.strictEqual(N.projectItems(list, "/a/w2", "   ").length, 4);
assert.strictEqual(N.projectItems(list, "/a/w2", "没有这个").length, 0);
// 只按名字搜，不按上层路径搜（"/a/" 人人都有）
assert.strictEqual(N.projectItems(list, "/a/w2", "a/").length, 0);

// 4. 当前项目不在列表里（比如自测打开的）：补在最上面并打勾；重复的只留一个
var odd = N.projectItems(["/a/sp2", "/a/sp2", "/a/w2"], "/tmp/试用", "");
assert.deepStrictEqual(odd.map(function (i) { return i.path; }), ["/tmp/试用", "/a/sp2", "/a/w2"]);
assert.strictEqual(odd[0].current, true);
assert.deepStrictEqual(N.projectItems(null, "/a/w2", "").map(function (i) { return i.path; }), ["/a/w2"]);

// 5. 切回项目时恢复上次停的地方（行政对话）
assert.strictEqual(typeof C.restorePlace, "function", "ChatCore.restorePlace 还没有");
var threads = [{ id: "t-new" }, { id: "t-old" }];
// 5a. 记录的对话还在：打开它，草稿放回去
assert.deepStrictEqual(C.restorePlace(threads, { thread: "t-old", draft: "还没发的话" }), { open: "t-old", draft: "还没发的话" });
// 5b. 记录的对话已被删：照旧接最近一段，草稿不要（它属于那段被删的对话）
assert.deepStrictEqual(C.restorePlace(threads, { thread: "t-gone", draft: "旧草稿" }), { open: "t-new", draft: "" });
// 5c. 离开时停在“新对话”（还没发过话）且打了字：回到新对话，字还在
assert.deepStrictEqual(C.restorePlace(threads, { thread: "", draft: "刚想说" }), { open: null, draft: "刚想说" });
// 5d. 没有记录、或记录是空的：和现在一样接最近一段
assert.deepStrictEqual(C.restorePlace(threads, null), { open: "t-new", draft: "" });
assert.deepStrictEqual(C.restorePlace(threads, { thread: "", draft: "" }), { open: "t-new", draft: "" });
assert.deepStrictEqual(C.restorePlace([], { thread: "", draft: "" }), { open: null, draft: "" });
// 5e. 只有空白的草稿当没有
assert.deepStrictEqual(C.restorePlace(threads, { thread: "", draft: "  \n " }), { open: "t-new", draft: "" });

console.log("OK");
