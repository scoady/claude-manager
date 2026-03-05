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
  updateTileAgentStrip,
  updateTileForProject,
} from './projects.js';
import {
  formatUptime,
  toast,
} from './utils.js';
import { FeedController } from './feed/FeedController.js';
import { CanvasEngine } from './canvas/CanvasEngine.js';

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
  canvasView:         $('canvas-view'),
  canvasRoot:         $('canvas-root'),
};

// ─── Canvas Engine ─────────────────────────────────────────────────────────────
const canvasEngine = new CanvasEngine();

// ─── Feed controller ───────────────────────────────────────────────────────────
const feed = new FeedController(dom.feedEl, {
  onDeleteProject(name) {
    state.projects = state.projects.filter(p => p.name !== name);
    state.agents = state.agents.filter(a => a.project_name !== name);
    showTileGrid();
  },
});

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
      feed.appendAgentSection(agent.session_id, agent.task || '', {
        phase: agent.phase,
        turnCount: agent.turn_count,
        isController: agent.is_controller,
        taskIndex: agent.task_index ?? null,
      });
    }
  }
}

function showTileGrid() {
  state.selectedProject = null;
  dom.feedEl?.classList.add('hidden');
  dom.workbenchEmpty?.classList.remove('hidden');
  renderProjectList(state.projects, null, selectProject);
  renderProjectTileGrid(state.projects, state.agents, selectProject);
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
      renderProjectTileGrid(state.projects, state.agents, selectProject);
      break;
    }

    case 'stats_update': {
      state.stats = msg.data;
      renderStats(msg.data);
      break;
    }

    case 'agent_state_sync': {
      // Full state replacement on (re)connect — clear and rebuild
      const agents = msg.data.agents || [];
      state.agents = agents.map(a => ({
        session_id: a.session_id,
        project_name: a.project_name,
        task: a.task,
        status: a.status || 'idle',
        phase: a.phase || 'idle',
        turn_count: a.turn_count || 0,
        started_at: a.started_at || msg.timestamp,
        model: a.model,
        is_controller: a.is_controller || false,
        task_index: a.task_index ?? null,
        latest_milestone: '',
      }));
      // Re-render tiles for all affected projects
      const projects = new Set(agents.map(a => a.project_name));
      for (const p of projects) updateTileForProject(p, state.agents);
      // Sync feed sections
      feed.handleStateSync(agents);
      break;
    }

    case 'agent_spawned': {
      // New agent just appeared — append only
      const d = msg.data;
      if (!state.agents.find(a => a.session_id === d.session_id)) {
        state.agents.push({
          session_id: d.session_id,
          project_name: d.project_name,
          task: d.task,
          status: d.status || 'working',
          phase: d.phase || 'starting',
          turn_count: d.turn_count || 0,
          started_at: d.started_at || msg.timestamp,
          model: d.model,
          is_controller: d.is_controller || false,
          task_index: d.task_index ?? null,
          latest_milestone: '',
        });
      }
      updateTileForProject(d.project_name, state.agents);
      feed.handleEvent(msg);
      break;
    }

    case 'agent_done': {
      const { session_id, reason, project_name } = msg.data;
      // Grab project name before potentially removing the agent
      const pName = project_name || state.agents.find(a => a.session_id === session_id)?.project_name;
      if (reason !== 'idle') {
        state.agents = state.agents.filter(a => a.session_id !== session_id);
      } else {
        const agent = state.agents.find(a => a.session_id === session_id);
        if (agent) {
          agent.status = 'idle';
          agent.phase = 'idle';
        }
      }
      if (pName) updateTileForProject(pName, state.agents);
      feed.handleEvent(msg);
      break;
    }

    case 'agent_id_assigned': {
      const { old_session_id, session_id } = msg.data;
      const agent = state.agents.find(a => a.session_id === old_session_id);
      if (agent) agent.session_id = session_id;
      // Update tile strip data-session attr
      const strip = document.querySelector(`.agent-strip[data-session="${old_session_id}"]`);
      if (strip) strip.dataset.session = session_id;
      feed.handleEvent(msg);
      break;
    }

    case 'session_phase': {
      const { session_id, phase } = msg.data;
      const agent = state.agents.find(a => a.session_id === session_id);
      if (agent) {
        agent.phase = phase;
        updateTileAgentStrip(agent);
      }
      feed.handleEvent(msg);
      break;
    }

    case 'turn_done': {
      const { session_id, turn_count } = msg.data;
      const agent = state.agents.find(a => a.session_id === session_id);
      if (agent) {
        agent.turn_count = turn_count;
        updateTileAgentStrip(agent);
      }
      feed.handleEvent(msg);
      break;
    }

    case 'tool_start': {
      const { session_id, tool } = msg.data;
      const agent = state.agents.find(a => a.session_id === session_id);
      if (agent) {
        agent.latest_milestone = tool.tool_name;
        updateTileAgentStrip(agent);
      }
      feed.handleEvent(msg);
      break;
    }

    case 'agent_stream':
    case 'tool_done':
    case 'agent_milestone':
    case 'subagent_spawned':
    case 'subagent_done':
    case 'subagent_tasks': {
      feed.handleEvent(msg);
      break;
    }

    case 'agent_update': {
      const updated = msg.data;
      const idx = state.agents.findIndex(a => a.session_id === updated.session_id);
      if (idx >= 0) state.agents[idx] = { ...state.agents[idx], ...updated };
      break;
    }

    case 'tasks_updated': {
      const { project_name, tasks } = msg.data;
      feed.handleTasksUpdated(project_name, tasks);
      break;
    }

    case 'milestones_updated': {
      const { project_name, milestones } = msg.data;
      feed.handleMilestonesUpdated(project_name, milestones);
      break;
    }

    case 'workflow_updated': {
      const { project_name, workflow } = msg.data;
      feed.handleWorkflowUpdated(project_name, workflow);
      break;
    }

    // ── Canvas widget events — route to both Canvas page and dashboard ────
    case 'canvas_widget_created': {
      const w = msg.widget ?? msg.data?.widget ?? msg.data;
      if (w && !w.widget_id && w.id) w.widget_id = w.id;
      canvasEngine.create(w);
      feed.handleCanvasEvent('canvas_widget_created', msg.data ?? msg);
      break;
    }

    case 'canvas_widget_updated': {
      const widgetId = msg.widget_id ?? msg.data?.widget_id;
      const patch    = msg.patch    ?? msg.data?.patch ?? msg.data;
      if (widgetId) canvasEngine.update(widgetId, patch);
      feed.handleCanvasEvent('canvas_widget_updated', msg.data ?? msg);
      break;
    }

    case 'canvas_widget_removed': {
      const widgetId = msg.widget_id ?? msg.data?.widget_id ?? msg.data;
      if (widgetId) canvasEngine.remove(widgetId);
      feed.handleCanvasEvent('canvas_widget_removed', msg.data ?? msg);
      break;
    }

    case 'canvas_cleared': {
      feed.handleCanvasEvent('canvas_cleared', msg.data ?? msg);
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
  if ($('new-project-model')) $('new-project-model').value = '';
}

