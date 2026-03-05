/** OrchestratorBanner — standalone orchestrator root node card (org chart tree root). */
import { escapeHtml } from '../utils.js';
import { renderMarkdown } from './MarkdownRenderer.js';

export class OrchestratorBanner {
  constructor() {
    this._el = document.createElement('div');
    this._el.className = 'orch-node';
    this._controllerSessionId = null;
    this._phase = null;
    this._summaryText = '';
    this._tasks = [];
    this._taskAgentMap = null; // Map<taskIndex, sessionId> — set by FeedController
    this._activeWork = new Map(); // tool_use_id → { description, color }
    this._render();
  }

  get el() { return this._el; }
  get controllerSessionId() { return this._controllerSessionId; }

  /** Initialize with project and task data. */
  setProject(project, tasks = []) {
    this._tasks = tasks;
    this._updateProgress();
  }

  /** Link to the controller agent session. */
  setControllerSession(sessionId) {
    this._controllerSessionId = sessionId;
    this._setPhaseDisplay('active');
  }

  /** Append streaming text from controller. */
  appendChunk(text) {
    if (!text) return;
    this._summaryText = text;
    const summaryEl = this._el.querySelector('.orch-summary');
    if (summaryEl) {
      summaryEl.innerHTML = renderMarkdown(text);
    }
  }

  /** Update progress bar from a new tasks list. */
  updateProgress(tasks) {
    this._tasks = tasks;
    this._updateProgress();
  }

  /** Update the alive indicator phase. */
  setPhase(phase) {
    this._phase = phase;
    const dot = this._el.querySelector('.orch-dot');
    const phaseEl = this._el.querySelector('.orch-phase');
    if (!dot || !phaseEl) return;

    const isWorking = !['idle', 'cancelled', 'error'].includes(phase);
    dot.classList.toggle('orch-alive', isWorking || phase === 'idle');
    dot.classList.toggle('orch-dead', phase === 'error' || phase === 'cancelled');
    phaseEl.textContent = phase === 'idle' ? 'idle' : isWorking ? 'working' : phase;
  }

  /** Track a new active subagent/task. */
  addActiveWork(id, description, color) {
    this._activeWork.set(id, { description, color });
    this._renderActiveWork();
  }

  /** Remove a completed subagent/task. */
  removeActiveWork(id) {
    this._activeWork.delete(id);
    this._renderActiveWork();
  }

  // ── Internal ──────────────────────────────────────────────────────────────

  _render() {
    this._el.innerHTML = `
      <div class="orch-header">
        <span class="orch-dot orch-alive"></span>
        <span class="orch-label">Controller</span>
        <span class="orch-summary-stat"></span>
        <span class="orch-phase">connecting</span>
      </div>
      <div class="orch-progress">
        <div class="orch-bar"><div class="orch-fill" style="width: 0%"></div></div>
        <span class="orch-pct"></span>
      </div>
      <div class="orch-active-work"></div>
      <div class="orch-task-table-wrap"></div>
    `;
  }

  _setPhaseDisplay(phase) {
    const phaseEl = this._el.querySelector('.orch-phase');
    if (phaseEl) phaseEl.textContent = phase;

    const dot = this._el.querySelector('.orch-dot');
    if (dot) {
      dot.classList.add('orch-alive');
      dot.classList.remove('orch-dead');
    }
  }

  _updateProgress() {
    const tasks = this._tasks;
    const total = tasks.length;
    const done = tasks.filter(t => t.status === 'done').length;
    const wip = tasks.filter(t => t.status === 'in_progress').length;
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;

    const fillEl = this._el.querySelector('.orch-fill');
    if (fillEl) fillEl.style.width = `${pct}%`;

    const statEl = this._el.querySelector('.orch-summary-stat');
    if (statEl) {
      statEl.textContent = total > 0
        ? `${wip ? wip + ' active \u00b7 ' : ''}${done}/${total} tasks`
        : '';
    }

    const pctEl = this._el.querySelector('.orch-pct');
    if (pctEl) {
      pctEl.textContent = total > 0 ? `${pct}%` : '';
    }

    this._renderTaskTable();
  }

  _renderActiveWork() {
    const wrap = this._el.querySelector('.orch-active-work');
    if (!wrap) return;

    if (!this._activeWork.size) {
      wrap.innerHTML = '';
      return;
    }

    const items = [...this._activeWork.values()];
    wrap.innerHTML = items.map(({ description, color }) => {
      const label = (description || '').length > 60
        ? description.slice(0, 57) + '...'
        : description;
      return `<span class="orch-work-item" style="--work-color: ${color}"><span class="orch-work-dot" style="background: ${color}"></span>${escapeHtml(label)}</span>`;
    }).join('');
  }

  /** Render the task table inside the controller root. */
  _renderTaskTable() {
    const wrap = this._el.querySelector('.orch-task-table-wrap');
    if (!wrap) return;

    const tasks = this._tasks;
    if (!tasks.length) {
      wrap.innerHTML = '';
      return;
    }

    const statusOrder = { in_progress: 0, queued: 1, planned: 1, pending: 1, done: 2 };
    const sorted = [...tasks].sort((a, b) => (statusOrder[a.status] ?? 1) - (statusOrder[b.status] ?? 1));

    wrap.innerHTML = `
      <table class="orch-task-table">
        <thead><tr><th>Task</th><th>Status</th><th>Agent</th></tr></thead>
        <tbody>
          ${sorted.map((t, i) => {
            const st = t.status || 'pending';
            const isDone = st === 'done';
            const isActive = st === 'in_progress';
            const dotColor = isDone ? 'var(--accent-green)' : isActive ? 'var(--accent-cyan)' : 'var(--text-muted)';
            const statusCls = isDone ? 'done' : isActive ? 'active' : 'queued';
            const statusLabel = isDone ? 'done' : isActive ? 'active' : st;
            const agentId = this._taskAgentMap?.get(t.index ?? i) || null;
            const agentLabel = agentId ? `<a class="ott-agent-link" data-session="${escapeHtml(agentId)}">${escapeHtml(agentId.slice(0, 8))}</a>` : '\u2014';
            return `<tr>
              <td class="${isDone ? 'ott-task-done' : ''}"><span class="ott-dot" style="background:${dotColor}"></span>${escapeHtml(t.text || t.content || '')}</td>
              <td><span class="ott-status ${statusCls}">${statusLabel}</span></td>
              <td class="ott-agent">${agentLabel}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>`;
  }
}
