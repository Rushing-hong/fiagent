/* Agent team visualization — rail + feature panel */

const AGENT_LABELS = {
  data_guardian: "Data Guardian",
  market_regime: "Market",
  company_research: "Company",
  quant_research: "Quant",
  red_team: "Red-Team",
  orchestrator: "CIO",
  trade_review: "Trade Review",
};

const DISPLAY_TO_AGENT = {
  "data guardian": "data_guardian",
  "market regime": "market_regime",
  "company research": "company_research",
  "quant research": "quant_research",
  "red-team": "red_team",
  "red team": "red_team",
  cio: "orchestrator",
  policy: "policy",
};

const DAG_NODES = [
  { id: "data_guardian", label: "Data", layer: 0 },
  { id: "market_regime", label: "Market", layer: 1 },
  { id: "company_research", label: "Company", layer: 1 },
  { id: "quant_research", label: "Quant", layer: 1 },
  { id: "red_team", label: "Red", layer: 2 },
  { id: "policy", label: "Policy", layer: 3 },
  { id: "orchestrator", label: "CIO", layer: 4 },
];

const agentTeamState = {
  runId: "",
  mode: "",
  workflow: "",
  researchers: [],
  includeRedTeam: true,
  agents: {},
  phase: "",
  panel: null,
  agentEls: {},
  agentData: {},
  selectedAgent: "",
  active: false,
};

function agentLabel(id) {
  return AGENT_LABELS[id] || id;
}

function statusClass(st) {
  if (st === "running") return "at-running";
  if (st === "completed") return "at-done";
  if (st === "failed") return "at-fail";
  return "at-pending";
}

function ensureAgentData(agentId) {
  if (!agentTeamState.agentData[agentId]) {
    agentTeamState.agentData[agentId] = {
      logs: [],
      preview: "",
      fullReport: "",
      timeline: [],
      live: {},
    };
  }
  return agentTeamState.agentData[agentId];
}

function prettyAgentToolText(text) {
  const raw = String(text || "").trim();
  if (!raw) return "(空)";
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch (_) {
    return raw;
  }
}

function agentPanelTimelineEl() {
  return document.getElementById("agent-panel-timeline");
}

function createAgentFold(item) {
  const wrap = document.createElement("div");
  wrap.className = `fold ${item.kind || "info"}${item.open ? " open" : ""}`;
  if (item.id) wrap.dataset.itemId = item.id;
  const head = document.createElement("button");
  head.type = "button";
  head.className = "fold-head";
  head.innerHTML = `<span class="chev"></span><span class="icon">${escapeHtml(item.icon || "·")}</span><span class="name">${escapeHtml(item.title || "")}</span><span class="meta">${escapeHtml(item.meta || "")}</span>`;
  const body = document.createElement("div");
  body.className = "fold-body";
  body.textContent = item.body || "";
  head.addEventListener("click", () => wrap.classList.toggle("open"));
  wrap.appendChild(head);
  wrap.appendChild(body);
  return {
    wrap,
    body,
    meta: head.querySelector(".meta"),
    name: head.querySelector(".name"),
  };
}

function mountAgentTimelineNode(agentId, node) {
  const data = ensureAgentData(agentId);
  data.timeline.push(node);
  const box = agentPanelTimelineEl();
  if (box && agentTeamState.selectedAgent === agentId) {
    const empty = box.querySelector(".agent-panel-empty");
    if (empty) empty.remove();
    box.appendChild(node);
    box.scrollTop = box.scrollHeight;
  }
}

function rebuildAgentPanelTimeline(agentId) {
  const box = agentPanelTimelineEl();
  if (!box) return;
  box.innerHTML = "";
  const data = ensureAgentData(agentId);
  if (!data.timeline.length) {
    box.innerHTML = '<div class="agent-panel-empty">（尚无执行轨迹 — hook / 思考 / 工具调用会显示在此）</div>';
    return;
  }
  data.timeline.forEach((node) => box.appendChild(node));
  box.scrollTop = box.scrollHeight;
}

