/* Atrading Web desk client */

const $ = (sel) => document.querySelector(sel);
const chat = $("#chat");
const input = $("#input");
const sendBtn = $("#send");
const abortBtn = $("#abort");
const statusText = $("#status-text");
const metaEl = $("#meta");
const slashMenu = $("#slash-menu");
const welcome = $("#welcome");
const app = $("#app");
const charCount = $("#char-count");
const ctxFill = $("#ctx-fill");
const ctxLabel = $("#ctx-label");
const modelLabel = $("#model-label");
const effortLabel = $("#effort-label");

let busy = false;
let liveReply = null;
let liveThink = null;
let liveActivity = null;
let bootstrap = null;
let commands = {};
let slashItems = [];
let slashIndex = 0;
let lastCtxRatio = 0;
let currentSessionId = null;
let sidebarSessions = [];
let enteredDesk = false;
let pendingMessages = [];

function apiHeaders() {
  return {
    "Content-Type": "application/json",
    "X-Atrading-CSRF": (bootstrap && bootstrap.csrf_token) || "",
  };
}

/** Secondary picker (TUI PickerScreen equivalent) */
let pickerState = null; // { kind, items, index, parent, back }

/** Command palette (TUI Ctrl+P) */
let paletteState = null; // { items, filtered, index }

const EXAMPLES = [
  "帮我分析 600519 的财务状况",
  "贵州茅台 PE 是多少，行业排第几",
  "回测 000001 双均线策略 2020-2025",
  "今天市场哪些板块最热",
];

const AGENT_MODES = [
  {
    id: "fast",
    label: "单 Agent",
    prefix: "",
    desc: "查行情、筛选、单次分析",
    placeholder: "输入消息… / 唤起命令 · Ctrl+P 面板 · Enter 发送",
  },
  {
    id: "research",
    label: "研究团队",
    prefix: "/research ",
    desc: "深度研究、行业与个股分析",
    placeholder: "【研究团队】输入研究问题，如：深度分析宁德时代…",
  },
  {
    id: "committee",
    label: "投资委员会",
    prefix: "/committee ",
    desc: "买入决策、仓位与组合评估",
    placeholder: "【投资委员会】输入决策问题，如：茅台是否值得买入…",
  },
  {
    id: "review",
    label: "交易复盘",
    prefix: "/review ",
    desc: "交割单、交易日记归因",
    placeholder: "【交易复盘】输入路径或描述，如：/review uploads/trades.csv",
  },
];

const AGENT_QUICK_LAUNCHES = [
  {
    mode: "research",
    label: "深度分析茅台",
    query: "深度分析贵州茅台基本面与估值",
  },
  {
    mode: "committee",
    label: "买入决策",
    query: "贵州茅台现在是否值得买入？请给出仓位建议区间",
  },
  {
    mode: "research",
    label: "板块拥挤度",
    query: "AI算力板块是否过度拥挤？",
  },
  {
    mode: "review",
    label: "交易复盘",
    query: "帮我复盘最近的交易记录，分析行为偏差",
  },
];

let agentMode = "fast";

function getAgentModeConfig(id) {
  return AGENT_MODES.find((m) => m.id === id) || AGENT_MODES[0];
}

function setAgentMode(mode, { focus = true } = {}) {
  const cfg = getAgentModeConfig(mode);
  agentMode = cfg.id;
  document.querySelectorAll("[data-agent-mode]").forEach((el) => {
    el.classList.toggle("active", el.dataset.agentMode === agentMode);
  });
  if (input) {
    input.placeholder = cfg.placeholder;
    if (focus) input.focus();
  }
  const hint = $("#agent-mode-hint");
  if (hint) hint.textContent = cfg.desc;
}

function buildSendText(raw) {
  const text = (raw || "").trim();
  if (!text || text.startsWith("/")) return text;
  const cfg = getAgentModeConfig(agentMode);
  if (!cfg.prefix) return text;
  return cfg.prefix + text;
}

function launchAgentQuery(mode, query) {
  setAgentMode(mode, { focus: false });
  showDesk();
  document.body.classList.add("rail-panel-open");
  send(buildSendText(query));
}

function escapeHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderMd(text) {
  if (window.marked) {
    try {
      return window.marked.parse(text || "", { breaks: true });
    } catch (_) {}
  }
  return `<p>${escapeHtml(text || "").replace(/\n/g, "<br>")}</p>`;
}

function el(tag, cls, html) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
}

function scrollChat() {
  chat.scrollTop = chat.scrollHeight;
}

function setBusy(on, text) {
  busy = !!on;
  sendBtn.disabled = busy;
  abortBtn.disabled = !busy;
  const railAbort = $("#rail-abort");
  if (railAbort) railAbort.disabled = !busy;
  if (text) statusText.textContent = text;
  else statusText.textContent = busy ? "运行中…" : "就绪";
  updateRailActivity(text || (busy ? "运行中…" : "就绪"), busy);
}

function updateCtx(usage) {
  if (!usage) return;
  const ratio = Number(usage.ratio || 0);
  lastCtxRatio = ratio;
  const pct = Math.max(0, Math.min(100, Math.round(ratio * 100)));
  ctxFill.style.width = `${pct}%`;
  ctxFill.classList.remove("warn", "danger");
  if (ratio >= 0.85) ctxFill.classList.add("danger");
  else if (ratio >= 0.65) ctxFill.classList.add("warn");
  ctxLabel.textContent = usage.label || `${pct}%`;
  const detail = $("#rail-ctx-detail");
  if (detail) {
    const parts = [usage.label || `${pct}%`];
    if (usage.prompt_tokens != null) parts.push(`in ${usage.prompt_tokens}`);
    if (usage.completion_tokens != null) parts.push(`out ${usage.completion_tokens}`);
    detail.textContent = "ctx · " + parts.join(" · ");
  }
}

function addUser(text) {
  const box = el("div", "msg user");
  box.appendChild(el("span", "role", "你"));
  const body = el("div", "body");
  body.textContent = text || "";
  box.appendChild(body);
  chat.appendChild(box);
  scrollChat();
  return box;
}

function addAssistant(text, { streaming = false } = {}) {
  const box = el("div", `msg assistant${streaming ? " streaming" : ""}`);
  box.appendChild(el("span", "role", (bootstrap && bootstrap.app) || "Atrading"));
  const body = el("div", "body");
  if (streaming) body.textContent = text || "";
  else body.innerHTML = renderMd(text || "");
  box.appendChild(body);
  chat.appendChild(box);
  scrollChat();
  return { box, body };
}

/** 对话流内的过程消息（思考 / 工具 / 运行状态）——与用户/助手气泡同一列 */
function addTrace({ kind, role, meta, body }) {
  const box = el("div", `msg trace ${kind || ""}`);
  const head = el("div", "trace-head");
  const roleEl = el("span", "role", role || "");
  head.appendChild(roleEl);
  const metaEl = el("span", "trace-meta", meta || "");
  head.appendChild(metaEl);
  const bodyEl = el("div", "body");
  bodyEl.textContent = body || "";
  box.appendChild(head);
  box.appendChild(bodyEl);
  chat.appendChild(box);
  scrollChat();
  return { box, body: bodyEl, role: roleEl, meta: metaEl };
}

function addFold({ kind, icon, title, meta, body, open = false }) {
  const wrap = el("div", `fold ${kind}${open ? " open" : ""}`);
  const head = document.createElement("button");
  head.type = "button";
  head.className = "fold-head";
  head.innerHTML = `<span class="chev"></span><span class="icon">${escapeHtml(icon)}</span><span class="name">${escapeHtml(title)}</span><span class="meta">${escapeHtml(meta || "")}</span>`;
  const metaEl = head.querySelector(".meta");
  const nameEl = head.querySelector(".name");
  const content = el("div", "fold-body");
  content.textContent = body || "";
  head.addEventListener("click", () => wrap.classList.toggle("open"));
  wrap.appendChild(head);
  wrap.appendChild(content);
  chat.appendChild(wrap);
  scrollChat();
  return { wrap, content, head, meta: metaEl, name: nameEl };
}

function prettyToolText(text) {
  const raw = String(text || "").trim();
  if (!raw) return "(空)";
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch (_) {
    return raw;
  }
}

function setActivity(text) {
  const msg = (text || "").trim();
  if (!msg) {
    clearActivity();
    return;
  }
  updateRailActivity(msg, true);
  const bar = $("#composer-activity");
  const txt = $("#composer-activity-text");
  if (bar && txt) {
    txt.textContent = msg;
    bar.classList.remove("hidden");
    bar.classList.add("live");
  }
  // 活动状态挂在输入框上方，不再塞进对话流
  liveActivity = { text: msg };
}

function clearActivity() {
  const bar = $("#composer-activity");
  if (bar) {
    bar.classList.add("hidden");
    bar.classList.remove("live");
  }
  const txt = $("#composer-activity-text");
  if (txt) txt.textContent = "运行中…";
  liveActivity = null;
  if (!busy) updateRailActivity("就绪", false);
}

function addLine(text) {
  const box = el("div", "msg line");
  box.textContent = text || "";
  chat.appendChild(box);
  scrollChat();
}

function addError(text) {
  const box = el("div", "msg err");
  box.textContent = text || "error";
  chat.appendChild(box);
  scrollChat();
}

function addPanel(title, bodyText) {
  return addFold({
    kind: "tool",
    icon: "ℹ",
    title: title || "信息",
    meta: "",
    body: bodyText || "",
    open: true,
  });
}

function addSessions(items) {
  const wrap = el("div", "panel-box");
  wrap.appendChild(el("div", "panel-title", "Sessions · 点击恢复"));
  const list = el("div", "session-list");
  if (!items || !items.length) {
    list.appendChild(el("div", "panel-empty", "暂无 session"));
  } else {
    items.forEach((s) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "session-item";
      b.innerHTML = `<span class="sid">${escapeHtml(s.id)}</span><span class="stitle">${escapeHtml(s.title || "")}</span><span class="smeta">${escapeHtml(String(s.messages || 0))} msgs</span>`;
      b.addEventListener("click", () => send(`/resume ${s.id}`));
      list.appendChild(b);
    });
  }
  wrap.appendChild(list);
  chat.appendChild(wrap);
  scrollChat();
}

function addHelp(items) {
  const wrap = el("div", "panel-box");
  wrap.appendChild(el("div", "panel-title", "命令帮助 · 点击填入"));
  const list = el("div", "help-list");
  (items || []).forEach((it) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "help-item";
    b.innerHTML = `<span class="cmd">${escapeHtml(it.cmd)}</span><span class="desc">${escapeHtml(it.desc)}</span>`;
    b.addEventListener("click", () => {
      input.value = it.cmd + " ";
      input.focus();
      renderSlash();
    });
    list.appendChild(b);
  });
  wrap.appendChild(list);
  chat.appendChild(wrap);
  scrollChat();
}

function applyPrefs(ev) {
  if (ev.model_label || ev.model) modelLabel.textContent = ev.model_label || ev.model;
  if (ev.effort_label || ev.effort) effortLabel.textContent = ev.effort_label || ev.effort;
  if (ev.session_id) {
    currentSessionId = ev.session_id;
    metaEl.textContent = `Session ${ev.session_id}`;
  } else if (ev.session_title) {
    metaEl.textContent = `Session · ${ev.session_title}`;
  }
  highlightActiveSession();
}

function closePicker() {
  pickerState = null;
  const root = $("#picker");
  if (!root) return;
  root.classList.add("hidden");
  root.setAttribute("aria-hidden", "true");
  input.focus();
}

