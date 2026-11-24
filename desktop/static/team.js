/* 团队与模型配置页：行政 + 三位执行员（写测试、写代码、复查）+ 各家工具具体使用的模型 → 每行各自“检测生效”或直接保存 → 通知窗口切到对话。
   （11-17 起写方案、备交接资料是后台作业，不占团队里的一行。） */
"use strict";

/* 规则层：不碰页面，tests/team_page_check.js 用 node 直接检查。 */
var TeamCore = {
  // 选“不复查”的那行不用测
  needsCheck: function (team, role) { return !(role === "reviewer" && team.reviewer === "none"); },
  // 这一行和哪些行一起测：同一家工具、同样的模型和强度（每个岗位各选各的，流程修理 ①）；不给 picks 时按同一家算
  rolesSharingTool: function (team, role, active, picks) {
    if (!TeamCore.needsCheck(team, role)) return [];
    var key = TeamCore.pickKey(team, role, picks);
    return active.filter(function (r) {
      return team[r] === team[role] && TeamCore.needsCheck(team, r) && TeamCore.pickKey(team, r, picks) === key;
    });
  },
  // agy 拆出思考强度框（11-14）：编号结尾是强度的几档并成一个模型，存的仍是完整编号
  EFFORT_ORDER: ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
  splitEfforts: function (opts) {
    var groups = [], byBase = {};
    (opts || []).forEach(function (o) {
      var m = /^(.*)-(none|minimal|low|medium|high|xhigh|max)$/.exec(o.value || "");
      if (!m) { groups.push({ base: o.value, label: o.label, efforts: [] }); return; }
      var g = byBase[m[1]];
      if (!g) {
        g = byBase[m[1]] = { base: m[1], label: String(o.label || m[1]).replace(/\s*[（(][^）)]*[）)]\s*$/, "") || m[1], efforts: [] };
        groups.push(g);
      }
      g.efforts.push({ effort: m[2], value: o.value });
    });
    groups.forEach(function (g) {
      g.efforts.sort(function (a, b) { return TeamCore.EFFORT_ORDER.indexOf(a.effort) - TeamCore.EFFORT_ORDER.indexOf(b.effort); });
    });
    return groups;
  },
  findEffort: function (groups, value) {
    for (var i = 0; i < groups.length; i++) {
      var e = groups[i].efforts.find(function (x) { return x.value === value; });
      if (e) return { base: groups[i].base, effort: e.effort };
    }
    return null;
  },
  // 换模型时尽量保持原档位，没有就选最接近的（一样近选高的）；原来没档位按“高”算
  nearestEffort: function (group, effort) {
    var order = TeamCore.EFFORT_ORDER, want = order.indexOf(effort || "high");
    if (want < 0) want = order.indexOf("high");
    var best = null;
    group.efforts.forEach(function (e) {
      var d = Math.abs(order.indexOf(e.effort) - want);
      if (!best || d <= best.d) best = { d: d, e: e };
    });
    return best ? best.e : null;
  },
  pickKey: function (team, role, picks) {
    var p = picks && picks[role];
    if (!p || p.tool !== team[role]) return String(team[role]);
    return [p.tool, p.model || "", p.effort || ""].join("|");
  },
  // 没保存的改动该恢复哪几行：用户真改过（now 和 base 不同），并且之后没人动过这一行（现在存着的还等于 base）。
  // 不能整份恢复：期间行政帮忙改过、保存过的行会被盖回去（复查第 1 轮）
  draftRows: function (saved, draft) {
    var out = {};
    if (!draft || !draft.base || !draft.now || !draft.now.team) return out;
    var b = draft.base, n = draft.now, s = saved || {};
    ["tester", "developer", "reviewer"].forEach(function (r) {
      function row(x) { return JSON.stringify([x && x.team && x.team[r], (x && x.picks && x.picks[r]) || null]); }
      if (!(r in n.team)) return;
      if (row(n) !== row(b) && row(s) === row(b)) out[r] = { tool: n.team[r], pick: (n.picks && n.picks[r]) || null };
    });
    return out;
  },
  // 团队有改动却存不了时，说清是谁还没测通（68b 前用户点保存，设置悄悄丢了，②）
  unsavedReason: function (s) {
    var names = { tester: "写测试", developer: "写代码", reviewer: "复查" };
    var need = s.active.filter(function (r) { return TeamCore.needsCheck(s.team, r); });
    var bad = need.filter(function (r) { return s.results[r] && !s.results[r].ok; });
    if (bad.length) return bad.map(function (r) { return names[r]; }).join("、") + "没测通";
    var unchecked = need.filter(function (r) { return !(s.results[r] && s.results[r].ok); });
    if (!s.configured && unchecked.length) return unchecked.map(function (r) { return names[r]; }).join("、") + "还没检测";
    return "";
  },
  // 行政保存成功后下一步：save-team 存团队并回对话；leave 不存团队直接回对话；team-unsaved 团队配过又改了却没测通，照实说
  // （没接入的文件夹没有执行员可存，直接回对话；团队没配过，只用行政也能回对话）
  afterClerkSaved: function (s) {
    if (s.canSaveTeam) return "save-team";
    if (!s.installed || !s.configured) return "leave";
    return "team-unsaved";
  },
  // 行政是对话的入口，必配：没配好不放人离开设置页（11-17 起没有退回去和别人聊的路）；返回空串表示可以走
  leaveBlockedReason: function (s) {
    return s.clerkReady ? "" : "还差行政：先在上面配好行政（填密钥、检测通过），才能回到对话。";
  },
  // 第一次配置要全员测通；已配置的项目没测的不拦，只拦标红的
  canSave: function (s) {
    if (s.busy || !s.active.length) return false;   // 检测中、或没有执行员可存（没接入的文件夹）都不能存团队
    var need = s.active.filter(function (r) { return TeamCore.needsCheck(s.team, r); });
    if (need.some(function (r) { return s.results[r] && !s.results[r].ok; })) return false;
    return s.configured || need.every(function (r) { return s.results[r] && s.results[r].ok; });
  },
  TOOL_COMMANDS: { codex: "codex", cursor: "agent", agy: "agy", qwen: "codex", tencent: "codex" },
  commandOf: function (tool) { return TeamCore.TOOL_COMMANDS[tool] || ""; },
  normalizeUpgrades: function (items, legacyMessages) {
    if (Array.isArray(items) && items.length) {
      return items.filter(function (it) { return it && typeof it === "object"; }).map(function (it) {
        return {
          command: String(it.command || ""),
          name: String(it.name || ""),
          old: String(it.old || ""),
          now: String(it.now || ""),
          tools: Array.isArray(it.tools) ? it.tools.map(String) : [],
          message: String(it.message || ""),
        };
      }).filter(function (it) { return !!(it.command || it.message); });
    }
    if (Array.isArray(legacyMessages) && legacyMessages.length) {
      return legacyMessages.filter(Boolean).map(function (m) {
        return { command: "", name: "", old: "", now: "", tools: [], message: String(m) };
      });
    }
    return [];
  },
  upgradeAffectedRoles: function (item, team, active) {
    var t = team || {};
    return (active || []).filter(function (r) {
      if (!TeamCore.needsCheck(t, r)) return false;
      if (item && Array.isArray(item.tools) && item.tools.length) {
        return item.tools.indexOf(t[r]) !== -1;
      }
      if (item && item.command) {
        return TeamCore.commandOf(t[r]) === item.command;
      }
      return true;
    });
  },
  upgradeState: function (s) {
    var nextResolved = Object.assign({}, (s && s.resolved) || {});
    var t = (s && s.team) || {};
    var active = (s && s.active) || [];
    var res = (s && s.results) || {};
    var pend = (s && s.pending) || {};
    var visible = [];
    var unpassedRoles = [];
    var toCheckRoles = [];
    var seenUnpassed = {};
    var seenToCheck = {};
    ((s && s.items) || []).forEach(function (item) {
      var key = (item && (item.command || item.message)) || "";
      if (!key || nextResolved[key]) return;
      var roles = TeamCore.upgradeAffectedRoles(item, t, active);
      if (!roles.length) return;
      var remaining = roles.filter(function (r) { return !(res[r] && res[r].ok); });
      if (!remaining.length) {
        nextResolved[key] = true;
        return;
      }
      visible.push({ item: item, affected: roles, remaining: remaining });
      remaining.forEach(function (r) {
        if (!seenUnpassed[r]) {
          seenUnpassed[r] = true;
          unpassedRoles.push(r);
        }
        if (!pend[r] && !seenToCheck[r]) {
          seenToCheck[r] = true;
          toCheckRoles.push(r);
        }
      });
    });
    return {
      resolved: nextResolved,
      visible: visible,
      unpassedRoles: unpassedRoles,
      toCheckRoles: toCheckRoles,
      checking: unpassedRoles.length > 0 && toCheckRoles.length === 0,
    };
  },
  upgradeLineText: function (entry, roleNames) {
    var names = roleNames || { tester: "写测试", developer: "写代码", reviewer: "复查" };
    var item = (entry && entry.item) || {};
    var affected = (entry && entry.affected) || [];
    var remaining = (entry && entry.remaining) || [];
    if (item.name && item.old && item.now) {
      var head = item.name + " 升级了（" + item.old + " → " + item.now + "）";
      if (remaining.length && remaining.length < affected.length) {
        return head + "，还差 " + remaining.map(function (r) { return names[r] || r; }).join("、") + " 未重新检测，确认它和你选的模型还接得上";
      }
      if (affected.length) {
        return head + "，受影响岗位：" + affected.map(function (r) { return names[r] || r; }).join("、") + "，建议重新检测确认还接得上";
      }
      return head + "，建议重新检测确认还接得上";
    }
    return item.message || "";
  },
};
if (typeof module !== "undefined") module.exports = TeamCore;

