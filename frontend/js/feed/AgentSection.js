/** AgentSection — a collapsible agent node in the narrative feed. */
import { escapeHtml, formatUptime } from '../utils.js';
import { ToolBlock } from './ToolBlock.js';
import { renderMarkdown } from './MarkdownRenderer.js';

/**
 * Parse a subagent result into a structured checklist + remaining detail text.
 * Handles: - [x] checkboxes, numbered lists (1. ...), plain bullets (- ...)
 * Strips metadata lines (agentId, total_tokens, duration_ms, tool_uses, <usage>).
 *
 * Strategy: if a "## Result" or "## Summary" heading exists, parse only the
 * section below it for checklist items (the stuff above becomes detail/context).
 * This makes parsing reliable even when the subagent writes prose before the
 * checklist.
 */
function parseSubagentResult(text) {
  if (!text) return { items: [], detail: text || '' };

  // Strip metadata noise from Claude Agent tool output
  const metaRe = /^(agentId:|total_tokens:|tool_uses:|duration_ms:|<\/?usage>)/;
  const cleaned = text.split('\n').filter(l => !metaRe.test(l.trim())).join('\n');

  // Look for a ## Result / ## Summary section and split there
  const sectionRe = /^##\s+(Result|Summary|Completed|Report)\s*$/im;
  const sectionMatch = cleaned.match(sectionRe);

  let checklistBlock, preamble;
  if (sectionMatch) {
    const idx = cleaned.indexOf(sectionMatch[0]);
    preamble = cleaned.slice(0, idx).trim();
    checklistBlock = cleaned.slice(idx + sectionMatch[0].length).trim();
  } else {
    preamble = '';
    checklistBlock = cleaned;
  }

  const lines = checklistBlock.split('\n');
  const items = [];
  const tailLines = [];

  // Patterns: checkbox, numbered list, plain bullet
  const checkRe   = /^\s*-\s*\[([ xX✓✗×~])\]\s*(.+)$/;
  const numberedRe = /^\s*\d+[.)]\s+(.+)$/;
  const bulletRe   = /^\s*[-*]\s+(.+)$/;

  // Headings like "## Summary" are structural, skip them for items
  const headingRe = /^\s*#{1,4}\s/;

  let foundAny = false;
  for (const line of lines) {
    // Checkbox format (highest priority)
    const cm = line.match(checkRe);
    if (cm) {
      const mark = cm[1].trim().toLowerCase();
      const done = mark !== '' && mark !== ' ';
      const ok = mark !== '✗' && mark !== '×';
      items.push({ text: cm[2].trim(), done, ok });
      foundAny = true;
      continue;
    }

    // Numbered list (treat as completed)
    const nm = line.match(numberedRe);
    if (nm && !headingRe.test(line)) {
      items.push({ text: nm[1].trim(), done: true, ok: true });
      foundAny = true;
      continue;
    }

    // Plain bullet — only capture if we're in a checklist section (i.e. we had
    // a ## heading OR we've already seen at least one item)
    const bm = line.match(bulletRe);
    if (bm && !headingRe.test(line) && (sectionMatch || foundAny) && items.length < 20) {
      items.push({ text: bm[1].trim(), done: true, ok: true });
      foundAny = true;
      continue;
    }

    tailLines.push(line);
  }

  // Combine preamble + non-checklist tail as the "detail" collapsed area
  const detailParts = [preamble, tailLines.join('\n').trim()].filter(Boolean);
  const detail = detailParts.join('\n\n').trim();
  return { items, detail };
}

const PHASE_LABELS = {
  starting:    'starting',
  thinking:    'thinking',
  generating:  'responding',
  tool_input:  'using tool',
  tool_exec:   'using tool',
  idle:        'idle',
  injecting:   'injecting',
  delegating:  'delegating',
  cancelled:   'cancelled',
  error:       'error',
};

