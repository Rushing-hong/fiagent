/* One-shot Agent collaboration tasks embedded in the main conversation. */

const COLLAB_AGENT_LABELS = {
  data_guardian: "Data Guardian",
  market_regime: "市场研究",
  company_research: "公司研究",
  quant_research: "量化研究",
  red_team: "反方审视",
  policy: "政策与风险门",
  orchestrator: "综合 Agent",
};

const collaborationState = {
  runs: new Map(),
  currentRunId: "",
  selectedAgent: "",
  historyMode: false,
  wired: false,
  eventSeq: 0,
};

function collabAgentLabel(id) {
  return COLLAB_AGENT_LABELS[id] || id || "子 Agent";
}

function collabModeLabel(mode) {
  if (mode === "committee") return "投资决策会";
  if (mode === "trade_review") return "交易复盘";
  return "自动组队研究";
}

function collabStatusLabel(status) {
  if (status === "running") return "执行中";
  if (status === "completed") return "已完成";
  if (status === "partial") return "部分完成";
  if (status === "failed") return "失败";
  return "等待中";
}

function collabStatusIcon(status) {
  if (status === "running") return "●";
  if (status === "completed") return "✓";
  if (status === "partial") return "!";
  if (status === "failed") return "×";
  return "○";
}

function expectedAgentIds(payload) {
  const ids = ["data_guardian"];
  const researchers = Array.isArray(payload.researchers) ? payload.researchers : [];
  researchers.forEach((id) => {
    if (id && !ids.includes(id)) ids.push(id);
  });
  if (!researchers.length) {
    ["market_regime", "company_research", "quant_research"].forEach((id) => ids.push(id));
  }
  if (payload.include_red_team !== false) ids.push("red_team");
  if (payload.mode === "committee") ids.push("policy");
  ids.push("orchestrator");
  return ids;
}

function createCollabRun(payload) {
  const runId = String(payload.run_id || "");
  let run = collaborationState.runs.get(runId);
  if (!run) {
    run = {
      runId,
      query: payload.query || "",
      mode: payload.mode || "research",
      workflow: payload.workflow || "",
      status: payload.status || "running",
      phase: "正在启动协作…",
      createdAt: payload.created_at || new Date().toISOString(),
      agents: new Map(),
      card: null,
      detailLoaded: false,
    };
    collaborationState.runs.set(runId, run);
  } else {
    run.query = payload.query || run.query;
    run.mode = payload.mode || run.mode;
    run.workflow = payload.workflow || run.workflow;
    run.status = payload.status || run.status;
  }
  expectedAgentIds(payload).forEach((id) => ensureCollabAgent(run, id));
  return run;
}

function ensureCollabAgent(run, agentId) {
  if (!run.agents.has(agentId)) {
    run.agents.set(agentId, {
      id: agentId,
      status: "pending",
      statusText: "等待上游任务",
      timeline: [],
      report: "",
      toolRounds: null,
      live: {},
    });
  }
  return run.agents.get(agentId);
}

function activeCollabRun() {
  return [...collaborationState.runs.values()].reverse().find((run) => run.status === "running") || null;
}

function nextCollabEventId() {
  collaborationState.eventSeq += 1;
  return `collab-event-${collaborationState.eventSeq}`;
}

function addCollabTimeline(agent, item) {
  const event = {
    id: nextCollabEventId(),
    kind: item.kind || "status",
    title: item.title || "进度",
    meta: item.meta || "",
    body: item.body || "",
    open: !!item.open,
  };
  agent.timeline.push(event);
  renderOpenCollaboration(agent.id);
  return event;
}

function updateCollabTimeline(agent, eventId, patch) {
  const event = agent.timeline.find((item) => item.id === eventId);
  if (!event) return;
  Object.assign(event, patch || {});
  renderOpenCollaboration(agent.id);
}

function collabCardAgentSummary(run) {
  const agents = [...run.agents.values()];
  const done = agents.filter((agent) => agent.status === "completed").length;
  const running = agents.find((agent) => agent.status === "running");
  if (running) return `${collabAgentLabel(running.id)}正在处理 · ${done}/${agents.length} 完成`;
  if (run.status === "running") return `正在分配任务 · ${done}/${agents.length} 完成`;
  return `${done}/${agents.length} 个子任务完成`;
}