function handleAgentUiEvent(ev) {
  const agentId = ev.agent;
  if (!agentId) return;
  const data = ensureAgentData(agentId);
  const live = data.live;
  const uiType = ev.ui_type || "";

  if (uiType === "hook" || uiType === "line") {
    const msg = ev.message || ev.tag || "";
    if (msg) appendTeamAgentLog(agentId, uiType === "hook" ? `hook:${ev.tag || ""} ${msg}` : msg);
    const fold = createAgentFold({
      kind: "hook",
      icon: "◎",
      title: ev.tag || (uiType === "line" ? "日志" : "hook"),
      meta: ev.level || "",
      body: msg,
      open: false,
    });
    mountAgentTimelineNode(agentId, fold.wrap);
    return;
  }
  if (uiType === "round" || uiType === "round_rule") {
    const title = uiType === "round_rule" ? ev.title || "工具轮次" : `第 ${ev.round_idx} 轮`;
    const fold = createAgentFold({
      kind: "round",
      icon: "●",
      title,
      meta: "LLM",
      body: "",
      open: false,
    });
    mountAgentTimelineNode(agentId, fold.wrap);
    if (uiType === "round") appendTeamAgentLog(agentId, title);
    return;
  }
  if (uiType === "activity") {
    if (agentTeamState.agents[agentId] === "running") {
      syncRailActivity(`${agentLabel(agentId)} · ${ev.text || "执行中…"}`);
    }
    return;
  }
  if (uiType === "think_begin") {
    const fold = createAgentFold({
      kind: "think",
      icon: "◇",
      title: "思考",
      meta: "流式中",
      body: "▌",
      open: true,
      id: `think-live-${agentId}`,
    });
    live.think = fold;
    mountAgentTimelineNode(agentId, fold.wrap);
    return;
  }
  if (uiType === "think_delta") {
    const text = ev.text || "";
    if (live.think) {
      live.think.body.textContent = text + "▌";
      if (live.think.meta) live.think.meta.textContent = `${text.length} 字`;
    }
    return;
  }
  if (uiType === "think_end") {
    const text = ev.text || "";
    if (live.think) {
      live.think.body.textContent = text || "(无思考内容)";
      if (live.think.meta) live.think.meta.textContent = `${text.length} 字 · 点击展开`;
      live.think.wrap.classList.remove("open");
      live.think = null;
    } else if (text) {
      const fold = createAgentFold({
        kind: "think",
        icon: "◇",
        title: "思考",
        meta: `${text.length} 字`,
        body: text,
        open: false,
      });
      mountAgentTimelineNode(agentId, fold.wrap);
    }
    return;
  }
  if (uiType === "tool_call") {
    const fold = createAgentFold({
      kind: "tool",
      icon: "⚙",
      title: `工具 · ${ev.name || "tool"}`,
      meta: "参数 · 点击展开",
      body: prettyAgentToolText(ev.args),
      open: false,
    });
    mountAgentTimelineNode(agentId, fold.wrap);
    appendTeamAgentLog(agentId, `⚙ ${ev.name || "tool"}`);
    return;
  }
  if (uiType === "tool_result") {
    const body = prettyAgentToolText(ev.text);
    const fold = createAgentFold({
      kind: "tool",
      icon: "↩",
      title: `结果 · ${ev.name || "result"}`,
      meta: `${body.length} 字 · 点击展开`,
      body,
      open: false,
    });
    mountAgentTimelineNode(agentId, fold.wrap);
    return;
  }
  if (uiType === "reply_begin") {
    const fold = createAgentFold({
      kind: "reply",
      icon: "◆",
      title: "输出",
      meta: "流式中",
      body: "▌",
      open: true,
      id: `reply-live-${agentId}`,
    });
    live.reply = fold;
    mountAgentTimelineNode(agentId, fold.wrap);
    return;
  }
  if (uiType === "reply_delta") {
    const text = ev.text || "";
    if (live.reply) {
      live.reply.body.textContent = text + "▌";
      if (live.reply.meta) live.reply.meta.textContent = `${text.length} 字`;
    }
    return;
  }
  if (uiType === "reply_end") {
    const text = ev.text || "";
    if (live.reply) {
      live.reply.body.textContent = text || "(无输出)";
      if (live.reply.meta) live.reply.meta.textContent = `${text.length} 字 · 点击展开`;
      live.reply.wrap.classList.remove("open");
      live.reply = null;
    } else if (text) {
      const fold = createAgentFold({
        kind: "reply",
        icon: "◆",
        title: "输出",
        meta: `${text.length} 字`,
        body: text,
        open: false,
      });
      mountAgentTimelineNode(agentId, fold.wrap);
    }
  }
}

function closeAgentDetailPanel() {
  agentTeamState.selectedAgent = "";
  document.body.classList.remove("agent-panel-open");
  const panel = document.getElementById("agent-panel");
  if (panel) panel.setAttribute("aria-hidden", "true");
  Object.values(agentTeamState.agentEls || {}).forEach((el) => {
    if (el && el.row) el.row.classList.remove("selected");
  });
}

function renderAgentPanelDag() {
  const box = document.getElementById("agent-panel-dag");
  if (!box || !agentTeamState.panel) return;
  const dag = agentTeamState.panel.querySelector(".team-run-dag");
  if (!dag) return;
  box.innerHTML = dag.innerHTML;
  const sel = agentTeamState.selectedAgent;
  if (!sel) return;
  box.querySelectorAll(".at-node").forEach((node) => {
    const title = node.getAttribute("title") || "";
    if (title === sel || title === "policy" && sel === "policy") {
      node.classList.add("at-selected");
    }
  });
}

