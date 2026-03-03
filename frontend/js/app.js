/** Claude Agent Manager — orchestration dashboard */
import { api } from './api.js';
import { WSClient } from './ws.js';
import {
  initSettingsTabs,
  initGlobalSettingsEditor,
  loadGlobalSettings,
} from './settings.js';
import {
  renderProjectList,
  renderProjectTileGrid,
  renderAgentsGrid,
  updateAgentCardChunk,
  updateAgentCardMilestone,
} from './projects.js';
import {
  escapeHtml,
  formatTime,
  formatUptime,
  formatMessageText,
  toast,
} from './utils.js';

// ─── State ─────────────────────────────────────────────────────────────────────
const state = {
  projects: [],            // ManagedProject[]
  agents: [],              // Agent[] (running only)
  selectedProject: null,   // project name string
  selectedAgentId: null,   // session_id for right panel
  messages: {},            // session_id → AgentMessage[]
  streamBuffers: {},       // session_id → accumulated text
  stats: null,
};

// ─── DOM refs ──────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const dom = {
  connectionStatus:   $('connection-status'),
  connLabel:          document.querySelector('.conn-label'),
  statProjects:       $('stat-projects'),
  statWorking:        $('stat-working'),
  statAgents:         $('stat-agents'),
  statUptime:         $('stat-uptime'),
  workbenchEmpty:     $('workbench-empty'),
  workbenchProject:   $('workbench-project'),
  wbProjectName:      $('wb-project-name'),
  wbProjectDesc:      $('wb-project-desc'),
  wbParallelism:      $('wb-parallelism'),
  wbModel:            $('wb-model'),
  dispatchProjectLabel: $('dispatch-project-label'),
  taskInput:          $('task-input'),
  dispatchBtn:        $('dispatch-btn'),
  agentDetail:        $('agent-detail'),
  agentDetailStatus:  $('agent-detail-status'),
  agentDetailProject: $('agent-detail-project'),
  agentDetailTask:    $('agent-detail-task'),
  agentMessages:      $('agent-messages'),
  injectInput:        $('inject-input'),
  injectBtn:          $('inject-btn'),
};

// ─── Utilities ─────────────────────────────────────────────────────────────────

function renderMessage(msg) {
  const role = msg.role;
  const ts = formatTime(msg.timestamp);
  let bubbleText = '';
  let hasBubble = false;
  const extraBlocks = [];

  for (const c of msg.content) {
    if (c.type === 'text' && c.text) {
      bubbleText += c.text;
      hasBubble = true;
    } else if (c.type === 'tool_use' || c.type === 'tool_result') {
      extraBlocks.push(renderToolBlock(c));
    }
  }

  const avatar = role === 'user' ? 'U' : '✦';
  let html = `<div class="message ${role}" data-uuid="${escapeHtml(msg.uuid)}">
    <div class="message-avatar">${avatar}</div>
    <div class="message-body">`;

  if (hasBubble) {
    html += `<div class="message-bubble">${formatMessageText(bubbleText)}</div>`;
  }
  if (extraBlocks.length) {
    html += `<div style="max-width:80%;${role === 'user' ? 'align-self:flex-end' : ''}">${extraBlocks.join('')}</div>`;
  }
  html += `<div class="message-time">${ts}</div></div></div>`;
  return html;
}

function renderToolBlock(content) {
  const tc = content.tool_call;
  if (!tc) return '';
  const inputLines = tc.input && Object.keys(tc.input).length
    ? Object.entries(tc.input).slice(0, 5).map(([k, v]) => {
        const val = typeof v === 'string' ? (v.length > 120 ? v.slice(0, 120) + '…' : v) : JSON.stringify(v);
        return `<div><span class="tool-key">${escapeHtml(k)}:</span> <span class="tool-val">${escapeHtml(val)}</span></div>`;
      }).join('')
    : '';
  const resultHtml = content.type === 'tool_result' && tc.output
    ? `<div class="tool-result-block">${escapeHtml(tc.output.slice(0, 600))}${tc.output.length > 600 ? '…' : ''}</div>`
    : '';
  const toolId = `tool-${Math.random().toString(36).slice(2, 9)}`;
  return `
    <div class="tool-block" id="${toolId}">
      <div class="tool-header" onclick="toggleTool('${toolId}')">
        <div class="tool-icon"><svg width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M5 1v8M1 5h8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg></div>
        <span class="tool-name">${escapeHtml(tc.name || 'tool')}</span>
        <svg class="tool-chevron" width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M3 4l2 2 2-2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </div>
      <div class="tool-body">${inputLines}${resultHtml}</div>
    </div>`;
}

