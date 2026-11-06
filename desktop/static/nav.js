/* 左栏：主对话和卡片列表。每 1.5 秒取一次卡片，点击用 superagent:// 地址告诉窗口；
   窗口用 window.SuperAgentNav.select(卡片编号, 模式) 告诉这里高亮谁。只读，不改动项目。 */
"use strict";

/* 规则层：不碰页面，tests/nav_page_check.js 用 node 直接检查。 */
var NavCore = {
  dotFor: function (state) {
    if (state === "待对齐" || state === "待派发") return { tone: "wait", label: "等你确认" };
    if (state === "开发中" || state === "待测试" || state === "测试中") return { tone: "run", label: "进行中" };
    if (state === "待验收") return { tone: "ok", label: "等你验收" };
    if (state === "需Claude介入") return { tone: "bad", label: "需要你处理" };
    if (state === "已合并") return { tone: "done", label: "已完成" };
    return { tone: "done", label: state };
  },
  filter: function (cards, text) {
    var q = String(text || "").trim().toLowerCase();
    if (!q) return cards;
    return cards.filter(function (c) {
      return c.title.toLowerCase().indexOf(q) >= 0 || c.id.toLowerCase().indexOf(q) >= 0;
    });
  },
  // 任务卡片一个列表：要你处理的排最前，然后进行中，最后已完成
  ordered: function (cards) {
    var rank = { wait: 0, ok: 0, bad: 0, run: 1, done: 2 };
    return [0, 1, 2].reduce(function (all, level) {
      return all.concat(cards.filter(function (c) { return rank[NavCore.dotFor(c.state).tone] === level; }));
    }, []);
  },
  threadTitle: function (title) { return String(title || "").replace(/（[^）]*）$/, "") || "对话"; },
  // 今天显示时间，昨天显示“昨天 时间”，更早显示日期
  timeLabel: function (sec, now) {
    if (!sec) return "";
    var d = new Date(sec * 1000), today = now || new Date();
    var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
    var clock = pad(d.getHours()) + ":" + pad(d.getMinutes());
    var yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    if (d.toDateString() === today.toDateString()) return clock;
    if (d.toDateString() === yesterday.toDateString()) return "昨天 " + clock;
    return pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  },
  visibleThreads: function (threads, all) { return all ? threads : threads.slice(0, 5); },
  markCurrent: function (threads, id) {
    return threads.map(function (t) { return Object.assign({}, t, { current: t.id === id }); });
  },
  // 选中的卡不在列表里了，回到主对话
  keepSelected: function (selected, cards) {
    return selected && cards.some(function (c) { return c.id === selected; }) ? selected : null;
  },
};
if (typeof module !== "undefined") module.exports = NavCore;