function renderPickerList() {
  const list = $("#picker-list");
  if (!list || !pickerState) return;
  list.innerHTML = "";
  if (!pickerState.items.length) {
    list.appendChild(el("div", "panel-empty", "暂无选项"));
    return;
  }
  pickerState.items.forEach((item, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "picker-item" + (i === pickerState.index ? " active" : "");
    b.dataset.index = String(i);
    let markClass = "mark";
    let mark = "○";
    if (item.current) {
      markClass = "mark current";
      mark = "✓";
    } else if (item.on === true) {
      markClass = "mark on";
      mark = "●";
    } else if (item.on === false) {
      markClass = "mark off";
      mark = "●";
    } else if (item.on === null || item.on === "mixed") {
      markClass = "mark mixed";
      mark = "●";
    } else if (item.ready === true) {
      markClass = "mark on";
      mark = "●";
    } else if (item.ready === false) {
      markClass = "mark off";
      mark = "●";
    }
    b.innerHTML =
      `<span class="${markClass}">${mark}</span>` +
      `<span class="body"><div class="label">${escapeHtml(item.label || item.id || "")}</div>` +
      (item.hint ? `<div class="hint">${escapeHtml(item.hint)}</div>` : "") +
      `</span>`;
    b.addEventListener("click", () => confirmPicker(i));
    b.addEventListener("mouseenter", () => {
      if (!pickerState || pickerState.index === i) return;
      pickerState.index = i;
      list.querySelectorAll(".picker-item").forEach((n, j) => {
        n.classList.toggle("active", j === i);
      });
    });
    list.appendChild(b);
  });
  const active = list.querySelector(".picker-item.active");
  if (active) active.scrollIntoView({ block: "nearest" });
}

function openPicker(ev) {
  showDesk();
  const items = Array.isArray(ev.items) ? ev.items : [];
  let index = items.findIndex((x) => x.current);
  if (index < 0) index = 0;
  pickerState = {
    kind: ev.kind || "session",
    items,
    index,
    parent: ev.parent || null,
    back: ev.back || null,
  };
  $("#picker-title").textContent = ev.title || "选择";
  $("#picker-hint").textContent = ev.hint || "选择一项 · Esc 关闭";
  renderPickerList();
  const root = $("#picker");
  root.classList.remove("hidden");
  root.setAttribute("aria-hidden", "false");
}

async function pickerApi(body) {
  const res = await fetch("/api/picker", {
    method: "POST",
    headers: apiHeaders(),
    body: JSON.stringify(body),
  });
  let data;
  try {
    data = await res.json();
  } catch {
    data = { ok: false, error: `HTTP ${res.status}` };
  }
  if (data && data.picker) {
    closeFeature();
    closeAbout();
    closePalette();
    openPicker(data.picker);
  } else if (data && data.closed) {
    closePicker();
  } else if (data && data.ok === false && data.error) {
    const st = $("#status-text");
    if (st) st.textContent = data.error;
  }
  return data;
}

async function cancelPicker() {
  if (!pickerState) return;
  const back = pickerState.back;
  if (back) {
    await pickerApi({ op: "back", back, kind: pickerState.kind });
    return;
  }
  closePicker();
}

async function confirmPicker(index) {
  if (!pickerState) return;
  const i = index != null ? index : pickerState.index;
  const item = pickerState.items[i];
  if (!item) return;
  const kind = pickerState.kind;
  const id = item.id || "";
  const parent = pickerState.parent;

  // Nested capability pickers → stay in modal, server re-emits picker
  if (
    kind === "tools-cat" ||
    kind === "tools" ||
    kind === "skills-cat" ||
    kind === "skills" ||
    kind === "mcp-srv" ||
    kind === "mcp"
  ) {
    await pickerApi({ op: "pick", kind, id, parent: parent || "" });
    return;
  }

  closePicker();
  if (kind === "session") {
    if (id === "__new__") send("/new");
    else if (id) send("/resume " + id);
    return;
  }
  if (kind === "model" && id) {
    send("/model " + id);
    return;
  }
  if (kind === "effort" && id) {
    send("/effort " + id);
  }
}

function handleEvent(ev) {
  const t = ev.type;
  if (t === "hello") return;
  if (t === "turn_reset") {
    resetRailTurn();
    if (window.AgentTeamUI) AgentTeamUI.reset();
    return;
  }
  if (t === "agent_team") {
    if (window.AgentTeamUI) AgentTeamUI.handle(ev);
    return;
  }

  if (t === "startup") {
    // 启动事件只更新元数据；欢迎页未点进工作台前不跳转
    if (ev.session_id) {
      currentSessionId = ev.session_id;
      metaEl.textContent = `Session ${ev.session_id}`;
    } else if (ev.session_title) {
      metaEl.textContent = `Session · ${ev.session_title}`;
    }
    if (Array.isArray(ev.messages)) {
      pendingMessages = ev.messages;
      if (bootstrap) bootstrap.messages = ev.messages;
    }
    if (enteredDesk) {
      if (ev.clear || Array.isArray(ev.messages)) {
        hydrateMessages(ev.messages || []);
      }
    }
    refreshSessions();
    return;
  }
  if (t === "prefs") {
    applyPrefs(ev);
    return;
  }
  if (t === "sessions_list") {
    const q = ($("#session-search") && $("#session-search").value.trim()) || "";
    if (q) {
      refreshSessions(q);
    } else {
      renderSessionList(ev.items || [], ev.current_id);
    }
    return;
  }
  if (t === "picker") {
    closePalette();
    openPicker(ev);
    return;
  }
  if (t === "sessions") {
    // Legacy shape → same secondary page
    const rows = [
      { id: "__new__", label: "新建对话", hint: "清空上下文", current: false },
    ];
    (ev.items || []).forEach((s) => {
      rows.push({
        id: s.id,
        label: s.title || s.id,
        hint: `${s.id || ""} · ${s.messages != null ? s.messages : s.message_count || 0} 条`,
        current: false,
      });
    });
    openPicker({
      kind: "session",
      title: "Session",
      hint: "选择要进入的对话 · Esc 关闭",
      items: rows,
    });
    return;
  }
  if (t === "help") {
    showDesk();
    addHelp(ev.items || []);
    return;
  }
  if (t === "user") {
    addUser(ev.text);
    return;
  }
  if (t === "line") {
    const text = ev.text || "";
    const cls = ev.classes || "";
    if (document.body.classList.contains("team-run-active")) {
      if (window.AgentTeamUI && AgentTeamUI.routeLine && AgentTeamUI.routeLine(text)) return;
      if (
        cls.includes("line-err") ||
        cls.includes("line-hook") ||
        (cls.includes("line-warn") && /LLM|流式|chunked/i.test(text))
      ) {
        if (window.AgentTeamUI && AgentTeamUI.routeExternalError && AgentTeamUI.routeExternalError(text, cls)) {
          return;
        }
        return;
      }
    }
    if (window.AgentTeamUI && AgentTeamUI.routeLine && AgentTeamUI.routeLine(text)) return;
    if (text.includes("\n") || text.length > 120) addPanel("信息", text);
    else addLine(text);
    return;
  }
  if (t === "rule") {
    addLine(ev.title || "——");
    return;
  }
  if (t === "think_begin") {
    setActivity("思考中…");
    updateRailThink("思考中…");
    const thinkFold = $("#rail-think-fold");
    if (thinkFold) thinkFold.open = true;
    liveThink = addFold({
      kind: "think",
      icon: "◇",
      title: "思考",
      meta: "流式中 · 点击收起",
      body: "▌",
      open: true,
    });
    return;
  }
  if (t === "think_delta") {
    const text = ev.text || "";
    updateRailThink(text);
    setActivity(`思考中… ${text.length} 字`);
    if (!liveThink) {
      liveThink = addFold({
        kind: "think",
        icon: "◇",
        title: "思考",
        meta: `${text.length} 字`,
        body: text + "▌",
        open: true,
      });
    } else {
      liveThink.content.textContent = text + "▌";
      if (liveThink.meta) liveThink.meta.textContent = `${text.length} 字 · 点击收起`;
    }
    scrollChat();
    return;
  }
  if (t === "think_end") {
    clearActivity();
    const text = ev.text || "";
    const chars = ev.chars || text.length;
    updateRailThink(text || "(无思考内容)");
    if (!liveThink) {
      addFold({
        kind: "think",
        icon: "◇",
        title: "思考",
        meta: `${chars} 字 · 点击展开`,
        body: text || "(无思考内容)",
        open: false,
      });
    } else {
      liveThink.content.textContent = text || "(无思考内容)";
      if (liveThink.name) liveThink.name.textContent = "思考";
      if (liveThink.meta) liveThink.meta.textContent = `${chars} 字 · 点击展开`;
      liveThink.wrap.classList.remove("open");
    }
    liveThink = null;
    return;
  }
  if (t === "reply_begin") {
    clearActivity();
    liveReply = addAssistant("", { streaming: true });
    return;
  }
  if (t === "reply_delta" && liveReply) {
    const text = ev.text || "";
    liveReply.body.textContent = text + "▌";
    scrollChat();
    return;
  }
  if (t === "reply_end") {
    const text = ev.text || "";
    if (!liveReply) liveReply = addAssistant(text);
    else {
      liveReply.box.classList.remove("streaming");
      liveReply.body.innerHTML = renderMd(text);
    }
    liveReply = null;
    return;
  }
  if (t === "reply_cancel") {
    if (liveReply) {
      liveReply.box.remove();
      liveReply = null;
    }
    return;
  }
  if (t === "reply") {
    clearActivity();
    addAssistant(ev.text || "");
    return;
  }
  if (t === "tool_call") {
    clearActivity();
    pushRailTool(ev.name || "tool", String(ev.args || "").slice(0, 80), "run");
    const toolsFold = $("#rail-tools-fold");
    if (toolsFold) toolsFold.open = true;
    addFold({
      kind: "tool",
      icon: "⚙",
      title: "工具 · " + (ev.name || "tool"),
      meta: "参数 · 点击展开",
      body: prettyToolText(ev.args),
      open: false,
    });
    return;
  }
  if (t === "tool_result") {
    clearActivity();
    const preview = String(ev.text || "").replace(/\s+/g, " ").slice(0, 80);
    pushRailTool("结果 · " + (ev.name || "result"), preview, "ok");
    const chars = (ev.text || "").length;
    addFold({
      kind: "tool",
      icon: "↩",
      title: "结果 · " + (ev.name || "result"),
      meta: `${chars} 字 · 点击展开`,
      body: prettyToolText(ev.text),
      open: false,
    });
    return;
  }
  if (t === "activity") {
    if (ev.busy === false || !ev.text) clearActivity();
    else setActivity(ev.text);
    return;
  }
  if (t === "busy") {
    setBusy(!!ev.busy, ev.text || (ev.busy ? "运行中…" : "就绪"));
    if (ev.busy) setActivity(ev.text || "处理中…");
    else clearActivity();
    return;
  }
  if (t === "status") {
    if (ev.text) statusText.textContent = ev.text;
    return;
  }
  if (t === "context") {
    updateCtx(ev.usage);
    return;
  }
  if (t === "round") {
    statusText.textContent = `第 ${ev.index} 轮…`;
    setActivity(`第 ${ev.index} 轮 · 连接模型…`);
    const round = $("#rail-round");
    if (round) round.textContent = `round ${ev.index}`;
    return;
  }
  if (t === "error") {
    addError(ev.text || "error");
    setBusy(false);
    return;
  }
  if (t === "aborted") {
    addLine("已中止本轮");
    setBusy(false);
    return;
  }
  if (t === "reexec") {
    statusText.textContent = "切换界面并重启…请刷新页面";
    addLine("界面模式已切换，进程正在重启。若未自动打开，请重新运行 atrading --web 并刷新。");
    return;
  }
  if (t === "shutdown") {
    statusText.textContent = "Atrading 已退出";
    addLine("Atrading 已退出，可关闭此页面。");
    setBusy(false);
    return;
  }
  if (t === "list_folds") {
    const folds = [...document.querySelectorAll(".fold")];
    if (!folds.length) {
      addLine("暂无折叠卡片");
      return;
    }
    folds.forEach((n, i) => {
      const name = n.querySelector(".name");
      addLine(`[${folds.length - i}] ${name ? name.textContent : "fold"}`);
    });
    addLine("发送 /e 或 /e 2 展开对应项");
    return;
  }
  if (t === "expand_slot") {
    const folds = [...document.querySelectorAll(".fold")];
    const slot = Math.max(1, Number(ev.slot) || 1);
    const idx = folds.length - slot;
    if (idx < 0 || idx >= folds.length) {
      addLine(`无效序号 [${slot}]`);
      return;
    }
    folds[idx].classList.add("open");
    folds[idx].scrollIntoView({ block: "nearest" });
    return;
  }
  if (t === "expand_all") {
    document.querySelectorAll(".fold").forEach((n) => n.classList.add("open"));
  }
}