const PHASE_CLASSES = {
  starting:    'phase-starting',
  thinking:    'phase-working',
  generating:  'phase-working',
  tool_input:  'phase-working',
  tool_exec:   'phase-working',
  idle:        'phase-idle',
  injecting:   'phase-working',
  delegating:  'phase-delegating',
  cancelled:   'phase-done',
  error:       'phase-error',
};

const PHASE_COLORS = {
  starting:    '#00f0ff',
  thinking:    '#ffcc00',
  generating:  '#ffcc00',
  tool_input:  '#e040fb',
  tool_exec:   '#e040fb',
  idle:        '#39ff14',
  injecting:   '#ffcc00',
  delegating:  '#b388ff',
  cancelled:   '#ff1744',
  error:       '#ff1744',
};

export class AgentSection {
  /**
   * @param {object} opts
   * @param {string} opts.sessionId
   * @param {string} opts.task
   * @param {string} opts.laneColor  — CSS color string for the left accent bar
   * @param {string} [opts.initialPhase] — initial phase (default: 'starting')
   * @param {number} [opts.initialTurnCount] — initial turn count (default: 0)
   * @param {Function} opts.onInject — (sessionId, message) => void
   * @param {Function} opts.onKill   — (sessionId) => void
   * @param {Function} opts.onStatus — (sessionId) => void
   * @param {Function} [opts.onFocus] — (sessionId) => void — called when user expands this section
   */
  constructor({ sessionId, task, laneColor, isController, isSubagent, subagentType, initialPhase, initialTurnCount, onInject, onKill, onStatus, onFocus }) {
    this.sessionId  = sessionId;
    this.task       = task;
    this.laneColor  = laneColor;
    this._isController = isController || false;
    this._isSubagent = isSubagent || false;
    this._subagentType = subagentType || '';
    this._onInject  = onInject;
    this._onKill    = onKill;
    this._onStatus  = onStatus;
    this._onFocus   = onFocus;

    this._phase     = initialPhase || 'starting';
    this._turnCount = initialTurnCount || 0;
    this._expanded  = false;
    this._streamText = '';
    this._lastCardIndex = 0;
    this._detailsOpen = false;
    this._toolBlocks = new Map();

    this._done = false;
    this._startTime = Date.now();
    this._currentPre = null;
    this._currentChunkText = '';
    this._autoFollow = true;
    this._phaseHistory = [{ phase: this._phase, startTime: Date.now() }];

    this.el = this._build();
    this._bindEvents();

    if (initialPhase) {
      this.setPhase(initialPhase);
    }
    if (initialTurnCount) {
      this.setTurnCount(initialTurnCount);
    }
  }

  // ── Build ──────────────────────────────────────────────────────────────────

  _build() {
    const el = document.createElement('div');
    const classes = ['agent-section'];
    if (this._isController) classes.push('controller');
    if (this._isSubagent) classes.push('subagent');
    el.className = classes.join(' ');
    el.dataset.session = this.sessionId;
    el.style.setProperty('--lane-color', this._isController ? '#ffcc00' : this.laneColor);
    el.innerHTML = `
      <div class="agent-section-header">
        <div class="agent-section-lane"></div>
        <div class="agent-section-title">${escapeHtml(this._taskLabel())}</div>
        <div class="agent-section-badges">
          ${this._isController ? '<span class="agent-controller-badge">\u2654 Controller</span>' : ''}
          ${this._isSubagent ? `<span class="agent-subagent-badge">${escapeHtml(this._subagentType || 'agent')}</span>` : ''}
          <div class="agent-phase-timeline"></div>
          <span class="agent-phase-badge ${PHASE_CLASSES[this._phase] || ''}">${PHASE_LABELS[this._phase] || this._phase}</span>
          <span class="agent-turn-count" title="Turns">${this._turnCount}t</span>
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
        <div class="agent-live-content">
          <div class="agent-stream-area">
            <div class="skeleton-loader">
              <div class="skeleton-line" style="width:85%"></div>
              <div class="skeleton-line" style="width:60%"></div>
              <div class="skeleton-line" style="width:40%"></div>
            </div>
            <span class="stream-cursor"></span>
            <button class="agent-follow-btn hidden">↓</button>
          </div>
          <div class="agent-status-card hidden"></div>
        </div>
        <button class="agent-detail-toggle hidden">Show raw log</button>
        <div class="agent-raw-log hidden">
          <pre class="agent-raw-pre"></pre>
        </div>
        <div class="agent-inject-composer hidden">
          <div class="inject-hint-row">
            <span class="inject-hint-label">Send a message</span>
          </div>
          <div class="inject-input-row">
            <textarea class="agent-inject-input" rows="1" placeholder="Inject a message to this agent..."></textarea>
            <button class="inject-send-btn" disabled>
              <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
                <path d="M1.5 6.5h10M7 2l4.5 4.5L7 11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </button>
          </div>
        </div>
      </div>
      ${this._isController ? '<div class="agent-children"></div>' : ''}`;
    return el;
  }

