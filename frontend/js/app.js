/** Claude Agent Manager — main application */
import { api } from './api.js';
import { WSClient } from './ws.js';
import {
  initSettingsTabs,
  initGlobalSettingsEditor,
  loadGlobalSettings,
} from './settings.js';

// ─── State ─────────────────────────────────────────────────────────────────────
const state = {
  agents: [],
  selectedId: null,
  messages: {},        // sessionId -> AgentMessage[]
  stats: null,
  loading: false,
  sending: false,
  autoScroll: true,
};

// ─── DOM references ─────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const dom = {
  agentList:       $('agent-list'),
  messageList:     $('message-list'),
  emptyConv:       $('empty-conversation'),
  convHeader:      $('conversation-header'),
  convProject:     $('conv-project'),
  convPid:         $('conv-pid'),
  convBranch:      $('conv-branch'),
  convModel:       $('conv-model'),
  convStatusDot:   $('conv-status-dot'),
  inputArea:       $('message-input-area'),
  messageInput:    $('message-input'),
  sendBtn:         $('send-btn'),
  inputAgentLabel: $('input-agent-label'),
  activityFeed:    $('activity-feed'),
  connectionStatus:$('connection-status'),
  connLabel:       document.querySelector('.conn-label'),
  statTotal:       $('stat-total'),
  statWorking:     $('stat-working'),
  statActive:      $('stat-active'),
  statMessages:    $('stat-messages'),
  statUptime:      $('stat-uptime'),
  refreshBtn:      $('refresh-btn'),
  scrollBottomBtn: $('scroll-bottom-btn'),
};

// ─── Utilities ─────────────────────────────────────────────────────────────────

function relativeTime(ts) {
  if (!ts) return '';
  const date = new Date(ts);
  const diff = Date.now() - date.getTime();
  if (diff < 60_000)  return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return date.toLocaleDateString();
}

function formatTime(ts) {
  if (!ts) return '';
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatUptime(seconds) {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

function escapeHtml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatMessageText(text) {
  if (!text) return '';
  const escaped = escapeHtml(text);

  // Code blocks
  let formatted = escaped.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) =>
    `<pre><code>${code.trim()}</code></pre>`
  );

  // Inline code
  formatted = formatted.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Preserve newlines outside pre blocks
  // Split on pre blocks to avoid double-escaping
  const parts = formatted.split(/(<pre>[\s\S]*?<\/pre>)/g);
  return parts.map(part => {
    if (part.startsWith('<pre>')) return part;
    return part.replace(/\n/g, '<br>');
  }).join('');
}

function toast(message, type = 'info', duration = 3500) {
  const container = $('toast-container');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  const icons = { success: '✓', error: '✗', info: 'ℹ', warn: '⚠' };
  el.innerHTML = `<span>${icons[type] || '•'}</span> ${escapeHtml(message)}`;
  container.appendChild(el);
  setTimeout(() => {
    el.classList.add('out');
    setTimeout(() => el.remove(), 250);
  }, duration);
}

// ─── Rendering ─────────────────────────────────────────────────────────────────

