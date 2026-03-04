/** FeedController — owns the #feed DOM element and manages the narrative feed. */
import { escapeHtml } from '../utils.js';
import { AgentSection } from './AgentSection.js';
import { api } from '../api.js';
import { toast } from '../utils.js';

const LANE_COLORS = [
  '#00f0ff', // cyan
  '#e040fb', // magenta
  '#39ff14', // neon green
  '#ff6ec7', // hot pink
  '#ffcc00', // golden
  '#ff1744', // hot red
  '#00ffc8', // seafoam
  '#b388ff', // lavender
];

export class FeedController {
  /**
   * @param {HTMLElement} container — the #feed element
   */
  constructor(container) {
    this._el        = container;
    this._project   = null;      // current ManagedProject
    this._sections  = new Map(); // sessionId → AgentSection
    this._laneIndex = 0;
    this._headerEl  = null;
    this._focusedSessionId = null;
  }

  // ── Project ────────────────────────────────────────────────────────────────

  /** Switch to a new project — clears old sections, renders project header. */
  setProject(project) {
    this._project = project;
    this._sections.clear();
    this._laneIndex = 0;
    this._el.innerHTML = '';
    this._headerEl = this._buildHeader(project);
    this._el.appendChild(this._headerEl);
  }

  _buildHeader(project) {
    const el = document.createElement('div');
    el.className = 'feed-project-header';
    const agentCount = project.active_session_ids?.length || 0;
    el.innerHTML = `
      <div class="feed-header-row">
        <span class="feed-project-title">${escapeHtml(project.name)}</span>
        <div class="feed-project-meta">
          <span class="feed-meta-chip">${agentCount} agent${agentCount !== 1 ? 's' : ''}</span>
          <span class="feed-meta-chip">×${project.config?.parallelism || 1} parallelism</span>
          ${project.config?.model ? `<span class="feed-meta-chip">${escapeHtml(project.config.model.split('-').slice(-2).join('-'))}</span>` : ''}
        </div>
      </div>
      <div class="feed-dispatch-composer">
        <div class="feed-dispatch-row">
          <textarea class="feed-task-input" rows="1" placeholder="Dispatch a task…"></textarea>
          <select class="feed-parallelism-select" title="Parallelism">
            ${[1,2,3,4].map(n => `<option value="${n}" ${n === (project.config?.parallelism || 1) ? 'selected' : ''}>×${n}</option>`).join('')}
          </select>
          <select class="feed-model-select" title="Model override">
            <option value="">default</option>
            <option value="claude-opus-4-6" ${project.config?.model === 'claude-opus-4-6' ? 'selected' : ''}>opus</option>
            <option value="claude-sonnet-4-6" ${project.config?.model === 'claude-sonnet-4-6' ? 'selected' : ''}>sonnet</option>
            <option value="claude-haiku-4-5-20251001" ${project.config?.model === 'claude-haiku-4-5-20251001' ? 'selected' : ''}>haiku</option>
          </select>
          <button class="feed-dispatch-btn" disabled>
            <svg width="15" height="15" viewBox="0 0 15 15" fill="none">
              <path d="M2 7.5h11M9 3l5 4.5L9 12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>
        </div>
      </div>
      <div class="skill-toggle-panel">
        <div class="skill-toggle-header" title="Toggle skills for this project">
          <svg class="skill-toggle-chevron" width="10" height="10" viewBox="0 0 10 10" fill="none">
            <path d="M3 2l4 3-4 3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <span class="skill-toggle-title">Skills</span>
          <span class="skill-toggle-count"></span>
        </div>
        <div class="skill-toggle-body hidden"></div>
      </div>`;
    this._bindHeaderEvents(el, project);
    this._loadSkillsPanel(el, project);
    return el;
  }