  /** Append a child AgentSection (subagent) into the nested tree area. */
  appendChildSection(childSection) {
    const container = this.el.querySelector('.agent-children');
    if (!container) return;
    childSection.el.style.opacity = '0';
    childSection.el.style.transform = 'translateY(8px)';
    container.appendChild(childSection.el);
    requestAnimationFrame(() => {
      childSection.el.style.transition = 'opacity 280ms ease, transform 280ms ease';
      childSection.el.style.opacity = '1';
      childSection.el.style.transform = 'translateY(0)';
    });
  }

  _taskLabel() {
    return this.task && this.task.length > 80 ? this.task.slice(0, 80) + '...' : (this.task || 'Agent');
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

    // Detail toggle — show/hide raw log
    this.el.querySelector('.agent-detail-toggle').addEventListener('click', e => {
      e.stopPropagation();
      this._detailsOpen = !this._detailsOpen;
      const rawLog = this.el.querySelector('.agent-raw-log');
      const btn = this.el.querySelector('.agent-detail-toggle');
      rawLog?.classList.toggle('hidden', !this._detailsOpen);
      btn.textContent = this._detailsOpen ? 'Hide raw log' : 'Show raw log';
    });

    // Auto-scroll detection on stream area
    const streamArea = this.el.querySelector('.agent-stream-area');
    streamArea?.addEventListener('scroll', () => {
      const atBottom = (streamArea.scrollHeight - streamArea.scrollTop - streamArea.clientHeight) < 30;
      this._autoFollow = atBottom;
      const followBtn = streamArea.querySelector('.agent-follow-btn');
      if (followBtn) followBtn.classList.toggle('hidden', atBottom);
    });

    // Follow button
    this.el.querySelector('.agent-follow-btn')?.addEventListener('click', e => {
      e.stopPropagation();
      this._autoFollow = true;
      const area = this.el.querySelector('.agent-stream-area');
      if (area) area.scrollTop = area.scrollHeight;
      e.currentTarget.classList.add('hidden');
    });

    // Inject composer
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

  /** Hydrate section from saved messages (on reconnect/refresh). */
  hydrateMessages(messages) {
    if (!messages || !messages.length) return;

    // Remove skeleton
    const skeleton = this.el.querySelector('.skeleton-loader');
    if (skeleton) skeleton.remove();

    // Accumulate all text for raw log
    for (const msg of messages) {
      if (msg.type === 'text' && msg.content) {
        this._streamText += msg.content;
      }
    }
    this._lastCardIndex = this._streamText.length;

    // Populate raw log with full output
    const rawPre = this.el.querySelector('.agent-raw-pre');
    if (rawPre) rawPre.textContent = this._streamText;

    if (['idle', 'cancelled', 'error'].includes(this._phase)) {
      // Find the last text message as a clean summary
      let summary = '';
      for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i].type === 'text' && messages[i].content?.trim()) {
          summary = messages[i].content;
          break;
        }
      }

