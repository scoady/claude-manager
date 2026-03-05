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
    this._collapsedGroups = new Set();
    // Per-task stream buffers: taskIndex → accumulated markdown text
    this._taskStreams = new Map();
    // Per-task tool calls: taskIndex → [{toolName, toolInput, toolOutput, duration, status}]
    this._taskTools = new Map();
    // taskIndex → sessionId mapping (set externally by FeedController)
    this._taskAgentMap = new Map();
    // Currently open modal task index (null if closed)
    this._modalTaskIndex = null;
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
   */
  appendAgentChunk(sessionId, chunk) {
    const taskIndex = this._resolveTaskIndex(sessionId);
    if (taskIndex == null) return;

    const prev = this._taskStreams.get(taskIndex) || '';
    this._taskStreams.set(taskIndex, prev + chunk);

    // If modal is open for this task, update it live
    if (this._modalTaskIndex === taskIndex) {
      this._updateModalOutput(taskIndex);
    }
  }

  /**
   * Handle a tool_start event — show milestone badge on the task row + track for modal.
   */
  addToolMilestone(sessionId, toolName, toolInput) {
    const taskIndex = this._resolveTaskIndex(sessionId);
    if (taskIndex == null) return;

    // Track tool call
    if (!this._taskTools.has(taskIndex)) this._taskTools.set(taskIndex, []);
    const tools = this._taskTools.get(taskIndex);
    tools.push({
      toolName: toolName || '',
      toolInput: toolInput || {},
      toolOutput: null,
      startTime: Date.now(),
      duration: null,
      status: 'running'
    });

    const row = this._el.querySelector(`.task-row[data-index="${taskIndex}"]`);
    if (!row) return;

    // Replace waiting indicator
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
    badge.textContent = this._toolLabel(toolName, toolInput);

    // Update modal tools tab if open
    if (this._modalTaskIndex === taskIndex) {
      this._updateModalTools(taskIndex);
    }
  }

  /**
   * Handle a tool_done event — update the last tool's output and status.
   */
  completeToolMilestone(sessionId, toolOutput) {
    const taskIndex = this._resolveTaskIndex(sessionId);
    if (taskIndex == null) return;

    const tools = this._taskTools.get(taskIndex);
    if (!tools || !tools.length) return;
    const last = tools[tools.length - 1];
    last.toolOutput = toolOutput;
    last.duration = Date.now() - last.startTime;
    last.status = 'ok';

    if (this._modalTaskIndex === taskIndex) {
      this._updateModalTools(taskIndex);
    }
  }

  /** Resolve sessionId → taskIndex via the agent map. */
  _resolveTaskIndex(sessionId) {
    for (const [idx, sid] of this._taskAgentMap) {
      if (sid === sessionId) return idx;
    }
    // Fallback: route to any in_progress task without a mapped agent
    const mappedIndices = new Set(this._taskAgentMap.values());
    const activeTask = this._tasks.find(t =>
      t.status === 'in_progress' && !mappedIndices.has(t.index)
    );
    if (activeTask) {
      this._taskAgentMap.set(activeTask.index, sessionId);
      return activeTask.index;
    }
    return null;
  }

  _toolLabel(toolName, input) {
    const name = (toolName || '').replace(/^mcp__\w+__/, '');
    if (name === 'Read' && input?.file_path) return `Read \u00b7 ${input.file_path.split('/').pop()}`;
    if (name === 'Edit' && input?.file_path) return `Edit \u00b7 ${input.file_path.split('/').pop()}`;
    if (name === 'Bash') return `Bash \u00b7 ${(input?.command || '').slice(0, 30)}`;
    if (name === 'Grep') return `Grep \u00b7 ${input?.pattern || ''}`;
    if (name === 'Glob') return `Glob \u00b7 ${input?.pattern || ''}`;
    return name;
  }

  _toolIcon(toolName) {
    const name = (toolName || '').replace(/^mcp__\w+__/, '');
    const map = { Read: 'R', Edit: 'E', Write: 'W', Bash: 'B', Grep: 'G', Glob: 'F', Agent: 'A', WebFetch: 'W', ToolSearch: 'S' };
    return map[name] || name.charAt(0).toUpperCase();
  }

  destroy() {
    this._closeModal();
  }

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

    // Compact inline status: waiting indicator or last tool badge
    let inlineStatus = '';
    if (groupStatus === 'active') {
      if (!hasStream && !hasMappedAgent) {
        inlineStatus = `<div class="task-waiting"><span class="task-waiting-dots"><span></span><span></span><span></span></span> Waiting for dispatch\u2026</div>`;
      } else if (!hasStream) {
        inlineStatus = `<div class="task-waiting"><span class="task-waiting-dots"><span></span><span></span><span></span></span> Agent starting\u2026</div>`;
      }
    }

    return `
      <div class="task-row" data-index="${t.index}">
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
        ${inlineStatus ? `<div style="padding: 2px 4px 6px 46px">${inlineStatus}</div>` : ''}
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

  // ── Modal ────────────────────────────────────────────────────────

  _openModal(taskIndex) {
    const task = this._tasks.find(t => t.index === taskIndex);
    if (!task) return;

    this._modalTaskIndex = taskIndex;
    const container = document.getElementById('task-detail-modal');
    const hasStream = this._taskStreams.has(taskIndex);
    const tools = this._taskTools.get(taskIndex) || [];
    const statusClass = task.status === 'done' ? 'done' : task.status === 'in_progress' ? 'active' : 'pending';
    const statusLabel = task.status === 'done' ? 'Done' : task.status === 'in_progress' ? 'Active' : 'Pending';

    container.innerHTML = `
      <div class="task-modal-overlay">
        <div class="task-modal">
          <div class="task-modal-header">
            <div class="task-modal-header-top">
              <div class="task-modal-title">${escapeHtml(task.text)}</div>
              <button class="task-modal-close">&times;</button>
            </div>
            <div class="task-modal-meta">
              <span class="task-modal-badge ${statusClass}">${statusLabel}</span>
              <span class="task-modal-meta-text">Task #${taskIndex}${tools.length ? ` \u00b7 <span>${tools.length} tool calls</span>` : ''}</span>
            </div>
          </div>
          <div class="task-modal-tabs">
            <button class="task-modal-tab active" data-tab="output">Output</button>
            <button class="task-modal-tab" data-tab="tools">Tools${tools.length ? ` <span style="color:var(--text-muted)">(${tools.length})</span>` : ''}</button>
          </div>
          <div class="task-modal-body" id="task-modal-body-output">
            ${this._renderModalOutput(taskIndex, hasStream)}
          </div>
          <div class="task-modal-body" id="task-modal-body-tools" style="display:none">
            ${this._renderModalTools(tools)}
          </div>
          ${task.status === 'in_progress' || task.status === 'done' ? `
          <div class="task-modal-inject">
            <input type="text" placeholder="Send a follow-up message to this agent..." />
            <button>Send</button>
          </div>` : ''}
        </div>
      </div>
    `;

    // Bind events
    const overlay = container.querySelector('.task-modal-overlay');
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) this._closeModal();
    });
    container.querySelector('.task-modal-close').addEventListener('click', () => this._closeModal());

    // Tab switching
    container.querySelectorAll('.task-modal-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        container.querySelectorAll('.task-modal-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        const isOutput = tab.dataset.tab === 'output';
        container.querySelector('#task-modal-body-output').style.display = isOutput ? '' : 'none';
        container.querySelector('#task-modal-body-tools').style.display = isOutput ? 'none' : '';
      });
    });

    // Tool entry expand/collapse
    this._bindModalToolEvents(container);

    // Inject bar
    const injectInput = container.querySelector('.task-modal-inject input');
    const injectBtn = container.querySelector('.task-modal-inject button');
    if (injectInput && injectBtn) {
      const sendInject = () => {
        const msg = injectInput.value.trim();
        if (!msg) return;
        const sid = this._taskAgentMap.get(taskIndex);
        if (sid) {
          api.injectMessage(sid, msg).then(() => {
            toast('Message sent', 'success', 2000);
            injectInput.value = '';
          }).catch(e => toast(`Failed: ${e.message}`, 'error'));
        } else {
          toast('No agent session for this task', 'error');
        }
      };
      injectBtn.addEventListener('click', sendInject);
      injectInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendInject(); }
      });
    }

    // Escape to close
    this._modalEscHandler = (e) => {
      if (e.key === 'Escape') this._closeModal();
    };
    document.addEventListener('keydown', this._modalEscHandler);
  }

  _closeModal() {
    this._modalTaskIndex = null;
    const container = document.getElementById('task-detail-modal');
    const overlay = container.querySelector('.task-modal-overlay');
    if (overlay) {
      overlay.classList.add('closing');
      overlay.addEventListener('animationend', () => {
        container.innerHTML = '';
      }, { once: true });
    } else {
      container.innerHTML = '';
    }
    if (this._modalEscHandler) {
      document.removeEventListener('keydown', this._modalEscHandler);
      this._modalEscHandler = null;
    }
  }

  _renderModalOutput(taskIndex, hasStream) {
    if (!hasStream) {
      const task = this._tasks.find(t => t.index === taskIndex);
      if (task && task.status === 'in_progress') {
        return `<div class="task-modal-waiting">
          <div class="task-modal-waiting-dots"><span></span><span></span><span></span></div>
          <div class="task-modal-waiting-text">Agent working\u2026</div>
        </div>`;
      }
      return `<div class="task-modal-output"><p style="color:var(--text-muted)">No output yet.</p></div>`;
    }
    return `<div class="task-modal-output">${renderMarkdown(this._taskStreams.get(taskIndex))}</div>`;
  }

  _renderModalTools(tools) {
    if (!tools.length) {
      return '<div style="padding:32px;text-align:center;color:var(--text-muted);font-size:13px">No tool calls recorded.</div>';
    }
    return `<div class="task-modal-tool-timeline">${tools.map((t, i) => {
      const name = (t.toolName || '').replace(/^mcp__\w+__/, '');
      const arg = this._toolArg(name, t.toolInput);
      const dur = t.duration != null ? `${(t.duration / 1000).toFixed(1)}s` : '\u2026';
      const statusClass = t.status === 'ok' ? 'ok' : t.status === 'running' ? '' : 'err';
      const statusLabel = t.status === 'ok' ? 'OK' : t.status === 'running' ? '\u2026' : 'ERR';
      return `<div class="task-modal-tool-entry" data-tool-index="${i}">
        <div class="task-modal-tool-icon">${this._toolIcon(t.toolName)}</div>
        <div class="task-modal-tool-info">
          <div class="task-modal-tool-name">${escapeHtml(name)}${arg ? ` <span class="tool-arg">${escapeHtml(arg)}</span>` : ''}</div>
          <div class="task-modal-tool-duration">${dur}</div>
        </div>
        ${statusClass ? `<div class="task-modal-tool-status ${statusClass}">${statusLabel}</div>` : ''}
      </div>`;
    }).join('')}</div>`;
  }

  _toolArg(name, input) {
    if (!input) return '';
    if (name === 'Read' || name === 'Edit' || name === 'Write') return input.file_path || '';
    if (name === 'Bash') return (input.command || '').slice(0, 60);
    if (name === 'Grep') return input.pattern || '';
    if (name === 'Glob') return input.pattern || '';
    return '';
  }

  _updateModalOutput(taskIndex) {
    const container = document.getElementById('task-detail-modal');
    const outputBody = container?.querySelector('#task-modal-body-output');
    if (!outputBody) return;
    outputBody.innerHTML = this._renderModalOutput(taskIndex, true);
    outputBody.scrollTop = outputBody.scrollHeight;
  }

  _updateModalTools(taskIndex) {
    const container = document.getElementById('task-detail-modal');
    const toolsBody = container?.querySelector('#task-modal-body-tools');
    if (!toolsBody) return;
    const tools = this._taskTools.get(taskIndex) || [];
    toolsBody.innerHTML = this._renderModalTools(tools);
    // Update tab count
    const toolsTab = container.querySelector('.task-modal-tab[data-tab="tools"]');
    if (toolsTab) toolsTab.innerHTML = `Tools <span style="color:var(--text-muted)">(${tools.length})</span>`;
    this._bindModalToolEvents(container);
    toolsBody.scrollTop = toolsBody.scrollHeight;
  }

  _bindModalToolEvents(container) {
    container.querySelectorAll('.task-modal-tool-entry').forEach(entry => {
      entry.addEventListener('click', () => {
        const idx = parseInt(entry.dataset.toolIndex, 10);
        const taskTools = this._taskTools.get(this._modalTaskIndex) || [];
        const tool = taskTools[idx];
        if (!tool) return;

        // Toggle: remove existing detail
        const existing = entry.nextElementSibling;
        if (existing && existing.classList.contains('task-modal-tool-detail')) {
          existing.remove();
          return;
        }

        // Remove any other open detail
        container.querySelectorAll('.task-modal-tool-detail').forEach(d => d.remove());

        // Create detail element
        const detail = document.createElement('div');
        detail.className = 'task-modal-tool-detail';
        let html = '';
        if (tool.toolInput && Object.keys(tool.toolInput).length) {
          const inputStr = typeof tool.toolInput === 'string' ? tool.toolInput : JSON.stringify(tool.toolInput, null, 2);
          html += `<div class="task-modal-tool-detail-label">Input</div>
            <div class="task-modal-tool-detail-content">${escapeHtml(inputStr)}</div>`;
        }
        if (tool.toolOutput) {
          const outputStr = typeof tool.toolOutput === 'string' ? tool.toolOutput : JSON.stringify(tool.toolOutput, null, 2);
          html += `<div class="task-modal-tool-detail-label">Output</div>
            <div class="task-modal-tool-detail-content">${escapeHtml(outputStr.slice(0, 2000))}</div>`;
        }
        if (!html) html = '<div class="task-modal-tool-detail-label">No details available</div>';
        detail.innerHTML = html;
        entry.after(detail);
      });
    });
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
        this._openModal(idx);
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
      toast('Task added \u2014 planner agent started', 'success', 3000);
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
