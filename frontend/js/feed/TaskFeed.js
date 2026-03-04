/** TaskFeed — task-centric feed with expandable rows and embedded agent sections. */
import { escapeHtml, toast } from '../utils.js';
import { api } from '../api.js';

export class TaskFeed {
  /**
   * @param {string} projectName
   * @param {{ onStartTask: (idx: number) => void, onAddTask: (text: string) => void }} callbacks
   */
  constructor(projectName, { onStartTask, onAddTask } = {}) {
    this._project = projectName;
    this._tasks = [];
    this._onStartTask = onStartTask;
    this._onAddTask = onAddTask;
    this._expandedIndices = new Set();
    this._agentSections = new Map(); // taskIndex → AgentSection
    this._agentPhases = new Map();   // taskIndex → phase string
    this._agentTurns = new Map();    // taskIndex → turn count

    this._el = document.createElement('div');
    this._el.className = 'task-feed';
    this._render();
  }

  get el() { return this._el; }

  /** Set/update the full task list and re-render. */
  setTasks(tasks) {
    this._tasks = tasks;
    this._renderRows();
  }

  /** Embed an AgentSection inside a task row. */
  attachAgent(taskIndex, agentSection) {
    this._agentSections.set(taskIndex, agentSection);
    const container = this._getRowBody(taskIndex);
    if (container) {
      container.innerHTML = '';
      container.appendChild(agentSection.el);
    }
    this._updateRowBadge(taskIndex);
  }

  /** Remove agent section from a task row. */
  detachAgent(taskIndex) {
    this._agentSections.delete(taskIndex);
    this._agentPhases.delete(taskIndex);
    this._agentTurns.delete(taskIndex);
    const container = this._getRowBody(taskIndex);
    if (container) container.innerHTML = '';
    this._updateRowBadge(taskIndex);
  }

  /** Return the body DOM element for a task row. */
  getAgentContainer(taskIndex) {
    return this._getRowBody(taskIndex);
  }

  /** Update the phase badge on a task row. */
  setAgentPhase(taskIndex, phase) {
    this._agentPhases.set(taskIndex, phase);
    this._updateRowBadge(taskIndex);
  }

  /** Update the turn count on a task row. */
  setAgentTurns(taskIndex, turns) {
    this._agentTurns.set(taskIndex, turns);
    this._updateRowBadge(taskIndex);
  }

  // ── Internal ──────────────────────────────────────────────────────────────

  _render() {
    this._el.innerHTML = `
      <div class="tf-add-form">
        <input type="text" class="tf-add-input" placeholder="Add a task..." />
        <button class="tf-add-btn" disabled>+</button>
      </div>
      <div class="tf-rows"></div>
    `;
    this._bindAddForm();
  }