window.toggleTool = id => {
  document.getElementById(id)?.classList.toggle('expanded');
};

// ─── Project selection ─────────────────────────────────────────────────────────

async function selectProject(name) {
  if (state.selectedProject === name) return;
  state.selectedProject = name;

  // Close agent detail panel
  closeAgentDetail();

  // Update sidebar selection
  renderProjectList(state.projects, name, selectProject);

  // Show workbench
  dom.workbenchEmpty?.classList.add('hidden');
  dom.workbenchProject?.classList.remove('hidden');

  // Populate workbench header
  const project = state.projects.find(p => p.name === name);
  if (dom.wbProjectName) dom.wbProjectName.textContent = name;
  if (dom.wbProjectDesc) dom.wbProjectDesc.textContent = project?.description || '';
  if (dom.dispatchProjectLabel) dom.dispatchProjectLabel.textContent = name;

  // Set parallelism/model selects
  if (dom.wbParallelism && project?.config) {
    dom.wbParallelism.value = String(project.config.parallelism || 1);
  }
  if (dom.wbModel && project?.config) {
    dom.wbModel.value = project.config.model || '';
  }

  // Render agents for this project
  renderAgentsGrid(state.agents, name, selectAgent, killAgent);
  updateDispatchBtn();
}

function showTileGrid() {
  state.selectedProject = null;
  closeAgentDetail();
  dom.workbenchProject?.classList.add('hidden');
  dom.workbenchEmpty?.classList.remove('hidden');
  renderProjectList(state.projects, null, selectProject);
  renderProjectTileGrid(state.projects, selectProject);
}

// ─── Agent detail panel ────────────────────────────────────────────────────────