function renderAgentList() {
  if (!state.agents.length) {
    dom.agentList.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">
          <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
            <circle cx="16" cy="16" r="12" stroke="currentColor" stroke-width="1.5" stroke-dasharray="4 2"/>
          </svg>
        </div>
        <p>No agents found</p>
        <p style="font-size:11px;color:var(--text-muted);margin-top:4px">Start a Claude session in another terminal</p>
      </div>`;
    return;
  }

  dom.agentList.innerHTML = state.agents.map(agent => {
    const selected = agent.session_id === state.selectedId ? 'selected' : '';
    const statusClass = `status-${agent.status}`;
    const pid = agent.pid ? `PID ${agent.pid}` : 'no process';

    return `
    <div class="agent-card ${selected} ${statusClass}" data-session="${escapeHtml(agent.session_id)}">
      <div class="agent-card-top">
        <div class="status-indicator ${escapeHtml(agent.status)}"></div>
        <span class="agent-name" title="${escapeHtml(agent.project_path)}">${escapeHtml(agent.project_name)}</span>
        ${agent.pid ? `<span class="agent-pid">${agent.pid}</span>` : ''}
      </div>
      ${agent.current_task ? `<div class="agent-task">${escapeHtml(agent.current_task)}</div>` : ''}
      <div class="agent-meta">
        <span class="agent-meta-tag">${escapeHtml(agent.status)}</span>
        ${agent.model ? `<span class="agent-meta-tag">${escapeHtml(agent.model.split('-').slice(-2).join('-'))}</span>` : ''}
        ${agent.git_branch ? `<span class="agent-meta-tag">⎇ ${escapeHtml(agent.git_branch)}</span>` : ''}
        <span class="agent-meta-tag">${agent.message_count} msgs</span>
        <span class="agent-time">${relativeTime(agent.last_activity)}</span>
      </div>
    </div>`;
  }).join('');

  // Re-attach click handlers
  dom.agentList.querySelectorAll('.agent-card').forEach(card => {
    card.addEventListener('click', () => selectAgent(card.dataset.session));
  });
}

function renderConversationHeader(agent) {
  if (!agent) {
    dom.convHeader.classList.add('hidden');
    dom.inputArea.classList.add('hidden');
    return;
  }
  dom.convHeader.classList.remove('hidden');
  dom.inputArea.classList.remove('hidden');

  dom.convProject.textContent = agent.project_name;
  dom.convStatusDot.className = `conv-status-dot ${agent.status}`;

  const parts = [];
  if (agent.pid) parts.push(`PID ${agent.pid}`);
  if (agent.cpu_percent != null) parts.push(`CPU ${agent.cpu_percent.toFixed(1)}%`);
  dom.convPid.textContent = parts.join(' · ');

  dom.convBranch.textContent = agent.git_branch ? `⎇ ${agent.git_branch}` : '';
  dom.convModel.textContent = agent.model || '';

  dom.inputAgentLabel.textContent = `→ ${agent.project_name}`;
}

function renderToolBlock(content) {
  const tc = content.tool_call;
  if (!tc) return '';

  const inputStr = tc.input && Object.keys(tc.input).length
    ? JSON.stringify(tc.input, null, 2)
    : '';

  const inputLines = inputStr ? Object.entries(tc.input).slice(0, 5).map(([k, v]) => {
    const val = typeof v === 'string' ? v.length > 120 ? v.slice(0, 120) + '…' : v : JSON.stringify(v);
    return `<div><span class="tool-key">${escapeHtml(k)}:</span> <span class="tool-val">${escapeHtml(val)}</span></div>`;
  }).join('') : '';

  const resultHtml = content.type === 'tool_result' && tc.output
    ? `<div class="tool-result-block">${escapeHtml(tc.output.slice(0, 600))}${tc.output.length > 600 ? '…' : ''}</div>`
    : '';

  const toolId = `tool-${Math.random().toString(36).slice(2, 9)}`;

  return `
    <div class="tool-block" id="${toolId}">
      <div class="tool-header" onclick="toggleTool('${toolId}')">
        <div class="tool-icon">
          <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
            <path d="M5 1v8M1 5h8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </svg>
        </div>
        <span class="tool-name">${escapeHtml(tc.name || 'tool')}</span>
        ${inputStr ? `<span style="color:var(--text-muted);font-size:9px">${Object.keys(tc.input).length} args</span>` : ''}
        <svg class="tool-chevron" width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M3 4l2 2 2-2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <div class="tool-body">
        ${inputLines}
        ${resultHtml}
      </div>
    </div>`;
}

window.toggleTool = function(id) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle('expanded');
};

function renderMessage(msg) {
  const role = msg.role;       // 'user' | 'assistant'
  const ts = formatTime(msg.timestamp);

  // Build content HTML
  let contentHtml = '';
  let hasBubbleContent = false;
  let bubbleText = '';
  const extraBlocks = [];

  for (const c of msg.content) {
    if (c.type === 'text' && c.text) {
      bubbleText += c.text;
      hasBubbleContent = true;
    } else if (c.type === 'tool_use' || c.type === 'tool_result') {
      extraBlocks.push(renderToolBlock(c));
    } else if (c.type === 'thinking' && c.thinking) {
      // Thinking is hidden by default
    }
  }

  const avatar = role === 'user' ? 'U' : '✦';

  let html = `<div class="message ${role}" data-uuid="${escapeHtml(msg.uuid)}">
    <div class="message-avatar">${avatar}</div>
    <div class="message-body">`;

  if (hasBubbleContent) {
    html += `<div class="message-bubble">${formatMessageText(bubbleText)}</div>`;
  }

  if (extraBlocks.length) {
    html += `<div style="max-width:80%;${role === 'user' ? 'align-self:flex-end' : ''}">`;
    html += extraBlocks.join('');
    html += '</div>';
  }

  html += `<div class="message-time">${ts}</div>`;
  html += `</div></div>`;

  return html;
}

function renderMessages(sessionId) {
  const msgs = state.messages[sessionId] || [];
  const list = dom.messageList;

  if (!msgs.length) {
    list.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon" style="opacity:0.3">
          <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
            <path d="M8 8h16M8 14h16M8 20h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </svg>
        </div>
        <p style="color:var(--text-muted);font-size:12px">No messages in this session</p>
      </div>`;
    return;
  }

  // Group messages by date for separators
  let lastDate = '';
  const chunks = [];

  for (const msg of msgs) {
    const d = msg.timestamp ? new Date(msg.timestamp).toLocaleDateString() : '';
    if (d && d !== lastDate) {
      chunks.push(`<div class="date-separator">${escapeHtml(d)}</div>`);
      lastDate = d;
    }
    chunks.push(renderMessage(msg));
  }

  list.innerHTML = chunks.join('');

  if (state.autoScroll) {
    list.scrollTop = list.scrollHeight;
  }
}

