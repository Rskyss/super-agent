/* 左边聊天视图。分两层：
   ChatCore（记账层）：把调度员的聊天事件整理成“对话现在该显示什么”，不碰页面，可以单独测试；
   页面层：按记账结果画出来，只重画变了的那几块。
   安全：调度员回复里的任何文字都当纯文字处理，只排版加粗、列表、代码这几样，不会被当成网页代码执行。 */
var ChatCore = (function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function inline(s) {
    // 先整体转义，再认几种简单写法；所有内容都已经是纯文字
    return esc(s)
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  }

  /** 简单排版：段落、标题、列表、代码块。 */
  function md(text) {
    var lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
    var out = [], para = [], list = null, code = null;
    function flushPara() { if (para.length) { out.push("<p>" + para.map(inline).join("<br>") + "</p>"); para = []; } }
    function flushList() { if (list) { out.push("<" + list.tag + ">" + list.items.map(function (i) { return "<li>" + inline(i) + "</li>"; }).join("") + "</" + list.tag + ">"); list = null; } }
    lines.forEach(function (line) {
      if (code !== null) {
        if (/^```/.test(line)) { out.push("<pre><code>" + esc(code.join("\n")) + "</code></pre>"); code = null; }
        else code.push(line);
        return;
      }
      if (/^```/.test(line)) { flushPara(); flushList(); code = []; return; }
      var h = /^(#{1,4})\s+(.*)$/.exec(line);
      var ul = /^\s*[-*•]\s+(.*)$/.exec(line);
      var ol = /^\s*\d+[.、)]\s+(.*)$/.exec(line);
      if (h) { flushPara(); flushList(); out.push("<p class=\"h\">" + inline(h[2]) + "</p>"); }
      else if (ul || ol) {
        flushPara();
        var tag = ul ? "ul" : "ol";
        if (!list || list.tag !== tag) { flushList(); list = { tag: tag, items: [] }; }
        list.items.push((ul || ol)[1]);
      } else if (!line.trim()) { flushPara(); flushList(); }
      else { flushList(); para.push(line); }
    });
    if (code !== null) out.push("<pre><code>" + esc(code.join("\n")) + "</code></pre>");
    flushPara(); flushList();
    return out.join("");
  }

  function Model() {
    this.blocks = [];       // 一块一块：sys / user / ai / ask / note / divider
    this.busy = false;
    this.usage = null;
    this.status = "";
    this.seq = 0;
    this._sawHistory = false;
    this._divided = false;
  }

  Model.prototype._push = function (block) { block.v = 1; this.blocks.push(block); return block; };
  Model.prototype._touch = function (block) { block.v = (block.v || 0) + 1; };
  Model.prototype._ai = function () {
    var last = this.blocks[this.blocks.length - 1];
    return last && last.kind === "ai" ? last : this._push({ kind: "ai", parts: [] });
  };
  Model.prototype._textPart = function (ai, id) {
    for (var i = ai.parts.length - 1; i >= 0; i--) {
      if (ai.parts[i].type === "text" && ai.parts[i].id === id) return ai.parts[i];
    }
    var part = { type: "text", id: id, text: "", done: false };
    ai.parts.push(part);
    return part;
  };

  /** 收下一条事件。重复的（编号不大于已收的）直接忽略，断线重连不会重复显示。 */
  Model.prototype.apply = function (ev) {
    if (ev.seq != null) { if (ev.seq <= this.seq) return; this.seq = ev.seq; }
    if (ev.history) this._sawHistory = true;
    else if (this._sawHistory && !this._divided && ev.type !== "status" && ev.type !== "usage") {
      this._divided = true;
      this._push({ kind: "divider", text: "以上是之前的对话" });
    }
    var ai, part, i;
    switch (ev.type) {
      case "status": this.status = ev.text; break;
      case "opening": this._push({ kind: "sys", text: ev.text }); break;
      case "user": this._push({ kind: "user", text: ev.text }); break;
      case "delta":
        ai = this._ai(); part = this._textPart(ai, ev.item);
        part.text += ev.text; this._touch(ai); break;
      case "message":
        if (!ev.text) break;
        ai = this._ai(); part = this._textPart(ai, ev.item);
        part.text = ev.text; part.done = true; this._touch(ai); break;
      case "activity":
        ai = this._ai();
        var acts = ai.parts[ai.parts.length - 1];
        if (!acts || acts.type !== "acts") { acts = { type: "acts", list: [], open: false }; ai.parts.push(acts); }
        var hit = null;
        for (i = 0; i < acts.list.length; i++) if (acts.list[i].id === ev.item) hit = acts.list[i];
        if (hit) { hit.label = ev.label; hit.status = ev.status; hit.detail = ev.detail || hit.detail; }
        else acts.list.push({ id: ev.item, label: ev.label, status: ev.status, detail: ev.detail || "" });
        this._touch(ai); break;
      case "image":
        ai = this._ai();
        // 画图中的那一行改成“画了 1 张图”
        ai.parts.forEach(function (p) {
          if (p.type === "acts") p.list.forEach(function (a) { if (a.id === ev.item) { a.status = "done"; a.label = "画了 1 张图"; } });
        });
        ai.parts.push({ type: "image", path: ev.path, prompt: ev.prompt || "" });
        this._touch(ai); break;
      case "approval":
        this._push({ kind: "ask", id: ev.id, what: ev.kind, command: ev.command || "", reason: ev.reason || "", state: "open" });
        break;
      case "approval_done":
        for (i = this.blocks.length - 1; i >= 0; i--) {
          var b = this.blocks[i];
          if (b.kind === "ask" && b.id === ev.id) { b.state = ev.decision; this._touch(b); break; }
        }
        break;
      case "turn_started": this.busy = true; break;
      case "turn_done":
        this.busy = false;
        this.blocks.forEach(function (bl) { if (bl.kind === "ai") bl.parts.forEach(function (p) { if (p.type === "text") p.done = true; }); });
        var last = this.blocks[this.blocks.length - 1];
        if (last) this._touch(last);
        if (ev.status === "interrupted") this._push({ kind: "note", tone: "", text: "已停止" });
        if (ev.status === "failed") this._push({ kind: "note", tone: "bad", text: "这一轮没完成：" + (ev.error || "原因不明") });
        break;
      case "usage": this.usage = ev.total; break;
      case "notice": this._push({ kind: "note", tone: "", text: ev.text }); break;
      case "error": this._push({ kind: "note", tone: "bad", text: ev.text }); break;
    }
  };

  // 每种动作怎么说（按标签开头认），顺序就是摘要里出现的顺序
  var KINDS = [
    [/^读 /, "读了 {n} 个文件"], [/^看文件夹/, "看了 {n} 个文件夹"], [/^搜索 /, "在项目里搜索 {n} 次"],
    [/^搜索网页/, "搜索网页 {n} 次"], [/^改 /, "改了 {n} 处文件"], [/^运行 /, "运行了 {n} 条命令"],
    [/^用工具/, "用了 {n} 次工具"], [/^看图片/, "看了 {n} 张图片"], [/^画/, "画了 {n} 张图"]
  ];

  /** 一组动作的摘要：“读了 2 个文件 · 运行了 1 条命令”。 */
  function actsSummary(list) {
    var counts = KINDS.map(function () { return 0; }), other = 0, declined = 0, going = null;
    list.forEach(function (a) {
      if (a.status === "started") going = a.label;
      if (a.status === "declined") declined++;
      for (var i = 0; i < KINDS.length; i++) { if (KINDS[i][0].test(a.label)) { counts[i]++; return; } }
      other++;
    });
    if (going) return going.replace(/…$/, "") + "…";
    var bits = [];
    KINDS.forEach(function (k, i) { if (counts[i]) bits.push(k[1].replace("{n}", counts[i])); });
    if (other) bits.push("另有 " + other + " 个动作");
    if (declined) bits.push("其中 " + declined + " 个被你拒绝");
    return bits.join(" · ") || "干活中";
  }

  function usageText(total) {
    if (total == null) return "";
    return "这次对话用量 " + (total >= 10000 ? (total / 10000).toFixed(1) + " 万" : total);
  }

  return { esc: esc, md: md, Model: Model, actsSummary: actsSummary, usageText: usageText };
})();

