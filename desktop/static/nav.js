/* 左栏：“主对话 | 等待处理”两个标签。点标签只换列表，点列表里的一项才切换中间。每 1.5 秒取一次卡片，点击用 superagent:// 地址告诉窗口；
   窗口用 window.SuperAgentNav.select(卡片编号, 模式) 告诉这里高亮谁。只改列表：删对话、归档卡片，不改动项目。 */
"use strict";

/* 规则层：不碰页面，tests/nav_page_check.js 用 node 直接检查。 */
var NavCore = {
  dotFor: function (card) {
    var ph = card && typeof card === "object" && card.phase;
    if (ph && ph.tone && ph.label) return { tone: ph.tone, label: ph.label };
    var state = (card && card.state) || "";
    return { tone: "done", label: state };
  },
  emptyText: function (cards, filtered) {
    if (!(cards || []).length) return "还没有卡片";
    if (!(filtered || []).length) return "没有找到";
    return "";
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
  archivedCards: function (cards) {
    return (cards || []).filter(function (c) { return c && c.archived; });
  },
  // 等待处理标签里的三组：等你处理、进行中、已完成。空的组不显示；已归档另放
  cardGroups: function (cards) {
    cards = (cards || []).filter(function (c) { return !c.archived; });
    var need = NavCore.needsYou(cards), rest = cards.filter(function (c) { return need.indexOf(c) < 0; });
    var isDone = function (c) { return NavCore.dotFor(c).tone === "done"; };
    return [
      { title: "等你处理", cards: need },
      { title: "进行中", cards: rest.filter(function (c) { return !isDone(c); }) },
      { title: "已完成", cards: rest.filter(isDone) },
    ].filter(function (g) { return g.cards.length; });
  },
  canArchive: function (card) {
    if (!card || !card.phase || card.archived) return false;
    return card.phase.tone === "done" && !card.phase.needs_you;
  },
  showArchived: function (open, text) {
    return !!open || !!String(text || "").trim();
  },
  startTab: function (mode) { return mode === "card" ? "cards" : "main"; },
  filterThreads: function (threads, text) {
    var q = String(text || "").trim().toLowerCase();
    if (!q) return threads;
    return threads.filter(function (t) { return NavCore.threadTitle(t.title).toLowerCase().indexOf(q) >= 0; });
  },
  // 没有历史对话时（如只能用终端的调度员），看卡片或设置也要有路回去
  needsBackRow: function (count, mode, canReturn) { return !count && mode !== "main" && !!canReturn; },
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
  canDelete: function (thread, cached) {
    if (cached || !thread || thread.deletable === false) return false;
    return !thread.busy;
  },
  deleteConfirm: function (thread, clerk) {
    var words = "删除这段对话？删除后找不回来，已提出的任务不受影响。";
    if (!clerk) words += "Codex 里的这段记录也会一起删除。";
    if (thread && thread.pending) words += "里面还有一个没确认的提议，会一起作废。";
    return words;
  },
  // 一条取列表的回答变成要显示的列表；没带列表返回 null，表示不动
  threadsFromAnswer: function (answer, deleted, pending) {
    if (!answer.threads) return null;
    var list = NavCore.withoutDeleted(answer.threads, deleted);
    return pending && answer.current !== pending ? NavCore.markCurrent(list, pending) : list;
  },
  // 同一件事进行中不再做一次；做完（成功失败都算）放开。hold 是做完后再挡多久，防连点落到顶上来的下一行
  oneAtATime: function (locks, key, work, hold) {
    if (locks[key]) return null;
    locks[key] = true;
    var free = function () { if (hold) setTimeout(function () { delete locks[key]; }, hold); else delete locks[key]; };
    return work().then(function (value) { free(); return value; }, function (err) { free(); throw err; });
  },
  withoutDeleted: function (threads, deleted) {
    deleted = deleted || {};
    return (threads || []).filter(function (t) { return !deleted[t.id]; });
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
  var cards = [], threads = [], showAllThreads = false, showArchivedOpen = false, pendingThread = null, lastKey = "", threadKey = "";
  var listCached = false, deleted = {}, locks = {};
  var tab = NavCore.startTab(mode);
  var TRASH = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"></path><path d="M8 6V4h8v2"></path><path d="M19 6l-1 14H6L5 6"></path><path d="M10 11v6M14 11v6"></path></svg>';
  var BOX = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 8v13H3V8"></path><path d="M23 3H1v5h22V3z"></path><path d="M10 12h4"></path></svg>';

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function tell(address) { location.href = address; }
  function chatApi(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Chat-Token": encodeURIComponent(token) },
      body: JSON.stringify(Object.assign({ project: project }, body || {}))
    }).catch(function () { throw new Error("没连上本机服务，请稍后再试"); }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.error || "出错了，请稍后再试");
        return d;
      });
    });
  }

  function cardRow(c) {
    var dot = NavCore.dotFor(c), on = mode === "card" && c.id === selected;
    var sub = dot.label + (c.rework ? " · 返工 " + c.rework + " 次" : "");
    var row = '<div class="row"><button class="item' + (on ? " on" : "") + '" type="button" data-id="' + esc(c.id) + '" title="' + esc(c.id) + '">' +
      '<span class="lead"><i class="dot ' + dot.tone + '"></i></span><span class="tt">' + esc(c.title) + '</span><span class="sub">' + esc(sub) + "</span></button>";
    if (NavCore.canArchive(c)) {
      row += '<button class="rowact" type="button" data-archive="' + esc(c.id) + '" aria-label="归档" title="归档">' + BOX + "</button>";
    } else if (c.archived) {
      row += '<button class="rowact" type="button" data-unarchive="' + esc(c.id) + '" aria-label="取消归档" title="取消归档">' + BOX + "</button>";
    }
    return row + "</div>";
  }
  function drawList() {
    var need = NavCore.needsYou(cards).length;
    $("needCount").hidden = !need;
    $("needCount").textContent = need;
    $("tabCards").setAttribute("aria-label", need ? "等待处理，" + need + " 张等你处理" : "等待处理");
    var q = tab === "cards" ? $("q").value : "";
    var filtered = NavCore.filter(cards, q);
    var empty = NavCore.emptyText(cards, filtered);
    if (empty) { $("cards").innerHTML = '<div class="empty">' + empty + "</div>"; return; }
    var html = NavCore.cardGroups(filtered).map(function (g) {
      return '<div class="gh"><span>' + g.title + "</span><span>" + g.cards.length + "</span></div>" + g.cards.map(cardRow).join("");
    }).join("");
    var archived = NavCore.archivedCards(filtered);
    if (archived.length) {
      if (NavCore.showArchived(showArchivedOpen, q)) {
        var count = "<span>已归档</span><span>" + archived.length + "</span>";
        html += (q.trim() ? '<div class="gh">' + count + "</div>"   // 搜索时总是展开，不做成能点的
          : '<button class="gh fold" id="toggleArchived" type="button" title="收起" aria-expanded="true">' + count + "</button>") + archived.map(cardRow).join("");
      } else {
        html += '<button class="more" id="toggleArchived" type="button" aria-expanded="false">已归档 ' + archived.length + "</button>";
      }
    }
    $("cards").innerHTML = html;
  }
  function drawThreads() {
    if (NavCore.needsBackRow(threads.length, mode, clerkMode || !!dispatcher)) {
      $("threads").innerHTML = '<button class="thread" type="button" id="backToChat"><span class="tt">回到对话</span></button>';
      threadKey = "back";
      return;
    }
    var q = tab === "main" ? $("q").value.trim() : "";
    var found = NavCore.filterThreads(threads, q);
    var shown = q ? found : NavCore.visibleThreads(found, showAllThreads);
    var key = JSON.stringify({
      q: q, all: showAllThreads, mode: mode, cached: listCached, day: new Date().toDateString(),   // 过了半夜“今天”要变“昨天”
      rows: shown.map(function (t) { return [t.id, t.title, t.updated, !!t.busy, !!t.pending, !!t.current, t.deletable]; })
    });
    if (key === threadKey) return;
    threadKey = key;
    if (q && !found.length) { $("threads").innerHTML = '<div class="empty">没有找到</div>'; return; }
    $("threads").innerHTML = shown.map(function (t) {
      var on = t.current && mode === "main";
      var row = '<div class="row"><button class="thread' + (on ? " on" : "") + '" type="button" data-thread="' + esc(t.id) + '" data-current="' + (t.current ? 1 : 0) + '">' +
        '<span class="tt">' + esc(NavCore.threadTitle(t.title)) + '</span><span class="tm">' + esc(NavCore.timeLabel(t.updated)) + "</span></button>";
      if (NavCore.canDelete(t, listCached)) {
        row += '<button class="rowact" type="button" data-delete="' + esc(t.id) + '" aria-label="删除这段对话" title="删除这段对话">' + TRASH + "</button>";
      }
      return row + "</div>";
    }).join("") + (found.length > shown.length ? '<button class="more" id="moreThreads" type="button">全部 ' + found.length + " 段</button>" : "");
  }
  function drawTabs() {
    var onCards = tab === "cards";
    $("tabMain").setAttribute("aria-selected", onCards ? "false" : "true");
    $("tabCards").setAttribute("aria-selected", onCards ? "true" : "false");
    $("tabMain").tabIndex = onCards ? -1 : 0;
    $("tabCards").tabIndex = onCards ? 0 : -1;
    $("paneMain").hidden = onCards;
    $("paneCards").hidden = !onCards;
    var hint = onCards ? "搜索卡片" : "搜索对话";
    $("q").placeholder = hint;
    $("q").setAttribute("aria-label", hint);
  }
  // 点标签只换左栏列表，中间内容不变
  function setTab(next) {
    if (next === tab) return;
    tab = next;
    $("q").value = "";
    $("scroll").scrollTop = 0;
    drawTabs(); drawList(); drawThreads();
  }
  function drawMarks() {
    $("gear").classList.toggle("on", mode === "settings");
    drawThreads();
  }

  $("tabMain").addEventListener("click", function () { setTab("main"); });
  $("tabCards").addEventListener("click", function () { setTab("cards"); });
  $("tabMain").parentNode.addEventListener("keydown", function (ev) {
    if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
    ev.preventDefault();
    setTab(ev.key === "ArrowRight" ? "cards" : "main");
    $(tab === "cards" ? "tabCards" : "tabMain").focus();
  });
  $("cards").addEventListener("click", function (ev) {
    if (ev.target.closest("#toggleArchived")) { showArchivedOpen = !showArchivedOpen; drawList(); $("toggleArchived").focus(); return; }
    var arch = ev.target.closest("button[data-archive], button[data-unarchive]");
    if (arch) {
      var on = arch.hasAttribute("data-archive");
      var cardId = arch.getAttribute(on ? "data-archive" : "data-unarchive");
      var doing = NavCore.oneAtATime(locks, "archive", function () {
        return chatApi("/api/clerk/card/archive", { card: cardId, archived: on }).then(poll);
      }, 400);
      if (doing) doing.then(function () {
        if (on && mode === "card" && selected === cardId) tell("superagent://main");
      }).catch(function (err) { window.alert(err.message || "出错了"); });
      return;
    }
    var b = ev.target.closest("button[data-id]");
    if (b) tell("superagent://card?id=" + encodeURIComponent(b.getAttribute("data-id")));
  });
  $("gear").addEventListener("click", function () { tell("superagent://settings"); });
  $("blocksBtn").addEventListener("click", function () { tell("superagent://blocks"); });
  $("newChat").addEventListener("click", function () {
    pendingThread = null; threads = NavCore.markCurrent(threads, null); drawMarks();
    tell("superagent://new");
  });
  $("threads").addEventListener("click", function (ev) {
    if (ev.target.closest("#backToChat")) { tell("superagent://main"); return; }
    if (ev.target.closest("#moreThreads")) { showAllThreads = true; threadKey = ""; drawThreads(); return; }
    var del = ev.target.closest("button[data-delete]");
    if (del) {
      var gone = del.getAttribute("data-delete");
      if (locks[gone]) return;
      var thread = null;
      for (var i = 0; i < threads.length; i++) if (threads[i].id === gone) { thread = threads[i]; break; }
      thread = thread || { id: gone };
      if (!window.confirm(NavCore.deleteConfirm(thread, clerkMode))) return;
      var path = clerkMode ? "/api/clerk/delete" : "/api/chat/delete";
      var body = clerkMode ? { conversation: gone } : { thread_id: gone };
      NavCore.oneAtATime(locks, gone, function () { return chatApi(path, body); }).then(function () {
        deleted[gone] = true;
        threads = NavCore.withoutDeleted(threads, deleted);
        if (pendingThread === gone) pendingThread = null;
        threadKey = "";
        drawThreads();
        tell("superagent://thread-deleted?id=" + encodeURIComponent(gone));
      }).catch(function (err) { window.alert(err.message || "出错了"); });
      return;
    }
    var b = ev.target.closest("button[data-thread]");
    if (!b) return;
    // 马上标出选中，不等连接
    pendingThread = b.getAttribute("data-thread");
    threads = NavCore.markCurrent(threads, pendingThread);
    threadKey = "";
    drawMarks();
    tell("superagent://thread?id=" + encodeURIComponent(pendingThread));
  });
  $("q").addEventListener("input", function () { if (tab === "cards") drawList(); else drawThreads(); });

  function poll() {
    var url = "/api/view?project=" + encodeURIComponent(project) + "&dispatcher=" + encodeURIComponent(dispatcher);
    return fetch(url).then(function (r) { return r.json(); }).then(function (v) {
      if (v.error) return;
      $("proj").textContent = v.project;
      $("busy").hidden = !(v.card && v.card.id === "调度中" && v.card.state === "调度中");
      cards = v.cards;
      var key = JSON.stringify(cards.map(function (c) {
        return [c.id, c.title, c.state, c.rework, c.phase ? c.phase.code : "", c.archived];
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
        listCached = !!d.cached;
        if (listCached) drawThreads();   // 连接断了：删除图标要跟着消失。内容没变不会重画
        if (d.cached && threads.length) return;   // 存好的只用于刚打开，别盖掉当前选中
        // 切到终端、连接断了、取失败、取回来是空，服务都回记住的列表，不会把列表清掉
        var next = NavCore.threadsFromAnswer(d, deleted, pendingThread);
        if (!next) return;
        if (d.current === pendingThread) pendingThread = null;
        threads = next;
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
      drawList(); drawMarks();
    },
    refreshThreads: fetchThreads,
  };

  drawTabs(); drawMarks();
  if (project) { loop(); threadLoop(); }
})();
