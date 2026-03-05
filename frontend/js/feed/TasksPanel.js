/** TasksPanel — equalizer-style task progress visualization */
import { escapeHtml, toast } from '../utils.js';
import { renderMarkdown } from './MarkdownRenderer.js';
import { api } from '../api.js';

export class TasksPanel {
  constructor(projectName) {
    this._project = projectName;
    this._tasks = [];
    this._el = document.createElement('div');
    this._el.className = 'tasks-panel';
    this._expandedIndices = new Set();
    this._collapsedGroups = new Set();
    this._lastStreamText = '';
    this._render();
  }

  get el() { return this._el; }

  async load() {
    try {
      this._tasks = await api.getTasks(this._project);
      this._renderContent();
    } catch (e) {
      this._el.querySelector('.tasks-body').innerHTML =
        '<div class="tasks-empty">Failed to load tasks</div>';
    }
  }

  updateTasks(tasks) {
    this._tasks = tasks;
    this._renderContent();
  }

  updateAgentStream(streamText) {
    this._lastStreamText = streamText;
    this._el.querySelectorAll('.task-detail:not(.hidden) .task-detail-content').forEach(el => {
      el.innerHTML = renderMarkdown(streamText);
      el.scrollTop = el.scrollHeight;
    });
  }

  destroy() {}

  // ── Rendering ──────────────────────────────────────────────────

  _render() {
    this._el.innerHTML = `
      <div class="tasks-add-form">
        <div class="tasks-add-row">
          <input type="text" class="tasks-add-input" placeholder="Add a task..." />
          <button class="tasks-add-btn" disabled title="Add task">+</button>
          <button class="tasks-plan-btn" disabled title="Add task &amp; run planner">Plan</button>
        </div>
      </div>
      <div class="tasks-body"></div>
    `;
    this._bindAddForm();
  }

  _renderContent() {
    const body = this._el.querySelector('.tasks-body');

    if (!this._tasks.length) {
      body.innerHTML = '<div class="tasks-empty">No tasks yet. Add one above.</div>';
      return;
    }

    const done = this._tasks.filter(t => t.status === 'done');
    const active = this._tasks.filter(t => t.status === 'in_progress');
    const pending = this._tasks.filter(t => t.status === 'pending');
    const total = this._tasks.length;

    // Build equalizer HTML
    const eqBars = this._tasks.map((t, i) => {
      if (t.status === 'done') {
        const h = 28 + this._seededRandom(i) * 32;
        return `<div class="eq-bar done" style="height:${h}px" data-index="${t.index}" title="${escapeHtml(t.text)}"></div>`;
      } else if (t.status === 'in_progress') {
        const low = 10 + this._seededRandom(i) * 16;
        const high = 40 + this._seededRandom(i + 100) * 20;
        const dur = (0.5 + this._seededRandom(i + 200) * 0.6).toFixed(2);
        return `<div class="eq-bar active" style="--eq-low:${low}px;--eq-high:${high}px;--bounce-dur:${dur}s;height:${low}px" data-index="${t.index}" title="${escapeHtml(t.text)}"></div>`;
      } else {
        const h = 6 + this._seededRandom(i) * 10;
        return `<div class="eq-bar queued" style="height:${h}px" data-index="${t.index}" title="${escapeHtml(t.text)}"></div>`;
      }
    }).join('');

    body.innerHTML = `
      <div class="tasks-eq-header">
        <div class="tasks-eq-title-row">
          <span class="tasks-eq-title">Task Progress</span>
          <div class="tasks-eq-stats">
            <span class="tasks-eq-stat"><span class="stat-dot done"></span> ${done.length} done</span>
            <span class="tasks-eq-stat"><span class="stat-dot active"></span> ${active.length} active</span>
            <span class="tasks-eq-stat"><span class="stat-dot queued"></span> ${pending.length} queued</span>
          </div>
        </div>
        <div class="eq-container">${eqBars}</div>
      </div>
      <div class="tasks-groups">
        ${this._renderGroup('active', 'In Progress', active)}
        ${this._renderGroup('queued', 'Pending', pending)}
        ${this._renderGroup('done', 'Completed', done)}
      </div>
    `;

    this._bindGroupEvents(body);
    this._bindRowEvents(body);
  }

  _renderGroup(status, label, tasks) {
    if (!tasks.length) return '';
    const collapsed = this._collapsedGroups.has(status);
    return `
      <div class="task-group${collapsed ? ' collapsed' : ''}" data-group="${status}">
        <div class="group-header">
          <svg class="group-chevron" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M5 3l5 5-5 5"/>
          </svg>
          <span class="group-badge ${status}">${status === 'active' ? 'ACTIVE' : status === 'done' ? 'DONE' : 'QUEUED'}</span>
          <span class="group-label">${label}</span>
          <span class="group-count">${tasks.length} task${tasks.length !== 1 ? 's' : ''}</span>
        </div>
        <div class="group-rows">
          ${tasks.map(t => this._renderTaskRow(t, status)).join('')}
        </div>
      </div>
    `;
  }