function buildTape(items, nowLabel) {
  const track = $("#tape-inner");
  const bits = [];
  const push = (html) => bits.push(html);
  for (const it of items || []) {
    const dir = it.direction || "flat";
    const pct = it.pct == null ? "—" : `${it.pct > 0 ? "+" : ""}${Number(it.pct).toFixed(2)}%`;
    const price = it.price == null ? "" : Number(it.price).toLocaleString("zh-CN");
    const arrow = dir === "up" ? "▲" : dir === "down" ? "▼" : "·";
    push(`<span><b>${escapeHtml(it.name)}</b> <span class="${dir}">${price} ${arrow}${pct}</span></span>`);
  }
  if (nowLabel) push(`<span><b>Atrading</b> <span class="flat">${escapeHtml(nowLabel)}</span></span>`);
  if (!bits.length) {
    push(`<span><b>Atrading</b> <span class="flat">DESK OPEN</span></span>`);
  }
  const row = bits.join("   ▌  ");
  track.innerHTML = `${row}   ▌  ${row}`;
  renderRailPulse(items || []);
}

async function refreshTicker() {
  try {
    const res = await fetch("/api/ticker");
    const data = await res.json();
    buildTape(data.items || [], bootstrap && bootstrap.now);
  } catch (_) {
    buildTape([], bootstrap && bootstrap.now);
  }
}

/* ——— Right rail ——— */
let railTools = [];
let railLimitBusy = false;
/** Shared limit-board snapshot for rail + feature panel (same fetch). */
let sharedLimitBoard = null;

function applySharedLimitBoard(payload) {
  const d = (payload && payload.data) || payload || {};
  sharedLimitBoard = {
    data: d,
    as_of: (payload && payload.as_of) || "",
    quality: (payload && payload.quality) || "",
    source: (payload && payload.source) || "",
    note: (payload && payload.note) || "",
  };
  return sharedLimitBoard;
}

function limitBoardMetaLine(snap) {
  if (!snap) return "";
  const d = snap.data || {};
  const up = d.limit_up_count != null ? d.limit_up_count : (d.limit_up || []).length;
  const broken =
    d.broken_board_count != null
      ? d.broken_board_count
      : (d.broken_board || []).length;
  const down =
    d.limit_down_count != null
      ? d.limit_down_count
      : (d.limit_down || []).length;
  const parts = [
    `涨停 ${up}`,
    `炸板 ${broken}`,
    `跌停 ${down}`,
    d.date ? `日期 ${d.date}` : "",
    snap.quality,
    snap.source,
    snap.as_of,
    snap.note,
  ];
  if (d.pool_errors && typeof d.pool_errors === "object") {
    const pe = Object.entries(d.pool_errors)
      .map(([k, v]) => `${k}:${v}`)
      .join(", ");
    if (pe) parts.push(`池异常 ${pe}`);
  }
  return parts.filter(Boolean).join(" · ");
}

function renderRailLimitFromShared() {
  const box = $("#rail-limit");
  if (!box || !sharedLimitBoard) return;
  const d = sharedLimitBoard.data || {};
  const up = d.limit_up_count != null ? d.limit_up_count : (d.limit_up || []).length;
  const broken =
    d.broken_board_count != null
      ? d.broken_board_count
      : (d.broken_board || []).length;
  const down =
    d.limit_down_count != null
      ? d.limit_down_count
      : (d.limit_down || []).length;
  const names = (d.limit_up || []).slice(0, 8);
  const rows = names
    .map((s) => {
      const pct = s.change_pct;
      const pctStr =
        pct == null
          ? "—"
          : `${pct > 0 ? "+" : ""}${Number(pct).toFixed(2)}%`;
      const board =
        s.consecutive_days != null && s.consecutive_days !== ""
          ? `${s.consecutive_days}板`
          : "";
      const sector = s.sector ? String(s.sector) : "";
      const extra = [board, sector].filter(Boolean).join(" · ");
      return `<button type="button" class="name-row" data-code="${escapeHtml(
        s.code || ""
      )}" data-name="${escapeHtml(s.name || "")}">
        <span class="code">${escapeHtml(s.code || "")}</span>
        <span class="nm">${escapeHtml(s.name || "")}</span>
        <span class="extra">${escapeHtml(extra)}</span>
        <span class="pct up">${escapeHtml(pctStr)}</span>
      </button>`;
    })
    .join("");
  box.innerHTML = `
    <div class="counts">
      <div class="count up"><div class="n">${up}</div><div class="k">涨停</div></div>
      <div class="count broken"><div class="n">${broken}</div><div class="k">炸板</div></div>
      <div class="count down"><div class="n">${down}</div><div class="k">跌停</div></div>
    </div>
    <div class="names">${rows || `<div class="rail-empty">暂无涨停明细</div>`}</div>
    <div class="rail-note">${escapeHtml(limitBoardMetaLine(sharedLimitBoard))}</div>
  `;
  box.dataset.loaded = "1";
  box.querySelectorAll(".name-row").forEach((btnRow) => {
    btnRow.addEventListener("click", () => {
      const code = btnRow.dataset.code;
      const name = btnRow.dataset.name;
      if (!code) return;
      showDesk();
      send(`帮我分析 ${code} ${name || ""}`.trim());
    });
  });
}

async function refreshRailLimit() {
  const box = $("#rail-limit");
  if (!box) return;
  if (railLimitBusy) return;
  railLimitBusy = true;
  const btn = $("#rail-limit-refresh");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "…";
  }
  box.innerHTML = `<div class="rail-empty">拉取中…</div>`;
  try {
    const date =
      (bootstrap && bootstrap.latest_trade_date) ||
      (typeof defaultEndDate === "function" ? defaultEndDate() : "");
    const res = await fetch("/api/tool", {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({
        name: "get_limit_board",
        args: { date: date || "" },
      }),
    });
    const data = await res.json();
    if (res.status === 404) {
      box.innerHTML = `<div class="rail-empty">接口未就绪，请重启服务</div>`;
      return;
    }
    if (!data.ok || (data.result && data.result.ok === false)) {
      const err =
        (data.result && data.result.error) || data.error || "加载失败";
      box.innerHTML = `<div class="rail-empty">${escapeHtml(err)}</div>`;
      return;
    }
    applySharedLimitBoard(data.result || {});
    renderRailLimitFromShared();
  } catch (err) {
    box.innerHTML = `<div class="rail-empty">${escapeHtml(String(err))}</div>`;
  } finally {
    railLimitBusy = false;
    if (btn) {
      btn.disabled = false;
      btn.textContent = "刷新";
    }
  }
}

function setRailOpen(on) {
  document.body.classList.toggle("rail-panel-open", !!on);
}

function updateRailActivity(text, live) {
  const elAct = $("#rail-activity");
  if (!elAct) return;
  elAct.textContent = text || (live ? "运行中…" : "就绪");
  elAct.classList.toggle("live", !!live);
}

function updateRailThink(text) {
  const body = $("#rail-think-body");
  if (!body) return;
  const t = (text || "").trim();
  if (!t) {
    body.textContent = "—";
    return;
  }
  body.textContent = t.length > 280 ? t.slice(0, 120) + "…" + t.slice(-140) : t;
}

function renderRailTools() {
  const box = $("#rail-tools");
  if (!box) return;
  if (!railTools.length) {
    box.innerHTML = `<div class="rail-empty">尚无工具调用</div>`;
    return;
  }
  box.innerHTML = railTools
    .slice()
    .reverse()
    .map((t) => {
      const cls = t.status === "ok" ? "ok" : t.status === "err" ? "err" : "";
      return `<div class="tool-item ${cls}">
        <div class="t-name">${escapeHtml(t.name)}</div>
        <div class="t-meta">${escapeHtml(t.meta || "")}</div>
      </div>`;
    })
    .join("");
}

function pushRailTool(name, meta, status) {
  railTools.push({
    name: name || "tool",
    meta: (meta || "").replace(/\s+/g, " ").slice(0, 80),
    status: status || "run",
  });
  if (railTools.length > 16) railTools = railTools.slice(-16);
  renderRailTools();
}

function resetRailTurn() {
  railTools = [];
  renderRailTools();
  updateRailThink("");
  const round = $("#rail-round");
  if (round) round.textContent = "";
  if (!busy) updateRailActivity("就绪", false);
}

function renderRailPulse(items) {
  const box = $("#rail-pulse");
  if (!box) return;
  const dateEl = $("#rail-trade-date");
  if (dateEl) {
    const d = (bootstrap && bootstrap.latest_trade_date) || "";
    dateEl.textContent = d ? `最近交易日 ${d}` : "";
  }
  if (!items || !items.length) {
    box.innerHTML = `<div class="rail-empty">暂无指数</div>`;
    return;
  }
  box.innerHTML = items
    .map((it) => {
      const dir = it.direction || "flat";
      const pct =
        it.pct == null
          ? "—"
          : `${it.pct > 0 ? "+" : ""}${Number(it.pct).toFixed(2)}%`;
      const price = it.price == null ? "—" : Number(it.price).toLocaleString("zh-CN");
      return `<button type="button" class="idx" data-name="${escapeHtml(it.name || "")}" data-code="${escapeHtml(it.code || "")}">
        <span class="name">${escapeHtml(it.name || "")}</span>
        <span class="price">${escapeHtml(price)}</span>
        <span class="pct ${dir}">${escapeHtml(pct)}</span>
      </button>`;
    })
    .join("");
  box.querySelectorAll(".idx").forEach((btn) => {
    btn.addEventListener("click", () => {
      const name = btn.dataset.name || "";
      const code = btn.dataset.code || "";
      showDesk();
      send(`简要解读指数 ${name}${code ? "（" + code + "）" : ""} 今日表现`);
    });
  });
}

function wireRail() {
  const openBtn = $("#rail-open");
  const closeBtn = $("#rail-close");
  if (openBtn) openBtn.addEventListener("click", () => setRailOpen(true));
  if (closeBtn) closeBtn.addEventListener("click", () => setRailOpen(false));
  const abortRail = $("#rail-abort");
  if (abortRail) abortRail.addEventListener("click", () => abort());
  const limOpen = $("#rail-limit-open");
  if (limOpen) {
    limOpen.addEventListener("click", () => {
      setRailOpen(false);
      openFeature("limit", { reuseShared: true });
    });
  }
  const limRef = $("#rail-limit-refresh");
  if (limRef) limRef.addEventListener("click", () => refreshRailLimit());

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!document.body.classList.contains("agent-panel-open")) return;
    if (window.AgentTeamUI && AgentTeamUI.closeAgent) {
      e.preventDefault();
      AgentTeamUI.closeAgent();
    }
  });
}

function closeAbout() {
  const root = $("#about");
  if (!root) return;
  root.classList.add("hidden");
  root.setAttribute("aria-hidden", "true");
}