  _renderRows() {
    const rowsEl = this._el.querySelector('.tf-rows');
    if (!rowsEl) return;

    if (!this._tasks.length) {
      rowsEl.innerHTML = '<div class="tf-empty">No tasks yet</div>';
      return;
    }

    rowsEl.innerHTML = this._tasks.map(t => {
      const expanded = this._expandedIndices.has(t.index);
      const hasAgent = this._agentSections.has(t.index);
      const phase = this._agentPhases.get(t.index);
      const turns = this._agentTurns.get(t.index);
      const indent = (t.indent || 0) * 16;

      return `
      <div class="tf-row tf-${t.status}${expanded ? ' expanded' : ''}" data-index="${t.index}" style="${indent ? `padding-left: ${indent}px` : ''}">
        <div class="tf-row-header">
          <span class="tf-icon">${this._statusIcon(t.status)}</span>
          <span class="tf-text">${escapeHtml(t.text)}</span>
          ${hasAgent ? `<span class="tf-agent-badge">agent${turns ? ` · ${turns}t` : ''}</span>` : ''}
          ${hasAgent && phase ? `<span class="tf-phase-badge phase-${phase}">${phase}</span>` : ''}
          ${!hasAgent && t.status === 'pending' ? `<button class="tf-start-btn" data-index="${t.index}">Start</button>` : ''}
          ${hasAgent || t.status !== 'pending' ? `<svg class="tf-chevron" width="12" height="12" viewBox="0 0 12 12" fill="none">
            <path d="M3 4.5l3 3 3-3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>` : ''}
        </div>
        <div class="tf-row-body" data-body-index="${t.index}"></div>
      </div>`;
    }).join('');

    // Re-attach agent sections to their rows
    for (const [idx, section] of this._agentSections) {
      const body = rowsEl.querySelector(`[data-body-index="${idx}"]`);
      if (body && section.el) {
        body.appendChild(section.el);
      }
    }

    this._bindRowEvents(rowsEl);
  }

  _statusIcon(status) {
    if (status === 'done') {
      return `<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <circle cx="8" cy="8" r="6" fill="var(--accent-green)" opacity="0.15"/>
        <circle cx="8" cy="8" r="6" stroke="var(--accent-green)" stroke-width="1.3"/>
        <path d="M5.5 8l2 2 3.5-3.5" stroke="var(--accent-green)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>`;
    }
    if (status === 'in_progress') {
      return `<svg width="16" height="16" viewBox="0 0 16 16" class="tf-spinner">
        <circle cx="8" cy="8" r="6" stroke="var(--accent-amber)" stroke-width="1.3" stroke-dasharray="4 3" fill="none"/>
      </svg>`;
    }
    // pending
    return `<svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6" stroke="var(--text-muted)" stroke-width="1.3"/>
    </svg>`;
  }

  _getRowBody(taskIndex) {
    return this._el.querySelector(`[data-body-index="${taskIndex}"]`);
  }

  _updateRowBadge(taskIndex) {
    const row = this._el.querySelector(`.tf-row[data-index="${taskIndex}"]`);
    if (!row) return;
    // Re-render will handle it; for live updates just update badge text
    const badge = row.querySelector('.tf-agent-badge');
    const turns = this._agentTurns.get(taskIndex);
    if (badge && turns) badge.textContent = `agent · ${turns}t`;
  }

  // ── Events ──────────────────────────────────────────────────────────────

  _bindAddForm() {
    const input = this._el.querySelector('.tf-add-input');
    const addBtn = this._el.querySelector('.tf-add-btn');
    if (!input || !addBtn) return;

    input.addEventListener('input', () => {
      addBtn.disabled = !input.value.trim();
    });

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this._addTask(input);
      }
    });

    addBtn.addEventListener('click', () => this._addTask(input));
  }

  _bindRowEvents(rowsEl) {
    // Row header click → expand/collapse
    rowsEl.querySelectorAll('.tf-row-header').forEach(header => {
      header.addEventListener('click', (e) => {
        if (e.target.closest('.tf-start-btn')) return;
        const row = header.closest('.tf-row');
        const idx = parseInt(row.dataset.index, 10);
        const isExpanded = this._expandedIndices.has(idx);

        if (isExpanded) {
          this._expandedIndices.delete(idx);
          row.classList.remove('expanded');
        } else {
          this._expandedIndices.add(idx);
          row.classList.add('expanded');
        }
      });
    });

    // Start buttons
    rowsEl.querySelectorAll('.tf-start-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const idx = parseInt(btn.dataset.index, 10);
        this._startTask(idx);
      });
    });
  }

  async _addTask(input) {
    const text = input.value.trim();
    if (!text) return;
    try {
      this._tasks = await api.addTask(this._project, text);
      input.value = '';
      input.dispatchEvent(new Event('input'));
      this._renderRows();
      toast('Task added', 'success', 2000);
      if (this._onAddTask) this._onAddTask(text);
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    }
  }

  async _startTask(taskIndex) {
    try {
      const result = await api.startTask(this._project, taskIndex);
      toast(`Agent started: ${result.task}`, 'success', 3000);
      if (this._onStartTask) this._onStartTask(taskIndex);
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    }
  }
}