if (typeof module !== "undefined") module.exports = ChatCore;

/* ---------------- 页面层（只在窗口里运行） ---------------- */
if (typeof document !== "undefined") (function () {
  "use strict";
  var params = new URLSearchParams(location.search);
  var project = params.get("project") || "", dispatcher = params.get("dispatcher") || "", token = params.get("token") || "";
  var names = { codex: "Codex", claude: "Claude" }, faces = { codex: "Cx", claude: "Cl" };
  var model = new ChatCore.Model(), drawn = [];
  var $ = function (id) { return document.getElementById(id); };
  var list = $("msgs");

  $("who").textContent = "调度员 " + (names[dispatcher] || dispatcher);
  $("whoFace").textContent = faces[dispatcher] || "调";

  function api(path, body) {
    return fetch(path, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Chat-Token": encodeURIComponent(token) },
      body: JSON.stringify(Object.assign({ project: project }, body || {}))
    }).then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || "出错了"); return d; }); });
  }

  function imageUrl(path) {
    return "/api/chat/image?" + new URLSearchParams({ project: project, path: path, token: token }).toString();
  }

  function renderBlock(b, idx) {
    var el = document.createElement("div");
    var face = faces[dispatcher] || "调";
    if (b.kind === "sys") {
      el.className = "sys";
      el.innerHTML = "超级助手自动交代<button type=\"button\" data-sys>看全文</button><pre hidden></pre>";
      el.querySelector("pre").textContent = b.text;
    } else if (b.kind === "user") {
      el.className = "row me";
      el.innerHTML = "<span class=\"av\">你</span><div class=\"bubble\"></div>";
      el.querySelector(".bubble").textContent = b.text;
    } else if (b.kind === "ai") {
      el.className = "row ai";
      var html = "<span class=\"av\">" + ChatCore.esc(face) + "</span><div class=\"stack\">";
      b.parts.forEach(function (p, pi) {
        if (p.type === "text") {
          html += "<div class=\"bubble\">" + ChatCore.md(p.text) + (p.done || !model.busy ? "" : "<span class=\"caret\"></span>") + "</div>";
        } else if (p.type === "acts") {
          html += "<div class=\"acts\"><button type=\"button\" data-acts=\"" + idx + ":" + pi + "\" aria-expanded=\"" + p.open + "\">" +
            ChatCore.esc(ChatCore.actsSummary(p.list)) + "</button>" + (p.open ? "<ul>" + p.list.map(function (a) {
              return "<li class=\"" + ChatCore.esc(a.status) + "\" title=\"" + ChatCore.esc(a.detail) + "\">" + ChatCore.esc(a.label) + "</li>";
            }).join("") + "</ul>" : "") + "</div>";
        } else if (p.type === "image") {
          html += "<figure class=\"img\"><a href=\"" + ChatCore.esc(imageUrl(p.path)) + "\" target=\"_blank\"><img alt=\"" +
            ChatCore.esc(p.prompt || "调度员画的图") + "\" src=\"" + ChatCore.esc(imageUrl(p.path)) + "\"></a><figcaption>" +
            ChatCore.esc(names[dispatcher] || "调度员") + " 画的图 · 已存在你电脑上</figcaption></figure>";
        }
      });
      el.innerHTML = html + "</div>";
    } else if (b.kind === "ask") {
      var titles = { command: "调度员想运行一条命令，允许吗？", file: "调度员想改文件，允许吗？", permissions: "调度员想要更多权限，允许吗？" };
      var done = { accept: "已允许", acceptForSession: "已允许（这次对话里同类不再问）", decline: "你拒绝了" }[b.state];
      el.className = "ask" + (b.state === "open" ? "" : (b.state === "decline" ? " no" : " done"));
      el.innerHTML = "<h3></h3>" + (b.command ? "<code></code>" : "") + (b.reason ? "<p></p>" : "") +
        (b.state === "open" ? "<div class=\"btns\"><button class=\"btn primary\" data-ans=\"accept\">允许</button>" +
          "<button class=\"btn\" data-ans=\"acceptForSession\">这次对话都允许</button><button class=\"btn\" data-ans=\"decline\">拒绝</button></div>" : "");
      el.querySelector("h3").textContent = done || titles[b.what] || "调度员想做一件事，允许吗？";
      if (b.command) el.querySelector("code").textContent = b.command;
      if (b.reason) el.querySelector("p").textContent = "理由：" + b.reason;
      el.setAttribute("data-ask", b.id);
    } else if (b.kind === "note") {
      el.className = "note " + (b.tone || "");
      el.textContent = b.text;
    } else if (b.kind === "divider") {
      el.className = "divider";
      el.textContent = b.text;
    }
    el.v = b.v;
    return el;
  }

  function render() {
    var nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
    model.blocks.forEach(function (b, i) {
      if (drawn[i] && drawn[i].v === b.v && !(b.kind === "ai" && i === model.blocks.length - 1)) return;
      var el = renderBlock(b, i);
      if (drawn[i]) {
        if (drawn[i].outerHTML === el.outerHTML) { drawn[i].v = b.v; return; }
        list.replaceChild(el, drawn[i]);
      } else {
        list.appendChild(el);
      }
      drawn[i] = el;
    });
    while (drawn.length > model.blocks.length) {
      list.removeChild(drawn.pop());
    }
    $("usage").textContent = ChatCore.usageText(model.usage);
    $("status").textContent = model.status;
    $("send").textContent = model.busy ? "停止" : "发送";
    $("send").className = "btn " + (model.busy ? "stop" : "primary");
    if (nearBottom) list.scrollTop = list.scrollHeight;
  }

  list.addEventListener("click", function (e) {
    var t = e.target;
    var sys = t.closest("[data-sys]");
    if (sys) { var pre = sys.nextElementSibling; pre.hidden = !pre.hidden; sys.textContent = pre.hidden ? "看全文" : "收起"; return; }
    var acts = t.closest("[data-acts]");
    if (acts) {
      var ij = acts.getAttribute("data-acts").split(":"), part = model.blocks[+ij[0]].parts[+ij[1]];
      part.open = !part.open; model.blocks[+ij[0]].v++; render(); return;
    }
    var ans = t.closest("[data-ans]");
    if (ans) {
      var card = ans.closest("[data-ask]");
      card.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
      api("/api/chat/answer", { id: card.getAttribute("data-ask"), decision: ans.getAttribute("data-ans") })
        .catch(function (err) { showError(err.message); card.querySelectorAll("button").forEach(function (b) { b.disabled = false; }); });
    }
  });

  function showError(text) { model.apply({ type: "error", text: text }); render(); }

  $("composer").addEventListener("submit", function (e) {
    e.preventDefault();
    if (model.busy) { api("/api/chat/stop").catch(function (err) { showError(err.message); }); return; }
    var box = $("input"), text = box.value.trim();
    if (!text) return;
    box.value = ""; resize();
    api("/api/chat/send", { text: text }).catch(function (err) { box.value = text; showError(err.message); });
  });
  $("input").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); }
  });
  function resize() { var box = $("input"); box.style.height = "auto"; box.style.height = Math.min(box.scrollHeight, 160) + "px"; }
  $("input").addEventListener("input", resize);

  var failures = 0, stopped = false, pendingModel = null, pollCtrl = null, pollTimer = null, gen = 0;

  function pause() {
    stopped = true;
    gen++;
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    if (pollCtrl) { try { pollCtrl.abort(); } catch (_) {} pollCtrl = null; }
    $("offline").hidden = true;
  }

  function resume() {
    pause();
    stopped = false;
    failures = 0;
    var myGen = ++gen;
    if (model.blocks.length > 0) {
      pendingModel = new ChatCore.Model();
      model.status = "正在同步对话…";
      $("status").textContent = model.status;
    } else {
      pendingModel = null;
      model.status = "正在连接 " + (names[dispatcher] || dispatcher) + "…";
      $("status").textContent = model.status;
    }
    api("/api/chat/open", { dispatcher: dispatcher, mode: "continue", opening: false })
      .catch(function (err) { if (myGen === gen) { pendingModel = null; showError(err.message); } })
      .then(function () { if (myGen === gen && !stopped) poll(myGen); });
  }

  function closeHistoryMenu() {
    var menu = $("historyMenu"), btn = $("historyBtn");
    if (menu) menu.hidden = true;
    if (btn) btn.setAttribute("aria-expanded", "false");
  }

  function switchSession(mode, withOpening, threadId) {
    pause();
    closeHistoryMenu();
    stopped = false;
    failures = 0;
    pendingModel = null;
    model = new ChatCore.Model();
    drawn = [];
    list.innerHTML = "";
    model.status = "正在连接 " + (names[dispatcher] || dispatcher) + "…";
    render();
    var myGen = ++gen;
    api("/api/chat/open", { dispatcher: dispatcher, mode: mode, opening: withOpening, thread_id: threadId || "" })
      .catch(function (err) { if (myGen === gen) showError(err.message); })
      .then(function () { if (myGen === gen && !stopped) poll(myGen); });
  }

  function formatTime(sec) {
    if (!sec) return "";
    var d = new Date(sec * 1000), now = new Date();
    var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
    var hm = pad(d.getHours()) + ":" + pad(d.getMinutes());
    if (d.toDateString() === now.toDateString()) return "今天 " + hm;
    return pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " + hm;
  }

  if ($("newChatBtn")) {
    $("newChatBtn").addEventListener("click", function () {
      switchSession("fresh", true, null);
    });
  }

  if ($("historyBtn")) {
    $("historyBtn").addEventListener("click", function (e) {
      e.stopPropagation();
      var menu = $("historyMenu"), btn = $("historyBtn");
      if (!menu.hidden) { closeHistoryMenu(); return; }
      menu.hidden = false;
      btn.setAttribute("aria-expanded", "true");
      menu.innerHTML = "<div class=\"history-empty\">正在读取历史对话…</div>";
      fetch("/api/chat/threads?" + new URLSearchParams({ project: project, token: token }).toString())
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var items = (d && d.threads) || [];
          if (!items.length) {
            menu.innerHTML = "<div class=\"history-empty\">还没有其他历史对话</div>";
            return;
          }
          menu.innerHTML = items.map(function (it) {
            var cur = it.current ? " current" : "";
            var mark = it.current ? "✓ " : "";
            return "<button type=\"button\" class=\"history-item" + cur + "\" data-thread=\"" + ChatCore.esc(it.id) +
              "\" data-current=\"" + (it.current ? "1" : "0") + "\"><span class=\"h-title\">" + mark +
              ChatCore.esc(it.title || "对话") + "</span><span class=\"h-time\">" +
              ChatCore.esc(formatTime(it.updated)) + "</span></button>";
          }).join("");
        })
        .catch(function () {
          menu.innerHTML = "<div class=\"history-empty\">读取历史对话失败</div>";
        });
    });
  }

  if ($("historyMenu")) {
    $("historyMenu").addEventListener("click", function (e) {
      e.stopPropagation();
      var item = e.target.closest("[data-thread]");
      if (!item) return;
      var tid = item.getAttribute("data-thread");
      if (item.getAttribute("data-current") === "1") { closeHistoryMenu(); return; }
      switchSession("continue", false, tid);
    });
  }

  document.addEventListener("click", closeHistoryMenu);

  window.SuperAgentChat = { pause: pause, resume: resume, startFresh: function () { switchSession("fresh", true, null); } };
  window.addEventListener("pagehide", pause);
  window.addEventListener("beforeunload", pause);

  function poll(myGen) {
    if (stopped || myGen !== gen) return;
    var active = pendingModel || model;
    pollCtrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    fetch("/api/chat/events?" + new URLSearchParams({ project: project, since: active.seq, wait: 20, token: token }).toString(),
          pollCtrl ? { signal: pollCtrl.signal } : {})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (stopped || myGen !== gen) return;
        failures = 0; $("offline").hidden = true;
        if (d.error) throw new Error(d.error);
        d.events.forEach(function (ev) {
          if (!pendingModel) { model.apply(ev); return; }
          pendingModel.apply(ev);
          if (ev.type === "status" && ev.text && ev.text.indexOf("正在等") === 0) {
            model.status = ev.text; $("status").textContent = model.status;
          } else if (ev.type === "status" && ev.text && ev.text.indexOf("已连上") === 0) {
            if (pendingModel.blocks.length > 0) {
              model = pendingModel;
              drawn.forEach(function (el) { if (el) el.v = 0; });
            } else {
              model.status = pendingModel.status;
            }
            pendingModel = null;
          } else if (ev.type === "error") {
            model.status = pendingModel.status || ("未连上 " + (names[dispatcher] || dispatcher));
            model.apply({ type: "error", text: ev.text });
            pendingModel = null;
          }
        });
        if (!pendingModel) {
          if (typeof d.busy === "boolean") model.busy = d.busy;
          render();
        }
        if (!d.open) { stopped = true; return; }
      })
      .catch(function (err) {
        if (stopped || myGen !== gen || (err && err.name === "AbortError")) return;
        failures++; $("offline").hidden = false;
      })
      .then(function () {
        if (!stopped && myGen === gen) {
          pollTimer = setTimeout(function () { poll(myGen); }, failures ? Math.min(5000, 500 * failures) : 0);
        }
      });
  }

  if (!project || !token) {
    $("status").textContent = "请从超级助手窗口打开聊天";
  } else {
    var initGen = ++gen;
    api("/api/chat/open", { dispatcher: dispatcher, mode: params.get("mode") || "continue",
                            opening: params.get("opening") !== "0" })
      .catch(function (err) { showError(err.message); })
      .then(function () { poll(initGen); });
  }
})();
