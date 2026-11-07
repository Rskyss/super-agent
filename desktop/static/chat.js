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
      case "proposal":
        this._push({
          kind: "proposal", id: ev.id, proposalKind: ev.kind || "task",
          title: ev.title || "", do: ev.do || "", not_do: ev.not_do || "", done_when: ev.done_when || "",
          role: ev.role || "", tool: ev.tool || "", before: ev.before != null ? ev.before : "",
          profile: ev.profile || "", text: ev.text || "",
          state: ev.state || "open", card: ev.card || "",
        });
        break;
      case "proposal_done":
        for (i = this.blocks.length - 1; i >= 0; i--) {
          var pb = this.blocks[i];
          if (pb.kind === "proposal" && pb.id === ev.id) {
            pb.state = ev.state; pb.card = ev.card || ""; this._touch(pb); break;
          }
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

  // 一段历史对话的记录直接变成画面（切换时先显示，不等连接）
  // 在干活但还没有文字往外出时显示“正在处理”；等用户确认时不显示
  Model.prototype.showWorking = function () {
    if (!this.busy) return false;
    var last = this.blocks[this.blocks.length - 1];
    if (last && last.kind === "ask" && last.state === "open") return false;
    var part = last && last.kind === "ai" ? last.parts[last.parts.length - 1] : null;
    return !(part && part.type === "text" && !part.done);
  };

  Model.prototype.dropErrors = function () {
    this.blocks = this.blocks.filter(function (b) { return !(b.kind === "note" && b.tone === "bad"); });
  };

  // 点左栏一段对话时要不要去连：换了对话，或这段上次没连上
  function needsOpen(id, current, failed) { return id !== current || !!failed; }

  function cardInfoRows(info) {
    var rows = [];
    if (!info) return rows;
    if (info.do) rows.push({ label: "要做什么", text: info.do });
    if (info.not_do) rows.push({ label: "不做什么", text: info.not_do });
    if (info.done_when) rows.push({ label: "怎么算做完", text: info.done_when });
    return rows;
  }

  function modelFrom(events) {
    var m = new Model();
    events.forEach(function (ev) { m.apply(ev); });
    m.busy = false;
    return m;
  }

  return { esc: esc, md: md, Model: Model, actsSummary: actsSummary, usageText: usageText, modelFrom: modelFrom, needsOpen: needsOpen, cardInfoRows: cardInfoRows };
})();

if (typeof module !== "undefined") module.exports = ChatCore;

