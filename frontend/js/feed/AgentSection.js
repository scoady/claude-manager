/** AgentSection — a collapsible agent node in the narrative feed. */
import { escapeHtml, formatUptime } from '../utils.js';
import { ToolBlock } from './ToolBlock.js';

const PHASE_LABELS = {
  starting:   'starting',
  thinking:   'thinking',
  tool_use:   'using tool',
  responding: 'responding',
  idle:       'idle',
  cancelled:  'cancelled',
  error:      'error',
};

const PHASE_CLASSES = {
  starting:   'phase-starting',
  thinking:   'phase-working',
  tool_use:   'phase-working',
  responding: 'phase-working',
  idle:       'phase-idle',
  cancelled:  'phase-done',
  error:      'phase-error',
};

export class AgentSection {
  /**
   * @param {object} opts
   * @param {string} opts.sessionId
   * @param {string} opts.task
   * @param {string} opts.laneColor  — CSS color string for the left accent bar
   * @param {Function} opts.onInject — (sessionId, message) => void
   * @param {Function} opts.onKill   — (sessionId) => void
   * @param {Function} opts.onStatus — (sessionId) => void
   */
  constructor({ sessionId, task, laneColor, onInject, onKill, onStatus }) {
    this.sessionId  = sessionId;
    this.task       = task;
    this.laneColor  = laneColor;
    this._onInject  = onInject;
    this._onKill    = onKill;
    this._onStatus  = onStatus;

    this._phase     = 'starting';
    this._turnCount = 0;
    this._expanded  = false;
    this._streamText = '';
    this._toolBlocks = new Map(); // toolId → ToolBlock

    this.el = this._build();
    this._bindEvents();
  }

  // ── Build ──────────────────────────────────────────────────────────────────

  _build() {
    const el = document.createElement('div');
    el.className = 'agent-section';
    el.dataset.session = this.sessionId;
    el.style.setProperty('--lane-color', this.laneColor);
    el.innerHTML = `
      <div class="agent-section-header">
        <div class="agent-section-lane"></div>
        <div class="agent-section-title">${escapeHtml(this._taskLabel())}</div>
        <div class="agent-section-badges">
          <span class="agent-phase-badge ${PHASE_CLASSES[this._phase] || ''}">${PHASE_LABELS[this._phase] || this._phase}</span>
          <span class="agent-turn-count" title="Turns">0t</span>
        </div>
        <div class="agent-section-actions">
          <button class="icon-btn agent-status-btn" title="Ask status">?</button>
          <button class="icon-btn danger agent-kill-btn" title="Kill agent">
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
              <path d="M2 2l7 7M9 2l-7 7" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
            </svg>
          </button>
          <button class="icon-btn agent-toggle-btn" title="Toggle expanded">
            <svg class="toggle-chevron" width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M3 5l3 3 3-3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>
        </div>
      </div>
      <div class="agent-section-body">
        <div class="agent-stream-area"></div>
        <div class="agent-tools-area"></div>
        <div class="agent-inject-composer hidden">
          <div class="inject-hint-row">
            <span class="inject-hint-label">Send a message</span>
          </div>
          <div class="inject-input-row">
            <textarea class="agent-inject-input" rows="1" placeholder="Inject a message to this agent…"></textarea>
            <button class="inject-send-btn" disabled>
              <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                <path d="M1.5 6.5h10M7 2l4.5 4.5L7 11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </button>
          </div>
        </div>
      </div>`;
    return el;
  }

  _taskLabel() {
    return this.task && this.task.length > 80 ? this.task.slice(0, 80) + '…' : (this.task || 'Agent');
  }

  // ── Events ─────────────────────────────────────────────────────────────────

