/** MilestonesPanel — renders captured work cycle milestones. */
import { escapeHtml, relativeTime } from '../utils.js';
import { api } from '../api.js';
import { renderMarkdown } from './MarkdownRenderer.js';

export class MilestonesPanel {
  constructor(projectName) {
    this._project = projectName;
    this._milestones = [];
    this._el = document.createElement('div');
    this._el.className = 'milestones-panel';
    this._refreshTimer = null;
    this._render();
  }

  get el() { return this._el; }

  async load() {
    try {
      this._milestones = await api.getMilestones(this._project);
      this._renderList();
    } catch (e) {
      this._el.querySelector('.milestones-list').innerHTML =
        '<div class="milestones-empty">Failed to load milestones</div>';
    }
  }

  updateMilestones(milestones) {
    this._milestones = milestones;
    this._renderList();
  }

  startAutoRefresh() {
    this.stopAutoRefresh();
    this._refreshTimer = setInterval(() => this.load(), 15_000);
  }

  stopAutoRefresh() {
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }
  }

  destroy() {
    this.stopAutoRefresh();
  }

  // ── Rendering ─────────────────────────────────────────────────────────────

  _render() {
    this._el.innerHTML = `
      <div class="milestones-header-row">
        <span class="milestones-count"></span>
        <button class="milestones-clear-btn hidden" title="Clear all milestones">Clear all</button>
      </div>
      <div class="milestones-list"></div>
    `;
    this._bindClearBtn();
  }

  _renderList() {
    const listEl = this._el.querySelector('.milestones-list');
    const countEl = this._el.querySelector('.milestones-count');
    const clearBtn = this._el.querySelector('.milestones-clear-btn');

    if (!this._milestones.length) {
      listEl.innerHTML = '<div class="milestones-empty">No milestones yet. Milestones are captured automatically when agents complete work.</div>';
      countEl.textContent = '';
      clearBtn.classList.add('hidden');
      return;
    }

    countEl.textContent = `${this._milestones.length} milestone${this._milestones.length !== 1 ? 's' : ''}`;
    clearBtn.classList.remove('hidden');

    listEl.innerHTML = this._milestones.map(m => {
      const typeIcon = m.agent_type === 'controller' ? '\u2654'
        : m.agent_type === 'subagent' ? '\u2192' : '\u25CF';
      const typeBadgeClass = `milestone-type-${m.agent_type || 'standalone'}`;
      const duration = m.duration_seconds
        ? this._formatDuration(m.duration_seconds)
        : '';
      const taskLabel = m.task && m.task.length > 120
        ? escapeHtml(m.task.slice(0, 120)) + '\u2026'
        : escapeHtml(m.task || 'Agent work');

      return `
        <div class="milestone-card" data-id="${escapeHtml(m.id)}">
          <div class="milestone-card-header">
            <span class="milestone-type-badge ${typeBadgeClass}">${typeIcon} ${escapeHtml(m.agent_type || 'standalone')}</span>
            <span class="milestone-task">${taskLabel}</span>
            <span class="milestone-meta">
              ${duration ? `<span class="milestone-duration">${duration}</span>` : ''}
              <span class="milestone-time">${relativeTime(m.timestamp)}</span>
            </span>
            <button class="milestone-delete-btn" data-id="${escapeHtml(m.id)}" title="Remove milestone">
              <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
                <path d="M2 2l7 7M9 2l-7 7" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
              </svg>
            </button>
          </div>
          <div class="milestone-summary agent-status-card">${renderMarkdown(m.summary || '')}</div>
        </div>
      `;
    }).join('');

    this._bindListEvents(listEl);
  }

  _formatDuration(secs) {
    secs = Math.floor(secs);
    if (secs < 60) return `${secs}s`;
    const mins = Math.floor(secs / 60);
    const rem = secs % 60;
    if (mins < 60) return `${mins}m ${rem}s`;
    const hrs = Math.floor(mins / 60);
    return `${hrs}h ${mins % 60}m`;
  }

  // ── Events ──────────────────────────────────────────────────────────────

  _bindClearBtn() {
    this._el.querySelector('.milestones-clear-btn')?.addEventListener('click', async () => {
      if (!confirm('Clear all milestones for this project?')) return;
      try {
        this._milestones = await api.clearMilestones(this._project);
        this._renderList();
      } catch (e) {
        console.error('Clear milestones failed:', e);
      }
    });
  }

  _bindListEvents(listEl) {
    listEl.querySelectorAll('.milestone-delete-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const id = btn.dataset.id;
        try {
          this._milestones = await api.deleteMilestone(this._project, id);
          this._renderList();
        } catch (err) {
          console.error('Delete milestone failed:', err);
        }
      });
    });
  }
}