  _renderTaskRow(t, groupStatus) {
    const expanded = this._expandedIndices.has(t.index);
    let indicator;
    if (groupStatus === 'active') {
      indicator = `<span class="task-status-indicator">
        <span class="mini-eq-bar"></span>
        <span class="mini-eq-bar"></span>
        <span class="mini-eq-bar"></span>
      </span>`;
    } else {
      indicator = `<span class="task-status-indicator"><span class="status-dot ${groupStatus}"></span></span>`;
    }

    return `
      <div class="task-row${expanded ? ' expanded' : ''}" data-index="${t.index}">
        <div class="task-row-main">
          ${indicator}
          <span class="task-name${groupStatus === 'queued' ? ' muted' : ''}">${escapeHtml(t.text)}</span>
          <div class="task-actions">
            ${groupStatus === 'queued' ? `<button class="task-start-btn" data-index="${t.index}" title="Start this task">Start</button>` : ''}
            <button class="task-cycle-btn" data-index="${t.index}" title="Cycle status">
              ${this._statusIcon(t.status)}
            </button>
            <button class="task-delete-btn" data-index="${t.index}" title="Remove task">
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
              </svg>
            </button>
          </div>
        </div>
        <div class="task-detail${expanded ? '' : ' hidden'}">
          <div class="task-detail-content">${expanded && this._lastStreamText ? renderMarkdown(this._lastStreamText) : '<span class="text-muted">Live agent output will appear here...</span>'}</div>
        </div>
      </div>
    `;
  }

  _statusIcon(status) {
    if (status === 'done') return `<svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <rect x="1" y="1" width="12" height="12" rx="3" fill="var(--accent-green)" opacity="0.15" stroke="var(--accent-green)" stroke-width="1.3"/>
      <path d="M4 7l2 2 4-4" stroke="var(--accent-green)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
    if (status === 'in_progress') return `<svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <rect x="1" y="1" width="12" height="12" rx="3" fill="var(--accent-amber)" opacity="0.15" stroke="var(--accent-amber)" stroke-width="1.3"/>
      <path d="M5 7h4" stroke="var(--accent-amber)" stroke-width="1.5" stroke-linecap="round"/>
    </svg>`;
    return `<svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <rect x="1" y="1" width="12" height="12" rx="3" stroke="var(--text-muted)" stroke-width="1.3"/>
    </svg>`;
  }

  /** Deterministic pseudo-random based on index (stable across re-renders). */
  _seededRandom(i) {
    const x = Math.sin(i * 9301 + 49297) * 49297;
    return x - Math.floor(x);
  }

  // ── Event binding ─────────────────────────────────────────────

  _bindAddForm() {
    const input = this._el.querySelector('.tasks-add-input');
    const addBtn = this._el.querySelector('.tasks-add-btn');
    const planBtn = this._el.querySelector('.tasks-plan-btn');

    input.addEventListener('input', () => {
      const hasText = input.value.trim().length > 0;
      addBtn.disabled = !hasText;
      planBtn.disabled = !hasText;
    });

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this._addTask(input);
      }
    });

    addBtn.addEventListener('click', () => this._addTask(input));
    planBtn.addEventListener('click', () => this._addAndPlan(input));
  }

  _bindGroupEvents(root) {
    root.querySelectorAll('.group-header').forEach(header => {
      header.addEventListener('click', () => {
        const group = header.closest('.task-group');
        const groupName = group.dataset.group;
        group.classList.toggle('collapsed');
        if (group.classList.contains('collapsed')) {
          this._collapsedGroups.add(groupName);
        } else {
          this._collapsedGroups.delete(groupName);
        }
      });
    });
  }

  _bindRowEvents(root) {
    // Expand/collapse on row click
    root.querySelectorAll('.task-row-main').forEach(main => {
      main.addEventListener('click', (e) => {
        if (e.target.closest('.task-actions')) return;
        const row = main.closest('.task-row');
        const idx = parseInt(row.dataset.index, 10);
        const detail = row.querySelector('.task-detail');
        const isExpanded = this._expandedIndices.has(idx);

        if (isExpanded) {
          this._expandedIndices.delete(idx);
          row.classList.remove('expanded');
          detail.classList.add('hidden');
        } else {
          this._expandedIndices.add(idx);
          row.classList.add('expanded');
          detail.classList.remove('hidden');
          const content = detail.querySelector('.task-detail-content');
          if (this._lastStreamText) {
            content.innerHTML = renderMarkdown(this._lastStreamText);
          }
        }
      });
    });

    root.querySelectorAll('.task-cycle-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const idx = parseInt(btn.dataset.index, 10);
        const task = this._tasks.find(t => t.index === idx);
        if (!task) return;
        const next = { pending: 'in_progress', in_progress: 'done', done: 'pending' };
        this._updateStatus(idx, next[task.status]);
      });
    });

    root.querySelectorAll('.task-start-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._startTask(parseInt(btn.dataset.index, 10));
      });
    });

    root.querySelectorAll('.task-delete-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._deleteTask(parseInt(btn.dataset.index, 10));
      });
    });
  }

  // ── Actions ────────────────────────────────────────────────────

  async _addTask(input) {
    const text = input.value.trim();
    if (!text) return;
    try {
      this._tasks = await api.addTask(this._project, text);
      input.value = '';
      input.dispatchEvent(new Event('input'));
      this._renderContent();
      toast('Task added', 'success', 2000);
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    }
  }

  async _addAndPlan(input) {
    const text = input.value.trim();
    if (!text) return;
    try {
      await api.planTask(this._project, text);
      input.value = '';
      input.dispatchEvent(new Event('input'));
      await this.load();
      toast('Task added — planner agent started', 'success', 3000);
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    }
  }

  async _updateStatus(taskIndex, newStatus) {
    try {
      this._tasks = await api.updateTask(this._project, taskIndex, newStatus);
      this._renderContent();
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    }
  }

  async _startTask(taskIndex) {
    try {
      const result = await api.startTask(this._project, taskIndex);
      toast(`Agent started: ${result.task}`, 'success', 3000);
      this._tasks = await api.getTasks(this._project);
      this._renderContent();
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    }
  }

  async _deleteTask(taskIndex) {
    try {
      this._tasks = await api.deleteTask(this._project, taskIndex);
      this._renderContent();
      toast('Task removed', 'success', 2000);
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    }
  }
}