function syncAgentDetailPanel(agentId) {
  const data = ensureAgentData(agentId);
  const title = document.getElementById("agent-panel-title");
  const meta = document.getElementById("agent-panel-meta");
  const previewBox = document.getElementById("agent-panel-preview");
  if (title) title.textContent = agentLabel(agentId);
  if (meta) {
    const st = agentTeamState.agents[agentId] || "pending";
    const parts = [teamStatusLabel(st)];
    if (agentTeamState.runId) parts.push(`run ${agentTeamState.runId}`);
    if (agentTeamState.mode) parts.push(agentTeamState.mode);
    meta.textContent = parts.join(" · ");
  }
  rebuildAgentPanelTimeline(agentId);
  const body = data.fullReport || data.preview || "";
  if (previewBox) {
    previewBox.textContent = body || "（尚无报告，执行完成后可查看）";
    previewBox.scrollTop = 0;
  }
  renderAgentPanelDag();
}

async function loadAgentFullReport(agentId) {
  if (!agentTeamState.runId || agentId === "policy") return;
  try {
    const detail = await fetchRunDetail(agentTeamState.runId);
    const rep = (detail.reports || []).find((r) => r.agent_name === agentId);
    if (rep && rep.content) {
      ensureAgentData(agentId).fullReport = rep.content;
      if (agentTeamState.selectedAgent === agentId) {
        const previewBox = document.getElementById("agent-panel-preview");
        if (previewBox) previewBox.textContent = rep.content;
      }
    }
  } catch (_) {
    /* ignore */
  }
}

function openAgentDetailPanel(agentId) {
  if (!agentId) return;
  agentTeamState.selectedAgent = agentId;
  Object.entries(agentTeamState.agentEls || {}).forEach(([id, el]) => {
    if (el && el.row) el.row.classList.toggle("selected", id === agentId);
  });
  const panel = document.getElementById("agent-panel");
  if (panel) panel.setAttribute("aria-hidden", "false");
  document.body.classList.add("agent-panel-open");
  syncAgentDetailPanel(agentId);
  loadAgentFullReport(agentId);
}

function teamRailBox() {
  return document.getElementById("rail-agent-team");
}

function teamDockScroll() {
  const dock = document.getElementById("rail-agent-dock");
  if (dock) dock.scrollTop = dock.scrollHeight;
}

function syncRailActivity(text) {
  const phase = (text || "").trim();
  agentTeamState.phase = phase;
  const act = document.getElementById("rail-activity");
  if (act && phase) act.textContent = phase;
  const phaseEl = agentTeamState.panel && agentTeamState.panel.querySelector(".team-run-phase");
  if (phaseEl) phaseEl.textContent = phase;
  if (phase && typeof setActivity === "function") setActivity(phase);
  teamDockScroll();
}

function teamStatusIcon(st) {
  if (st === "running") return "◉";
  if (st === "completed") return "✓";
  if (st === "failed") return "✗";
  return "○";
}

function teamStatusLabel(st) {
  if (st === "running") return "执行中";
  if (st === "completed") return "完成";
  if (st === "failed") return "失败";
  return "等待";
}

function teamAgentOrder(mode, researchers, includeRedTeam) {
  const order = ["data_guardian"];
  const r = researchers && researchers.length ? researchers : ["market_regime", "company_research", "quant_research"];
  r.forEach((a) => order.push(a));
  if (includeRedTeam) order.push("red_team");
  if (mode === "committee") order.push("policy");
  order.push("orchestrator");
  return order;
}

function renderTeamDag() {
  if (!agentTeamState.panel) return;
  const dag = agentTeamState.panel.querySelector(".team-run-dag");
  if (!dag) return;

  const activeSet = new Set(agentTeamState.researchers || []);
  const showPolicy = agentTeamState.mode === "committee";
  const showRed = agentTeamState.includeRedTeam !== false;

  const nodes = DAG_NODES.filter((n) => {
    if (n.id === "policy") return showPolicy;
    if (n.id === "red_team") return showRed;
    if (["market_regime", "company_research", "quant_research"].includes(n.id)) {
      return activeSet.size === 0 || activeSet.has(n.id);
    }
    return true;
  });

  const layers = {};
  nodes.forEach((n) => {
    layers[n.layer] = layers[n.layer] || [];
    layers[n.layer].push(n);
  });

  let html = "";
  Object.keys(layers)
    .sort((a, b) => Number(a) - Number(b))
    .forEach((layer) => {
      html += `<div class="at-layer">`;
      layers[layer].forEach((n) => {
        const key = n.id === "policy" ? "policy" : n.id;
        const st = agentTeamState.agents[key] || "pending";
        const parallel = layer === "1" && nodes.filter((x) => x.layer === 1).length > 1;
        const parTag = parallel && st === "running" ? '<span class="team-par">并行</span>' : "";
        html += `<div class="at-node ${statusClass(st)}" title="${escapeHtml(n.id)}">${escapeHtml(n.label)}${parTag}</div>`;
      });
      html += `</div>`;
      if (Number(layer) < Math.max(...Object.keys(layers).map(Number))) {
        html += `<div class="at-arrow">↓</div>`;
      }
    });
  dag.innerHTML = html;
}

