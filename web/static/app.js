const state = {
  sessionId: "default",
  rawSession: false,
  busy: false,
  memoryFiles: [],
  activeMemory: "MEMORY.md",
};

const els = {
  workspaceLabel: document.querySelector("#workspaceLabel"),
  statusBadge: document.querySelector("#statusBadge"),
  sessionsList: document.querySelector("#sessionsList"),
  newSessionBtn: document.querySelector("#newSessionBtn"),
  refreshMemoryBtn: document.querySelector("#refreshMemoryBtn"),
  memoryTabs: document.querySelector("#memoryTabs"),
  memoryContent: document.querySelector("#memoryContent"),
  sessionTitle: document.querySelector("#sessionTitle"),
  messages: document.querySelector("#messages"),
  composer: document.querySelector("#composer"),
  input: document.querySelector("#messageInput"),
  sendBtn: document.querySelector("#sendBtn"),
  composerState: document.querySelector("#composerState"),
  modeActions: document.querySelector(".mode-actions"),
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
  els.messages.scrollTop = els.messages.scrollHeight;
}

function renderSessions(sessions) {
  els.sessionsList.innerHTML = "";
  const rows = sessions || [];
  if (rows.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "暂无会话";
    els.sessionsList.append(empty);
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
      state.sessionId = isWeb ? session.chat_id : session.id;
      loadSession(isWeb ? session.chat_id : session.id, !isWeb);
    });
    els.sessionsList.append(button);
  }
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
    setStatus("就绪", "ready");
  } catch (error) {
    setStatus("异常", "error");
    els.workspaceLabel.textContent = error.message;
  }
}

async function loadSessions() {
  const data = await fetchJson("/api/sessions");
  renderSessions(data.sessions);
}

async function loadSession(sessionId = state.sessionId, raw = state.rawSession) {
  const data = await fetchJson(
    `/api/session?session_id=${encodeURIComponent(sessionId)}&raw=${raw ? "1" : "0"}`,
  );
  state.sessionId = data.session.chat_id || sessionId;
  state.rawSession = raw || data.session.channel !== "web";
  els.sessionTitle.textContent = state.sessionId;
  setBusy(false);
  renderMessages(data.session.messages || []);
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
  const currentMessages = [...els.messages.querySelectorAll(".message")];
  if (currentMessages.length === 0 || els.messages.querySelector(".empty-state")) {
    renderMessages([]);
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
    renderMessages(savedMessages.length > 0
      ? savedMessages
      : [
        { role: "user", content: message },
        { role: "assistant", content: data.reply },
      ]);
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
  els.messages.scrollTop = els.messages.scrollHeight;
}

function newSession() {
  state.sessionId = `web-${Date.now().toString(36)}`;
  state.rawSession = false;
  els.sessionTitle.textContent = state.sessionId;
  renderMessages([]);
  setBusy(false);
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
  const modifier = event.ctrlKey || event.metaKey;
  if (!modifier || event.altKey || event.shiftKey) return;

  if (event.key.toLowerCase() === "k") {
    event.preventDefault();
    newSession();
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

els.newSessionBtn.addEventListener("click", newSession);
els.refreshMemoryBtn.addEventListener("click", loadMemory);

async function init() {
  await loadHealth();
  await Promise.all([loadSessions(), loadMemory()]);
  await loadSession("default", false);
}

init().catch((error) => {
  setStatus("异常", "error");
  els.workspaceLabel.textContent = error.message;
});
