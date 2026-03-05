/** FeedController — owns the #feed DOM element and manages the dashboard + agent feed. */
import { escapeHtml } from '../utils.js';
import { AgentSection } from './AgentSection.js';
import { TasksPanel } from './TasksPanel.js';
import { MilestonesPanel } from './MilestonesPanel.js';
import { WorkflowPanel } from './WorkflowPanel.js';
import { ArtifactsPanel } from './ArtifactsPanel.js';
import { renderMarkdown } from './MarkdownRenderer.js';
import { CanvasEngine } from '../canvas/CanvasEngine.js';
import { api } from '../api.js';
import { toast } from '../utils.js';

const LANE_COLORS = [
  '#67e8f9', // cyan
  '#c084fc', // purple
  '#4ade80', // green
  '#f9a8d4', // pink
  '#fbbf24', // amber
  '#f87171', // red
  '#5eead4', // teal
  '#a78bfa', // lavender
];

export class FeedController {
  /**
   * @param {HTMLElement} container — the #feed element
   */
  constructor(container, { onDeleteProject } = {}) {
    this._el        = container;
    this._project   = null;      // current ManagedProject
    this._sections  = new Map(); // sessionId → AgentSection
    this._laneIndex = 0;
    this._headerEl  = null;
    this._focusedSessionId = null;
    this._activeTab = 'overview';
    this._agentContainer = null;
    this._tasksContainer = null;
    this._tasksPanel = null;
    this._milestonesContainer = null;
    this._milestonesPanel = null;
    this._workflowContainer = null;
    this._workflowPanel = null;
    this._artifactsContainer = null;
    this._artifactsPanel = null;
    this._subagentMap = new Map(); // tool_use_id → subagent section id
    this._taskAgentMap = new Map(); // taskIndex → sessionId
    this._onDeleteProject = onDeleteProject || null;
    this._dashboardCanvas = null;
    this._dashboardWidgetGrid = null;
  }

  // ── Project ────────────────────────────────────────────────────────────────

  /** Switch to a new project — clears old sections, renders project header. */
  setProject(project) {
    this._project = project;
    this._sections.clear();
    this._laneIndex = 0;
    this._activeTab = 'overview';
    this._taskAgentMap.clear();
    this._el.innerHTML = '';

    // 1. Header (tabs + dispatch composer)
    this._headerEl = this._buildHeader(project);
    this._el.appendChild(this._headerEl);

    // 2. Overview container — system strip + agent widget grid
    this._overviewContainer = document.createElement('div');
    this._overviewContainer.className = 'overview-container';

    // 2a. System strip — reserved top zone for system-generated widgets (tasks, agents)
    this._systemStrip = document.createElement('div');
    this._systemStrip.className = 'system-widget-strip';
    this._systemCanvas = new CanvasEngine(null, { static: true });
    this._overviewContainer.appendChild(this._systemStrip);
    this._systemCanvas.mount(this._systemStrip);

    // 2b. Agent widget grid (agent-controlled, drag/resize via GridStack)
    this._dashboardWidgetGrid = document.createElement('div');
    this._dashboardWidgetGrid.className = 'dashboard-widget-grid';
    this._overviewContainer.appendChild(this._dashboardWidgetGrid);
    this._dashboardCanvas = new CanvasEngine(null, {
      onRemove: (widgetId) => this._deleteWidget(project.name, widgetId),
    });
    this._dashboardCanvas.setProject(project.name);
    this._dashboardCanvas.mount(this._dashboardWidgetGrid);

    this._el.appendChild(this._overviewContainer);

    // 3. Tasks container (hidden by default)
    this._tasksContainer = document.createElement('div');
    this._tasksContainer.className = 'feed-tasks-container hidden';
    this._el.appendChild(this._tasksContainer);

    if (this._tasksPanel) this._tasksPanel.destroy();
    this._tasksPanel = new TasksPanel(project.name);
    this._tasksPanel.setTaskAgentMap(this._taskAgentMap);
    this._tasksContainer.appendChild(this._tasksPanel.el);

    // 4. Milestones container (hidden by default)
    this._milestonesContainer = document.createElement('div');
    this._milestonesContainer.className = 'feed-milestones-container hidden';
    this._el.appendChild(this._milestonesContainer);

    if (this._milestonesPanel) this._milestonesPanel.destroy();
    this._milestonesPanel = new MilestonesPanel(project.name);
    this._milestonesContainer.appendChild(this._milestonesPanel.el);

    // 5. Workflow container (hidden by default)
    this._workflowContainer = document.createElement('div');
    this._workflowContainer.className = 'feed-workflow-container hidden';
    this._el.appendChild(this._workflowContainer);

    if (this._workflowPanel) this._workflowPanel.destroy();
    this._workflowPanel = new WorkflowPanel(project.name);
    this._workflowContainer.appendChild(this._workflowPanel.el);

    // 6. Artifacts container (hidden by default)
    this._artifactsContainer = document.createElement('div');
    this._artifactsContainer.className = 'feed-artifacts-container hidden';
    this._el.appendChild(this._artifactsContainer);

    if (this._artifactsPanel) this._artifactsPanel.destroy();
    this._artifactsPanel = new ArtifactsPanel(project.name);
    this._artifactsContainer.appendChild(this._artifactsPanel.el);

    this._bindTabEvents();

    // 7. Load initial data
    this._loadInitialData(project);
  }

