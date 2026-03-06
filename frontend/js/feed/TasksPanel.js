/** TasksPanel — Mission Control Board with canvas-style task widgets */
import { escapeHtml, toast } from '../utils.js';
import { renderMarkdown } from './MarkdownRenderer.js';
import { api } from '../api.js';

// Color palette
const COLORS = {
  done: { main: '#4ade80', dim: 'rgba(74,222,128,0.12)', glow: 'rgba(74,222,128,0.35)' },
  in_progress: { main: '#fbbf24', dim: 'rgba(251,191,36,0.12)', glow: 'rgba(251,191,36,0.35)' },
  pending: { main: '#475569', dim: 'rgba(71,85,105,0.15)', glow: 'rgba(71,85,105,0.2)' },
  cyan: '#67e8f9',
  purple: '#a78bfa',
  surface: '#0e1525',
  elevated: '#141d30',
};

export class TasksPanel {
  constructor(projectName) {
    this._project = projectName;
    this._tasks = [];
    this._el = document.createElement('div');
    this._el.className = 'tasks-panel-v2';
    this._taskStreams = new Map();
    this._taskTools = new Map();
    this._taskAgentMap = new Map();
    this._modalTaskIndex = null;
    this._particles = [];
    this._animFrame = null;
    this._filterStatus = 'all'; // all | in_progress | pending | done
    this._render();
  }

  get el() { return this._el; }

  setTaskAgentMap(map) { this._taskAgentMap = map; }

  async load() {
    try {
      this._tasks = await api.getTasks(this._project);
      this._renderContent();
    } catch (e) {
      const body = this._el.querySelector('.mc-board');
      if (body) body.innerHTML = '<div class="mc-empty">Failed to load tasks</div>';
    }
  }

  updateTasks(tasks) {
    const oldStatuses = new Map(this._tasks.map(t => [t.index, t.status]));
    this._tasks = tasks;
    // Detect completions for particle burst
    tasks.forEach(t => {
      if (t.status === 'done' && oldStatuses.get(t.index) !== 'done') {
        this._queueCompletionBurst(t.index);
      }
    });
    this._renderContent();
  }

  appendAgentChunk(sessionId, chunk) {
    const taskIndex = this._resolveTaskIndex(sessionId);
    if (taskIndex == null) return;
    const prev = this._taskStreams.get(taskIndex) || '';
    this._taskStreams.set(taskIndex, prev + chunk);
    if (this._modalTaskIndex === taskIndex) this._updateModalOutput(taskIndex);
    // Update the card's live indicator
    this._updateCardLiveState(taskIndex);
  }

  addToolMilestone(sessionId, toolName, toolInput) {
    const taskIndex = this._resolveTaskIndex(sessionId);
    if (taskIndex == null) return;
    if (!this._taskTools.has(taskIndex)) this._taskTools.set(taskIndex, []);
    this._taskTools.get(taskIndex).push({
      toolName: toolName || '', toolInput: toolInput || {},
      toolOutput: null, startTime: Date.now(), duration: null, status: 'running'
    });
    this._updateCardMilestone(taskIndex, toolName, toolInput);
    if (this._modalTaskIndex === taskIndex) this._updateModalTools(taskIndex);
  }

  completeToolMilestone(sessionId, toolOutput) {
    const taskIndex = this._resolveTaskIndex(sessionId);
    if (taskIndex == null) return;
    const tools = this._taskTools.get(taskIndex);
    if (!tools?.length) return;
    const last = tools[tools.length - 1];
    last.toolOutput = toolOutput;
    last.duration = Date.now() - last.startTime;
    last.status = 'ok';
    if (this._modalTaskIndex === taskIndex) this._updateModalTools(taskIndex);
  }

