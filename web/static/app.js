const state = {
  sessionId: "default",
  rawSession: false,
  busy: false,
  sessions: [],
  memoryFiles: [],
  activeMemory: "MEMORY.md",
  sessionFilter: "",
  sidebarPanel: localStorage.getItem("sidebarPanel") || "sessions",
  sidebarCollapsed: localStorage.getItem("sidebarCollapsed") === "1",
  mobileSidebarOpen: false,
  currentMode: "hybrid",
};

const els = {
  appShell: document.querySelector(".app-shell"),
  sidebarToggle: document.querySelector("#sidebarToggle"),
  sidebarOverlay: document.querySelector("#sidebarOverlay"),
  mobileSidebarOpen: document.querySelector("#mobileSidebarOpen"),
  sidebarTabs: [...document.querySelectorAll("[data-sidebar-tab]")],
  sidebarPanels: [...document.querySelectorAll("[data-sidebar-panel]")],
  workspaceLabel: document.querySelector("#workspaceLabel"),
  workspacePath: document.querySelector("#workspacePath"),
  statusBadge: document.querySelector("#statusBadge"),
  sessionsList: document.querySelector("#sessionsList"),
  sessionSearch: document.querySelector("#sessionSearch"),
  newSessionBtn: document.querySelector("#newSessionBtn"),
  refreshMemoryBtn: document.querySelector("#refreshMemoryBtn"),
  memoryTabs: document.querySelector("#memoryTabs"),
  memoryContent: document.querySelector("#memoryContent"),
  sessionTitle: document.querySelector("#sessionTitle"),
  chatScroll: document.querySelector("#chatScroll"),
  messages: document.querySelector("#messages"),
  composer: document.querySelector("#composer"),
  input: document.querySelector("#messageInput"),
  sendBtn: document.querySelector("#sendBtn"),
  composerState: document.querySelector("#composerState"),
  modeActions: document.querySelector(".mode-actions"),
  activeSessionMetric: document.querySelector("#activeSessionMetric"),
  modeMetric: document.querySelector("#modeMetric"),
  sessionCountMetric: document.querySelector("#sessionCountMetric"),
  memoryCountMetric: document.querySelector("#memoryCountMetric"),
};

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
}

function setStatus(text, kind = "") {
  els.statusBadge.textContent = text;
  els.statusBadge.className = `status-badge ${kind}`.trim();
}

function setBusy(value) {
  state.busy = value;
  els.sendBtn.disabled = value || state.rawSession;
  els.input.disabled = value || state.rawSession;
  els.composerState.textContent = value ? "思考中" : state.rawSession ? "只读会话" : "";
}

function setSidebarPanel(panelName) {
  state.sidebarPanel = panelName;
  localStorage.setItem("sidebarPanel", panelName);

  for (const tab of els.sidebarTabs) {
    tab.classList.toggle("active", tab.dataset.sidebarTab === panelName);
  }
  for (const panel of els.sidebarPanels) {
    panel.classList.toggle("active", panel.dataset.sidebarPanel === panelName);
  }
}

function isMobileViewport() {
  return window.matchMedia("(max-width: 860px)").matches;
}

function setMobileSidebarOpen(value) {
  state.mobileSidebarOpen = value;
  els.appShell.classList.toggle("is-sidebar-open", value);
  document.body.classList.toggle("no-scroll", value);
  els.mobileSidebarOpen.setAttribute("aria-expanded", value ? "true" : "false");
  els.sidebarToggle.textContent = value && isMobileViewport() ? "×" : "‹";
  if (isMobileViewport()) {
    els.sidebarToggle.title = value ? "关闭侧栏" : "折叠侧栏";
    els.sidebarToggle.setAttribute("aria-label", value ? "关闭侧栏" : "折叠侧栏");
  }
}

function setSidebarCollapsed(value) {
  state.sidebarCollapsed = value;
  localStorage.setItem("sidebarCollapsed", value ? "1" : "0");
  els.appShell.classList.toggle("is-collapsed", !isMobileViewport() && value);
  els.sidebarToggle.title = value ? "展开侧栏" : "折叠侧栏";
  els.sidebarToggle.setAttribute("aria-label", value ? "展开侧栏" : "折叠侧栏");
}

function syncResponsiveSidebar() {
  if (isMobileViewport()) {
    els.appShell.classList.remove("is-collapsed");
    setMobileSidebarOpen(false);
    return;
  }
  setMobileSidebarOpen(false);
  els.appShell.classList.toggle("is-collapsed", state.sidebarCollapsed);
}

