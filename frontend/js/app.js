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
};

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

    // ── Canvas widget events — route to feed for dashboard display ────
    case 'canvas_widget_created':
    case 'canvas_widget_updated':
    case 'canvas_widget_removed':
    case 'canvas_cleared': {
      feed.handleCanvasEvent(msg.type, msg.data ?? msg);
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


// ─── Template Catalog ───────────────────────────────────────────────────────────

let _pendingTemplate = null; // holds generated template before save
let _catalogTemplates = [];  // cached full templates for filtering
let _activeCategory = 'all';
let _searchQuery = '';
let _viewMode = 'grid';      // 'grid' | 'list'
let _selectedTemplateId = null;

function _renderTemplatePreview(template, container) {
  // Render template HTML with preview_data substituted
  let html = template.html || '';
  let css = template.css || '';
  const data = template.preview_data || {};

  // {{#each key}}...{{/each}} expansion
  html = html.replace(/\{\{#each\s+(\w+)\}\}([\s\S]*?)\{\{\/each\}\}/g, (_, key, body) => {
    const items = data[key];
    if (!Array.isArray(items)) return '';
    return items.map((item, i) => {
      let chunk = body;
      if (typeof item === 'object') {
        for (const [k, v] of Object.entries(item)) chunk = chunk.replaceAll(`{{${k}}}`, String(v));
      } else {
        chunk = chunk.replaceAll('{{.}}', String(item));
      }
      return chunk.replaceAll('{{@index}}', String(i));
    }).join('');
  });

  // {{key}} replacement
  for (const [k, v] of Object.entries(data)) {
    if (typeof v !== 'object') {
      html = html.replaceAll(`{{${k}}}`, String(v));
      css = css.replaceAll(`{{${k}}}`, String(v));
    }
  }

  container.innerHTML = `<style>${css}</style>${html}`;

  // Execute JS if present
  if (template.js) {
    try {
      const fn = new Function('root', template.js);
      fn(container);
    } catch (_) {}
  }
}

const _BADGE_CLASS = {
  metrics: 'tpl-badge-metrics',
  chart: 'tpl-badge-chart',
  status: 'tpl-badge-status',
  log: 'tpl-badge-log',
  custom: 'tpl-badge-custom',
};

// Category-specific SVG icons for static thumbnails
const _THUMB_ICONS = {
  metrics: `<svg width="36" height="36" viewBox="0 0 36 36" fill="none">
    <rect x="4" y="20" width="6" height="12" rx="1.5" fill="currentColor" opacity="0.2"/>
    <rect x="15" y="12" width="6" height="20" rx="1.5" fill="currentColor" opacity="0.3"/>
    <rect x="26" y="6" width="6" height="26" rx="1.5" fill="currentColor" opacity="0.4"/>
  </svg>`,
  chart: `<svg width="36" height="36" viewBox="0 0 36 36" fill="none">
    <path d="M4 28L12 18L20 22L32 8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" opacity="0.4"/>
    <path d="M4 28L12 18L20 22L32 8V28H4Z" fill="currentColor" opacity="0.08"/>
  </svg>`,
  status: `<svg width="36" height="36" viewBox="0 0 36 36" fill="none">
    <circle cx="18" cy="18" r="12" stroke="currentColor" stroke-width="2" opacity="0.15"/>
    <circle cx="18" cy="18" r="12" stroke="currentColor" stroke-width="2" stroke-dasharray="53 22" opacity="0.4" transform="rotate(-90 18 18)"/>
    <circle cx="18" cy="18" r="3" fill="currentColor" opacity="0.3"/>
  </svg>`,
  log: `<svg width="36" height="36" viewBox="0 0 36 36" fill="none">
    <rect x="4" y="8" width="20" height="2" rx="1" fill="currentColor" opacity="0.3"/>
    <rect x="4" y="14" width="28" height="2" rx="1" fill="currentColor" opacity="0.2"/>
    <rect x="4" y="20" width="16" height="2" rx="1" fill="currentColor" opacity="0.25"/>
    <rect x="4" y="26" width="24" height="2" rx="1" fill="currentColor" opacity="0.15"/>
  </svg>`,
  custom: `<svg width="36" height="36" viewBox="0 0 36 36" fill="none">
    <rect x="6" y="6" width="10" height="10" rx="2" stroke="currentColor" stroke-width="1.5" opacity="0.25"/>
    <rect x="20" y="6" width="10" height="10" rx="2" stroke="currentColor" stroke-width="1.5" opacity="0.25"/>
    <rect x="6" y="20" width="10" height="10" rx="2" stroke="currentColor" stroke-width="1.5" opacity="0.25"/>
    <rect x="20" y="20" width="10" height="10" rx="2" stroke="currentColor" stroke-width="1.5" stroke-dasharray="3 3" opacity="0.2"/>
  </svg>`,
};

function _escHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function _getFilteredTemplates() {
  return _catalogTemplates.filter(t => {
    const cat = (t.category || 'custom').toLowerCase();
    if (_activeCategory !== 'all' && cat !== _activeCategory) return false;
    if (_searchQuery) {
      const q = _searchQuery.toLowerCase();
      const name = (t.name || t.id || '').toLowerCase();
      const desc = (t.description || '').toLowerCase();
      if (!name.includes(q) && !desc.includes(q) && !cat.includes(q)) return false;
    }
    return true;
  });
}

function _renderCatalogGrid() {
  const grid = $('template-grid');
  if (!grid) return;

  const filtered = _getFilteredTemplates();

  // Apply view mode class
  grid.classList.toggle('list-view', _viewMode === 'list');

  if (filtered.length === 0 && _catalogTemplates.length === 0) {
    grid.innerHTML = `
      <div class="studio-catalog-empty">
        <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
          <rect x="8" y="8" width="14" height="14" rx="3" stroke="currentColor" stroke-width="1.5"/>
          <rect x="26" y="8" width="14" height="14" rx="3" stroke="currentColor" stroke-width="1.5"/>
          <rect x="8" y="26" width="14" height="14" rx="3" stroke="currentColor" stroke-width="1.5"/>
          <rect x="26" y="26" width="14" height="14" rx="3" stroke="currentColor" stroke-width="1.5" stroke-dasharray="3 3"/>
        </svg>
        <div class="studio-catalog-empty-title">No templates yet</div>
        <div class="studio-catalog-empty-sub">Describe a widget above and generate your first reusable template</div>
      </div>`;
    return;
  }

  if (filtered.length === 0) {
    grid.innerHTML = `
      <div class="studio-catalog-empty">
        <div class="studio-catalog-empty-title">No matching templates</div>
        <div class="studio-catalog-empty-sub">Try a different search term or category filter</div>
      </div>`;
    return;
  }

  grid.innerHTML = filtered.map(t => {
    const cat = (t.category || 'custom').toLowerCase();
    const badgeClass = _BADGE_CLASS[cat] || _BADGE_CLASS.custom;
    const thumbIcon = _THUMB_ICONS[cat] || _THUMB_ICONS.custom;
    const colSpan = t.col_span || 1;
    const rowSpan = t.row_span || 1;
    return `
      <div class="tpl-card" data-template-id="${_escHtml(t.id)}">
        <div class="tpl-card-thumb" data-cat="${_escHtml(cat)}">
          <div class="tpl-card-thumb-icon">${thumbIcon}</div>
          <span class="tpl-card-thumb-size">${colSpan}x${rowSpan}</span>
        </div>
        <div class="tpl-card-info">
          <div class="tpl-card-top">
            <span class="tpl-card-name">${_escHtml(t.name || t.id)}</span>
            <span class="tpl-card-badge ${badgeClass}">${_escHtml(cat)}</span>
          </div>
          <div class="tpl-card-desc">${_escHtml(t.description || '')}</div>
        </div>
      </div>`;
  }).join('');

  // Click on card → open detail panel
  grid.querySelectorAll('.tpl-card').forEach(card => {
    card.addEventListener('click', () => {
      const id = card.dataset.templateId;
      _openDetailPanel(id);
    });
  });
}

async function _openDetailPanel(templateId) {
  const panel = $('studio-detail-panel');
  const backdrop = $('studio-detail-backdrop');
  if (!panel || !backdrop) return;

  _selectedTemplateId = templateId;

  // Find template in cache
  let template = _catalogTemplates.find(t => t.id === templateId);

  // Fetch full template (with html/css/js) if we only have metadata
  if (template && !template.html) {
    try {
      const resp = await fetch(`/api/widget-catalog/${encodeURIComponent(templateId)}`);
      if (resp.ok) {
        template = await resp.json();
        // Update cache
        const idx = _catalogTemplates.findIndex(t => t.id === templateId);
        if (idx >= 0) _catalogTemplates[idx] = template;
      }
    } catch (_) {}
  }

  if (!template) return;

  const cat = (template.category || 'custom').toLowerCase();
  const badgeClass = _BADGE_CLASS[cat] || _BADGE_CLASS.custom;

  // Populate header
  $('studio-detail-title').textContent = template.name || template.id || 'Untitled';
  const badge = $('studio-detail-badge');
  badge.textContent = cat;
  badge.className = `studio-detail-badge ${badgeClass}`;

  // Populate description
  $('studio-detail-desc').textContent = template.description || 'No description available.';

  // Populate metadata
  const metaEl = $('studio-detail-meta');
  const colSpan = template.col_span || 1;
  const rowSpan = template.row_span || 1;
  const schemaKeys = template.data_schema ? Object.keys(template.data_schema) : [];
  metaEl.innerHTML = `
    <div class="studio-detail-meta-item"><span class="meta-label">Size:</span> ${colSpan}x${rowSpan}</div>
    <div class="studio-detail-meta-item"><span class="meta-label">Category:</span> ${_escHtml(cat)}</div>
    ${schemaKeys.length ? `<div class="studio-detail-meta-item"><span class="meta-label">Data fields:</span> ${_escHtml(schemaKeys.join(', '))}</div>` : ''}
    ${template.created_at ? `<div class="studio-detail-meta-item"><span class="meta-label">Created:</span> ${new Date(template.created_at).toLocaleDateString()}</div>` : ''}
  `;

  // Render live preview (only here, not in the grid)
  const previewEl = $('studio-detail-preview');
  previewEl.innerHTML = '';
  if (template.html || template.css) {
    _renderTemplatePreview(template, previewEl);
  } else {
    previewEl.innerHTML = `<div style="color: var(--text-muted); font-size: 11px; font-family: var(--font-mono);">No preview available</div>`;
  }

  // Hide project picker on open
  $('studio-project-picker')?.classList.add('hidden');

  // Show panel with animation
  panel.classList.remove('hidden');
  backdrop.classList.remove('hidden');
  // Force reflow before adding visible class for CSS transition
  void panel.offsetWidth;
  panel.classList.add('visible');
  backdrop.classList.add('visible');
}

function _closeDetailPanel() {
  const panel = $('studio-detail-panel');
  const backdrop = $('studio-detail-backdrop');
  if (!panel || !backdrop) return;

  panel.classList.remove('visible');
  backdrop.classList.remove('visible');

  // Wait for transition before hiding
  setTimeout(() => {
    panel.classList.add('hidden');
    backdrop.classList.add('hidden');
    _selectedTemplateId = null;
  }, 350);
}

async function loadTemplateCatalog() {
  const grid = $('template-grid');
  const count = $('studio-template-count');
  if (!grid) return;

  try {
    const resp = await fetch('/api/widget-catalog');
    const templates = await resp.json();
    if (count) count.textContent = templates.length;
    _catalogTemplates = templates;
    _renderCatalogGrid();
  } catch (e) {
    console.warn('[catalog] Failed to load templates:', e);
  }
}

function initTemplateCatalog() {
  const promptEl = $('template-builder-prompt');
  const generateBtn = $('template-builder-generate');
  const statusEl = $('template-builder-status');
  const previewEl = $('template-builder-preview');
  const saveBtn = $('template-builder-save');
  const discardBtn = $('template-builder-discard');

  // Enable generate button when prompt has text
  promptEl?.addEventListener('input', () => {
    if (generateBtn) generateBtn.disabled = !promptEl.value.trim();
  });

  // Generate template
  generateBtn?.addEventListener('click', async () => {
    const prompt = promptEl?.value.trim();
    if (!prompt) return;

    generateBtn.disabled = true;
    generateBtn.textContent = 'Generating...';
    statusEl?.classList.remove('hidden');
    if (statusEl) statusEl.textContent = 'Claude is designing your template...';
    previewEl?.classList.add('hidden');
    _pendingTemplate = null;

    try {
      const resp = await fetch('/api/widget-catalog/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: prompt }),
      });
      if (!resp.ok) {
        const detail = await resp.json().then(d => d.detail).catch(() => resp.statusText);
        throw new Error(detail || `${resp.status} ${resp.statusText}`);
      }
      const template = await resp.json();
      _pendingTemplate = template;

      // Show preview
      $('template-preview-name').textContent = template.name || template.id || 'Untitled';
      $('template-preview-desc').textContent = template.description || '';

      const previewContent = $('template-preview-content');
      if (previewContent) {
        _renderTemplatePreview(template, previewContent);
      }

      previewEl?.classList.remove('hidden');
      statusEl?.classList.add('hidden');
    } catch (e) {
      if (statusEl) {
        statusEl.textContent = `Generation failed — ${e.message}. Try again or simplify your prompt.`;
        statusEl.classList.remove('hidden');
      }
      toast(`Generate failed: ${e.message}`, 'error');
    } finally {
      generateBtn.disabled = !promptEl?.value.trim();
      generateBtn.textContent = 'Generate';
    }
  });

  // Save template
  saveBtn?.addEventListener('click', async () => {
    if (!_pendingTemplate) return;
    try {
      const resp = await fetch('/api/widget-catalog', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(_pendingTemplate),
      });
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
      toast('Template saved to catalog', 'success', 3000);
      _pendingTemplate = null;
      previewEl?.classList.add('hidden');
      if (promptEl) promptEl.value = '';
      loadTemplateCatalog();
    } catch (e) {
      toast(`Save failed: ${e.message}`, 'error');
    }
  });

  // Discard
  discardBtn?.addEventListener('click', () => {
    _pendingTemplate = null;
    previewEl?.classList.add('hidden');
    statusEl?.classList.add('hidden');
    if (promptEl) promptEl.value = '';
  });

  // Enter key in prompt -> generate
  promptEl?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      generateBtn?.click();
    }
  });

  // ── Filter bar: search ─────────────────────────────────────────────────────
  const searchEl = $('studio-search');
  searchEl?.addEventListener('input', () => {
    _searchQuery = searchEl.value.trim();
    _renderCatalogGrid();
  });

  // ── Filter bar: category buttons ───────────────────────────────────────────
  document.querySelectorAll('.studio-cat-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.studio-cat-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _activeCategory = btn.dataset.category;
      _renderCatalogGrid();
    });
  });

  // ── View toggle: grid / list ───────────────────────────────────────────────
  document.querySelectorAll('.studio-view-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.studio-view-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _viewMode = btn.dataset.view;
      _renderCatalogGrid();
    });
  });

  // ── Detail panel: close ────────────────────────────────────────────────────
  $('studio-detail-close')?.addEventListener('click', _closeDetailPanel);
  $('studio-detail-backdrop')?.addEventListener('click', _closeDetailPanel);

  // ── Detail panel: copy canvas_put ──────────────────────────────────────────
  $('studio-detail-copy')?.addEventListener('click', () => {
    if (!_selectedTemplateId) return;
    const snippet = `canvas_put(project="PROJECT", widget_id="my-widget", title="Widget", template="${_selectedTemplateId}", data='{}')`;
    navigator.clipboard.writeText(snippet).then(() => {
      toast('canvas_put snippet copied', 'success', 2000);
    });
  });

  // ── Detail panel: delete ───────────────────────────────────────────────────
  $('studio-detail-delete')?.addEventListener('click', async () => {
    if (!_selectedTemplateId) return;
    try {
      await fetch(`/api/widget-catalog/${encodeURIComponent(_selectedTemplateId)}`, { method: 'DELETE' });
      toast('Template deleted', 'success', 2000);
      _closeDetailPanel();
      loadTemplateCatalog();
    } catch (err) {
      toast(`Delete failed: ${err.message}`, 'error');
    }
  });

  // ── Detail panel: Add to Canvas ────────────────────────────────────────────
  const pickerEl = $('studio-project-picker');
  const pickerSelect = $('studio-picker-select');

  $('studio-detail-add-canvas')?.addEventListener('click', async () => {
    if (!pickerEl || !pickerSelect) return;
    // Fetch projects and populate dropdown
    try {
      const projects = await api.getProjects();
      pickerSelect.innerHTML = projects.map(p =>
        `<option value="${_escHtml(p.name)}">${_escHtml(p.name)}</option>`
      ).join('');
      if (projects.length === 0) {
        pickerSelect.innerHTML = '<option value="" disabled>No projects found</option>';
      }
      pickerEl.classList.remove('hidden');
    } catch (e) {
      toast(`Failed to load projects: ${e.message}`, 'error');
    }
  });

  $('studio-picker-cancel')?.addEventListener('click', () => {
    pickerEl?.classList.add('hidden');
  });

  $('studio-picker-confirm')?.addEventListener('click', async () => {
    const project = pickerSelect?.value;
    if (!project || !_selectedTemplateId) return;

    // Find the full template
    let template = _catalogTemplates.find(t => t.id === _selectedTemplateId);
    if (!template) return;

    // Fetch full template if needed
    if (!template.html) {
      try {
        const resp = await fetch(`/api/widget-catalog/${encodeURIComponent(_selectedTemplateId)}`);
        if (resp.ok) template = await resp.json();
      } catch (_) {}
    }

    // POST widget to canvas
    try {
      const widgetBody = {
        title: template.name || template.id || 'Widget',
        html: template.html || '',
        css: template.css || '',
        js: template.js || '',
        template_id: template.id,
        template_data: template.preview_data || {},
        col_span: template.col_span || 1,
        row_span: template.row_span || 1,
        gs_w: Math.max((template.col_span || 1) * 4, 4),
        gs_h: Math.max((template.row_span || 1) * 3, 3),
      };

      const resp = await fetch(`/api/canvas/${encodeURIComponent(project)}/widgets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(widgetBody),
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }

      pickerEl?.classList.add('hidden');
      toast(`Widget added to "${project}" canvas`, 'success', 4000);
    } catch (e) {
      toast(`Failed to add widget: ${e.message}`, 'error');
    }
  });

  // ── Escape key closes detail panel ─────────────────────────────────────────
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && _selectedTemplateId) {
      _closeDetailPanel();
    }
  });

  // Initial load
  loadTemplateCatalog();
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

  initTemplateCatalog();

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
        loadTemplateCatalog();
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