  async _loadInitialData(project) {
    // Fetch tasks (read-only)
    try {
      const tasks = await api.getTasks(project.name);
      this._tasksPanel?.updateTasks(tasks);
    } catch (_) {}

    // Load any existing dashboard widgets (no auto-seeding)
    await this._loadDashboardWidgets(project.name);
  }

  async _loadDashboardWidgets(projectName) {
    try {
      const resp = await fetch(`/api/canvas/${encodeURIComponent(projectName)}`);
      const widgets = await resp.json();
      this._dashboardCanvas.clear();
      this._systemCanvas.clear();

      // Always seed system widgets (they update from live data)
      await this._seedDashboard(projectName);

      for (const w of widgets) {
        if (!w.widget_id) w.widget_id = w.id;
        // Route sys- widgets to system strip, rest to agent grid
        if (w.widget_id?.startsWith('sys-')) {
          this._systemCanvas.create(w);
        } else {
          this._dashboardCanvas.create(w);
        }
      }

      // Add "Add from Template" placeholder tile
      this._addTemplatePlaceholder(projectName);
    } catch (_) {}
  }

  _addTemplatePlaceholder(projectName) {
    if (!this._dashboardWidgetGrid || !this._dashboardCanvas) return;

    // Remove existing placeholder if any
    const existing = this._dashboardWidgetGrid.querySelector('.gs-add-placeholder');
    if (existing && this._dashboardCanvas._grid) {
      this._dashboardCanvas._grid.removeWidget(existing, false);
    } else if (existing) {
      existing.remove();
    }

    const placeholderContent = `
      <div class="add-widget-placeholder">
        <div class="add-widget-icon">
          <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
            <rect x="3" y="3" width="22" height="22" rx="6" stroke="currentColor" stroke-width="1.5" stroke-dasharray="4 3" opacity="0.4"/>
            <path d="M14 9v10M9 14h10" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
          </svg>
        </div>
        <span class="add-widget-label">Add from Template</span>
        <span class="add-widget-hint">Browse widget catalog</span>
      </div>
    `;

    if (this._dashboardCanvas._grid) {
      const gridEl = this._dashboardCanvas._grid.addWidget({
        w: 4, h: 3, content: placeholderContent, noResize: true, noMove: true,
        id: '__add-placeholder__',
      });
      gridEl.classList.add('gs-add-placeholder');
      gridEl.dataset.widgetId = '__add-placeholder__';
      gridEl.addEventListener('click', () => {
        this._showTemplatePickerModal(projectName);
      });
    }
  }

  async _showTemplatePickerModal(projectName) {
    // Fetch available templates
    let templates = [];
    try {
      const resp = await fetch('/api/widget-catalog');
      templates = await resp.json();
    } catch (_) {
      toast('Failed to load templates', 'error');
      return;
    }

    if (templates.length === 0) {
      toast('No templates available — create one in Widget Studio', 'info');
      return;
    }

    // Build modal
    const modal = document.createElement('div');
    modal.className = 'template-picker-modal';
    modal.innerHTML = `
      <div class="template-picker-backdrop"></div>
      <div class="template-picker-dialog">
        <div class="template-picker-header">
          <span class="template-picker-title">Add Widget from Template</span>
          <button class="template-picker-close">&times;</button>
        </div>
        <div class="template-picker-grid">
          ${templates.map(t => `
            <div class="template-picker-card" data-template-id="${t.template_id || t.id}">
              <div class="template-picker-card-header">
                <span class="template-picker-card-name">${escapeHtml(t.name || t.template_id || 'Untitled')}</span>
                <span class="template-picker-card-category">${escapeHtml(t.category || 'custom')}</span>
              </div>
              <div class="template-picker-card-desc">${escapeHtml(t.description || '')}</div>
            </div>
          `).join('')}
        </div>
      </div>
    `;

    const close = () => modal.remove();
    modal.querySelector('.template-picker-backdrop').addEventListener('click', close);
    modal.querySelector('.template-picker-close').addEventListener('click', close);

    modal.querySelectorAll('.template-picker-card').forEach(card => {
      card.addEventListener('click', async () => {
        const templateId = card.dataset.templateId;
        const tpl = templates.find(t => (t.template_id || t.id) === templateId);
        close();
        await this._addWidgetFromTemplate(projectName, tpl);
      });
    });

    document.body.appendChild(modal);
  }

