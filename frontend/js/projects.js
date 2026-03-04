/** Project rendering — sidebar nav, tile grid with live agent strips */
import { escapeHtml } from './utils.js';

// ─── Phase constants for agent strips ────────────────────────────────────────

const PHASE_DOT_COLORS = {
  starting:   'var(--accent-cyan)',
  thinking:   'var(--accent-amber)',
  generating: 'var(--accent-amber)',
  tool_input: 'var(--accent-amber)',
  tool_exec:  'var(--accent-amber)',
  idle:       'var(--accent-green)',
  injecting:  'var(--accent-amber)',
  cancelled:  'var(--accent-teal)',
  error:      'var(--accent-red)',
};

const PHASE_SHORT = {
  starting: 'starting', thinking: 'thinking', generating: 'writing',
  tool_input: 'tool', tool_exec: 'tool', idle: 'idle',
  injecting: 'inject', cancelled: 'done', error: 'error',
};

// ─── Sidebar ─────────────────────────────────────────────────────────────────

/**
 * Render compact project cards in the left sidebar.
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

// ─── Tile Grid ───────────────────────────────────────────────────────────────

function _isWorking(phase) {
  return phase && phase !== 'idle' && phase !== 'cancelled' && phase !== 'error';
}

function _stripClass(phase) {
  if (_isWorking(phase)) return 'strip-working';
  if (phase === 'idle') return 'strip-idle';
  if (phase === 'error') return 'strip-error';
  return '';
}

function renderAgentStrip(agent) {
  const phase = agent.phase || 'starting';
  const dotColor = PHASE_DOT_COLORS[phase] || 'var(--text-muted)';
  const phaseLabel = PHASE_SHORT[phase] || phase;
  const isWorking = _isWorking(phase);
  const task = agent.task || 'Agent';
  const taskTrunc = task.length > 40 ? task.slice(0, 40) + '\u2026' : task;
  const milestone = agent.latest_milestone || '';

  return `
  <div class="agent-strip ${_stripClass(phase)}" data-session="${agent.session_id}">
    <span class="strip-dot ${isWorking ? 'pulsing' : ''}" style="background:${dotColor}"></span>
    <span class="strip-task">${escapeHtml(taskTrunc)}</span>
    <span class="strip-phase">${phaseLabel}</span>
    <span class="strip-turns">${agent.turn_count || 0}t</span>
    ${milestone ? `<span class="strip-milestone">${escapeHtml(milestone)}</span>` : ''}
  </div>`;
}

/**
 * Render the full project tile grid with live agent strips.
 */
export function renderProjectTileGrid(projects, agents, onSelect) {
  const grid = document.getElementById('project-tile-grid');
  const emptyMsg = document.getElementById('tile-grid-empty');
  if (!grid) return;

  if (!projects.length) {
    grid.innerHTML = '';
    emptyMsg?.classList.remove('hidden');
    return;
  }
  emptyMsg?.classList.add('hidden');

  // Suppress entrance animation on re-renders
  if (grid.dataset.initialized) {
    grid.classList.add('no-animate');
    requestAnimationFrame(() => grid.classList.remove('no-animate'));
  }

  grid.innerHTML = projects.map(p => {
    const projectAgents = (agents || []).filter(a => a.project_name === p.name);
    const hasActive = projectAgents.length > 0;
    const hasWorking = projectAgents.some(a => _isWorking(a.phase));

    const tileClass = hasWorking ? 'tile-working' : (hasActive ? 'tile-active' : '');
    const indicatorClass = hasWorking ? 'working' : (hasActive ? 'active' : '');

    return `
    <div class="project-tile ${tileClass}" data-name="${escapeHtml(p.name)}">
      <div class="project-tile-header">
        <div class="project-tile-indicator ${indicatorClass}"></div>
        <span class="project-tile-name">${escapeHtml(p.name)}</span>
        ${hasActive ? `<span class="project-tile-agents">${projectAgents.length} agent${projectAgents.length !== 1 ? 's' : ''}</span>` : ''}
      </div>
      ${p.description ? `<div class="project-tile-desc">${escapeHtml(p.description)}</div>` : ''}
      <div class="project-tile-agent-strips" data-project="${escapeHtml(p.name)}">
        ${projectAgents.map(a => renderAgentStrip(a)).join('')}
      </div>
      <div class="project-tile-footer">
        <span class="project-tile-parallelism">\u00d7${p.config?.parallelism || 1}</span>
        ${p.config?.model ? `<span class="project-tile-model">${escapeHtml(p.config.model.split('-').slice(-2).join('-'))}</span>` : ''}
        <span class="project-tile-open">Open \u2192</span>
      </div>
    </div>`;
  }).join('');

  grid.dataset.initialized = 'true';

  grid.querySelectorAll('.project-tile').forEach(tile => {
    tile.addEventListener('click', () => onSelect(tile.dataset.name));
  });
}