      if (summary) {
        const card = this.el.querySelector('.agent-status-card');
        if (card) {
          card.innerHTML = renderMarkdown(summary);
          card.classList.remove('hidden');
        }
      }

      // Hide stream area, show raw log toggle
      const streamArea = this.el.querySelector('.agent-stream-area');
      if (streamArea) streamArea.classList.add('hidden');
      const toggle = this.el.querySelector('.agent-detail-toggle');
      if (toggle) toggle.classList.remove('hidden');

      // Remove cursor — not streaming
      this.el.querySelector('.stream-cursor')?.remove();
    } else {
      // Agent still running — replay into live stream area
      for (const msg of messages) {
        if (msg.type === 'text' && msg.content) {
          // Re-append as rich markdown (appendChunk would double-count _streamText)
          const streamArea = this.el.querySelector('.agent-stream-area');
          const cursor = streamArea?.querySelector('.stream-cursor');
          if (streamArea) {
            if (!this._currentPre) {
              this._currentPre = document.createElement('div');
              this._currentPre.className = 'agent-status-card';
              if (cursor) {
                streamArea.insertBefore(this._currentPre, cursor);
              } else {
                streamArea.appendChild(this._currentPre);
              }
              this._currentChunkText = '';
            }
            this._currentChunkText += msg.content;
            this._currentPre.innerHTML = renderMarkdown(this._currentChunkText);
          }
        } else if (msg.type === 'tool_use') {
          this.addToolBlock({
            toolId: msg.tool_id || `h-${Math.random().toString(36).slice(2, 8)}`,
            toolName: msg.tool_name || 'tool',
            toolInput: msg.tool_input || {},
          });
        }
      }
    }
  }

  /** Append a streaming text chunk — rendered as rich markdown in stream area. */
  appendChunk(text) {
    this._streamText += text;
    this._currentChunkText += text;

    // Update raw log
    const rawPre = this.el.querySelector('.agent-raw-pre');
    if (rawPre) rawPre.textContent = this._streamText;

    // Remove skeleton if present
    const skeleton = this.el.querySelector('.skeleton-loader');
    if (skeleton) skeleton.remove();

    const streamArea = this.el.querySelector('.agent-stream-area');
    if (!streamArea) return;
    streamArea.classList.remove('hidden');

    // Render as rich markdown (reuses agent-status-card styles)
    const cursor = streamArea.querySelector('.stream-cursor');
    if (!this._currentPre) {
      this._currentPre = document.createElement('div');
      this._currentPre.className = 'agent-status-card';
      if (cursor) {
        streamArea.insertBefore(this._currentPre, cursor);
      } else {
        streamArea.appendChild(this._currentPre);
      }
    }

    this._currentPre.innerHTML = renderMarkdown(this._currentChunkText);

    // Auto-scroll if following
    if (this._autoFollow) {
      streamArea.scrollTop = streamArea.scrollHeight;
    }
  }

  /** Render a structured subagent result as checklist + collapsed detail. */
  setSubagentResult(resultText) {
    const { items, detail } = parseSubagentResult(resultText);
    const card = this.el.querySelector('.agent-status-card');
    if (!card) return;

    // Hide stream area
    const streamArea = this.el.querySelector('.agent-stream-area');
    if (streamArea) streamArea.classList.add('hidden');

    let html = '';

    if (items.length) {
      html += '<div class="subagent-checklist">';
      for (const item of items) {
        const icon = item.done
          ? (item.ok
            ? '<span class="sa-check ok">✓</span>'
            : '<span class="sa-check fail">✗</span>')
          : '<span class="sa-check pending">○</span>';
        const cls = item.done ? (item.ok ? 'sa-done' : 'sa-fail') : 'sa-pending';
        html += `<div class="sa-item ${cls}">${icon}<span class="sa-text">${escapeHtml(item.text)}</span></div>`;
      }
      html += '</div>';
    }

    if (detail) {
      html += `
        <div class="sa-detail-section">
          <button class="sa-detail-toggle">Show full output</button>
          <div class="sa-detail-body hidden">
            <div class="sa-detail-content">${renderMarkdown(detail)}</div>
          </div>
        </div>`;
    }

    if (!html) {
      // No checklist parsed — fall back to rendering as markdown
      html = renderMarkdown(resultText);
    }

    card.innerHTML = html;
    card.classList.remove('hidden');
    card.classList.add('card-fade-in');
    setTimeout(() => card.classList.remove('card-fade-in'), 250);

    // Bind the detail toggle
    const toggle = card.querySelector('.sa-detail-toggle');
    const body = card.querySelector('.sa-detail-body');
    toggle?.addEventListener('click', () => {
      const open = body.classList.toggle('hidden');
      toggle.textContent = open ? 'Show full output' : 'Hide full output';
    });
  }

  /** Update the status card with rendered markdown from accumulated text. */
  updateStatusCard() {
    const card = this.el.querySelector('.agent-status-card');
    if (!card) return;

    const newText = this._streamText.slice(this._lastCardIndex).trim();
    this._lastCardIndex = this._streamText.length;

    if (!newText) return;

    const html = renderMarkdown(newText);
    card.innerHTML = html;
    card.classList.remove('hidden');
    card.classList.add('card-fade-in');
    setTimeout(() => card.classList.remove('card-fade-in'), 250);

    // Switch to status card view — hide stream, show raw log toggle
    const streamArea = this.el.querySelector('.agent-stream-area');
    if (streamArea) streamArea.classList.add('hidden');
    const toggle = this.el.querySelector('.agent-detail-toggle');
    if (toggle) toggle.classList.remove('hidden');
  }

  /** Add a new ToolBlock inline in the stream area. */
  addToolBlock({ toolId, toolName, toolInput }) {
    const block = new ToolBlock({ toolId, toolName, toolInput });
    this._toolBlocks.set(toolId, block);

    const streamArea = this.el.querySelector('.agent-stream-area');
    if (!streamArea) return;

    // Remove skeleton if present
    const skeleton = streamArea.querySelector('.skeleton-loader');
    if (skeleton) skeleton.remove();

    const cursor = streamArea.querySelector('.stream-cursor');
    if (cursor) {
      streamArea.insertBefore(block.el, cursor);
    } else {
      streamArea.appendChild(block.el);
    }

    // Break current block so next text chunk creates a new one after tool block
    this._currentPre = null;
    this._currentChunkText = '';

    if (this._autoFollow) {
      streamArea.scrollTop = streamArea.scrollHeight;
    }
  }

  /** Update an existing ToolBlock with its output. */
  updateToolBlock(toolId, output) {
    const block = this._toolBlocks.get(toolId);
    if (block) block.setOutput(output);
  }

  /** Update the phase badge and manage view transitions. */
  setPhase(phase) {
    const prevPhase = this._phase;
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

    // Pulse lane bar when working
    const lane = this.el.querySelector('.agent-section-lane');
    if (lane) {
      const isWorking = !['idle', 'cancelled', 'error'].includes(phase);
      lane.classList.toggle('pulsing', isWorking);
    }

    // When resuming work from idle, switch back to live stream view
    if (prevPhase === 'idle' && !['idle', 'cancelled', 'error'].includes(phase)) {
      const streamArea = this.el.querySelector('.agent-stream-area');
      const card = this.el.querySelector('.agent-status-card');
      const toggle = this.el.querySelector('.agent-detail-toggle');
      const rawLog = this.el.querySelector('.agent-raw-log');
      streamArea?.classList.remove('hidden');
      card?.classList.add('hidden');
      toggle?.classList.add('hidden');
      rawLog?.classList.add('hidden');
      this._detailsOpen = false;

      // Clear stream area content for new turn
      this._clearStreamArea();
      this._currentPre = null;
    }

    // Update phase timeline
    if (phase !== prevPhase) {
      this._phaseHistory.push({ phase, startTime: Date.now() });
      this._renderTimeline();
    }
  }

  /** Update the turn counter. */
  setTurnCount(count) {
    this._turnCount = count;
    const el = this.el.querySelector('.agent-turn-count');
    if (el) el.textContent = `${count}t`;
  }

  /** Mark session as done — collapse and add duration badge. */
  markDone(reason) {
    // Controllers going idle are NOT "done" — they persist
    if (this._isController && reason === 'idle') {
      this.setPhase('idle');
      return;
    }

    this._done = true;
    const phase = reason === 'cancelled' ? 'cancelled' : (reason === 'error' ? 'error' : 'idle');
    this.setPhase(phase);

    // Remove blinking cursor
    this.el.querySelector('.stream-cursor')?.remove();

    // Add completed class (controllers never get this)
    if (!this._isController) {
      this.el.classList.add('completed');
    }

    // Add duration badge
    const duration = this._formatDuration(Date.now() - this._startTime);
    const badges = this.el.querySelector('.agent-section-badges');
    if (badges) {
      const durBadge = document.createElement('span');
      durBadge.className = 'agent-duration';
      durBadge.textContent = duration;
      badges.appendChild(durBadge);
    }

    // Auto-collapse after delay (not for controllers)
    if (!this._isController) {
      setTimeout(() => {
        if (this._done) this.setExpanded(false);
      }, 600);
    }
  }

  /** Expand or collapse the body. */
  setExpanded(expanded) {
    this._expanded = expanded;
    this.el.classList.toggle('expanded', expanded);
    // Notify parent for adaptive focus
    if (expanded) this._onFocus?.(this.sessionId);
  }

  /** Update the session ID (pending-PID → real UUID). */
  updateSessionId(newId) {
    this.sessionId = newId;
    this.el.dataset.session = newId;
  }

  // ── Private helpers ─────────────────────────────────────────────────────────

  _clearStreamArea() {
    const area = this.el.querySelector('.agent-stream-area');
    if (!area) return;
    const cursor = area.querySelector('.stream-cursor');
    const followBtn = area.querySelector('.agent-follow-btn');
    Array.from(area.children).forEach(child => {
      if (child !== cursor && child !== followBtn) child.remove();
    });
  }

  _formatDuration(ms) {
    const secs = Math.floor(ms / 1000);
    if (secs < 60) return `${secs}s`;
    const mins = Math.floor(secs / 60);
    const remSecs = secs % 60;
    if (mins < 60) return `${mins}m ${remSecs}s`;
    const hrs = Math.floor(mins / 60);
    const remMins = mins % 60;
    return `${hrs}h ${remMins}m`;
  }

  _renderTimeline() {
    const container = this.el.querySelector('.agent-phase-timeline');
    if (!container || this._phaseHistory.length < 2) return;

    const now = Date.now();
    const totalMs = now - this._phaseHistory[0].startTime;
    if (totalMs < 1000) return;

    let html = '';
    for (let i = 0; i < this._phaseHistory.length; i++) {
      const entry = this._phaseHistory[i];
      const endTime = (i + 1 < this._phaseHistory.length)
        ? this._phaseHistory[i + 1].startTime
        : now;
      const durationMs = endTime - entry.startTime;
      const pct = Math.max(2, (durationMs / totalMs) * 100);
      const color = PHASE_COLORS[entry.phase] || '#484f58';
      const isLast = (i === this._phaseHistory.length - 1);
      html += `<div class="phase-seg${isLast ? ' phase-seg-active' : ''}" style="width:${pct}%;background:${color}" title="${entry.phase}"></div>`;
    }

    container.innerHTML = html;
  }
}