  async _addWidgetFromTemplate(projectName, template) {
    try {
      const templateId = template.id || template.template_id;
      const widgetId = `tpl-${Date.now().toString(36)}`;

      // Pass template_id + template_data (preview_data as initial seed).
      // The backend canvas service renders HTML from the catalog template
      // and stores template_id + data so agents can push fresh data later.
      const body = {
        id: widgetId,
        title: template.name || 'Widget',
        template_id: templateId,
        template_data: template.preview_data || {},
        gs_w: Math.max(template.col_span || 1, 1) * 4,
        gs_h: Math.max(template.row_span || 1, 1) * 3,
      };
      await fetch(`/api/canvas/${encodeURIComponent(projectName)}/widgets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      toast(`Added "${template.name}" widget — agents can push live data to it`, 'success');
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    }
  }

  async _deleteWidget(projectName, widgetId) {
    try {
      await fetch(`/api/canvas/${encodeURIComponent(projectName)}/widgets/${encodeURIComponent(widgetId)}`, {
        method: 'DELETE',
      });
    } catch (e) {
      console.warn('[FeedController] Failed to delete widget:', e);
    }
  }

  async _clearAllWidgets(projectName) {
    if (!confirm('Clear all dashboard widgets?')) return;
    try {
      await fetch(`/api/canvas/${encodeURIComponent(projectName)}`, {
        method: 'DELETE',
      });
      this._dashboardCanvas?.clear();
      this._addTemplatePlaceholder(projectName);
      toast('Dashboard cleared', 'success', 2000);
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    }
  }

  async _seedDashboard(projectName) {
    try {
      const resp = await fetch(`/api/canvas/${encodeURIComponent(projectName)}/seed`, {
        method: 'POST',
      });
      if (!resp.ok) return;
      const result = await resp.json();
      if (!result.seeded) return;
      // Widgets will arrive via WS broadcast — no need to manually create here
    } catch (_) {}
  }

  /** Route a canvas widget event to the appropriate zone (system strip vs agent grid). */
  handleCanvasEvent(type, data) {
    if (!this._project || !this._dashboardCanvas) return;

    const _engine = (widgetId) =>
      widgetId?.startsWith('sys-') ? this._systemCanvas : this._dashboardCanvas;

    if (type === 'canvas_widget_created') {
      const w = data.widget ?? data;
      if (w.project && w.project !== this._project.name) return;
      if (w && !w.widget_id && w.id) w.widget_id = w.id;
      _engine(w.widget_id || w.id).create(w);
    } else if (type === 'canvas_widget_updated') {
      const widgetId = data.widget_id;
      const patch = data.patch ?? data;
      if (widgetId) _engine(widgetId).update(widgetId, patch);
    } else if (type === 'canvas_widget_removed') {
      const widgetId = data.widget_id ?? data;
      if (widgetId) _engine(widgetId).remove(widgetId);
    } else if (type === 'canvas_cleared') {
      if (data.project === this._project.name) {
        this._dashboardCanvas.clear();
        this._systemCanvas.clear();
      }
    }
  }

  async _startTask(taskIndex) {
    try {
      const result = await api.startTask(this._project.name, taskIndex);
      toast(`Agent started: ${result.task}`, 'success', 3000);
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    }
  }

  _buildHeader(project) {
    const el = document.createElement('div');
    el.className = 'feed-project-header';
    const agentCount = project.active_session_ids?.length || 0;
    el.innerHTML = `
      <div class="feed-header-row">
        <span class="feed-project-title">${escapeHtml(project.name)}</span>
        <div class="feed-project-meta">
          <span class="feed-meta-chip">${agentCount} agent${agentCount !== 1 ? 's' : ''}</span>
          <span class="feed-meta-chip">&times;${project.config?.parallelism || 1} parallelism</span>
          ${project.config?.model ? `<span class="feed-meta-chip">${escapeHtml(project.config.model.split('-').slice(-2).join('-'))}</span>` : ''}
          <button class="feed-delete-project-btn" title="Delete project">
            <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
              <path d="M3.5 3V2.5a1 1 0 011-1h4a1 1 0 011 1V3M2 3h9M4.5 5.5v4M6.5 5.5v4M8.5 5.5v4M3 3h7l-.5 7.5a1 1 0 01-1 .5h-4a1 1 0 01-1-.5L3 3z" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>
        </div>
      </div>
      <div class="feed-tab-bar">
        <button class="feed-tab active" data-feed-tab="overview">Overview</button>
        <button class="feed-tab" data-feed-tab="tasks">Tasks</button>
        <button class="feed-tab" data-feed-tab="milestones">Milestones</button>
        <button class="feed-tab" data-feed-tab="workflow">Workflow</button>
        <button class="feed-tab" data-feed-tab="artifacts">Artifacts</button>
        <button class="feed-save-layout-btn" title="Save dashboard layout">
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
            <path d="M10.5 11.5H2.5a1 1 0 01-1-1V2.5a1 1 0 011-1h6.59a1 1 0 01.7.29l1.92 1.92a1 1 0 01.29.7V10.5a1 1 0 01-1 1z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>
            <path d="M4.5 1.5v3h4v-3M4.5 8h4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
          </svg>
          Save Layout
        </button>
        <button class="feed-clear-widgets-btn" title="Clear all widgets">
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
            <path d="M3.5 3V2.5a1 1 0 011-1h4a1 1 0 011 1V3M2 3h9M4.5 5.5v4M6.5 5.5v4M8.5 5.5v4M3 3h7l-.5 7.5a1 1 0 01-1 .5h-4a1 1 0 01-1-.5L3 3z" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          Clear All
        </button>
      </div>
      <div class="feed-dispatch-composer">
        <div class="feed-dispatch-row">
          <textarea class="feed-task-input" rows="1" placeholder="Dispatch a task\u2026"></textarea>
          <select class="feed-parallelism-select" title="Parallelism">
            ${[1,2,3,4].map(n => `<option value="${n}" ${n === (project.config?.parallelism || 1) ? 'selected' : ''}>&times;${n}</option>`).join('')}
          </select>
          <select class="feed-model-select" title="Model override">
            <option value="">default</option>
            <option value="claude-opus-4-6" ${project.config?.model === 'claude-opus-4-6' ? 'selected' : ''}>opus</option>
            <option value="claude-sonnet-4-6" ${project.config?.model === 'claude-sonnet-4-6' ? 'selected' : ''}>sonnet</option>
            <option value="claude-haiku-4-5-20251001" ${project.config?.model === 'claude-haiku-4-5-20251001' ? 'selected' : ''}>haiku</option>
          </select>
          <button class="feed-dispatch-btn" disabled>
            <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
              <path d="M2 7.5h11M9 3l5 4.5L9 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>
        </div>
      </div>
      <div class="skill-toggle-panel">
        <div class="skill-toggle-header" title="Toggle skills for this project">
          <svg class="skill-toggle-chevron" width="10" height="10" viewBox="0 0 10 10" fill="none">
            <path d="M3 2l4 3-4 3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <span class="skill-toggle-title">Skills</span>
          <span class="skill-toggle-count"></span>
        </div>
        <div class="skill-toggle-body hidden"></div>
      </div>`;
    this._bindHeaderEvents(el, project);
    this._loadSkillsPanel(el, project);
    return el;
  }

  _bindHeaderEvents(el, project) {
    const textarea   = el.querySelector('.feed-task-input');
    const dispatchBtn = el.querySelector('.feed-dispatch-btn');
    const parallelSel = el.querySelector('.feed-parallelism-select');
    const modelSel   = el.querySelector('.feed-model-select');

    textarea?.addEventListener('input', () => {
      dispatchBtn.disabled = !textarea.value.trim();
      textarea.style.height = 'auto';
      textarea.style.height = Math.min(textarea.scrollHeight, 160) + 'px';
    });

    textarea?.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this._dispatch(project.name, textarea, parallelSel, modelSel, dispatchBtn);
      }
    });

    dispatchBtn?.addEventListener('click', () => {
      this._dispatch(project.name, textarea, parallelSel, modelSel, dispatchBtn);
    });

    parallelSel?.addEventListener('change', async () => {
      try {
        await api.updateProjectConfig(project.name, {
          parallelism: parseInt(parallelSel.value, 10),
          model: modelSel.value || null,
        });
      } catch (_) {}
    });

    modelSel?.addEventListener('change', async () => {
      try {
        await api.updateProjectConfig(project.name, {
          parallelism: parseInt(parallelSel.value, 10),
          model: modelSel.value || null,
        });
      } catch (_) {}
    });

    // Save layout
    el.querySelector('.feed-save-layout-btn')?.addEventListener('click', async () => {
      await this._dashboardCanvas.saveLayout();
      toast('Layout saved', 'success', 2000);
    });

    // Clear all widgets
    el.querySelector('.feed-clear-widgets-btn')?.addEventListener('click', () => {
      this._clearAllWidgets(project.name);
    });

    // Delete project
    el.querySelector('.feed-delete-project-btn')?.addEventListener('click', async () => {
      if (!confirm(`Delete project "${project.name}"?\n\nThis will kill all running agents and move the project to unmanaged/.`)) return;
      try {
        await api.deleteProject(project.name);
        toast(`"${project.name}" deleted`, 'success');
        if (this._onDeleteProject) this._onDeleteProject(project.name);
      } catch (e) {
        toast(`Delete failed: ${e.message}`, 'error');
      }
    });
  }

  async _dispatch(projectName, textarea, parallelSel, modelSel, btn) {
    const task = textarea?.value.trim();
    if (!task) return;

    const parallelism = parseInt(parallelSel?.value || '1', 10);
    const model = modelSel?.value || null;

    btn.disabled = true;
    btn.classList.add('sending');

    try {
      await api.updateProjectConfig(projectName, { parallelism, model });
      const result = await api.dispatchTask(projectName, task, model || undefined);
      textarea.value = '';
      textarea.style.height = 'auto';
      const route = result.status === 'delegated' ? 'controller' : 'new agent';
      toast(`Dispatched to ${route}`, 'success', 2000);
    } catch (e) {
      toast(`Dispatch failed: ${e.message}`, 'error');
    } finally {
      btn.classList.remove('sending');
      btn.disabled = !textarea?.value.trim();
    }
  }

  // ── Skills panel ──────────────────────────────────────────────────────────

  async _loadSkillsPanel(headerEl, project) {
    const panel = headerEl.querySelector('.skill-toggle-panel');
    const header = panel.querySelector('.skill-toggle-header');
    const body = panel.querySelector('.skill-toggle-body');
    const countEl = panel.querySelector('.skill-toggle-count');

    // Toggle collapse
    header.addEventListener('click', () => {
      const open = body.classList.toggle('hidden');
      panel.classList.toggle('open', !open);
    });

    // Load skills
    try {
      const skills = await api.getProjectSkills(project.name);
      const enabledCount = skills.filter(s => s.enabled).length;
      countEl.textContent = `(${enabledCount} of ${skills.length} enabled)`;

      if (!skills.length) {
        body.innerHTML = '<div class="skill-empty">No skills available. Create skills in Settings.</div>';
        return;
      }

      body.innerHTML = skills.map(s => `
        <div class="skill-row" data-skill="${escapeHtml(s.name)}" data-source="${escapeHtml(s.source)}">
          <span class="skill-dot ${s.enabled ? 'on' : ''}"></span>
          <span class="skill-name">${escapeHtml(s.name)}</span>
          <span class="skill-desc">${escapeHtml(s.description || '')}</span>
          <span class="skill-source-badge source-${escapeHtml(s.source)}">${escapeHtml(s.source)}</span>
          ${s.source !== 'local' ? `
            <label class="toggle-switch toggle-sm" title="${s.enabled ? 'Disable' : 'Enable'} skill">
              <input type="checkbox" ${s.enabled ? 'checked' : ''} data-skill-name="${escapeHtml(s.name)}" />
              <span class="toggle-track"></span>
            </label>` : '<span class="skill-always-on">always on</span>'}
        </div>`).join('');

      // Bind toggles
      body.querySelectorAll('input[data-skill-name]').forEach(cb => {
        cb.addEventListener('change', async () => {
          const skillName = cb.dataset.skillName;
          const enabled = cb.checked;
          try {
            if (enabled) {
              await api.enableProjectSkill(project.name, skillName);
            } else {
              await api.disableProjectSkill(project.name, skillName);
            }
            const dot = cb.closest('.skill-row').querySelector('.skill-dot');
            dot.classList.toggle('on', enabled);
            // Update count
            const allCbs = body.querySelectorAll('input[data-skill-name]');
            const localCount = body.querySelectorAll('.skill-always-on').length;
            const checkedCount = Array.from(allCbs).filter(c => c.checked).length + localCount;
            countEl.textContent = `(${checkedCount} of ${skills.length} enabled)`;
            toast(`Skill ${skillName} ${enabled ? 'enabled' : 'disabled'}`, 'success', 2000);
          } catch (e) {
            cb.checked = !enabled;
            toast(`Failed: ${e.message}`, 'error');
          }
        });
      });
    } catch (e) {
      body.innerHTML = `<div class="skill-empty">Failed to load skills</div>`;
    }
  }

  // ── Sections ───────────────────────────────────────────────────────────────

  /** Create and append an AgentSection for a newly spawned agent. */
  appendAgentSection(sessionId, task, { phase, turnCount, isController, taskIndex } = {}) {
    if (this._sections.has(sessionId)) return this._sections.get(sessionId);

    const color = isController ? '#fbbf24' : LANE_COLORS[this._laneIndex % LANE_COLORS.length];
    if (!isController) this._laneIndex++;

    const section = new AgentSection({
      sessionId,
      task,
      laneColor: color,
      isController: isController || false,
      initialPhase: phase,
      initialTurnCount: turnCount,
      onInject: (sid, msg) => this._inject(sid, msg),
      onKill:   (sid) => this._kill(sid),
      onStatus: (sid) => this._askStatus(sid),
      onFocus:  (sid) => this._setFocused(sid),
    });

    this._sections.set(sessionId, section);

    // Skip mounting agents that are already idle/done
    const isAlreadyDone = ['idle', 'cancelled', 'error'].includes(phase);
    if (isAlreadyDone) {
      return section;
    }

    if (taskIndex != null) {
      this._taskAgentMap.set(taskIndex, sessionId);
    }

    // Hydrate existing agent output on reconnect
    if (turnCount > 0 || (phase && phase !== 'starting')) {
      api.getMessages(sessionId).then(messages => {
        section.hydrateMessages(messages);
      }).catch(() => {});
    }

    return section;
  }

  /** Get an existing section by sessionId. */
  getSection(sessionId) {
    return this._sections.get(sessionId);
  }

  /** Remap a pending-PID session ID to the real UUID. */
  remapSessionId(oldId, newId) {
    const section = this._sections.get(oldId);
    if (!section) return;
    section.updateSessionId(newId);
    this._sections.delete(oldId);
    this._sections.set(newId, section);

    // Update task agent map
    for (const [idx, sid] of this._taskAgentMap) {
      if (sid === oldId) {
        this._taskAgentMap.set(idx, newId);
        break;
      }
    }
  }

  /** Handle full agent state sync from server (on connect/reconnect). */
  handleStateSync(agents) {
    if (!this._project) return;

    const projectAgents = agents.filter(a => a.project_name === this._project.name);
    const syncIds = new Set(projectAgents.map(a => a.session_id));

    // Remove sections that no longer exist on the server
    for (const [sid] of this._sections) {
      if (!syncIds.has(sid)) {
        const col = this._agentContainer?.querySelector(`[data-session="${sid}"]`);
        if (col) col.remove();
        this._sections.delete(sid);
      }
    }

    // Add/update sections for each agent in the sync
    for (const a of projectAgents) {
      const existing = this._sections.get(a.session_id);
      if (existing) {
        existing.setPhase(a.phase || 'idle');
        if (a.turn_count) existing.setTurnCount(a.turn_count);
      } else {
        this.appendAgentSection(a.session_id, a.task, {
          phase: a.phase,
          turnCount: a.turn_count,
          isController: a.is_controller,
          taskIndex: a.task_index ?? null,
        });
      }
    }
  }

  // ── WS event routing ───────────────────────────────────────────────────────

  handleEvent(msg) {
    switch (msg.type) {
      case 'agent_spawned': {
        const { session_id, project_name, task, phase, turn_count, is_controller, task_index } = msg.data;
        if (!this._project || project_name !== this._project.name) return;
        this.appendAgentSection(session_id, task, {
          phase: phase,
          turnCount: turn_count,
          isController: is_controller,
          taskIndex: task_index ?? null,
        });
        break;
      }
      case 'agent_stream': {
        const { session_id, chunk, done } = msg.data;
        if (done) return;
        const section = this._sections.get(session_id);
        section?.appendChunk(chunk);
        // Also route to TasksPanel for per-task output
        this._tasksPanel?.appendAgentChunk(session_id, chunk);
        break;
      }
      case 'session_phase': {
        const { session_id, phase } = msg.data;
        const section = this._sections.get(session_id);
        section?.setPhase(phase);
        break;
      }
      case 'tool_start': {
        const { session_id, tool } = msg.data;
        // Route milestone to TasksPanel
        this._tasksPanel?.addToolMilestone(session_id, tool.tool_name, tool.tool_input);
        let startSection = null;
        if (tool.parent_tool_use_id) {
          const subId = this._subagentMap.get(tool.parent_tool_use_id);
          const sub = subId ? this._sections.get(subId) : null;
          if (sub?.el?.isConnected) startSection = sub;
        }
        if (!startSection) startSection = this._sections.get(session_id);
        startSection?.addToolBlock({
          toolId:    tool.tool_id,
          toolName:  tool.tool_name,
          toolInput: tool.tool_input,
        });
        break;
      }
      case 'tool_done': {
        const { session_id, tool } = msg.data;
        let doneSection = null;
        if (tool.parent_tool_use_id) {
          const subId = this._subagentMap.get(tool.parent_tool_use_id);
          const sub = subId ? this._sections.get(subId) : null;
          if (sub?.el?.isConnected) doneSection = sub;
        }
        if (!doneSection) doneSection = this._sections.get(session_id);
        doneSection?.updateToolBlock(tool.tool_id, tool.output);
        // Route tool completion to TasksPanel for modal tool timeline
        this._tasksPanel?.completeToolMilestone(session_id, tool.output);
        break;
      }
      case 'turn_done': {
        const { session_id, turn_count } = msg.data;
        const section = this._sections.get(session_id);
        section?.setTurnCount(turn_count);
        section?.updateStatusCard();
        break;
      }
      case 'agent_done': {
        const { session_id, reason } = msg.data;
        const section = this._sections.get(session_id);
        section?.markDone(reason);
        this._fadeOutDoneSection(section);
        break;
      }
      case 'agent_id_assigned': {
        const { old_session_id, session_id } = msg.data;
        this.remapSessionId(old_session_id, session_id);
        break;
      }
      case 'agent_milestone': {
        break;
      }

      case 'subagent_spawned': {
        const { session_id, tool_use_id, description, subagent_type } = msg.data;
        const controllerSection = this._sections.get(session_id);
        if (!controllerSection) return;

        controllerSection.setPhase('delegating');

        const subId = `subagent-${tool_use_id}`;
        const color = LANE_COLORS[this._laneIndex % LANE_COLORS.length];
        this._laneIndex++;

        const childSection = new AgentSection({
          sessionId: subId,
          task: description,
          laneColor: color,
          isSubagent: true,
          subagentType: subagent_type,
          initialPhase: 'thinking',
          onInject: () => {},
          onKill:   () => {},
          onStatus: () => {},
        });

        childSection.setCompactMode(true);
        this._sections.set(subId, childSection);
        this._subagentMap.set(tool_use_id, subId);

        this._updateControllerMonitoring();
        break;
      }

      case 'subagent_done': {
        const { session_id, tool_use_id, result, is_error } = msg.data;
        const subId = this._subagentMap.get(tool_use_id);
        if (!subId) return;

        const section = this._sections.get(subId);
        if (section) {
          if (result) section.setSubagentResult(result);
          section.markDone(is_error ? 'error' : 'idle');
          this._fadeOutDoneSection(section);
        }

        this._subagentMap.delete(tool_use_id);
        this._updateControllerMonitoring();
        break;
      }

      case 'subagent_tasks': {
        const { tool_use_id, todos } = msg.data;
        const subId = this._subagentMap.get(tool_use_id);
        if (!subId) return;
        const section = this._sections.get(subId);
        section?.updateTaskList(todos);
        break;
      }
    }
  }

  // ── Tab switching ──────────────────────────────────────────────────────────

  _bindTabEvents() {
    const tabs = this._headerEl.querySelectorAll('.feed-tab');
    tabs.forEach(tab => {
      tab.addEventListener('click', () => {
        const tabName = tab.dataset.feedTab;
        if (tabName === this._activeTab) return;
        this._activeTab = tabName;

        tabs.forEach(t => t.classList.remove('active'));
        tab.classList.add('active');

        // Hide all containers
        this._overviewContainer?.classList.add('hidden');
        this._tasksContainer?.classList.add('hidden');
        this._milestonesContainer?.classList.add('hidden');
        this._workflowContainer?.classList.add('hidden');
        this._artifactsContainer?.classList.add('hidden');

        // Overview-only UI elements
        const showOverviewUI = (tabName === 'overview');
        this._headerEl.querySelector('.feed-dispatch-composer')?.classList.toggle('hidden', !showOverviewUI);
        this._headerEl.querySelector('.skill-toggle-panel')?.classList.toggle('hidden', !showOverviewUI);
        this._headerEl.querySelector('.feed-save-layout-btn')?.classList.toggle('hidden', !showOverviewUI);
        this._headerEl.querySelector('.feed-clear-widgets-btn')?.classList.toggle('hidden', !showOverviewUI);

        // Stop panel refreshes
        this._milestonesPanel?.stopAutoRefresh();
        this._workflowPanel?.stopAutoRefresh();

        if (tabName === 'overview') {
          this._overviewContainer?.classList.remove('hidden');
        } else if (tabName === 'tasks') {
          this._tasksContainer?.classList.remove('hidden');
          this._tasksPanel?.load();
        } else if (tabName === 'milestones') {
          this._milestonesContainer?.classList.remove('hidden');
          this._milestonesPanel?.load();
          this._milestonesPanel?.startAutoRefresh();
        } else if (tabName === 'workflow') {
          this._workflowContainer?.classList.remove('hidden');
          this._workflowPanel?.load();
          this._workflowPanel?.startAutoRefresh();
        } else if (tabName === 'artifacts') {
          this._artifactsContainer?.classList.remove('hidden');
          this._artifactsPanel?.load();
        }
      });
    });
  }

  /** Handle tasks_updated WS event. */
  handleTasksUpdated(projectName, tasks) {
    if (this._project && this._project.name === projectName) {
      this._tasksPanel?.updateTasks(tasks);
    }
  }

  /** Handle workflow_updated WS event. */
  handleWorkflowUpdated(projectName, workflow) {
    if (this._project && this._project.name === projectName) {
      this._workflowPanel?.updateWorkflow(workflow);
    }
  }

  /** Handle milestones_updated WS event. */
  handleMilestonesUpdated(projectName, milestones) {
    if (this._project && this._project.name === projectName) {
      this._milestonesPanel?.updateMilestones(milestones);
    }
  }

  // ── Adaptive layout ────────────────────────────────────────────────────────

  _setFocused(sessionId) {
    this._focusedSessionId = sessionId;
    for (const [sid, section] of this._sections) {
      if (sid === sessionId) {
        section.setExpanded(true);
      } else if (!section._done) {
        section.setExpanded(false);
      }
    }
  }

  // ── Done agent cleanup ───────────────────────────────────────────────────

  _fadeOutDoneSection(section, delay = 2000) {
    if (!section?.el?.isConnected) return;

    setTimeout(() => {
      if (!section.el.isConnected) return;
      const col = section.el.closest('.agent-strip-card');
      const target = col || section.el;
      target.style.transition = 'opacity 0.4s ease, max-width 0.4s ease, padding 0.4s ease, flex 0.4s ease';
      target.style.opacity = '0';
      target.style.maxWidth = '0';
      target.style.padding = '0';
      target.style.flex = '0';
      target.style.overflow = 'hidden';
      setTimeout(() => {
        if (target.isConnected) target.remove();
      }, 500);
    }, delay);
  }

  // ── Agent actions ──────────────────────────────────────────────────────────

  async _inject(sessionId, message) {
    try {
      const result = await api.injectMessage(sessionId, message);
      const status = result.status === 'queued' ? 'queued (agent is working)' : 'sent';
      toast(`Message ${status}`, 'success', 2000);
    } catch (e) {
      toast(`Inject failed: ${e.message}`, 'error');
    }
  }

  async _kill(sessionId) {
    try {
      await api.killAgent(sessionId);
      const section = this._sections.get(sessionId);
      section?.markDone('cancelled');
      toast('Agent killed', 'warn', 2000);
    } catch (e) {
      toast(`Kill failed: ${e.message}`, 'error');
    }
  }

  async _askStatus(sessionId) {
    try {
      await api.injectMessage(sessionId, 'Please give a brief status update: what have you completed, what are you working on right now, and what\'s next?');
      toast('Status request sent', 'success', 2000);
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    }
  }

  /** Update controller's monitoring indicator with active subagent count and colors. */
  _updateControllerMonitoring() {
    let controllerSection = null;
    for (const [, section] of this._sections) {
      if (section._isController) { controllerSection = section; break; }
    }
    if (!controllerSection) return;

    const colors = [];
    for (const [, subId] of this._subagentMap) {
      const sub = this._sections.get(subId);
      if (sub && !sub._done) {
        colors.push(sub.laneColor);
      }
    }

    controllerSection.setMonitoring(colors.length, colors);
  }
}
