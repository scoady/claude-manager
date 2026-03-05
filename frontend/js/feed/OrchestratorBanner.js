/** OrchestratorBanner — standalone orchestrator root node card (org chart tree root). */
import { renderMarkdown } from './MarkdownRenderer.js';

export class OrchestratorBanner {
  constructor() {
    this._el = document.createElement('div');
    this._el.className = 'orch-node';
    this._controllerSessionId = null;
    this._phase = null;
    this._summaryText = '';
    this._tasks = [];
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

  // ── Internal ──────────────────────────────────────────────────────────────

  _render() {
    this._el.innerHTML = `
      <div class="orch-header">
        <span class="orch-dot orch-alive"></span>
        <span class="orch-label">Orchestrator</span>
        <span class="orch-summary-stat"></span>
        <span class="orch-phase">connecting</span>
      </div>
      <div class="orch-progress">
        <div class="orch-bar"><div class="orch-fill" style="width: 0%"></div></div>
        <span class="orch-pct"></span>
      </div>
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
        ? `${wip ? wip + ' active · ' : ''}${done}/${total}`
        : '';
    }

    const pctEl = this._el.querySelector('.orch-pct');
    if (pctEl) {
      pctEl.textContent = total > 0 ? `${pct}%` : '';
    }
  }
}
