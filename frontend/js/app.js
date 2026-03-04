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
} from './projects.js';
import {
  formatUptime,
  toast,
} from './utils.js';
import { FeedController } from './feed/FeedController.js';

// ─── State ─────────────────────────────────────────────────────────────────────
const state = {
  projects: [],            // ManagedProject[]
  agents: [],              // Agent[] (running only)
  selectedProject: null,   // project name string
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
  feedEl:             $('feed'),
};

// ─── Feed controller ───────────────────────────────────────────────────────────
const feed = new FeedController(dom.feedEl);

// ─── Project selection ─────────────────────────────────────────────────────────

async function selectProject(name) {
  if (state.selectedProject === name) return;
  state.selectedProject = name;

  // Update sidebar selection
  renderProjectList(state.projects, name, selectProject);

  // Switch from tile grid to feed
  dom.workbenchEmpty?.classList.add('hidden');
  dom.feedEl?.classList.remove('hidden');

  // Find project and initialize feed
  const project = state.projects.find(p => p.name === name);
  if (project) {
    feed.setProject(project);
  }

  // Re-attach any already-running agents for this project
  const running = state.agents.filter(a => a.project_name === name);
  for (const agent of running) {
    if (agent.session_id) {
      feed.appendAgentSection(agent.session_id, agent.task || '');
    }
  }
}

function showTileGrid() {
  state.selectedProject = null;
  dom.feedEl?.classList.add('hidden');
  dom.workbenchEmpty?.classList.remove('hidden');
  renderProjectList(state.projects, null, selectProject);
  renderProjectTileGrid(state.projects, selectProject);
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
      break;
    }

    case 'stats_update': {
      state.stats = msg.data;
      renderStats(msg.data);
      break;
    }

    case 'agent_spawned': {
      const d = msg.data;
      if (!state.agents.find(a => a.session_id === d.session_id)) {
        state.agents.push({
          session_id: d.session_id,
          project_name: d.project_name,
          task: d.task,
          status: d.status || 'working',
          phase: d.phase,
          turn_count: d.turn_count || 0,
          started_at: d.started_at || msg.timestamp,
          model: d.model,
        });
      }
      // Feed handles this via handleEvent if project matches
      feed.handleEvent(msg);
      break;
    }

    case 'agent_done': {
      const { session_id, reason } = msg.data;
      if (reason !== 'idle') {
        // Only remove agents that are truly done (cancelled, error)
        state.agents = state.agents.filter(a => a.session_id !== session_id);
      } else {
        // Idle agents stay — update their status
        const agent = state.agents.find(a => a.session_id === session_id);
        if (agent) agent.status = 'idle';
      }
      feed.handleEvent(msg);
      break;
    }

    case 'agent_id_assigned': {
      const { old_session_id, session_id } = msg.data;
      const agent = state.agents.find(a => a.session_id === old_session_id);
      if (agent) agent.session_id = session_id;
      feed.handleEvent(msg);
      break;
    }

    case 'agent_stream':
    case 'session_phase':
    case 'tool_start':
    case 'tool_done':
    case 'turn_done':
    case 'agent_milestone': {
      feed.handleEvent(msg);
      break;
    }

    case 'agent_update': {
      const updated = msg.data;
      const idx = state.agents.findIndex(a => a.session_id === updated.session_id);
      if (idx >= 0) state.agents[idx] = { ...state.agents[idx], ...updated };
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
  if ($('new-project-description')) $('new-project-description').value = '';
}

async function createProject() {
  const name = $('new-project-name')?.value.trim();
  const description = $('new-project-description')?.value.trim();

  if (!name) {
    toast('Project name is required', 'error');
    return;
  }
  if (!description) {
    toast('Please describe what you want to build', 'error');
    return;
  }

  const btn = $('modal-create-btn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Starting agent…';
  }

  try {
    const project = await api.createProject(name, description);
    state.projects.push(project);
    renderProjectList(state.projects, state.selectedProject, selectProject);
    renderProjectTileGrid(state.projects, selectProject);
    closeNewProjectModal();
    toast(`"${name}" created — agent starting`, 'success');
    await selectProject(name);
  } catch (e) {
    toast(`Create failed: ${e.message}`, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Create & Start Agent';
    }
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

  // Load running agents
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

  // ── New project modal ─────────────────────────────────────────────────────
  $('new-project-btn')?.addEventListener('click', openNewProjectModal);
  $('new-project-tile-btn')?.addEventListener('click', openNewProjectModal);
  $('new-project-empty-btn')?.addEventListener('click', openNewProjectModal);
  $('modal-close-btn')?.addEventListener('click', closeNewProjectModal);
  $('modal-cancel-btn')?.addEventListener('click', closeNewProjectModal);
  $('modal-backdrop')?.addEventListener('click', closeNewProjectModal);
  $('modal-create-btn')?.addEventListener('click', createProject);

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