function ensureTeamAgentRow(agentId) {
  if (!agentTeamState.panel || agentTeamState.agentEls[agentId]) return agentTeamState.agentEls[agentId];
  const list = agentTeamState.panel.querySelector(".team-run-agents");
  if (!list) return null;

  const row = document.createElement("div");
  row.className = "team-agent";
  row.dataset.agent = agentId;
  row.innerHTML = `
    <button type="button" class="team-agent-head">
      <span class="team-agent-icon">${teamStatusIcon("pending")}</span>
      <span class="team-agent-name">${escapeHtml(agentLabel(agentId))}</span>
      <span class="team-agent-status">等待</span>
      <span class="team-agent-meta"></span>
      <span class="team-agent-chev"></span>
    </button>
    <div class="team-agent-body">
      <div class="team-agent-log"></div>
      <div class="team-agent-preview"></div>
    </div>`;
  row.querySelector(".team-agent-head").addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    openAgentDetailPanel(agentId);
  });
  list.appendChild(row);
  agentTeamState.agentEls[agentId] = {
    row,
    icon: row.querySelector(".team-agent-icon"),
    status: row.querySelector(".team-agent-status"),
    meta: row.querySelector(".team-agent-meta"),
    log: row.querySelector(".team-agent-log"),
    preview: row.querySelector(".team-agent-preview"),
  };
  return agentTeamState.agentEls[agentId];
}

function setTeamAgentStatus(agentId, st, metaText) {
  const el = ensureTeamAgentRow(agentId);
  if (!el) return;
  agentTeamState.agents[agentId] = st;
  el.row.classList.remove("at-pending", "at-running", "at-done", "at-fail");
  el.row.classList.add(statusClass(st));
  if (el.icon) el.icon.textContent = teamStatusIcon(st);
  if (el.status) el.status.textContent = teamStatusLabel(st);
  if (el.meta && metaText != null) el.meta.textContent = metaText;
  if (st === "running") el.row.classList.add("open");
  renderTeamDag();
  if (agentTeamState.selectedAgent === agentId) {
    syncAgentDetailPanel(agentId);
  }
  teamDockScroll();
}

function appendTeamAgentLog(agentId, text) {
  const el = ensureTeamAgentRow(agentId);
  if (!el || !text) return;
  ensureAgentData(agentId).logs.push(text);
  const line = document.createElement("div");
  line.className = "team-log-line";
  line.textContent = text;
  el.log.appendChild(line);
  if (el.log.childElementCount > 30) {
    el.log.removeChild(el.log.firstElementChild);
  }
  if (agentTeamState.selectedAgent === agentId) {
    syncAgentDetailPanel(agentId);
  }
  teamDockScroll();
}

function setTeamAgentPreview(agentId, preview, extra) {
  const el = ensureTeamAgentRow(agentId);
  if (!el) return;
  const body = (preview || "").trim();
  if (!body) return;
  ensureAgentData(agentId).preview = body;
  el.preview.textContent = body;
  if (extra && el.meta) el.meta.textContent = extra;
  if (agentTeamState.selectedAgent === agentId) {
    syncAgentDetailPanel(agentId);
  }
  loadAgentFullReport(agentId);
  teamDockScroll();
}

function mountTeamTaskCard(ev) {
  const chat = document.getElementById("chat");
  if (!chat) return;
  const prev = document.getElementById("live-team-task-card");
  if (prev) prev.remove();
  const modeLabel = ev.mode === "committee" ? "投资委员会" : "研究团队";
  const researchers = (ev.researchers || []).map(agentLabel).join(" · ") || "Market · Company · Quant";
  const wrap = document.createElement("div");
  wrap.className = "fold team-task-card open";
  wrap.id = "live-team-task-card";
  wrap.innerHTML = `
    <button type="button" class="fold-head">
      <span class="chev"></span>
      <span class="icon">◎</span>
      <span class="name">${escapeHtml(modeLabel)}</span>
      <span class="meta">${escapeHtml(ev.run_id || "")} · 执行中</span>
    </button>
    <div class="fold-body">${escapeHtml(ev.workflow || "")} · ${escapeHtml(researchers)}
右侧「本轮运行」可查看各专家进度与日志。</div>`;
  wrap.querySelector(".fold-head").addEventListener("click", () => wrap.classList.toggle("open"));
  chat.appendChild(wrap);
  if (typeof scrollChat === "function") scrollChat();
}