  _bindEvents() {
    this.el.querySelector('.agent-toggle-btn').addEventListener('click', e => {
      e.stopPropagation();
      this.setExpanded(!this._expanded);
    });

    this.el.querySelector('.agent-section-header').addEventListener('click', () => {
      this.setExpanded(!this._expanded);
    });

    this.el.querySelector('.agent-kill-btn').addEventListener('click', e => {
      e.stopPropagation();
      this._onKill?.(this.sessionId);
    });

    this.el.querySelector('.agent-status-btn').addEventListener('click', e => {
      e.stopPropagation();
      this._onStatus?.(this.sessionId);
    });

    const textarea = this.el.querySelector('.agent-inject-input');
    const sendBtn  = this.el.querySelector('.inject-send-btn');

    textarea?.addEventListener('input', () => {
      sendBtn.disabled = !textarea.value.trim();
      textarea.style.height = 'auto';
      textarea.style.height = Math.min(textarea.scrollHeight, 100) + 'px';
    });

    textarea?.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this._doInject();
      }
    });

    sendBtn?.addEventListener('click', () => this._doInject());
  }

  _doInject() {
    const textarea = this.el.querySelector('.agent-inject-input');
    const msg = textarea?.value.trim();
    if (!msg) return;
    this._onInject?.(this.sessionId, msg);
    textarea.value = '';
    textarea.style.height = 'auto';
    this.el.querySelector('.inject-send-btn').disabled = true;
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /** Append a streaming text chunk. */
  appendChunk(text) {
    this._streamText += text;
    const area = this.el.querySelector('.agent-stream-area');
    if (!area) return;

    if (this._expanded) {
      // In expanded mode show full text
      let cursor = area.querySelector('.stream-cursor');
      if (!cursor) {
        area.textContent = '';
        const pre = document.createElement('pre');
        pre.className = 'agent-stream-pre';
        area.appendChild(pre);
        cursor = document.createElement('span');
        cursor.className = 'stream-cursor';
        area.appendChild(cursor);
      }
      const pre = area.querySelector('.agent-stream-pre');
      if (pre) pre.textContent = this._streamText;
    } else {
      // Collapsed: show last N lines
      const lines = this._streamText.split('\n');
      const preview = lines.slice(-6).join('\n');
      let pre = area.querySelector('.agent-stream-pre');
      if (!pre) {
        pre = document.createElement('pre');
        pre.className = 'agent-stream-pre collapsed-preview';
        area.appendChild(pre);
      }
      pre.textContent = preview;
    }
    // Auto-scroll if expanded
    if (this._expanded) {
      area.scrollTop = area.scrollHeight;
    }
  }

  /** Add a new ToolBlock for a tool_start event. */
  addToolBlock({ toolId, toolName, toolInput }) {
    const block = new ToolBlock({ toolId, toolName, toolInput });
    this._toolBlocks.set(toolId, block);
    const toolsArea = this.el.querySelector('.agent-tools-area');
    toolsArea?.appendChild(block.el);
  }

  /** Update an existing ToolBlock with its output. */
  updateToolBlock(toolId, output) {
    const block = this._toolBlocks.get(toolId);
    if (block) block.setOutput(output);
  }

  /** Update the phase badge. */
  setPhase(phase) {
    this._phase = phase;
    const badge = this.el.querySelector('.agent-phase-badge');
    if (badge) {
      badge.textContent = PHASE_LABELS[phase] || phase;
      badge.className = `agent-phase-badge ${PHASE_CLASSES[phase] || ''}`;
    }

    // Show/hide inject composer when idle
    const composer = this.el.querySelector('.agent-inject-composer');
    if (composer) {
      composer.classList.toggle('hidden', phase !== 'idle');
    }

    // Pulse on header lane bar when working
    const lane = this.el.querySelector('.agent-section-lane');
    if (lane) {
      const isWorking = phase !== 'idle' && phase !== 'cancelled' && phase !== 'error';
      lane.classList.toggle('pulsing', isWorking);
    }
  }

  /** Update the turn counter. */
  setTurnCount(count) {
    this._turnCount = count;
    const el = this.el.querySelector('.agent-turn-count');
    if (el) el.textContent = `${count}t`;
  }

  /** Mark session as done (no more streaming). */
  markDone(reason) {
    const phase = reason === 'cancelled' ? 'cancelled' : (reason === 'error' ? 'error' : 'idle');
    this.setPhase(phase);
    const area = this.el.querySelector('.agent-stream-area');
    // Remove blinking cursor if present
    area?.querySelector('.stream-cursor')?.remove();
  }

  /** Expand or collapse the body. */
  setExpanded(expanded) {
    this._expanded = expanded;
    this.el.classList.toggle('expanded', expanded);
    const chevron = this.el.querySelector('.toggle-chevron path');
    if (chevron) {
      chevron.setAttribute('d', expanded
        ? 'M3 7l3-3 3 3'
        : 'M3 5l3 3 3-3'
      );
    }
    // If expanding, re-render full stream text
    if (expanded && this._streamText) {
      const area = this.el.querySelector('.agent-stream-area');
      if (area) {
        area.innerHTML = '';
        const pre = document.createElement('pre');
        pre.className = 'agent-stream-pre';
        pre.textContent = this._streamText;
        area.appendChild(pre);
      }
    }
  }

  /** Update the session ID (pending-PID → real UUID). */
  updateSessionId(newId) {
    this.sessionId = newId;
    this.el.dataset.session = newId;
  }
}
