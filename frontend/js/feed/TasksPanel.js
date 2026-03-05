/** TasksPanel — equalizer-style task progress with per-task agent output */
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
    // Per-task stream buffers: taskIndex → accumulated markdown text
    this._taskStreams = new Map();
    // taskIndex → sessionId mapping (set externally by FeedController)
    this._taskAgentMap = new Map();
    this._render();
  }

  get el() { return this._el; }

  /** Set the task→agent mapping so streams route to the right row. */
  setTaskAgentMap(map) {
    this._taskAgentMap = map;
  }

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

  /**
   * Append a stream chunk for a specific agent session.
   * Routes to the correct task row via taskAgentMap.
   * Falls back to any in_progress task if no explicit mapping.
   */
  appendAgentChunk(sessionId, chunk) {
    // Find which task this agent is working on
    let taskIndex = null;
    for (const [idx, sid] of this._taskAgentMap) {
      if (sid === sessionId) {
        taskIndex = idx;
        break;
      }
    }
    // Fallback: if no explicit mapping, route to any in_progress task
    // that doesn't already have a mapped agent
    if (taskIndex == null) {
      const mappedIndices = new Set(this._taskAgentMap.values());
      const activeTask = this._tasks.find(t =>
        t.status === 'in_progress' && !mappedIndices.has(t.index)
      );
      if (activeTask) {
        taskIndex = activeTask.index;
        // Cache this mapping for future chunks
        this._taskAgentMap.set(taskIndex, sessionId);
      }
    }
    if (taskIndex == null) return;

    const prev = this._taskStreams.get(taskIndex) || '';
    this._taskStreams.set(taskIndex, prev + chunk);

    // If this task row is expanded, update the detail content live
    const row = this._el.querySelector(`.task-row[data-index="${taskIndex}"]`);
    if (row && this._expandedIndices.has(taskIndex)) {
      const content = row.querySelector('.task-detail-content');
      if (content) {
        content.innerHTML = renderMarkdown(this._taskStreams.get(taskIndex));
        content.scrollTop = content.scrollHeight;
      }
    }
  }

  /**
   * Handle a tool_start event — show milestone on the task row.
   */
  addToolMilestone(sessionId, toolName, toolInput) {
    let taskIndex = null;
    for (const [idx, sid] of this._taskAgentMap) {
      if (sid === sessionId) { taskIndex = idx; break; }
    }
    if (taskIndex == null) {
      const mappedIndices = new Set(this._taskAgentMap.values());
      const activeTask = this._tasks.find(t =>
        t.status === 'in_progress' && !mappedIndices.has(t.index)
      );
      if (activeTask) {
        taskIndex = activeTask.index;
        this._taskAgentMap.set(taskIndex, sessionId);
      }
    }
    if (taskIndex == null) return;

    const row = this._el.querySelector(`.task-row[data-index="${taskIndex}"]`);
    if (!row) return;

    // Replace waiting indicator once agent starts working
    const waiting = row.querySelector('.task-waiting');
    if (waiting) {
      waiting.innerHTML = '<span class="task-waiting-dots"><span></span><span></span><span></span></span> Agent working\u2026';
    }

    let badge = row.querySelector('.task-tool-badge');
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'task-tool-badge';
      const main = row.querySelector('.task-row-main');
      main?.insertBefore(badge, main.querySelector('.task-actions'));
    }
    const label = this._toolLabel(toolName, toolInput);
    badge.textContent = label;
  }

  _toolLabel(toolName, input) {
    const name = (toolName || '').replace(/^mcp__\w+__/, '');
    if (name === 'Read' && input?.file_path) {
      return `Read · ${input.file_path.split('/').pop()}`;
    }
    if (name === 'Edit' && input?.file_path) {
      return `Edit · ${input.file_path.split('/').pop()}`;
    }
    if (name === 'Bash') {
      const cmd = (input?.command || '').slice(0, 30);
      return `Bash · ${cmd}`;
    }
    if (name === 'Grep') return `Grep · ${input?.pattern || ''}`;
    if (name === 'Glob') return `Glob · ${input?.pattern || ''}`;
    return name;
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
    const hasStream = this._taskStreams.has(t.index);
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

    const hasMappedAgent = this._taskAgentMap.has(t.index);
    let streamContent;
    if (hasStream) {
      streamContent = renderMarkdown(this._taskStreams.get(t.index));
    } else if (groupStatus === 'active') {
      streamContent = hasMappedAgent
        ? '<div class="task-waiting"><span class="task-waiting-dots"><span></span><span></span><span></span></span> Agent starting\u2026</div>'
        : '<div class="task-waiting"><span class="task-waiting-dots"><span></span><span></span><span></span></span> Waiting for controller to dispatch\u2026</div>';
    } else {
      streamContent = '<span class="text-muted">Live agent output will appear here...</span>';
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
          <div class="task-detail-content">${streamContent}</div>
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
          if (this._taskStreams.has(idx)) {
            content.innerHTML = renderMarkdown(this._taskStreams.get(idx));
            content.scrollTop = content.scrollHeight;
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