function updateTeamTaskCard(ev) {
  const card = document.getElementById("live-team-task-card");
  if (!card) return;
  const meta = card.querySelector(".meta");
  const body = card.querySelector(".fold-body");
  const failed = ev.failed_count || 0;
  if (meta) {
    meta.textContent = failed
      ? `${ev.run_id || ""} · 完成（${failed} 个环节失败）`
      : `${ev.run_id || ""} · 完成`;
  }
  if (body) {
    body.textContent = failed
      ? "部分专家未完成，CIO 已基于可用报告综合。详情见右侧「本轮运行」。"
      : "专家报告已综合，CIO 结论见下方。";
  }
  card.classList.remove("open");
}

function createTeamRailPanel(ev) {
  const box = teamRailBox();
  if (!box) return;

  const modeLabel = ev.mode === "committee" ? "投资委员会" : "研究团队";
  box.innerHTML = `
    <div class="team-run open" data-run-id="${escapeHtml(ev.run_id || "")}">
      <div class="team-run-head">
        <span class="team-run-badge">${escapeHtml(modeLabel)}</span>
        <code class="team-run-id">${escapeHtml(ev.run_id || "")}</code>
        <span class="team-run-workflow">${escapeHtml(ev.workflow || "")}</span>
        <span class="team-run-phase">启动中…</span>
      </div>
      <div class="team-run-dag at-dag"></div>
      <div class="team-run-agents"></div>
    </div>`;

  agentTeamState.panel = box.querySelector(".team-run");
  agentTeamState.runId = ev.run_id || "";
  agentTeamState.mode = ev.mode || "";
  agentTeamState.workflow = ev.workflow || "";
  agentTeamState.researchers = ev.researchers || [];
  agentTeamState.includeRedTeam = ev.include_red_team !== false;
  agentTeamState.agents = { data_guardian: "pending" };
  agentTeamState.agentEls = {};
  agentTeamState.agentData = {};
  agentTeamState.selectedAgent = "";
  agentTeamState.active = true;

  (agentTeamState.researchers || []).forEach((a) => {
    agentTeamState.agents[a] = "pending";
  });
  if (agentTeamState.includeRedTeam) agentTeamState.agents.red_team = "pending";
  if (agentTeamState.mode === "committee") agentTeamState.agents.policy = "pending";
  agentTeamState.agents.orchestrator = "pending";

  const order = teamAgentOrder(agentTeamState.mode, agentTeamState.researchers, agentTeamState.includeRedTeam);
  order.forEach((a) => ensureTeamAgentRow(a));

  const researchers = (agentTeamState.researchers || []).map(agentLabel).join(" · ") || "Market · Company · Quant";
  syncRailActivity(`工作流 ${agentTeamState.workflow || "—"} · ${researchers}`);
  renderTeamDag();
  document.body.classList.add("rail-panel-open");
  document.body.classList.add("team-run-active");
  mountTeamTaskCard(ev);
  if (typeof setActivity === "function") setActivity("研究团队启动…");
  const dock = document.getElementById("rail-agent-dock");
  if (dock) dock.classList.add("team-active");
}

function resetAgentTeamRail() {
  agentTeamState.runId = "";
  agentTeamState.mode = "";
  agentTeamState.workflow = "";
  agentTeamState.researchers = [];
  agentTeamState.agents = {};
  agentTeamState.phase = "";
  agentTeamState.panel = null;
  agentTeamState.agentEls = {};
  agentTeamState.agentData = {};
  agentTeamState.selectedAgent = "";
  agentTeamState.active = false;
  document.body.classList.remove("team-run-active");
  const taskCard = document.getElementById("live-team-task-card");
  if (taskCard) taskCard.remove();
  closeAgentDetailPanel();
  const box = teamRailBox();
  if (box) {
    box.innerHTML = `<div class="rail-empty">Research/Committee 模式启动后显示</div>`;
  }
  const dock = document.getElementById("rail-agent-dock");
  if (dock) dock.classList.remove("team-active");
}

function isTeamRunActive() {
  return document.body.classList.contains("team-run-active") || (agentTeamState.active && !!agentTeamState.panel);
}

function routeExternalError(text, cls) {
  if (!text) return false;
  const raw = String(text).trim();
  if (raw.includes("chunked read") || raw.includes("LLM") || (cls || "").includes("line-err")) {
    appendTeamAgentLog("orchestrator", raw.slice(0, 240));
    syncRailActivity("运行异常 — 见右侧日志");
    return true;
  }
  return false;
}

function routeTeamLine(text) {
  if (!isTeamRunActive() || !text) return false;
  const raw = String(text).trim();
  const m = raw.match(/^\[([^\]]+)\]\s*(.*)$/);
  if (m) {
    appendTeamAgentLog(resolveAgentKey(m[1]), m[2] || raw);
    return true;
  }
  if (raw.startsWith("[Policy]") || raw.includes("政策")) {
    appendTeamAgentLog("policy", raw);
    return true;
  }
  if (raw.startsWith("[CIO]") || raw.includes("CIO")) {
    appendTeamAgentLog("orchestrator", raw);
    return true;
  }
  if (raw.includes("研究团队启动") || raw.includes("多 Agent 模式")) {
    syncRailActivity(raw);
    return true;
  }
  if (/LLM|chunked read|流式连接/.test(raw)) {
    return routeExternalError(raw, "line-err");
  }
  return false;
}