function appendMessage(sessionId, msg) {
  if (sessionId !== state.selectedId) return;
  const list = dom.messageList;

  // Remove empty state if present
  const empty = list.querySelector('.empty-state');
  if (empty) empty.remove();

  const div = document.createElement('div');
  div.innerHTML = renderMessage(msg);
  list.appendChild(div.firstElementChild);

  if (state.autoScroll) {
    list.scrollTop = list.scrollHeight;
  }
}

function renderStats(stats) {
  dom.statTotal.textContent   = stats.total_agents;
  dom.statWorking.textContent = stats.working_agents;
  dom.statActive.textContent  = stats.active_agents;
  dom.statMessages.textContent = stats.total_messages.toLocaleString();
  dom.statUptime.textContent  = formatUptime(stats.uptime_seconds);
}

function renderActivityItem(event) {
  const el = document.createElement('div');
  el.className = 'activity-item';
  el.dataset.session = event.session_id;

  const time = formatTime(event.timestamp);
  el.innerHTML = `
    <div class="activity-item-top">
      <span class="activity-project">${escapeHtml(event.project || '')}</span>
      <span class="activity-role ${event.role || ''}">${event.role || ''}</span>
    </div>
    <div class="activity-text">${escapeHtml(event.text || '')}</div>
    <div class="activity-time">${time}</div>`;

  el.addEventListener('click', () => selectAgent(event.session_id));

  // Remove empty state
  const empty = dom.activityFeed.querySelector('.activity-empty');
  if (empty) empty.remove();

  dom.activityFeed.insertBefore(el, dom.activityFeed.firstChild);

  // Keep feed from growing unbounded
  while (dom.activityFeed.children.length > 50) {
    dom.activityFeed.lastChild.remove();
  }
}

// ─── Agent selection ───────────────────────────────────────────────────────────