if (typeof document !== "undefined") (function () {
  var POLL_MS = 1500, THREADS_MS = 5000;
  var params = new URLSearchParams(location.search);
  var project = params.get("project") || "", dispatcher = params.get("dispatcher") || "", token = params.get("token") || "";
  var selected = params.get("selected") || null, mode = params.get("mode") || "main";
  var cards = [], threads = [], showAllThreads = false, pendingThread = null, lastKey = "";

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function tell(address) { location.href = address; }

  function drawList() {
    var list = NavCore.filter(cards, $("q").value);
    var head = '<div class="gh"><span>任务卡片</span>' + (cards.length ? "<span>" + cards.length + "</span>" : "") + "</div>";
    if (!cards.length) { $("cards").innerHTML = head + '<div class="empty">还没有卡片</div>'; return; }
    if (!list.length) { $("cards").innerHTML = head + '<div class="empty">没有找到</div>'; return; }
    $("cards").innerHTML = head + NavCore.ordered(list).map(function (c) {
      var dot = NavCore.dotFor(c.state), on = mode === "card" && c.id === selected;
      var sub = dot.label + (c.rework ? " · 返工 " + c.rework + " 次" : "");
      return '<button class="item' + (on ? " on" : "") + '" type="button" data-id="' + esc(c.id) + '" title="' + esc(c.id) + '">' +
        '<span class="lead"><i class="dot ' + dot.tone + '"></i></span><span class="tt">' + esc(c.title) + '</span><span class="sub">' + esc(sub) + "</span></button>";
    }).join("");
  }
  function drawThreads() {
    var shown = NavCore.visibleThreads(threads, showAllThreads);
    $("threads").innerHTML = shown.map(function (t) {
      return '<button class="thread' + (t.current && mode === "main" ? " on" : "") + '" type="button" data-thread="' + esc(t.id) + '" data-current="' + (t.current ? 1 : 0) + '">' +
        '<span class="tt">' + esc(NavCore.threadTitle(t.title)) + '</span><span class="tm">' + esc(NavCore.timeLabel(t.updated)) + "</span></button>";
    }).join("") + (threads.length > shown.length ? '<button class="more" id="moreThreads" type="button">全部 ' + threads.length + " 段</button>" : "");
  }
  function drawMarks() {
    $("main").classList.toggle("on", mode === "main");
    $("gear").classList.toggle("on", mode === "settings");
    drawThreads();
  }

  $("cards").addEventListener("click", function (ev) {
    var b = ev.target.closest("button[data-id]");
    if (b) tell("superagent://card?id=" + encodeURIComponent(b.getAttribute("data-id")));
  });
  $("main").addEventListener("click", function () { tell("superagent://main"); });
  $("gear").addEventListener("click", function () { tell("superagent://settings"); });
  $("newChat").addEventListener("click", function () {
    pendingThread = null; threads = NavCore.markCurrent(threads, null); drawThreads();
    tell("superagent://new");
  });
  $("threads").addEventListener("click", function (ev) {
    if (ev.target.closest("#moreThreads")) { showAllThreads = true; drawThreads(); return; }
    var b = ev.target.closest("button[data-thread]");
    if (!b) return;
    // 马上标出选中，不等连接
    pendingThread = b.getAttribute("data-thread");
    threads = NavCore.markCurrent(threads, pendingThread);
    drawThreads();
    tell("superagent://thread?id=" + encodeURIComponent(pendingThread));
  });
  $("q").addEventListener("input", drawList);

  function poll() {
    var url = "/api/view?project=" + encodeURIComponent(project) + "&dispatcher=" + encodeURIComponent(dispatcher);
    return fetch(url).then(function (r) { return r.json(); }).then(function (v) {
      if (v.error) return;
      $("proj").textContent = v.project;
      $("cardArea").hidden = !v.installed;
      $("busy").hidden = !(v.card && v.card.id === "调度中" && v.card.state === "调度中");
      cards = v.cards;
      var key = JSON.stringify(cards.map(function (c) { return [c.id, c.title, c.state, c.rework]; }));
      if (key !== lastKey) { lastKey = key; drawList(); }   // 没变化不重画：不打断滚动
      if (selected && mode === "card" && NavCore.keepSelected(selected, cards) === null) tell("superagent://main");
    }).catch(function () { /* 下一次再取 */ });
  }
  function fetchThreads() {
    if (!token) return Promise.resolve();
    return fetch("/api/chat/threads?" + new URLSearchParams({ project: project, token: token }).toString())
      .then(function (r) { return r.json(); })
      .then(function (d) {
        // 切到终端时对话让出去了，列表保留上一次的
        if (!d.threads || !d.threads.length) return;
        if (d.current === pendingThread) pendingThread = null;
        threads = pendingThread ? NavCore.markCurrent(d.threads, pendingThread) : d.threads;
        drawThreads();
      }).catch(function () { /* 下一次再取 */ });
  }
  function threadLoop() { fetchThreads().then(function () { setTimeout(threadLoop, THREADS_MS); }); }
  function loop() { poll().then(function () { setTimeout(loop, POLL_MS); }); }

  window.SuperAgentNav = {
    select: function (id, newMode) { selected = id || null; mode = newMode; drawList(); drawMarks(); },
    refreshThreads: fetchThreads,
  };

  drawMarks();
  if (project) { loop(); threadLoop(); }
})();