function resolveAgentKey(label) {
  const raw = String(label || "").trim();
  const low = raw.toLowerCase();
  if (DISPLAY_TO_AGENT[low]) return DISPLAY_TO_AGENT[low];
  const byLabel = Object.keys(AGENT_LABELS).find(
    (k) => AGENT_LABELS[k].toLowerCase() === low || k === low.replace(/\s+/g, "_")
  );
  return byLabel || low.replace(/\s+/g, "_");
}

function handleAgentTeamEvent(ev) {
  const phase = ev.phase || "";
  if (phase === "start") {
    createTeamRailPanel(ev);
    syncRailActivity("Data Guardian 取证中…（研究员将在取证后并行启动）");
    return;
  }
  if (!agentTeamState.panel) return;

  if (phase === "agent_start") {
    const a = ev.agent;
    if (!a) return;
    setTeamAgentStatus(a, "running");
    appendTeamAgentLog(a, "开始执行…");
    if (["market_regime", "company_research", "quant_research"].includes(a)) {
      const running = ["market_regime", "company_research", "quant_research"].filter(
        (id) => agentTeamState.agents[id] === "running"
      );
      if (running.length > 1) {
        syncRailActivity(`${running.map(agentLabel).join(" / ")} 并行执行中…`);
      } else {
        syncRailActivity(`${agentLabel(a)} 执行中…`);
      }
    } else {
      syncRailActivity(`${agentLabel(a)} 执行中…`);
    }
    return;
  }
  if (phase === "agent_tool") {
    const a = ev.agent;
    if (!a) return;
    const mark = ev.success === false ? "✗" : "⚙";
    appendTeamAgentLog(a, `${mark} ${ev.tool || "tool"} ${(ev.args_preview || "").slice(0, 72)}`);
    return;
  }
  if (phase === "agent_log") {
    const a = ev.agent;
    if (!a || !ev.message) return;
    const prefix = ev.level === "error" ? "✗ " : "";
    appendTeamAgentLog(a, prefix + ev.message);
    handleAgentUiEvent({
      agent: a,
      ui_type: "line",
      level: ev.level || "info",
      message: prefix + ev.message,
    });
    return;
  }
  if (phase === "agent_ui") {
    handleAgentUiEvent(ev);
    return;
  }
  if (phase === "agent_done") {
    const a = ev.agent;
    if (!a) return;
    const st = ev.status === "completed" ? "completed" : "failed";
    const meta = [];
    if (ev.tool_rounds != null) meta.push(`${ev.tool_rounds} 轮工具`);
    if (ev.valid === false) meta.push("校验未通过");
    setTeamAgentStatus(a, st, meta.join(" · "));
    if (ev.preview) setTeamAgentPreview(a, ev.preview, meta.join(" · "));
    if (ev.errors && ev.errors.length) {
      ev.errors.forEach((err) => appendTeamAgentLog(a, `校验: ${err}`));
    }
    syncRailActivity(`${agentLabel(a)} ${teamStatusLabel(st)}`);
    return;
  }
  if (phase === "policy_start") {
    setTeamAgentStatus("policy", "running");
    syncRailActivity("合规 / 风险 / 执行模拟…");
    return;
  }
  if (phase === "policy_done") {
    setTeamAgentStatus("policy", "completed");
    syncRailActivity("政策硬门完成");
    return;
  }
  if (phase === "cio_start") {
    setTeamAgentStatus("orchestrator", "running");
    syncRailActivity("CIO 综合专家报告…");
    return;
  }
  if (phase === "complete") {
    setTeamAgentStatus("orchestrator", "completed");
    syncRailActivity("研究团队完成 · 见聊天区 CIO 结论");
    agentTeamState.active = false;
    document.body.classList.remove("team-run-active");
    updateTeamTaskCard(ev);
    if (agentTeamState.panel) agentTeamState.panel.classList.add("team-run-done");
  }
}