  _resolveTaskIndex(sessionId) {
    for (const [idx, sid] of this._taskAgentMap) {
      if (sid === sessionId) return idx;
    }
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

  destroy() {
    this._closeModal();
    if (this._animFrame) cancelAnimationFrame(this._animFrame);
  }

  // ── Rendering ──────────────────────────────────────────────────

  _render() {
    this._el.innerHTML = `
      <div class="mc-canvas-bg"><canvas class="mc-particle-canvas"></canvas></div>
      <div class="mc-content">
        <div class="mc-add-form">
          <div class="mc-add-row">
            <input type="text" class="mc-add-input" placeholder="Add a new task..." />
            <button class="mc-add-btn" disabled>+</button>
            <button class="mc-plan-btn" disabled>Plan</button>
          </div>
        </div>
        <div class="mc-board"></div>
      </div>
    `;
    this._bindAddForm();
    this._initParticleCanvas();
  }

  _renderContent() {
    const board = this._el.querySelector('.mc-board');
    if (!this._tasks.length) {
      board.innerHTML = `
        <div class="mc-empty">
          <div class="mc-empty-icon">
            <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
              <circle cx="24" cy="24" r="20" stroke="${COLORS.cyan}" stroke-width="1" stroke-dasharray="4 4" opacity="0.3"/>
              <circle cx="24" cy="24" r="3" fill="${COLORS.cyan}" opacity="0.4"/>
              <path d="M24 12v4M24 32v4M12 24h4M32 24h4" stroke="${COLORS.cyan}" stroke-width="1" opacity="0.3"/>
            </svg>
          </div>
          <div class="mc-empty-text">No tasks yet</div>
          <div class="mc-empty-sub">Add a task above to begin</div>
        </div>`;
      return;
    }

    const done = this._tasks.filter(t => t.status === 'done');
    const active = this._tasks.filter(t => t.status === 'in_progress');
    const pending = this._tasks.filter(t => t.status === 'pending');
    const total = this._tasks.length;
    const pctDone = total ? Math.round((done.length / total) * 100) : 0;
    const pctActive = total ? Math.round((active.length / total) * 100) : 0;
    const pctPending = total ? 100 - pctDone - pctActive : 0;

    // Filter tasks
    const filtered = this._filterStatus === 'all'
      ? this._tasks
      : this._tasks.filter(t => t.status === this._filterStatus);

    board.innerHTML = `
      <div class="mc-status-strip">
        <div class="mc-flow-bar">
          <div class="mc-flow-seg mc-flow-done" style="width:${pctDone}%"
               title="${done.length} done"></div>
          <div class="mc-flow-seg mc-flow-active" style="width:${pctActive}%"
               title="${active.length} active"></div>
          <div class="mc-flow-seg mc-flow-pending" style="width:${pctPending}%"
               title="${pending.length} pending"></div>
        </div>
        <div class="mc-stats-row">
          <div class="mc-stat mc-stat-pct">
            <span class="mc-stat-big">${pctDone}</span><span class="mc-stat-unit">%</span>
          </div>
          <div class="mc-stat-filters">
            <button class="mc-filter-btn${this._filterStatus === 'all' ? ' active' : ''}" data-filter="all">
              ALL <span class="mc-filter-count">${total}</span>
            </button>
            <button class="mc-filter-btn mc-filter-active${this._filterStatus === 'in_progress' ? ' active' : ''}" data-filter="in_progress">
              <span class="mc-filter-dot" style="background:${COLORS.in_progress.main}"></span>
              ACTIVE <span class="mc-filter-count">${active.length}</span>
            </button>
            <button class="mc-filter-btn mc-filter-pending${this._filterStatus === 'pending' ? ' active' : ''}" data-filter="pending">
              <span class="mc-filter-dot" style="background:${COLORS.pending.main}"></span>
              QUEUED <span class="mc-filter-count">${pending.length}</span>
            </button>
            <button class="mc-filter-btn mc-filter-done${this._filterStatus === 'done' ? ' active' : ''}" data-filter="done">
              <span class="mc-filter-dot" style="background:${COLORS.done.main}"></span>
              DONE <span class="mc-filter-count">${done.length}</span>
            </button>
          </div>
        </div>
      </div>
      <div class="mc-grid">
        ${filtered.map((t, i) => this._renderCard(t, i)).join('')}
      </div>
    `;

    this._bindFilterEvents(board);
    this._bindCardEvents(board);
  }

  _renderCard(task, staggerIndex) {
    const st = task.status;
    const c = COLORS[st] || COLORS.pending;
    const hasStream = this._taskStreams.has(task.index);
    const tools = this._taskTools.get(task.index) || [];
    const lastTool = tools.length ? tools[tools.length - 1] : null;
    const hasMappedAgent = this._taskAgentMap.has(task.index);
    const toolCount = tools.length;

    // Progress ring values
    let ringPct = 0;
    if (st === 'done') ringPct = 100;
    else if (st === 'in_progress') ringPct = Math.min(95, 10 + toolCount * 5);

    const circumference = 2 * Math.PI * 18;
    const dashOffset = circumference - (ringPct / 100) * circumference;

    // Status label
    const statusLabel = st === 'done' ? 'COMPLETE' : st === 'in_progress' ? 'ACTIVE' : 'QUEUED';

    // Last milestone text
    let milestoneText = '';
    if (lastTool) {
      milestoneText = this._toolLabel(lastTool.toolName, lastTool.toolInput);
    } else if (st === 'in_progress' && !hasStream && !hasMappedAgent) {
      milestoneText = 'Waiting for dispatch...';
    } else if (st === 'in_progress' && !hasStream) {
      milestoneText = 'Agent starting...';
    }

    // Indentation indicator
    const indent = task.indent || 0;
    const indentMark = indent > 0
      ? `<span class="mc-card-indent" title="Subtask (depth ${indent})">${'\u2514'.repeat(1)}</span>`
      : '';

    return `
      <div class="mc-card mc-card-${st}" data-index="${task.index}"
           style="animation-delay:${staggerIndex * 40}ms">
        <div class="mc-card-glow" style="background:radial-gradient(ellipse at 50% 0%, ${c.glow}, transparent 70%)"></div>
        <div class="mc-card-inner">
          <div class="mc-card-top">
            <div class="mc-card-ring">
              <svg viewBox="0 0 40 40" class="mc-ring-svg">
                <circle cx="20" cy="20" r="18" fill="none" stroke="${COLORS.elevated}"
                        stroke-width="2.5"/>
                <circle cx="20" cy="20" r="18" fill="none" stroke="${c.main}"
                        stroke-width="2.5" stroke-linecap="round"
                        stroke-dasharray="${circumference}"
                        stroke-dashoffset="${dashOffset}"
                        transform="rotate(-90 20 20)"
                        class="mc-ring-progress"/>
                ${st === 'done'
                  ? `<path d="M14 20l4 4 8-8" stroke="${c.main}" stroke-width="2" fill="none"
                           stroke-linecap="round" stroke-linejoin="round"
                           class="mc-ring-check"/>`
                  : st === 'in_progress'
                    ? `<circle cx="20" cy="20" r="3" fill="${c.main}" class="mc-ring-pulse"/>`
                    : `<circle cx="20" cy="20" r="2.5" fill="${COLORS.pending.main}" opacity="0.5"/>`
                }
              </svg>
            </div>
            <div class="mc-card-info">
              <div class="mc-card-badge" style="color:${c.main};background:${c.dim};border-color:${c.main}30">
                ${statusLabel}
              </div>
              <div class="mc-card-title">${indentMark}${escapeHtml(task.text)}</div>
            </div>
            <div class="mc-card-actions">
              ${st === 'pending' ? `<button class="mc-start-btn" data-index="${task.index}" title="Start task">
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                  <polygon points="4,2 12,7 4,12" fill="${COLORS.done.main}"/>
                </svg>
              </button>` : ''}
              <button class="mc-cycle-btn" data-index="${task.index}" title="Cycle status">
                ${this._statusIcon(task.status)}
              </button>
              <button class="mc-delete-btn" data-index="${task.index}" title="Remove">
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                  <path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
                </svg>
              </button>
            </div>
          </div>
          ${milestoneText ? `
          <div class="mc-card-milestone">
            ${st === 'in_progress' ? '<span class="mc-milestone-dot"></span>' : ''}
            <span class="mc-milestone-text">${escapeHtml(milestoneText)}</span>
          </div>` : ''}
          ${toolCount > 0 ? `
          <div class="mc-card-footer">
            <span class="mc-tool-count">
              <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
                <path d="M1 5h8M5 1v8" stroke="${COLORS.purple}" stroke-width="1.2" stroke-linecap="round"/>
              </svg>
              ${toolCount} tool call${toolCount !== 1 ? 's' : ''}
            </span>
            ${hasStream ? '<span class="mc-stream-indicator">STREAMING</span>' : ''}
          </div>` : ''}
        </div>
      </div>
    `;
  }

  _statusIcon(status) {
    if (status === 'done') return `<svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <rect x="1" y="1" width="12" height="12" rx="3" fill="${COLORS.done.dim}" stroke="${COLORS.done.main}" stroke-width="1.3"/>
      <path d="M4 7l2 2 4-4" stroke="${COLORS.done.main}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
    if (status === 'in_progress') return `<svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <rect x="1" y="1" width="12" height="12" rx="3" fill="${COLORS.in_progress.dim}" stroke="${COLORS.in_progress.main}" stroke-width="1.3"/>
      <path d="M5 7h4" stroke="${COLORS.in_progress.main}" stroke-width="1.5" stroke-linecap="round"/>
    </svg>`;
    return `<svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <rect x="1" y="1" width="12" height="12" rx="3" stroke="${COLORS.pending.main}" stroke-width="1.3"/>
    </svg>`;
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

  // ── Live card updates (without full re-render) ─────────────────

  _updateCardLiveState(taskIndex) {
    const card = this._el.querySelector(`.mc-card[data-index="${taskIndex}"]`);
    if (!card) return;
    // Add streaming indicator if not present
    let footer = card.querySelector('.mc-card-footer');
    if (!footer) {
      const inner = card.querySelector('.mc-card-inner');
      footer = document.createElement('div');
      footer.className = 'mc-card-footer';
      inner.appendChild(footer);
    }
    if (!footer.querySelector('.mc-stream-indicator')) {
      footer.innerHTML += '<span class="mc-stream-indicator">STREAMING</span>';
    }
  }

  _updateCardMilestone(taskIndex, toolName, toolInput) {
    const card = this._el.querySelector(`.mc-card[data-index="${taskIndex}"]`);
    if (!card) return;
    const label = this._toolLabel(toolName, toolInput);

    let milestone = card.querySelector('.mc-card-milestone');
    if (!milestone) {
      milestone = document.createElement('div');
      milestone.className = 'mc-card-milestone';
      const inner = card.querySelector('.mc-card-inner');
      const footer = inner.querySelector('.mc-card-footer');
      if (footer) inner.insertBefore(milestone, footer);
      else inner.appendChild(milestone);
    }
    milestone.innerHTML = `
      <span class="mc-milestone-dot"></span>
      <span class="mc-milestone-text">${escapeHtml(label)}</span>
    `;
    // Animate flash
    milestone.classList.remove('mc-milestone-flash');
    void milestone.offsetWidth;
    milestone.classList.add('mc-milestone-flash');

    // Update tool count
    const tools = this._taskTools.get(taskIndex) || [];
    let countEl = card.querySelector('.mc-tool-count');
    if (countEl) {
      countEl.innerHTML = `
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M1 5h8M5 1v8" stroke="${COLORS.purple}" stroke-width="1.2" stroke-linecap="round"/>
        </svg>
        ${tools.length} tool call${tools.length !== 1 ? 's' : ''}
      `;
    }
  }

  // ── Particle canvas ──────────────────────────────────────────────

  _initParticleCanvas() {
    const canvas = this._el.querySelector('.mc-particle-canvas');
    if (!canvas) return;
    this._pCtx = canvas.getContext('2d');
    this._pCanvas = canvas;
    this._resizeCanvas();
    this._particles = [];

    // Seed ambient particles
    for (let i = 0; i < 30; i++) {
      this._particles.push(this._makeParticle());
    }
    this._animateParticles();

    this._resizeObserver = new ResizeObserver(() => this._resizeCanvas());
    this._resizeObserver.observe(this._el);
  }

  _resizeCanvas() {
    if (!this._pCanvas) return;
    this._pCanvas.width = this._el.offsetWidth;
    this._pCanvas.height = this._el.offsetHeight;
  }

  _makeParticle(x, y, burst = false) {
    return {
      x: x ?? Math.random() * (this._pCanvas?.width || 800),
      y: y ?? Math.random() * (this._pCanvas?.height || 600),
      vx: (Math.random() - 0.5) * (burst ? 3 : 0.3),
      vy: (Math.random() - 0.5) * (burst ? 3 : 0.3) - (burst ? 1 : 0),
      r: burst ? 1.5 + Math.random() * 2.5 : 0.5 + Math.random() * 1.2,
      life: burst ? 60 + Math.random() * 40 : 200 + Math.random() * 300,
      maxLife: burst ? 100 : 500,
      color: burst ? COLORS.done.main : COLORS.cyan,
      burst,
    };
  }

  _queueCompletionBurst(taskIndex) {
    const card = this._el.querySelector(`.mc-card[data-index="${taskIndex}"]`);
    if (!card || !this._pCanvas) return;
    const rect = card.getBoundingClientRect();
    const elRect = this._el.getBoundingClientRect();
    const cx = rect.left - elRect.left + rect.width / 2;
    const cy = rect.top - elRect.top + rect.height / 2;
    for (let i = 0; i < 20; i++) {
      this._particles.push(this._makeParticle(cx, cy, true));
    }
  }

  _animateParticles() {
    if (!this._pCtx || !this._pCanvas) return;
    const ctx = this._pCtx;
    const w = this._pCanvas.width;
    const h = this._pCanvas.height;

    ctx.clearRect(0, 0, w, h);

    for (let i = this._particles.length - 1; i >= 0; i--) {
      const p = this._particles[i];
      p.x += p.vx;
      p.y += p.vy;
      p.life--;

      if (p.life <= 0) {
        if (p.burst) {
          this._particles.splice(i, 1);
        } else {
          // Respawn ambient particle
          Object.assign(p, this._makeParticle());
        }
        continue;
      }

      // Wrap ambient particles
      if (!p.burst) {
        if (p.x < 0) p.x = w;
        if (p.x > w) p.x = 0;
        if (p.y < 0) p.y = h;
        if (p.y > h) p.y = 0;
      }

      const alpha = Math.min(1, p.life / (p.maxLife * 0.3));
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = p.color;
      ctx.globalAlpha = alpha * (p.burst ? 0.8 : 0.15);
      ctx.fill();

      if (p.burst && p.r > 1.5) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r * 2, 0, Math.PI * 2);
        ctx.fillStyle = p.color;
        ctx.globalAlpha = alpha * 0.15;
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;

    this._animFrame = requestAnimationFrame(() => this._animateParticles());
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

    const overlay = container.querySelector('.task-modal-overlay');
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) this._closeModal();
    });
    container.querySelector('.task-modal-close').addEventListener('click', () => this._closeModal());

    container.querySelectorAll('.task-modal-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        container.querySelectorAll('.task-modal-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        const isOutput = tab.dataset.tab === 'output';
        container.querySelector('#task-modal-body-output').style.display = isOutput ? '' : 'none';
        container.querySelector('#task-modal-body-tools').style.display = isOutput ? 'none' : '';
      });
    });

    this._bindModalToolEvents(container);

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

    this._modalEscHandler = (e) => { if (e.key === 'Escape') this._closeModal(); };
    document.addEventListener('keydown', this._modalEscHandler);
  }

  _closeModal() {
    this._modalTaskIndex = null;
    const container = document.getElementById('task-detail-modal');
    const overlay = container.querySelector('.task-modal-overlay');
    if (overlay) {
      overlay.classList.add('closing');
      overlay.addEventListener('animationend', () => { container.innerHTML = ''; }, { once: true });
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

        const existing = entry.nextElementSibling;
        if (existing && existing.classList.contains('task-modal-tool-detail')) {
          existing.remove();
          return;
        }
        container.querySelectorAll('.task-modal-tool-detail').forEach(d => d.remove());

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
    const input = this._el.querySelector('.mc-add-input');
    const addBtn = this._el.querySelector('.mc-add-btn');
    const planBtn = this._el.querySelector('.mc-plan-btn');

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

  _bindFilterEvents(root) {
    root.querySelectorAll('.mc-filter-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        this._filterStatus = btn.dataset.filter;
        this._renderContent();
      });
    });
  }

  _bindCardEvents(root) {
    root.querySelectorAll('.mc-card').forEach(card => {
      card.addEventListener('click', (e) => {
        if (e.target.closest('.mc-card-actions')) return;
        const idx = parseInt(card.dataset.index, 10);
        this._openModal(idx);
      });
    });

    root.querySelectorAll('.mc-cycle-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const idx = parseInt(btn.dataset.index, 10);
        const task = this._tasks.find(t => t.index === idx);
        if (!task) return;
        const next = { pending: 'in_progress', in_progress: 'done', done: 'pending' };
        this._updateStatus(idx, next[task.status]);
      });
    });

    root.querySelectorAll('.mc-start-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._startTask(parseInt(btn.dataset.index, 10));
      });
    });

    root.querySelectorAll('.mc-delete-btn').forEach(btn => {
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