function openAbout() {
  setSidebarOpen(false);
  setRailOpen(false);
  closePalette();
  closePicker();
  closeFeature();
  const root = $("#about");
  const body = $("#about-body");
  if (!root || !body) return;
  const name = (bootstrap && bootstrap.app) || "Atrading";
  const tag =
    (bootstrap && (bootstrap.tagline_zh || bootstrap.tagline)) ||
    "A股投研助手 · 行情 · 回测 · 复盘";
  body.innerHTML = `
    <p><b style="color:var(--text-primary)">${escapeHtml(name)}</b> — ${escapeHtml(tag)}</p>
    <p>用自然语言做 A 股投研：查行情、筛票、看涨停、跑回测、解读结果。中间是对话；左侧是导航与快捷工具；右侧是市场脉搏与本轮运行状态。</p>

    <h3>整体能做什么</h3>
    <ul>
      <li>行情与排行：涨跌榜、成交额、估值等全市场筛选</li>
      <li>情绪复盘：涨停 / 炸板 / 跌停池，连板与封板时间</li>
      <li>基本面选股：PE / PB / ROE / 市值等条件筛选</li>
      <li>策略回测：均线、RSI、动量、买入持有等内置策略</li>
      <li>研报、资金流、龙虎榜、因子、产业链等（对话里按需调用 60+ 工具）</li>
    </ul>

    <h3>怎么用这个界面</h3>
    <ul>
      <li><b style="color:var(--text-primary)">中间</b>：直接提问；输入 <kbd>/</kbd> 看斜杠命令；<kbd>Ctrl+P</kbd> 打开命令面板</li>
      <li><b style="color:var(--text-primary)">左侧快捷</b>：涨跌榜 / 涨停板 / 选股 / 回测 — 不经过 AI，立刻出表</li>
      <li><b style="color:var(--text-primary)">左侧功能</b>：模型 / 思考强度可切换多厂商（DeepSeek、GLM、Kimi、Grok、GPT、Gemini、Claude）</li>
      <li><b style="color:var(--text-primary)">左侧对话</b>：可搜索标题或聊天内容，点一下恢复会话</li>
      <li><b style="color:var(--text-primary)">右侧检视</b>：指数脉搏、涨停速览、本轮工具与思考摘要；忙时可点中止</li>
    </ul>

    <h3>试试这样问</h3>
    <button type="button" class="ask" data-q="今天市场情绪怎么样，涨停多吗？">今天市场情绪怎么样，涨停多吗？</button>
    <button type="button" class="ask" data-q="帮我找出 PE&lt;20、ROE&gt;15% 的股票">帮我找出 PE&lt;20、ROE&gt;15% 的股票</button>
    <button type="button" class="ask" data-q="用均线交叉回测 600519.SH 近一年">用均线交叉回测 600519.SH 近一年</button>
    <button type="button" class="ask" data-q="茅台最近资金流向和研报观点？">茅台最近资金流向和研报观点？</button>

    <h3>常用命令</h3>
    <ul>
      <li><kbd>/new</kbd> 新建对话 · <kbd>/sessions</kbd> 切换会话</li>
      <li><kbd>/model</kbd> 换模型 · <kbd>/effort</kbd> 思考强度</li>
      <li><kbd>/tools</kbd> · <kbd>/skills</kbd> · <kbd>/mcp</kbd> 管理能力开关</li>
    </ul>
    <p style="color:var(--text-muted);font-size:12px;margin-top:10px">研究辅助，不构成投资建议；不连接券商实盘。</p>
  `;
  body.querySelectorAll(".ask").forEach((btn) => {
    btn.addEventListener("click", () => {
      const q = btn.dataset.q || btn.textContent;
      closeAbout();
      showDesk();
      send(q);
    });
  });
  root.classList.remove("hidden");
  root.setAttribute("aria-hidden", "false");
}

function wireAbout() {
  const closeBtn = $("#about-close");
  const backdrop = $("#about-backdrop");
  if (closeBtn) closeBtn.addEventListener("click", closeAbout);
  if (backdrop) backdrop.addEventListener("click", closeAbout);
  document.addEventListener("keydown", (e) => {
    const root = $("#about");
    if (!root || root.classList.contains("hidden")) return;
    if (e.key === "Escape") {
      e.preventDefault();
      closeAbout();
    }
  });
}

function showWelcome() {
  enteredDesk = false;
  welcome.classList.remove("hidden");
  app.classList.add("hidden");
}

function showDesk() {
  enteredDesk = true;
  welcome.classList.add("hidden");
  app.classList.remove("hidden");
  input.focus();
  if (!$("#rail-limit") || !$("#rail-limit").dataset.loaded) {
    refreshRailLimit().then(() => {
      const box = $("#rail-limit");
      if (box) box.dataset.loaded = "1";
    });
  }
}

function setSidebarOpen(on) {
  document.body.classList.toggle("sidebar-open", !!on);
}

function highlightActiveSession() {
  const list = $("#session-list");
  if (!list) return;
  list.querySelectorAll(".sb-session").forEach((n) => {
    n.classList.toggle("active", currentSessionId && n.dataset.id === currentSessionId);
  });
}

function renderSessionList(items, currentId) {
  sidebarSessions = items || [];
  if (currentId != null) currentSessionId = currentId;
  const list = $("#session-list");
  if (!list) return;
  list.innerHTML = "";
  const q = ($("#session-search") && $("#session-search").value.trim()) || "";
  if (!sidebarSessions.length) {
    list.appendChild(
      el("div", "sb-empty", q ? `无匹配「${q}」的对话` : "暂无对话记录")
    );
    return;
  }
  sidebarSessions.forEach((s) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "sb-session" + (currentSessionId && s.id === currentSessionId ? " active" : "");
    b.dataset.id = s.id || "";
    const ts = String(s.updated || "").slice(0, 16).replace("T", " ");
    b.innerHTML =
      `<div class="stitle">${escapeHtml(s.title || s.id || "")}</div>` +
      `<div class="smeta">${escapeHtml(s.id || "")} · ${escapeHtml(String(s.messages || 0))} 条` +
      (ts ? ` · ${escapeHtml(ts)}` : "") +
      `</div>` +
      (s.snippet
        ? `<div class="snippet">${escapeHtml(s.snippet)}</div>`
        : "");
    b.addEventListener("click", () => {
      if (!s.id || busy) return;
      setSidebarOpen(false);
      if (s.id === currentSessionId) return;
      send("/resume " + s.id);
    });
    list.appendChild(b);
  });
}

let sessionSearchTimer = null;

async function refreshSessions(query) {
  try {
    const q =
      query != null
        ? String(query)
        : (($("#session-search") && $("#session-search").value) || "");
    const url = q.trim()
      ? `/api/sessions?q=${encodeURIComponent(q.trim())}`
      : "/api/sessions";
    const res = await fetch(url);
    const data = await res.json();
    if (data && data.ok !== false) {
      renderSessionList(data.items || [], data.current_id);
    }
  } catch (_) {}
}

function wireSessionSearch() {
  const input = $("#session-search");
  if (!input) return;
  input.addEventListener("input", () => {
    if (sessionSearchTimer) clearTimeout(sessionSearchTimer);
    sessionSearchTimer = setTimeout(() => {
      refreshSessions(input.value);
    }, 220);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (input.value) {
        e.preventDefault();
        input.value = "";
        refreshSessions("");
      }
    }
  });
}

async function runAction(action) {
  showDesk();
  setSidebarOpen(false);
  closePalette();
  switch (action) {
    case "new":
      await send("/new");
      break;
    case "model":
      await pickerApi({ op: "open", kind: "model" });
      break;
    case "effort":
      await pickerApi({ op: "open", kind: "effort" });
      break;
    case "tools":
      await pickerApi({ op: "open", kind: "tools-cat" });
      break;
    case "skills":
      await pickerApi({ op: "open", kind: "skills-cat" });
      break;
    case "mcp":
      await pickerApi({ op: "open", kind: "mcp-srv" });
      break;
    case "sessions":
      await pickerApi({ op: "open", kind: "session" });
      break;
    case "palette":
      openPalette();
      break;
    case "about":
      openAbout();
      break;
    case "help":
      await send("/help");
      break;
    case "reload":
      await send("/reload");
      break;
    case "reload_comp":
      await send("/reload_comp");
      break;
    case "thinking":
      await send("/thinking");
      break;
    case "verbose":
      await send("/verbose");
      break;
    case "ui":
      await send("/ui");
      break;
    default:
      break;
  }
}

const PALETTE_ITEMS = [
  { id: "sessions", label: "切换 Session", hint: "次级界面选择 / 新建对话", key: "/sessions" },
  { id: "model", label: "切换模型", hint: "DeepSeek V4 / GLM-5 / Kimi K3 / GPT-5.6…", key: "/model" },
  { id: "effort", label: "切换思考强度", hint: "High / Max / 关闭", key: "/effort" },
  { id: "tools", label: "管理工具", hint: "分类开关工具", key: "/tools" },
  { id: "skills", label: "管理 Skills", hint: "分类开关 Skills", key: "/skills" },
  { id: "mcp", label: "管理 MCP", hint: "Server / 工具开关", key: "/mcp" },
  { id: "new", label: "新建对话", hint: "清空上下文", key: "/new" },
  { id: "about", label: "产品介绍", hint: "能做什么 · 怎么用", key: "介绍" },
  { id: "help", label: "斜杠命令", hint: "列出全部 / 指令", key: "/help" },
  { id: "thinking", label: "切换思考展开", hint: "思考过程展开/折叠", key: "/thinking" },
  { id: "verbose", label: "切换长内容展开", hint: "长内容默认展开/折叠", key: "/verbose" },
  { id: "reload", label: "重新扫描能力", hint: "skills / tools / mcp", key: "/reload" },
  { id: "reload_comp", label: "全面重启", hint: "退出进程并重新进入", key: "/reload_comp" },
  { id: "ui", label: "当前界面模式", hint: "查看 tui / plain / web", key: "/ui" },
];

function closePalette() {
  paletteState = null;
  const root = $("#palette");
  if (!root) return;
  root.classList.add("hidden");
  root.setAttribute("aria-hidden", "true");
}

function renderPaletteList() {
  const list = $("#palette-list");
  if (!list || !paletteState) return;
  list.innerHTML = "";
  const rows = paletteState.filtered;
  if (!rows.length) {
    list.appendChild(el("div", "sb-empty", "无匹配命令"));
    return;
  }
  rows.forEach((item, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "palette-item" + (i === paletteState.index ? " active" : "");
    b.innerHTML =
      `<span class="plabel">${escapeHtml(item.label)}</span>` +
      `<span class="pkey">${escapeHtml(item.key || "")}</span>` +
      `<span class="phint">${escapeHtml(item.hint || "")}</span>`;
    b.addEventListener("click", () => confirmPalette(i));
    b.addEventListener("mouseenter", () => {
      paletteState.index = i;
      list.querySelectorAll(".palette-item").forEach((n, j) => {
        n.classList.toggle("active", j === i);
      });
    });
    list.appendChild(b);
  });
  const active = list.querySelector(".palette-item.active");
  if (active) active.scrollIntoView({ block: "nearest" });
}

function filterPalette(q) {
  const needle = (q || "").trim().toLowerCase();
  if (!needle) return PALETTE_ITEMS.slice();
  return PALETTE_ITEMS.filter((it) => {
    const hay = `${it.label} ${it.hint} ${it.key} ${it.id}`.toLowerCase();
    return hay.includes(needle);
  });
}

function openPalette() {
  if (pickerState) closePicker();
  showDesk();
  paletteState = { filtered: PALETTE_ITEMS.slice(), index: 0 };
  const inputEl = $("#palette-input");
  inputEl.value = "";
  renderPaletteList();
  const root = $("#palette");
  root.classList.remove("hidden");
  root.setAttribute("aria-hidden", "false");
  setTimeout(() => inputEl.focus(), 0);
}

function confirmPalette(index) {
  if (!paletteState) return;
  const i = index != null ? index : paletteState.index;
  const item = paletteState.filtered[i];
  if (!item) return;
  closePalette();
  runAction(item.id);
}

function hydrateMessages(messages) {
  chat.innerHTML = "";
  for (const m of messages || []) {
    if (m.role === "user") {
      if (window.AgentTeamUI && AgentTeamUI.parseBrief && AgentTeamUI.parseBrief(m.content)) {
        continue;
      }
      addUser(m.content);
    } else if (m.role === "assistant") addAssistant(m.content);
  }
}

function applyBootstrap(data) {
  bootstrap = data;
  commands = data.commands || {};
  currentSessionId = data.session_id || null;
  const name = data.app || "Atrading";
  const nameEl = $("#app-name");
  if (nameEl) nameEl.textContent = name;
  $("#welcome-title").textContent = name;
  $("#welcome-sub").textContent = data.tagline_zh || data.tagline || "";
  metaEl.textContent = data.session_id
    ? `Session ${data.session_id}`
    : "Session · draft";
  modelLabel.textContent = data.model_label || data.model || "—";
  effortLabel.textContent = data.effort_label || data.effort || "—";
  renderSessionList(data.sessions || [], data.session_id);
  pendingMessages = data.messages || [];
  // 始终先停在欢迎页，由用户点「开始 / 继续 / 工作台」再进入
  showWelcome();
  setBusy(!!data.busy);
}