// ─── Targeted Updates (no full re-render) ────────────────────────────────────

/**
 * Update a single agent strip in-place (phase, turns, milestone).
 */
export function updateTileAgentStrip(agent) {
  const stripEl = document.querySelector(`.agent-strip[data-session="${agent.session_id}"]`);
  if (!stripEl) return;

  const phase = agent.phase || 'starting';
  const dotColor = PHASE_DOT_COLORS[phase] || 'var(--text-muted)';
  const phaseLabel = PHASE_SHORT[phase] || phase;
  const isWorking = _isWorking(phase);

  // Update dot
  const dot = stripEl.querySelector('.strip-dot');
  if (dot) {
    dot.style.background = dotColor;
    dot.classList.toggle('pulsing', isWorking);
  }

  // Update phase label
  const phaseEl = stripEl.querySelector('.strip-phase');
  if (phaseEl) phaseEl.textContent = phaseLabel;

  // Update turns
  const turnsEl = stripEl.querySelector('.strip-turns');
  if (turnsEl) turnsEl.textContent = `${agent.turn_count || 0}t`;

  // Update milestone
  if (agent.latest_milestone) {
    let milestoneEl = stripEl.querySelector('.strip-milestone');
    if (!milestoneEl) {
      milestoneEl = document.createElement('span');
      milestoneEl.className = 'strip-milestone';
      stripEl.appendChild(milestoneEl);
    }
    milestoneEl.textContent = agent.latest_milestone;
  }

  // Update strip class
  stripEl.className = `agent-strip ${_stripClass(phase)}`;

  // Propagate to parent tile
  _updateTileIndicators(stripEl.closest('.project-tile'), agent.project_name);
}

/**
 * Re-render agent strips for a single project tile (structural change).
 */
export function updateTileForProject(projectName, agents) {
  const stripsContainer = document.querySelector(
    `.project-tile-agent-strips[data-project="${projectName}"]`
  );
  if (!stripsContainer) return;

  const projectAgents = (agents || []).filter(a => a.project_name === projectName);
  stripsContainer.innerHTML = projectAgents.map(a => renderAgentStrip(a)).join('');

  const tile = stripsContainer.closest('.project-tile');
  if (!tile) return;

  const hasActive = projectAgents.length > 0;
  const hasWorking = projectAgents.some(a => _isWorking(a.phase));

  tile.classList.toggle('tile-working', hasWorking);
  tile.classList.toggle('tile-active', hasActive && !hasWorking);

  const indicator = tile.querySelector('.project-tile-indicator');
  if (indicator) {
    indicator.className = `project-tile-indicator ${hasWorking ? 'working' : (hasActive ? 'active' : '')}`;
  }

  const badge = tile.querySelector('.project-tile-agents');
  if (hasActive) {
    if (badge) {
      badge.textContent = `${projectAgents.length} agent${projectAgents.length !== 1 ? 's' : ''}`;
    } else {
      // Insert badge if didn't exist
      const header = tile.querySelector('.project-tile-header');
      if (header) {
        const newBadge = document.createElement('span');
        newBadge.className = 'project-tile-agents';
        newBadge.textContent = `${projectAgents.length} agent${projectAgents.length !== 1 ? 's' : ''}`;
        header.appendChild(newBadge);
      }
    }
  } else if (badge) {
    badge.remove();
  }
}

function _updateTileIndicators(tile, projectName) {
  if (!tile) return;
  const strips = tile.querySelectorAll('.agent-strip');
  const hasWorking = Array.from(strips).some(s => s.classList.contains('strip-working'));
  const hasActive = strips.length > 0;

  tile.classList.toggle('tile-working', hasWorking);
  tile.classList.toggle('tile-active', hasActive && !hasWorking);

  const indicator = tile.querySelector('.project-tile-indicator');
  if (indicator) {
    indicator.className = `project-tile-indicator ${hasWorking ? 'working' : (hasActive ? 'active' : '')}`;
  }
}
