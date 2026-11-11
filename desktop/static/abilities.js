/* 能力层页面：左栏能力层列表、中间能力卡片详情、运行卡片、更换模型、移出能力层。
   分两层：AbilitiesCore（规则层，不碰页面，tests/abilities_page_check.js 用 node 直接检查）；页面层。
   纪律：页面里没有任何密钥输入框，请求里不带密钥；不使用本地存储；不向控制台打印。 */
"use strict";

var AbilitiesCore = {
  esc: function (s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  },

  statusView: function (item) {
    var st = (item && item.status) || "ok";
    if (st === "ok") return { cls: "ok", text: "能用" };
    if (st === "failed") return { cls: "warn", text: "上次没成" };
    return { cls: "idle", text: "不能用" };
  },

  subText: function (item) {
    item = item || {};
    var name = item.block_name || "";
    if (item.model) {
      return name ? name + " · " + item.model : item.model;
    }
    return name;
  },

  outputText: function (type) {
    var map = { text: "文字", list: "列表", image: "图片", file: "文件" };
    return map[type] || type || "";
  },

  checkText: function (output, inputSchema, kind) {
    output = output || {};
    var type = output.type || "";
    var check = output.check || {};

    if (type === "text") {
      if (check.of && check.min_ratio !== undefined && check.max_ratio !== undefined) {
        var ofKey = check.of;
        var ofTitle = ofKey;
        if (inputSchema && inputSchema.properties && inputSchema.properties[ofKey] && inputSchema.properties[ofKey].title) {
          ofTitle = inputSchema.properties[ofKey].title;
        }
        var ratioName = function (r) {
          if (r === 0.5) return "一半";
          if (r === 1) return "一样多";
          if (r === 2) return "两倍";
          if (r === 3) return "三倍";
          return r + " 倍";
        };
        return "有内容，字数在你填的「" + ofTitle + "」的" + ratioName(check.min_ratio) + "到" + ratioName(check.max_ratio) + "之间";
      }
      if (check.min_chars !== undefined && check.max_chars !== undefined) {
        return "有内容，字数在 " + check.min_chars + " 到 " + check.max_chars + " 之间";
      }
      if (check.min_chars !== undefined) {
        return "有内容，不少于 " + check.min_chars + " 字";
      }
      if (check.max_chars !== undefined) {
        return "有内容，不超过 " + check.max_chars + " 字";
      }
      return "有内容";
    }

    if (type === "list") {
      var n = check.min_items !== undefined ? check.min_items : 1;
      var suffix = kind === "search" ? "和链接" : "";
      return "至少 " + n + " 条，每条都有标题" + suffix;
    }

    if (type === "image") {
      var side = check.min_side !== undefined ? check.min_side : 256;
      return "存出了图片，是 PNG 或 JPG，宽高都不小于 " + side;
    }

    if (type === "file") {
      var exts = (check.extensions || []).join("、");
      return "存出了文件，不是空的，扩展名是 " + exts + " 之一";
    }

    return "有内容";
  },

  formFields: function (inputSchema) {
    if (!inputSchema || !inputSchema.properties) return [];
    var props = inputSchema.properties;
    var reqs = Array.isArray(inputSchema.required) ? inputSchema.required : [];
    var fields = [];
    for (var key in props) {
      if (!Object.prototype.hasOwnProperty.call(props, key)) continue;
      var p = props[key] || {};
      var kind = "text";
      var options = null;
      if (Array.isArray(p.enum) && p.enum.length) {
        kind = "select";
        options = p.enum;
      } else if (p.type === "integer" || p.type === "number") {
        kind = "number";
      } else if (p.type === "string" && p.maxLength !== undefined && p.maxLength > 200) {
        kind = "textarea";
      }
      var val = p.default !== undefined ? p.default : "";
      var placeholder = (Array.isArray(p.examples) && p.examples.length) ? "例如：" + p.examples[0] : "";
      var isReq = reqs.indexOf(key) >= 0;
      fields.push({
        name: key,
        title: p.title || key,
        kind: kind,
        options: options,
        value: val,
        placeholder: placeholder,
        required: isReq,
        min: p.minimum !== undefined ? p.minimum : null,
        max: p.maximum !== undefined ? p.maximum : null,
        maxLength: p.maxLength !== undefined ? p.maxLength : null,
      });
    }
    return fields;
  },

  collectInputs: function (fields, raw) {
    fields = fields || [];
    raw = raw || {};
    var inputs = {};
    for (var i = 0; i < fields.length; i++) {
      var f = fields[i];
      var rawVal = raw[f.name];
      var strVal = String(rawVal !== undefined && rawVal !== null ? rawVal : "").trim();
      if (!strVal) {
        if (f.required) {
          return { ok: false, error: "请填写「" + f.title + "」" };
        }
        continue;
      }
      if (f.kind === "number") {
        if (!/^-?\d+$/.test(strVal)) {
          return { ok: false, error: "「" + f.title + "」要填 " + f.min + " 到 " + f.max + " 的整数" };
        }
        var num = parseInt(strVal, 10);
        if ((f.min !== null && num < f.min) || (f.max !== null && num > f.max)) {
          return { ok: false, error: "「" + f.title + "」要填 " + f.min + " 到 " + f.max + " 的整数" };
        }
        inputs[f.name] = num;
      } else {
        if (f.maxLength !== null && strVal.length > f.maxLength) {
          return { ok: false, error: "「" + f.title + "」最多 " + f.maxLength + " 个字" };
        }
        inputs[f.name] = strVal;
      }
    }
    return { ok: true, inputs: inputs };
  },

  resultView: function (result, card) {
    result = result || {};
    var status = result.status || (result.ok ? "ok" : "error");
    var sec = result.seconds !== undefined ? result.seconds : 0;
    var secStr = sec < 1 ? "不到 1 秒" : Math.round(sec) + " 秒";

    var tone = "bad";
    var head = "";
    var said = result.platform_said || "";
    var tip = "";
    var ask = null;
    var body = null;

    if (status === "ok") {
      tone = "ok";
      if (result.model) {
        head = "用了 " + result.model + " · " + secStr;
      } else {
        head = sec < 1 ? "用时不到 1 秒" : ("用时 " + secStr);
      }
    } else if (status === "format") {
      tone = "bad";
      head = "没成：" + (result.reason || "");
    } else if (status === "model") {
      tone = "warn";
      head = "模型不通：" + (result.reason || "");
      if (result.next_model) {
        ask = { model: result.next_model, text: "要换成 " + result.next_model + " 再试一次吗？" };
      } else {
        if (card && card.how && card.how.kind === "llm_api") {
          tip = "可以在上面的模型下拉框换一个再试。";
        } else {
          tip = "这张卡没有备用模型。";
        }
      }
    } else if (status === "block") {
      tone = "bad";
      head = "积木块的问题：" + (result.reason || "");
      tip = result.how || "";
    } else {
      tone = "bad";
      head = "没成：" + (result.reason || "");
      tip = result.how || "";
    }

    if (result.output !== null && result.output !== undefined) {
      var outType = (card && card.output && card.output.type) || "";
      if (outType === "text") {
        body = { type: "text", text: String(result.output) };
      } else if (outType === "list") {
        body = { type: "list", items: Array.isArray(result.output) ? result.output : [] };
      } else if (outType === "image") {
        body = { type: "image", name: String(result.output) };
      } else if (outType === "file") {
        body = { type: "file", name: String(result.output) };
      }
    }

    return {
      tone: tone,
      head: head,
      said: said,
      tip: tip,
      ask: ask,
      body: body,
    };
  },

  runsView: function (records) {
    if (!Array.isArray(records)) return [];
    var out = [];
    for (var i = 0; i < records.length; i++) {
      var r = records[i] || {};
      var t = String(r.time || "");
      var timeStr = "";
      var m = t.match(/^\d{4}-(\d{2}-\d{2})[T ](\d{2}:\d{2})/);
      if (m) {
        timeStr = m[1] + " " + m[2];
      } else {
        timeStr = t;
      }
      out.push({
        time: timeStr,
        ok: !!r.ok,
        text: r.ok ? "成了" : "没成",
        model: r.model || "",
        reason: r.ok ? "" : (r.reason || ""),
      });
    }
    return out;
  },

  promptText: function (card) {
    if (!card || !card.how) return "";
    var kind = card.how.kind;
    if (kind === "llm_api") {
      var sys = card.how.system || "";
      var pr = card.how.prompt || "";
      return "系统：" + sys + "\n\n要求：" + pr;
    }
    if (kind === "cli") {
      return card.how.prompt || "";
    }
    return "";
  },

  removeConfirm: function (card) {
    var title = (card && card.title) || "";
    return "把「" + title + "」移出能力层？记录和成果都留着。";
  },

  canChangeModel: function (card) {
    return !!(card && card.how && card.how.kind === "llm_api");
  },

  inputsView: function (inputSchema, inputs) {
    if (!inputs || typeof inputs !== "object") return [];
    inputSchema = inputSchema || {};
    var props = inputSchema.properties || {};
    var out = [];
    var keys = Object.keys(props);
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      if (k in inputs && inputs[k] !== null && inputs[k] !== undefined) {
        var str = String(inputs[k]);
        if (!str.trim()) continue;
        var p = props[k] || {};
        var tit = p.title || k;
        out.push({ title: tit, value: str });
      }
    }
    return out;
  },
};

