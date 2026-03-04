/** Project rendering — sidebar nav, tile grid */
import { escapeHtml } from './utils.js';

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

/**
 * Render the full project tile grid (empty state when no project selected).
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