function mountCollaborationCard(run) {
  const chatRoot = document.getElementById("chat");
  if (!chatRoot || !run.runId) return;
  if (run.card && document.body.contains(run.card)) {
    updateCollaborationCard(run);
    return;
  }
  const card = document.createElement("button");
  card.type = "button";
  card.className = "collaboration-card";
  card.dataset.runId = run.runId;
  card.addEventListener("click", () => openCollaborationRun(run.runId));
  run.card = card;
  chatRoot.appendChild(card);
  updateCollaborationCard(run);
  if (typeof scrollChat === "function") scrollChat();
}

function updateCollaborationCard(run) {
  const card = run.card;
  if (!card || !document.body.contains(card)) return;
  const agents = [...run.agents.values()];
  const visible = agents.slice(0, 6);
  const chips = visible.map((agent) => `
    <span class="collaboration-card-agent ${escapeHtml(agent.status)}">
      <span aria-hidden="true">${collabStatusIcon(agent.status)}</span>
      ${escapeHtml(collabAgentLabel(agent.id))}
    </span>`).join("");
  card.className = `collaboration-card ${escapeHtml(run.status || "running")}`;
  card.innerHTML = `
    <span class="collaboration-card-icon" aria-hidden="true">${run.status === "running" ? "◎" : "✓"}</span>
    <span class="collaboration-card-copy">
      <span class="collaboration-card-topline">
        <strong>${escapeHtml(collabModeLabel(run.mode))}</strong>
        <span>${escapeHtml(collabStatusLabel(run.status))}</span>
      </span>
      <span class="collaboration-card-query">${escapeHtml(run.query || "协作任务")}</span>
      <span class="collaboration-card-progress">${escapeHtml(collabCardAgentSummary(run))}</span>
      <span class="collaboration-card-agents">${chips}</span>
    </span>
    <span class="collaboration-card-open">查看任务 <span aria-hidden="true">›</span></span>`;
}

function setCollabAgentStatus(run, agentId, status, statusText) {
  const agent = ensureCollabAgent(run, agentId);
  agent.status = status;
  if (statusText) agent.statusText = statusText;
  updateCollaborationCard(run);
  renderOpenCollaboration(agentId);
  return agent;
}

function handleCollabAgentUi(run, event) {
  const agent = ensureCollabAgent(run, event.agent);
  const type = event.ui_type || "";
  if (type === "activity") {
    agent.statusText = event.text || agent.statusText;
    renderOpenCollaboration(agent.id);
    return;
  }
  if (type === "think_begin") {
    const item = addCollabTimeline(agent, {
      kind: "think",
      title: "思考",
      meta: "进行中",
      body: "正在思考…",
      open: true,
    });
    agent.live.think = item.id;
    return;
  }
  if (type === "think_delta") {
    if (agent.live.think) {
      updateCollabTimeline(agent, agent.live.think, {
        body: event.text || "",
        meta: `${String(event.text || "").length} 字`,
      });
    }
    return;
  }
  if (type === "think_end") {
    if (agent.live.think) {
      updateCollabTimeline(agent, agent.live.think, {
        body: event.text || "（无思考内容）",
        meta: `${String(event.text || "").length} 字`,
        open: false,
      });
      agent.live.think = "";
    } else if (event.text) {
      addCollabTimeline(agent, {
        kind: "think",
        title: "思考",
        meta: `${String(event.text).length} 字`,
        body: event.text,
      });
    }
    return;
  }
  if (type === "tool_call") {
    addCollabTimeline(agent, {
      kind: "tool",
      title: `调用工具 · ${event.name || "tool"}`,
      meta: "查看参数",
      body: prettyToolText(event.args),
    });
    return;
  }
  if (type === "tool_result") {
    addCollabTimeline(agent, {
      kind: "tool",
      title: `工具结果 · ${event.name || "result"}`,
      meta: `${String(event.text || "").length} 字`,
      body: prettyToolText(event.text),
    });
    return;
  }
  if (type === "reply_begin") {
    const item = addCollabTimeline(agent, {
      kind: "reply",
      title: "Agent 输出",
      meta: "生成中",
      body: "",
      open: true,
    });
    agent.live.reply = item.id;
    return;
  }
  if (type === "reply_delta") {
    if (agent.live.reply) {
      updateCollabTimeline(agent, agent.live.reply, {
        body: event.text || "",
        meta: `${String(event.text || "").length} 字`,
      });
    }
    return;
  }
  if (type === "reply_end") {
    if (agent.live.reply) {
      updateCollabTimeline(agent, agent.live.reply, {
        body: event.text || "（无输出）",
        meta: `${String(event.text || "").length} 字`,
      });
      agent.live.reply = "";
    } else if (event.text) {
      addCollabTimeline(agent, {
        kind: "reply",
        title: "Agent 输出",
        meta: `${String(event.text).length} 字`,
        body: event.text,
      });
    }
    return;
  }
  if (type === "round" || type === "round_rule") {
    addCollabTimeline(agent, {
      kind: "status",
      title: type === "round" ? `第 ${event.round_idx || "?"} 轮` : (event.title || "工具轮次"),
      meta: "LLM",
      body: "",
    });
    return;
  }
  if ((type === "line" || type === "hook") && (event.message || event.tag)) {
    addCollabTimeline(agent, {
      kind: "status",
      title: event.tag || "运行信息",
      meta: event.level || "",
      body: event.message || "",
    });
  }
}

