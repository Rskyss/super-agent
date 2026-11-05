/* 右边流转面板：每 1.5 秒向本机数据服务要一次最新状态，并把计时、“几秒前有动静”在两次之间走起来。
   只读：这里没有任何会改动项目的操作。 */
(function () {
  "use strict";
  var ROW = 68, POLL_MS = 1500;
  var params = new URLSearchParams(location.search);
  var project = params.get("project") || "";
  var dispatcher = params.get("dispatcher") || "";  // 桌面窗口告诉面板：用户选的调度员是谁
  var selected = null;          // 手动选中的卡片；null 表示跟随最新的一张
  var view = null, fetchedAt = 0, prevActive = -1, filterWho = null;
  var opened = {}, texts = {};

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function mmss(sec) { sec = Math.max(0, Math.round(sec)); return Math.floor(sec / 60) + ":" + (sec % 60 < 10 ? "0" : "") + (sec % 60); }
  function ago(sec) { sec = Math.max(0, Math.round(sec)); return sec < 60 ? sec + " 秒" : Math.floor(sec / 60) + " 分钟"; }
  function since() { return (Date.now() - fetchedAt) / 1000; }
  function finished(v) {
    return !v.card || ["待验收", "已合并", "需Claude介入"].indexOf(v.card.state) >= 0 ||
      (v.card.id === "调度中" && v.card.state !== "调度中");
  }

  // ---------- 流程：7 行先搭好，之后只改内容 ----------
  var flow = $("vflow"), rows = [];
  for (var i = 0; i < 7; i++) {
    var row = document.createElement("div");
    row.className = "row idle";
    row.innerHTML = '<button class="face" type="button"></button>' +
      '<div><div class="t1"></div><div class="t2"><i></i><span></span></div></div>';
    (function (idx) {
      row.querySelector("button").addEventListener("click", function () {
        var s = view && view.stations[idx];
        if (!s) return;
        showLog(s.key === "check" || s.key === "you" || s.key === "accept" ? null : s.who);
      });
    })(i);
    flow.appendChild(row);
    rows.push(row);
  }

  function rowText(s) {
    var parts = [s.detail || ""];
    if (s.state === "active" && s.elapsed != null) parts.push("已干 " + mmss(s.elapsed + since()));
    if (s.state === "active" && s.quiet != null) parts.push(ago(s.quiet + since()) + "前有动静");
    return parts.filter(Boolean).join(" · ");
  }

  function animateToken(from, to) {
    var token = $("vtoken"), start = performance.now(), dur = 900;
    token.className = "vtoken on" + (to < from ? " red" : "");
    function step(t) {
      var k = Math.min(1, (t - start) / dur);
      var pos = from + (to - from) * (1 - Math.pow(1 - k, 3));
      token.style.top = (ROW / 2 + pos * ROW) + "px";
      if (k < 1) requestAnimationFrame(step); else token.className = "vtoken";
    }
    requestAnimationFrame(step);
  }

  function renderFlow(v) {
    var reach = 0, active = -1;
    v.stations.forEach(function (s, idx) {
      var r = rows[idx];
      var cls = "row " + s.state + (s.state === "active" && s.live ? " live" : "") +
        (s.state === "active" && v.stale ? " stale" : "");
      if (r.className !== cls) r.className = cls;
      var face = r.querySelector("button");
      if (face.textContent !== s.face) face.textContent = s.face;
      face.setAttribute("aria-label", "看 " + s.who + " 的交接");
      r.querySelector(".t1").innerHTML = "<b>" + esc(s.who) + "</b>" + esc(s.role);
      r.querySelector(".t2 span").textContent = rowText(s);
      if (s.state !== "idle") reach = idx;
      if (active < 0 && (s.state === "active" || s.state === "wait")) active = idx;
    });
    $("vlit").style.height = (reach * ROW) + "px";
    $("vback").className = "vback" + (v.back ? " on" : "");
    if (prevActive >= 0 && active >= 0 && active !== prevActive) animateToken(prevActive, active);
    prevActive = active;
    var foot = "点头像，看这个人的全部交接。";
    if (!v.installed) foot = "这个项目还没装超级助手。在左边对调度员说“用协作包做……”，它会先帮你装好。";
    else if (!v.card) foot = "在左边对调度员说“用协作包做这个需求：……”，卡片建好后这里就会动起来。";
    else if (v.card.id === "调度中") foot = "调度员正在处理你的要求；切到「交接」可看每一步检查与回复。";
    $("flowFoot").textContent = foot;
  }

  // ---------- 顶部 ----------
  function renderHead(v) {
    $("project").textContent = v.project + (v.card ? " · " + v.card.id : "");
    $("title").textContent = v.card ? v.card.title : (v.installed ? "还没有需求卡" : "这个项目还没装超级助手");
    $("now").textContent = v.now.text;
    $("now").className = "now " + v.now.tone;
    $("round").textContent = v.card ? (v.card.id === "调度中"
      ? (v.card.state === "调度中" ? "调度员执行中" : "等你指示下一步")
      : "第 " + v.card.round + " 轮 · 返工 " + v.card.rework + " 次") : "";
    $("count").textContent = v.handoffs.length;
    $("cardCount").textContent = v.cards.length;
  }

  function tick() {
    if (!view) return;
    $("elapsed").textContent = mmss(view.elapsed + (finished(view) ? 0 : since()));
    view.stations.forEach(function (s, idx) {
      if (s.state === "active") rows[idx].querySelector(".t2 span").textContent = rowText(s);
    });
  }

  // ---------- 交接 ----------
  function drawList() {
    if (!view) return;
    var list = view.handoffs.filter(function (h) {
      return !filterWho || h.from.indexOf(filterWho) >= 0 || h.to.indexOf(filterWho) >= 0;
    });
    $("filter").innerHTML = filterWho
      ? "只看 " + esc(filterWho) + ' <button class="btn" type="button" id="clearFilter">看全部</button>'
      : "全部交接，最新的在最下面；点一条看全文";
    var clear = $("clearFilter");
    if (clear) clear.addEventListener("click", function () { showLog(null); });
    if (!list.length) { $("list").innerHTML = '<li class="empty">还没有交接记录</li>'; return; }
    var key = view.card ? view.card.id : "";
    $("list").innerHTML = list.map(function (h) {
      var id = key + "#" + h.seq, open = opened[id];
      var body = open ? "<pre>" + esc(texts[id] == null ? "读取中…" : texts[id]) + "</pre>" : "";
      return '<li><button type="button" data-seq="' + h.seq + '" aria-expanded="' + (!!open) + '">' +
        '<div class="m"><span>' + esc(h.time) + "</span>" + esc(h.from) + (h.to ? " → " + esc(h.to) : "") + "</div>" +
        '<div class="s ' + esc(h.tone) + '">' + esc(h.summary) + "</div>" + body + "</button></li>";
    }).join("");
  }
  $("list").addEventListener("click", function (ev) {
    var b = ev.target.closest("button[data-seq]");
    if (!b || !view || !view.card) return;
    var seq = b.getAttribute("data-seq"), id = view.card.id + "#" + seq;
    opened[id] = !opened[id];
    if (opened[id] && texts[id] == null) {
      fetch("/api/text?project=" + encodeURIComponent(project) + "&card=" + encodeURIComponent(view.card.id) + "&seq=" + seq)
        .then(function (r) { return r.json(); })
        .then(function (d) { texts[id] = d.text != null ? d.text : (d.error || "读不到全文"); drawList(); })
        .catch(function () { texts[id] = "读不到全文"; drawList(); });
    }
    drawList();
    var again = $("list").querySelector('button[data-seq="' + seq + '"]');
    if (again) again.focus();
  });

  // ---------- 卡片 ----------
  function drawCards() {
    if (!view) return;
    if (!view.cards.length) { $("cards").innerHTML = '<div class="empty">还没有卡片</div>'; return; }
    var current = view.card ? view.card.id : "";
    $("cards").innerHTML = (selected ? '<button class="btn" type="button" id="follow">回到最新的一张</button>' : "") +
      view.cards.map(function (c) {
        var cls = ["待验收", "已合并"].indexOf(c.state) >= 0 ? "fin" : (c.state === "需Claude介入" ? "bad" : "run");
        return '<button class="cardi' + (c.id === current ? " current" : "") + '" type="button" data-id="' + esc(c.id) + '">' +
          '<span class="id">' + esc(c.id) + '</span><span class="nm">' + esc(c.title) + "</span>" +
          '<span class="st ' + cls + '">' + esc(c.state) + " · 返工 " + c.rework + " 次</span></button>";
      }).join("");
    var follow = $("follow");
    if (follow) follow.addEventListener("click", function () { selected = null; prevActive = -1; select("flow"); poll(); });
  }
  $("cards").addEventListener("click", function (ev) {
    var b = ev.target.closest("button[data-id]");
    if (!b) return;
    selected = b.getAttribute("data-id");
    prevActive = -1; filterWho = null;
    select("flow"); poll();
  });

  // ---------- 标签页 ----------
  var tabs = ["flow", "log", "cards"];
  function select(name) {
    tabs.forEach(function (t) {
      $("tab-" + t).setAttribute("aria-selected", String(t === name));
      $("pane-" + t).hidden = t !== name;
    });
    if (name === "log") drawList();
    if (name === "cards") drawCards();
  }
  function showLog(who) { filterWho = who; select("log"); }
  tabs.forEach(function (t) {
    $("tab-" + t).addEventListener("click", function () { if (t === "log") showLog(null); else select(t); });
  });

  // ---------- 取数据 ----------
  var lastLogKey = "", lastCardsKey = "";
  function poll() {
    var url = "/api/view?project=" + encodeURIComponent(project) + "&dispatcher=" + encodeURIComponent(dispatcher) +
      (selected ? "&card=" + encodeURIComponent(selected) : "");
    return fetch(url).then(function (r) { return r.json(); }).then(function (v) {
      $("offline").hidden = true;
      if (v.error) { $("title").textContent = v.error; return; }
      view = v; fetchedAt = Date.now();
      renderHead(v); renderFlow(v); tick();
      var logKey = (v.card ? v.card.id : "") + ":" + v.handoffs.length;
      if (!$("pane-log").hidden && logKey !== lastLogKey) drawList();
      var cardsKey = JSON.stringify(v.cards.map(function (c) { return [c.id, c.state, c.rework]; })) + (v.card ? v.card.id : "") + selected;
      if (!$("pane-cards").hidden && cardsKey !== lastCardsKey) drawCards();
      lastLogKey = logKey; lastCardsKey = cardsKey;
    }).catch(function () { $("offline").hidden = false; });
  }
  function loop() { poll().then(function () { setTimeout(loop, POLL_MS); }); }

  if (!project) {
    $("title").textContent = "没有指定项目";
    $("now").textContent = "用 ?project=项目文件夹 打开这个页面";
    $("now").className = "now idle";
  } else {
    loop();
    setInterval(tick, 500);
  }
})();