if (typeof module !== "undefined") module.exports = AbilitiesCore;

if (typeof document !== "undefined") (function () {
  var POLL_MS = 5000;
  var JOB_MS = 1500;
  var params = new URLSearchParams(location.search);
  var chatToken = params.get("token") || "";

  var currentAbilityId = null;
  var currentCard = null;
  var currentDetailData = null;
  var abilityGen = 0;
  var currentJobTimer = null;
  var pollTimer = null;

  function $(id) {
    return document.getElementById(id);
  }

  function apiGet(url, query) {
    var full = url;
    var q = new URLSearchParams(query || {});
    if (chatToken) q.set("token", chatToken);
    var qs = q.toString();
    if (qs) full += (full.indexOf("?") >= 0 ? "&" : "?") + qs;
    return fetch(full, { method: "GET" }).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok) throw new Error(data.error || "请求失败");
        return data;
      });
    });
  }

  function apiPost(url, body) {
    var headers = { "Content-Type": "application/json" };
    if (chatToken) headers["X-Chat-Token"] = chatToken;
    return fetch(url, {
      method: "POST",
      headers: headers,
      body: JSON.stringify(body || {}),
    }).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok) throw new Error(data.error || "请求失败");
        return data;
      });
    });
  }

  function refreshList() {
    return apiGet("/api/abilities/list").then(function (data) {
      drawList(data);
    }).catch(function () {});
  }

  function drawList(data) {
    var list = $("abilitiesList");
    if (!list) return;
    list.innerHTML = "";
    var abilities = (data && data.abilities) || [];
    var broken = (data && data.broken) || [];
    if (!abilities.length && !broken.length) {
      list.innerHTML = '<div class="empty">还没有能力卡片</div>';
      return;
    }

    abilities.forEach(function (item) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "block-item" + (item.id === currentAbilityId ? " on" : "");
      btn.setAttribute("data-id", item.id);

      var top = document.createElement("div");
      top.className = "block-top";
      var nameSpan = document.createElement("span");
      nameSpan.className = "block-name";
      nameSpan.textContent = item.title || item.id;
      top.appendChild(nameSpan);

      var st = AbilitiesCore.statusView(item);
      var stSpan = document.createElement("span");
      stSpan.className = "block-status";
      stSpan.innerHTML = '<span class="dot ' + st.cls + '"></span><span>' + AbilitiesCore.esc(st.text) + "</span>";
      top.appendChild(stSpan);
      btn.appendChild(top);

      var meta = document.createElement("div");
      meta.className = "block-meta";
      meta.textContent = AbilitiesCore.subText(item);
      btn.appendChild(meta);

      btn.addEventListener("click", function () {
        openDetail(item.id);
      });
      list.appendChild(btn);
    });

    broken.forEach(function (b) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "block-item broken" + (b.id === currentAbilityId ? " on" : "");
      btn.setAttribute("data-id", b.id);
      var top = document.createElement("div");
      top.className = "block-top";
      var nameSpan = document.createElement("span");
      nameSpan.className = "block-name";
      nameSpan.textContent = b.id;
      top.appendChild(nameSpan);

      var stSpan = document.createElement("span");
      stSpan.className = "block-status";
      stSpan.innerHTML = '<span class="dot bad"></span><span>坏卡片</span>';
      top.appendChild(stSpan);
      btn.appendChild(top);

      var meta = document.createElement("div");
      meta.className = "block-meta";
      meta.textContent = "这张能力卡片坏了";
      btn.appendChild(meta);

      btn.addEventListener("click", function () {
        openBrokenDetail(b);
      });
      list.appendChild(btn);
    });
  }

  function openBrokenDetail(b) {
    if (window.BlocksStage && typeof window.BlocksStage.closeBlock === "function") {
      window.BlocksStage.closeBlock();
    }
    var chatFrame = $("chatFrame");
    if (chatFrame) chatFrame.hidden = true;
    var blockDetail = $("blockDetail");
    if (blockDetail) blockDetail.hidden = true;

    currentAbilityId = b.id;
    abilityGen++;
    if (currentJobTimer) {
      clearTimeout(currentJobTimer);
      currentJobTimer = null;
    }
    currentCard = null;
    currentDetailData = null;

    var detail = $("abilityDetail");
    if (detail) detail.hidden = false;

    highlightSidebar(b.id);

    $("abilityTitle").textContent = b.id;
    $("abilityStatus").innerHTML = '<span class="detail-status bad"><span class="dot bad"></span> 这张能力卡片坏了</span>';
    $("abilityDesc").textContent = b.problem || "";

    $("abilityRows").hidden = true;
    $("abilityExampleSection").hidden = true;
    $("abilityRunSection").hidden = true;
    $("abilityResult").hidden = true;
    $("abilityResult").innerHTML = "";
    $("abilityRunsSection").hidden = true;
    $("abilityPrompt").hidden = true;
    $("abilityRemove").hidden = true;
  }

  function openDetail(id) {
    if (window.BlocksStage && typeof window.BlocksStage.closeBlock === "function") {
      window.BlocksStage.closeBlock();
    }
    var chatFrame = $("chatFrame");
    if (chatFrame) chatFrame.hidden = true;
    var blockDetail = $("blockDetail");
    if (blockDetail) blockDetail.hidden = true;

    currentAbilityId = id;
    abilityGen++;
    var thisGen = abilityGen;

    if (currentJobTimer) {
      clearTimeout(currentJobTimer);
      currentJobTimer = null;
    }

    var detail = $("abilityDetail");
    if (detail) detail.hidden = false;

    // 清空旧内容
    $("abilityTitle").textContent = "正在读取…";
    $("abilityStatus").innerHTML = "";
    $("abilityDesc").textContent = "";
    $("abilityRows").hidden = true;
    $("abilityExampleSection").hidden = true;
    $("abilityRunSection").hidden = true;
    $("abilityResult").hidden = true;
    $("abilityResult").innerHTML = "";
    $("abilityRunsSection").hidden = true;
    $("abilityPrompt").hidden = true;
    $("abilityRemove").hidden = false;
    $("abilityRemove").disabled = false;

    highlightSidebar(id);

    apiGet("/api/abilities/detail", { id: id }).then(function (d) {
      if (thisGen !== abilityGen) return;
      currentCard = d.ability;
      currentDetailData = d;
      renderDetail(d, false);
    }).catch(function (err) {
      if (thisGen !== abilityGen) return;
      $("abilityTitle").textContent = id;
      $("abilityStatus").innerHTML = '<span class="detail-status bad">' + AbilitiesCore.esc(err.message) + "</span>";
    });
  }

  function highlightSidebar(id) {
    var items = document.querySelectorAll("#abilitiesList .block-item");
    items.forEach(function (el) {
      if (el.getAttribute("data-id") === id) {
        el.classList.add("on");
      } else {
        el.classList.remove("on");
      }
    });
  }

  function renderDetail(d, keepForm) {
    var card = d.ability;
    $("abilityTitle").textContent = card.title || card.id;

    // 状态
    var st = AbilitiesCore.statusView(d);
    var stHtml = '<span class="detail-status ' + st.cls + '"><span class="dot ' + st.cls + '"></span> ' + AbilitiesCore.esc(st.text) + "</span>";
    if (d.status_reason) {
      stHtml += '<span class="detail-reason"> · ' + AbilitiesCore.esc(d.status_reason) + "</span>";
    }
    $("abilityStatus").innerHTML = stHtml;

    // 说明
    $("abilityDesc").textContent = card.description || "";

    // 行
    $("abilityRows").hidden = false;
    // 积木块
    var bname = d.block.name || d.block.id;
    if (!d.block.exists) bname += "（已经移出基础层）";
    $("abilityBlockVal").textContent = bname;

    // 模型
    var isLlm = AbilitiesCore.canChangeModel(card);
    if (isLlm) {
      $("abilityModelRow").hidden = false;
      $("abilityModelGroup").hidden = false;
      $("abilityCliModelVal").hidden = true;
      renderModelSelectors(d);
    } else if (card.how && card.how.kind === "cli") {
      $("abilityModelRow").hidden = false;
      $("abilityModelGroup").hidden = true;
      $("abilityCliModelVal").hidden = false;
      var cliModel = card.model || (d.block && d.block.model) || "跟着积木块默认";
      $("abilityCliModelVal").innerHTML = AbilitiesCore.esc(cliModel) + ' <small class="dim">这一版不能换</small>';
    } else {
      // 搜索卡片不显示模型行
      $("abilityModelRow").hidden = true;
    }

    // 交回什么
    $("abilityOutputVal").textContent = AbilitiesCore.outputText(card.output && card.output.type);

    // 怎么算干成了
    $("abilityCheckVal").textContent = AbilitiesCore.checkText(card.output, card.inputSchema, card.how && card.how.kind);

    // 试做示例
    $("abilityExampleSection").hidden = false;
    var exBox = $("abilityExampleBox");
    if (d.example) {
      exBox.innerHTML = "";
      var exInputs = AbilitiesCore.inputsView(card.inputSchema, d.example.inputs || d.example.args || {});
      if (exInputs.length) {
        var inWrap = document.createElement("div");
        inWrap.className = "example-inputs";
        exInputs.forEach(function (it) {
          var inRow = document.createElement("div");
          inRow.className = "example-input-row";
          var titSpan = document.createElement("span");
          titSpan.className = "example-input-title";
          titSpan.textContent = it.title + "：";
          var valSpan = document.createElement("span");
          valSpan.className = "example-input-value";
          valSpan.textContent = it.value;
          inRow.appendChild(titSpan);
          inRow.appendChild(valSpan);
          inWrap.appendChild(inRow);
        });
        exBox.appendChild(inWrap);
      }
      renderResultInBox(exBox, d.example, card, null);
    } else {
      exBox.textContent = "还没有试做示例";
    }

    // 用一次
    $("abilityRunSection").hidden = false;
    var fields = AbilitiesCore.formFields(card.inputSchema);
    if (!keepForm) {
      renderForm(fields);
    }

    var runBtn = $("abilityRun");
    if (d.job) {
      runBtn.textContent = "正在用…";
      runBtn.disabled = true;
      pollJob(d.job, card, d.job_inputs || {});
    } else if (d.running) {
      runBtn.textContent = "正在用…";
      runBtn.disabled = true;
    } else {
      runBtn.textContent = "开始";
      runBtn.disabled = false;
    }

    // 最近几次
    $("abilityRunsSection").hidden = false;
    renderRuns(d.runs || []);

    // 交代原文
    var pText = AbilitiesCore.promptText(card);
    var promptDetails = $("abilityPrompt");
    if (pText) {
      promptDetails.hidden = false;
      $("abilityPromptPre").textContent = pText;
    } else {
      promptDetails.hidden = true;
    }
  }

  function renderModelSelectors(d) {
    var card = d.ability;
    var models = d.models || [];
    var selModel = $("abilityModel");
    var tip = $("abilityModelTip");
    selModel.innerHTML = "";
    if (!models.length) {
      var opt = document.createElement("option");
      opt.value = card.model || "";
      opt.textContent = card.model || "";
      selModel.appendChild(opt);
      tip.hidden = false;
      tip.textContent = "先去基础层检测「" + (d.block.name || d.block.id) + "」，拿到模型名单后才能换";
    } else {
      tip.hidden = true;
      models.forEach(function (m) {
        var opt = document.createElement("option");
        opt.value = m;
        opt.textContent = m;
        if (m === card.model) opt.selected = true;
        selModel.appendChild(opt);
      });
    }

    var backups = card.backup_models || [];
    var selB1 = $("abilityBackup1");
    var selB2 = $("abilityBackup2");
    [selB1, selB2].forEach(function (sel, idx) {
      sel.innerHTML = '<option value="">不用</option>';
      models.forEach(function (m) {
        var opt = document.createElement("option");
        opt.value = m;
        opt.textContent = m;
        if (backups[idx] === m) opt.selected = true;
        sel.appendChild(opt);
      });
    });

    $("abilitySaveModelTip").textContent = "";
  }

  function renderForm(fields) {
    var form = $("abilityForm");
    form.innerHTML = "";
    fields.forEach(function (f) {
      var row = document.createElement("div");
      row.className = "field";
      var lab = document.createElement("label");
      lab.setAttribute("for", "input_" + f.name);
      lab.textContent = f.title + (f.required ? " *" : "");
      row.appendChild(lab);

      var inputEl;
      if (f.kind === "select") {
        inputEl = document.createElement("select");
        (f.options || []).forEach(function (optVal) {
          var opt = document.createElement("option");
          opt.value = optVal;
          opt.textContent = optVal;
          if (optVal === f.value) opt.selected = true;
          inputEl.appendChild(opt);
        });
      } else if (f.kind === "textarea") {
        inputEl = document.createElement("textarea");
        inputEl.value = f.value || "";
        inputEl.placeholder = f.placeholder || "";
      } else {
        inputEl = document.createElement("input");
        inputEl.type = f.kind === "number" ? "number" : "text";
        inputEl.value = f.value !== undefined ? f.value : "";
        inputEl.placeholder = f.placeholder || "";
      }
      inputEl.id = "input_" + f.name;
      inputEl.setAttribute("data-name", f.name);
      row.appendChild(inputEl);
      form.appendChild(row);
    });
  }

  function renderRuns(records) {
    var box = $("abilityRuns");
    box.innerHTML = "";
    var list = AbilitiesCore.runsView(records);
    if (!list.length) {
      box.textContent = "还没用过";
      return;
    }
    list.forEach(function (r) {
      var row = document.createElement("div");
      row.className = "ability-run-item";
      var tSpan = document.createElement("span");
      tSpan.className = "run-time";
      tSpan.textContent = r.time;
      row.appendChild(tSpan);

      var stSpan = document.createElement("span");
      stSpan.className = "run-status " + (r.ok ? "ok" : "bad");
      stSpan.textContent = r.text;
      row.appendChild(stSpan);

      if (r.model) {
        var mSpan = document.createElement("span");
        mSpan.className = "run-model";
        mSpan.textContent = r.model;
        row.appendChild(mSpan);
      }

      if (r.reason) {
        var rSpan = document.createElement("span");
        rSpan.className = "run-reason";
        rSpan.textContent = r.reason;
        row.appendChild(rSpan);
      }
      box.appendChild(row);
    });
  }

  function renderResultInBox(box, result, card, taskInputs) {
    var v = AbilitiesCore.resultView(result, card);
    var headDiv = document.createElement("div");
    headDiv.className = "result-head " + v.tone;
    headDiv.textContent = v.head;
    box.appendChild(headDiv);

    if (v.said) {
      var saidDiv = document.createElement("div");
      saidDiv.className = "result-said";
      saidDiv.textContent = v.said;
      box.appendChild(saidDiv);
    }

    if (v.tip) {
      var tipDiv = document.createElement("div");
      tipDiv.className = "result-tip";
      tipDiv.textContent = v.tip;
      box.appendChild(tipDiv);
    }

    if (v.ask && taskInputs) {
      var askDiv = document.createElement("div");
      askDiv.className = "result-ask";
      var askText = document.createElement("span");
      askText.textContent = v.ask.text;
      askDiv.appendChild(askText);

      var tryBtn = document.createElement("button");
      tryBtn.type = "button";
      tryBtn.className = "btn small primary";
      tryBtn.id = "abilityTryBackup";
      tryBtn.textContent = "换这个试";
      tryBtn.addEventListener("click", function () {
        executeRun(taskInputs, v.ask.model);
      });
      askDiv.appendChild(tryBtn);

      var noBtn = document.createElement("button");
      noBtn.type = "button";
      noBtn.className = "btn small ghost";
      noBtn.textContent = "先不试";
      noBtn.addEventListener("click", function () {
        askDiv.hidden = true;
      });
      askDiv.appendChild(noBtn);

      box.appendChild(askDiv);
    }

    if (v.body) {
      var bodyDiv = document.createElement("div");
      bodyDiv.className = "result-body";
      if (v.body.type === "text") {
        var textWrap = document.createElement("div");
        textWrap.className = "result-text-wrap";
        var pre = document.createElement("pre");
        pre.className = "result-text";
        pre.textContent = v.body.text;
        textWrap.appendChild(pre);

        var copyBtn = document.createElement("button");
        copyBtn.type = "button";
        copyBtn.className = "btn small copy-btn";
        copyBtn.textContent = "复制";
        copyBtn.addEventListener("click", function () {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(v.body.text).then(function () {
              copyBtn.textContent = "已复制";
              setTimeout(function () { copyBtn.textContent = "复制"; }, 2000);
            }).catch(function () {
              alert("没复制成，请手动选中复制");
            });
          } else {
            alert("没复制成，请手动选中复制");
          }
        });
        textWrap.appendChild(copyBtn);
        bodyDiv.appendChild(textWrap);
      } else if (v.body.type === "list") {
        var listUl = document.createElement("ul");
        listUl.className = "result-list";
        (v.body.items || []).forEach(function (item) {
          var li = document.createElement("li");
          var a = document.createElement("a");
          a.href = item.url || "#";
          a.target = "_blank";
          a.setAttribute("rel", "noopener"); // rel="noopener"
          a.textContent = item.title || item.url || "无标题";
          li.appendChild(a);
          if (item.summary || item.snippet) {
            var sum = document.createElement("p");
            sum.className = "result-summary";
            sum.textContent = item.summary || item.snippet || "";
            li.appendChild(sum);
          }
          listUl.appendChild(li);
        });
        bodyDiv.appendChild(listUl);
      } else if (v.body.type === "image") {
        var img = document.createElement("img");
        var q = new URLSearchParams({
          token: chatToken,
          id: card.id,
          run: result.run_id || "",
          name: v.body.name,
        });
        img.src = "/api/abilities/file?" + q.toString();
        img.alt = v.body.name;
        img.className = "result-image";
        bodyDiv.appendChild(img);
      } else if (v.body.type === "file") {
        var fLink = document.createElement("a");
        var fq = new URLSearchParams({
          token: chatToken,
          id: card.id,
          run: result.run_id || "",
          name: v.body.name,
        });
        fLink.href = "/api/abilities/file?" + fq.toString();
        fLink.target = "_blank";
        fLink.rel = "noopener";
        fLink.textContent = "成果文件：" + v.body.name;
        bodyDiv.appendChild(fLink);
      }
      box.appendChild(bodyDiv);
    }

    if (result.files && result.files.length && result.run_id) {
      var actDiv = document.createElement("div");
      actDiv.className = "result-actions";
      var revBtn = document.createElement("button");
      revBtn.type = "button";
      revBtn.className = "btn small";
      revBtn.textContent = "在访达里打开";
      revBtn.addEventListener("click", function () {
        apiPost("/api/abilities/reveal", { id: card.id, run: result.run_id }).catch(function (e) {
          alert("打开成果文件夹失败：" + e.message);
        });
      });
      actDiv.appendChild(revBtn);
      box.appendChild(actDiv);
    }
  }

  function executeRun(inputs, optModel) {
    if (!currentCard) return;
    var runCard = currentCard;
    var runGen = abilityGen;
    var runBtn = $("abilityRun");
    runBtn.textContent = "正在用…";
    runBtn.disabled = true;
    $("abilityFormError").textContent = "";

    var resBox = $("abilityResult");
    resBox.hidden = false;
    resBox.innerHTML = '<div class="result-running">正在运行中，请稍候…</div>';

    var payload = { id: runCard.id, inputs: inputs };
    if (optModel) payload.model = optModel;

    apiPost("/api/abilities/run", payload).then(function (data) {
      if (runGen !== abilityGen || !currentCard || currentCard.id !== runCard.id) return;
      pollJob(data.job, runCard, inputs);
    }).catch(function (err) {
      if (runGen !== abilityGen || !currentCard || currentCard.id !== runCard.id) return;
      runBtn.textContent = "开始";
      runBtn.disabled = false;
      $("abilityFormError").textContent = err.message;
      resBox.hidden = true;
    });
  }

  function pollJob(jobId, card, taskInputs) {
    if (currentJobTimer) clearTimeout(currentJobTimer);
    var thisGen = abilityGen;

    function check() {
      apiGet("/api/blocks/job", { id: jobId }).then(function (job) {
        if (thisGen !== abilityGen) return;
        if (!job.done) {
          currentJobTimer = setTimeout(check, JOB_MS);
          return;
        }
        // 完成
        if (!currentCard || currentCard.id !== card.id) return;
        $("abilityRun").textContent = "开始";
        $("abilityRun").disabled = false;
        var resBox = $("abilityResult");
        resBox.innerHTML = "";
        resBox.hidden = false;
        if (job.error) {
          var errDiv = document.createElement("div");
          errDiv.className = "result-head bad";
          errDiv.textContent = "没成：" + job.error;
          resBox.appendChild(errDiv);
        } else if (job.result) {
          renderResultInBox(resBox, job.result, card, taskInputs);
        }

        // 重新拉取详情，刷新“最近几次”
        apiGet("/api/abilities/detail", { id: card.id }).then(function (d) {
          if (thisGen !== abilityGen || !currentCard || currentCard.id !== card.id) return;
          renderRuns(d.runs || []);
          var st = AbilitiesCore.statusView(d);
          var stHtml = '<span class="detail-status ' + st.cls + '"><span class="dot ' + st.cls + '"></span> ' + AbilitiesCore.esc(st.text) + "</span>";
          if (d.status_reason) stHtml += '<span class="detail-reason"> · ' + AbilitiesCore.esc(d.status_reason) + "</span>";
          $("abilityStatus").innerHTML = stHtml;
        }).catch(function () {});

        refreshList();
      }).catch(function (err) {
        if (thisGen !== abilityGen) return;
        if (!currentCard || currentCard.id !== card.id) return;
        $("abilityRun").textContent = "开始";
        $("abilityRun").disabled = false;
        $("abilityResult").innerHTML = '<div class="result-head bad">查询任务出错：' + AbilitiesCore.esc(err.message) + "</div>";
      });
    }

    currentJobTimer = setTimeout(check, JOB_MS);
  }

  function closeDetail() {
    abilityGen++;
    if (currentJobTimer) {
      clearTimeout(currentJobTimer);
      currentJobTimer = null;
    }
    currentAbilityId = null;
    currentCard = null;
    currentDetailData = null;
    var detail = $("abilityDetail");
    if (detail) detail.hidden = true;
    highlightSidebar(null);
  }

  // 事件绑定
  var backBtn = $("abilityBack");
  if (backBtn) {
    backBtn.addEventListener("click", function () {
      closeDetail();
      var chatFrame = $("chatFrame");
      if (chatFrame) chatFrame.hidden = false;
    });
  }

  var runBtn = $("abilityRun");
  if (runBtn) {
    runBtn.addEventListener("click", function () {
      if (!currentCard) return;
      var fields = AbilitiesCore.formFields(currentCard.inputSchema);
      var raw = {};
      fields.forEach(function (f) {
        var el = $("input_" + f.name);
        if (el) raw[f.name] = el.value;
      });
      var check = AbilitiesCore.collectInputs(fields, raw);
      if (!check.ok) {
        $("abilityFormError").textContent = check.error;
        return;
      }
      $("abilityFormError").textContent = "";
      executeRun(check.inputs, null);
    });
  }

  var saveModelBtn = $("abilitySaveModel");
  if (saveModelBtn) {
    saveModelBtn.addEventListener("click", function () {
      if (!currentCard) return;
      var saveCard = currentCard;
      var saveGen = abilityGen;
      var modelVal = $("abilityModel").value;
      var b1 = $("abilityBackup1").value;
      var b2 = $("abilityBackup2").value;
      var backups = [];
      if (b1) backups.push(b1);
      if (b2) backups.push(b2);

      var tip = $("abilitySaveModelTip");
      tip.textContent = "正在保存…";
      tip.className = "model-save-tip";

      apiPost("/api/abilities/model", {
        id: saveCard.id,
        model: modelVal,
        backup_models: backups,
      }).then(function (data) {
        if (saveGen !== abilityGen || !currentCard || currentCard.id !== saveCard.id) return;
        tip.textContent = "已保存";
        tip.className = "model-save-tip ok";
        currentCard = data.ability;
        refreshList();
      }).catch(function (err) {
        if (saveGen !== abilityGen || !currentCard || currentCard.id !== saveCard.id) return;
        tip.textContent = err.message;
        tip.className = "model-save-tip bad";
      });
    });
  }

  var removeBtn = $("abilityRemove");
  if (removeBtn) {
    removeBtn.addEventListener("click", function () {
      if (!currentCard) return;
      var removeCard = currentCard;
      var removeGen = abilityGen;
      if (window.confirm(AbilitiesCore.removeConfirm(removeCard))) {
        removeBtn.disabled = true;
        apiPost("/api/abilities/remove", { id: removeCard.id }).then(function () {
          refreshList();
          if (removeGen !== abilityGen || !currentCard || currentCard.id !== removeCard.id) return;
          closeDetail();
          var chatFrame = $("chatFrame");
          if (chatFrame) chatFrame.hidden = false;
        }).catch(function (err) {
          if (removeGen !== abilityGen || !currentCard || currentCard.id !== removeCard.id) return;
          removeBtn.disabled = false;
          alert("移出失败：" + err.message);
        });
      }
    });
  }

  window.AbilitiesPage = {
    open: openDetail,
    close: closeDetail,
    refresh: refreshList,
  };

  refreshList();
  pollTimer = setInterval(function () {
    var detail = $("abilityDetail");
    // 只有在页面活跃时刷新
    if (detail && !$("mainLayout").hidden) {
      refreshList();
    }
  }, POLL_MS);
})();