function handleCollaborationEvent(event) {
  const phase = event.phase || "";
  if (phase === "start") {
    const run = createCollabRun(event);
    run.status = "running";
    run.phase = "正在建立任务计划";
    mountCollaborationCard(run);
    return;
  }
  const run = collaborationState.runs.get(String(event.run_id || ""));
  if (!run) return;
  if (phase === "agent_start") {
    const agent = setCollabAgentStatus(run, event.agent, "running", "正在处理任务");
    addCollabTimeline(agent, { kind: "status", title: "任务开始", meta: "运行中", body: "" });
    run.phase = `${collabAgentLabel(event.agent)}正在处理`;
  } else if (phase === "agent_ui") {
    handleCollabAgentUi(run, event);
  } else if (phase === "agent_tool") {
    const agent = ensureCollabAgent(run, event.agent);
    agent.statusText = `正在使用 ${event.tool || "工具"}`;
    renderOpenCollaboration(agent.id);
  } else if (phase === "agent_log") {
    const agent = ensureCollabAgent(run, event.agent || "orchestrator");
    addCollabTimeline(agent, {
      kind: event.level === "error" ? "error" : "status",
      title: event.level === "error" ? "运行异常" : "运行信息",
      meta: event.level || "",
      body: event.message || "",
      open: event.level === "error",
    });
  } else if (phase === "agent_done") {
    const status = event.status === "completed" ? "completed" : "failed";
    const agent = setCollabAgentStatus(
      run,
      event.agent,
      status,
      status === "completed" ? "任务已完成" : "任务未完成"
    );
    agent.report = event.preview || agent.report;
    agent.toolRounds = event.tool_rounds;
    run.phase = `${collabAgentLabel(event.agent)}${collabStatusLabel(status)}`;
  } else if (phase === "policy_start") {
    setCollabAgentStatus(run, "policy", "running", "正在执行合规、风险与执行检查");
  } else if (phase === "policy_done") {
    setCollabAgentStatus(run, "policy", "completed", "政策与风险门已完成");
  } else if (phase === "cio_start") {
    setCollabAgentStatus(run, "orchestrator", "running", "正在综合所有子任务");
  } else if (phase === "complete") {
    run.status = event.status || (event.failed_count ? "partial" : "completed");
    const failed = run.status === "failed";
    setCollabAgentStatus(
      run,
      "orchestrator",
      failed ? "failed" : "completed",
      failed ? (event.error || "协作任务失败") : "综合结论已返回主对话"
    );
    run.phase = failed ? "协作任务未完成" : "协作完成，结论已回到主对话";
    updateCollaborationCard(run);
    setTimeout(() => refreshCollaborationRun(run.runId, { silent: true }), 150);
  }
  updateCollaborationCard(run);
  renderOpenCollaboration();
}

async function fetchCollaborationRun(runId) {
  return apiJson(`/api/research/runs/${encodeURIComponent(runId)}`, { timeoutMs: 15000 });
}

