/** Project rendering — sidebar nav, tile grid, workbench */
import { escapeHtml, relativeTime } from './utils.js';

/**
 * Render compact project cards in the left sidebar.
 * @param {Array} projects - ManagedProject[]
 * @param {string|null} selectedName
 * @param {Function} onSelect - (name: string) => void
 */
export function renderProjectList(projects, selectedName, onSelect) {
  const container = document.getElementById('project-list');
  if (!container) return;

  if (!projects.length) {
    container.innerHTML = `
      <div class="empty-state">
        <p style="font-size:12px;color:var(--text-muted)">No managed projects</p>
        <p style="font-size:11px;color:var(--text-dim);margin-top:4px">Use + to create one</p>
      </div>`;
    return;
  }

  container.innerHTML = projects.map(p => {
    const selected = p.name === selectedName ? 'selected' : '';
    const activeCount = p.active_session_ids?.length || 0;
    const hasActive = activeCount > 0;

    return `
    <div class="project-nav-card ${selected}" data-name="${escapeHtml(p.name)}">
      <div class="project-nav-top">
        <div class="project-nav-indicator ${hasActive ? 'active' : ''}"></div>
        <span class="project-nav-name">${escapeHtml(p.name)}</span>
        ${hasActive ? `<span class="project-nav-badge">${activeCount}</span>` : ''}
      </div>
      ${p.description ? `<div class="project-nav-desc">${escapeHtml(p.description)}</div>` : ''}
    </div>`;
  }).join('');

  container.querySelectorAll('.project-nav-card').forEach(card => {
    card.addEventListener('click', () => onSelect(card.dataset.name));
  });
}

/**
 * Render the full project tile grid (empty state in workbench center).
 * @param {Array} projects - ManagedProject[]
 * @param {Function} onSelect
 */
export function renderProjectTileGrid(projects, onSelect) {
  const grid = document.getElementById('project-tile-grid');
  const emptyMsg = document.getElementById('tile-grid-empty');
  if (!grid) return;

  if (!projects.length) {
    grid.innerHTML = '';
    emptyMsg?.classList.remove('hidden');
    return;
  }
  emptyMsg?.classList.add('hidden');

  grid.innerHTML = projects.map(p => {
    const activeCount = p.active_session_ids?.length || 0;
    const hasActive = activeCount > 0;

    return `
    <div class="project-tile" data-name="${escapeHtml(p.name)}">
      <div class="project-tile-header">
        <div class="project-tile-indicator ${hasActive ? 'active' : ''}"></div>
        <span class="project-tile-name">${escapeHtml(p.name)}</span>
        ${hasActive ? `<span class="project-tile-agents">${activeCount} agent${activeCount !== 1 ? 's' : ''}</span>` : ''}
      </div>
      ${p.description ? `<div class="project-tile-desc">${escapeHtml(p.description)}</div>` : ''}
      <div class="project-tile-footer">
        <span class="project-tile-parallelism">×${p.config?.parallelism || 1}</span>
        ${p.config?.model ? `<span class="project-tile-model">${escapeHtml(p.config.model.split('-').slice(-2).join('-'))}</span>` : ''}
        <span class="project-tile-open">Open →</span>
      </div>
    </div>`;
  }).join('');

  grid.querySelectorAll('.project-tile').forEach(tile => {
    tile.addEventListener('click', () => onSelect(tile.dataset.name));
  });
}

// Grid-level event delegation callbacks (set when renderAgentsGrid is called)
let _onSelectAgent = null;
let _onKillAgent = null;

/**
 * Render the agent cards grid inside the project workbench.
 * Uses event delegation on the grid for click handling.
 */