function filterSlash(q) {
  const query = (q || "").toLowerCase();
  const entries = Object.entries(commands);
  if (!query || query === "/") return entries.slice(0, 12);
  return entries
    .filter(([cmd, desc]) => cmd.startsWith(query) || desc.toLowerCase().includes(query.slice(1)))
    .slice(0, 12);
}

function renderSlash() {
  const val = input.value;
  if (!val.startsWith("/") || val.includes("\n")) {
    slashMenu.classList.add("hidden");
    slashItems = [];
    return;
  }
  const token = val.trim().split(/\s+/)[0];
  slashItems = filterSlash(token);
  if (!slashItems.length) {
    slashMenu.classList.add("hidden");
    return;
  }
  slashIndex = Math.min(slashIndex, slashItems.length - 1);
  slashMenu.innerHTML = "";
  slashItems.forEach(([cmd, desc], i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = i === slashIndex ? "active" : "";
    b.innerHTML = `<span class="cmd">${escapeHtml(cmd)}</span><span class="desc">${escapeHtml(desc)}</span>`;
    b.addEventListener("mousedown", (e) => {
      e.preventDefault();
      input.value = cmd + " ";
      slashMenu.classList.add("hidden");
      input.focus();
    });
    slashMenu.appendChild(b);
  });
  slashMenu.classList.remove("hidden");
}

async function send(text) {
  const raw = (text != null ? text : input.value).trim();
  const value = buildSendText(raw);
  if (!value || busy) return;
  showDesk();
  input.value = "";
  charCount.textContent = "";
  slashMenu.classList.add("hidden");
  setBusy(true, "发送中…");
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: apiHeaders(),
    body: JSON.stringify({ text: value }),
  });
  const data = await res.json();
  if (!data.ok) {
    setBusy(false, data.error || "失败");
    if (data.error === "busy") addLine("上一轮仍在进行");
  }
}

async function abort() {
  await fetch("/api/abort", {
    method: "POST",
    headers: apiHeaders(),
    body: "{}",
  });
}

function connectEvents() {
  const es = new EventSource("/api/events");
  es.onmessage = (e) => {
    try {
      handleEvent(JSON.parse(e.data));
    } catch (err) {
      console.error("sse event failed", err, e.data);
    }
  };
  es.onerror = () => {
    statusText.textContent = "重连中…";
  };
}

function wireWelcome() {
  const box = $("#examples");
  EXAMPLES.forEach((q) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = `“${q}”`;
    b.addEventListener("click", () => {
      setAgentMode("fast", { focus: false });
      send(q);
    });
    box.appendChild(b);
  });

  const welcomeLaunch = $("#agent-launch-welcome");
  if (welcomeLaunch) {
    welcomeLaunch.innerHTML = `<div class="agent-launch-title">点击启动 Agent 团队</div><div class="agent-launch-grid"></div>`;
    const grid = welcomeLaunch.querySelector(".agent-launch-grid");
    AGENT_MODES.forEach((m) => {
      if (m.id === "fast") return;
      const card = document.createElement("button");
      card.type = "button";
      card.className = "agent-launch-card";
      card.dataset.agentMode = m.id;
      card.innerHTML = `<div class="alc-title">${escapeHtml(m.label)}</div><div class="alc-desc">${escapeHtml(m.desc)}</div>`;
      card.addEventListener("click", () => {
        showDesk();
        setAgentMode(m.id);
        document.body.classList.add("rail-panel-open");
      });
      grid.appendChild(card);
    });
    AGENT_QUICK_LAUNCHES.forEach((item) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "agent-launch-quick";
      b.textContent = item.label;
      b.addEventListener("click", () => launchAgentQuery(item.mode, item.query));
      grid.appendChild(b);
    });
  }
  $("#btn-new").addEventListener("click", async () => {
    showDesk();
    await send("/new");
  });
  $("#btn-continue").addEventListener("click", () => {
    showDesk();
    const msgs =
      (bootstrap && bootstrap.messages && bootstrap.messages.length
        ? bootstrap.messages
        : pendingMessages) || [];
    if (msgs.length) {
      hydrateMessages(msgs);
    } else if (bootstrap && bootstrap.sessions && bootstrap.sessions.length) {
      const first = bootstrap.sessions[0];
      if (first && first.id) send("/resume " + first.id);
      else addLine("没有可继续的会话，直接提问即可");
    } else {
      addLine("没有可继续的会话，直接提问即可");
      input.focus();
    }
  });
  $("#btn-history").addEventListener("click", () => {
    showDesk();
    refreshSessions();
    input.focus();
  });
  const btnModel = $("#btn-model");
  if (btnModel) {
    btnModel.addEventListener("click", () => {
      showDesk();
      runAction("model");
    });
  }
  if (modelLabel) {
    modelLabel.addEventListener("click", () => runAction("model"));
  }
  if (effortLabel) {
    effortLabel.addEventListener("click", () => runAction("effort"));
  }
}

function wireAgentLauncher() {
  document.querySelectorAll("[data-agent-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const mode = btn.dataset.agentMode;
      if (!mode) return;
      showDesk();
      setAgentMode(mode);
      if (mode !== "fast") document.body.classList.add("rail-panel-open");
    });
  });

  const quick = $("#sb-agent-quick");
  if (quick) {
    quick.innerHTML = "";
    AGENT_QUICK_LAUNCHES.forEach((item) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "sb-agent-quick-btn";
      b.textContent = item.label;
      b.title = item.query;
      b.addEventListener("click", () => launchAgentQuery(item.mode, item.query));
      quick.appendChild(b);
    });
  }
}

function wireSidebar() {
  $("#sb-actions").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;
    runAction(btn.dataset.action);
  });
  const feats = $("#sb-features");
  if (feats) {
    feats.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-feature]");
      if (!btn) return;
      openFeature(btn.dataset.feature);
    });
  }
  $("#btn-refresh-sessions").addEventListener("click", () => refreshSessions());
  $("#sidebar-open").addEventListener("click", () => setSidebarOpen(true));
  $("#sidebar-close").addEventListener("click", () => setSidebarOpen(false));
  $("#btn-palette").addEventListener("click", () => openPalette());
}

function wireFeature() {
  $("#feature-close").addEventListener("click", closeFeature);
  $("#feature-backdrop").addEventListener("click", closeFeature);
  document.addEventListener("keydown", (e) => {
    if (!featureKind) return;
    if (pickerState || paletteState) return;
    if (e.key === "Escape") {
      e.preventDefault();
      closeFeature();
    }
  });
}

let featureKind = null;
let featureLimitTab = "limit_up";
let featureLimitCache = null;
let featureBacktestCache = null;

function ymdLocal(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function defaultEndDate() {
  return (bootstrap && bootstrap.latest_trade_date) || ymdLocal(new Date());
}

function defaultStartDate() {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 1);
  return ymdLocal(d);
}

function normalizeCodes(raw) {
  return String(raw || "")
    .split(/[,，\s]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean)
    .map((c) => {
      if (/^\d{6}$/.test(c)) {
        if (c.startsWith("6") || c.startsWith("9")) return `${c}.SH`;
        if (c.startsWith("0") || c.startsWith("3")) return `${c}.SZ`;
        if (c.startsWith("4") || c.startsWith("8")) return `${c}.BJ`;
      }
      return c;
    });
}

function syncBacktestParams() {
  const box = $("#f-bt-params");
  const strat = $("#f-strategy");
  if (!box || !strat) return;
  const s = strat.value;
  if (s === "ma_cross") {
    box.innerHTML = `
      <label>快线<input id="f-fast" type="number" min="2" max="60" value="5" /></label>
      <label>慢线<input id="f-slow" type="number" min="5" max="250" value="20" /></label>
    `;
  } else if (s === "rsi") {
    box.innerHTML = `
      <label>周期<input id="f-period" type="number" min="5" max="60" value="14" /></label>
      <label>超卖<input id="f-oversold" type="number" min="5" max="40" value="30" /></label>
      <label>超买<input id="f-overbought" type="number" min="60" max="95" value="70" /></label>
    `;
  } else if (s === "momentum") {
    box.innerHTML = `
      <label>窗口<input id="f-window" type="number" min="5" max="120" value="20" /></label>
    `;
  } else {
    box.innerHTML = "";
  }
}

function closeFeature() {
  featureKind = null;
  featureLimitCache = null;
  featureBacktestCache = null;
  const root = $("#feature");
  if (!root) return;
  root.classList.add("hidden");
  root.removeAttribute("data-kind");
  root.setAttribute("aria-hidden", "true");
}