function mergeCollaborationDetail(run, detail) {
  run.query = detail.query || run.query;
  run.mode = detail.mode || run.mode;
  run.status = detail.run_status || detail.status || run.status;
  run.createdAt = detail.created_at || run.createdAt;
  run.phase = run.status === "running"
    ? "协作任务仍在执行"
    : run.status === "failed"
      ? "协作任务未完成"
      : "协作完成，结论已回到主对话";

  const actualAgentIds = new Set();
  (detail.tasks || []).forEach((task) => {
    actualAgentIds.add(task.agent_name);
    const agent = ensureCollabAgent(run, task.agent_name);
    agent.status = task.status || agent.status;
    agent.statusText = task.status === "completed" ? "任务已完成" : collabStatusLabel(task.status);
  });
  if (run.mode === "committee") {
    actualAgentIds.add("policy");
    const policy = ensureCollabAgent(run, "policy");
    if (run.status !== "running" && (detail.policy || []).length) {
      policy.status = "completed";
      policy.statusText = "政策与风险门已完成";
    }
  }
  (detail.reports || []).forEach((report) => {
    actualAgentIds.add(report.agent_name);
    const agent = ensureCollabAgent(run, report.agent_name);
    agent.report = report.content || agent.report;
    if (agent.status === "pending") {
      agent.status = "completed";
      agent.statusText = "任务已完成";
    }
  });
  if (!(detail.reports || []).some((report) => report.agent_name === "orchestrator")) {
    ensureCollabAgent(run, "orchestrator");
  }
  actualAgentIds.add("orchestrator");
  if (run.status !== "running") {
    [...run.agents.entries()].forEach(([id, agent]) => {
      if (!actualAgentIds.has(id) && agent.status === "pending") run.agents.delete(id);
    });
  }

  const toolGroups = new Map();
  (detail.tool_calls || []).forEach((tool) => {
    const id = tool.agent_name || "orchestrator";
    if (!toolGroups.has(id)) toolGroups.set(id, []);
    toolGroups.get(id).push(tool);
  });
  toolGroups.forEach((tools, agentId) => {
    const agent = ensureCollabAgent(run, agentId);
    if (agent.timeline.length) return;
    tools.forEach((tool) => {
      agent.timeline.push({
        id: nextCollabEventId(),
        kind: "tool",
        title: `调用工具 · ${tool.tool_name || "tool"}`,
        meta: tool.success === false ? "失败" : "已完成",
        body: tool.result_snip || tool.arguments || "",
        open: false,
      });
    });
  });
  run.detailLoaded = true;
}

async function refreshCollaborationRun(runId, { silent = false } = {}) {
  const run = collaborationState.runs.get(runId) || createCollabRun({ run_id: runId });
  try {
    const detail = await fetchCollaborationRun(runId);
    if (detail.status === "error") throw new Error(detail.error || "任务不存在");
    mergeCollaborationDetail(run, detail);
    updateCollaborationCard(run);
    renderOpenCollaboration();
    return run;
  } catch (error) {
    if (!silent && typeof notify === "function") {
      notify(friendlyError(error, "协作任务加载失败"), "error");
    }
    return run;
  }
}

function setCollaborationViewOpen(open) {
  const view = document.getElementById("collaboration-view");
  if (!view) return;
  view.classList.toggle("hidden", !open);
  view.setAttribute("aria-hidden", open ? "false" : "true");
  document.body.classList.toggle("collaboration-view-open", !!open);
}

function closeCollaborationView() {
  setCollaborationViewOpen(false);
  collaborationState.currentRunId = "";
  collaborationState.selectedAgent = "";
  collaborationState.historyMode = false;
  if (typeof input !== "undefined" && input) input.focus();
}

function renderCollaborationRunSummary(run) {
  const box = document.getElementById("collaboration-run-summary");
  if (!box) return;
  box.innerHTML = `
    <div class="collaboration-run-mode">${escapeHtml(collabModeLabel(run.mode))}</div>
    <div class="collaboration-run-status ${escapeHtml(run.status)}">
      <span aria-hidden="true">${collabStatusIcon(run.status)}</span>
      ${escapeHtml(collabStatusLabel(run.status))}
    </div>
    <p>${escapeHtml(run.query || "协作任务")}</p>
    <code>${escapeHtml(run.runId)}</code>`;
}