async function selectAgent(sessionId) {
  if (state.selectedId === sessionId) return;
  state.selectedId = sessionId;
  state.autoScroll = true;

  // Update sidebar selection
  dom.agentList.querySelectorAll('.agent-card').forEach(card => {
    card.classList.toggle('selected', card.dataset.session === sessionId);
  });

  const agent = state.agents.find(a => a.session_id === sessionId);
  renderConversationHeader(agent);

  // Show loading
  dom.messageList.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:center;height:100%;gap:12px">
      <div class="loading-spinner"></div>
      <span style="color:var(--text-muted);font-size:12px;font-family:var(--font-mono)">Loading conversation…</span>
    </div>`;

  // Hide empty-conversation overlay
  if (dom.emptyConv) dom.emptyConv.style.display = 'none';

  try {
    const messages = await api.getMessages(sessionId);
    state.messages[sessionId] = messages;
    renderMessages(sessionId);
  } catch (e) {
    dom.messageList.innerHTML = `
      <div class="empty-state">
        <p style="color:var(--accent-red)">Failed to load messages</p>
        <p style="font-size:11px;color:var(--text-muted)">${escapeHtml(e.message)}</p>
      </div>`;
  }

  // Update send button state
  updateSendButton();
}

// ─── Message sending ───────────────────────────────────────────────────────────

function updateSendButton() {
  const hasInput = dom.messageInput.value.trim().length > 0;
  const hasAgent = state.selectedId != null;
  dom.sendBtn.disabled = !hasInput || !hasAgent || state.sending;
}

async function sendMessage() {
  const text = dom.messageInput.value.trim();
  if (!text || !state.selectedId || state.sending) return;

  state.sending = true;
  dom.sendBtn.classList.add('sending');
  dom.sendBtn.disabled = true;
  dom.messageInput.disabled = true;

  const sessionId = state.selectedId;

  // Optimistically add user message to UI
  const optimisticMsg = {
    uuid: `opt-${Date.now()}`,
    parent_uuid: null,
    role: 'user',
    content: [{ type: 'text', text }],
    timestamp: new Date().toISOString(),
    session_id: sessionId,
  };

  if (!state.messages[sessionId]) state.messages[sessionId] = [];
  state.messages[sessionId].push(optimisticMsg);
  appendMessage(sessionId, optimisticMsg);

  // Show typing indicator
  const typingEl = document.createElement('div');
  typingEl.className = 'message assistant';
  typingEl.id = 'typing-indicator';
  typingEl.innerHTML = `
    <div class="message-avatar">✦</div>
    <div class="message-body">
      <div class="message-bubble">
        <div class="typing-dots">
          <div class="typing-dot"></div>
          <div class="typing-dot"></div>
          <div class="typing-dot"></div>
        </div>
      </div>
    </div>`;
  dom.messageList.appendChild(typingEl);
  dom.messageList.scrollTop = dom.messageList.scrollHeight;

  dom.messageInput.value = '';
  dom.messageInput.style.height = 'auto';

  try {
    const result = await api.sendMessage(sessionId, text);
    if (!result.success) {
      toast(`Send failed: ${result.error || 'unknown error'}`, 'error');
    } else {
      toast('Message sent', 'success', 2000);
    }
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  } finally {
    // Remove typing indicator
    document.getElementById('typing-indicator')?.remove();

    state.sending = false;
    dom.sendBtn.classList.remove('sending');
    dom.messageInput.disabled = false;
    dom.messageInput.focus();
    updateSendButton();

    // Refresh messages for this session
    try {
      const messages = await api.getMessages(sessionId);
      state.messages[sessionId] = messages;
      renderMessages(sessionId);
    } catch (_) {}
  }
}

// ─── WebSocket events ──────────────────────────────────────────────────────────

function onWSMessage(msg) {
  switch (msg.type) {
    case 'agent_list': {
      state.agents = msg.data;
      renderAgentList();
      // Restore selection if agent still exists
      if (state.selectedId) {
        const agent = state.agents.find(a => a.session_id === state.selectedId);
        if (agent) {
          renderConversationHeader(agent);
        }
      }
      break;
    }

    case 'agent_update': {
      const updated = msg.data;
      const idx = state.agents.findIndex(a => a.session_id === updated.session_id);
      if (idx >= 0) state.agents[idx] = updated;
      else state.agents.push(updated);
      renderAgentList();
      if (state.selectedId === updated.session_id) {
        renderConversationHeader(updated);
      }
      break;
    }

    case 'new_message': {
      const { session_id, message } = msg.data;
      if (!state.messages[session_id]) state.messages[session_id] = [];
      state.messages[session_id].push(message);
      if (session_id === state.selectedId) {
        appendMessage(session_id, message);
      }
      break;
    }

    case 'stats_update': {
      state.stats = msg.data;
      renderStats(msg.data);
      break;
    }

    case 'activity_event': {
      renderActivityItem(msg.data);
      break;
    }
  }
}

// ─── Initialization ────────────────────────────────────────────────────────────

async function init() {
  // Initial data load
  try {
    const [agents, stats] = await Promise.all([
      api.getAgents(),
      api.getStats(),
    ]);
    state.agents = agents;
    state.stats = stats;
    renderAgentList();
    renderStats(stats);
  } catch (e) {
    toast(`Failed to load: ${e.message}`, 'error');
  }

  // WebSocket
  const ws = new WSClient({
    onOpen() {
      dom.connectionStatus.className = 'connection-status connected';
      dom.connLabel.textContent = 'connected';
    },
    onClose() {
      dom.connectionStatus.className = 'connection-status';
      dom.connLabel.textContent = 'disconnected';
    },
    onError() {
      dom.connectionStatus.className = 'connection-status error';
      dom.connLabel.textContent = 'error';
    },
    onReconnecting(delay) {
      dom.connectionStatus.className = 'connection-status';
      dom.connLabel.textContent = `reconnecting…`;
    },
    onMessage: onWSMessage,
  });

  // Event listeners
  dom.messageInput.addEventListener('input', () => {
    updateSendButton();
    // Auto-resize textarea
    dom.messageInput.style.height = 'auto';
    dom.messageInput.style.height = Math.min(dom.messageInput.scrollHeight, 160) + 'px';
  });

  dom.messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  dom.sendBtn.addEventListener('click', sendMessage);

  dom.refreshBtn.addEventListener('click', async () => {
    dom.refreshBtn.classList.add('spinning');
    try {
      const agents = await api.getAgents();
      state.agents = agents;
      renderAgentList();
      toast('Agents refreshed', 'success', 2000);
    } catch (e) {
      toast(`Refresh failed: ${e.message}`, 'error');
    } finally {
      setTimeout(() => dom.refreshBtn.classList.remove('spinning'), 600);
    }
  });

  dom.scrollBottomBtn.addEventListener('click', () => {
    state.autoScroll = true;
    dom.messageList.scrollTop = dom.messageList.scrollHeight;
  });

  // ── View switching (Agents ↔ Settings) ──────────────────────────────────
  const agentsView   = document.getElementById('main-layout');
  const settingsView = document.getElementById('settings-view');

  document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const view = tab.dataset.view;
      document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      if (view === 'settings') {
        agentsView.classList.add('hidden');
        settingsView.classList.remove('hidden');
        loadGlobalSettings();
      } else {
        settingsView.classList.add('hidden');
        agentsView.classList.remove('hidden');
      }
    });
  });

  // Default: agents tab active
  document.getElementById('tab-agents').classList.add('active');

  // Init settings panel
  initSettingsTabs();
  initGlobalSettingsEditor();

  dom.messageList.addEventListener('scroll', () => {
    const { scrollTop, scrollHeight, clientHeight } = dom.messageList;
    state.autoScroll = scrollHeight - scrollTop - clientHeight < 50;
  });

  // Uptime counter
  setInterval(() => {
    if (state.stats) {
      state.stats.uptime_seconds += 1;
      dom.statUptime.textContent = formatUptime(state.stats.uptime_seconds);
    }
  }, 1000);
}

init();