export function renderAgentsGrid(agents, projectName, onSelectAgent, onKillAgent) {
  const grid = document.getElementById('agents-grid');
  const emptyEl = document.getElementById('agents-grid-empty');
  if (!grid) return;

  // Store callbacks for delegation
  _onSelectAgent = onSelectAgent;
  _onKillAgent = onKillAgent;

  // Attach delegated listeners once
  if (!grid._delegated) {
    grid._delegated = true;
    grid.addEventListener('click', (e) => {
      const killBtn = e.target.closest('.agent-card-kill');
      const card = e.target.closest('.agent-mini-card');
      if (!card) return;
      const sid = card.dataset.session;
      if (killBtn) {
        e.stopPropagation();
        _onKillAgent?.(sid);
      } else {
        _onSelectAgent?.(sid);
      }
    });
  }

  const projectAgents = agents.filter(a => a.project_name === projectName);

  if (!projectAgents.length) {
    grid.querySelectorAll('.agent-mini-card').forEach(el => el.remove());
    emptyEl?.classList.remove('hidden');
    return;
  }
  emptyEl?.classList.add('hidden');

  // Reconcile: update existing cards, add new, remove stale
  const existingCards = new Map(
    [...grid.querySelectorAll('.agent-mini-card')].map(el => [el.dataset.session, el])
  );
  const currentIds = new Set(projectAgents.map(a => a.session_id));

  for (const [sid, el] of existingCards) {
    if (!currentIds.has(sid)) el.remove();
  }

  for (const agent of projectAgents) {
    const sid = agent.session_id;
    if (!sid) continue;
    const existing = existingCards.get(sid);
    if (existing) {
      _updateAgentCard(existing, agent);
    } else {
      const card = document.createElement('div');
      card.className = 'agent-mini-card';
      card.dataset.session = sid;
      _updateAgentCard(card, agent);
      grid.insertBefore(card, emptyEl || null);
    }
  }
}

function _updateAgentCard(card, agent) {
  const isWorking = agent.status === 'working';
  const milestone = agent.current_milestone || (agent.milestones && agent.milestones[agent.milestones.length - 1]) || null;

  card.className = `agent-mini-card status-${agent.status}`;
  card.innerHTML = `
    <div class="agent-card-top">
      <div class="agent-card-status ${agent.status} ${isWorking ? 'pulse' : ''}"></div>
      <span class="agent-card-task">${escapeHtml((agent.task || '').slice(0, 60))}${(agent.task || '').length > 60 ? '…' : ''}</span>
      <button class="agent-card-kill icon-btn danger" title="Kill agent">
        <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
          <path d="M2 2l7 7M9 2l-7 7" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
        </svg>
      </button>
    </div>
    ${milestone ? `<div class="agent-card-milestone">${escapeHtml(milestone)}</div>` : ''}
    <div class="agent-card-footer">
      <span class="agent-card-status-label">${agent.status}</span>
      ${agent.model ? `<span class="agent-card-model">${escapeHtml(agent.model.split('-').slice(-2).join('-'))}</span>` : ''}
      ${agent.has_pending_injection ? '<span class="agent-card-pending">pending inject</span>' : ''}
    </div>`;
}

/**
 * Update the live preview text on an existing agent card.
 */
export function updateAgentCardChunk(sessionId, chunk) {
  const card = document.querySelector(`.agent-mini-card[data-session="${CSS.escape(sessionId)}"]`);
  if (!card) return;
  let preview = card.querySelector('.agent-card-preview');
  if (!preview) {
    preview = document.createElement('div');
    preview.className = 'agent-card-preview';
    card.querySelector('.agent-card-footer')?.insertAdjacentElement('beforebegin', preview);
  }
  const current = preview.textContent || '';
  const updated = (current + chunk).slice(-200);
  preview.textContent = updated;
}

/**
 * Update the milestone label on an existing agent card.
 */
export function updateAgentCardMilestone(sessionId, milestone) {
  const card = document.querySelector(`.agent-mini-card[data-session="${CSS.escape(sessionId)}"]`);
  if (!card) return;
  let el = card.querySelector('.agent-card-milestone');
  if (!el) {
    el = document.createElement('div');
    el.className = 'agent-card-milestone';
    card.querySelector('.agent-card-top')?.insertAdjacentElement('afterend', el);
  }
  el.textContent = milestone;
}