function updateMetrics(session = {}) {
  const mode = session.current_mode || state.currentMode || "hybrid";
  state.currentMode = mode;
  els.activeSessionMetric.textContent = state.sessionId || "default";
  els.modeMetric.textContent = mode;
  els.sessionCountMetric.textContent = String(state.sessions.length);
  els.memoryCountMetric.textContent = String(state.memoryFiles.length);
}

function messageText(message) {
  const content = message?.content ?? "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => {
        if (typeof part === "string") return part;
        return part?.text || JSON.stringify(part);
      })
      .join("");
  }
  return JSON.stringify(content, null, 2);
}

function roleLabel(role) {
  const labels = {
    user: "你",
    assistant: "Agent",
    tool: "Tool",
    system: "System",
  };
  return labels[role] || role || "message";
}

function scrollMessagesToBottom() {
  els.chatScroll.scrollTop = els.chatScroll.scrollHeight;
}

function renderMessages(messages) {
  els.messages.innerHTML = "";
  const visibleMessages = (messages || []).filter((message) => message.role !== "system");
  if (visibleMessages.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "新的 Web 会话";
    els.messages.append(empty);
    return;
  }

  for (const message of visibleMessages) {
    const item = document.createElement("article");
    item.className = `message ${message.role || "assistant"}`;

    const role = document.createElement("div");
    role.className = "message-role";
    role.textContent = roleLabel(message.role);

    const body = document.createElement("div");
    body.className = "message-body";
    body.textContent = messageText(message);

    item.append(role, body);
    els.messages.append(item);
  }
  scrollMessagesToBottom();
}

function renderSessions(sessions = state.sessions) {
  els.sessionsList.innerHTML = "";
  const filter = state.sessionFilter.trim().toLowerCase();
  const rows = (sessions || []).filter((session) => {
    const label = session.channel === "web" ? session.chat_id : session.id;
    return !filter || `${label} ${session.current_mode || ""}`.toLowerCase().includes(filter);
  });

  if (rows.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = filter ? "没有匹配会话" : "暂无会话";
    els.sessionsList.append(empty);
    updateMetrics();
    return;
  }

  for (const session of rows) {
    const button = document.createElement("button");
    const isWeb = session.channel === "web";
    const label = isWeb ? session.chat_id : session.id;
    button.type = "button";
    button.className = `session-item ${label === state.sessionId ? "active" : ""}`;

    const name = document.createElement("span");
    name.className = "session-name";
    name.textContent = label;

    const meta = document.createElement("span");
    meta.className = "session-meta";
    meta.textContent = `${session.current_mode || "hybrid"} · ${formatDate(session.updated_at)}`;

    button.append(name, meta);
    button.addEventListener("click", () => {
      state.rawSession = !isWeb;
      state.sessionId = label;
      loadSession(label, !isWeb);
      if (isMobileViewport()) {
        setMobileSidebarOpen(false);
      }
    });
    els.sessionsList.append(button);
  }
  updateMetrics();
}

function renderMemory() {
  els.memoryTabs.innerHTML = "";
  for (const file of state.memoryFiles) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = file.name.replace(".md", "");
    button.className = file.name === state.activeMemory ? "active" : "";
    button.addEventListener("click", () => {
      state.activeMemory = file.name;
      renderMemory();
    });
    els.memoryTabs.append(button);
  }

  const active = state.memoryFiles.find((file) => file.name === state.activeMemory)
    || state.memoryFiles[0];
  els.memoryContent.textContent = active?.content || "";
  updateMetrics();
}

function formatDate(value) {
  if (!value) return "未保存";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function loadHealth() {
  try {
    const data = await fetchJson("/api/health");
    els.workspaceLabel.textContent = data.workspace;
    els.workspacePath.textContent = data.workspace;
    setStatus("就绪", "ready");
  } catch (error) {
    setStatus("异常", "error");
    els.workspaceLabel.textContent = error.message;
    els.workspacePath.textContent = error.message;
  }
}

async function loadSessions() {
  const data = await fetchJson("/api/sessions");
  state.sessions = data.sessions || [];
  renderSessions();
}

async function loadSession(sessionId = state.sessionId, raw = state.rawSession) {
  const data = await fetchJson(
    `/api/session?session_id=${encodeURIComponent(sessionId)}&raw=${raw ? "1" : "0"}`,
  );
  const session = data.session || {};
  const channel = session.channel || (session.id?.startsWith("web:") ? "web" : "");
  state.sessionId = session.chat_id || sessionId;
  state.rawSession = raw || (channel !== "web" && channel !== "");
  state.currentMode = session.current_mode || "hybrid";
  els.sessionTitle.textContent = state.sessionId;
  updateMetrics(session);
  setBusy(false);
  renderMessages(session.messages || []);
  await loadSessions();
}

async function loadMemory() {
  const data = await fetchJson("/api/memory");
  state.memoryFiles = data.files || [];
  if (!state.memoryFiles.some((file) => file.name === state.activeMemory)) {
    state.activeMemory = state.memoryFiles[0]?.name || "";
  }
  renderMemory();
}

async function sendMessage(message) {
  setBusy(true);
  if (!els.messages.querySelector(".message") || els.messages.querySelector(".empty-state")) {
    els.messages.innerHTML = "";
  }
  renderOptimisticUserMessage(message);

  try {
    const data = await fetchJson("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.sessionId,
        message,
      }),
    });
    const savedMessages = data.session?.messages || [];
    state.currentMode = data.session?.current_mode || state.currentMode;
    renderMessages(savedMessages.length > 0
      ? savedMessages
      : [
        { role: "user", content: message },
        { role: "assistant", content: data.reply },
      ]);
    updateMetrics(data.session || {});
    await loadSessions();
  } catch (error) {
    renderMessages([
      { role: "user", content: message },
      { role: "assistant", content: error.message },
    ]);
    setStatus("异常", "error");
  } finally {
    setBusy(false);
    await loadMemory();
  }
}

