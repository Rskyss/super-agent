/* 左栏：主对话和卡片列表。每 1.5 秒取一次卡片，点击用 superagent:// 地址告诉窗口；
   窗口用 window.SuperAgentNav.select(卡片编号, 模式) 告诉这里高亮谁。只读，不改动项目。 */
"use strict";

/* 规则层：不碰页面，tests/nav_page_check.js 用 node 直接检查。 */
var NavCore = {
  dotFor: function (card) {
    var ph = card && typeof card === "object" && card.phase;
    if (ph && ph.tone && ph.label) return { tone: ph.tone, label: ph.label };
    var state = typeof card === "string" ? card : (card && card.state) || "";
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
  needsYou: function (cards) {
    return (cards || []).filter(function (c) {
      var ph = c && c.phase;
      return ph && ph.needs_you === true;
    });
  },
  // 任务卡片一个列表：要你处理的排最前，然后进行中，最后已完成
  ordered: function (cards) {
    var rank = { wait: 0, ok: 0, bad: 0, run: 1, done: 2 };
    return [0, 1, 2].reduce(function (all, level) {
      return all.concat(cards.filter(function (c) { return rank[NavCore.dotFor(c).tone] === level; }));
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
  var POLL_MS = 1500, THREADS_MS = 5000, WARM_MS = 500, WARM_TRIES = 30;
  var clerkMode = false;
  var waiting = true, warmTries = 0;   // 还在用存好的列表：连上前勤快点问，连上马上换成最新的
  var params = new URLSearchParams(location.search);
  clerkMode = params.get("clerk") === "1";   // 行政对话都在本机，刷新不花什么
  if (clerkMode) THREADS_MS = 1500;
  var project = params.get("project") || "", dispatcher = params.get("dispatcher") || "", token = params.get("token") || "";
  var selected = params.get("selected") || null, mode = params.get("mode") || "main";
  var cards = [], threads = [], showAllThreads = false, pendingThread = null, lastKey = "";
  var codexOldOpen = false, codexOldLoaded = false, codexThreads = [], codexCurrent = null;

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function tell(address) { location.href = address; }

  function drawNeedsYou() {
    var box = $("needsYou");
    if (!box) return;
    var filtered = NavCore.filter(cards, $("q").value);
    var ny = NavCore.needsYou(filtered);
    if (!ny.length) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    box.hidden = false;
    box.innerHTML =
      '<div class="gh"><span>等你处理</span><span>' + ny.length + "</span></div>" +
      ny
        .map(function (c) {
          var dot = NavCore.dotFor(c);
          var on = mode === "card" && c.id === selected;
          var sub = dot.label + (c.rework ? " · 返工 " + c.rework + " 次" : "");
          return (
            '<button class="item' +
            (on ? " on" : "") +
            '" type="button" data-id="' +
            esc(c.id) +
            '" title="' +
            esc(c.id) +
            '">' +
            '<span class="lead"><i class="dot ' +
            dot.tone +
            '"></i></span><span class="tt">' +
            esc(c.title) +
            '</span><span class="sub">' +
            esc(sub) +
            "</span></button>"
          );
        })
        .join("");
  }
  function drawList() {
    var filtered = NavCore.filter(cards, $("q").value);
    var needIds = {};
    NavCore.needsYou(filtered).forEach(function (c) {
      needIds[c.id] = true;
    });
    var list = filtered.filter(function (c) {
      return !needIds[c.id];
    });
    drawNeedsYou();
    var head = '<div class="gh"><span>任务卡片</span>' + (cards.length ? "<span>" + cards.length + "</span>" : "") + "</div>";
    if (!cards.length) { $("cards").innerHTML = head + '<div class="empty">还没有卡片</div>'; return; }
    if (!list.length) { $("cards").innerHTML = head + '<div class="empty">没有找到</div>'; return; }
    $("cards").innerHTML = head + NavCore.ordered(list).map(function (c) {
      var dot = NavCore.dotFor(c), on = mode === "card" && c.id === selected;
      var sub = dot.label + (c.rework ? " · 返工 " + c.rework + " 次" : "");
      return '<button class="item' + (on ? " on" : "") + '" type="button" data-id="' + esc(c.id) + '" title="' + esc(c.id) + '">' +
        '<span class="lead"><i class="dot ' + dot.tone + '"></i></span><span class="tt">' + esc(c.title) + '</span><span class="sub">' + esc(sub) + "</span></button>";
    }).join("");
  }
  function drawThreads() {
    var shown = NavCore.visibleThreads(threads, showAllThreads);
    $("threads").innerHTML = shown.map(function (t) {
      return '<button class="thread' + (t.current && mode === "main" && !codexCurrent ? " on" : "") + '" type="button" data-thread="' + esc(t.id) + '" data-current="' + (t.current ? 1 : 0) + '">' +
        '<span class="tt">' + esc(NavCore.threadTitle(t.title)) + '</span><span class="tm">' + esc(NavCore.timeLabel(t.updated)) + "</span></button>";
    }).join("") + (threads.length > shown.length ? '<button class="more" id="moreThreads" type="button">全部 ' + threads.length + " 段</button>" : "");
  }
  function drawCodexOld() {
    var box = $("codexOld"), list = $("codexOldList"), toggle = $("codexOldToggle");
    if (!clerkMode) { box.hidden = true; return; }
    box.hidden = false;
    toggle.setAttribute("aria-expanded", codexOldOpen ? "true" : "false");
    list.hidden = !codexOldOpen;
    if (!codexOldOpen) return;
    if (!codexOldLoaded) { list.innerHTML = '<div class="empty">正在读取…</div>'; return; }
    if (!codexThreads.length) { box.hidden = true; return; }
    list.innerHTML = codexThreads.map(function (t) {
      return '<button class="thread' + (codexCurrent === t.id ? " on" : "") + '" type="button" data-codex="' + esc(t.id) + '">' +
        '<span class="tt">' + esc(NavCore.threadTitle(t.title)) + '</span><span class="tm">' + esc(NavCore.timeLabel(t.updated)) + "</span></button>";
    }).join("");
  }
  function loadCodexOld() {
    if (!token) { codexOldLoaded = true; codexThreads = []; drawCodexOld(); return; }
    fetch("/api/clerk/codex/list?" + new URLSearchParams({ project: project, token: token }).toString())
      .then(function (r) { return r.json(); })
      .then(function (d) {
        codexThreads = d.threads || [];
        codexOldLoaded = true;
        if (!codexThreads.length) codexOldOpen = false;
        drawCodexOld();
      })
      .catch(function () { codexThreads = []; codexOldLoaded = true; drawCodexOld(); });
  }
  function drawMarks() {
    $("main").classList.toggle("on", mode === "main" && !codexCurrent);
    $("gear").classList.toggle("on", mode === "settings");
    drawThreads();
    drawCodexOld();
  }

  $("cards").addEventListener("click", function (ev) {
    var b = ev.target.closest("button[data-id]");
    if (b) tell("superagent://card?id=" + encodeURIComponent(b.getAttribute("data-id")));
  });
  $("needsYou").addEventListener("click", function (ev) {
    var b = ev.target.closest("button[data-id]");
    if (b) tell("superagent://card?id=" + encodeURIComponent(b.getAttribute("data-id")));
  });
  $("main").addEventListener("click", function () {
    codexCurrent = null; drawMarks();
    tell("superagent://main");
  });
  $("gear").addEventListener("click", function () { tell("superagent://settings"); });
  $("newChat").addEventListener("click", function () {
    pendingThread = null; codexCurrent = null; threads = NavCore.markCurrent(threads, null); drawMarks();
    tell("superagent://new");
  });
  $("threads").addEventListener("click", function (ev) {
    if (ev.target.closest("#moreThreads")) { showAllThreads = true; drawThreads(); return; }
    var b = ev.target.closest("button[data-thread]");
    if (!b) return;
    // 马上标出选中，不等连接
    pendingThread = b.getAttribute("data-thread");
    codexCurrent = null;
    threads = NavCore.markCurrent(threads, pendingThread);
    drawMarks();
    tell("superagent://thread?id=" + encodeURIComponent(pendingThread));
  });
  $("codexOldToggle").addEventListener("click", function () {
    codexOldOpen = !codexOldOpen;
    drawCodexOld();
    if (codexOldOpen && !codexOldLoaded) loadCodexOld();
  });
  $("codexOldList").addEventListener("click", function (ev) {
    var b = ev.target.closest("button[data-codex]");
    if (!b) return;
    codexCurrent = b.getAttribute("data-codex");
    pendingThread = null;
    threads = NavCore.markCurrent(threads, null);
    drawMarks();
    tell("superagent://codex?id=" + encodeURIComponent(codexCurrent));
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
      var key = JSON.stringify(cards.map(function (c) {
        return [c.id, c.title, c.state, c.rework, c.phase ? c.phase.code : ""];
      }));
      if (key !== lastKey) { lastKey = key; drawList(); }   // 没变化不重画：不打断滚动
      if (selected && mode === "card" && NavCore.keepSelected(selected, cards) === null) tell("superagent://main");
    }).catch(function () { /* 下一次再取 */ });
  }
  function fetchThreads() {
    if (!token || params.get("chat") !== "1") { waiting = false; return Promise.resolve(); }  // 只能用终端的调度员没有这个列表
    var feed = clerkMode ? "/api/clerk/list?" : "/api/chat/threads?";
    return fetch(feed + new URLSearchParams({ project: project, token: token }).toString())
      .then(function (r) { return r.json(); })
      .then(function (d) {
        waiting = !!d.cached;
        if (d.cached && threads.length) return;   // 存好的只用于刚打开，别盖掉当前选中
        // 切到终端时对话让出去了，列表保留上一次的
        if (!d.threads || !d.threads.length) return;
        if (d.current === pendingThread) pendingThread = null;
        threads = pendingThread ? NavCore.markCurrent(d.threads, pendingThread) : d.threads;
        drawThreads();
      }).catch(function () { /* 下一次再取 */ });
  }
  function threadLoop() {
    fetchThreads().then(function () { setTimeout(threadLoop, waiting && warmTries++ < WARM_TRIES ? WARM_MS : THREADS_MS); });
  }
  function loop() { poll().then(function () { setTimeout(loop, POLL_MS); }); }

  window.SuperAgentNav = {
    select: function (id, newMode) {
      selected = id || null; mode = newMode;
      if (newMode !== "main") codexCurrent = null;
      drawList(); drawMarks();
    },
    refreshThreads: fetchThreads,
  };

  drawMarks();
  if (project) { loop(); threadLoop(); }
})();