function parseResearchBrief(content) {
  const text = String(content || "");
  if (!text.includes("## Research Run ID") || !text.includes("## 用户问题")) return null;
  const runM = text.match(/## Research Run ID\s*\n([^\n]+)/);
  const modeM = text.match(/## 运行模式\s*\n([^\n]+)/);
  const queryM = text.match(/## 用户问题\s*\n([^\n]+)/);
  return {
    runId: (runM && runM[1].trim()) || "",
    mode: (modeM && modeM[1].trim()) || "research",
    query: (queryM && queryM[1].trim()) || "",
  };
}

async function fetchResearchRuns() {
  const res = await fetch("/api/research/runs?limit=40");
  return res.json();
}

async function fetchRunDetail(runId) {
  const res = await fetch(`/api/research/runs/${encodeURIComponent(runId)}`);
  return res.json();
}

async function fetchEvals() {
  const res = await fetch("/api/evals");
  return res.json();
}

function renderRunList(runs, onSelect) {
  if (!runs || !runs.length) {
    return `<div class="empty">暂无研究 run。使用 /research 或 /committee 启动。</div>`;
  }
  return `<div class="at-run-list">${runs
    .map(
      (r) => `
    <button type="button" class="at-run-item" data-run="${escapeHtml(r.run_id)}">
      <div class="at-run-top">
        <span class="at-run-id">${escapeHtml(r.run_id)}</span>
        <span class="at-run-mode">${escapeHtml(r.mode)}</span>
        <span class="at-run-st ${escapeHtml(r.status)}">${escapeHtml(r.status)}</span>
      </div>
      <div class="at-run-query">${escapeHtml((r.query || "").slice(0, 120))}</div>
      <div class="at-run-time">${escapeHtml(String(r.created_at || "").slice(0, 19))}</div>
    </button>`
    )
    .join("")}</div>`;
}

function renderRunDetail(d) {
  if (d.status === "error") {
    return `<div class="empty">${escapeHtml(d.error || "加载失败")}</div>`;
  }
  const reports = d.reports || [];
  const policy = d.policy || [];
  const lineage = d.lineage || [];
  const claims = d.claims || [];
  const evidence = d.evidence || [];
  const tools = d.tool_calls || [];

  let html = `<div class="at-detail-head">
    <h3>Run ${escapeHtml(d.run_id)}</h3>
    <p>${escapeHtml(d.query || "")}</p>
    <div class="at-detail-meta">${escapeHtml(d.mode)} · ${escapeHtml(d.status)} · ${escapeHtml(String(d.created_at || "").slice(0, 19))}</div>
  </div>`;

  if (lineage.length) {
    html += `<h4>决策血缘</h4><div class="at-lineage">${lineage
      .map(
        (row) =>
          `<div class="at-line-item"><span class="at-line-step">${escapeHtml(row.step)}</span> ` +
          `<span class="at-line-sym">${escapeHtml(row.symbol || "—")}</span> ` +
          `<code>${escapeHtml(JSON.stringify(row.payload || {}).slice(0, 120))}</code></div>`
      )
      .join("")}</div>`;
  }

  if (policy.length) {
    html += `<h4>政策门</h4><div class="at-policy">${policy
      .map(
        (p) =>
          `<div class="at-policy-item ${p.approved ? "ok" : "no"}">` +
          `<strong>${escapeHtml(p.engine)}</strong> ${p.approved ? "✓" : "✗"} ` +
          `<code>${escapeHtml(JSON.stringify(p.payload || {}).slice(0, 100))}</code></div>`
      )
      .join("")}</div>`;
  }

  if (evidence.length) {
    html += `<h4>证据 (${evidence.length})</h4><ul class="at-evidence">${evidence
      .map(
        (e) =>
          `<li>${escapeHtml(e.symbol || "")} pit=${e.pit_safe ? "✓" : "✗"} ` +
          `q=${escapeHtml(e.quality || "")}</li>`
      )
      .join("")}</ul>`;
  }

  if (claims.length) {
    html += `<h4>结构化结论</h4><pre class="at-json">${escapeHtml(JSON.stringify(claims, null, 2).slice(0, 4000))}</pre>`;
  }

  html += `<h4>Agent 报告</h4>`;
  reports.forEach((r) => {
    const body = (r.content || "").slice(0, 3000);
    html += `<details class="at-report"><summary>${escapeHtml(r.agent_name)}</summary><pre>${escapeHtml(body)}</pre></details>`;
  });

  if (tools.length) {
    html += `<h4>工具调用 (${tools.length})</h4><div class="at-tools">${tools
      .slice(-20)
      .map(
        (t) =>
          `<div class="at-tool-row"><span>${escapeHtml(t.agent_name)}</span> ` +
          `<code>${escapeHtml(t.tool_name)}</code></div>`
      )
      .join("")}</div>`;
  }

  return html;
}

function renderEvalsDashboard(data) {
  if (data.status === "empty") {
    return `<div class="empty">${escapeHtml(data.message || "无评估数据")}<br>设置 FIAGENT_EVAL=1 后运行对话。</div>`;
  }
  const vs = data.by_variant || data.variant_stats || {};
  const costMap = data.cost_index_vs_fast || data.cost_vs_fast || {};
  const rows = Object.entries(vs)
    .map(([k, st]) => {
      const cost = costMap[k];
      return `<tr>
        <td><strong>${escapeHtml(k)}</strong></td>
        <td>${st.runs}</td>
        <td>${(st.success_rate * 100).toFixed(0)}%</td>
        <td>${Math.round(st.avg_latency_ms)}ms</td>
        <td>${st.avg_tool_calls.toFixed(1)}</td>
        <td>${(st.pit_unsafe_rate * 100).toFixed(0)}%</td>
        <td>${cost != null ? cost + "×" : "—"}</td>
      </tr>`;
    })
    .join("");

  let html = `<p class="at-eval-note">变体：A=Fast · B=Research无Red · C=Research+Red · D=Committee · R=Review</p>`;
  html += `<table class="at-eval-table"><thead><tr>
    <th>变体</th><th>次数</th><th>成功率</th><th>延迟</th><th>工具</th><th>PIT命中</th><th>成本比</th>
  </tr></thead><tbody>${rows}</tbody></table>`;

  const tools = (data.aggregate && data.aggregate.top_tools) || data.top_tools || [];
  if (tools.length) {
    html += `<h4>高频工具</h4><ul>${tools
      .slice(0, 10)
      .map((t) => {
        const name = Array.isArray(t) ? t[0] : t.name;
        const cnt = Array.isArray(t) ? t[1] : t.count;
        return `<li><code>${escapeHtml(name || "")}</code> × ${cnt || ""}</li>`;
      })
      .join("")}</ul>`;
  }
  return html;
}

async function openTeamFeature() {
  if (typeof setSidebarOpen === "function") setSidebarOpen(false);
  if (typeof closePalette === "function") closePalette();
  if (typeof closePicker === "function") closePicker();

  const root = document.getElementById("feature");
  root.setAttribute("data-kind", "team");
  root.classList.remove("hidden");
  root.setAttribute("aria-hidden", "false");
  document.getElementById("feature-title").textContent = "研究团队";
  const form = document.getElementById("feature-form");
  const result = document.getElementById("feature-result");
  document.getElementById("feature-meta").textContent = "证据账本 · 决策血缘 · 政策门";
  form.innerHTML = `<button type="button" class="run" id="at-refresh-runs">刷新列表</button>`;
  result.innerHTML = `<div class="empty">加载中…</div>`;

  async function loadList() {
    result.innerHTML = `<div class="empty">加载中…</div>`;
    const data = await fetchResearchRuns();
    result.innerHTML = renderRunList(data.runs || []);
    result.querySelectorAll(".at-run-item").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const rid = btn.dataset.run;
        result.innerHTML = `<div class="empty">加载 run ${escapeHtml(rid)}…</div>`;
        const detail = await fetchRunDetail(rid);
        result.innerHTML =
          `<button type="button" class="ghost at-back">← 返回列表</button>` + renderRunDetail(detail);
        const back = result.querySelector(".at-back");
        if (back) back.addEventListener("click", loadList);
      });
    });
  }

  document.getElementById("at-refresh-runs").addEventListener("click", loadList);
  await loadList();
}