  _bindHeaderEvents(el, project) {
    const textarea   = el.querySelector('.feed-task-input');
    const dispatchBtn = el.querySelector('.feed-dispatch-btn');
    const parallelSel = el.querySelector('.feed-parallelism-select');
    const modelSel   = el.querySelector('.feed-model-select');

    textarea?.addEventListener('input', () => {
      dispatchBtn.disabled = !textarea.value.trim();
      textarea.style.height = 'auto';
      textarea.style.height = Math.min(textarea.scrollHeight, 160) + 'px';
    });

    textarea?.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this._dispatch(project.name, textarea, parallelSel, modelSel, dispatchBtn);
      }
    });

    dispatchBtn?.addEventListener('click', () => {
      this._dispatch(project.name, textarea, parallelSel, modelSel, dispatchBtn);
    });

    parallelSel?.addEventListener('change', async () => {
      try {
        await api.updateProjectConfig(project.name, {
          parallelism: parseInt(parallelSel.value, 10),
          model: modelSel.value || null,
        });
      } catch (_) {}
    });

    modelSel?.addEventListener('change', async () => {
      try {
        await api.updateProjectConfig(project.name, {
          parallelism: parseInt(parallelSel.value, 10),
          model: modelSel.value || null,
        });
      } catch (_) {}
    });
  }

  async _dispatch(projectName, textarea, parallelSel, modelSel, btn) {
    const task = textarea?.value.trim();
    if (!task) return;

    const parallelism = parseInt(parallelSel?.value || '1', 10);
    const model = modelSel?.value || null;

    btn.disabled = true;
    btn.classList.add('sending');

    try {
      await api.updateProjectConfig(projectName, { parallelism, model });
      await api.dispatchTask(projectName, task, model || undefined);
      textarea.value = '';
      textarea.style.height = 'auto';
      toast(`Dispatched to ${projectName}`, 'success', 2000);
    } catch (e) {
      toast(`Dispatch failed: ${e.message}`, 'error');
    } finally {
      btn.classList.remove('sending');
      btn.disabled = !textarea?.value.trim();
    }
  }

  // ── Skills panel ──────────────────────────────────────────────────────────

  async _loadSkillsPanel(headerEl, project) {
    const panel = headerEl.querySelector('.skill-toggle-panel');
    const header = panel.querySelector('.skill-toggle-header');
    const body = panel.querySelector('.skill-toggle-body');
    const countEl = panel.querySelector('.skill-toggle-count');

    // Toggle collapse
    header.addEventListener('click', () => {
      const open = body.classList.toggle('hidden');
      panel.classList.toggle('open', !open);
    });

    // Load skills
    try {
      const skills = await api.getProjectSkills(project.name);
      const enabledCount = skills.filter(s => s.enabled).length;
      countEl.textContent = `(${enabledCount} of ${skills.length} enabled)`;

      if (!skills.length) {
        body.innerHTML = '<div class="skill-empty">No skills available. Create skills in Settings.</div>';
        return;
      }

      body.innerHTML = skills.map(s => `
        <div class="skill-row" data-skill="${escapeHtml(s.name)}" data-source="${escapeHtml(s.source)}">
          <span class="skill-dot ${s.enabled ? 'on' : ''}"></span>
          <span class="skill-name">${escapeHtml(s.name)}</span>
          <span class="skill-desc">${escapeHtml(s.description || '')}</span>
          <span class="skill-source-badge source-${escapeHtml(s.source)}">${escapeHtml(s.source)}</span>
          ${s.source !== 'local' ? `
            <label class="toggle-switch toggle-sm" title="${s.enabled ? 'Disable' : 'Enable'} skill">
              <input type="checkbox" ${s.enabled ? 'checked' : ''} data-skill-name="${escapeHtml(s.name)}" />
              <span class="toggle-track"></span>
            </label>` : '<span class="skill-always-on">always on</span>'}
        </div>`).join('');

      // Bind toggles
      body.querySelectorAll('input[data-skill-name]').forEach(cb => {
        cb.addEventListener('change', async () => {
          const skillName = cb.dataset.skillName;
          const enabled = cb.checked;
          try {
            if (enabled) {
              await api.enableProjectSkill(project.name, skillName);
            } else {
              await api.disableProjectSkill(project.name, skillName);
            }
            const dot = cb.closest('.skill-row').querySelector('.skill-dot');
            dot.classList.toggle('on', enabled);
            // Update count
            const allCbs = body.querySelectorAll('input[data-skill-name]');
            const localCount = body.querySelectorAll('.skill-always-on').length;
            const checkedCount = Array.from(allCbs).filter(c => c.checked).length + localCount;
            countEl.textContent = `(${checkedCount} of ${skills.length} enabled)`;
            toast(`Skill ${skillName} ${enabled ? 'enabled' : 'disabled'}`, 'success', 2000);
          } catch (e) {
            cb.checked = !enabled;
            toast(`Failed: ${e.message}`, 'error');
          }
        });
      });
    } catch (e) {
      body.innerHTML = `<div class="skill-empty">Failed to load skills</div>`;
    }
  }

  // ── Sections ───────────────────────────────────────────────────────────────

  /** Create and append an AgentSection for a newly spawned agent. */
  appendAgentSection(sessionId, task, { phase, turnCount } = {}) {
    if (this._sections.has(sessionId)) return this._sections.get(sessionId);

    const color = LANE_COLORS[this._laneIndex % LANE_COLORS.length];
    this._laneIndex++;

    const section = new AgentSection({
      sessionId,
      task,
      laneColor: color,
      initialPhase: phase,
      initialTurnCount: turnCount,
      onInject: (sid, msg) => this._inject(sid, msg),
      onKill:   (sid) => this._kill(sid),
      onStatus: (sid) => this._askStatus(sid),
      onFocus:  (sid) => this._setFocused(sid),
    });

    this._sections.set(sessionId, section);

    // Animate in
    section.el.style.opacity = '0';
    section.el.style.transform = 'translateY(12px)';
    this._el.appendChild(section.el);
    requestAnimationFrame(() => {
      section.el.style.transition = 'opacity 280ms ease, transform 280ms ease';
      section.el.style.opacity = '1';
      section.el.style.transform = 'translateY(0)';
    });

    // Adaptive layout: when 3+ agents, focus the newest one
    if (this._sections.size >= 3) {
      this._setFocused(sessionId);
    }

    return section;
  }

  /** Get an existing section by sessionId. */
  getSection(sessionId) {
    return this._sections.get(sessionId);
  }

  /** Remap a pending-PID session ID to the real UUID. */
  remapSessionId(oldId, newId) {
    const section = this._sections.get(oldId);
    if (!section) return;
    section.updateSessionId(newId);
    this._sections.delete(oldId);
    this._sections.set(newId, section);
  }

  // ── WS event routing ───────────────────────────────────────────────────────

  handleEvent(msg) {
    switch (msg.type) {
      case 'agent_spawned': {
        const { session_id, project_name, task, phase, turn_count } = msg.data;
        if (!this._project || project_name !== this._project.name) return;
        this.appendAgentSection(session_id, task, {
          phase: phase,
          turnCount: turn_count,
        });
        break;
      }
      case 'agent_stream': {
        const { session_id, chunk, done } = msg.data;
        if (done) return;
        const section = this._sections.get(session_id);
        section?.appendChunk(chunk);
        break;
      }
      case 'session_phase': {
        const { session_id, phase } = msg.data;
        const section = this._sections.get(session_id);
        section?.setPhase(phase);
        break;
      }
      case 'tool_start': {
        const { session_id, tool } = msg.data;
        const section = this._sections.get(session_id);
        section?.addToolBlock({
          toolId:    tool.tool_id,
          toolName:  tool.tool_name,
          toolInput: tool.tool_input,
        });
        break;
      }
      case 'tool_done': {
        const { session_id, tool } = msg.data;
        const section = this._sections.get(session_id);
        section?.updateToolBlock(tool.tool_id, tool.output);
        break;
      }
      case 'turn_done': {
        const { session_id, turn_count } = msg.data;
        const section = this._sections.get(session_id);
        section?.setTurnCount(turn_count);
        section?.updateStatusCard();
        break;
      }
      case 'agent_done': {
        const { session_id, reason } = msg.data;
        const section = this._sections.get(session_id);
        section?.markDone(reason);
        break;
      }
      case 'agent_id_assigned': {
        const { old_session_id, session_id } = msg.data;
        this.remapSessionId(old_session_id, session_id);
        break;
      }
      case 'agent_milestone': {
        // milestones are shown via tool blocks — no extra action needed
        break;
      }
    }
  }

  // ── Adaptive layout ────────────────────────────────────────────────────────

  _setFocused(sessionId) {
    this._focusedSessionId = sessionId;
    for (const [sid, section] of this._sections) {
      if (sid === sessionId) {
        section.setExpanded(true);
      } else if (!section._done) {
        section.setExpanded(false);
      }
    }
  }

  // ── Agent actions ──────────────────────────────────────────────────────────

  async _inject(sessionId, message) {
    try {
      const result = await api.injectMessage(sessionId, message);
      const status = result.status === 'queued' ? 'queued (agent is working)' : 'sent';
      toast(`Message ${status}`, 'success', 2000);
    } catch (e) {
      toast(`Inject failed: ${e.message}`, 'error');
    }
  }

  async _kill(sessionId) {
    try {
      await api.killAgent(sessionId);
      const section = this._sections.get(sessionId);
      section?.markDone('cancelled');
      toast('Agent killed', 'warn', 2000);
    } catch (e) {
      toast(`Kill failed: ${e.message}`, 'error');
    }
  }

  async _askStatus(sessionId) {
    try {
      await api.injectMessage(sessionId, 'Please give a brief status update: what have you completed, what are you working on right now, and what\'s next?');
      toast('Status request sent', 'success', 2000);
    } catch (e) {
      toast(`Failed: ${e.message}`, 'error');
    }
  }
}
