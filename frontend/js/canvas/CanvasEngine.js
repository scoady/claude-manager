/**
 * CanvasEngine — manages canvas widgets inside a GridStack drag/resize grid.
 *
 * Replaces the old CSS Grid layout with gridstack.js for full
 * drag-to-reposition, drag-to-resize, and saveable layouts.
 */

import { WidgetFrame } from './WidgetFrame.js';
import { WidgetRegistry } from './WidgetRegistry.js';

const STAGGER_WINDOW_MS = 50;
const STAGGER_DELAY_MS  = 60;
const GRID_COLUMNS      = 12;

export class CanvasEngine {
  /**
   * @param {HTMLElement|null} [hostEl]
   * @param {object} [opts]
   * @param {boolean} [opts.static]  — disable drag/resize (for system strip)
   */
  constructor(hostEl = null, opts = {}) {
    this._host     = null;
    this._grid     = null;
    this._registry = new WidgetRegistry();
    this._static   = opts.static ?? false;
    this._projectName = null;

    // Entrance stagger state
    this._pendingBatch = [];
    this._batchTimer   = null;

    // Debounced layout save
    this._saveTimer = null;

    if (hostEl) this.mount(hostEl);
  }

  mount(hostEl) {
    this._host = hostEl;
    this._initGrid();
  }

  setProject(name) {
    this._projectName = name;
  }

  // ── GridStack init ───────────────────────────────────────────────────────

  _initGrid() {
    if (!this._host || this._grid) return;

    // For static canvases (system strip), use simple CSS Grid — no GridStack overhead
    if (this._static) return;

    // GridStack expects a specific class on the container
    this._host.classList.add('grid-stack');

    this._grid = GridStack.init({
      column: GRID_COLUMNS,
      cellHeight: 80,
      margin: 5,
      animate: true,
      float: true,
      removable: false,
      acceptWidgets: false,
    }, this._host);

    // Save layout on drag/resize end
    this._grid.on('change', () => this._debouncedSave());
  }

  // ── Public API ────────────────────────────────────────────────────────────

  create(widgetDef) {
    if (!widgetDef?.widget_id) {
      console.warn('[CanvasEngine] create() called without widget_id, skipping.');
      return;
    }

    if (this._registry.get(widgetDef.widget_id)) {
      this.update(widgetDef.widget_id, widgetDef);
      return;
    }

    const frame = new WidgetFrame({
      ...widgetDef,
      onRemove: (id) => this.remove(id),
    });

    this._registry.set(widgetDef.widget_id, frame);

    if (this._host) {
      if (this._grid) {
        // GridStack mode — drag/resize enabled
        const gsW = widgetDef.gs_w ?? widgetDef.col_span ?? 4;
        const gsH = widgetDef.gs_h ?? widgetDef.row_span ?? 3;
        const gsX = widgetDef.gs_x ?? undefined;
        const gsY = widgetDef.gs_y ?? undefined;

        const opts = { w: gsW, h: gsH, id: widgetDef.widget_id, content: '' };
        if (gsX !== undefined && gsX !== null) opts.x = gsX;
        if (gsY !== undefined && gsY !== null) opts.y = gsY;

        const gridEl = this._grid.addWidget(opts);
        const contentEl = gridEl.querySelector('.grid-stack-item-content');
        if (contentEl) {
          contentEl.innerHTML = '';
          contentEl.appendChild(frame.element);
        }
        gridEl.dataset.widgetId = widgetDef.widget_id;
      } else {
        // Static CSS Grid mode (system strip)
        this._host.appendChild(frame.element);
      }

      this._scheduleEntrance(frame, widgetDef.widget_id);
      this._hideEmptyState();
    }
  }

