/** OrchestratorBanner — always-on controller status card at the top of the feed. */
import { renderMarkdown } from './MarkdownRenderer.js';

export class OrchestratorBanner {
  constructor() {
    this._el = document.createElement('div');
    this._el.className = 'orchestrator-banner';
    this._controllerSessionId = null;
    this._phase = null;
    this._summaryText = '';
    this._tasks = [];
    this._render();
  }

  get el() { return this._el; }
  get controllerSessionId() { return this._controllerSessionId; }

  /** Initialize banner with project and task data. */
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
    const summaryEl = this._el.querySelector('.ob-summary');
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
    const dot = this._el.querySelector('.ob-alive-dot');
    const phaseEl = this._el.querySelector('.ob-phase');
    if (!dot || !phaseEl) return;

    const isWorking = !['idle', 'cancelled', 'error'].includes(phase);
    dot.classList.toggle('ob-alive', isWorking || phase === 'idle');
    dot.classList.toggle('ob-dead', phase === 'error' || phase === 'cancelled');
    phaseEl.textContent = phase === 'idle' ? 'idle' : isWorking ? 'working' : phase;
  }

  // ── Internal ──────────────────────────────────────────────────────────────

  _render() {
    this._el.innerHTML = `
      <div class="ob-header">
        <span class="ob-alive-dot ob-alive"></span>
        <span class="ob-label">orchestrator</span>
        <span class="ob-phase">connecting</span>
      </div>
      <div class="ob-summary">
        <span class="ob-placeholder">Waiting for orchestrator status...</span>
      </div>
      <div class="ob-progress">
        <div class="ob-progress-bar"><div class="ob-progress-fill" style="width: 0%"></div></div>
        <div class="ob-progress-label"></div>
      </div>
    `;
  }

  _setPhaseDisplay(phase) {
    const phaseEl = this._el.querySelector('.ob-phase');
    if (phaseEl) phaseEl.textContent = phase;

    const dot = this._el.querySelector('.ob-alive-dot');
    if (dot) {
      dot.classList.add('ob-alive');
      dot.classList.remove('ob-dead');
    }
  }

  _updateProgress() {
    const tasks = this._tasks;
    const total = tasks.length;
    const done = tasks.filter(t => t.status === 'done').length;
    const wip = tasks.filter(t => t.status === 'in_progress').length;
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;

    const fillEl = this._el.querySelector('.ob-progress-fill');
    if (fillEl) fillEl.style.width = `${pct}%`;

    const labelEl = this._el.querySelector('.ob-progress-label');
    if (labelEl) {
      labelEl.innerHTML = total > 0
        ? `<span>${done}/${total} tasks</span><span>${pct}%</span>${wip ? `<span>${wip} in progress</span>` : ''}`
        : '<span>No tasks yet</span>';
    }
  }
}