async function createProject() {
  const name = $('new-project-name')?.value.trim();
  const description = $('new-project-description')?.value.trim();
  const model = $('new-project-model')?.value || null;

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
    const project = await api.createProject(name, description, model);
    state.projects.push(project);
    renderProjectList(state.projects, state.selectedProject, selectProject);
    renderProjectTileGrid(state.projects, state.agents, selectProject);
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

// ─── Canvas ────────────────────────────────────────────────────────────────────

async function loadCanvasWidgets() {
  // Populate project selector
  const select = document.getElementById('canvas-project-select');
  if (select && select.options.length <= 1) {
    for (const p of state.projects) {
      const opt = document.createElement('option');
      opt.value = p.name;
      opt.textContent = p.name;
      select.appendChild(opt);
    }
  }

  // Load widgets for the selected project (or first project)
  const project = select?.value || state.projects[0]?.name;
  if (!project) return;

  try {
    const resp = await fetch(`/api/canvas/${encodeURIComponent(project)}`);
    const widgets = await resp.json();
    // Clear existing and load fresh
    canvasEngine.clear();
    for (const w of widgets) {
      if (!w.widget_id) w.widget_id = w.id;
      canvasEngine.create(w);
    }
  } catch (e) {
    console.warn('[canvas] Failed to load widgets:', e);
  }
}

function initCanvasPrompt() {
  const input = document.getElementById('canvas-prompt-input');
  const btn = document.getElementById('canvas-prompt-btn');
  const clearBtn = document.getElementById('canvas-clear-btn');
  const select = document.getElementById('canvas-project-select');
  const status = document.getElementById('canvas-prompt-status');
  if (!input || !btn) return;

  input.addEventListener('input', () => {
    btn.disabled = !input.value.trim() || !select?.value;
  });

  select?.addEventListener('change', () => {
    btn.disabled = !input.value.trim() || !select.value;
    // Reload widgets for selected project
    if (select.value) loadCanvasWidgets();
  });

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      dispatchCanvasPrompt();
    }
  });

  btn.addEventListener('click', () => dispatchCanvasPrompt());

  clearBtn?.addEventListener('click', async () => {
    const project = select?.value;
    if (!project) return;
    try {
      await fetch(`/api/canvas/${encodeURIComponent(project)}`, { method: 'DELETE' });
      canvasEngine.clear();
      toast('Canvas cleared', 'success', 2000);
    } catch (e) {
      toast(`Clear failed: ${e.message}`, 'error');
    }
  });

  async function dispatchCanvasPrompt() {
    const prompt = input.value.trim();
    const project = select?.value;
    if (!prompt || !project) return;

    btn.disabled = true;
    if (status) {
      status.textContent = 'Generating widgets...';
      status.className = 'canvas-prompt-status generating';
    }

    const canvasPrompt =
      `You have canvas MCP tools. Use canvas_put() to create widgets on the "${project}" canvas.\n\n` +
      `User request: "${prompt}"\n\n` +
      `Create visually rich HTML/CSS widgets using canvas_put(). Each widget should:\n` +
      `- Use self-contained inline HTML + CSS (no external deps)\n` +
      `- Use colors: cyan (#67e8f9), green (#4ade80), amber (#fbbf24), purple (#a78bfa)\n` +
      `- Use fonts: 'IBM Plex Mono' for data, 'Instrument Serif' for headings\n` +
      `- Use transparent backgrounds (the widget frame provides the card bg)\n` +
      `- Include subtle animations (glow, pulse, transitions)\n` +
      `Place widgets in a grid layout using grid_col/grid_row (1-indexed).`;

    try {
      const resp = await fetch(`/api/projects/${encodeURIComponent(project)}/dispatch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task: canvasPrompt }),
      });
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
      input.value = '';
      if (status) status.textContent = 'Agent dispatched — widgets will appear as they are created';
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
      if (status) {
        status.textContent = `Error: ${e.message}`;
        status.className = 'canvas-prompt-status';
      }
    } finally {
      btn.disabled = !input.value.trim();
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
    renderStats(stats);
  } catch (e) {
    toast(`Failed to load: ${e.message}`, 'error');
  }

  // Load running agents and normalize phase
  try {
    state.agents = (await api.getAgents()).map(a => ({
      ...a,
      phase: a.phase || (a.status === 'idle' ? 'idle' : 'thinking'),
      turn_count: a.turn_count || 0,
      latest_milestone: '',
    }));
  } catch (_) {}

  // Render tile grid after agents are loaded
  renderProjectTileGrid(state.projects, state.agents, selectProject);

  // Mount canvas engine to its host element
  if (dom.canvasRoot) {
    canvasEngine.mount(dom.canvasRoot);
  }

  initCanvasPrompt();

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
  const canvasView   = $('canvas-view');

  document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const view = tab.dataset.view;
      document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');

      // Hide all top-level views first.
      mainLayout?.classList.add('hidden');
      settingsView?.classList.add('hidden');
      canvasView?.classList.add('hidden');

      if (view === 'settings') {
        settingsView?.classList.remove('hidden');
        loadGlobalSettings();
      } else if (view === 'canvas') {
        canvasView?.classList.remove('hidden');
        loadCanvasWidgets();
      } else {
        // 'projects' (default)
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
  document.querySelector('.app-title')?.addEventListener('click', () => {
    const projectsTab = $('tab-projects');
    if (projectsTab) {
      document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
      projectsTab.classList.add('active');
      settingsView?.classList.add('hidden');
      canvasView?.classList.add('hidden');
      mainLayout?.classList.remove('hidden');
    }
    showTileGrid();
  });
  document.querySelector('.app-logo')?.addEventListener('click', () => {
    const projectsTab = $('tab-projects');
    if (projectsTab) {
      document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
      projectsTab.classList.add('active');
      settingsView?.classList.add('hidden');
      canvasView?.classList.add('hidden');
      mainLayout?.classList.remove('hidden');
    }
    showTileGrid();
  });

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