  update(widgetId, patch) {
    const frame = this._registry.get(widgetId);
    if (!frame) {
      console.warn(`[CanvasEngine] update() — widget "${widgetId}" not found.`);
      return;
    }
    frame.update(patch);

    // Update grid position if gs fields changed
    if (this._grid && (patch.gs_w || patch.gs_h || patch.gs_x !== undefined || patch.gs_y !== undefined)) {
      const gridEl = this._host.querySelector(`[data-widget-id="${widgetId}"]`);
      if (gridEl) {
        const updateOpts = {};
        if (patch.gs_w) updateOpts.w = patch.gs_w;
        if (patch.gs_h) updateOpts.h = patch.gs_h;
        if (patch.gs_x !== undefined) updateOpts.x = patch.gs_x;
        if (patch.gs_y !== undefined) updateOpts.y = patch.gs_y;
        this._grid.update(gridEl, updateOpts);
      }
    }
  }

  remove(widgetId) {
    const frame = this._registry.get(widgetId);
    if (!frame) return;

    frame.destroy();
    this._registry.delete(widgetId);

    if (this._grid) {
      const gridEl = this._host.querySelector(`[data-widget-id="${widgetId}"]`);
      if (gridEl) {
        this._grid.removeWidget(gridEl, false);
      }
    } else {
      // Static mode — just remove from DOM
      frame.element.remove();
    }

    if (this._registry.size === 0) {
      this._showEmptyState();
    }
  }

  animate(widgetId, keyframesCss, opts = {}) {
    const frame = this._registry.get(widgetId);
    if (!frame) return;
    frame.animate(keyframesCss, opts);
  }

  clear() {
    for (const frame of this._registry.getAll()) {
      frame.destroy();
      if (!this._grid) frame.element.remove();
    }
    this._registry.clear();
    if (this._grid) {
      this._grid.removeAll(false);
    }
    if (this._host) {
      this._showEmptyState();
    }
  }

  destroy() {
    this.clear();
    if (this._grid) {
      this._grid.destroy(false);
      this._grid = null;
    }
  }

  /** Get the current layout as an array of {id, x, y, w, h}. */
  getLayout() {
    if (!this._grid) return [];
    return this._grid.getGridItems().map(el => {
      const node = el.gridstackNode;
      return {
        id: el.dataset.widgetId || node?.id,
        x: node?.x ?? 0,
        y: node?.y ?? 0,
        w: node?.w ?? 4,
        h: node?.h ?? 3,
      };
    }).filter(item => item.id && item.id !== '__add-placeholder__');
  }

  /** Save current layout to the backend. */
  async saveLayout() {
    if (!this._projectName || this._static) return;
    const layout = this.getLayout();
    try {
      await fetch(`/api/canvas/${encodeURIComponent(this._projectName)}/layout`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(layout),
      });
    } catch (e) {
      console.warn('[CanvasEngine] Failed to save layout:', e);
    }
  }

  // ── Entrance stagger ──────────────────────────────────────────────────────

  _scheduleEntrance(frame, widgetId) {
    this._pendingBatch.push({ frame, widgetId });

    if (this._batchTimer !== null) {
      clearTimeout(this._batchTimer);
    }

    this._batchTimer = setTimeout(() => {
      this._batchTimer = null;
      const batch = this._pendingBatch.splice(0);
      batch.forEach(({ frame: f }, index) => {
        setTimeout(() => f.triggerEntrance(), index * STAGGER_DELAY_MS);
      });
    }, STAGGER_WINDOW_MS);
  }

  // ── Layout persistence ────────────────────────────────────────────────────

  _debouncedSave() {
    if (this._saveTimer) clearTimeout(this._saveTimer);
    this._saveTimer = setTimeout(() => this.saveLayout(), 800);
  }

  // ── Empty state helpers ───────────────────────────────────────────────────

  _hideEmptyState() {
    const el = this._host?.querySelector('.canvas-empty-state');
    if (el) el.style.display = 'none';
  }

  _showEmptyState() {
    const el = this._host?.querySelector('.canvas-empty-state');
    if (el) el.style.display = '';
  }
}