function renderCollaborationAgentList(run) {
  const list = document.getElementById("collaboration-agent-list");
  if (!list) return;
  list.innerHTML = [...run.agents.values()].map((agent) => `
    <button type="button" class="collaboration-agent-item ${escapeHtml(agent.status)}${collaborationState.selectedAgent === agent.id ? " active" : ""}" data-agent="${escapeHtml(agent.id)}">
      <span class="collaboration-agent-state" aria-hidden="true">${collabStatusIcon(agent.status)}</span>
      <span class="collaboration-agent-copy">
        <strong>${escapeHtml(collabAgentLabel(agent.id))}</strong>
        <small>${escapeHtml(agent.statusText || collabStatusLabel(agent.status))}</small>
      </span>
      <span aria-hidden="true">›</span>
    </button>`).join("");
  list.querySelectorAll("[data-agent]").forEach((button) => {
    button.addEventListener("click", () => {
      collaborationState.selectedAgent = button.dataset.agent || "";
      renderCollaborationRun(run);
    });
  });
}

function renderCollabEvent(item) {
  if (item.kind === "reply") {
    return `<section class="collaboration-message">
      <div class="collaboration-message-avatar">A</div>
      <div class="collaboration-message-copy">
        <div class="collaboration-message-meta">${escapeHtml(item.title)} <span>${escapeHtml(item.meta || "")}</span></div>
        <div class="collaboration-markdown">${renderMd(item.body || "（等待输出）")}</div>
      </div>
    </section>`;
  }
  const icon = item.kind === "tool" ? "⚙" : item.kind === "think" ? "◇" : item.kind === "error" ? "!" : "·";
  return `<details class="collaboration-event ${escapeHtml(item.kind)}"${item.open ? " open" : ""}>
    <summary>
      <span class="collaboration-event-icon" aria-hidden="true">${icon}</span>
      <strong>${escapeHtml(item.title)}</strong>
      <span>${escapeHtml(item.meta || "")}</span>
    </summary>
    ${item.body ? `<pre>${escapeHtml(item.body)}</pre>` : ""}
  </details>`;
}

function renderCollaborationThread(run) {
  const head = document.getElementById("collaboration-thread-head");
  const timeline = document.getElementById("collaboration-timeline");
  if (!head || !timeline) return;
  const agent = ensureCollabAgent(run, collaborationState.selectedAgent);
  head.innerHTML = `
    <div>
      <span class="collaboration-thread-kicker">SUB-AGENT</span>
      <h3>${escapeHtml(collabAgentLabel(agent.id))}</h3>
      <p>${escapeHtml(agent.statusText || collabStatusLabel(agent.status))}</p>
    </div>
    <span class="collaboration-thread-status ${escapeHtml(agent.status)}">${escapeHtml(collabStatusLabel(agent.status))}</span>`;

  let html = agent.timeline.map(renderCollabEvent).join("");
  if (agent.report) {
    html += `<section class="collaboration-message report">
      <div class="collaboration-message-avatar">A</div>
      <div class="collaboration-message-copy">
        <div class="collaboration-message-meta">${escapeHtml(collabAgentLabel(agent.id))} 报告</div>
        <div class="collaboration-markdown">${renderMd(agent.report)}</div>
      </div>
    </section>`;
  }
  if (!html) {
    html = `<div class="collaboration-empty">
      <span class="collaboration-empty-icon" aria-hidden="true">${agent.status === "running" ? "◎" : "○"}</span>
      <strong>${agent.status === "running" ? "子 Agent 正在工作" : "等待任务开始"}</strong>
      <p>思考、工具调用与输出会实时显示在这里。</p>
    </div>`;
  }
  timeline.innerHTML = html;
  timeline.querySelectorAll(".collaboration-markdown").forEach((node) => decorateMd(node));
  timeline.scrollTop = agent.status === "running" ? timeline.scrollHeight : 0;
}

function renderCollaborationRun(run) {
  const title = document.getElementById("collaboration-title");
  const subtitle = document.getElementById("collaboration-subtitle");
  if (title) title.textContent = run.query || "协作任务";
  if (subtitle) subtitle.textContent = `${collabModeLabel(run.mode)} · ${run.phase || collabStatusLabel(run.status)}`;
  if (!collaborationState.selectedAgent || !run.agents.has(collaborationState.selectedAgent)) {
    const running = [...run.agents.values()].find((agent) => agent.status === "running");
    collaborationState.selectedAgent = (running || [...run.agents.values()][0] || {}).id || "orchestrator";
  }
  renderCollaborationRunSummary(run);
  renderCollaborationAgentList(run);
  renderCollaborationThread(run);
}

function renderOpenCollaboration(agentId) {
  if (!document.body.classList.contains("collaboration-view-open")) return;
  const run = collaborationState.runs.get(collaborationState.currentRunId);
  if (!run || collaborationState.historyMode) return;
  if (agentId && collaborationState.selectedAgent !== agentId) {
    renderCollaborationAgentList(run);
    return;
  }
  renderCollaborationRun(run);
}

