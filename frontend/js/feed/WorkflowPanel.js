/** WorkflowPanel — template-driven workflow setup and progress tracking. */
import { escapeHtml } from '../utils.js';
import { api } from '../api.js';
import { toast } from '../utils.js';

const TEMPLATE_ICONS = {
  code: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
  search: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  edit: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
  database: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>',
  default: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/><path d="M3 9h18"/></svg>',
};

export class WorkflowPanel {
  constructor(projectName) {
    this._project = projectName;
    this._workflow = null;
    this._templates = null;
    this._selectedTemplate = null;
    this._step = 'template'; // 'template' | 'config'
    this._el = document.createElement('div');
    this._el.className = 'workflow-panel';
    this._refreshTimer = null;
    this._render();
  }

  get el() { return this._el; }

  async load() {
    try {
      this._workflow = await api.getWorkflow(this._project);
      this._render();
    } catch (e) {
      this._el.innerHTML = '<div class="wf-error">Failed to load workflow</div>';
    }
  }

  updateWorkflow(workflow) {
    this._workflow = workflow;
    this._render();
  }

  startAutoRefresh() {
    this.stopAutoRefresh();
    this._refreshTimer = setInterval(() => this.load(), 15_000);
  }

  stopAutoRefresh() {
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }
  }

  destroy() { this.stopAutoRefresh(); }

  // ── Template Loading ───────────────────────────────────────────────────────

  async _loadTemplates() {
    if (this._templates) return;
    try {
      this._templates = await api.getTemplates();
    } catch (e) {
      this._templates = [];
      console.error('Failed to load templates:', e);
    }
  }

  // ── Rendering ─────────────────────────────────────────────────────────────

  _render() {
    if (!this._workflow || this._workflow.status === 'draft') {
      this._renderSetup();
    } else {
      this._renderProgress();
    }
  }

  async _renderSetup() {
    await this._loadTemplates();

    if (this._step === 'template' && !this._selectedTemplate) {
      this._renderTemplateSelector();
    } else {
      this._renderConfigForm();
    }
  }

  _renderTemplateSelector() {
    const templates = this._templates || [];

    this._el.innerHTML = `
      <div class="wf-setup">
        <h3 class="wf-setup-title">Team Workflow</h3>
        <p class="wf-setup-desc">Choose a workflow template. Each template defines roles, phases, and isolation strategy for different types of work.</p>

        <div class="wf-template-grid">
          ${templates.map(t => `
            <div class="wf-template-card" data-id="${escapeHtml(t.id)}">
              <div class="wf-template-icon">${TEMPLATE_ICONS[t.icon] || TEMPLATE_ICONS.default}</div>
              <div class="wf-template-info">
                <div class="wf-template-name">${escapeHtml(t.name)}</div>
                <div class="wf-template-desc">${escapeHtml(t.description)}</div>
                <div class="wf-template-meta">
                  <span class="wf-template-category">${escapeHtml(t.category)}</span>
                  <span class="wf-template-isolation">${escapeHtml(t.isolation_strategy.replace('_', ' '))}</span>
                </div>
              </div>
            </div>
          `).join('')}
        </div>
      </div>
    `;

    this._el.querySelectorAll('.wf-template-card').forEach(card => {
      card.addEventListener('click', () => {
        const id = card.dataset.id;
        this._selectedTemplate = this._templates.find(t => t.id === id);
        this._step = 'config';
        this._render();
      });
    });
  }

  _renderConfigForm() {
    const tpl = this._selectedTemplate;
    if (!tpl) { this._step = 'template'; this._render(); return; }

    const wf = this._workflow;
    const team = wf?.team || tpl.default_team || [
      { role: tpl.role_presets[0]?.role || 'worker', count: 1, instructions: '' },
    ];

    // Build config values from template defaults + existing workflow values
    const configValues = {};
    for (const [key, field] of Object.entries(tpl.config_schema || {})) {
      configValues[key] = wf?.config?.values?.[key] ?? field.default;
    }

    this._el.innerHTML = `
      <div class="wf-setup">
        <div class="wf-setup-header">
          <button class="wf-back-btn" title="Change template">&larr;</button>
          <div>
            <h3 class="wf-setup-title">${escapeHtml(tpl.name)}</h3>
            <p class="wf-setup-desc">${escapeHtml(tpl.description)}</p>
          </div>
        </div>

        <div class="wf-team-section">
          <label class="wf-label">Team Composition</label>
          <div class="wf-team-rows">
            ${team.map((r, i) => this._roleRow(r, i, tpl.role_presets)).join('')}
          </div>
          <button class="wf-add-role-btn">+ Add Role</button>
        </div>

        <div class="wf-config-section">
          <label class="wf-label">Configuration</label>
          <div class="wf-config-grid">
            ${this._renderConfigFields(tpl.config_schema, configValues)}
          </div>
        </div>

        <div class="wf-actions">
          <button class="wf-start-btn">Create & Start Workflow</button>
        </div>
      </div>
    `;
    this._bindConfigEvents(tpl);
  }

  _renderConfigFields(schema, values) {
    if (!schema) return '';
    return Object.entries(schema).map(([key, field]) => {
      const val = values[key] ?? field.default ?? '';
      if (field.type === 'number') {
        return `
          <div class="wf-config-field">
            <label>${escapeHtml(field.label)}</label>
            <input type="number" class="wf-config-input" data-key="${escapeHtml(key)}"
              value="${val}" min="${field.min || ''}" max="${field.max || ''}" />
          </div>`;
      } else if (field.type === 'string') {
        return `
          <div class="wf-config-field">
            <label>${escapeHtml(field.label)}</label>
            <input type="text" class="wf-config-input" data-key="${escapeHtml(key)}"
              value="${escapeHtml(String(val))}" />
          </div>`;
      } else if (field.type === 'boolean') {
        return `
          <div class="wf-config-field">
            <label class="wf-checkbox-label">
              <input type="checkbox" class="wf-config-input" data-key="${escapeHtml(key)}"
                ${val ? 'checked' : ''} />
              ${escapeHtml(field.label)}
            </label>
          </div>`;
      } else if (field.type === 'select') {
        return `
          <div class="wf-config-field">
            <label>${escapeHtml(field.label)}</label>
            <select class="wf-config-input" data-key="${escapeHtml(key)}">
              ${(field.options || []).map(opt =>
                `<option value="${escapeHtml(opt)}" ${opt === val ? 'selected' : ''}>${escapeHtml(opt)}</option>`
              ).join('')}
            </select>
          </div>`;
      }
      return '';
    }).join('');
  }

  _roleRow(role, index, presets) {
    const rolePresets = presets || [{ role: 'worker', label: 'Worker' }];
    return `
      <div class="wf-role-row" data-index="${index}">
        <select class="wf-role-select">
          ${rolePresets.map(p =>
            `<option value="${p.role}" ${p.role === role.role ? 'selected' : ''}>${escapeHtml(p.label)}</option>`
          ).join('')}
        </select>
        <input type="number" class="wf-role-count" value="${role.count}" min="1" max="8" />
        <input type="text" class="wf-role-instructions" placeholder="Custom instructions..." value="${escapeHtml(role.instructions || '')}" />
        <button class="wf-remove-role" data-index="${index}" title="Remove">&times;</button>
      </div>
    `;
  }

  _renderProgress() {
    const wf = this._workflow;
    const completedCount = wf.phases.filter(p => p.status === 'complete').length;
    const totalPhases = wf.phases.length;
    const pct = Math.round((completedCount / totalPhases) * 100);

    this._el.innerHTML = `
      <div class="wf-progress">
        <div class="wf-progress-header">
          <h3 class="wf-progress-title">Team Workflow</h3>
          <span class="wf-status-badge wf-status-${wf.status}">${wf.status}</span>
        </div>

        <div class="wf-team-chips">
          ${wf.team.map(r => `<span class="wf-team-chip">${r.count}x ${escapeHtml(r.role)}</span>`).join('')}
        </div>

        <div class="wf-overall-progress">
          <div class="wf-progress-bar"><div class="wf-progress-fill" style="width:${pct}%"></div></div>
          <span class="wf-progress-label">${completedCount}/${totalPhases} phases (${pct}%)</span>
        </div>

        <div class="wf-phase-timeline">
          ${wf.phases.map((p, i) => this._phaseItem(p, i, wf.current_phase_index)).join('')}
        </div>

        ${this._renderIsolation(wf)}

        <div class="wf-controls">
          ${wf.status === 'running' ? `
            <button class="wf-pause-btn">Pause</button>
            <button class="wf-skip-btn">Skip Phase</button>
          ` : ''}
          ${wf.status === 'paused' ? `
            <button class="wf-resume-btn">Resume</button>
          ` : ''}
          ${wf.status === 'complete' ? `
            <button class="wf-new-btn">New Workflow</button>
          ` : ''}
          <button class="wf-cancel-btn danger">Cancel Workflow</button>
        </div>
      </div>
    `;
    this._bindProgressEvents();
  }

  _renderIsolation(wf) {
    // Show isolation entries if available, otherwise fall back to worktrees
    const items = wf.isolation?.length ? wf.isolation : wf.worktrees;
    if (!items?.length) return '';

    return `
      <div class="wf-worktrees">
        <label class="wf-label">Active Workspaces</label>
        ${items.map(it => `
          <div class="wf-worktree-row wf-wt-${it.status}">
            <span class="wf-wt-role">${escapeHtml(it.role)}-${it.instance}</span>
            ${it.branch ? `<span class="wf-wt-branch">${escapeHtml(it.branch)}</span>` : ''}
            <span class="wf-wt-path">${escapeHtml(it.path?.split('/').pop() || '')}</span>
            <span class="wf-wt-status">${it.status}</span>
          </div>
        `).join('')}
      </div>
    `;
  }

  _phaseItem(phase, index, currentIndex) {
    let statusClass = 'wf-phase-pending';
    if (phase.status === 'complete') statusClass = 'wf-phase-complete';
    else if (phase.status === 'active') statusClass = 'wf-phase-active';

    // Use phase_label from template-driven backend; fall back to legacy lookup
    const label = phase.phase_label || this._legacyPhaseLabel(phase);

    return `
      <div class="wf-phase-item ${statusClass}" data-index="${index}">
        <div class="wf-phase-dot"></div>
        <div class="wf-phase-label">${escapeHtml(label)}</div>
        ${phase.summary ? `<div class="wf-phase-summary">${escapeHtml(phase.summary)}</div>` : ''}
      </div>
    `;
  }

  _legacyPhaseLabel(phase) {
    const PHASE_LABELS = {
      quarter_planning: 'Quarter Planning',
      sprint_planning: 'Sprint Planning',
      sprint_execution: 'Sprint Execution',
      sprint_review: 'Sprint Review',
      sprint_retrospective: 'Sprint Retro',
      complete: 'Complete',
    };
    const sn = phase.sprint_number || phase.iteration_number;
    const pt = phase.phase_type || phase.phase_id;
    if (sn) return `S${sn}: ${PHASE_LABELS[pt] || pt}`;
    return PHASE_LABELS[pt] || pt || 'Unknown';
  }

  // ── Events ────────────────────────────────────────────────────────────────

  _bindConfigEvents(tpl) {
    this._el.querySelector('.wf-back-btn')?.addEventListener('click', () => {
      this._selectedTemplate = null;
      this._step = 'template';
      this._render();
    });

    this._el.querySelector('.wf-add-role-btn')?.addEventListener('click', () => {
      const rows = this._el.querySelector('.wf-team-rows');
      const index = rows.querySelectorAll('.wf-role-row').length;
      const defaultRole = tpl.role_presets[0]?.role || 'worker';
      const wrapper = document.createElement('div');
      wrapper.innerHTML = this._roleRow(
        { role: defaultRole, count: 1, instructions: '' }, index, tpl.role_presets
      );
      rows.appendChild(wrapper.firstElementChild);
    });

    this._el.addEventListener('click', (e) => {
      if (e.target.classList.contains('wf-remove-role')) {
        e.target.closest('.wf-role-row')?.remove();
      }
    });

    this._el.querySelector('.wf-start-btn')?.addEventListener('click', async () => {
      const team = this._collectTeam();
      const config = this._collectConfig(tpl);
      if (!team.length) { toast('Add at least one team role', 'error'); return; }

      const btn = this._el.querySelector('.wf-start-btn');
      btn.disabled = true;
      btn.textContent = 'Starting...';
      try {
        await api.createWorkflow(this._project, team, config, tpl.id);
        await api.startWorkflow(this._project);
        toast('Workflow started', 'success');
        await this.load();
      } catch (e) {
        toast(`Failed: ${e.message}`, 'error');
        btn.disabled = false;
        btn.textContent = 'Create & Start Workflow';
      }
    });
  }

  _bindProgressEvents() {
    this._el.querySelector('.wf-pause-btn')?.addEventListener('click', async () => {
      try {
        await api.workflowAction(this._project, 'pause');
        toast('Workflow paused', 'success');
        await this.load();
      } catch (e) { toast(`Failed: ${e.message}`, 'error'); }
    });

    this._el.querySelector('.wf-resume-btn')?.addEventListener('click', async () => {
      try {
        await api.workflowAction(this._project, 'resume');
        toast('Workflow resumed', 'success');
        await this.load();
      } catch (e) { toast(`Failed: ${e.message}`, 'error'); }
    });

    this._el.querySelector('.wf-skip-btn')?.addEventListener('click', async () => {
      if (!confirm('Skip the current phase?')) return;
      try {
        await api.workflowAction(this._project, 'skip_phase');
        toast('Phase skipped', 'success');
        await this.load();
      } catch (e) { toast(`Failed: ${e.message}`, 'error'); }
    });

    this._el.querySelector('.wf-cancel-btn')?.addEventListener('click', async () => {
      if (!confirm('Cancel the entire workflow? Workspaces will be cleaned up.')) return;
      try {
        await api.deleteWorkflow(this._project);
        this._workflow = null;
        this._selectedTemplate = null;
        this._step = 'template';
        toast('Workflow cancelled', 'success');
        this._render();
      } catch (e) { toast(`Failed: ${e.message}`, 'error'); }
    });

    this._el.querySelector('.wf-new-btn')?.addEventListener('click', async () => {
      try {
        await api.deleteWorkflow(this._project);
        this._workflow = null;
        this._selectedTemplate = null;
        this._step = 'template';
        this._render();
      } catch (e) { toast(`Failed: ${e.message}`, 'error'); }
    });
  }

  _collectTeam() {
    const rows = this._el.querySelectorAll('.wf-role-row');
    return Array.from(rows).map(row => ({
      role: row.querySelector('.wf-role-select').value,
      count: parseInt(row.querySelector('.wf-role-count').value, 10) || 1,
      instructions: row.querySelector('.wf-role-instructions').value.trim(),
    }));
  }

  _collectConfig(tpl) {
    const values = {};
    this._el.querySelectorAll('.wf-config-input').forEach(input => {
      const key = input.dataset.key;
      const fieldDef = tpl.config_schema?.[key];
      if (!fieldDef) return;

      if (fieldDef.type === 'boolean') {
        values[key] = input.checked;
      } else if (fieldDef.type === 'number') {
        values[key] = parseInt(input.value, 10) || fieldDef.default || 1;
      } else {
        values[key] = input.value;
      }
    });

    // Extract auto_continue from values to put at top level
    const autoContinue = values.auto_continue !== undefined ? values.auto_continue : true;
    delete values.auto_continue;

    return {
      auto_continue: autoContinue,
      values,
    };
  }
}