async function selectAgent(sessionId) {
  state.selectedAgentId = sessionId;

  const agent = state.agents.find(a => a.session_id === sessionId);
  if (!agent) return;

  // Update header
  if (dom.agentDetailStatus) {
    dom.agentDetailStatus.className = `agent-detail-status ${agent.status}`;
  }
  if (dom.agentDetailProject) dom.agentDetailProject.textContent = agent.project_name;
  if (dom.agentDetailTask) dom.agentDetailTask.textContent = (agent.task || '').slice(0, 80);

  // Show panel
  dom.agentDetail?.classList.remove('hidden');
  updateInjectBtn();

  // Load conversation history
  const messagesEl = dom.agentMessages;
  if (messagesEl) {
    messagesEl.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:center;height:100%;gap:12px">
        <div class="loading-spinner"></div>
        <span style="color:var(--text-muted);font-size:12px">Loading…</span>
      </div>`;
  }

  try {
    const messages = await api.getMessages(sessionId);
    state.messages[sessionId] = messages;
    renderAgentMessages(sessionId);

    // If we have stream buffer, show it too
    appendStreamBuffer(sessionId);
  } catch (e) {
    if (messagesEl) {
      messagesEl.innerHTML = `<div class="empty-state"><p style="color:var(--text-muted)">No history yet</p></div>`;
    }
  }
}

function closeAgentDetail() {
  state.selectedAgentId = null;
  dom.agentDetail?.classList.add('hidden');
}

function renderAgentMessages(sessionId) {
  const msgs = state.messages[sessionId] || [];
  const el = dom.agentMessages;
  if (!el) return;

  const empty = el.querySelector('.empty-state');
  if (empty) empty.remove();

  const chunks = [];
  for (const msg of msgs) {
    chunks.push(renderMessage(msg));
  }
  el.innerHTML = chunks.join('') || `<div class="empty-state"><p style="color:var(--text-muted);font-size:12px">No messages yet</p></div>`;
  el.scrollTop = el.scrollHeight;
}

function appendMilestoneToDetail(milestone) {
  const feed = dom.agentMessages;
  if (!feed) return;
  let milestoneBar = feed.querySelector('.milestone-feed');
  if (!milestoneBar) {
    milestoneBar = document.createElement('div');
    milestoneBar.className = 'milestone-feed';
    feed.insertBefore(milestoneBar, feed.firstChild);
  }
  const item = document.createElement('div');
  item.className = 'milestone-item';
  item.textContent = milestone;
  milestoneBar.appendChild(item);
  // Keep last 10
  const items = milestoneBar.querySelectorAll('.milestone-item');
  if (items.length > 10) items[0].remove();
}

function appendStreamBuffer(sessionId) {
  const buf = state.streamBuffers[sessionId];
  if (!buf || !dom.agentMessages) return;

  // Check if there's a streaming indicator already
  let streamEl = dom.agentMessages.querySelector('.stream-output');
  if (!streamEl) {
    streamEl = document.createElement('div');
    streamEl.className = 'message assistant stream-output';
    streamEl.innerHTML = `
      <div class="message-avatar">✦</div>
      <div class="message-body">
        <div class="message-bubble stream-bubble"></div>
      </div>`;
    dom.agentMessages.appendChild(streamEl);
  }
  const bubble = streamEl.querySelector('.stream-bubble');
  if (bubble) bubble.innerHTML = formatMessageText(buf);
  dom.agentMessages.scrollTop = dom.agentMessages.scrollHeight;
}

// ─── Dispatch ──────────────────────────────────────────────────────────────────

function updateDispatchBtn() {
  if (!dom.dispatchBtn || !dom.taskInput) return;
  const hasTask = dom.taskInput.value.trim().length > 0;
  const hasProject = state.selectedProject != null;
  dom.dispatchBtn.disabled = !hasTask || !hasProject;
}

async function dispatchTask() {
  const task = dom.taskInput?.value.trim();
  if (!task || !state.selectedProject) return;

  const model = dom.wbModel?.value || null;

  // Save parallelism before dispatch
  const parallelism = parseInt(dom.wbParallelism?.value || '1', 10);
  try {
    await api.updateProjectConfig(state.selectedProject, {
      parallelism,
      model: model || null,
    });
    // Update local state
    const proj = state.projects.find(p => p.name === state.selectedProject);
    if (proj) {
      proj.config = { parallelism, model: model || null };
    }
  } catch (_) {}

  dom.dispatchBtn.disabled = true;
  dom.dispatchBtn.classList.add('sending');

  try {
    await api.dispatchTask(state.selectedProject, task, model || undefined);
    if (dom.taskInput) {
      dom.taskInput.value = '';
      dom.taskInput.style.height = 'auto';
    }
    toast(`Dispatched to ${state.selectedProject}`, 'success', 2000);
  } catch (e) {
    toast(`Dispatch failed: ${e.message}`, 'error');
  } finally {
    dom.dispatchBtn.classList.remove('sending');
    updateDispatchBtn();
  }
}

// ─── Injection ─────────────────────────────────────────────────────────────────

function updateInjectBtn() {
  if (!dom.injectBtn || !dom.injectInput) return;
  const hasMsg = dom.injectInput.value.trim().length > 0;
  const hasAgent = state.selectedAgentId != null;
  dom.injectBtn.disabled = !hasMsg || !hasAgent;
}

async function injectMessage() {
  const msg = dom.injectInput?.value.trim();
  if (!msg || !state.selectedAgentId) return;

  dom.injectBtn.disabled = true;
  try {
    const result = await api.injectMessage(state.selectedAgentId, msg);
    if (dom.injectInput) {
      dom.injectInput.value = '';
      dom.injectInput.style.height = 'auto';
    }
    const status = result.status === 'queued' ? 'queued (agent is working)' : 'sent';
    toast(`Message ${status}`, 'success', 2000);
    updateInjectBtn();
  } catch (e) {
    toast(`Inject failed: ${e.message}`, 'error');
    dom.injectBtn.disabled = false;
  }
}

async function killAgent(sessionId) {
  try {
    await api.killAgent(sessionId);
    if (state.selectedAgentId === sessionId) {
      closeAgentDetail();
    }
    // Remove from local agents list
    state.agents = state.agents.filter(a => a.session_id !== sessionId);
    if (state.selectedProject) {
      renderAgentsGrid(state.agents, state.selectedProject, selectAgent, killAgent);
    }
    toast('Agent killed', 'warn', 2000);
  } catch (e) {
    toast(`Kill failed: ${e.message}`, 'error');
  }
}

// ─── Stats ─────────────────────────────────────────────────────────────────────

function renderStats(stats) {
  if (dom.statProjects) dom.statProjects.textContent = stats.total_projects ?? '—';
  if (dom.statWorking) dom.statWorking.textContent = stats.working_agents ?? '—';
  if (dom.statAgents) dom.statAgents.textContent = stats.total_agents ?? '—';
  if (dom.statUptime) dom.statUptime.textContent = formatUptime(stats.uptime_seconds ?? 0);
}

// ─── WebSocket event handlers ──────────────────────────────────────────────────

function onWSMessage(msg) {
  switch (msg.type) {

    case 'project_list': {
      state.projects = msg.data;
      renderProjectList(state.projects, state.selectedProject, selectProject);
      renderProjectTileGrid(state.projects, selectProject);

      // Refresh workbench header if a project is selected
      if (state.selectedProject) {
        const proj = state.projects.find(p => p.name === state.selectedProject);
        if (proj && dom.wbProjectDesc) dom.wbProjectDesc.textContent = proj.description || '';
      }
      break;
    }

    case 'stats_update': {
      state.stats = msg.data;
      renderStats(msg.data);
      break;
    }

    case 'agent_spawned': {
      const { session_id, project_name, task } = msg.data;
      // Add to local agents (minimal info until backend refreshes)
      if (!state.agents.find(a => a.session_id === session_id)) {
        state.agents.push({
          session_id,
          project_name,
          project_path: '',
          task,
          status: 'working',
          last_chunk: null,
          model: null,
          started_at: msg.timestamp,
          has_pending_injection: false,
        });
      }
      if (state.selectedProject === project_name) {
        renderAgentsGrid(state.agents, project_name, selectAgent, killAgent);
      }
      break;
    }

    case 'agent_done': {
      const { session_id } = msg.data;
      const agent = state.agents.find(a => a.session_id === session_id);
      if (agent) {
        agent.status = 'idle';
        agent.last_chunk = null;
      }
      if (state.selectedProject) {
        renderAgentsGrid(state.agents, state.selectedProject, selectAgent, killAgent);
      }
      // If this agent is open in detail panel, reload history
      if (state.selectedAgentId === session_id) {
        api.getMessages(session_id).then(messages => {
          state.messages[session_id] = messages;
          renderAgentMessages(session_id);
        }).catch(() => {});
        // Clear stream buffer
        delete state.streamBuffers[session_id];
        const streamEl = dom.agentMessages?.querySelector('.stream-output');
        if (streamEl) streamEl.remove();
      }
      break;
    }

    case 'agent_stream': {
      const { session_id, project_name, chunk, done } = msg.data;

      if (done) break;

      // Update agent status to working
      const agent = state.agents.find(a => a.session_id === session_id);
      if (agent) {
        agent.status = 'working';
        agent.last_chunk = chunk;
      }

      // Accumulate in stream buffer
      state.streamBuffers[session_id] = (state.streamBuffers[session_id] || '') + chunk;

      // Update agent card preview
      updateAgentCardChunk(session_id, chunk);

      // If this agent is selected in the detail panel, append to stream view
      if (state.selectedAgentId === session_id) {
        appendStreamBuffer(session_id);
      }
      break;
    }

    case 'agent_update': {
      const updated = msg.data;
      const idx = state.agents.findIndex(a => a.session_id === updated.session_id);
      if (idx >= 0) {
        state.agents[idx] = { ...state.agents[idx], ...updated };
      }
      if (state.selectedProject === updated.project_name) {
        renderAgentsGrid(state.agents, updated.project_name, selectAgent, killAgent);
      }
      if (state.selectedAgentId === updated.session_id) {
        if (dom.agentDetailStatus) {
          dom.agentDetailStatus.className = `agent-detail-status ${updated.status}`;
        }
      }
      break;
    }

    case 'agent_id_assigned': {
      // Backend promoted a pending-PID agent to a real session_id
      const { old_session_id, session_id, project_name } = msg.data;
      const agent = state.agents.find(a => a.session_id === old_session_id);
      if (agent) {
        agent.session_id = session_id;
        // Update stream buffer key
        if (state.streamBuffers[old_session_id]) {
          state.streamBuffers[session_id] = state.streamBuffers[old_session_id];
          delete state.streamBuffers[old_session_id];
        }
        // Update selected agent id if it was the pending one
        if (state.selectedAgentId === old_session_id) {
          state.selectedAgentId = session_id;
        }
      }
      if (state.selectedProject === project_name) {
        renderAgentsGrid(state.agents, project_name, selectAgent, killAgent);
      }
      break;
    }

    case 'agent_milestone': {
      const { session_id, project_name, milestone, milestones } = msg.data;
      const agent = state.agents.find(a => a.session_id === session_id);
      if (agent) {
        agent.current_milestone = milestone;
        agent.milestones = milestones;
      }
      updateAgentCardMilestone(session_id, milestone);
      // If this agent is open in the detail panel, append to milestone feed
      if (state.selectedAgentId === session_id) {
        appendMilestoneToDetail(milestone);
      }
      break;
    }
  }
}

// ─── New project modal ────────────────────────────────────────────────────────

function openNewProjectModal() {
  $('new-project-modal')?.classList.remove('hidden');
  $('new-project-name')?.focus();
}

function closeNewProjectModal() {
  $('new-project-modal')?.classList.add('hidden');
  if ($('new-project-name')) $('new-project-name').value = '';
  if ($('new-project-goal')) $('new-project-goal').value = '';
  if ($('new-project-description')) $('new-project-description').value = '';
}

async function createProject() {
  const name = $('new-project-name')?.value.trim();
  const goal = $('new-project-goal')?.value.trim();
  const description = $('new-project-description')?.value.trim();

  if (!name) {
    toast('Project name is required', 'error');
    return;
  }
  if (!goal) {
    toast('Goal is required', 'error');
    return;
  }

  const btn = $('modal-create-btn');
  if (btn) btn.disabled = true;

  try {
    const project = await api.createProject(name, goal, description || goal);
    state.projects.push(project);
    renderProjectList(state.projects, state.selectedProject, selectProject);
    renderProjectTileGrid(state.projects, selectProject);
    closeNewProjectModal();
    toast(`Project "${name}" created`, 'success');
    // Auto-select the new project
    await selectProject(name);
  } catch (e) {
    toast(`Create failed: ${e.message}`, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ─── Initialization ────────────────────────────────────────────────────────────

async function init() {
  // Load initial data
  try {
    const [projects, stats] = await Promise.all([
      api.getProjects(),
      api.getStats(),
    ]);
    state.projects = projects;
    state.stats = stats;
    renderProjectList(projects, null, selectProject);
    renderProjectTileGrid(projects, selectProject);
    renderStats(stats);
  } catch (e) {
    toast(`Failed to load: ${e.message}`, 'error');
  }

  // Also load running agents
  try {
    state.agents = await api.getAgents();
  } catch (_) {}

  // WebSocket
  new WSClient({
    onOpen() {
      if (dom.connectionStatus) dom.connectionStatus.className = 'connection-status connected';
      if (dom.connLabel) dom.connLabel.textContent = 'connected';
    },
    onClose() {
      if (dom.connectionStatus) dom.connectionStatus.className = 'connection-status';
      if (dom.connLabel) dom.connLabel.textContent = 'disconnected';
    },
    onError() {
      if (dom.connectionStatus) dom.connectionStatus.className = 'connection-status error';
      if (dom.connLabel) dom.connLabel.textContent = 'error';
    },
    onReconnecting() {
      if (dom.connLabel) dom.connLabel.textContent = 'reconnecting…';
    },
    onMessage: onWSMessage,
  });

  // ── Nav tabs ─────────────────────────────────────────────────────────────
  const mainLayout   = $('main-layout');
  const settingsView = $('settings-view');

  document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const view = tab.dataset.view;
      document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      if (view === 'settings') {
        mainLayout?.classList.add('hidden');
        settingsView?.classList.remove('hidden');
        loadGlobalSettings();
      } else {
        settingsView?.classList.add('hidden');
        mainLayout?.classList.remove('hidden');
      }
    });
  });

  // ── Dispatch area ─────────────────────────────────────────────────────────
  dom.taskInput?.addEventListener('input', () => {
    updateDispatchBtn();
    dom.taskInput.style.height = 'auto';
    dom.taskInput.style.height = Math.min(dom.taskInput.scrollHeight, 160) + 'px';
  });
  dom.taskInput?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      dispatchTask();
    }
  });
  dom.dispatchBtn?.addEventListener('click', dispatchTask);

  // ── Parallelism / model changes ───────────────────────────────────────────
  dom.wbParallelism?.addEventListener('change', async () => {
    if (!state.selectedProject) return;
    const parallelism = parseInt(dom.wbParallelism.value, 10);
    const model = dom.wbModel?.value || null;
    try {
      await api.updateProjectConfig(state.selectedProject, { parallelism, model });
      const proj = state.projects.find(p => p.name === state.selectedProject);
      if (proj) proj.config = { parallelism, model };
    } catch (_) {}
  });
  dom.wbModel?.addEventListener('change', async () => {
    if (!state.selectedProject) return;
    const parallelism = parseInt(dom.wbParallelism?.value || '1', 10);
    const model = dom.wbModel.value || null;
    try {
      await api.updateProjectConfig(state.selectedProject, { parallelism, model });
      const proj = state.projects.find(p => p.name === state.selectedProject);
      if (proj) proj.config = { parallelism, model };
    } catch (_) {}
  });

  // ── Inject area ───────────────────────────────────────────────────────────
  dom.injectInput?.addEventListener('input', () => {
    updateInjectBtn();
    dom.injectInput.style.height = 'auto';
    dom.injectInput.style.height = Math.min(dom.injectInput.scrollHeight, 120) + 'px';
  });
  dom.injectInput?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      injectMessage();
    }
  });
  dom.injectBtn?.addEventListener('click', injectMessage);

  // ── Agent detail panel ────────────────────────────────────────────────────
  $('close-detail-btn')?.addEventListener('click', closeAgentDetail);
  $('kill-agent-btn')?.addEventListener('click', () => {
    if (state.selectedAgentId) killAgent(state.selectedAgentId);
  });

  // ── New project modal ─────────────────────────────────────────────────────
  $('new-project-btn')?.addEventListener('click', openNewProjectModal);
  $('new-project-tile-btn')?.addEventListener('click', openNewProjectModal);
  $('new-project-empty-btn')?.addEventListener('click', openNewProjectModal);
  $('modal-close-btn')?.addEventListener('click', closeNewProjectModal);
  $('modal-cancel-btn')?.addEventListener('click', closeNewProjectModal);
  $('modal-backdrop')?.addEventListener('click', closeNewProjectModal);
  $('modal-create-btn')?.addEventListener('click', createProject);

  // Enter to submit in name field
  $('new-project-name')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') createProject();
  });

  // ── Logo / title click: go back to tile grid ──────────────────────────────
  document.querySelector('.app-title')?.addEventListener('click', showTileGrid);
  document.querySelector('.app-logo')?.addEventListener('click', showTileGrid);

  // ── Settings init ─────────────────────────────────────────────────────────
  initSettingsTabs();
  initGlobalSettingsEditor();

  // ── Uptime counter ────────────────────────────────────────────────────────
  setInterval(() => {
    if (state.stats) {
      state.stats.uptime_seconds += 1;
      if (dom.statUptime) dom.statUptime.textContent = formatUptime(state.stats.uptime_seconds);
    }
  }, 1000);
}

init();