function submitComposer() {
  const message = els.input.value.trim();
  if (!message || state.busy || state.rawSession) return;
  els.input.value = "";
  sendMessage(message);
}

function renderOptimisticUserMessage(message) {
  const item = document.createElement("article");
  item.className = "message user";

  const role = document.createElement("div");
  role.className = "message-role";
  role.textContent = "你";

  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = message;

  item.append(role, body);
  els.messages.append(item);
  scrollMessagesToBottom();
}

function newSession() {
  state.sessionId = `web-${Date.now().toString(36)}`;
  state.rawSession = false;
  state.currentMode = "hybrid";
  els.sessionTitle.textContent = state.sessionId;
  renderMessages([]);
  updateMetrics({ current_mode: "hybrid" });
  setBusy(false);
  setSidebarPanel("sessions");
  loadSessions();
  els.input.focus();
}

els.composer.addEventListener("submit", (event) => {
  event.preventDefault();
  submitComposer();
});

els.input.addEventListener("keydown", (event) => {
  if (event.isComposing) return;
  if (event.key !== "Enter") return;
  if (event.shiftKey) return;

  event.preventDefault();
  submitComposer();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && state.mobileSidebarOpen) {
    event.preventDefault();
    setMobileSidebarOpen(false);
    return;
  }

  const modifier = event.ctrlKey || event.metaKey;
  if (!modifier || event.altKey || event.shiftKey) return;

  if (event.key.toLowerCase() === "k") {
    event.preventDefault();
    newSession();
    return;
  }

  if (event.key.toLowerCase() === "b") {
    event.preventDefault();
    if (isMobileViewport()) {
      setMobileSidebarOpen(!state.mobileSidebarOpen);
    } else {
      setSidebarCollapsed(!state.sidebarCollapsed);
    }
    return;
  }

  const modeCommands = {
    "1": "/hybrid",
    "2": "/chat",
    "3": "/coding",
  };
  const command = modeCommands[event.key];
  if (!command || state.busy || state.rawSession) return;
  event.preventDefault();
  sendMessage(command);
});

els.modeActions.addEventListener("click", (event) => {
  const command = event.target?.dataset?.command;
  if (!command || state.busy || state.rawSession) return;
  sendMessage(command);
});

els.sidebarTabs.forEach((tab) => {
  tab.addEventListener("click", () => setSidebarPanel(tab.dataset.sidebarTab));
});

els.sessionSearch.addEventListener("input", () => {
  state.sessionFilter = els.sessionSearch.value;
  renderSessions();
});

els.sidebarToggle.addEventListener("click", () => {
  if (isMobileViewport()) {
    setMobileSidebarOpen(false);
  } else {
    setSidebarCollapsed(!state.sidebarCollapsed);
  }
});

els.mobileSidebarOpen.addEventListener("click", () => {
  setMobileSidebarOpen(true);
});

els.sidebarOverlay.addEventListener("click", () => {
  setMobileSidebarOpen(false);
});

window.addEventListener("resize", syncResponsiveSidebar);

els.newSessionBtn.addEventListener("click", newSession);
els.refreshMemoryBtn.addEventListener("click", loadMemory);

async function init() {
  setSidebarPanel(state.sidebarPanel);
  setSidebarCollapsed(state.sidebarCollapsed);
  syncResponsiveSidebar();
  await loadHealth();
  await Promise.all([loadSessions(), loadMemory()]);
  await loadSession("default", false);
}

init().catch((error) => {
  setStatus("异常", "error");
  els.workspaceLabel.textContent = error.message;
  els.workspacePath.textContent = error.message;
});
