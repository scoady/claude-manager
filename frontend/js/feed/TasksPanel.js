/** TasksPanel — renders and manages the tasks list from TASKS.md */
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
    this._lastStreamText = '';
    this._render();
  }

  get el() { return this._el; }

  /** Load tasks from backend and render. */
  async load() {
    try {
      this._tasks = await api.getTasks(this._project);
      this._renderList();
    } catch (e) {
      this._el.querySelector('.tasks-list').innerHTML =
        '<div class="tasks-empty">Failed to load tasks</div>';
    }
  }

  /** Update tasks from a WS event payload. */
  updateTasks(tasks) {
    this._tasks = tasks;
    this._renderList();
  }

  /** Receive live agent stream text and push to expanded detail sections. */
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
      <div class="tasks-summary"></div>
      <div class="tasks-list"></div>
    `;
    this._bindAddForm();
  }

  _renderList() {
    const listEl = this._el.querySelector('.tasks-list');
    const summaryEl = this._el.querySelector('.tasks-summary');

    if (!this._tasks.length) {
      listEl.innerHTML = '<div class="tasks-empty">No tasks yet. Add one above.</div>';
      summaryEl.innerHTML = '';
      return;
    }

    const total = this._tasks.length;
    const done = this._tasks.filter(t => t.status === 'done').length;
    const inProgress = this._tasks.filter(t => t.status === 'in_progress').length;
    const pending = total - done - inProgress;
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;

    summaryEl.innerHTML = `
      <div class="tasks-progress-bar">
        <div class="tasks-progress-fill" style="width: ${pct}%"></div>
      </div>
      <div class="tasks-progress-label">
        <span>${done}/${total} done</span>
        ${inProgress ? `<span class="tasks-wip-count">${inProgress} in progress</span>` : ''}
        ${pending ? `<span class="tasks-pending-count">${pending} pending</span>` : ''}
      </div>
    `;

    listEl.innerHTML = this._tasks.map(t => {
      const expanded = this._expandedIndices.has(t.index);
      return `
      <div class="task-row task-${t.status}${expanded ? ' expanded' : ''}" data-index="${t.index}" style="padding-left: ${12 + t.indent * 20}px">
        <div class="task-row-header">
          <button class="task-checkbox" data-index="${t.index}" title="Toggle status">
            ${this._checkboxIcon(t.status)}
          </button>
          <span class="task-text">${escapeHtml(t.text)}</span>
          <div class="task-actions">
            ${t.status === 'pending' ? `
              <button class="task-start-btn" data-index="${t.index}" title="Start this task">Start</button>
            ` : ''}
            <button class="task-delete-btn" data-index="${t.index}" title="Remove task">
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
              </svg>
            </button>
          </div>
        </div>
        <div class="task-detail${expanded ? '' : ' hidden'}">
          <div class="task-detail-content agent-status-card">${expanded && this._lastStreamText ? renderMarkdown(this._lastStreamText) : '<span class="text-muted">Live agent output will appear here…</span>'}</div>
        </div>
      </div>`;
    }).join('');

    this._bindListEvents(listEl);
  }

  _checkboxIcon(status) {
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

  _bindListEvents(listEl) {
    // Expand/collapse on row header click
    listEl.querySelectorAll('.task-row-header').forEach(header => {
      header.addEventListener('click', (e) => {
        // Don't toggle if clicking a button inside the header
        if (e.target.closest('.task-checkbox') || e.target.closest('.task-actions')) return;
        const row = header.closest('.task-row');
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
          // Populate with latest stream text
          const content = detail.querySelector('.task-detail-content');
          if (this._lastStreamText) {
            content.innerHTML = renderMarkdown(this._lastStreamText);
          }
        }
      });
    });

    listEl.querySelectorAll('.task-checkbox').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const idx = parseInt(btn.dataset.index, 10);
        const task = this._tasks.find(t => t.index === idx);
        if (!task) return;
        const next = { pending: 'in_progress', in_progress: 'done', done: 'pending' };
        this._updateStatus(idx, next[task.status]);
      });
    });

    listEl.querySelectorAll('.task-start-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._startTask(parseInt(btn.dataset.index, 10));
      });
    });

    listEl.querySelectorAll('.task-delete-btn').forEach(btn => {
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
      this._renderList();
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
      // Reload to show the newly added task
      await this.load();
      toast('Task added — planner agent started', 'success', 3000);
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    }
  }

  async _updateStatus(taskIndex, newStatus) {
    try {
      this._tasks = await api.updateTask(this._project, taskIndex, newStatus);
      this._renderList();
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    }
  }

  async _startTask(taskIndex) {
    try {
      const result = await api.startTask(this._project, taskIndex);
      toast(`Agent started: ${result.task}`, 'success', 3000);
      this._tasks = await api.getTasks(this._project);
      this._renderList();
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    }
  }

  async _deleteTask(taskIndex) {
    try {
      this._tasks = await api.deleteTask(this._project, taskIndex);
      this._renderList();
      toast('Task removed', 'success', 2000);
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    }
  }
}