function openFeature(kind, opts) {
  setSidebarOpen(false);
  closePalette();
  closePicker();
  featureKind = kind;
  featureLimitTab = "limit_up";
  featureLimitCache = null;
  featureBacktestCache = null;
  const root = $("#feature");
  root.setAttribute("data-kind", kind);
  const title = $("#feature-title");
  const form = $("#feature-form");
  const meta = $("#feature-meta");
  const result = $("#feature-result");
  meta.textContent = "";
  result.innerHTML = `<div class="empty">配置条件后点击「查询」</div>`;
  const reuseShared = !!(opts && opts.reuseShared);

  if (kind === "screen") {
    title.textContent = "涨跌榜";
    form.innerHTML = `
      <label>排序
        <select id="f-sort">
          <option value="change_pct">涨跌幅</option>
          <option value="amount">成交额</option>
          <option value="volume">成交量</option>
          <option value="turnover">换手率</option>
          <option value="market_cap">总市值</option>
          <option value="pe">市盈率</option>
          <option value="pb">市净率</option>
        </select>
      </label>
      <label>方向
        <select id="f-asc">
          <option value="0">降序（涨幅榜）</option>
          <option value="1">升序（跌幅榜）</option>
        </select>
      </label>
      <label>条数
        <input id="f-topn" type="number" min="5" max="100" value="30" />
      </label>
      <button type="button" class="run" id="f-run">查询</button>
    `;
  } else if (kind === "limit") {
    title.textContent = "涨停板";
    const defaultDate =
      (bootstrap && bootstrap.latest_trade_date) || defaultEndDate();
    form.innerHTML = `
      <label>交易日
        <span class="feature-date-row">
          <input id="f-date" type="date" value="${escapeHtml(defaultDate)}" />
          <button type="button" class="ghost" id="f-date-latest" title="用最近交易日">最新</button>
        </span>
      </label>
      <button type="button" class="run" id="f-run">查询</button>
    `;
  } else if (kind === "fund") {
    title.textContent = "基本面选股";
    form.innerHTML = `
      <label>最高PE
        <input id="f-maxpe" type="number" step="0.1" value="20" />
      </label>
      <label>最高PB
        <input id="f-maxpb" type="number" step="0.1" value="3" />
      </label>
      <label>最低ROE%
        <input id="f-minroe" type="number" step="0.1" value="10" />
      </label>
      <label>最低市值(亿)
        <input id="f-mincap" type="number" step="1" placeholder="可选" />
      </label>
      <label>最低股息%
        <input id="f-mindy" type="number" step="0.1" placeholder="可选" />
      </label>
      <label>排序
        <select id="f-fund-sort">
          <option value="pe">PE</option>
          <option value="pb">PB</option>
          <option value="roe">ROE</option>
          <option value="market_cap">市值</option>
          <option value="dividend_yield">股息率</option>
        </select>
      </label>
      <label>条数
        <input id="f-topn" type="number" min="5" max="100" value="30" />
      </label>
      <label class="chk"><input id="f-exst" type="checkbox" checked /> 排除 ST</label>
      <button type="button" class="run" id="f-run">查询</button>
    `;
  } else if (kind === "backtest") {
    title.textContent = "策略回测";
    form.innerHTML = `
      <label class="wide">代码（逗号分隔）
        <input id="f-codes" type="text" value="600519.SH" placeholder="600519.SH,000001.SZ" />
      </label>
      <label>开始
        <input id="f-start" type="date" value="${escapeHtml(defaultStartDate())}" />
      </label>
      <label>结束
        <input id="f-end" type="date" value="${escapeHtml(defaultEndDate())}" />
      </label>
      <label>策略
        <select id="f-strategy">
          <option value="ma_cross">均线交叉</option>
          <option value="rsi">RSI</option>
          <option value="momentum">动量</option>
          <option value="buy_hold">买入持有</option>
        </select>
      </label>
      <span id="f-bt-params" class="feature-date-row"></span>
      <label>初始资金
        <input id="f-cash" type="number" min="10000" step="10000" value="1000000" />
      </label>
      <details class="feature-adv" id="f-bt-adv">
        <summary>高级参数（A股约束 / 成本 / 对冲）</summary>
        <div class="feature-adv-grid">
          <label>信号延迟
            <select id="f-lag">
              <option value="1" selected>T+1 开盘（推荐）</option>
              <option value="0">同 bar（易前视）</option>
            </select>
          </label>
          <label>成交价
            <select id="f-exec">
              <option value="open" selected>开盘</option>
              <option value="close">收盘</option>
            </select>
          </label>
          <label>佣金
            <input id="f-comm" type="number" step="0.0001" value="0.0003" />
          </label>
          <label>印花税
            <input id="f-stamp" type="number" step="0.0001" value="0.0005" />
          </label>
          <label>闲置资金年化
            <input id="f-cashrate" type="number" step="0.001" value="0" placeholder="0.015≈货基" />
          </label>
          <label class="chk"><input id="f-limitlock" type="checkbox" checked /> 涨跌停拒单</label>
          <label class="chk"><input id="f-impact" type="checkbox" checked /> 冲击成本模型</label>
          <label class="chk"><input id="f-halt" type="checkbox" checked /> 跳过停牌</label>
          <label class="chk"><input id="f-hedge" type="checkbox" /> 股指期货对冲</label>
          <label>对冲品种
            <select id="f-hedge-sym">
              <option value="IF">IF 沪深300</option>
              <option value="IC">IC 中证500</option>
              <option value="IM">IM 中证1000</option>
              <option value="IH">IH 上证50</option>
            </select>
          </label>
          <label>对冲比例
            <input id="f-hedge-ratio" type="number" step="0.1" min="0" max="2" value="1" />
          </label>
          <label class="wide">期货合约（对冲必填）
            <input id="f-fut" type="text" placeholder="如 IF2506" />
          </label>
        </div>
      </details>
      <button type="button" class="run" id="f-run">运行</button>
    `;
  } else if (kind === "team") {
    if (window.AgentTeamUI) {
      window.AgentTeamUI.openTeam();
    }
    return;
  } else if (kind === "evals") {
    if (window.AgentTeamUI) {
      window.AgentTeamUI.openEvals();
    }
    return;
  } else {
    return;
  }

  root.classList.remove("hidden");
  root.setAttribute("aria-hidden", "false");
  $("#f-run").addEventListener("click", () => runFeature());
  const latestBtn = $("#f-date-latest");
  if (latestBtn) {
    latestBtn.addEventListener("click", () => {
      const el = $("#f-date");
      if (!el) return;
      el.value = defaultEndDate();
    });
  }
  const strat = $("#f-strategy");
  if (strat) {
    syncBacktestParams();
    strat.addEventListener("change", syncBacktestParams);
  }
  // 右侧栏「完整」复用同一快照，避免二次拉取数字不一致
  if (
    kind === "limit" &&
    reuseShared &&
    sharedLimitBoard &&
    sharedLimitBoard.data
  ) {
    const d = sharedLimitBoard.data;
    const dateEl = $("#f-date");
    if (dateEl && d.date && /^\d{4}-\d{2}-\d{2}$/.test(String(d.date))) {
      dateEl.value = d.date;
    }
    featureLimitCache = d;
    meta.textContent = limitBoardMetaLine(sharedLimitBoard);
    renderLimitBoard(featureLimitCache);
    return;
  }
  // 打开即查；回测也带默认参数直接跑
  runFeature();
}

async function runFeature() {
  if (!featureKind) return;
  const btn = $("#f-run");
  const meta = $("#feature-meta");
  const result = $("#feature-result");
  btn.disabled = true;
  meta.textContent = featureKind === "backtest" ? "回测中…" : "查询中…";
  result.innerHTML = `<div class="empty">${featureKind === "backtest" ? "拉取行情并运行策略…" : "拉取中…"}</div>`;

  let name = "";
  let args = {};
  try {
    if (featureKind === "screen") {
      name = "screen_market";
      args = {
        market: "a",
        sort_by: $("#f-sort").value,
        ascending: $("#f-asc").value === "1",
        top_n: Number($("#f-topn").value) || 30,
      };
    } else if (featureKind === "limit") {
      name = "get_limit_board";
      args = { date: ($("#f-date").value || "").trim() };
    } else if (featureKind === "fund") {
      name = "screen_fundamental";
      const maxPe = $("#f-maxpe").value;
      const maxPb = $("#f-maxpb").value;
      const minRoe = $("#f-minroe").value;
      const minCap = $("#f-mincap").value;
      const minDy = $("#f-mindy")?.value;
      args = {
        sort_by: $("#f-fund-sort").value,
        top_n: Number($("#f-topn").value) || 30,
        exclude_st: $("#f-exst") ? !!$("#f-exst").checked : true,
      };
      if (maxPe !== "") args.max_pe = Number(maxPe);
      if (maxPb !== "") args.max_pb = Number(maxPb);
      if (minRoe !== "") args.min_roe = Number(minRoe);
      if (minCap !== "") args.min_market_cap = Number(minCap);
      if (minDy != null && minDy !== "") args.min_dividend_yield = Number(minDy);
    } else if (featureKind === "backtest") {
      name = "run_backtest";
      const codes = normalizeCodes($("#f-codes").value);
      if (!codes.length) {
        meta.textContent = "请填写股票代码";
        result.innerHTML = `<div class="empty">请填写股票代码</div>`;
        btn.disabled = false;
        return;
      }
      const strategy = $("#f-strategy").value;
      const strategy_params = {};
      if (strategy === "ma_cross") {
        strategy_params.fast = Number($("#f-fast").value) || 5;
        strategy_params.slow = Number($("#f-slow").value) || 20;
      } else if (strategy === "rsi") {
        strategy_params.period = Number($("#f-period").value) || 14;
        strategy_params.oversold = Number($("#f-oversold").value) || 30;
        strategy_params.overbought = Number($("#f-overbought").value) || 70;
      } else if (strategy === "momentum") {
        strategy_params.window = Number($("#f-window").value) || 20;
      }
      args = {
        codes,
        start_date: ($("#f-start").value || "").trim(),
        end_date: ($("#f-end").value || "").trim(),
        strategy,
        strategy_params,
        initial_cash: Number($("#f-cash").value) || 1000000,
        signal_lag: Number($("#f-lag")?.value ?? 1),
        exec_price: ($("#f-exec")?.value || "open").trim(),
        commission: Number($("#f-comm")?.value ?? 0.0003),
        stamp_duty: Number($("#f-stamp")?.value ?? 0.0005),
        cash_annual_rate: Number($("#f-cashrate")?.value ?? 0),
        reject_limit_lock: !!$("#f-limitlock")?.checked,
        use_impact_model: !!$("#f-impact")?.checked,
        skip_halted: !!$("#f-halt")?.checked,
      };
      if ($("#f-hedge")?.checked) {
        args.hedge_enabled = true;
        args.hedge_symbol = ($("#f-hedge-sym")?.value || "IF").trim();
        args.hedge_ratio = Number($("#f-hedge-ratio")?.value ?? 1);
        const fut = ($("#f-fut")?.value || "").trim();
        if (!fut) {
          meta.textContent = "启用对冲时请填写期货合约，如 IF2506";
          result.innerHTML = `<div class="empty">请填写期货合约代码</div>`;
          btn.disabled = false;
          return;
        }
        args.futures_symbol = fut;
      }
    }

    const res = await fetch("/api/tool", {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify({ name, args }),
    });
    let data;
    try {
      data = await res.json();
    } catch {
      data = { ok: false, error: `HTTP ${res.status}` };
    }
    if (res.status === 404) {
      const tip = "接口未就绪：请重启 atrading --web 后再试（Ctrl+F5）";
      meta.textContent = tip;
      result.innerHTML = `<div class="empty">${escapeHtml(tip)}</div>`;
      return;
    }
    if (!data.ok) {
      meta.textContent = data.error || "失败";
      result.innerHTML = `<div class="empty">${escapeHtml(data.error || "失败")}</div>`;
      return;
    }
    const payload = data.result || {};
    if (payload.ok === false) {
      meta.textContent = payload.error || "工具失败";
      result.innerHTML = `<div class="empty">${escapeHtml(payload.error || "工具失败")}</div>`;
      return;
    }
    const note = [payload.quality, payload.source, payload.as_of, payload.note]
      .filter(Boolean)
      .join(" · ");
    meta.textContent = note;
    if (featureKind === "limit") {
      applySharedLimitBoard(payload);
      featureLimitCache = sharedLimitBoard.data || payload.data || payload;
      const usedDate = featureLimitCache && featureLimitCache.date;
      if (usedDate && $("#f-date") && /^\d{4}-\d{2}-\d{2}$/.test(usedDate)) {
        $("#f-date").value = usedDate;
        if (bootstrap) bootstrap.latest_trade_date = usedDate;
      }
      meta.textContent = limitBoardMetaLine(sharedLimitBoard) || note;
      renderLimitBoard(featureLimitCache);
      renderRailLimitFromShared();
    } else if (featureKind === "backtest") {
      featureBacktestCache = payload.data || payload;
      const cfg = featureBacktestCache.config || {};
      meta.textContent = [
        cfg.strategy,
        cfg.date_range,
        cfg.codes && cfg.codes.join(","),
        note,
      ]
        .filter(Boolean)
        .join(" · ");
      renderBacktest(featureBacktestCache);
    } else {
      const body = payload.data || payload;
      const stocks = body.stocks || body.rows || [];
      if (featureKind === "screen") {
        renderStockTable(stocks, {
          mode: "screen",
          sortBy: ($("#f-sort") && $("#f-sort").value) || "change_pct",
        });
      } else if (featureKind === "fund") {
        const filters = body.filters || {};
        if (filters && Object.keys(filters).length) {
          meta.textContent = [
            note,
            `筛选 ${JSON.stringify(filters)}`,
            body.exclude_st != null ? (body.exclude_st ? "排ST" : "含ST") : "",
          ]
            .filter(Boolean)
            .join(" · ");
        }
        renderStockTable(stocks, {
          mode: "fund",
          sortBy: ($("#f-fund-sort") && $("#f-fund-sort").value) || "pe",
        });
      } else {
        renderStockTable(stocks, { mode: "fund" });
      }
    }
  } catch (err) {
    meta.textContent = String(err);
    result.innerHTML = `<div class="empty">${escapeHtml(String(err))}</div>`;
  } finally {
    btn.disabled = false;
  }
}

function renderEquitySvg(curve, opts) {
  const pts = Array.isArray(curve)
    ? curve.filter((p) => p && Number.isFinite(Number(p.equity)))
    : [];
  if (pts.length < 2) return "";
  const w = 640;
  const h = 168;
  const padL = 8;
  const padR = 8;
  const padT = 12;
  const padB = 20;
  const vals = pts.map((p) => Number(p.equity));
  let minV = Math.min(...vals);
  let maxV = Math.max(...vals);
  if (minV === maxV) {
    minV *= 0.99;
    maxV *= 1.01;
  }
  const span = maxV - minV || 1;
  const n = pts.length;
  const xy = pts.map((p, i) => {
    const x = padL + ((w - padL - padR) * i) / (n - 1);
    const y = padT + (h - padT - padB) * (1 - (Number(p.equity) - minV) / span);
    return [x, y];
  });
  const line = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area =
    `${padL},${h - padB} ` +
    line +
    ` ${(w - padR).toFixed(1)},${h - padB}`;
  const first = vals[0];
  const last = vals[vals.length - 1];
  const up = last >= first;
  const stroke = up ? "#3fb950" : "#f85149";
  const fill = up ? "rgba(63,185,80,0.12)" : "rgba(248,81,73,0.12)";
  const title = (opts && opts.title) || "净值曲线";
  const startD = String(pts[0].date || "");
  const endD = String(pts[pts.length - 1].date || "");
  return `<div class="feature-equity">
    <div class="feature-equity-head">
      <span>${escapeHtml(title)}</span>
      <span class="muted">${escapeHtml(startD)} → ${escapeHtml(endD)}</span>
      <span class="num ${up ? "up" : "down"}">${fmtNum(first)} → ${fmtNum(last)}</span>
    </div>
    <svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="${escapeHtml(title)}">
      <polygon points="${area}" fill="${fill}" />
      <polyline points="${line}" fill="none" stroke="${stroke}" stroke-width="2" />
    </svg>
  </div>`;
}