async function openEvalsFeature() {
  if (typeof setSidebarOpen === "function") setSidebarOpen(false);
  const root = document.getElementById("feature");
  root.setAttribute("data-kind", "evals");
  root.classList.remove("hidden");
  root.setAttribute("aria-hidden", "false");
  document.getElementById("feature-title").textContent = "Agent 评估";
  document.getElementById("feature-form").innerHTML =
    `<button type="button" class="run" id="at-refresh-evals">刷新</button>`;
  const result = document.getElementById("feature-result");
  document.getElementById("feature-meta").textContent = "FIAGENT_EVAL=1 启用记录";
  async function load() {
    result.innerHTML = `<div class="empty">加载中…</div>`;
    const data = await fetchEvals();
    result.innerHTML = renderEvalsDashboard(data);
  }
  document.getElementById("at-refresh-evals").addEventListener("click", load);
  await load();
}

function wireAgentTeamUI() {
  const openBtn = document.getElementById("rail-team-open");
  if (openBtn) {
    openBtn.addEventListener("click", () => {
      if (typeof showDesk === "function") showDesk();
      if (typeof openFeature === "function") {
        openTeamFeature();
      }
    });
  }
  const closeBtn = document.getElementById("agent-panel-close");
  if (closeBtn) closeBtn.addEventListener("click", closeAgentDetailPanel);

  const dock = document.getElementById("rail-agent-dock");
  if (dock) {
    dock.addEventListener("click", (e) => {
      const node = e.target.closest(".at-node");
      if (!node) return;
      const agentId = node.getAttribute("title");
      if (agentId) openAgentDetailPanel(agentId);
    });
  }
}

window.AgentTeamUI = {
  handle: handleAgentTeamEvent,
  reset: resetAgentTeamRail,
  isActive: isTeamRunActive,
  routeLine: routeTeamLine,
  routeExternalError,
  parseBrief: parseResearchBrief,
  openAgent: openAgentDetailPanel,
  closeAgent: closeAgentDetailPanel,
  openTeam: openTeamFeature,
  openEvals: openEvalsFeature,
  wire: wireAgentTeamUI,
};

document.addEventListener("DOMContentLoaded", () => {
  wireAgentTeamUI();
});