async function openCollaborationRun(runId) {
  const run = collaborationState.runs.get(runId) || createCollabRun({ run_id: runId });
  collaborationState.currentRunId = runId;
  collaborationState.historyMode = false;
  collaborationState.selectedAgent =
    ([...run.agents.values()].find((agent) => agent.status === "running") || [...run.agents.values()][0] || {}).id || "";
  if (typeof setSidebarOpen === "function") setSidebarOpen(false);
  if (typeof setRailOpen === "function") setRailOpen(false);
  setCollaborationViewOpen(true);
  renderCollaborationRun(run);
  await refreshCollaborationRun(runId, { silent: run.status === "running" });
}

function mountHistoryCollaborationCard(metadata) {
  if (!metadata || !metadata.run_id) return;
  const run = createCollabRun({
    run_id: metadata.run_id,
    query: metadata.query || "",
    mode: metadata.mode || "research",
    status: metadata.status || "completed",
    include_red_team: metadata.mode !== "trade_review",
    researchers: metadata.researchers || [],
  });
  if (run.status === "running") run.status = "completed";
  run.phase = "点击查看子 Agent 任务与报告";
  mountCollaborationCard(run);
  if (!run.detailLoaded) {
    setTimeout(() => refreshCollaborationRun(run.runId, { silent: true }), 0);
  }
}