function metricCards(items) {
  return items
    .filter(([, v]) => v != null && v !== "")
    .map(([k, v, tone]) => {
      let cls = "";
      if (tone === "dd") cls = pctClass(-Math.abs(Number(v)));
      else if (tone === "pnl") cls = pctClass(v);
      else if (tone === "auto") cls = pctClass(v);
      return `<div class="m"><div class="k">${escapeHtml(k)}</div><div class="v ${cls}">${escapeHtml(
        String(v)
      )}</div></div>`;
    })
    .join("");
}

function kvTable(rows, headers) {
  if (!rows || !rows.length) return `<div class="empty">暂无</div>`;
  const head = (headers || []).map((h) => `<th>${escapeHtml(h)}</th>`).join("");
  const body = rows
    .map(
      (cells) =>
        `<tr>${cells
          .map((c, i) => {
            const raw = c;
            const isNum = typeof raw === "number" || /^-?\d+(\.\d+)?%?$/.test(String(raw));
            const cls = isNum ? ` class="num ${pctClass(raw)}"` : "";
            return `<td${cls}>${escapeHtml(String(raw ?? "—"))}</td>`;
          })
          .join("")}</tr>`
    )
    .join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderBacktestSection(title, bodyHtml) {
  if (!bodyHtml) return "";
  return `<section class="feature-section"><div class="feature-section-title">${escapeHtml(
    title
  )}</div>${bodyHtml}</section>`;
}

function renderBacktest(data) {
  const result = $("#feature-result");
  if (!data || !data.metrics) {
    result.innerHTML = `<div class="empty">无回测结果</div>`;
    return;
  }
  const m = data.metrics;
  const cfg = data.config || {};

  const coreCards = metricCards([
    ["总收益%", m.total_return, "auto"],
    ["年化%", m.annual_return, "auto"],
    ["夏普", m.sharpe_ratio, "auto"],
    ["索提诺", m.sortino_ratio, "auto"],
    ["最大回撤%", m.max_drawdown, "dd"],
    ["卡玛", m.calmar_ratio, "auto"],
    ["胜率%", m.win_rate, "auto"],
    ["盈亏比", m.profit_factor, "auto"],
    ["平均盈利", m.avg_win, "pnl"],
    ["平均亏损", m.avg_loss, "pnl"],
    ["成交笔数", m.total_trades],
    ["期末权益", m.final_equity],
    ["买入持有%", m.buy_hold_return, "auto"],
    ["交易日", m.n_days],
    ["拒单次数", m.rejects],
    ["停牌日次", m.halt_code_days],
    ["现金利息", m.cash_interest_total],
  ]);

  const cfgBits = [
    cfg.strategy && `策略 ${cfg.strategy}`,
    cfg.date_range,
    cfg.codes && cfg.codes.join(","),
    cfg.signal_lag != null && `lag=${cfg.signal_lag}`,
    cfg.exec_price && `成交=${cfg.exec_price}`,
    cfg.reject_limit_lock != null && (cfg.reject_limit_lock ? "涨跌停拒单" : "不拒涨跌停"),
    cfg.use_impact_model && "冲击成本",
    cfg.hedge_enabled && `对冲 ${cfg.hedge_symbol}@${cfg.hedge_ratio}`,
    cfg.commission != null && `佣金 ${cfg.commission}`,
    cfg.stamp_duty != null && `印花税 ${cfg.stamp_duty}`,
  ].filter(Boolean);
  const cfgHtml = `<div class="feature-cfg">${escapeHtml(cfgBits.join(" · "))}</div>`;

  const equitySvg = renderEquitySvg(data.equity_curve, { title: "净值曲线" });
  const hedgeSvg = renderEquitySvg(data.hedge_curve, { title: "对冲净值" });

  // Layer1 attribution
  const l1 = m.layer1_attribution || {};
  let l1Html = "";
  if (l1.top5_winners || l1.exit_reason_pnl) {
    const winRows = (l1.top5_winners || []).map((x) => [x.code, x.pnl]);
    const loseRows = (l1.top5_losers || []).map((x) => [x.code, x.pnl]);
    const reasonRows = Object.entries(l1.exit_reason_pnl || {}).map(([k, v]) => [
      k,
      v,
      (l1.exit_reason_count && l1.exit_reason_count[k]) || "—",
    ]);
    const holdRows = Object.entries(l1.holding_buckets || {}).map(([k, n]) => [
      k,
      n,
      (l1.holding_bucket_pnl && l1.holding_bucket_pnl[k]) || "—",
    ]);
    l1Html =
      `<div class="feature-subgrid">` +
      `<div><div class="feature-sub">总盈亏 ${escapeHtml(String(l1.total_pnl ?? "—"))}` +
      (l1.pnl_excluding_top5_winners != null
        ? ` · 剔Top5后 ${escapeHtml(String(l1.pnl_excluding_top5_winners))}` +
          (l1.still_profitable_ex_top5 ? "（仍盈利）" : "（转亏）")
        : "") +
      `</div>` +
      (winRows.length
        ? `<div class="feature-mini-title">贡献 Top</div>${kvTable(winRows, ["代码", "盈亏"])}`
        : "") +
      (loseRows.length
        ? `<div class="feature-mini-title">拖累 Top</div>${kvTable(loseRows, ["代码", "盈亏"])}`
        : "") +
      `</div><div>` +
      (reasonRows.length
        ? `<div class="feature-mini-title">出场原因</div>${kvTable(reasonRows, [
            "原因",
            "盈亏",
            "笔数",
          ])}`
        : "") +
      (holdRows.length
        ? `<div class="feature-mini-title">持仓天数</div>${kvTable(holdRows, [
            "区间",
            "笔数",
            "盈亏",
          ])}`
        : "") +
      `</div></div>`;
  }

  // Layer2 beta
  const l2 = m.layer2_attribution || {};
  let l2Html = "";
  const bmEntries = Object.entries(l2.benchmarks || {});
  if (bmEntries.length) {
    const rows = bmEntries.map(([name, fit]) => {
      if (!fit || fit.error) return [name, fit?.error || "—", "—", "—", "—", "—"];
      return [
        name,
        fit.beta ?? "—",
        fit.alpha_annualized != null
          ? (Number(fit.alpha_annualized) * 100).toFixed(2) + "%"
          : "—",
        fit.r2 ?? "—",
        fit.t_beta ?? "—",
        fit.n ?? "—",
      ];
    });
    l2Html =
      kvTable(rows, ["基准", "β", "α年化", "R²", "t(β)", "n"]) +
      (l2.note ? `<div class="feature-note">${escapeHtml(l2.note)}</div>` : "");
  }

  // Risk / hedge / fills / rejects
  const risk = m.risk_attribution || {};
  let riskHtml = "";
  if (risk && !risk.error) {
    const rows = Object.entries(risk)
      .filter(([k]) => !["purpose", "note", "error"].includes(k))
      .slice(0, 16)
      .map(([k, v]) => [
        k,
        typeof v === "number" ? Number(v).toFixed(4) : typeof v === "object" ? JSON.stringify(v) : v,
      ]);
    if (rows.length) {
      riskHtml =
        kvTable(rows, ["项", "值"]) +
        (risk.note ? `<div class="feature-note">${escapeHtml(String(risk.note))}</div>` : "");
    }
  } else if (risk.error) {
    riskHtml = `<div class="empty">${escapeHtml(String(risk.error))}</div>`;
  }

  const hedge = m.hedge || {};
  let hedgeHtml = "";
  if (hedge && (hedge.symbol || hedge.net_hedge_pnl != null)) {
    hedgeHtml = `<div class="feature-metrics">${metricCards([
      ["品种", hedge.symbol],
      ["合约乘数", hedge.multiplier],
      ["对冲比例", hedge.hedge_ratio],
      ["期末手数", hedge.final_contracts],
      ["实现盈亏", hedge.realized_pnl, "pnl"],
      ["净对冲盈亏", hedge.net_hedge_pnl, "pnl"],
      ["手续费", hedge.commission_paid],
      ["移仓成本", hedge.roll_cost_paid],
    ])}</div>`;
  }

  const sleeve = m.sleeve_attribution_cum_return;
  let sleeveHtml = "";
  if (sleeve && typeof sleeve === "object") {
    sleeveHtml = kvTable(
      Object.entries(sleeve).map(([k, v]) => [k, v]),
      ["Sleeve", "累计收益%"]
    );
  }

  const fillStats = m.fill_stats;
  let fillHtml = "";
  if (fillStats && typeof fillStats === "object") {
    fillHtml = kvTable(
      Object.entries(fillStats).map(([k, v]) => [k, v]),
      ["成交统计", "值"]
    );
  }

  const rejects = Array.isArray(m.reject_sample) ? m.reject_sample.slice(0, 15) : [];
  let rejectHtml = "";
  if (rejects.length) {
    rejectHtml = kvTable(
      rejects.map((r) => {
        if (typeof r === "string") return [r];
        return [
          r.date || r.trade_date || "—",
          r.code || "—",
          r.reason || r.msg || JSON.stringify(r),
        ];
      }),
      rejects[0] && typeof rejects[0] === "object"
        ? ["日期", "代码", "原因"]
        : ["详情"]
    );
  }

  const trades = Array.isArray(data.trades) ? data.trades.slice(0, 50) : [];
  let tradeHtml = "";
  if (trades.length) {
    const rows = trades.map((t) => [
      t.entry_date || "",
      t.exit_date || "—",
      t.code || "",
      fmtNum(t.entry_price),
      fmtNum(t.exit_price),
      t.quantity ?? "—",
      t.pnl == null ? "—" : fmtNum(t.pnl),
      t.pnl_pct == null ? "—" : fmtNum(t.pnl_pct) + "%",
      t.exit_reason || "—",
    ]);
    tradeHtml = kvTable(rows, [
      "开仓",
      "平仓",
      "代码",
      "开仓价",
      "平仓价",
      "数量",
      "盈亏",
      "盈亏%",
      "出场",
    ]);
  } else {
    tradeHtml = `<div class="empty">无成交明细</div>`;
  }

  const skipped = data.skipped_symbols;
  const skipHtml =
    Array.isArray(skipped) && skipped.length
      ? renderBacktestSection(
          "跳过标的",
          `<div class="feature-note">${escapeHtml(skipped.slice(0, 8).join("；"))}</div>`
        )
      : "";

  result.innerHTML = `
    ${cfgHtml}
    <div class="feature-metrics">${coreCards}</div>
    ${equitySvg}
    ${hedgeSvg}
    <div class="feature-actions">
      <button type="button" id="f-bt-ask">让 AI 解读这次回测</button>
    </div>
    ${renderBacktestSection("Layer1 交易归因", l1Html)}
    ${renderBacktestSection("Layer2 基准 β / α", l2Html)}
    ${renderBacktestSection("风险暴露", riskHtml)}
    ${renderBacktestSection("期货对冲", hedgeHtml)}
    ${renderBacktestSection("Sleeve 归因", sleeveHtml)}
    ${renderBacktestSection("成交统计", fillHtml)}
    ${renderBacktestSection("拒单样本", rejectHtml)}
    ${skipHtml}
    ${renderBacktestSection("成交明细", tradeHtml)}
  `;

  const ask = $("#f-bt-ask");
  if (ask) {
    ask.addEventListener("click", () => {
      const codes = (cfg.codes || []).join("、") || "标的";
      const l2bits = bmEntries
        .map(([name, fit]) =>
          fit && fit.beta != null
            ? `${name} β=${fit.beta} α年化=${
                fit.alpha_annualized != null
                  ? (Number(fit.alpha_annualized) * 100).toFixed(2) + "%"
                  : "—"
              }`
            : null
        )
        .filter(Boolean)
        .join("；");
      closeFeature();
      showDesk();
      send(
        `请解读这次回测：策略 ${cfg.strategy || "?"}，标的 ${codes}，区间 ${
          cfg.date_range || "?"
        }，执行 lag=${cfg.signal_lag} ${cfg.exec_price || ""}。` +
          `关键指标：总收益 ${m.total_return}% / 年化 ${m.annual_return}% / 夏普 ${m.sharpe_ratio}` +
          ` / 索提诺 ${m.sortino_ratio} / 最大回撤 ${m.max_drawdown}% / 卡玛 ${m.calmar_ratio}` +
          ` / 胜率 ${m.win_rate}% / 盈亏比 ${m.profit_factor} / 成交 ${m.total_trades} 笔` +
          (m.buy_hold_return != null ? ` / 买入持有 ${m.buy_hold_return}%` : "") +
          (l2bits ? `。基准归因：${l2bits}` : "") +
          (l1.pnl_excluding_top5_winners != null
            ? `。Layer1 剔Top5后盈亏 ${l1.pnl_excluding_top5_winners}`
            : "") +
          `。指出强弱与可改进点。`
      );
    });
  }
}

function pctClass(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "up" : "down";
}

function fmtNum(v, digits) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return n.toFixed(digits != null ? digits : 2);
}