/* ---------------- 页面层（只在窗口里运行） ---------------- */
if (typeof document !== "undefined") (function () {
  "use strict";
  var params = new URLSearchParams(location.search);
  var project = params.get("project") || "", dispatcher = params.get("dispatcher") || "", token = params.get("token") || "";
  var names = { codex: "Codex", claude: "Claude" }, faces = { codex: "Cx", claude: "Cl" };
  var clerkMode = params.get("clerk") === "1";   // 聊天找行政人员：走 API，不连 Codex
  var model = new ChatCore.Model(), drawn = [];
  var $ = function (id) { return document.getElementById(id); };
  var list = $("msgs");

  $("who").textContent = names[dispatcher] || dispatcher;
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
    } else if (b.kind === "proposal") {
      function propRoleLabel(role) {
        return ({ dispatcher: "调度员", tester: "写测试", developer: "写代码", reviewer: "复查" })[role] || role;
      }
      function propToolLabel(tool) {
        if (!tool && tool !== "") return "";
        if (String(tool).indexOf("custom:") === 0) return "自定义命令";
        return ({ codex: "Codex", cursor: "Cursor", agy: "agy", none: "不复查" })[tool] || tool;
      }
      var isTeam = b.proposalKind === "team", isProfile = b.proposalKind === "profile";
      var isRevise = b.proposalKind === "revise";
      var isRevise = b.proposalKind === "revise";
      var result = isRevise ? {
        confirmed: "已确认，开始修改。",
        cancelled: "好的，先不改。",
        replaced: "已换成新的说法",
      } : isProfile ? {
        confirmed: "项目说明已保存。",
        cancelled: "好的，先不保存。",
        replaced: "已换成新的提议",
      } : isTeam ? {
        confirmed: "团队设置已更新。",
        cancelled: "好的，先不改团队设置。",
        replaced: "已换成新的提议",
      } : {
        confirmed: b.card ? ("已生成卡片 " + b.card) : "已确认",
        cancelled: "好的，先不做。",
        replaced: "已换成新的提议",
      };
      result = result[b.state];
      el.className = "ask proposal" + (b.state === "open" ? "" : (b.state === "cancelled" ? " no" : " done"));
      if (b.state === "open") {
        el.innerHTML = "<h3></h3><div class=\"rows\"></div>" +
          "<div class=\"btns\"><button class=\"btn primary\" data-prop=\"confirm\">确认</button>" +
          "<button class=\"btn\" data-prop=\"cancel\">先不做</button></div>";
        if (isRevise) {
          el.querySelector("h3").textContent = "还要修改？";
          var note = document.createElement("p");
          note.textContent = b.text || "";
          el.querySelector(".rows").appendChild(note);
        } else if (isProfile) {
          el.querySelector("h3").textContent = "保存项目说明？";
          var draft = document.createElement("div");
          draft.innerHTML = "<span>内容</span><p></p>";
          draft.querySelector("p").textContent = b.profile;
          draft.querySelector("p").style.whiteSpace = "pre-wrap";
          el.querySelector(".rows").appendChild(draft);
        } else if (isTeam) {
          el.querySelector("h3").textContent = "改团队设置？";
          var row = document.createElement("div");
          var beforeT = b.before === "" || b.before == null ? "还没选" : propToolLabel(b.before);
          row.innerHTML = "<span></span><p></p>";
          row.querySelector("span").textContent = propRoleLabel(b.role);
          row.querySelector("p").textContent = beforeT + " → " + propToolLabel(b.tool);
          el.querySelector(".rows").appendChild(row);
        } else {
          el.querySelector("h3").textContent = "变成任务？";
          el.querySelector(".rows").innerHTML =
            "<div><span>要做什么</span><p></p></div>" +
            "<div><span>不做什么</span><p></p></div>" +
            "<div><span>怎么算做完</span><p></p></div>";
          var ps = el.querySelectorAll(".rows p");
          ps[0].textContent = b.do; ps[1].textContent = b.not_do; ps[2].textContent = b.done_when;
          if (b.title) {
            var title = document.createElement("p");
            title.className = "prop-title";
            title.textContent = b.title;
            el.querySelector("h3").after(title);
          }
        }
      } else {
        el.innerHTML = "<h3></h3>";
        el.querySelector("h3").textContent = result || b.state;
      }
      el.setAttribute("data-proposal", b.id);
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
    $("usage").textContent = ChatCore.usageText(model.usage).replace(/^这次对话用量\s*/, "");
    var st = model.status || "";
    var dot = $("connDot");
    if (dot) {
      dot.className = "conn-dot " + (st.indexOf("已连上") === 0 ? "ok" : (st.indexOf("未连上") === 0 ? "bad" : (st ? "wait" : "")));
    }
    $("status").textContent = st.indexOf("已连上") === 0 ? "" : st;
    $("working").hidden = !model.showWorking();
    $("send").textContent = model.busy ? "停止" : "↑";
    $("send").className = "btn send-btn " + (model.busy ? "stop" : "primary");
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
      return;
    }
    var prop = t.closest("[data-prop]");
    if (prop) {
      var box = prop.closest("[data-proposal]");
      box.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
      api("/api/clerk/proposal", {
        conversation: currentThread || "", id: box.getAttribute("data-proposal"),
        decision: prop.getAttribute("data-prop"),
      }).catch(function (err) {
        showError(err.message);
        box.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
      });
    }
  });

  function showError(text) { model.apply({ type: "error", text: text }); render(); }

  $("composer").addEventListener("submit", function (e) {
    e.preventDefault();
    if (model.busy) {
      api(clerkMode ? "/api/clerk/stop" : "/api/chat/stop", { conversation: currentThread }).catch(function (err) { showError(err.message); });
      return;
    }
    var box = $("input"), text = box.value.trim();
    if (!text) return;
    if (pendingModel) { $("status").textContent = "正在同步，稍等一下再发送"; return; }  // 话留在输入框里
    box.value = ""; resize();
    if (clerkMode) { clerkSend(text); return; }
    api("/api/chat/send", { text: text }).catch(function (err) { box.value = text; showError(err.message); });
  });
  $("input").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); }
  });
  function resize() { var box = $("input"); box.style.height = "auto"; box.style.height = Math.min(box.scrollHeight, 160) + "px"; }
  $("input").addEventListener("input", resize);

  var failures = 0, stopped = false, pendingModel = null, pollCtrl = null, pollTimer = null, gen = 0;
  var threadCache = {}, currentThread = null;   // 读好的历史对话：点的时候直接显示
  var connectFailed = false;                    // 当前这段没连上，再点一次要重连
  var blankChat = false;                        // 新对话还没说话：Codex 还没存档，接不回来
  var viewingCard = false, mainThread = null;   // 正在看任务对话；回去时接着这段主对话
  var clerkPlaceholder = "跟行政说…";

  function pause() {
    stopped = true;
    gen++;
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    if (pollCtrl) { try { pollCtrl.abort(); } catch (_) {} pollCtrl = null; }
    $("offline").hidden = true;
  }

  // 行政对话：内容都在本机，切换马上显示，不用连接
  function clerkOpen(id) {
    pause();
    stopped = false;
    failures = 0;
    if ((id || null) !== currentThread) $("input").value = "";   // 草稿不带到别的对话
    currentThread = id || null;
    if (!viewingCard) mainThread = currentThread;
    pendingModel = null;
    model = new ChatCore.Model();
    drawn = [];
    list.innerHTML = "";
    render();
    api("/api/clerk/focus", { conversation: id || "" }).catch(function () { /* 只影响左栏选中 */ });
    if (id) { var myGen = ++gen; poll(myGen); }
  }

  function clerkSend(text) {
    var fromBlank = !currentThread, mine = gen;   // 回应到之前用户可能已经切走了
    api("/api/clerk/send", { conversation: currentThread || "", text: text }).then(function (d) {
      if (!fromBlank || gen !== mine) return;
      currentThread = d.conversation;
      if (!viewingCard) mainThread = currentThread;
      api("/api/clerk/focus", { conversation: currentThread }).catch(function () {});
      var myGen = ++gen;
      poll(myGen);
    }).catch(function (err) {
      if (gen === mine) $("input").value = text;   // 已经切走就不放回去，免得发到别的对话
      showError(err.message);
    });
  }

  // 从终端或设置回来：和切换对话走同一条路，接着当前这段
  function resume() {
    if (clerkMode) { clerkOpen(currentThread); return; }
    if (blankChat) switchSession("fresh", false, null);
    else switchSession("continue", false, currentThread);
  }

  function switchSession(mode, withOpening, threadId) {
    pause();
    stopped = false;
    failures = 0;
    if (connectFailed) model.dropErrors();
    connectFailed = false;
    blankChat = false;
    if (currentThread && !pendingModel && model.blocks.length) threadCache[currentThread] = model;
    var cached = threadId ? threadCache[threadId] : null;
    currentThread = threadId || null;
    drawn = [];
    list.innerHTML = "";
    if (cached) {
      // 先显示读好的内容，连接在后台进行，连上后静默换成最新的
      model = cached;
      model.busy = false;
      model.status = "已连上 " + (names[dispatcher] || dispatcher);
      pendingModel = new ChatCore.Model();
    } else {
      model = new ChatCore.Model();
      model.status = "正在连接 " + (names[dispatcher] || dispatcher) + "…";
      pendingModel = null;
    }
    render();
    list.scrollTop = list.scrollHeight;
    var myGen = ++gen;
    api("/api/chat/open", { dispatcher: dispatcher, mode: mode, opening: withOpening, thread_id: threadId || "" })
      .catch(function (err) { if (myGen === gen) showError(err.message); })
      .then(function () { if (myGen === gen && !stopped) poll(myGen); });
  }

  function hideCard() {
    viewingCard = false;
    var head = document.getElementById("cardHead");
    var box = document.getElementById("cardInfo");
    if (head) { head.hidden = true; head.textContent = ""; }
    if (box) { box.hidden = true; box.textContent = ""; }
    if (window.__cardPhaseTimer) clearInterval(window.__cardPhaseTimer);
  }

  window.SuperAgentChat = {
    pause: pause, resume: resume,
    isWorking: function () { return !clerkMode && !stopped && !!(model.busy || (pendingModel && pendingModel.busy)); },
    startFresh: function () {
      if (clerkMode) { hideCard(); clerkOpen(null); } else switchSession("fresh", true, null);
    },
    openThread: function (id) {
      if (clerkMode) { if (id !== currentThread) clerkOpen(id); return; }
      if (ChatCore.needsOpen(id, currentThread, connectFailed)) switchSession("continue", false, id);
    },
    openCard: function (info) {
      info = info || {};
      var head = document.getElementById("cardHead");
      var box = document.getElementById("cardInfo");
      if (head) {
        head.hidden = false;
        head.textContent = (info.title || "") + (info.phase && info.phase.label ? " · " + info.phase.label : "");
      }
      if (box) {
        box.hidden = false;
        box.textContent = "";
        ChatCore.cardInfoRows(info).forEach(function (row) {
          var line = document.createElement("p");
          line.textContent = row.label + "：" + row.text;
          box.appendChild(line);
        });
      }
      viewingCard = true;
      if (info.conversation) clerkOpen(info.conversation);
      if (window.__cardPhaseTimer) clearInterval(window.__cardPhaseTimer);
      window.__cardPhaseTimer = setInterval(function () {
        if (!info.conversation) return;
        fetch("/api/cards?project=" + encodeURIComponent(project)).then(function (r) { return r.json(); }).then(function (cards) {
          if (!Array.isArray(cards) || !head) return;
          for (var i = 0; i < cards.length; i++) {
            if (cards[i].id === info.id) {
              var label = cards[i].phase && cards[i].phase.label;
              if (label) head.textContent = (info.title || "") + " · " + label;
            }
          }
        }).catch(function () {});
      }, 3000);
    },
    closeCard: function () {
      if (!viewingCard) return;
      hideCard();
      clerkOpen(mainThread);
    },
  };
  window.addEventListener("pagehide", pause);
  window.addEventListener("beforeunload", pause);

  // 连上之后，把最近几段对话的内容先读好
  function prefetchThreads() {
    var query = function (extra) { return new URLSearchParams(Object.assign({ project: project, token: token }, extra)).toString(); };
    fetch("/api/chat/threads?" + query({})).then(function (r) { return r.json(); }).then(function (d) {
      var wanted = (d.threads || []).filter(function (t) { return !t.current && !threadCache[t.id]; }).slice(0, 10);
      return wanted.reduce(function (done, t) {
        return done.then(function () {
          return fetch("/api/chat/thread?" + query({ id: t.id })).then(function (r) { return r.json(); }).then(function (x) {
            if (x.events && x.events.length && !threadCache[t.id]) threadCache[t.id] = ChatCore.modelFrom(x.events);
          });
        });
      }, Promise.resolve());
    }).catch(function () { /* 读不到就等点的时候再连 */ });
  }

  function poll(myGen) {
    if (stopped || myGen !== gen) return;
    var active = pendingModel || model;
    pollCtrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    var feed = clerkMode ? "/api/clerk/events?" : "/api/chat/events?";
    fetch(feed + new URLSearchParams({ project: project, conversation: currentThread || "", since: active.seq, wait: 20, token: token }).toString(),
          pollCtrl ? { signal: pollCtrl.signal } : {})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (stopped || myGen !== gen) return;
        failures = 0; $("offline").hidden = true;
        if (d.error) throw new Error(d.error);
        d.events.forEach(function (ev) {
          if (ev.type === "status" && ev.thread_id) {
            currentThread = ev.thread_id;
            blankChat = !(pendingModel || model).blocks.length;
            setTimeout(prefetchThreads, 300);
          }
          if (ev.type === "user" || ev.type === "opening") blankChat = false;
          if (ev.type === "status" && ev.text && ev.text.indexOf("未连上") === 0) connectFailed = true;
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
              model.seq = pendingModel.seq;   // 接着新连接的编号取，不然旧事件会再来一遍
            }
            pendingModel = null;
          } else if (ev.type === "error") {
            model.status = pendingModel.status || ("未连上 " + (names[dispatcher] || dispatcher));
            model.apply({ type: "error", text: ev.text });
            model.seq = pendingModel.seq;
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
  } else if (clerkMode) {
    $("whoFace").textContent = "行";
    $("who").textContent = "行政";
    $("input").placeholder = clerkPlaceholder;
    fetch("/api/clerk/list?" + new URLSearchParams({ project: project, token: token }).toString())
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var latest = d.threads && d.threads.length ? d.threads[0].id : null;
        if (viewingCard) mainThread = latest;   // 列表回来前已经开了卡，不能把卡片换掉
        else clerkOpen(latest);
      })
      .catch(function () { if (!viewingCard) clerkOpen(null); });
  } else {
    var initGen = ++gen;
    api("/api/chat/open", { dispatcher: dispatcher, mode: params.get("mode") || "continue",
                            opening: params.get("opening") !== "0" })
      .catch(function (err) { showError(err.message); })
      .then(function () { poll(initGen); });
  }
})();