function parseLegacyCollaborationBrief(content) {
  const text = String(content || "");
  if (!text.includes("## Research Run ID") || !text.includes("## 用户问题")) return null;
  const runMatch = text.match(/## Research Run ID\s*\n([^\n]+)/);
  const modeMatch = text.match(/## 运行模式\s*\n([^\n]+)/);
  const queryMatch = text.match(/## 用户问题\s*\n([^\n]+)/);
  return {
    run_id: (runMatch && runMatch[1].trim()) || "",
    mode: (modeMatch && modeMatch[1].trim()) || "research",
    query: (queryMatch && queryMatch[1].trim()) || "",
    status: "completed",
  };
}

async function openCollaborationHistory() {
  if (typeof setSidebarOpen === "function") setSidebarOpen(false);
  if (typeof setRailOpen === "function") setRailOpen(false);
  collaborationState.currentRunId = "";
  collaborationState.selectedAgent = "";
  collaborationState.historyMode = true;
  setCollaborationViewOpen(true);
  const title = document.getElementById("collaboration-title");
  const subtitle = document.getElementById("collaboration-subtitle");
  const summary = document.getElementById("collaboration-run-summary");
  const list = document.getElementById("collaboration-agent-list");
  const head = document.getElementById("collaboration-thread-head");
  const timeline = document.getElementById("collaboration-timeline");
  if (title) title.textContent = "协作任务";
  if (subtitle) subtitle.textContent = "从主对话委派出去的 Agent 工作";
  if (summary) summary.innerHTML = `<div class="collaboration-run-mode">TASK HISTORY</div><p>选择一项查看子 Agent 过程与报告。</p>`;
  if (list) list.innerHTML = `<div class="collaboration-list-loading">正在加载…</div>`;
  if (head) head.innerHTML = `<div><span class="collaboration-thread-kicker">MAIN THREAD</span><h3>任务详情</h3><p>选择左侧协作任务</p></div>`;
  if (timeline) timeline.innerHTML = `<div class="collaboration-empty"><span class="collaboration-empty-icon">↗</span><strong>主对话中的一次性委派</strong><p>任务完成后会回到原对话，不会切换成另一个聊天环境。</p></div>`;
  try {
    const data = await apiJson("/api/research/runs?limit=40", { timeoutMs: 15000 });
    const runs = data.runs || [];
    if (!list) return;
    if (!runs.length) {
      list.innerHTML = `<div class="collaboration-list-loading">暂无协作任务</div>`;
      return;
    }
    list.innerHTML = runs.map((item) => `
      <button type="button" class="collaboration-history-item ${escapeHtml(item.status || "")}" data-run="${escapeHtml(item.run_id)}">
        <span class="collaboration-agent-state">${collabStatusIcon(item.status)}</span>
        <span class="collaboration-agent-copy">
          <strong>${escapeHtml((item.query || "协作任务").slice(0, 56))}</strong>
          <small>${escapeHtml(collabModeLabel(item.mode))} · ${escapeHtml(collabStatusLabel(item.status))}</small>
        </span>
        <span>›</span>
      </button>`).join("");
    list.querySelectorAll("[data-run]").forEach((button) => {
      button.addEventListener("click", () => openCollaborationRun(button.dataset.run || ""));
    });
  } catch (error) {
    if (list) list.innerHTML = `<div class="collaboration-list-loading">${escapeHtml(friendlyError(error, "加载失败"))}</div>`;
  }
}

function routeCollaborationLine(text, classes) {
  const run = activeCollabRun();
  if (!run) return false;
  const raw = String(text || "").trim();
  if (!raw) return false;
  if (raw.includes("协作任务已启动") || raw.includes("多 Agent 模式")) return true;
  if ((classes || "").includes("line-err") || /chunked read|流式连接|LLM 请求/.test(raw)) {
    const agent = ensureCollabAgent(run, "orchestrator");
    addCollabTimeline(agent, {
      kind: "error",
      title: "运行异常",
      meta: "需要关注",
      body: raw,
      open: true,
    });
    return true;
  }
  return false;
}

function renderCollaborationEvals(data) {
  if (data.status === "empty") {
    return `<div class="empty">${escapeHtml(data.message || "无评估数据")}</div>`;
  }
  const variants = data.by_variant || data.variant_stats || {};
  const rows = Object.entries(variants).map(([key, stat]) => `<tr>
    <td><strong>${escapeHtml(key)}</strong></td>
    <td>${Number(stat.runs || 0)}</td>
    <td>${(Number(stat.success_rate || 0) * 100).toFixed(0)}%</td>
    <td>${Math.round(Number(stat.avg_latency_ms || 0))}ms</td>
    <td>${Number(stat.avg_tool_calls || 0).toFixed(1)}</td>
  </tr>`).join("");
  return `<table class="at-eval-table"><thead><tr><th>变体</th><th>次数</th><th>成功率</th><th>延迟</th><th>工具</th></tr></thead><tbody>${rows}</tbody></table>`;
}

async function openCollaborationEvals() {
  if (typeof setSidebarOpen === "function") setSidebarOpen(false);
  const root = document.getElementById("feature");
  root.setAttribute("data-kind", "evals");
  root.classList.remove("hidden");
  root.setAttribute("aria-hidden", "false");
  document.getElementById("feature-title").textContent = "Agent 评估";
  document.getElementById("feature-form").innerHTML = `<button type="button" class="run" id="collab-refresh-evals">刷新</button>`;
  document.getElementById("feature-meta").textContent = "协作流程成功率、延迟与工具用量";
  const result = document.getElementById("feature-result");
  async function load() {
    result.innerHTML = `<div class="empty">加载中…</div>`;
    try {
      result.innerHTML = renderCollaborationEvals(await apiJson("/api/evals", { timeoutMs: 15000 }));
    } catch (error) {
      result.innerHTML = `<div class="empty">${escapeHtml(friendlyError(error, "评估数据加载失败"))}</div>`;
    }
  }
  document.getElementById("collab-refresh-evals").addEventListener("click", load);
  await load();
}

function wireCollaborationUI() {
  if (collaborationState.wired) return;
  collaborationState.wired = true;
  const back = document.getElementById("collaboration-back");
  if (back) back.addEventListener("click", closeCollaborationView);
  const refresh = document.getElementById("collaboration-refresh");
  if (refresh) {
    refresh.addEventListener("click", () => {
      if (collaborationState.historyMode || !collaborationState.currentRunId) {
        openCollaborationHistory();
      } else {
        refreshCollaborationRun(collaborationState.currentRunId);
      }
    });
  }
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !document.body.classList.contains("collaboration-view-open")) return;
    event.preventDefault();
    closeCollaborationView();
  });
}

window.CollaborationUI = {
  handle: handleCollaborationEvent,
  onTurnReset() {},
  routeLine: routeCollaborationLine,
  mountHistoryCard: mountHistoryCollaborationCard,
  parseLegacyBrief: parseLegacyCollaborationBrief,
  openRun: openCollaborationRun,
  openHistory: openCollaborationHistory,
  openEvals: openCollaborationEvals,
  close: closeCollaborationView,
  wire: wireCollaborationUI,
};

document.addEventListener("DOMContentLoaded", wireCollaborationUI);