function fmtCap(v) {
  if (v == null || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  // 东财/akshare 多为元；基本面筛有时已是亿
  if (Math.abs(n) >= 1e6) return fmtNum(n / 1e8, 1) + "亿";
  return fmtNum(n, 1) + "亿";
}

function fmtVol(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1e8) return fmtNum(n / 1e8, 2) + "亿";
  if (Math.abs(n) >= 1e4) return fmtNum(n / 1e4, 1) + "万";
  return fmtNum(n, 0);
}

function fmtAmt(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1e8) return fmtNum(n / 1e8, 2) + "亿";
  if (Math.abs(n) >= 1e4) return fmtNum(n / 1e4, 1) + "万";
  return fmtNum(n, 0);
}

function wireStockRows(root) {
  root.querySelectorAll("tbody tr").forEach((tr) => {
    tr.addEventListener("click", () => {
      const code = tr.dataset.code;
      const name = tr.dataset.name;
      if (!code) return;
      closeFeature();
      showDesk();
      send(`帮我分析 ${code} ${name}`.trim());
    });
  });
}

function renderStockTable(stocks, opts) {
  const result = $("#feature-result");
  if (!stocks || !stocks.length) {
    result.innerHTML = `<div class="empty">无结果</div>`;
    return;
  }
  const mode = (opts && opts.mode) || "fund";
  const sortBy = (opts && opts.sortBy) || "";

  if (mode === "screen") {
    const rows = stocks
      .map((s) => {
        const pct = s.change_pct != null ? s.change_pct : s.pct;
        const turn = s.turnover_rate != null ? s.turnover_rate : s.turnover;
        const pe = s.pe != null ? s.pe : s.pe_ttm;
        return `<tr data-code="${escapeHtml(s.code || "")}" data-name="${escapeHtml(s.name || "")}">
          <td>${escapeHtml(s.code || "")}</td>
          <td>${escapeHtml(s.name || "")}</td>
          <td class="num">${fmtNum(s.price)}</td>
          <td class="num ${pctClass(pct)}">${pct == null ? "—" : fmtNum(pct) + "%"}</td>
          <td class="num">${fmtVol(s.volume)}</td>
          <td class="num">${fmtAmt(s.amount)}</td>
          <td class="num">${turn == null ? "—" : fmtNum(turn)}</td>
          <td class="num">${pe == null ? "—" : fmtNum(pe)}</td>
          <td class="num">${s.pb == null ? "—" : fmtNum(s.pb)}</td>
          <td class="num">${fmtCap(s.market_cap)}</td>
        </tr>`;
      })
      .join("");
    result.innerHTML = `<table>
      <thead><tr>
        <th>代码</th><th>名称</th><th>现价</th><th>涨跌%</th>
        <th>成交量</th><th>成交额</th><th>换手%</th><th>PE</th><th>PB</th><th>市值</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
    wireStockRows(result);
    return;
  }

  // fund / default
  const rows = stocks
    .map((s) => {
      const pct = s.change_pct != null ? s.change_pct : s.pct;
      const pe = s.pe != null ? s.pe : s.pe_ttm;
      const dy = s.dividend_yield;
      return `<tr data-code="${escapeHtml(s.code || "")}" data-name="${escapeHtml(s.name || "")}">
        <td>${escapeHtml(s.code || "")}</td>
        <td>${escapeHtml(s.name || "")}</td>
        <td class="num">${fmtNum(s.price)}</td>
        <td class="num ${pctClass(pct)}">${pct == null ? "—" : fmtNum(pct) + "%"}</td>
        <td class="num">${pe == null ? "—" : fmtNum(pe)}</td>
        <td class="num">${s.pb == null ? "—" : fmtNum(s.pb)}</td>
        <td class="num">${s.roe == null ? "—" : fmtNum(s.roe)}</td>
        <td class="num">${dy == null ? "—" : fmtNum(dy) + "%"}</td>
        <td class="num">${fmtCap(s.market_cap)}</td>
      </tr>`;
    })
    .join("");
  result.innerHTML = `<table>
    <thead><tr>
      <th>代码</th><th>名称</th><th>现价</th><th>涨跌%</th>
      <th>PE</th><th>PB</th><th>ROE</th><th>股息%</th><th>市值</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
  wireStockRows(result);
}

function renderLimitBoard(data) {
  const result = $("#feature-result");
  if (!data) {
    result.innerHTML = `<div class="empty">无数据</div>`;
    return;
  }
  const tabs = [
    ["limit_up", `涨停 ${data.limit_up_count != null ? data.limit_up_count : (data.limit_up || []).length}`],
    ["broken_board", `炸板 ${data.broken_board_count != null ? data.broken_board_count : (data.broken_board || []).length}`],
    ["limit_down", `跌停 ${data.limit_down_count != null ? data.limit_down_count : (data.limit_down || []).length}`],
  ];
  const list = data[featureLimitTab] || [];
  const tabHtml = tabs
    .map(
      ([id, label]) =>
        `<button type="button" class="${id === featureLimitTab ? "active" : ""}" data-tab="${id}">${escapeHtml(label)}</button>`
    )
    .join("");
  if (!list.length) {
    result.innerHTML = `<div class="feature-tabs">${tabHtml}</div><div class="empty">该分类暂无</div>`;
  } else {
    const rows = list
      .map((s) => {
        const pct = s.change_pct;
        const seal = s.limit_order_ratio;
        const turn = s.turnover_rate;
        return `<tr data-code="${escapeHtml(s.code || "")}" data-name="${escapeHtml(s.name || "")}">
          <td>${escapeHtml(s.code || "")}</td>
          <td>${escapeHtml(s.name || "")}</td>
          <td class="num">${fmtNum(s.price)}</td>
          <td class="num ${pctClass(pct)}">${pct == null ? "—" : fmtNum(pct) + "%"}</td>
          <td class="num">${escapeHtml(String(s.consecutive_days ?? "—"))}</td>
          <td class="num">${escapeHtml(String(s.open_count ?? "—"))}</td>
          <td class="num">${seal == null ? "—" : fmtAmt(seal)}</td>
          <td class="num">${turn == null ? "—" : fmtNum(turn)}</td>
          <td>${escapeHtml(String(s.limit_up_time || "—"))}</td>
          <td>${escapeHtml(String(s.sector || "—"))}</td>
        </tr>`;
      })
      .join("");
    result.innerHTML = `<div class="feature-tabs">${tabHtml}</div>
      <table>
        <thead><tr>
          <th>代码</th><th>名称</th><th>现价</th><th>涨跌%</th><th>连板</th><th>开板</th>
          <th>封单</th><th>换手%</th><th>时间</th><th>板块</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }
  result.querySelectorAll(".feature-tabs button").forEach((b) => {
    b.addEventListener("click", () => {
      featureLimitTab = b.dataset.tab;
      renderLimitBoard(featureLimitCache);
    });
  });
  wireStockRows(result);
}

function wirePalette() {
  $("#palette-backdrop").addEventListener("click", () => closePalette());
  $("#palette-input").addEventListener("input", () => {
    if (!paletteState) return;
    paletteState.filtered = filterPalette($("#palette-input").value);
    paletteState.index = 0;
    renderPaletteList();
  });
  $("#palette-input").addEventListener("keydown", (e) => {
    if (!paletteState) return;
    if (e.key === "Escape") {
      e.preventDefault();
      closePalette();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!paletteState.filtered.length) return;
      paletteState.index = (paletteState.index + 1) % paletteState.filtered.length;
      renderPaletteList();
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!paletteState.filtered.length) return;
      paletteState.index =
        (paletteState.index - 1 + paletteState.filtered.length) %
        paletteState.filtered.length;
      renderPaletteList();
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      confirmPalette();
    }
  });
}

function wirePicker() {
  $("#picker-close").addEventListener("click", () => closePicker());
  $("#picker-backdrop").addEventListener("click", () => closePicker());
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "p" || e.key === "P")) {
      e.preventDefault();
      if (paletteState) closePalette();
      else openPalette();
      return;
    }
    if (paletteState) {
      if (e.key === "Escape") {
        e.preventDefault();
        closePalette();
      }
      return;
    }
    if (!pickerState) return;
    if (e.key === "Escape") {
      e.preventDefault();
      cancelPicker();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!pickerState.items.length) return;
      pickerState.index = (pickerState.index + 1) % pickerState.items.length;
      renderPickerList();
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!pickerState.items.length) return;
      pickerState.index =
        (pickerState.index - 1 + pickerState.items.length) % pickerState.items.length;
      renderPickerList();
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      confirmPicker();
    }
  });
}

function wireComposer() {
  sendBtn.addEventListener("click", () => send());
  abortBtn.addEventListener("click", abort);
  input.addEventListener("input", () => {
    const n = input.value.length;
    charCount.textContent = n ? `${n}` : "";
    renderSlash();
  });
  input.addEventListener("keydown", (e) => {
    if (pickerState || paletteState) return;
    if (e.key === "Escape") {
      if (!slashMenu.classList.contains("hidden")) {
        slashMenu.classList.add("hidden");
        return;
      }
      if (busy) abort();
      return;
    }
    if (!slashMenu.classList.contains("hidden") && slashItems.length) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        slashIndex = (slashIndex + 1) % slashItems.length;
        renderSlash();
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        slashIndex = (slashIndex - 1 + slashItems.length) % slashItems.length;
        renderSlash();
        return;
      }
      if (e.key === "Tab") {
        e.preventDefault();
        input.value = slashItems[slashIndex][0] + " ";
        slashMenu.classList.add("hidden");
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
}

async function main() {
  if (window.marked) {
    marked.setOptions({ breaks: true, gfm: true });
  }
  wireWelcome();
  wireSidebar();
  wireSessionSearch();
  wireFeature();
  wireAgentLauncher();
  wireRail();
  wireAbout();
  wirePalette();
  wirePicker();
  wireComposer();
  wireAgentLauncher();
  setAgentMode("fast", { focus: false });
  if (window.AgentTeamUI) AgentTeamUI.wire();
  const res = await fetch("/api/bootstrap");
  const data = await res.json();
  applyBootstrap(data);
  connectEvents();
  await refreshTicker();
  setInterval(refreshTicker, 12000);
}

main().catch((err) => {
  statusText.textContent = String(err);
  showDesk();
});