if (typeof document !== "undefined") (function () {
  var params = new URLSearchParams(location.search);
  var project = params.get("project") || "", token = params.get("token") || "";
  var clerkReady = params.get("clerk") === "1";
  var ROLE = { tester: "写测试", developer: "写代码", reviewer: "复查" };
  var ALL_ROLES = ["tester", "developer", "reviewer"];
  var rows = {}, info = null, results = {}, installed = true;
  var pending = {}, runSeq = 0;   // pending：岗位 -> 正在等第几次检测的结果；改了这一行就不再等
  var currentModels = { codex: "gpt-6.1-sol", codex_effort: "xhigh", cursor: "auto", agy: "gemini-4-argon-high", qwen: "deepseek-v4.1-flash", tencent: "hy4" };  // 每家工具的整体设置（岗位没单独选时用它）
  var picks = {};          // 岗位 -> {tool, model, effort}：每个岗位各选各的（流程修理 ①）
  var savedSnapshot = "";  // 打开页面时存着的团队和岗位模型：用来判断有没有没保存的改动
  var checkTimeout = 120;
  var modelOptions = { codex: [], codex_effort: [], cursor: [], agy: [], qwen: [], tencent: [] };
  var providers = [];
  var clerkBaseline = "";
  var clerkWaiting = false;
  var clerkStopped = false;
  var clerkSent = "";       // 正在保存的那份选择：保存成功后以它为准，不以输入框当前内容为准
  var clerkCheckFor = "";   // 正在检测的那份选择：改了就不再要这次的结果
  var upgradeItems = [], resolvedUpgrades = {};
  document.querySelectorAll(".member").forEach(function (m) {
    var role = m.getAttribute("data-role");
    if (role && role !== "clerk") rows[role] = m;
  });
  function $(id) { return document.getElementById(id); }

  function clerkChoice() {
    var provider = $("clerkProvider").value || "tencent";
    var item = providers.find(function (p) { return p.id === provider; });
    var base = provider === "other"
      ? ($("clerkAddress").value || "").trim()
      : ((item && item.base_url) || "");
    return { provider: provider, base_url: base, model: ($("clerkModel").value || "").trim() };
  }
  function clerkSnapshot(c) {
    return [c.provider, c.base_url, c.model].join("\n");
  }
  // 保存期间锁住这一行：否则删密钥、停用、改模型都会被测试回来的结果盖掉
  function lockClerkRow(on) {
    ["clerkProvider", "clerkModel", "clerkAddress", "clerkCheck", "clerkKey", "clerkClear", "clerkForget"].forEach(function (id) {
      $(id).disabled = on;
    });
  }
  function clerkQuery(c) {
    return "project=" + encodeURIComponent(project)
      + "&provider=" + encodeURIComponent(c.provider)
      + "&base_url=" + encodeURIComponent(c.base_url)
      + "&model=" + encodeURIComponent(c.model);
  }
  function fillClerkModels(providerId) {
    var list = $("clerkModels");
    list.innerHTML = "";
    var item = providers.find(function (p) { return p.id === providerId; });
    (item && item.models || []).forEach(function (name) {
      var op = document.createElement("option");
      op.value = name;
      list.appendChild(op);
    });
  }
  function syncClerkExtras() {
    var other = $("clerkProvider").value === "other";
    $("clerkAddress").hidden = !other;
    $("clerkCost").hidden = !other;
    fillClerkModels($("clerkProvider").value);
  }
  function setClerkState(cls, text) {
    var box = $("clerkStateBox");
    box.className = "state " + (cls || "");
    $("clerkState").textContent = text;
  }
  function paintClerk(saved) {
    // 没设过的项目沿用最近一次：页面必须显示实际在用的那一家
    var params = new URLSearchParams(location.search);
    var inherited = !saved && params.get("clerk_provider");
    var setting = saved || (inherited ? { provider: params.get("clerk_provider"), base_url: params.get("clerk_base"), model: params.get("clerk_model") } : null);
    var provider = (setting && setting.provider) || "tencent";
    var item = providers.find(function (p) { return p.id === provider; }) || providers[0];
    if (!item) return;
    $("clerkProvider").value = item.id;
    $("clerkAddress").value = (setting && setting.base_url) || item.base_url || "";
    $("clerkModel").value = (setting && setting.model) || (item.models && item.models[0]) || "";
    syncClerkExtras();
    clerkStopped = !!(setting && setting.disabled);
    $("clerkClear").textContent = clerkStopped ? "启用" : "停用";
    $("clerkKey").textContent = clerkReady || clerkStopped ? "更换密钥" : "填写密钥";
    if (clerkStopped) setClerkState("", "已停用");
    else if (clerkReady) setClerkState("ok", inherited ? "已配置（沿用最近一次设置）" : "已配置");
    else setClerkState("", "还没配置密钥");
    clerkBaseline = clerkSnapshot(clerkChoice());
  }
  function loadClerkRow() {
    return get("/api/clerk/providers").then(function (d) {
      providers = d.providers || [];
      var sel = $("clerkProvider");
      sel.innerHTML = "";
      providers.forEach(function (p) {
        var op = document.createElement("option");
        op.value = p.id;
        op.textContent = p.name;
        sel.appendChild(op);
      });
      return get("/api/clerk/setting");
    }).then(function (d) {
      paintClerk(d.setting);
    }).catch(function (err) {
      setClerkState("bad", err.message || "读不到行政设置");
    });
  }

  function post(path, body) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Chat-Token": encodeURIComponent(token) },
      body: JSON.stringify(Object.assign({ project: project }, body)) })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || "出错了"); return d; }); });
  }
  function get(path, extra) {
    return fetch(path + "?" + new URLSearchParams(Object.assign({ project: project, token: token }, extra)).toString())
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || "出错了"); return d; }); });
  }

  function toolSelect(role) {
    return rows[role].querySelector(".tool-select") || rows[role].querySelector("select");
  }

  function fill(role, options, value) {
    var sel = toolSelect(role);
    sel.innerHTML = "";
    options.forEach(function (o) { var op = document.createElement("option"); op.value = o.value; op.textContent = o.label; sel.appendChild(op); });
    sel.value = value || "";
    sel.addEventListener("change", function () {
      renderRoleModelBox(role);
      invalidate([role]);
    });
  }

  function renderRoleModelBox(role) {
    var box = rows[role].querySelector(".model-box");
    if (!box) return;
    var tool = toolSelect(role).value;
    if (tool !== "codex" && tool !== "cursor" && tool !== "agy" && tool !== "qwen" && tool !== "tencent") {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    box.hidden = false;
    box.innerHTML = "";

    var pick = pickOf(role, tool);
    if (tool === "agy" && TeamCore.findEffort(TeamCore.splitEfforts(modelOptions.agy), pick.model)) {
      renderEffortSplitBox(role, box, pick, TeamCore.splitEfforts(modelOptions.agy));
      return;
    }
    var opts = (modelOptions[tool] || []).slice();
    if (tool === "agy") {  // 当前是名单外的自定义名：照旧单框；名单里的模型并成一项，选了再出强度框
      opts = TeamCore.splitEfforts(opts).map(function (g) {
        return { value: g.efforts.length ? TeamCore.nearestEffort(g, "high").value : g.base, label: g.label };
      });
    }
    var curVal = pick.model || "";
    var inPreset = opts.some(function (o) { return o.value === curVal; });
    if (curVal && !inPreset) {
      opts.unshift({ value: curVal, label: curVal + "（当前）" });
    }
    opts.push({ value: "__custom__", label: "其他自定义模型名…" });

    var row1 = document.createElement("div");
    row1.className = "model-row";
    var lbl1 = document.createElement("span");
    lbl1.className = "model-lbl";
    lbl1.textContent = "使用模型";
    var sel1 = document.createElement("select");
    sel1.className = "model-sub";
    sel1.setAttribute("aria-label", ROLE[role] + "模型");
    opts.forEach(function (o) {
      var op = document.createElement("option");
      op.value = o.value;
      op.textContent = o.label;
      sel1.appendChild(op);
    });
    sel1.value = curVal || (opts[0] && opts[0].value) || "";
    row1.appendChild(lbl1);
    row1.appendChild(sel1);
    box.appendChild(row1);

    var customInput = document.createElement("input");
    customInput.type = "text";
    customInput.className = "model-sub model-custom";
    customInput.placeholder = "输入模型名，例如 " + (curVal || "gpt-6.1-sol");
    customInput.hidden = true;
    box.appendChild(customInput);

    sel1.addEventListener("change", function () {
      if (sel1.value === "__custom__") {
        customInput.hidden = false;
        customInput.value = pick.model || "";
        customInput.focus();
      } else {
        customInput.hidden = true;
        pick.model = sel1.value;
        if (tool === "codex" || tool === "agy") renderRoleModelBox(role);
        onModelModified(role);
      }
    });
    customInput.addEventListener("input", function () {
      var v = customInput.value.trim();
      if (v) {
        pick.model = v;
        onModelModified(role);
      }
    });

    if (tool === "codex") {
      var matched = (modelOptions.codex || []).find(function (m) { return m.value === pick.model; });
      var allowedEfforts = (matched && matched.efforts && matched.efforts.length) ? matched.efforts : null;
      var effortOpts = (modelOptions.codex_effort || []).filter(function (o) {
        return !allowedEfforts || allowedEfforts.indexOf(o.value) !== -1;
      });
      if (effortOpts.length && !effortOpts.some(function (o) { return o.value === pick.effort; })) {
        pick.effort = effortOpts[effortOpts.length - 1].value;
      }
      var row2 = document.createElement("div");
      row2.className = "model-row effort";
      var lbl2 = document.createElement("span");
      lbl2.className = "model-lbl";
      lbl2.textContent = "思考强度";
      var sel2 = document.createElement("select");
      sel2.className = "model-sub";
      sel2.setAttribute("aria-label", ROLE[role] + "思考强度");
      effortOpts.forEach(function (o) {
        var op = document.createElement("option");
        op.value = o.value;
        op.textContent = o.label;
        sel2.appendChild(op);
      });
      sel2.value = pick.effort || "xhigh";
      sel2.addEventListener("change", function () {
        pick.effort = sel2.value;
        onModelModified(role);
      });
      row2.appendChild(lbl2);
      row2.appendChild(sel2);
      box.appendChild(row2);
    } else {
      var none = document.createElement("span");
      none.className = "na";
      none.textContent = "—";
      box.appendChild(none);
    }
  }

  // agy：左边选模型、右边选思考强度；两个框合起来存成完整编号（如 gemini-4-hydrogen-xhigh），保存格式不变
  var EFFORT_SHORT = { none: "无", minimal: "最少", low: "低", medium: "中", high: "高", xhigh: "极高", max: "最大" };
  function effortLabel(effort) {
    var o = (modelOptions.codex_effort || []).find(function (x) { return x.value === effort; });
    return o ? o.label : (EFFORT_SHORT[effort] || effort);
  }
  function renderEffortSplitBox(role, box, pick, groups) {
    var cur = TeamCore.findEffort(groups, pick.model);
    var group = groups.find(function (g) { return g.base === cur.base; });

    var row1 = document.createElement("div");
    row1.className = "model-row";
    var lbl1 = document.createElement("span");
    lbl1.className = "model-lbl";
    lbl1.textContent = "使用模型";
    var sel1 = document.createElement("select");
    sel1.className = "model-sub";
    sel1.setAttribute("aria-label", ROLE[role] + "模型");
    groups.concat([{ base: "__custom__", label: "其他自定义模型名…" }]).forEach(function (g) {
      var op = document.createElement("option");
      op.value = g.base;
      op.textContent = g.label;
      sel1.appendChild(op);
    });
    sel1.value = cur.base;
    row1.appendChild(lbl1);
    row1.appendChild(sel1);
    box.appendChild(row1);

    var customInput = document.createElement("input");
    customInput.type = "text";
    customInput.className = "model-sub model-custom";
    customInput.placeholder = "输入模型名，例如 " + pick.model;
    customInput.hidden = true;
    box.appendChild(customInput);

    var row2 = document.createElement("div");
    sel1.addEventListener("change", function () {
      if (sel1.value === "__custom__") {  // 填自定义名时收起强度框，免得点它把自定义名盖掉（复查 11-14）
        if (row2.parentNode) row2.parentNode.removeChild(row2);  // .model-row 是 display: contents，hidden 盖不住（复查第 2 轮）
        customInput.hidden = false;
        customInput.value = pick.model || "";
        customInput.focus();
        return;
      }
      var g = groups.find(function (x) { return x.base === sel1.value; });
      var now = TeamCore.findEffort(groups, pick.model);  // 按此刻的档位（用户可能刚改过强度，复查 11-14）
      var e = g && TeamCore.nearestEffort(g, now ? now.effort : cur.effort);
      pick.model = e ? e.value : sel1.value;
      renderRoleModelBox(role);
      onModelModified(role);
    });
    customInput.addEventListener("input", function () {
      var v = customInput.value.trim();
      if (v) {
        pick.model = v;
        onModelModified(role);
      }
    });

    row2.className = "model-row effort";
    var lbl2 = document.createElement("span");
    lbl2.className = "model-lbl";
    lbl2.textContent = "思考强度";
    var sel2 = document.createElement("select");
    sel2.className = "model-sub";
    sel2.setAttribute("aria-label", ROLE[role] + "思考强度");
    group.efforts.forEach(function (e) {
      var op = document.createElement("option");
      op.value = e.value;
      op.textContent = effortLabel(e.effort);
      sel2.appendChild(op);
    });
    sel2.value = pick.model;
    sel2.addEventListener("change", function () {
      if (sel1.value === "__custom__") return;  // 正在填自定义名时不许盖掉它
      pick.model = sel2.value;
      onModelModified(role);
    });
    row2.appendChild(lbl2);
    row2.appendChild(sel2);
    box.appendChild(row2);
  }

  // 这个岗位此刻的模型选择；换了工具就从这家的整体设置起步
  function pickOf(role, tool) {
    var p = picks[role];
    if (!p || p.tool !== tool) {
      p = { tool: tool, model: currentModels[tool] || "" };
      if (tool === "codex") p.effort = currentModels.codex_effort || "xhigh";
      picks[role] = p;
    }
    return p;
  }
  function rolePicks() {
    var t = team(), out = {};
    ALL_ROLES.forEach(function (r) {
      var tool = t[r];
      if (tool === "codex" || tool === "cursor" || tool === "agy" || tool === "qwen" || tool === "tencent") out[r] = pickOf(r, tool);
    });
    return out;
  }
  function snapshot() { return JSON.stringify({ team: team(), picks: rolePicks() }); }
  function teamChanged() { return installed && !!savedSnapshot && snapshot() !== savedSnapshot; }
  // 改了没保存就离开（点齿轮重开、退出应用）时不能丢（④）：暂存在项目里（应用每次换端口，页面自己存的会丢），回来恢复
  // 一次一次排队写：连着改两下，后写的不会被先写的盖掉（复查第 2 轮）
  var draftChain = Promise.resolve();
  function writeDraft(draft) {
    draftChain = draftChain.then(function () { return post("/api/team/draft", { draft: draft }); })
      .catch(function () { /* 存不了就算了 */ });
  }
  function keepDraft() {
    if (!installed) return;
    writeDraft(teamChanged() ? { base: JSON.parse(savedSnapshot), now: JSON.parse(snapshot()) } : null);
  }
  function dropDraft() { if (installed) writeDraft(null); }

  function renderAllModelBoxes() {
    ALL_ROLES.forEach(renderRoleModelBox);
  }

  function team() {
    return { tester: toolSelect("tester").value,
             developer: toolSelect("developer").value, reviewer: toolSelect("reviewer").value };
  }
  function activeRoles() { return installed ? ALL_ROLES : []; }   // 没接入（不是 git）的文件夹没有执行员可配，只有行政
  function isAlreadyConfigured() {
    return installed ? !!(info && info.team && info.team.configured) : clerkReady;
  }

  function setState(role, cls, text, how) {
    var s = rows[role].querySelector(".state");
    s.className = "state " + cls;
    s.innerHTML = '<span class="dot"></span><span class="txt"></span>';
    s.querySelector(".txt").textContent = text;
    if (how) { var h = document.createElement("span"); h.className = "how"; h.textContent = "怎么办：" + how; s.appendChild(h); }
  }

  function setIdle(role, changed) {
    var t = team();
    if (!TeamCore.needsCheck(t, role)) setState(role, "", "不复查，不用检测");
    else if (!isAlreadyConfigured()) setState(role, "", "还没检测");
    else if (changed) setState(role, "", "已调整，还没检测");
    else setState(role, "ok", "已配置");
  }

  function refresh() {
    var t = team(), active = activeRoles(), configured = isAlreadyConfigured();
    var busy = Object.keys(pending).length > 0;
    ALL_ROLES.forEach(function (r) {
      var b = rows[r].querySelector(".check-one");
      b.hidden = !TeamCore.needsCheck(t, r);
      b.disabled = !!pending[r];
      b.textContent = pending[r] ? "检测中…" : "检测";
      b.setAttribute("aria-label", ROLE[r] + (pending[r] ? "检测中" : "检测生效"));
    });
    $("save").textContent = configured ? "保存并返回对话" : "保存并开始";
    $("save").disabled = !TeamCore.canSave({ team: t, active: active, results: results, configured: configured, busy: busy }) && !clerkChanged() && !(clerkReady && (!installed || !configured));

    var need = active.filter(function (r) { return TeamCore.needsCheck(t, r); });
    var bad = need.filter(function (r) { return results[r] && !results[r].ok; });
    var sum = $("summary");
    sum.className = "summary";
    if (bad.length) { sum.className = "summary bad"; sum.textContent = bad.length + " 位没通，处理好后再点“检测”，或换一家 / 换个模型。"; }
    else if (need.length && need.every(function (r) { return results[r] && results[r].ok; })) { sum.className = "summary ok"; sum.textContent = "全部通了"; }
    else sum.textContent = "";

    $("sameWarn").hidden = !(installed && t.developer === t.reviewer);
    refreshUpgradeBanner();
  }

  function refreshUpgradeBanner() {
    var banner = $("upgradeBanner"), text = $("upgradeText"), btn = $("recheckUpgraded");
    if (!banner || !text || !btn) return;
    var st = TeamCore.upgradeState({
      items: upgradeItems, resolved: resolvedUpgrades, team: team(),
      active: activeRoles(), results: results, pending: pending
    });
    resolvedUpgrades = st.resolved;
    if (!st.visible.length) {
      banner.hidden = true;
      text.innerHTML = "";
      return;
    }
    banner.hidden = false;
    text.innerHTML = "";
    st.visible.forEach(function (entry) {
      var line = document.createElement("div");
      line.className = "upgrade-line";
      line.textContent = TeamCore.upgradeLineText(entry, ROLE);
      text.appendChild(line);
    });
    btn.disabled = st.checking || !st.toCheckRoles.length;
    btn.textContent = st.checking ? "检测中…" : "重新检测受影响岗位";
    btn.setAttribute("aria-label", st.checking ? "受影响岗位检测中" : ("重新检测：" + st.toCheckRoles.map(function (r) { return ROLE[r]; }).join("、")));
  }

  // 改了选择：只清这几行的结果；还在路上的检测结果也不再要（它测的是改之前的选择）
  function invalidate(roles) {
    roles.forEach(function (r) { delete results[r]; delete pending[r]; setIdle(r, true); });
    refresh();
    keepDraft();
  }

  function onModelModified(role) {
    invalidate([role]);
  }

  function apply(r) {
    results[r.role] = r;
    var shared = r.shared_with && team()[r.shared_with] === team()[r.role];
    setState(r.role, r.ok ? "ok" : "bad", r.reason + (shared ? "（和" + ROLE[r.shared_with] + "同一家，一起测过）" : ""), r.ok && !r.warn ? "" : r.how);   // 通了但带提醒（用量太大）也把说明显示出来（第四轮复查）
  }

  function waitingOn(id, roles) { return roles.filter(function (r) { return pending[r] === id; }); }

  // 这次检测没给出结果的行标红，不能让它一直停在“检测中”
  function giveUp(id, roles, message) {
    waitingOn(id, roles).forEach(function (r) {
      delete pending[r];
      results[r] = { ok: false };
      setState(r, "bad", message);
    });
    refresh();
  }

  function watch(run, id, roles, started, guard) {
    var limit = (checkTimeout + 30) * 1000;
    var timer = setInterval(function () {
      if (!waitingOn(id, roles).length) { clearInterval(timer); if (guard) clearTimeout(guard); return; }
      // 到时间一定给结果（③：agy 检测曾一直停在“检测中”）
      if (Date.now() - started > limit) {
        clearInterval(timer);
        if (guard) clearTimeout(guard);
        giveUp(id, roles, "超过 " + Math.round(limit / 60000) + " 分钟还没有结果，算没通：可能卡住了。再点一次“检测”，或换一家、换个模型");
        return;
      }
      get("/api/team/check", { run: run }).then(function (d) {
        d.results.forEach(function (r) {
          if (pending[r.role] === id) { delete pending[r.role]; apply(r); }
        });
        if (d.done) { clearInterval(timer); if (guard) clearTimeout(guard); giveUp(id, roles, "这次检测没出结果，再点一次“检测生效”"); }
        refresh();
      }).catch(function () { /* 下一次再取 */ });
    }, 700);
  }

  function check(role) {
    checkRoles(TeamCore.rolesSharingTool(team(), role, activeRoles(), rolePicks()));
  }

  function checkRoles(roles) {
    if (!roles || !roles.length) return;
    var t = team(), id = ++runSeq;
    roles.forEach(function (r) {
      delete results[r];
      pending[r] = id;
      setState(r, "run", "检测中…在临时文件夹里试着建一个文件");
    });
    refresh();
    var started = Date.now();   // 从点“检测”那一刻算：连发起检测的请求本身卡住，也要到时间给结果（复查第 1 轮）
    var guard = setTimeout(function () {
      giveUp(id, roles, "超过 " + Math.round((checkTimeout + 30) / 60) + " 分钟还没有结果，算没通：可能卡住了。再点一次“检测”，或换一家、换个模型");
    }, (checkTimeout + 30) * 1000);
    post("/api/team/check", { team: t, models: currentModels, role_models: rolePicks(), roles: roles })
      .then(function (d) { watch(d.run, id, roles, started, guard); })
      .catch(function (err) { clearTimeout(guard); giveUp(id, roles, err.message); });
  }

  function reset() {
    results = {};
    activeRoles().forEach(function (r) { setIdle(r, false); });
    refresh();
  }

  var needClerkNotice = false;   // 顶部那条“还差行政”是不是现在挂着
  function hideNeedClerkNotice() {   // 行政接通了就撤掉它；别的提示（配置有问题、工具升级了）不动
    if (!needClerkNotice) return;
    needClerkNotice = false;
    $("problem").hidden = true;
  }
  function done() {
    // 在开发助手窗口里，这个地址会被窗口接住：行政接通了就切回对话；没接通它会把本页带着 need_clerk 重开，不会放人进对话
    location.href = "superagent://saved";
    setTimeout(function () { $("summary").className = "summary ok"; $("summary").textContent = "已保存。"; }, 300);
  }

  function canSaveTeam() {
    return TeamCore.canSave({
      team: team(), active: activeRoles(), results: results,
      configured: isAlreadyConfigured(), busy: Object.keys(pending).length > 0
    });
  }
  function clerkChanged() {
    return !!clerkBaseline && clerkSnapshot(clerkChoice()) !== clerkBaseline;
  }
  function saveTeamThenDone() {
    var t = team();
    if (!installed) { done(); return; }
    post("/api/team/save", { team: t, models: currentModels, role_models: rolePicks() }).then(function () { dropDraft(); done(); })
      .catch(function (err) { $("summary").className = "summary bad"; $("summary").textContent = err.message; });
  }

  function restoreDraft() {
    if (!installed) return;
    get("/api/team/draft").then(function (d) {
      var rowsToApply = TeamCore.draftRows(JSON.parse(savedSnapshot), d && d.draft);
      var atLoad = JSON.parse(savedSnapshot), current = JSON.parse(snapshot());
      function rowAtLoad(r) { return JSON.stringify([atLoad.team[r], (atLoad.picks || {})[r] || null]); }
      function rowNow(r) { return JSON.stringify([current.team[r], (current.picks || {})[r] || null]); }
      // 读草稿期间用户已经动过的行不碰（复查第 2 轮）
      var roles = Object.keys(rowsToApply).filter(function (r) { return rowNow(r) === rowAtLoad(r); });
      if (!roles.length) return;
      roles.forEach(function (r) {
        toolSelect(r).value = rowsToApply[r].tool || "";
        if (rowsToApply[r].pick) picks[r] = rowsToApply[r].pick;
      });
      renderAllModelBoxes();
      // 恢复的行一律要重新检测才能保存：上次可能就是测不通才没存（复查第 1 轮）
      roles.forEach(function (r) {
        results[r] = { ok: false, restored: true };
        setState(r, "bad", "上次改过还没检测：请点“检测”，通了才能保存");
      });
      refresh();
      $("summary").className = "summary";
      $("summary").textContent = "上次改了还没保存，已帮你恢复（要重新检测再保存）。";
    }).catch(function () { /* 读不到就算了 */ });
  }

  window.SuperAgentTeam = {
    clerkConnected: function () {   // 填密钥并“检测”通过后，窗口真的把行政接通了（页面没重载）：跟着知道
      clerkReady = true;
      hideNeedClerkNotice();
      refresh();
    },
    clerkResult: function (ok, reason) {
      clerkWaiting = false;
      lockClerkRow(false);
      if (ok) {
        clerkReady = true;
        hideNeedClerkNotice();
        clerkStopped = false;
        $("clerkClear").textContent = "停用";
        clerkBaseline = clerkSent || clerkSnapshot(clerkChoice());
        setClerkState("ok", "已配置");
        var next = TeamCore.afterClerkSaved({ canSaveTeam: canSaveTeam(), installed: installed, configured: isAlreadyConfigured() });
        if (next === "save-team") saveTeamThenDone();
        else if (next === "leave") leaveWithoutTeam();   // 没接入的文件夹、或团队还没配：只用行政也能回到对话
        else {
          refresh();
          $("summary").className = "summary bad";
          $("summary").textContent = "行政已保存。团队有没通过检测的项，还没保存。";
        }
      } else {
        refresh();
        setClerkState("bad", reason || "没有保存成功");
        $("summary").className = "summary bad";
        $("summary").textContent = reason || "行政设置没有保存，团队也没改。";
      }
    },
    clerkCheckResult: function (ok, reason) {
      var checked = clerkCheckFor;
      clerkCheckFor = "";
      lockClerkRow(false);
      if (checked && checked !== clerkSnapshot(clerkChoice())) return;  // 测完已经换了选择
      setClerkState(ok ? "ok" : "bad", ok ? (reason || "通了") : (reason || "没有通"));
    }
  };

  $("clerkProvider").addEventListener("change", function () {
    var item = providers.find(function (p) { return p.id === $("clerkProvider").value; });
    if (item && item.id !== "other") $("clerkAddress").value = item.base_url || "";
    // 选“其他”时不能带着别家的地址，不然密钥会存到别家的域名下
    else if (providers.some(function (p) { return p.base_url && p.base_url === $("clerkAddress").value; })) $("clerkAddress").value = "";
    if (item && item.models && item.models.length && item.models.indexOf($("clerkModel").value) < 0) {
      $("clerkModel").value = item.models[0];
    }
    syncClerkExtras();
    setClerkState("", "已调整，还没保存");
    refresh();
  });
  $("clerkModel").addEventListener("input", function () { setClerkState("", "已调整，还没保存"); refresh(); });
  $("clerkAddress").addEventListener("input", function () { setClerkState("", "已调整，还没保存"); refresh(); });
  $("clerkCheck").addEventListener("click", function () {
    clerkCheckFor = clerkSnapshot(clerkChoice());
    setClerkState("run", "检测中…");
    location.href = "superagent://clerk-check?" + clerkQuery(clerkChoice());
  });
  $("clerkKey").addEventListener("click", function () {
    clerkCheckFor = "";
    location.href = "superagent://clerk-setup?" + clerkQuery(clerkChoice());
  });
  $("clerkClear").addEventListener("click", function () {
    if (!clerkStopped) { location.href = "superagent://clerk-clear?" + clerkQuery(clerkChoice()); return; }
    clerkCheckFor = "";
    clerkSent = clerkSnapshot(clerkChoice());
    lockClerkRow(true);
    setClerkState("run", "正在测试并启用…");
    location.href = "superagent://clerk-enable?" + clerkQuery(clerkChoice());
  });
  $("clerkForget").addEventListener("click", function () {
    location.href = "superagent://clerk-forget?" + clerkQuery(clerkChoice());
  });

  ALL_ROLES.forEach(function (r) {
    rows[r].querySelector(".check-one").addEventListener("click", function () { check(r); });
  });
  $("recheckUpgraded").addEventListener("click", function () {
    var st = TeamCore.upgradeState({
      items: upgradeItems, resolved: resolvedUpgrades, team: team(),
      active: activeRoles(), results: results, pending: pending
    });
    if (st.toCheckRoles.length) checkRoles(st.toCheckRoles);
  });
  // 团队有改动却存不了：说清楚是谁没测通，问要不要不保存就回去（②：以前悄悄丢掉）
  function leaveWithoutTeam() {
    if (!teamChanged()) { done(); return; }
    var why = TeamCore.unsavedReason({ team: team(), active: activeRoles(), results: results, configured: isAlreadyConfigured() });
    $("summary").className = "summary bad";
    $("summary").textContent = "团队的改动还没存：" + (why || "有一行没测通") + "。";
    if (window.confirm("团队的改动还没存：" + (why || "有一行没测通") + "。\n不保存，直接回到对话吗？（改动会留着，下次打开设置还在）")) done();
  }
  $("save").addEventListener("click", function () {
    if (clerkWaiting) return;
    if (!clerkChanged()) {
      if (canSaveTeam()) saveTeamThenDone();
      else leaveWithoutTeam();   // 只用行政：团队没配也能回到对话；但改了的不能悄悄丢
      return;
    }
    clerkWaiting = true;
    clerkSent = clerkSnapshot(clerkChoice());
    lockClerkRow(true);
    $("save").disabled = true;
    setClerkState("run", "正在测试并保存…");
    location.href = "superagent://clerk-apply?" + clerkQuery(clerkChoice());
  });

  if (!project || !token) {
    $("problem").hidden = false;
    $("problem").textContent = "请从应用窗口打开团队配置。";
  } else {
    get("/api/team").then(function (d) {
      info = d;
      installed = d.installed;
      if (d.models) currentModels = Object.assign(currentModels, d.models);
      if (d.role_models) picks = JSON.parse(JSON.stringify(d.role_models));
      if (d.check_timeout) checkTimeout = d.check_timeout;
      if (d.model_options) modelOptions = Object.assign(modelOptions, d.model_options);
      $("workers").hidden = !installed;
      $("noGit").hidden = installed;
      if (d.problem) { $("problem").hidden = false; $("problem").textContent = "原来的配置有问题，已换成默认值：" + d.problem; }
      upgradeItems = TeamCore.normalizeUpgrades(d.upgraded_items, d.upgraded);
      resolvedUpgrades = {};
      fill("tester", d.options.worker, d.team.tester);
      fill("developer", d.options.worker, d.team.developer);
      fill("reviewer", d.options.reviewer, d.team.reviewer);
      renderAllModelBoxes();
      reset();
      savedSnapshot = snapshot();
      restoreDraft();
      var needClerk = params.get("need_clerk");   // 1：保存团队后想回对话被拦；2：没保存，只是再点“团队”想关掉被拦
      if ((needClerk === "1" || needClerk === "2") && !clerkReady) {   // 行政还没配好：窗口把本页带着提示重开
        needClerkNotice = true;
        $("problem").hidden = false;
        $("problem").textContent = (needClerk === "1" ? "团队已保存。" : "") + TeamCore.leaveBlockedReason({ clerkReady: false });
      }
      return loadClerkRow();
    }).catch(function (err) { $("problem").hidden = false; $("problem").textContent = "读不到团队配置：" + err.message; });
  }
})();
