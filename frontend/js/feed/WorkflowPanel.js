/** WorkflowPanel — team workflow setup and progress tracking. */
import { escapeHtml } from '../utils.js';
import { api } from '../api.js';
import { toast } from '../utils.js';

const ROLE_PRESETS = [
  { role: 'engineer', label: 'Software Engineer' },
  { role: 'qa', label: 'QA Engineer' },
  { role: 'devops', label: 'DevOps' },
  { role: 'designer', label: 'Designer' },
];

const PHASE_LABELS = {
  quarter_planning:     'Quarter Planning',
  sprint_planning:      'Sprint Planning',
  sprint_execution:     'Sprint Execution',
  sprint_review:        'Sprint Review',
  sprint_retrospective: 'Sprint Retro',
  complete:             'Complete',
};

export class WorkflowPanel {
  constructor(projectName) {
    this._project = projectName;
    this._workflow = null;
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

  // ── Rendering ─────────────────────────────────────────────────────────────

  _render() {
    if (!this._workflow || this._workflow.status === 'draft') {
      this._renderSetup();
    } else {
      this._renderProgress();
    }
  }

  _renderSetup() {
    const wf = this._workflow;
    const team = wf?.team || [
      { role: 'engineer', count: 2, instructions: '' },
      { role: 'qa', count: 1, instructions: '' },
    ];
    const config = wf?.config || {
      total_sprints: 4,
      auto_continue: true,
      sprint_duration_hint: '1 week',
      merge_strategy: 'squash',
    };

    this._el.innerHTML = `
      <div class="wf-setup">
        <h3 class="wf-setup-title">Team Workflow</h3>
        <p class="wf-setup-desc">Define your team and sprint configuration. The system will autonomously plan, execute, review, and report — sprint by sprint.</p>

        <div class="wf-team-section">
          <label class="wf-label">Team Composition</label>
          <div class="wf-team-rows">
            ${team.map((r, i) => this._roleRow(r, i)).join('')}
          </div>
          <button class="wf-add-role-btn">+ Add Role</button>
        </div>

        <div class="wf-config-section">
          <label class="wf-label">Sprint Configuration</label>
          <div class="wf-config-grid">
            <div class="wf-config-field">
              <label>Total Sprints</label>
              <input type="number" class="wf-sprints-input" value="${config.total_sprints}" min="1" max="12" />
            </div>
            <div class="wf-config-field">
              <label>Sprint Duration</label>
              <input type="text" class="wf-duration-input" value="${escapeHtml(config.sprint_duration_hint)}" />
            </div>
            <div class="wf-config-field">
              <label>Merge Strategy</label>
              <select class="wf-merge-select">
                <option value="squash" ${config.merge_strategy === 'squash' ? 'selected' : ''}>Squash</option>
                <option value="merge" ${config.merge_strategy === 'merge' ? 'selected' : ''}>Merge</option>
              </select>
            </div>
            <div class="wf-config-field">
              <label class="wf-checkbox-label">
                <input type="checkbox" class="wf-auto-continue" ${config.auto_continue !== false ? 'checked' : ''} />
                Auto-continue between phases
              </label>
            </div>
          </div>
        </div>

        <div class="wf-actions">
          <button class="wf-start-btn">Create & Start Workflow</button>
        </div>
      </div>
    `;
    this._bindSetupEvents();
  }

  _roleRow(role, index) {
    return `
      <div class="wf-role-row" data-index="${index}">
        <select class="wf-role-select">
          ${ROLE_PRESETS.map(p =>
            `<option value="${p.role}" ${p.role === role.role ? 'selected' : ''}>${p.label}</option>`
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

        ${wf.worktrees?.length ? `
          <div class="wf-worktrees">
            <label class="wf-label">Active Worktrees</label>
            ${wf.worktrees.map(wt => `
              <div class="wf-worktree-row wf-wt-${wt.status}">
                <span class="wf-wt-role">${escapeHtml(wt.role)}-${wt.instance}</span>
                <span class="wf-wt-branch">${escapeHtml(wt.branch)}</span>
                <span class="wf-wt-status">${wt.status}</span>
              </div>
            `).join('')}
          </div>
        ` : ''}

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

  _phaseItem(phase, index, currentIndex) {
    let statusClass = 'wf-phase-pending';
    if (phase.status === 'complete') statusClass = 'wf-phase-complete';
    else if (phase.status === 'active') statusClass = 'wf-phase-active';

    const label = phase.sprint_number
      ? `S${phase.sprint_number}: ${PHASE_LABELS[phase.phase_type] || phase.phase_type}`
      : PHASE_LABELS[phase.phase_type] || phase.phase_type;

    return `
      <div class="wf-phase-item ${statusClass}" data-index="${index}">
        <div class="wf-phase-dot"></div>
        <div class="wf-phase-label">${escapeHtml(label)}</div>
        ${phase.summary ? `<div class="wf-phase-summary">${escapeHtml(phase.summary)}</div>` : ''}
      </div>
    `;
  }

  // ── Events ────────────────────────────────────────────────────────────────

  _bindSetupEvents() {
    this._el.querySelector('.wf-add-role-btn')?.addEventListener('click', () => {
      const rows = this._el.querySelector('.wf-team-rows');
      const index = rows.querySelectorAll('.wf-role-row').length;
      const wrapper = document.createElement('div');
      wrapper.innerHTML = this._roleRow({ role: 'engineer', count: 1, instructions: '' }, index);
      rows.appendChild(wrapper.firstElementChild);
    });

    this._el.addEventListener('click', (e) => {
      if (e.target.classList.contains('wf-remove-role')) {
        e.target.closest('.wf-role-row')?.remove();
      }
    });

    this._el.querySelector('.wf-start-btn')?.addEventListener('click', async () => {
      const team = this._collectTeam();
      const config = this._collectConfig();
      if (!team.length) { toast('Add at least one team role', 'error'); return; }

      const btn = this._el.querySelector('.wf-start-btn');
      btn.disabled = true;
      btn.textContent = 'Starting...';
      try {
        await api.createWorkflow(this._project, team, config);
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
      if (!confirm('Cancel the entire workflow? Worktrees will be cleaned up.')) return;
      try {
        await api.deleteWorkflow(this._project);
        this._workflow = null;
        toast('Workflow cancelled', 'success');
        this._render();
      } catch (e) { toast(`Failed: ${e.message}`, 'error'); }
    });

    this._el.querySelector('.wf-new-btn')?.addEventListener('click', async () => {
      try {
        await api.deleteWorkflow(this._project);
        this._workflow = null;
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

  _collectConfig() {
    return {
      total_sprints: parseInt(this._el.querySelector('.wf-sprints-input')?.value, 10) || 4,
      sprint_duration_hint: this._el.querySelector('.wf-duration-input')?.value.trim() || '1 week',
      merge_strategy: this._el.querySelector('.wf-merge-select')?.value || 'squash',
      auto_continue: this._el.querySelector('.wf-auto-continue')?.checked ?? true,
    };
  }
}
