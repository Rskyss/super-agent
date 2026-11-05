/* 团队配置页：选调度员和三位执行员 → 检测连通（后台逐个出结果）→ 全部通了才能保存 → 通知窗口切到对话。
   调度员没有默认值：必须用户亲手选。 */
(function () {
  "use strict";
  var params = new URLSearchParams(location.search);
  var project = params.get("project") || "", token = params.get("token") || "";
  var savedDispatcher = params.get("dispatcher") || "";   // 没接入的文件夹，调度员存在窗口自己的设置里
  var ROLE = { dispatcher: "调度员", tester: "写测试", developer: "写代码", reviewer: "复查" };
  var rows = {}, info = null, results = {}, timer = null, installed = true;
  document.querySelectorAll(".member").forEach(function (m) { rows[m.getAttribute("data-role")] = m; });
  function $(id) { return document.getElementById(id); }

  function post(path, body) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Chat-Token": encodeURIComponent(token) },
      body: JSON.stringify(Object.assign({ project: project }, body)) })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || "出错了"); return d; }); });
  }
  function get(path) {
    return fetch(path + "?" + new URLSearchParams({ project: project, token: token }).toString())
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || "出错了"); return d; }); });
  }

  function fill(role, options, value) {
    var sel = rows[role].querySelector("select");
    sel.innerHTML = "";
    if (role === "dispatcher") options = [{ value: "", label: "请选择…" }].concat(options, [{ value: "custom", label: "自定义命令…" }]);
    options.forEach(function (o) { var op = document.createElement("option"); op.value = o.value; op.textContent = o.label; sel.appendChild(op); });
    if (role === "dispatcher" && value && value.indexOf("custom:") === 0) {
      sel.value = "custom";
      var box = rows.dispatcher.querySelector(".custom");
      box.hidden = false; box.value = value.slice(7);
    } else {
      sel.value = value || "";
    }
    sel.addEventListener("change", function () {
      if (role === "dispatcher") rows.dispatcher.querySelector(".custom").hidden = sel.value !== "custom";
      reset();
    });
  }

  function team() {
    var disp = rows.dispatcher.querySelector("select").value;
    if (disp === "custom") {
      var cmd = rows.dispatcher.querySelector(".custom").value.trim();
      disp = cmd ? "custom:" + cmd : "";
    }
    return { dispatcher: disp, tester: rows.tester.querySelector("select").value,
             developer: rows.developer.querySelector("select").value, reviewer: rows.reviewer.querySelector("select").value };
  }
  function activeRoles() { return installed ? ["dispatcher", "tester", "developer", "reviewer"] : ["dispatcher"]; }

  function setState(role, cls, text, how, retry) {
    var s = rows[role].querySelector(".state");
    s.className = "state " + cls;
    s.innerHTML = '<span class="dot"></span><span class="txt"></span>';
    var txt = s.querySelector(".txt");
    txt.textContent = text;
    if (retry) {  // 放在文字里面跟着折行，原因长时不会被挤到单独一行
      var b = document.createElement("button");
      b.className = "retry"; b.type = "button"; b.textContent = "重试";
      b.addEventListener("click", function () { check([role]); });
      txt.appendChild(b);
    }
    if (how) { var h = document.createElement("span"); h.className = "how"; h.textContent = "怎么办：" + how; s.appendChild(h); }
  }

  function reset() {
    results = {};
    var t = team();
    activeRoles().forEach(function (r) { setState(r, "", r === "reviewer" && t.reviewer === "none" ? "不复查，不用检测" : "还没检测"); });
    $("save").disabled = true;
    $("summary").className = "summary";
    $("summary").textContent = "每人会在临时文件夹里试着建一个文件，大约半分钟到一分钟，会用掉一点点额度。";
    $("sameWarn").hidden = !(installed && t.developer === t.reviewer);
    var d = t.dispatcher;
    $("dispNote").textContent = !d ? "调度员必须你亲手选。"
      : d === "codex" ? "Codex 用聊天视图（一问一答、能看图）。"
      : (d.indexOf("custom:") === 0 ? "自定义调度员用终端视图，检测只能查“装没装”。" : "先用终端视图（聊天视图目前只接了 Codex）。");
  }

  function apply(r) {
    results[r.role] = r;
    var text = r.reason + (r.shared_with ? "（和" + ROLE[r.shared_with] + "同一家，只测一次）" : "");
    setState(r.role, r.ok ? "ok" : "bad", text, r.ok ? "" : r.how, !r.ok && r.status !== "unset");
  }

  function finish() {
    $("check").disabled = false;
    $("check").textContent = "重新检测";
    var roles = activeRoles(), bad = roles.filter(function (r) { return results[r] && !results[r].ok; });
    var allOk = roles.every(function (r) { return results[r] && results[r].ok; });
    if (allOk) { $("summary").className = "summary ok"; $("summary").textContent = "全部通了，可以开始。"; $("save").disabled = false; }
    else if (bad.length) { $("summary").className = "summary bad"; $("summary").textContent = bad.length + " 位没通，处理好后点重试，或换一家。"; }
  }

  function check(roles) {
    var t = team();
    if (!t.dispatcher) { $("summary").className = "summary bad"; $("summary").textContent = "先选一位调度员。"; return; }
    roles = roles || activeRoles();
    roles.forEach(function (r) {
      if (!(r === "reviewer" && t.reviewer === "none")) setState(r, "run", "检测中…在临时文件夹里试着建一个文件");
    });
    $("check").disabled = true; $("save").disabled = true;
    post("/api/team/check", { team: t, roles: roles }).then(function () {
      clearInterval(timer);
      timer = setInterval(function () {
        get("/api/team/check").then(function (d) {
          d.results.forEach(apply);
          if (d.done) { clearInterval(timer); finish(); }
        }).catch(function () { /* 下一次再取 */ });
      }, 700);
    }).catch(function (err) { $("check").disabled = false; $("summary").className = "summary bad"; $("summary").textContent = err.message; });
  }

  function done(dispatcher) {
    // 在超级助手窗口里，这个地址会被窗口接住：切到和调度员的对话
    location.href = "superagent://saved" + (dispatcher ? "?dispatcher=" + encodeURIComponent(dispatcher) : "");
    setTimeout(function () { $("summary").className = "summary ok"; $("summary").textContent = "已保存。"; }, 300);
  }

  $("check").addEventListener("click", function () { check(); });
  $("save").addEventListener("click", function () {
    var t = team();
    if (!installed) { done(t.dispatcher); return; }
    post("/api/team/save", { team: t }).then(function () { done(); })
      .catch(function (err) { $("summary").className = "summary bad"; $("summary").textContent = err.message; });
  });
  rows.dispatcher.querySelector(".custom").addEventListener("input", reset);

  if (!project || !token) {
    $("lead").textContent = "请从超级助手窗口打开团队配置。";
  } else {
    get("/api/team").then(function (d) {
      info = d;
      installed = d.installed;
      $("workers").hidden = !installed;
      $("noGit").hidden = installed;
      if (d.problem) { $("problem").hidden = false; $("problem").textContent = "原来的配置有问题，已换成默认值：" + d.problem; }
      fill("dispatcher", d.options.dispatcher, installed ? d.team.dispatcher : savedDispatcher);
      fill("tester", d.options.worker, d.team.tester);
      fill("developer", d.options.worker, d.team.developer);
      fill("reviewer", d.options.reviewer, d.team.reviewer);
      reset();
    }).catch(function (err) { $("lead").textContent = "读不到团队配置：" + err.message; });
  }
})();
